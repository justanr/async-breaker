from asyncbreaker.breaker import (
    BreakerState,
    CircuitBreaker,
    OpenBreaker,
)

from contextlib import suppress
from datetime import datetime, timedelta
import asyncio
import inspect

import pytest


class IntendedFailure(Exception):
    """
    Test specific exception to avoid collisions with other exceptions that may arise
    from test failures
    """
    pass


async def raiser(exc):
    raise exc


async def const(sentinel):
    return sentinel


def test_circuit_break_str():
    assert str(CircuitBreaker('fred', 5)) == '<CircuitBreaker: fred state: closed>'


@pytest.mark.asyncio
async def test_records_latest_failure():
    breaker = CircuitBreaker('test', 5)

    with suppress(IntendedFailure):
        await breaker.run(raiser(IntendedFailure()))

    report = await breaker.report()
    assert report['status']['last_failure_at'] != datetime.fromtimestamp(0)
    assert report['status']['failures'] == 1


@pytest.mark.asyncio
async def test_succesful_run_records_no_failures():
    sentinel = object()

    breaker = CircuitBreaker('test', 5)
    result = await breaker.run(const(sentinel))
    report = await breaker.report()

    assert result is sentinel
    assert 'last_failure_at' not in report['status']
    assert report['status']['failures'] == 0


@pytest.mark.asyncio
async def test_transitions_to_open_upon_reaching_threshold():

    breaker = CircuitBreaker('test', 1)

    with pytest.raises(OpenBreaker):
        await breaker.run(raiser(IntendedFailure()))

    report = await breaker.report()

    assert report['status']['state'] is BreakerState.OPEN


@pytest.mark.asyncio
async def test_records_only_specific_failures():
    breaker = CircuitBreaker('test', 1, record_only=ValueError)

    with pytest.raises(IntendedFailure):
        await breaker.run(raiser(IntendedFailure()))

    report = await breaker.report()

    assert report['status']['failures'] == 0


@pytest.mark.asyncio
async def test_doesnt_even_call_if_in_open_state():
    tattle = {'fail': False}

    async def f():
        tattle['fail'] = True

    breaker = CircuitBreaker(name='test', max_failures=5)

    for _ in range(5):
        with suppress(IntendedFailure, OpenBreaker):
            await breaker.run(raiser(IntendedFailure()))

    with pytest.raises(OpenBreaker):
        await breaker.run(f())

    assert not tattle['fail']


@pytest.mark.asyncio
async def test_closes_coro_if_in_open_state():
    breaker = CircuitBreaker('test', max_failures=1)

    with suppress(OpenBreaker):
        await breaker.run(raiser(IntendedFailure()))

    coro = const(1)

    with suppress(OpenBreaker):
        await breaker.run(coro)

    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED


@pytest.mark.asyncio
async def test_doesnt_transition_to_open_if_threshold_not_met():
    breaker = CircuitBreaker('test', max_failures=5)

    for _ in range(4):
        with suppress(IntendedFailure):
            await breaker.run(raiser(IntendedFailure()))

    report = await breaker.report()

    assert report['status']['state'] is BreakerState.CLOSED
    assert report['status']['failures'] == 4


@pytest.mark.asyncio
async def test_transitions_to_half_open_after_cooldown():
    breaker = CircuitBreaker('test', max_failures=5, cooldown=timedelta(seconds=2))

    with pytest.raises(OpenBreaker):
        for _ in range(5):
            with suppress(IntendedFailure):
                await breaker.run(raiser(IntendedFailure()))

    await asyncio.sleep(2)

    report = await breaker.report()

    assert report['status']['state'] is BreakerState.HALF_OPEN
    assert report['status']['failures'] == 5


@pytest.mark.asyncio
async def test_transitions_back_to_open_if_exploratory_call_fails():
    breaker = CircuitBreaker('test', max_failures=1, cooldown=timedelta(seconds=1))

    with suppress(OpenBreaker):
        await breaker.run(raiser(IntendedFailure()))

    await asyncio.sleep(2)

    report = await breaker.report()
    assert report['status']['state'] is BreakerState.HALF_OPEN

    with suppress(OpenBreaker):
        await breaker.run(raiser(IntendedFailure()))

    report = await breaker.report()

    assert report['status']['state'] is BreakerState.OPEN


@pytest.mark.asyncio
async def test_transitions_to_closed_after_successful_cooldown():
    breaker = CircuitBreaker('test', max_failures=1, cooldown=timedelta(seconds=2))

    with suppress(OpenBreaker):
        await breaker.run(raiser(IntendedFailure()))

    await asyncio.sleep(2)

    await breaker.run(const(1))
    report = await breaker.report()

    assert report['status']['state'] is BreakerState.CLOSED
    assert report['status']['failures'] == 0
    assert 'last_failure_at' not in report['status']


@pytest.mark.asyncio
async def test_returns_to_initial_state_after_reset_period():
    breaker = CircuitBreaker(
        'test',
        record_only=IntendedFailure,
        max_failures=2,
        reset_period=timedelta(seconds=2)
    )

    with suppress(IntendedFailure):
        await breaker.run(raiser(IntendedFailure()))

    await asyncio.sleep(2)
    report = await breaker.report()

    assert report['status']['failures'] == 0
    assert 'last_failure_at' not in report['status']


@pytest.mark.asyncio
async def test_as_decorator():
    breaker = CircuitBreaker('test', max_failures=5)

    @breaker
    async def f():
        raise IntendedFailure

    with suppress(IntendedFailure):
        await f()

    report = await breaker.report()

    assert report['status']['failures'] == 1


@pytest.mark.asyncio
async def test_concurrent_calls_fail_if_threshold_is_reached():
    breaker = CircuitBreaker('test', max_failures=2)

    tattle = {'calls': 0}

    @breaker
    async def f():
        tattle['calls'] += 1
        raise IntendedFailure

    await asyncio.gather(f(), f(), f(), return_exceptions=True)

    report = await breaker.report()

    assert tattle['calls'] == 2
    assert report['status']['failures'] == 2


@pytest.mark.asyncio
async def test_allows_fine_grained_control_over_exception_recording():
    breaker = CircuitBreaker(
        'test', max_failures=25,
        process_exception=lambda exc: exc.args[0] == 1
    )

    for x in range(2):
        with suppress(IntendedFailure):
            await breaker.run(raiser(IntendedFailure(x)))

    report = await breaker.report()
    assert report['status']['failures'] == 1


@pytest.mark.asyncio
async def test_open_breaker_cascades_upwards():
    breaker = CircuitBreaker('test', max_failures=3, cooldown=timedelta(minutes=5))

    with pytest.raises(OpenBreaker) as excinfo:
        await breaker.run(raiser(OpenBreaker('coro', datetime(3000, 1, 1))))

    report = await breaker.report()
    assert report['status']['state'] is BreakerState.OPEN
    assert excinfo.value.name == 'test'
    assert excinfo.value.retry_at == datetime(3000, 1, 1, 0, 5)
    assert str(excinfo.value) == 'test is tripped, retry at 3000-01-01T00:05:00'
