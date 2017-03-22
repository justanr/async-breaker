from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
import asyncio


class OpenBreaker(Exception):
    """
    Exception thrown when the breaker is in an open state, used to fail fast
    """
    def __init__(self, breaker, retry_at, *a):
        self.name = breaker
        self.retry_at = retry_at
        super().__init__(breaker, retry_at, *a)

    def __str__(self):
        return "{name} is tripped, retry at {time}".format(
            name=self.name,
            time=self.retry_at.isoformat()
        )


class BreakerState(Enum):
    """
    Used to track the current state of the circuit breaker.
    """
    OPEN = 'open'
    CLOSED = 'closed'
    HALF_OPEN = 'half_open'

    def __str__(self):
        return str(self.value)


class CircuitBreaker:
    """
    CircuitBreaker allows a program to fail fast when there has been a recent history of errors
    associated with a call. The breaker begins in a closed state and allows all calls through it,
    when an exception is encountered (and of the enumerated type), it is recorded and re-raised.
    If a frequency of exceptions is encountered (e.g. 10 exceptions / 1 minute), then the breaker
    enters an open state and an OpenBreaker exception is raised (and preserves the initial
    traceback).

    The open state lasts for a cooldown period (30 seconds by default), during which any calls
    through the breaker are closed and an OpenBreaker exception is raised instead. After this
    period, the breaker will allow a single exploratory request to pass through.

    If the call is successful, the breaker resets to its initial state and begins allowing
    requests through again. If the request fails, the breaker re-enters its cooldown period.

    Of note, if the circuit breaker encounters an OpenBreaker exception itself, the breaker
    immediately trips itself and syncs its cooldown period with the retry time noted in the
    exception.

    If finer grained control of recording should happen, the process_exception hook is provided.
    This can be used to determine things like HTTP status code, error messages, etc. The function
    provided should be a synchronous function that accepts the current exception and returns a
    boolean. This will only be called for exceptions listed in record_only (OpenBreaker bypasses
    this handling). It is extremely important that this function **DOES NOT** raise an exception,
    otherwise the reporting framework will be short circuited and the breaker will not close::

        from asyncbreaker import CircuitBreaker
        from requests import HTTPError

        # record only 502s from a remote service
        breaker = CircuitBreaker(
            'HTTPIntegration', max_failures=5,
            process_exception=lambda exc: exc.response.status == 502,
            record_only=HTTPError
        )


    The two main interfaces for CircuitBreaker are CircuitBreaker.run and as a decorator, which
    automatically wraps a coroutine function in CircuitBreaker.run

    When CircuitBreaker.run is used, pass the coroutine itself to the call::

        from random import randrange
        from asyncbreaker import CircuitBreaker

        breaker = CircuitBreaker('test', max_failures=5)

        async def maybe_fail():
            x = randrange(1, 10)
            if x == 4:
                raise ValueError('Not random enough')
            return x

        async def main():
            await breaker.run(maybe_fail())


    When used as a decorator, it will handle passing the arguments to the function::

        from asyncbreaker import CircuitBreaker

        @CircuitBreaker('test', max_failures=5)
        async def maybe_fail():
            x = randrange(1, 10)
            if x == 4:
                raise ValueError('Not random enough')
            return x

        async def main():
            await maybe_fail()

    CircuitBreaker can safely be used to decorate methods as well, although it is not a descriptor
    so it must decorate something that is callable::

        from asyncbreaker import CircuitBreaker

        class IntegrationPoint:
            # wrong!
            @CircuitBreaker('test', max_failures=5)
            @classmethod
            async def cls_coro(cls):
                raise Exception('whoops!')

            #right
            @classmethod
            @CircuitBreaker('test', max_failures=5)
            async def cls_coro(cls):
                raise Exception('whoops')

    :param str name: Name for the breaker
    :param int max_failures: Maximum number of failures to account for
    :param ExceptionType|Tuple[ExceptionType] record_only: Exception types to record as failures.
        Default: Exception
    :param timedelta reset_period: Time period that must pass with no exceptions for the breaker
        to reset to completely blank slate. Default: 30 seconds.
    :param timedelta cooldown: Time period that the breaker will refuse calls after being tripped,
        after which it will allow the exploratory request. Default: 30 seconds.
    :param asyncio.Lock lock: The lock that should be used to guard requests, if not provided
        one is created using the current event loop.
    :param Callable[Exception,Boolean] process_exception: Callback used to help decide if an
        exception should be counted as a failure
    """

    def __init__(
        self, name, max_failures, *,
        record_only=Exception, reset_period=timedelta(seconds=30),
        cooldown=timedelta(seconds=30), lock=None, process_exception=lambda exc: True
    ):
        self.name = name
        self._state = BreakerState.CLOSED

        self._max_failures = max_failures
        self._record_only = record_only

        self._last_failure = datetime.utcfromtimestamp(0)
        self._failures = 0
        self._reset_period = reset_period

        self._cooldown = cooldown
        self._retry_at = datetime.utcfromtimestamp(0)

        self._process_exception = process_exception
        self._lock = lock or asyncio.Lock(loop=asyncio.get_event_loop())

    async def run(self, coro):
        async with self._lock:
            self._ensure_state()

            if self._state is BreakerState.OPEN:
                coro.close()
                self._fail_fast(None)

            try:
                res = await coro

            except OpenBreaker as e:
                self._process_failure(e)
                self._open(e)
                self._fail_fast(e)

            except self._record_only as e:
                self._process_failure(e)
                raise

            if self._state is BreakerState.HALF_OPEN:
                self._reset()

            return res

    async def report(self):
        async with self._lock:
            self._ensure_state()

            state = {
                'name': self.name,
                'threshold': {
                    'max_failures': self._max_failures,
                    'frequency': self._reset_period
                },
                'status': {
                    'state': self._state,
                    'failures': self._failures,
                }
            }

            if self._failures > 0:
                state['status']['last_failure_at'] = self._last_failure

            if self._state is BreakerState.OPEN:
                state['status']['retry_at'] = self._retry_at

            return state

    def guard(self, f):
        @wraps(f)
        async def wrapper(*a, **k):
            return (await self.run(f(*a, **k)))
        return wrapper

    __call__ = guard

    def _ensure_state(self):
        if self._should_allow_retry():
            self._halfopen()

        if self._should_reset():
            self._reset()

    def _reset(self):
        self._close()
        self._failures = 0
        self._last_failure = datetime.utcfromtimestamp(0)

    def _open(self, exc):
        self._state = BreakerState.OPEN
        # if the exception has a retry_at attr, prefer that than the current time
        # since it is probably an OpenBreaker exception, or one pretending to be at least,
        # and the underlying breaker has tripped
        self._retry_at = getattr(exc, 'retry_at', datetime.utcnow()) + self._cooldown

    def _close(self):
        self._state = BreakerState.CLOSED

    def _halfopen(self):
        self._state = BreakerState.HALF_OPEN

    def _should_allow_retry(self):
        return self._state is BreakerState.OPEN and datetime.utcnow() >= self._retry_at

    def _should_reset(self):
        return (
            self._state is BreakerState.CLOSED and
            datetime.utcnow() >= self._last_failure + self._reset_period
        )

    def _process_failure(self, exc):
        if isinstance(exc, OpenBreaker) or self._process_exception(exc):
            self._failures += 1
            self._last_failure = datetime.utcnow()

            if self._failures >= self._max_failures:
                self._open(exc)
                self._fail_fast(exc)

    def _fail_fast(self, exc):
        raise OpenBreaker(breaker=self.name, retry_at=self._retry_at) from exc

    def __str__(self):
        return "<{type}: {name} state: {state!s}>".format(
            name=self.name,
            type=self.__class__.__name__,
            state=self._state
        )
