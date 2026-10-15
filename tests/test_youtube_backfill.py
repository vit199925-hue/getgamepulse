import asyncio

from sqlalchemy import select

from app.db import Game, Run
from app.youtube_backfill import fill


def seed(sessions):
    with sessions.begin() as db:
        for slug in ("real-one", "real-two", "demo-game-1"):
            db.add(
                Game(
                    slug=slug,
                    title=slug,
                    source_url="https://www.metacritic.com/",
                    cover="https://example.com/cover.jpg",
                    description="Keep this description",
                    letsplay={"status": "ready", "url": "https://www.youtube.com/watch?v=existing"},
                )
            )


def test_youtube_backfill_updates_only_real_games_and_records_progress(sessions, config, monkeypatch):
    seed(sessions)

    async def analyze(self, title, previous=None):
        return {"status": "not_found"}

    monkeypatch.setattr("app.youtube_backfill.YouTube.analyze", analyze)
    report = asyncio.run(fill(sessions, config))
    assert report["updated"] == 2 and report["not_found"] == 2
    with sessions() as db:
        run = db.scalar(select(Run))
        assert run.status == "completed" and run.processed == 2 and run.trigger == "youtube"
        demo = db.scalar(select(Game).where(Game.slug == "demo-game-1"))
        assert demo.letsplay["status"] == "ready"
        assert all(g.description == "Keep this description" for g in db.scalars(select(Game)))


def test_quota_stops_the_batch_and_preserves_saved_video(sessions, config, monkeypatch):
    seed(sessions)
    calls = []

    async def analyze(self, title, previous=None):
        calls.append(title)
        return {"status": "quota_exceeded", "error_code": "quotaExceeded"}

    monkeypatch.setattr("app.youtube_backfill.YouTube.analyze", analyze)
    report = asyncio.run(fill(sessions, config))
    assert len(calls) == 1 and report["status"] == "partial" and report["updated"] == 0
    with sessions() as db:
        assert all(g.letsplay["status"] == "ready" for g in db.scalars(select(Game)))
