import uuid
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Tenant
from app.pricing.motor import TZ_MADRID
from app.routing import Geocoder, RouteProvider


def tenant_por_slug(slug: str, db: Session = Depends(get_db)) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
    if tenant is None or tenant.estado_suscripcion != "activa":
        raise HTTPException(404, "Este enlace de reserva no está disponible")
    return tenant


def proveedores(request: Request) -> tuple[Geocoder, RouteProvider]:
    return request.app.state.geocoder, request.app.state.rutas


def email_sender(request: Request):
    return request.app.state.email_sender


def push_sender(request: Request):
    return request.app.state.push_sender


def tenant_sesion(request: Request, db: Session = Depends(get_db)) -> Tenant:
    """Tenant autenticado del panel (cookie de sesión)."""
    tenant_id = request.session.get("tenant_id")
    if not tenant_id:
        raise HTTPException(401, "Sesión no iniciada", headers={"Location": "/panel/login"})
    try:
        tenant = db.get(Tenant, uuid.UUID(tenant_id))
    except ValueError:
        tenant = None
    if tenant is None:
        request.session.clear()
        raise HTTPException(401, "Sesión no válida")
    return tenant


def parsear_fecha_recogida(valor: str) -> datetime:
    """El formulario envía la hora local de Madrid (datetime-local, naive)."""
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        raise HTTPException(422, "Fecha de recogida no válida")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_MADRID)
    return dt
