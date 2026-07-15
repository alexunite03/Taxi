import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="taxi-tests-")
os.environ["TAXI_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["TAXI_JUSTIFICANTES_DIR"] = f"{_tmp}/justificantes"
os.environ["TAXI_SEED_DEMO"] = "0"
os.environ["TAXI_RATE_LIMIT_POR_IP_HORA"] = "1000"
os.environ["TAXI_ROUTE_PROVIDER"] = "fake"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Tenant
from app.security import hash_password


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as sesion:
        yield sesion


@pytest.fixture()
def tenant(db):
    t = Tenant(
        slug="demo",
        nombre="Taxi Demo",
        nif="00000000T",
        num_licencia="1234",
        matricula="0000XXX",
        email="demo@example.com",
        password_hash=hash_password("demo1234"),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture()
def client(tenant):
    with TestClient(app) as c:
        yield c
