"""Cache verified image bytes locally, avoiding dependence on browser hotlink access."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings


async def cache_cover(url, client, directory):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not (
        parsed.hostname == "metacritic.com" or (parsed.hostname or "").endswith(".metacritic.com")
    ):
        raise ValueError("Cover must be an HTTPS Metacritic image")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    for ext in ("webp", "jpg", "png"):
        if (directory / f"{digest}.{ext}").is_file():
            return f"/media/covers/{digest}.{ext}"
    async with client.stream(
        "GET", url, headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    ) as response:
        response.raise_for_status()
        mime = response.headers.get("content-type", "").split(";")[0]
        extension = {"image/webp": "webp", "image/jpeg": "jpg", "image/png": "png"}.get(mime)
        if not extension:
            raise ValueError("Source did not return a supported image")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 8 * 1024 * 1024:
                raise ValueError("Image exceeds 8 MB")
    # Some original .jpg URLs return PNG bytes with an image/jpeg Content-Type.
    # Serve the validated file format rather than trusting its name or MIME label.
    if content[:3] == b"\xff\xd8\xff":
        extension = "jpg"
    elif content[:8] == b"\x89PNG\r\n\x1a\n":
        extension = "png"
    elif content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        extension = "webp"
    else:
        raise ValueError("Invalid image signature")
    name = f"{digest}.{extension}"
    temporary = directory / (name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(directory / name)
    return "/media/covers/" + name


async def refresh(database_url):
    from app.db import Game
    from app.source import Metacritic, parse_game

    config = settings()
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    source = Metacritic(config)
    try:
        with sessions() as db:
            games = db.scalars(select(Game).where(~Game.slug.like("demo-game-%")).order_by(Game.id)).all()
        for game in games:
            if (game.cover or "").startswith("/media/covers/") and (
                Path(config.cover_directory) / game.cover.rsplit("/", 1)[-1]
            ).is_file():
                continue
            try:
                current = parse_game(await source.get(f"/game/{game.slug}/"), game.slug)
                if not current.cover:
                    continue
                await asyncio.sleep(config.request_delay_seconds)
                local = await cache_cover(current.cover, source.client, config.cover_directory)
                with sessions.begin() as db:
                    db.get(Game, game.id).cover = local
                print(json.dumps({"slug": game.slug, "cover": local}), flush=True)
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                print(json.dumps({"slug": game.slug, "error": type(exc).__name__}), flush=True)
    finally:
        await source.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    asyncio.run(refresh(parser.parse_args().database_url))
