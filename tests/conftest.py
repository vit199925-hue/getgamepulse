import os

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base, SchedulerLock


@pytest.fixture
def web_client(sessions, config, monkeypatch):
    from app import auth
    from app.main import app, db_session

    config.web_password_hash = auth.password_hash("test-password")
    config.session_secret = "test-session-secret-" + "a" * 32
    monkeypatch.setattr("app.main.Session", sessions)
    monkeypatch.setattr("app.main.settings", lambda: config)
    monkeypatch.setattr("app.main.init_db", lambda: None)
    auth.attempts.clear()

    def dependency():
        with sessions() as db:
            yield db

    app.dependency_overrides[db_session] = dependency
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        auth.attempts.clear()


def sign_in(client, password="test-password"):
    html = client.get("/login").text
    challenge = BeautifulSoup(html, "html.parser").select_one('input[name="csrf"]')["value"]
    return client.post(
        "/login", data={"username": "admin", "password": password, "csrf": challenge}, follow_redirects=False
    )


@pytest.fixture
def sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if url and not (make_url(url).database or "").endswith("_test"):
        raise ValueError("TEST_DATABASE_URL must point to a disposable database ending with _test")
    engine = (
        create_engine(url)
        if url
        else create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(SchedulerLock(id=1))
    yield factory
    engine.dispose()


@pytest.fixture
def config():
    return Settings(_env_file=None, llm_api_key="", youtube_api_key="", request_delay_seconds=0)
