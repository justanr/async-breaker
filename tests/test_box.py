from asyncbreaker.box import BreakerBox
from asyncbreaker.breaker import BreakerState
import pytest


def test_persists_breakers_it_created():
    box = BreakerBox()
    breaker = box.breaker('test', max_failures=5)

    assert box.breakers == {'test': breaker}


def test_reuses_existing_breaker_if_same_name():
    box = BreakerBox()
    breaker = box.breaker('test', max_failures=5)
    other = box.breaker('test', max_failures=10)

    assert breaker is other


@pytest.mark.asyncio
async def test_creates_breaked_callable():
    box = BreakerBox()

    @box.as_breaker(max_failures=5)
    async def hello():
        return 'hello'

    report = await box.report()
    assert report['hello']


@pytest.mark.asyncio
async def test_reports_all_children():
    box = BreakerBox()
    box.breaker('test', max_failures=5)
    box.breaker('test2', max_failures=5)
    report = await box.report()

    assert report['test']
    assert report['test2']
    assert report['test']['name'] == 'test'
    assert report['test']['status']['state'] is BreakerState.CLOSED
