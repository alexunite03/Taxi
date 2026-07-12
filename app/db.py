from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _crear_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        ruta = url.removeprefix("sqlite:///")
        if ruta and ruta != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, **kwargs)


engine = _crear_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registra las tablas)

    Base.metadata.create_all(engine)
