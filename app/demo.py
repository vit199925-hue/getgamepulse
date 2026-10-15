"""Synthetic, explicitly labelled fixtures; never presented as scraped data."""

from app.source import GameData


class DemoSource:
    async def close(self):
        pass

    async def listing(self, home=False, page=1):
        if home:
            return [f"demo-game-{i}" for i in range(1, 21)]
        return [f"demo-game-{i}" for i in range(21, 41)] if page == 1 else []

    async def game(self, slug):
        number = int(slug.rsplit("-", 1)[1])
        genres = ["RPG", "Adventure"] if number % 2 else ["Action", "Roguelike"]
        return GameData(
            slug=slug,
            title=f"Demo · {'Lunar Journey' if number % 2 else 'Neon Frontier'} {number}",
            source_url="https://www.metacritic.com/game/",
            cover=f"/static/covers/{'lunar-journey' if number % 2 else 'neon-frontier'}.svg",
            developer="Demo Studio",
            description="Синтетическая игра для проверки интерфейса и пайплайна. "
            "Эти сведения и оценки не получены с Metacritic.",
            genres=genres,
            platforms=[
                {"name": "PC", "metascore": 65 + number % 30, "userscore": 6 + number % 4},
                {"name": "PlayStation 5", "metascore": 70 + number % 25, "userscore": None},
            ],
            reviews={
                kind: [
                    {
                        "id": f"{kind}-0",
                        "text": "Мне понравились сражения, но загрузки долгие.",
                        "author": "Synthetic reviewer",
                        "platform": "PC",
                    }
                ]
                for kind in ("critics", "users")
            },
        )
