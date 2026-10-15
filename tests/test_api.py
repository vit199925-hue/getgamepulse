import asyncio

from bs4 import BeautifulSoup
from conftest import sign_in

from app.demo import DemoSource
from app.jobs import Runner
from app.main import safe_url


def test_local_demo_cover_urls_are_allowed_without_allowing_arbitrary_paths():
    assert safe_url("/static/covers/lunar-journey.svg") == "/static/covers/lunar-journey.svg"
    assert safe_url("https://example.com/cover.jpg") == "https://example.com/cover.jpg"
    assert safe_url("javascript:alert(1)") == ""
    assert safe_url("//external.example/cover.svg") == ""
    assert safe_url("/static/covers/../../.env") == ""


def test_catalog_search_filter_detail_and_manual_auth(sessions, config, web_client):
    asyncio.run(Runner(sessions, config, source=DemoSource()).execute())
    client = web_client
    assert sign_in(client).status_code == 303
    response = client.get("/api/games", params={"q": "Lunar", "platform": "PC", "sort": "userscore"})
    assert response.status_code == 200
    assert response.json()["total"] == 10
    assert client.get("/api/games", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/games?page=0").status_code == 422
    assert client.get("/games/demo-game-1").status_code == 200
    assert client.get("/games/missing").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/monitor").status_code == 200
    assert client.get("/api/status").json()["total_games"] == 20
    assert client.post("/api/runs").status_code == 403
    csrf = BeautifulSoup(client.get("/monitor").text, "html.parser").select_one('meta[name="csrf-token"]')[
        "content"
    ]
    assert client.post("/api/runs", headers={"X-CSRF-Token": csrf}).status_code == 202
    client.cookies.clear()
    config.admin_token = "test-token"
    assert client.post("/api/runs").status_code == 401
    assert client.post("/api/runs", headers={"Authorization": "Bearer test-token"}).status_code == 202
