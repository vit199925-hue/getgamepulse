import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select, update

from app.ai import Analyzer, YouTube
from app.db import DailyItem, DayCursor, Game, PlatformRating, Run, SchedulerLock, utcnow
from app.source import Metacritic

log = logging.getLogger(__name__)


class RunQueue:
    """Database lease and queue operations shared by the API and the worker."""

    def __init__(self, sessions, config):
        self.sessions, self.config = sessions, config
        self.owner = uuid.uuid4().hex

    def claim(self):
        now = utcnow()
        with self.sessions.begin() as db:
            result = db.execute(
                update(SchedulerLock)
                .where(
                    SchedulerLock.id == 1,
                    or_(SchedulerLock.owner.is_(None), SchedulerLock.expires_at < now),
                )
                .values(owner=self.owner, expires_at=now + timedelta(seconds=self.config.lease_seconds))
            )
            return result.rowcount == 1

    def renew(self):
        with self.sessions.begin() as db:
            result = db.execute(
                update(SchedulerLock)
                .where(
                    SchedulerLock.id == 1,
                    SchedulerLock.owner == self.owner,
                )
                .values(expires_at=utcnow() + timedelta(seconds=self.config.lease_seconds))
            )
            if result.rowcount != 1:
                raise RuntimeError("Worker lost its lease")

    def release(self):
        with self.sessions.begin() as db:
            db.execute(
                update(SchedulerLock)
                .where(SchedulerLock.id == 1, SchedulerLock.owner == self.owner)
                .values(owner=None, expires_at=utcnow())
            )

    def enqueue(self, trigger="manual"):
        # Serialize enqueue and scheduled/manual dispatch through the same DB lease.
        if not self.claim():
            return None
        try:
            with self.sessions.begin() as db:
                active = db.scalar(select(Run).where(Run.status.in_(["queued", "running"])))
                if active and active.status == "running":
                    active.status, active.error, active.finished_at = (
                        "interrupted",
                        "Worker lease expired",
                        utcnow(),
                    )
                    active = None
                if active:
                    return active.id
                run = Run(trigger=trigger)
                db.add(run)
                db.flush()
                return run.id
        finally:
            self.release()


class Runner(RunQueue):
    def __init__(self, sessions, config, source=None, analyzer=None):
        super().__init__(sessions, config)
        self.source = source or Metacritic(config)
        self.analyzer = analyzer or Analyzer(config)
        self.youtube = YouTube(config, self.analyzer)

    async def select_batch(self, day, run_id):
        with self.sessions.begin() as db:
            cursor = db.get(DayCursor, day)
            if cursor is None:
                cursor = DayCursor(day=day, home_done=False, page=1, pending=[])
                db.add(cursor)
            # Retry unfinished work before advancing the listing cursor.
            retry = db.scalars(
                select(DailyItem)
                .where(DailyItem.day == day, DailyItem.status.in_(["pending", "failed"]))
                .order_by(DailyItem.id)
            ).all()
            batch = [x.slug for x in retry[:20]]
            seen = set(db.scalars(select(DailyItem.slug).where(DailyItem.day == day)).all())
            pending, home_done, page = list(cursor.pending), cursor.home_done, cursor.page
        pages = 0
        while len(batch) < 20:
            while pending and len(batch) < 20:
                slug = pending.pop(0)
                if slug not in seen:
                    batch.append(slug)
                    seen.add(slug)
            if len(batch) == 20 or pages >= self.config.max_pages_per_run:
                break
            if not home_done:
                pending = await self.source.listing(home=True)
                home_done = True
            else:
                pending = await self.source.listing(page=page)
                if not pending:
                    break
                page += 1
            pages += 1
            self.renew()
        with self.sessions.begin() as db:
            cursor = db.get(DayCursor, day)
            cursor.pending, cursor.home_done, cursor.page = pending, home_done, page
            for slug in batch:
                item = db.scalar(select(DailyItem).where(DailyItem.day == day, DailyItem.slug == slug))
                if item:
                    item.status, item.run_id, item.error = "pending", run_id, None
                else:
                    db.add(DailyItem(day=day, slug=slug, run_id=run_id))
            db.get(Run, run_id).selected = len(batch)
        return batch

    async def execute(self):
        if not self.claim():
            return
        run_id = None
        try:
            with self.sessions.begin() as db:
                # Only a worker holding the lease can recover abandoned jobs.
                for stale in db.scalars(select(Run).where(Run.status == "running")):
                    stale.status, stale.error, stale.finished_at = "interrupted", "Worker restarted", utcnow()
                lock = db.get(SchedulerLock, 1)
                run = db.scalar(select(Run).where(Run.status == "queued").order_by(Run.id))
                now = utcnow()
                due = lock.next_run.replace(tzinfo=now.tzinfo) <= now
                if run is None and due:
                    run = Run(trigger="schedule")
                    db.add(run)
                    db.flush()
                if run is None:
                    return
                run.status, run.heartbeat = "running", now
                run_id = run.id
                lock.next_run = now + timedelta(seconds=self.config.interval_seconds)
            day = datetime.now(ZoneInfo(self.config.timezone)).date()
            batch = await self.select_batch(day, run_id)
            for slug in batch:
                self.renew()
                with self.sessions.begin() as db:
                    run = db.get(Run, run_id)
                    run.current, run.heartbeat = slug, utcnow()
                    old = db.scalar(select(Game).where(Game.slug == slug))
                    previous = old.summaries if old else {}
                    previous_letsplay = old.letsplay if old else None
                try:
                    data = await self.source.game(slug)
                    summaries = {}
                    for kind in ("critics", "users"):
                        summaries[kind] = await self.analyzer.summarize(
                            data.reviews.get(kind, []), previous.get(kind)
                        )
                    letsplay = await self.youtube.analyze(data.title, previous_letsplay)
                    if (
                        letsplay.get("status") in ("error", "quota_exceeded")
                        and previous_letsplay
                        and previous_letsplay.get("url")
                    ):
                        letsplay = dict(previous_letsplay, refresh_error=letsplay)
                    self.renew()
                    with self.sessions.begin() as db:
                        game = db.scalar(select(Game).where(Game.slug == slug))
                        if game is None:
                            game = Game(slug=slug)
                            db.add(game)
                        for key, value in asdict(data).items():
                            setattr(game, key, value)
                        game.summaries, game.letsplay, game.updated_at = summaries, letsplay, utcnow()
                        db.flush()
                        db.execute(delete(PlatformRating).where(PlatformRating.game_id == game.id))
                        for platform in data.platforms:
                            db.add(PlatformRating(game_id=game.id, **platform))
                        item = db.scalar(
                            select(DailyItem).where(DailyItem.day == day, DailyItem.slug == slug)
                        )
                        item.status, item.error = "done", None
                        db.get(Run, run_id).processed += 1
                    log.info("game_processed", extra={"run_id": run_id, "slug": slug})
                except Exception as exc:
                    log.exception("game_failed: %s", slug)
                    with self.sessions.begin() as db:
                        item = db.scalar(
                            select(DailyItem).where(DailyItem.day == day, DailyItem.slug == slug)
                        )
                        item.status, item.error = "failed", str(exc)[:2000]
                        db.get(Run, run_id).failed += 1
            with self.sessions.begin() as db:
                run = db.get(Run, run_id)
                run.status = "partial" if run.failed else "completed"
                run.current, run.finished_at, run.heartbeat = None, utcnow(), utcnow()
        except Exception as exc:
            log.exception("run_failed")
            if run_id:
                with self.sessions.begin() as db:
                    run = db.get(Run, run_id)
                    run.status, run.error, run.finished_at = "failed", str(exc)[:2000], utcnow()
        finally:
            self.release()

    async def loop(self):
        while True:
            try:
                await self.execute()
            except Exception:
                log.exception("worker_tick_failed")
            await asyncio.sleep(3)


async def main():
    from app.config import settings
    from app.db import Session, init_db
    from app.demo import DemoSource

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()
    config = settings()
    runner = Runner(Session, config, source=DemoSource() if config.demo_mode else None)
    try:
        await runner.loop()
    finally:
        await runner.source.close()


if __name__ == "__main__":
    asyncio.run(main())
