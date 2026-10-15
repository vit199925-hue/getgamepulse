"""Regression tests against reduced DOM captured from Metacritic on 2026-10-15."""

import json
from pathlib import Path

from bs4 import BeautifulSoup

from app.source import (
    aggregate_score,
    card_image,
    parse_game,
    parse_listing,
    parse_reviews,
    platform_identifiers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "metacritic"


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_current_game_platform_scores_and_identifiers():
    html = read("game.html")
    game = parse_game(html, "enshrouded")
    assert game.title == "Enshrouded" and game.cover.startswith("https://")
    platforms = {p["name"]: p for p in game.platforms}
    assert platforms["PC"]["metascore"] == 89
    assert platforms["PlayStation 5"]["metascore"] is None
    assert platform_identifiers(BeautifulSoup(html, "html.parser"))["Xbox Series X"] == "xbox-series-x"
    assert game.video.startswith("https://cdn.jwplayer.com/players/")


def test_current_critic_reviews_preserve_authors_platforms_and_scores():
    html = read("critics.html")
    reviews = parse_reviews(html, "critics")
    assert len(reviews) == 2
    assert reviews[0]["author"] == "Try Hard Guides"
    assert reviews[0]["score"] == 100
    assert reviews[0]["platform"] == "PC"
    assert reviews[0]["source_url"] == "https://tryhardguides.com/enshrouded-review/"
    assert aggregate_score(BeautifulSoup(html, "html.parser"), "critics") == 89


def test_current_empty_user_reviews_are_not_a_parse_error():
    assert parse_reviews(read("users-empty.html"), "users") == []


def test_current_browse_results_exclude_navigation_links():
    slugs = parse_listing(read("browse.html"))
    assert len(slugs) == 2 and slugs[0] == "fatal-train"


def test_portrait_cover_is_read_from_safe_nuxt_json_references():
    table = [
        {"slug": 1, "images": 2},
        "enshrouded",
        [3],
        {"typeName": 4, "bucketType": 5, "bucketPath": 6},
        "cardImage",
        "catalog",
        "/provider/7/2/7-1790044020.jpg",
    ]
    html = '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(table) + "</script>"
    assert card_image(BeautifulSoup(html, "html.parser"), "enshrouded") == (
        "https://www.metacritic.com/a/img/catalog/provider/7/2/7-1790044020.jpg"
    )
    assert card_image(BeautifulSoup(html, "html.parser"), "different-game") is None
