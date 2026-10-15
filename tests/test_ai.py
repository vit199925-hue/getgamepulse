import asyncio
import json

import httpx

from app.ai import Analyzer


def test_no_key_and_no_reviews_have_explicit_statuses(config):
    analyzer = Analyzer(config)
    assert asyncio.run(analyzer.summarize([]))["status"] == "no_reviews"
    assert asyncio.run(analyzer.summarize([{"id": "1", "text": "Good"}]))["status"] == "disabled"


def test_gpt35_input_respects_small_context_window(config, monkeypatch):
    config.llm_api_key = "test"
    config.llm_model = "gpt-3.5-turbo"
    seen = []

    async def post(self, url, **kwargs):
        content = kwargs["json"]["messages"][1]["content"]
        assert len(content.encode("utf-8")) <= 12000
        seen.extend(json.loads(content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"conclusion": "Limited sample", "likes": [], "dislikes": []}
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    records = [{"id": str(i), "text": "Review " * 400} for i in range(40)]
    result = asyncio.run(Analyzer(config).summarize(records))
    assert result["status"] == "ready" and 0 < len(seen) < 40
    assert result["sample_size"] == len(seen) and result["total_collected"] == 40


def mock_model(monkeypatch, result):
    async def post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result)}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)


def test_gpt5_nano_uses_supported_reasoning_parameters(config, monkeypatch):
    config.llm_model = "gpt-5-nano"
    config.llm_api_key = "test"

    async def post(self, url, **kwargs):
        body = kwargs["json"]
        assert body["model"] == "gpt-5-nano"
        assert body["max_completion_tokens"] == 4000
        assert body["reasoning_effort"] == "minimal"
        assert "temperature" not in body and "max_tokens" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"conclusion": "Sample", "likes": [], "dislikes": []})
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = asyncio.run(Analyzer(config).summarize([{"id": "1", "text": "Good combat"}]))
    assert result["status"] == "ready" and result["model"] == "gpt-5-nano"


def test_transcript_analysis_uses_narrator_context(config, monkeypatch):
    config.llm_api_key = "test"

    async def post(self, url, **kwargs):
        system = kwargs["json"]["messages"][0]["content"]
        assert "расшифровку летсплея" in system and "одного блоггера" in system
        assert "Ты анализируешь выборку отзывов" not in system
        output = kwargs["json"]["response_format"]
        assert output["type"] == "json_schema" and output["json_schema"]["strict"]
        schema = output["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["Claim"]["properties"]["evidence_ids"]["items"]["enum"] == ["t1"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"conclusion": "Narrator opinion", "likes": [], "dislikes": []}
                            )
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = asyncio.run(
        Analyzer(config).summarize([{"id": "t1", "text": "Nice combat"}], source_kind="letsplay")
    )
    assert result["status"] == "ready"


def test_unknown_evidence_is_rejected(config, monkeypatch):
    config.llm_api_key = "test"
    mock_model(
        monkeypatch,
        {"conclusion": "Good", "likes": [{"text": "Combat", "evidence_ids": ["invented"]}], "dislikes": []},
    )
    result = asyncio.run(Analyzer(config).summarize([{"id": "1", "text": "Great combat"}]))
    assert result["status"] == "error"


def test_valid_output_is_cached_only_for_same_input_and_model(config, monkeypatch):
    config.llm_api_key = "test"
    mock_model(
        monkeypatch,
        {
            "conclusion": "Выборка положительная.",
            "likes": [{"text": "Бои", "evidence_ids": ["1"]}],
            "dislikes": [],
        },
    )
    records = [{"id": "1", "text": "Great combat"}]
    analyzer = Analyzer(config)
    result = asyncio.run(analyzer.summarize(records))
    assert result["status"] == "ready"

    async def forbidden(*args, **kwargs):
        raise AssertionError("Cache should avoid the API")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    assert asyncio.run(analyzer.summarize(records, result)) == result
