"""Refresh summaries for saved reviews without fetching or reprocessing games."""

import argparse
import asyncio
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai import Analyzer
from app.config import settings
from app.db import Game


async def fill(sessions, config, limit=20, demo_only=False):
    analyzer = Analyzer(config)
    cache = {}
    report = {
        "games_updated": 0,
        "summaries_ready": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "total_tokens": 0,
        "errors": [],
    }
    with sessions() as db:
        query = select(Game).order_by(Game.id).limit(limit)
        if demo_only:
            query = query.where(Game.slug.like("demo-game-%"))
        games = db.scalars(query).all()
    for game in games:
        summaries = dict(game.summaries)
        for kind in ("critics", "users"):
            records = game.reviews.get(kind, [])
            key = json.dumps(records, sort_keys=True, ensure_ascii=False)
            if key in cache:
                result = cache[key]
                report["cache_hits"] += 1
            else:
                previous = summaries.get(kind)
                result = await analyzer.summarize(records, previous)
                if result is not previous and records and config.llm_api_key:
                    report["api_calls"] += 1
                    report["total_tokens"] += result.get("usage", {}).get("total_tokens", 0)
                if result["status"] == "ready":
                    cache[key] = result
            if result["status"] not in ("ready", "no_reviews"):
                report["errors"].append(
                    {
                        "slug": game.slug,
                        "kind": kind,
                        "status": result["status"],
                        "reason": result.get("reason"),
                        "http_status": result.get("http_status"),
                        "error_code": result.get("error_code"),
                    }
                )
                return report
            summaries[kind] = result
            report["summaries_ready"] += result["status"] == "ready"
        with sessions.begin() as db:
            current = db.get(Game, game.id)
            if current.reviews != game.reviews:
                report["errors"].append({"slug": game.slug, "reason": "Reviews changed during analysis"})
                continue
            current.summaries = summaries
        report["games_updated"] += 1
        print(json.dumps({"slug": game.slug, "status": "saved"}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--demo-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be between 1 and 1000")
    engine = create_engine(args.database_url)
    try:
        report = asyncio.run(
            fill(sessionmaker(engine, expire_on_commit=False), settings(), args.limit, args.demo_only)
        )
        print(json.dumps(report), flush=True)
        if report["errors"]:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
