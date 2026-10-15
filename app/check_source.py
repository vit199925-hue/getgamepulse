"""Manually verify one live game without calling the LLM."""

import asyncio
import json

from app.config import settings
from app.source import Metacritic


async def main():
    source = Metacritic(settings())
    try:
        slugs = await source.listing(home=True)
        game = await source.game(slugs[0])
        print(
            json.dumps(
                {
                    "slug": game.slug,
                    "title": game.title,
                    "cover": game.cover,
                    "developer": game.developer,
                    "video": game.video,
                    "platforms": game.platforms,
                    "review_counts": {k: len(v) for k, v in game.reviews.items()},
                    "warnings": game.reviews.get("warnings", []),
                }
            ),
            flush=True,
        )
    finally:
        await source.close()


if __name__ == "__main__":
    asyncio.run(main())
