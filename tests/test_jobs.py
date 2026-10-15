import asyncio
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db import DailyItem, DayCursor, Game, Run, SchedulerLock, utcnow
from app.demo import DemoSource
from app.jobs import Runner


def queue(sessions):
    with sessions.begin() as db:
        run = Run()
        db.add(run)
        db.flush()
        return run.id


def test_daily_selection_starts_at_home_and_does_not_drop_page_tail(sessions, config):
    class Source(DemoSource):
        async def listing(self, home=False, page=1):
            return (
                [f"demo-game-{i}" for i in range(1, 21)]
                if home
                else ([f"demo-game-{i}" for i in range(1, 45)] if page == 1 else [])
            )

    runner = Runner(sessions, config, source=Source())
    assert runner.claim()
    day = date(2026, 10, 15)
    first = asyncio.run(runner.select_batch(day, queue(sessions)))
    assert first == [f"demo-game-{i}" for i in range(1, 21)]
    with sessions.begin() as db:
        for item in db.scalars(select(DailyItem)):
            item.status = "done"
    second = asyncio.run(runner.select_batch(day, queue(sessions)))
    assert second == [f"demo-game-{i}" for i in range(21, 41)]
    with sessions.begin() as db:
        for item in db.scalars(select(DailyItem)):
            item.status = "done"
    third = asyncio.run(runner.select_batch(day, queue(sessions)))
    assert third == [f"demo-game-{i}" for i in range(41, 45)]
    tomorrow = asyncio.run(runner.select_batch(day + timedelta(days=1), queue(sessions)))
    assert tomorrow == first


def test_failed_item_is_retried_and_upsert_is_idempotent(sessions, config):
    class Flaky(DemoSource):
        broken = True

        async def game(self, slug):
            if slug == "demo-game-1" and self.broken:
                raise RuntimeError("Transient source failure")
            return await super().game(slug)

    source = Flaky()
    runner = Runner(sessions, config, source=source)
    asyncio.run(runner.execute())
    with sessions() as db:
        first = db.scalar(select(Run).order_by(Run.id))
        assert (first.status, first.processed, first.failed) == ("partial", 19, 1)
    source.broken = False
    assert runner.enqueue() is not None
    asyncio.run(runner.execute())
    with sessions() as db:
        assert db.scalar(select(func.count(Game.id))) == 39
        second = db.scalar(select(Run).order_by(Run.id.desc()))
        assert second.processed == 20
        assert db.scalar(select(DailyItem).where(DailyItem.slug == "demo-game-1")).status == "done"
    # New day resets selection, existing games are updated, not duplicated.
    with sessions.begin() as db:
        for cursor in db.scalars(select(DayCursor)):
            cursor.home_done, cursor.page, cursor.pending = False, 1, []
        for item in db.scalars(select(DailyItem)):
            db.delete(item)
    runner.enqueue()
    asyncio.run(runner.execute())
    with sessions() as db:
        assert db.scalar(select(func.count(Game.id))) == 39


def test_only_one_worker_can_claim_and_expired_lease_is_recovered(sessions, config):
    one, two = [Runner(sessions, config, source=DemoSource()) for _ in range(2)]
    assert one.claim()
    assert not two.claim()
    assert two.enqueue() is None
    with sessions.begin() as db:
        db.get(SchedulerLock, 1).expires_at = utcnow() - timedelta(seconds=1)
    assert two.claim()
    one.release()
    with sessions() as db:
        assert db.get(SchedulerLock, 1).owner == two.owner


def test_source_failure_does_not_advance_cursor(sessions, config):
    class Broken(DemoSource):
        async def listing(self, **kwargs):
            raise RuntimeError("403")

    runner = Runner(sessions, config, source=Broken())
    asyncio.run(runner.execute())
    with sessions() as db:
        assert db.scalar(select(Run)).status == "failed"
        cursor = db.scalar(select(DayCursor))
        assert not cursor.home_done and cursor.page == 1
        assert db.scalar(select(func.count(Game.id))) == 0
