import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from youtube_transcript_api import YouTubeTranscriptApi

from app.ai import Analyzer, YouTube, video_matches


def test_title_filter_rejects_different_games_and_videos_without_commentary():
    assert not video_matches("Fatal Train", "Dishonored 2 - To Boat by Train - Lets Play")
    assert not video_matches("Thalia", "Star Ocean Walkthrough: Thalia Plains")
    assert not video_matches("Hunt Me", "Hunt Me Daddy UwU | The Price Of Flesh")
    assert not video_matches("The Guardians", "GUARDIANS OF THE GALAXY PS5 Walkthrough")
    assert not video_matches("Castlevania: Belmonts Curse", "Castlevania Curse of Darkness Walkthrough")
    assert not video_matches("Example Game", "Example Game Full Walkthrough No Commentary")
    assert not video_matches("Disneyland Adventures", "Land Adventures Gameplay")
    assert video_matches("Enshrouded", "I Created Infinite Chaos - Enshrouded")
    assert video_matches("Example Game", "Example Game lets play")
    assert video_matches("Warhammer 40,000: Boltgun 2", "Warhammer 40K Boltgun 2 - Gameplay")


def test_most_viewed_relevant_video_is_selected_and_captions_are_analyzed(config, monkeypatch):
    config.youtube_api_key = "test"

    async def get(self, url, **kwargs):
        assert "key" not in kwargs["params"]
        assert kwargs["headers"]["X-Goog-Api-Key"] == "test"
        if url.endswith("/search"):
            data = {
                "items": [
                    {"id": {"videoId": i}, "snippet": {"title": title}}
                    for i, title in [
                        ("a", "Example Game walkthrough"),
                        ("b", "Example Game lets play"),
                        ("c", "Other title"),
                    ]
                ]
            }
        else:
            assert kwargs["params"]["id"] == "a,b"
            data = {
                "items": [
                    {
                        "id": i,
                        "snippet": {"title": "Example Game lets play"},
                        "statistics": {"viewCount": str(views)},
                    }
                    for i, views in [("a", 10), ("b", 1000)]
                ]
            }
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    class Segment:
        text = "Combat is fun, loading is slow."
        start = 20.0

    def fetch(self, video_id, **kwargs):
        assert video_id == "b"
        return [Segment()]

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(YouTubeTranscriptApi, "fetch", fetch)
    result = asyncio.run(YouTube(config, Analyzer(config)).analyze("Example Game"))
    assert result["status"] == "ready" and result["views"] == 1000
    assert result["url"].endswith("v=b")
    assert result["transcript_method"] == "captions"
    assert result["summary"]["status"] == "disabled"


def test_fresh_search_result_uses_cache_without_api_call(config, monkeypatch):
    config.youtube_api_key = "test"
    previous = {"status": "not_found", "checked_at": datetime.now(timezone.utc).isoformat()}

    async def forbidden(*args, **kwargs):
        raise AssertionError("Cached result must not spend search quota")

    monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)
    assert asyncio.run(YouTube(config, Analyzer(config)).analyze("Example", previous)) is previous


def test_quota_error_is_sanitized_and_classified(config, monkeypatch):
    config.youtube_api_key = "secret-test"
    calls = []

    async def get(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(
            403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    service = YouTube(config, Analyzer(config))
    result = asyncio.run(service.analyze("Example"))
    assert result["status"] == "quota_exceeded" and result["http_status"] == 403
    assert result["error_code"] == "quotaExceeded"
    assert "secret-test" not in str(result)
    assert asyncio.run(service.analyze("Another game"))["status"] == "quota_exceeded"
    assert len(calls) == 1


@pytest.mark.parametrize("body", [{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, {}])
def test_rate_limit_pauses_following_searches(config, monkeypatch, body):
    config.youtube_api_key = "test"
    calls = []

    async def get(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(429, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    service = YouTube(config, Analyzer(config))
    first = asyncio.run(service.analyze("Example"))
    second = asyncio.run(service.analyze("Another game"))
    assert first["status"] == second["status"] == "quota_exceeded"
    assert first["error_code"] == second["error_code"] == "rateLimitExceeded"
    assert first["retry_at"] == second["retry_at"]
    assert len(calls) == 1
