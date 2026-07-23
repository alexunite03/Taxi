"""Herramientas del titular de la plataforma, protegidas por token
(`TAXI_ADMIN_TOKEN`): verificación de taxistas (DSA art. 30) y exportación
de datos del art. 47 ORT. Sin token configurado, los endpoints no existen.

Pensado para usarse desde el navegador del titular:
  /api/admin/pendientes?token=...
  /api/admin/verificar?token=...&slug=...
  /api/admin/export/art47.csv?token=...&conjunto=servicios&desde=2026-01-01
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Queja, Reserva, SolicitudViaje, Tenant

router = APIRouter(prefix="/api/admin")


def _autorizar(token: str) -> None:
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(404, "No encontrado")


def _rango(desde: str, hasta: str) -> tuple[datetime, datetime]:
    try:
        d = date.fromisoformat(desde) if desde else date(2000, 1, 1)
        h = date.fromisoformat(hasta) if hasta else date(2100, 1, 1)
    except ValueError:
        raise HTTPException(422, "Fechas en formato AAAA-MM-DD")
    return (datetime.combine(d, time.min, tzinfo=timezone.utc),
            datetime.combine(h, time.max, tzinfo=timezone.utc))


def _en_rango(dt: datetime, d: datetime, h: datetime) -> bool:
    if dt.tzinfo is None:  # SQLite pierde el tzinfo (UTC)
        dt = dt.replace(tzinfo=timezone.utc)
    return d <= dt <= h


@router.get("/pendientes")
def pendientes(token: str = "", db: Session = Depends(get_db)):
    """Taxistas registrados a la espera de verificación (no listados)."""
    _autorizar(token)
    filas = db.execute(
        select(Tenant).where(Tenant.verificado.is_(False))
        .order_by(Tenant.fecha_alta)
    ).scalars().all()
    return {"pendientes": [
        {"slug": t.slug, "nombre": t.nombre, "nif": t.nif,
         "num_licencia": t.num_licencia, "matricula": t.matricula,
         "email": t.email, "alta": t.fecha_alta.isoformat()}
        for t in filas
    ], "como_verificar": "/api/admin/verificar?token=…&slug=<slug>"}


@router.get("/verificar")
def verificar(token: str = "", slug: str = "", db: Session = Depends(get_db)):
    """Marca al taxista como verificado (identidad y licencia comprobadas
    por el titular) y lo publica en el listado."""
    _autorizar(token)
    tenant = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(404, "Taxista no encontrado")
    tenant.verificado = True
    db.commit()
    return {"ok": True, "slug": tenant.slug, "verificado": True,
            "perfil": f"{settings.base_url}/t/{tenant.slug}/perfil"}


@router.get("/export/art47.csv")
def export_art47(
    token: str = "",
    conjunto: str = "servicios",
    desde: str = "",
    hasta: str = "",
    db: Session = Depends(get_db),
):
    """Exportación del art. 47 ORT por rango de fechas:
    `servicios` (reservas), `demandas` (solicitudes no atendidas) o
    `quejas`."""
    _autorizar(token)
    d, h = _rango(desde, hasta)
    buf = io.StringIO()
    w = csv.writer(buf)

    if conjunto == "servicios":
        w.writerow(["creada", "recogida", "origen", "destino", "precio_eur",
                    "estado", "canal", "taxista", "licencia", "justificante"])
        for r in db.execute(select(Reserva)).scalars():
            if not _en_rango(r.creada_en, d, h):
                continue
            j = r.justificante
            w.writerow([
                r.creada_en.isoformat(), r.cotizacion.fecha_hora_recogida.isoformat(),
                r.cotizacion.origen_texto, r.cotizacion.destino_texto,
                r.precio_cerrado, r.estado, r.canal,
                r.tenant.nombre, r.tenant.num_licencia,
                f"{j.serie}-{j.numero:06d}" if j else "",
            ])
        # Servicios con taxímetro: se confirma la solicitud, sin reserva
        for s in db.execute(
            select(SolicitudViaje).where(
                SolicitudViaje.estado == "asignada",
                SolicitudViaje.reserva_id.is_(None),
            )
        ).scalars():
            if not _en_rango(s.creada_en, d, h):
                continue
            t = s.tenant_destino
            w.writerow([
                s.creada_en.isoformat(), s.fecha_hora_recogida.isoformat(),
                s.origen_texto, s.destino_texto,
                "taximetro", "confirmada", "web_taximetro",
                t.nombre if t else "", t.num_licencia if t else "", "",
            ])
    elif conjunto == "demandas":
        w.writerow(["creada", "recogida", "origen", "destino",
                    "precio_estimado_eur", "estado", "dirigida_a_taxista"])
        for s in db.execute(select(SolicitudViaje)).scalars():
            if s.estado == "asignada" or not _en_rango(s.creada_en, d, h):
                continue  # demandas NO atendidas: todo lo no asignado
            w.writerow([
                s.creada_en.isoformat(), s.fecha_hora_recogida.isoformat(),
                s.origen_texto, s.destino_texto, s.precio_estimado, s.estado,
                s.tenant_destino.nombre if s.tenant_destino else "",
            ])
    elif conjunto == "quejas":
        w.writerow(["creada", "nombre", "email", "referencia", "estado", "texto"])
        for q in db.execute(select(Queja).order_by(Queja.creada_en)).scalars():
            if not _en_rango(q.creada_en, d, h):
                continue
            w.writerow([q.creada_en.isoformat(), q.nombre, q.email or "",
                        q.referencia or "", q.estado, q.texto])
    else:
        raise HTTPException(422, "conjunto debe ser servicios, demandas o quejas")

    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="art47-{conjunto}.csv"'},
    )
