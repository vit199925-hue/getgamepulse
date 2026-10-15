from datetime import datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(250), unique=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    cover: Mapped[str | None] = mapped_column(Text)
    developer: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    video: Mapped[str | None] = mapped_column(Text)
    genres: Mapped[list] = mapped_column(JSON, default=list)
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    reviews: Mapped[dict] = mapped_column(JSON, default=dict)
    summaries: Mapped[dict] = mapped_column(JSON, default=dict)
    letsplay: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlatformRating(Base):
    __tablename__ = "platform_ratings"
    __table_args__ = (UniqueConstraint("game_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    metascore: Mapped[float | None] = mapped_column(Float)
    userscore: Mapped[float | None] = mapped_column(Float)


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(100))
    credential_tag: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DailyItem(Base):
    __tablename__ = "daily_items"
    __table_args__ = (UniqueConstraint("day", "slug"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[datetime] = mapped_column(Date, index=True)
    slug: Mapped[str] = mapped_column(String(250))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    error: Mapped[str | None] = mapped_column(Text)


class DayCursor(Base):
    __tablename__ = "day_cursors"
    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    home_done: Mapped[bool] = mapped_column(default=False)
    page: Mapped[int] = mapped_column(default=1)
    pending: Mapped[list] = mapped_column(JSON, default=list)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    selected: Mapped[int] = mapped_column(default=0)
    current: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class SchedulerLock(Base):
    __tablename__ = "scheduler_lock"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_run: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


engine = create_engine(
    settings().database_url,
    **(
        {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if settings().database_url.startswith("sqlite")
        else {"pool_pre_ping": True}
    ),
)
Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    Base.metadata.create_all(engine)
    insert = sqlite_insert if engine.dialect.name == "sqlite" else pg_insert
    with Session.begin() as db:
        db.execute(insert(SchedulerLock).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
