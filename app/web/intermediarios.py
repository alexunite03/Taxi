"""Cuenta de intermediario: hoteles, restaurantes y conserjerías que piden
taxis para sus clientes. Sus solicitudes van a la bolsa identificadas con el
nombre del establecimiento."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.antifraude import limitar_por_ip
from app.api.deps import email_sender, parsear_fecha_recogida, proveedores, telegram_sender
from app.db import get_db
from app.models import Intermediario, SolicitudViaje
from app.routing import Lugar
from app.security import hash_password, verify_password
from app.services.bolsa import ErrorBolsa, crear_solicitud
from app.services.cotizaciones import ErrorCotizacion

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


def intermediario_sesion(request: Request, db: Session) -> Intermediario | None:
    ident = request.session.get("intermediario_id")
    if not ident:
        return None
    try:
        return db.get(Intermediario, uuid.UUID(ident))
    except ValueError:
        return None


@router.get("/registro/intermediario", response_class=HTMLResponse)
def registro_form(request: Request):
    return templates.TemplateResponse(
        request, "registro_intermediario.html", {"error": None, "valores": {}}
    )


@router.post("/registro/intermediario", response_class=HTMLResponse)
def registro(
    request: Request,
    nombre: str = Form(...),
    contacto: str = Form(""),
    telefono: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    direccion: str = Form(""),
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    valores = {"nombre": nombre, "contacto": contacto, "telefono": telefono,
               "email": email, "direccion": direccion}

    def error(mensaje: str):
        return templates.TemplateResponse(
            request, "registro_intermediario.html",
            {"error": mensaje, "valores": valores}, status_code=422,
        )

    email = email.strip().lower()
    if len(password) < 8:
        return error("La contraseña necesita al menos 8 caracteres")
    if not re.fullmatch(r"[+0-9 ]{9,20}", telefono.strip()):
        return error("El teléfono no parece válido")
    if db.execute(select(Intermediario.id).where(Intermediario.email == email)).first():
        return error("Ya existe una cuenta con ese email")

    inter = Intermediario(
        nombre=nombre.strip(),
        contacto=contacto.strip(),
        telefono=telefono.strip().replace(" ", ""),
        email=email,
        password_hash=hash_password(password),
        direccion_texto=direccion.strip(),
    )
    db.add(inter)
    db.commit()
    db.refresh(inter)
    request.session.clear()
    request.session["intermediario_id"] = str(inter.id)
    return RedirectResponse("/intermediario", status_code=303)


@router.get("/intermediario/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "intermediario_login.html", {"error": None})


@router.post("/intermediario/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    inter = db.execute(
        select(Intermediario).where(Intermediario.email == email.strip().lower())
    ).scalar_one_or_none()
    if inter is None or not verify_password(password, inter.password_hash):
        return templates.TemplateResponse(
            request, "intermediario_login.html",
            {"error": "Email o contraseña incorrectos"}, status_code=401,
        )
    request.session.clear()
    request.session["intermediario_id"] = str(inter.id)
    return RedirectResponse("/intermediario", status_code=303)


@router.post("/intermediario/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/intermediario", response_class=HTMLResponse)
def panel(request: Request, db: Session = Depends(get_db)):
    inter = intermediario_sesion(request, db)
    if inter is None:
        return RedirectResponse("/intermediario/login", status_code=303)
    solicitudes = (
        db.execute(
            select(SolicitudViaje)
            .where(SolicitudViaje.intermediario_id == inter.id)
            .order_by(SolicitudViaje.creada_en.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request, "intermediario_panel.html",
        {"inter": inter, "solicitudes": solicitudes, "error": None, "valores": {}},
    )


@router.post("/intermediario/pedir", response_class=HTMLResponse)
def pedir_taxi(
    request: Request,
    cliente_nombre: str = Form(...),
    cliente_telefono: str = Form(...),
    origen: str = Form(...),
    destino: str = Form(...),
    fecha_hora: str = Form(...),
    origen_lat: str | None = Form(None),
    origen_lng: str | None = Form(None),
    destino_lat: str | None = Form(None),
    destino_lng: str | None = Form(None),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
    sender=Depends(email_sender),
    telegram=Depends(telegram_sender),
):
    limitar_por_ip(request)
    inter = intermediario_sesion(request, db)
    if inter is None:
        return RedirectResponse("/intermediario/login", status_code=303)
    geocoder, rutas = provs

    def lugar(texto, lat, lng):
        try:
            return Lugar(texto=texto.strip(), lat=float(lat), lng=float(lng))
        except (TypeError, ValueError):
            return None

    try:
        solicitud = crear_solicitud(
            db, geocoder, rutas,
            cliente_nombre, cliente_telefono, None,
            origen, destino,
            parsear_fecha_recogida(fecha_hora),
            origen_lugar=lugar(origen, origen_lat, origen_lng),
            destino_lugar=lugar(destino, destino_lat, destino_lng),
            intermediario_id=inter.id,
        )
    except (ErrorCotizacion, ErrorBolsa) as e:
        solicitudes = (
            db.execute(
                select(SolicitudViaje)
                .where(SolicitudViaje.intermediario_id == inter.id)
                .order_by(SolicitudViaje.creada_en.desc())
                .limit(50)
            ).scalars().all()
        )
        return templates.TemplateResponse(
            request, "intermediario_panel.html",
            {"inter": inter, "solicitudes": solicitudes, "error": str(e),
             "valores": {"cliente_nombre": cliente_nombre,
                         "cliente_telefono": cliente_telefono,
                         "origen": origen, "destino": destino,
                         "fecha_hora": fecha_hora}},
        )
    from app.services.notificaciones import avisar_bolsa_nueva_solicitud

    avisar_bolsa_nueva_solicitud(db, sender, telegram, solicitud)
    return RedirectResponse("/intermediario", status_code=303)
