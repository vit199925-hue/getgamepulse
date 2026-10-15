import asyncio

from sqlalchemy import select

from app.backfill import fill
from app.db import Game


def seed(sessions):
    with sessions.begin() as db:
        for slug in ("demo-game-1", "demo-game-2", "real-game"):
            db.add(
                Game(
                    slug=slug,
                    title=slug,
                    source_url="https://example.com/",
                    reviews={
                        kind: [{"id": f"{kind}-0", "text": "Good combat"}] for kind in ("critics", "users")
                    },
                )
            )


def test_backfill_deduplicates_requests_and_limits_changes_to_demo(sessions, config, monkeypatch):
    seed(sessions)
    calls = []
    config.llm_api_key = "test"

    async def summarize(self, records, previous=None):
        calls.append(records)
        return {
            "status": "ready",
            "conclusion": "Good",
            "likes": [],
            "dislikes": [],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("app.backfill.Analyzer.summarize", summarize)
    report = asyncio.run(fill(sessions, config, 40, demo_only=True))
    assert report["games_updated"] == 2 and report["summaries_ready"] == 4
    assert report["api_calls"] == 2 and report["cache_hits"] == 2 and len(calls) == 2
    assert report["total_tokens"] == 20
    with sessions() as db:
        real = db.scalar(select(Game).where(Game.slug == "real-game"))
        assert real.summaries == {}


def test_backfill_stops_on_api_error_without_overwriting_results(sessions, config, monkeypatch):
    seed(sessions)

    async def summarize(self, records, previous=None):
        return {"status": "error", "reason": "HTTPStatusError", "http_status": 429}

    monkeypatch.setattr("app.backfill.Analyzer.summarize", summarize)
    report = asyncio.run(fill(sessions, config, 40, demo_only=True))
    assert report["games_updated"] == 0 and len(report["errors"]) == 1
    with sessions() as db:
        assert all(not game.summaries for game in db.scalars(select(Game)))
