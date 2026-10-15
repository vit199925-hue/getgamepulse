from datetime import timedelta

from bs4 import BeautifulSoup
from conftest import sign_in
from sqlalchemy import select

from app.auth import COOKIE, password_hash, verify_password
from app.db import WebSession, utcnow


def test_password_is_salted_and_verified():
    one, two = password_hash("secret"), password_hash("secret")
    assert one != two and verify_password("secret", one)
    assert not verify_password("wrong", one) and not verify_password("secret", "malformed")


def test_anonymous_cannot_access_pages_api_or_docs(web_client):
    for url in ("/", "/monitor", "/games/demo-game-1", "/docs", "/openapi.json"):
        response = web_client.get(url, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    for url in ("/api/games", "/api/status", "/api/events"):
        assert web_client.get(url).status_code == 401
    assert web_client.get("/health").status_code == 200


def test_session_cookies_csrf_and_logout_revoke_server_session(web_client):
    response = sign_in(web_client)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    old_token = web_client.cookies.get(COOKIE)
    html = web_client.get("/monitor").text
    csrf = BeautifulSoup(html, "html.parser").select_one('meta[name="csrf-token"]')["content"]
    assert web_client.post("/logout", data={"csrf": "wrong"}).status_code == 403
    assert (
        web_client.post(
            "/api/runs", headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert web_client.post("/logout", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    web_client.cookies.set(COOKIE, old_token)
    assert web_client.get("/api/games").status_code == 401


def test_bad_login_is_rate_limited_and_login_csrf_is_required(web_client):
    assert (
        web_client.post("/login", data={"username": "admin", "password": "test-password"}).status_code == 403
    )
    for _ in range(5):
        assert sign_in(web_client, "wrong").status_code == 401
    assert sign_in(web_client, "wrong").status_code == 429


def test_expiration_and_password_changes_invalidate_session(web_client, sessions, config):
    assert sign_in(web_client).status_code == 303
    with sessions.begin() as db:
        db.scalar(select(WebSession)).expires_at = utcnow() - timedelta(seconds=1)
    assert web_client.get("/api/games").status_code == 401
    assert sign_in(web_client).status_code == 303
    config.web_password_hash = password_hash("new-password")
    assert web_client.get("/api/games").status_code == 401
