"""Single-user authentication with server-side revocable sessions."""

import asyncio
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from datetime import timedelta, timezone
from urllib.parse import parse_qs

from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete

from app.db import WebSession, utcnow

COOKIE = "gamepulse_session"
LOGIN_CSRF = "gamepulse_login_csrf"
ITERATIONS = 600_000
attempts = OrderedDict()


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def configured(config):
    return bool(config.web_password_hash and len(config.session_secret) >= 32)


def credential_tag(config):
    return hashlib.sha256(
        (config.web_username + config.web_password_hash + config.session_secret).encode()
    ).hexdigest()


def csrf_for(token, config):
    return hmac.new(config.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


async def form_fields(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            return {}
    try:
        parsed = parse_qs(body.decode("utf-8"), max_num_fields=10)
        return {key: value[0] for key, value in parsed.items()}
    except (UnicodeError, ValueError):
        return {}


def new_session(sessions, config):
    token = secrets.token_urlsafe(32)
    now = utcnow()
    with sessions.begin() as db:
        db.execute(delete(WebSession).where(WebSession.expires_at < now))
        db.add(
            WebSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                username=config.web_username,
                credential_tag=credential_tag(config),
                expires_at=now + timedelta(hours=config.session_hours),
            )
        )
    return token


def valid_session(sessions, config, token):
    if not token or len(token) > 200 or not configured(config):
        return False
    with sessions() as db:
        session = db.get(WebSession, hashlib.sha256(token.encode()).hexdigest())
        return bool(
            session
            and session.expires_at.replace(tzinfo=timezone.utc) > utcnow()
            and session.username == config.web_username
            and hmac.compare_digest(session.credential_tag, credential_tag(config))
        )


async def guard(request, call_next, config, sessions):
    path = request.url.path
    request.state.username, request.state.csrf = None, ""
    if path in ("/health", "/favicon.ico", "/login") or path.startswith("/static/"):
        return await call_next(request)
    bearer = request.headers.get("authorization", "")
    if (
        path.startswith("/api/")
        and config.admin_token
        and hmac.compare_digest(bearer.encode(), ("Bearer " + config.admin_token).encode())
    ):
        return await call_next(request)
    token = request.cookies.get(COOKIE, "")
    valid = await asyncio.to_thread(valid_session, sessions, config, token)
    if not valid:
        if path.startswith("/api/") or request.method != "GET":
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    request.state.username = config.web_username
    request.state.csrf = csrf_for(token, config)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
        supplied = request.headers.get("x-csrf-token", "")
        if path == "/logout":
            supplied = (await form_fields(request)).get("csrf", "")
        if not hmac.compare_digest(supplied.encode(), request.state.csrf.encode()):
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


async def login(request, config, sessions, templates):
    error, status = "", 200
    challenge = request.cookies.get(LOGIN_CSRF, "")
    if request.method == "POST":
        fields = await form_fields(request)
        origin = request.headers.get("origin")
        csrf = fields.get("csrf", "")
        if (
            not challenge
            or not hmac.compare_digest(csrf.encode(), challenge.encode())
            or (origin and origin.rstrip("/") != str(request.base_url).rstrip("/"))
        ):
            error, status = "Обновите страницу входа и повторите попытку.", 403
        elif not configured(config):
            error, status = "Вход ещё не настроен на сервере.", 503
        else:
            address = request.client.host if request.client else "unknown"
            count, start = attempts.get(address, (0, time.monotonic()))
            if time.monotonic() - start > 300:
                count, start = 0, time.monotonic()
            attempts[address] = (count + 1, start)
            attempts.move_to_end(address)
            if len(attempts) > 10000:
                attempts.popitem(last=False)
            if count >= 5:
                error, status = "Слишком много попыток. Повторите через 5 минут.", 429
            else:
                valid = await asyncio.to_thread(
                    verify_password, fields.get("password", ""), config.web_password_hash
                )
                username_valid = hmac.compare_digest(
                    fields.get("username", "").encode(), config.web_username.encode()
                )
                if valid and username_valid:
                    attempts.pop(address, None)
                    token = await asyncio.to_thread(new_session, sessions, config)
                    response = RedirectResponse("/", status_code=303)
                    response.set_cookie(
                        COOKIE,
                        token,
                        max_age=config.session_hours * 3600,
                        httponly=True,
                        secure=config.cookie_secure,
                        samesite="lax",
                    )
                    response.delete_cookie(LOGIN_CSRF)
                    response.headers["Cache-Control"] = "no-store"
                    return response
                error, status = "Неверный логин или пароль.", 401
    challenge = challenge or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        status_code=status,
        context={"error": error, "challenge": challenge, "demo": config.demo_mode},
    )
    response.set_cookie(
        LOGIN_CSRF, challenge, max_age=600, httponly=True, secure=config.cookie_secure, samesite="strict"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def logout(request, sessions):
    token = request.cookies.get(COOKIE, "")
    with sessions.begin() as db:
        db.execute(
            delete(WebSession).where(WebSession.token_hash == hashlib.sha256(token.encode()).hexdigest())
        )
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    response.headers["Cache-Control"] = "no-store"
    return response
