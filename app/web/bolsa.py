"""Páginas de la bolsa de viajes (pasajero) y de taxistas favoritos."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.antifraude import comprobar_honeypot, limitar_por_ip
from app.api.deps import parsear_fecha_recogida, proveedores, tenant_por_slug
from app.db import get_db
from app.models import Favorito, Tenant
from app.routing import Lugar
from app.services.bolsa import (
    ErrorBolsa,
    crear_solicitud,
    solicitud_por_token,
)
from app.services.cotizaciones import ErrorCotizacion

from .cuentas import usuario_sesion

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


def _lugar(texto, lat, lng) -> Lugar | None:
    try:
        return Lugar(texto=texto.strip(), lat=float(lat), lng=float(lng))
    except (TypeError, ValueError, AttributeError):
        return None


# --- Bolsa de viajes (pasajero) -------------------------------------------


@router.get("/viaje", response_class=HTMLResponse)
def viaje_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "viaje_form.html",
        {"valores": {}, "error": None, "usuario": usuario_sesion(request, db)},
    )


@router.post("/viaje", response_class=HTMLResponse)
def viaje_solicitar(
    request: Request,
    origen: str = Form(...),
    destino: str = Form(...),
    fecha_hora: str = Form(...),
    nombre: str = Form(...),
    telefono: str = Form(...),
    email: str | None = Form(None),
    origen_lat: str | None = Form(None),
    origen_lng: str | None = Form(None),
    destino_lat: str | None = Form(None),
    destino_lng: str | None = Form(None),
    website: str | None = Form(None),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
):
    limitar_por_ip(request)
    comprobar_honeypot(website)
    geocoder, rutas = provs
    valores = {
        "origen": origen, "destino": destino, "fecha_hora": fecha_hora,
        "nombre": nombre, "telefono": telefono, "email": email,
    }
    try:
        solicitud = crear_solicitud(
            db, geocoder, rutas,
            nombre, telefono, email,
            origen, destino,
            parsear_fecha_recogida(fecha_hora),
            origen_lugar=_lugar(origen, origen_lat, origen_lng),
            destino_lugar=_lugar(destino, destino_lat, destino_lng),
        )
    except (ErrorCotizacion, ErrorBolsa) as e:
        return templates.TemplateResponse(
            request, "viaje_form.html",
            {"valores": valores, "error": str(e),
             "usuario": usuario_sesion(request, db)},
        )
    return RedirectResponse(f"/s/{solicitud.token_publico}", status_code=303)


@router.get("/s/{token}", response_class=HTMLResponse)
def ver_solicitud(request: Request, token: str, db: Session = Depends(get_db)):
    solicitud = solicitud_por_token(db, token)
    if solicitud is None:
        return HTMLResponse("<h1>Solicitud no encontrada</h1>", status_code=404)
    if solicitud.estado == "asignada" and solicitud.reserva is not None:
        return RedirectResponse(f"/r/{solicitud.reserva.token_publico}", status_code=303)
    return templates.TemplateResponse(
        request, "s_solicitud.html", {"solicitud": solicitud}
    )


@router.post("/s/{token}/cancelar")
def cancelar_solicitud(request: Request, token: str, db: Session = Depends(get_db)):
    limitar_por_ip(request)
    solicitud = solicitud_por_token(db, token)
    if solicitud is not None and solicitud.estado == "abierta":
        solicitud.estado = "cancelada"
        db.commit()
    return RedirectResponse(f"/s/{token}", status_code=303)


# --- Favoritos (pasajero registrado) ---------------------------------------


@router.post("/favoritos/{slug}")
def toggle_favorito(
    request: Request,
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
):
    usuario = usuario_sesion(request, db)
    if usuario is None:
        return RedirectResponse("/usuario/login", status_code=303)
    existente = db.execute(
        select(Favorito).where(
            Favorito.usuario_id == usuario.id, Favorito.tenant_id == tenant.id
        )
    ).scalar_one_or_none()
    if existente is not None:
        db.delete(existente)
    else:
        db.add(Favorito(usuario_id=usuario.id, tenant_id=tenant.id))
    db.commit()
    destino = request.headers.get("referer") or f"/t/{tenant.slug}"
    return RedirectResponse(destino, status_code=303)


def es_favorito(db: Session, usuario, tenant) -> bool:
    if usuario is None:
        return False
    return (
        db.execute(
            select(Favorito.id).where(
                Favorito.usuario_id == usuario.id, Favorito.tenant_id == tenant.id
            )
        ).first()
        is not None
    )


def favoritos_de(db: Session, usuario) -> list[Favorito]:
    return (
        db.execute(
            select(Favorito)
            .where(Favorito.usuario_id == usuario.id)
            .order_by(Favorito.creado_en)
        )
        .scalars()
        .all()
    )
