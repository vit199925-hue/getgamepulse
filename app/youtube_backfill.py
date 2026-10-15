"""Populate saved games' letsplays without refetching their Metacritic pages."""

import argparse
import asyncio
import json

from sqlalchemy import select

from app.ai import Analyzer, YouTube
from app.db import Game, Run, utcnow
from app.jobs import RunQueue


async def fill(sessions, config, limit=40, slug=None, force=False):
    queue = RunQueue(sessions, config)
    report = {"updated": 0, "ready_summaries": 0, "not_found": 0, "transcript_unavailable": 0, "errors": []}
    if not queue.claim():
        return dict(report, status="busy")
    run_id = None
    try:
        service = YouTube(config, Analyzer(config))
        with sessions.begin() as db:
            query = select(Game).where(~Game.slug.like("demo-game-%")).order_by(Game.id).limit(limit)
            if slug:
                query = query.where(Game.slug == slug)
            games = db.scalars(query).all()
            run = Run(status="running", trigger="youtube", selected=len(games), heartbeat=utcnow())
            db.add(run)
            db.flush()
            run_id = run.id
        for game in games:
            queue.renew()
            with sessions.begin() as db:
                run = db.get(Run, run_id)
                run.current, run.heartbeat = "YouTube: " + game.title, utcnow()
            result = await service.analyze(game.title, None if force else game.letsplay)
            if result.get("status") in ("error", "quota_exceeded", "disabled"):
                report["errors"].append(
                    {
                        "slug": game.slug,
                        "status": result["status"],
                        "reason": result.get("error_code") or result.get("reason"),
                    }
                )
                with sessions.begin() as db:
                    run = db.get(Run, run_id)
                    run.failed += 1
                    run.error = "YouTube: " + str(report["errors"][-1]["reason"])
                break  # Don't burn the daily quota repeatedly after a configuration/quota error.
            with sessions.begin() as db:
                db.get(Game, game.id).letsplay = result
                db.get(Run, run_id).processed += 1
            report["updated"] += 1
            report["ready_summaries"] += result.get("summary", {}).get("status") == "ready"
            for status in ("not_found", "transcript_unavailable"):
                report[status] += result.get("status") == status
            print(
                json.dumps(
                    {
                        "slug": game.slug,
                        "status": result.get("status"),
                        "summary": result.get("summary", {}).get("status"),
                        "url": result.get("url"),
                    }
                ),
                flush=True,
            )
        with sessions.begin() as db:
            run = db.get(Run, run_id)
            run.status = "partial" if report["errors"] else "completed"
            run.current, run.finished_at, run.heartbeat = None, utcnow(), utcnow()
        return dict(report, status="partial" if report["errors"] else "completed", run_id=run_id)
    except Exception:
        if run_id:
            with sessions.begin() as db:
                run = db.get(Run, run_id)
                run.status, run.error, run.finished_at = "failed", "YouTube batch interrupted", utcnow()
        raise
    finally:
        queue.release()


def main():
    from app.config import settings
    from app.db import Session, init_db

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--slug")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be between 1 and 1000")
    init_db()
    result = asyncio.run(fill(Session, settings(), args.limit, args.slug, args.force))
    print(json.dumps(result), flush=True)
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
