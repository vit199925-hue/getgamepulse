import asyncio
import hmac
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text

from app import auth
from app.config import settings
from app.db import Game, PlatformRating, Run, SchedulerLock, Session, init_db, utcnow
from app.jobs import RunQueue

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app):
    Path(settings().cover_directory).mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title="GamePulse", version="0.1.0", lifespan=lifespan)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.mount("/media/covers", StaticFiles(directory=settings().cover_directory, check_dir=False), name="covers")
templates = Jinja2Templates(directory=HERE / "templates")


@app.middleware("http")
async def authentication(request: Request, call_next):
    return await auth.guard(request, call_next, settings(), Session)


@app.get("/login", response_class=HTMLResponse)
@app.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    return await auth.login(request, settings(), Session, templates)


@app.post("/logout")
def logout(request: Request):
    return auth.logout(request, Session)


def safe_url(value):
    if value and re.fullmatch(r"/(?:static|media)/covers/[a-z0-9-]+\.(?:svg|png|jpg|webp)", value):
        return value
    return value if value and urlparse(value).scheme in ("https", "http") else ""


templates.env.filters["safeurl"] = safe_url


def db_session():
    with Session() as db:
        yield db


def game_dict(game):
    return {
        "id": game.id,
        "slug": game.slug,
        "title": game.title,
        "cover": game.cover,
        "developer": game.developer,
        "description": game.description,
        "source_url": game.source_url,
        "platforms": game.platforms,
        "genres": game.genres,
        "video": game.video,
        "updated_at": game.updated_at.isoformat(),
        "summaries": game.summaries,
        "letsplay": game.letsplay,
    }


def list_games(db, q="", platform="", sort="metascore", page=1):
    rating = PlatformRating.userscore if sort == "userscore" else PlatformRating.metascore
    ratings = select(PlatformRating.game_id, func.max(rating).label("rating"))
    if platform:
        ratings = ratings.where(PlatformRating.name == platform)
    ratings = ratings.group_by(PlatformRating.game_id).subquery()
    statement = select(Game).outerjoin(ratings, ratings.c.game_id == Game.id)
    if platform:
        statement = statement.where(ratings.c.game_id.is_not(None))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(Game.title.ilike(f"%{escaped}%", escape="\\"))
    count = db.scalar(select(func.count()).select_from(statement.subquery()))
    ordering = Game.updated_at.desc() if sort == "updated" else ratings.c.rating.desc().nulls_last()
    rows = db.scalars(statement.order_by(ordering, Game.id).offset((page - 1) * 20).limit(20)).all()
    return rows, count


def similar_games(db, game):
    # Genres/platforms/developer provide an explainable baseline without external embedding costs.
    candidates = db.scalars(select(Game).where(Game.id != game.id)).all()
    genres, platforms = set(game.genres), {p["name"] for p in game.platforms}

    def rank(other):
        other_genres = set(other.genres)
        union = genres | other_genres
        genre_score = len(genres & other_genres) / len(union) if union else 0
        return (
            genre_score * 0.7
            + bool(platforms & {p["name"] for p in other.platforms}) * 0.2
            + bool(game.developer and game.developer == other.developer) * 0.1
        )

    ranked = sorted(((rank(x), x) for x in candidates), key=lambda x: (-x[0], x[1].id))
    return [x for score, x in ranked[:6] if score > 0]


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = Query("", max_length=200),
    platform: str = "",
    sort: str = Query("metascore", pattern="^(metascore|userscore|updated)$"),
    page: int = Query(1, ge=1),
    db=Depends(db_session),
):
    games, total = list_games(db, q, platform, sort, page)
    platforms = db.scalars(select(PlatformRating.name).distinct().order_by(PlatformRating.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "games": games,
            "total": total,
            "platforms": platforms,
            "q": q,
            "platform": platform,
            "sort": sort,
            "page": page,
            "demo": settings().demo_mode,
        },
    )


@app.get("/games/{slug}", response_class=HTMLResponse)
def detail(slug: str, request: Request, db=Depends(db_session)):
    game = db.scalar(select(Game).where(Game.slug == slug))
    if game is None:
        raise HTTPException(404, "Game not found")
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={"game": game, "similar": similar_games(db, game), "demo": settings().demo_mode},
    )


@app.get("/monitor", response_class=HTMLResponse)
def monitor(request: Request):
    return templates.TemplateResponse(
        request=request, name="monitor.html", context={"demo": settings().demo_mode}
    )


@app.get("/api/games")
def api_games(
    q: str = Query("", max_length=200),
    platform: str = "",
    sort: str = Query("metascore", pattern="^(metascore|userscore|updated)$"),
    page: int = Query(1, ge=1),
    db=Depends(db_session),
):
    games, total = list_games(db, q, platform, sort, page)
    return {"items": [game_dict(g) for g in games], "total": total, "page": page, "page_size": 20}


@app.get("/api/games/{slug}")
def api_detail(slug: str, db=Depends(db_session)):
    game = db.scalar(select(Game).where(Game.slug == slug))
    if not game:
        raise HTTPException(404, "Game not found")
    return dict(
        game_dict(game),
        reviews=game.reviews,
        similar=[{"slug": x.slug, "title": x.title} for x in similar_games(db, game)],
    )


@app.post("/api/runs", status_code=202)
def force_run(request: Request, authorization: str = Header("")):
    token = settings().admin_token
    if not token and not request.state.username:
        raise HTTPException(503, "Set ADMIN_TOKEN to enable manual runs")
    if not request.state.username and not hmac.compare_digest(
        authorization.encode(), f"Bearer {token}".encode()
    ):
        raise HTTPException(401, "Invalid admin token")
    run_id = RunQueue(Session, settings()).enqueue()
    if run_id is None:
        raise HTTPException(409, "A worker is already processing a run")
    return {"run_id": run_id}


def snapshot():
    with Session() as db:
        runs = db.scalars(select(Run).order_by(Run.id.desc()).limit(20)).all()
        lock = db.get(SchedulerLock, 1)
        return {
            "time": utcnow().isoformat(),
            "total_games": db.scalar(select(func.count(Game.id))),
            "worker": {
                "owner": lock.owner,
                "lease_expires": lock.expires_at.isoformat(),
                "next_run": lock.next_run.isoformat(),
            },
            "runs": [
                {
                    "id": r.id,
                    "status": r.status,
                    "trigger": r.trigger,
                    "processed": r.processed,
                    "failed": r.failed,
                    "selected": r.selected,
                    "current": r.current,
                    "error": r.error,
                    "started_at": r.started_at.isoformat(),
                    "heartbeat": r.heartbeat.isoformat() if r.heartbeat else None,
                }
                for r in runs
            ],
        }


@app.get("/api/status")
def status():
    return snapshot()


@app.get("/api/events")
async def events(request: Request):
    async def stream():
        while not await request.is_disconnected():
            state = await asyncio.to_thread(snapshot)
            yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health(db=Depends(db_session)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "demo": settings().demo_mode}
