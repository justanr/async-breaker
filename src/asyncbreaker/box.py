from .breaker import CircuitBreaker
import asyncio


class BreakerBox:
    """
    BreakerBox is a factory of :class:`CircuitBreaker` instances and a reporting mechanism for
    all instances it has created.

    This is used as a tool to report on multiple breakers rather than keeping individual references
    to each breaker.

    Breakers created by the box are cached based on their name, so multiple calls with the same
    name will return the same breaker. Best practice is to always include all needed arguments with
    each call to BreakerBox.breaker.
    """

    def __init__(self, *breakers):
        self.breakers = {b.name: b for b in breakers}

    def breaker(self, name, *, max_failures, **kwargs):
        """
        Retrieves a circuit breaker from the cache or creates one, inserts it into the cache
        and returns it.

        :param name str: Name of the circuit breaker instance to retrieve or create
        :param max_failures int: Number of failures that the underlying CircuitBreaker should
        tolerate.
        :param kwargs: Other options to pass to the underlying CircuitBreaker instance.
        """
        return self.breakers.setdefault(name, CircuitBreaker(name, max_failures, **kwargs))

    def as_breaker(self, f=None, *, max_failures, name=None, **kwargs):
        """
        Transforms a function into a circuit breaker controlled function.

        Unless a name is provided, it takes the name of the function as the Circuit Breaker's
        name.

        Additionally, if the breaker name already exists in the local breaker cache, it is
        reused instead of creating a new instance. This can be used to decorated multiple methods
        or functions that integrate a common service.

        :param f: The callable to decorate
        :param max_failures int: The number of failures to tolerate
        :param kwargs: Other options to pass to the underlying circuit breaker instance
        """
        if f is None:
            return lambda f: self.as_breaker(f, max_failures=max_failures, **kwargs)

        name = name or f.__name__
        return self.breaker(name, max_failures=max_failures, **kwargs)(f)

    async def report(self):
        """
        Returns a dictionary of breaker names to their reports.
        """
        reports = await asyncio.gather(*[breaker.report() for breaker in self.breakers.values()])
        return {report['name']: report for report in reports}
