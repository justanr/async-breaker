# async-breaker

Experimental circuit breaker package for Python's asyncio.

By experimental, I mean this is a mostly naive, first pass over the design and
implementation of this pattern.

It is probably not suited for production use as it aggressively locks to ensure the integrity
of the underlying data and this could potentially do more damage to your system than suffering
through repeated fail calls to a failing service.

## Install it

Clone from github:

```bash
git clone git@github.com:justanr/async-breaker.git
cd async-breaker
pip install .
```

If you get complaints about pip not being able to install it, try either creating a
virtual environment and installing it there, or adding the `--user` flag to the install command.


## Use It

Now that it is installed, you can start guarding your calls from repeated failures

```python
from asyncbreaker import CircuitBreaker

breaker = CircuitBreaker('some-name', max_failures=5)
```

With a created circuit breaker, you can run coroutines inside of it:

```python
loop.run_until_complete(breaker.run(unreliable_coro(*args, **kwargs)))
```

Each time this is run, the circuit breaker will record exceptions that propagate out of the
coroutine and when a certain threshold (set by the `max_failures` argument), the breaker
"trips open" and all calls through the breaker immediately fail with an `OpenBreaker` exception.

However, manually running coroutines through the breaker doesn't provide a lot of benefit, instead
you may choose to decorate your coroutine functions with a circuit breaker instead:

```python
@breaker.guard
async def unreliable_coro(*a, **k):
    # do something that might raise an exception
```

Alternatively, `CircuitBreaker.__call__` is an alias to the guard method

```python
@breaker
async def unreliable_coro(*a, **k):
    # do something that might raise an exception
```

### Fine Tuning Exception recording

If you only want *some* but not all exceptions recorded as part of the circuit breaker,
you may pass a single exception type or a tuple of exception types to the breaker at creation:

```python
breaker = CircuitBreaker('some-breaker', max_failures=5, record_only=(ValueError, TypeError))
```

Additionally, you may pass a *synchronous* function to tune the exception handling even more:

```python
from aiohttp import HttpProcessingError

breaker = CircuitBreaker(
    'some-breaker', max_failures=5,
    record_only=HttpProcessingError
    process_exception=lambda exc: getattr(exc, 'code', 0) >= 500   
)
```

This breaker will only count `aiohttp.HttpProcessingError` exceptions that have a status code of
500 or greater.

It should be noted that this filtering function should *never* throw an exception or the exception
handling framework will not function properly and the breaker will never trip.


### Other Options

* `reset_period`: A timedelta instance that represents the period that no exceptions must occur
    before the circuit breaker instance will reset to a blank slate. By default 30 seconds.
* `cooldown`: A timedelta instance that represents how long the circuit breaker instance will
    deny calls through to the underlying coroutine. By default 30 seconds.
* `lock`: If desired, a specific asyncio compatible lock may be passed, by default each breaker
    acquires its own. 

### Status

Since CircuitBreaker instances can provide information about the current status of integration,
 it exposes this information under the CircuitBreaker.report() coroutine:

```python
loop.run_until_complete(breaker.report())
# {
#   'name': 'some-breaker',
#   'threshold': {
#     'max_failures': 5,
#     'frequency': ...
#   },
#   'status': {
#     'state': OPEN | HALF_OPEN | CLOSED,
#     'failures': ...
#   }
# }
```

This will return information such as the name of the breaker, the current state, how many
failures have occurred in the last period, and if available when the last failure occurred and 
at what time the instance will enter a `HALF_OPEN` state inside the `status` section.

### Circuit Breaker States

An individual circuit breaker may be in one of three states:

* CLOSED: The breaker has not reached its threshold and is allowing calls through to the
  underlying coroutine
* OPEN: The breaker has reached its threshold but has not finished its cooldown period, any calls
  to the underlying coroutine will fail immediately with an OpenBreaker exception.
* HALF\_OPEN: The breaker was open but has finished its cooldown period, a single exploratory
  request will be allowed through to determine the responsiveness of the underlying coroutine.

## BreakerBox

Keeping with the circuit breaker theme, there is also a `BreakerBox` class that is essentially
a collection of circuit breakers.

```python
from asyncbreaker import BreakBox

box = BreakerBox()
```

With this, you can create individual circuit breakers using the box which are then cached
inside the `BreakerBox` instance. The `BreakerBox.breaker` factory method accepts all the same
arguments as the `CircuitBreaker` constructor.

```python
breaker = box.breaker('some-breaker', max_failures=5)
assert breaker is box.breaker('some-breaker', max_failures=10)
```

As a consequence, seen above, the first circuit breaker definition is the cannonical one
and all others following it will ignore any settings passed to it. 

Similar to the individual circuit breaker, the `BreakerBox` has a decorator option as well:

```python
@box.as_breaker(max_failures=5)
async def unreliable_coro(*a, **k):
    # do something that might fail
```

This decorator format accepts all the same arguments as the `CircuitBreaker` constructor with the
option to use the function name as the circuit breaker name. However, if the name is passed
explicitly then that is the cannonical name of the breaker, this is useful for decoratoring
multiple methods in a class with a single breaker:

```python
class SomeIntegrationPoint:
    @box.as_breaker(name='integration-1', max_failures=5)
    async def do_something(self):
        ...

    @box.as_breaker(name='integration-1', max_failures=5)
    async def do_something_else(self):
        ...
```

Finally, the `BreakerBox` offers its own reporting method which gathers the reports of all of
its child `CircuitBreaker` instances into one report:


```python
loop.run_until_complete(box.report())
# {
#   'integration-1': { ... },
#   'unreliable_coro': { ... },
#   'some-breaker': { ... }
# }
```

## The Idea

I first encountered this idea from
 [Daniel Riti - Remote Calls != Local Calls: Graceful Degradation when Services Fail](http://pyvideo.org/pycon-us-2016/daniel-riti-remote-calls-local-calls-graceful-degradation-when-services-fail-pycon-2016.html). But was more fully exposed to it in 
[Michael Nygard's Release It!](https://www.amazon.com/Release-Production-Ready-Software-Pragmatic-Programmers/dp/0978739213).

For further information see:

* [Martin Fowler](https://martinfowler.com/bliki/CircuitBreaker.html)
* [Wikipedia](https://en.wikipedia.org/wiki/Circuit_breaker_design_pattern)
