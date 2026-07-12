"""Tenant de demostración para desarrollo local (TAXI_SEED_DEMO=1)."""
from sqlalchemy import select

from .db import SessionLocal
from .models import Tenant
from .security import hash_password

DEMO = {
    "slug": "demo",
    "nombre": "Taxi Demo",
    "nif": "00000000T",
    "num_licencia": "0000",
    "matricula": "0000XXX",
    "email": "demo@example.com",
}


def crear_tenant_demo(password: str = "demo1234") -> Tenant:
    with SessionLocal() as db:
        existente = db.execute(
            select(Tenant).where(Tenant.slug == DEMO["slug"])
        ).scalar_one_or_none()
        if existente:
            return existente
        tenant = Tenant(**DEMO, password_hash=hash_password(password))
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        print(f"Tenant demo creado: /t/{tenant.slug} · panel: {DEMO['email']} / {password}")
        return tenant
