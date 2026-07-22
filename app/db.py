from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _crear_engine(url: str):
    # Render/Heroku dan URLs "postgres://"; SQLAlchemy quiere el driver
    # explícito. Normalizamos para que baste con pegar la URL.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        ruta = url.removeprefix("sqlite:///")
        if ruta and ruta != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    else:
        # Conexiones sanas en despliegues gestionados
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
    return create_engine(url, **kwargs)


engine = _crear_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columnas añadidas después del primer despliegue: create_all no altera
# tablas existentes, así que se aplican aquí (mini-migración idempotente).
# Cuando el esquema se estabilice, migrar a Alembic.
_COLUMNAS_NUEVAS = [
    ("tenants", "ubicacion_lat", "FLOAT"),
    ("tenants", "ubicacion_lng", "FLOAT"),
    ("tenants", "ubicacion_en", "TIMESTAMP WITH TIME ZONE"),
    ("tenants", "radio_km", "FLOAT"),
    ("solicitudes_viaje", "tenant_destino_id", "UUID"),
    ("solicitudes_viaje", "cotizacion_id", "UUID"),
    ("justificantes", "html", "TEXT"),
    ("tenants", "verificado", "BOOLEAN"),
]


def _migrar(engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conexion:
        for tabla, columna, tipo in _COLUMNAS_NUEVAS:
            if tabla not in inspector.get_table_names():
                continue
            existentes = {c["name"] for c in inspector.get_columns(tabla)}
            if columna in existentes:
                continue
            if engine.dialect.name == "sqlite" and "TIME ZONE" in tipo:
                tipo = "TIMESTAMP"
            if engine.dialect.name == "sqlite" and tipo == "UUID":
                tipo = "CHAR(32)"
            conexion.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}"))
            print(f"Migración: añadida {tabla}.{columna}", flush=True)


def init_db() -> None:
    from . import models  # noqa: F401  (registra las tablas)

    Base.metadata.create_all(engine)
    _migrar(engine)
