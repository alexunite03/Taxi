"""Registro y cuentas: alta de taxistas (tenants) y cuenta opcional del
pasajero.

El registro del taxista sustituye al alta manual del onboarding: crea su
tenant y entra directo al panel. La cuenta del pasajero es opcional
(reservar sigue sin exigir registro, plan §3): sirve para consultar sus
reservas, asociadas por teléfono.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.antifraude import limitar_por_ip
from app.db import get_db
from app.models import ClienteFinal, Reserva, Tenant, Usuario
from app.security import hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


def _slugify(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    plano = re.sub(r"[^a-z0-9]+", "-", plano.lower()).strip("-")
    return plano or "taxista"


def _slug_libre(db: Session, base: str) -> str:
    slug, n = base, 1
    while db.execute(select(Tenant.id).where(Tenant.slug == slug)).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def usuario_sesion(request: Request, db: Session) -> Usuario | None:
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        return None
    try:
        return db.get(Usuario, uuid.UUID(usuario_id))
    except ValueError:
        return None


# --- Elección -----------------------------------------------------------


@router.get("/registro", response_class=HTMLResponse)
def elegir(request: Request):
    return templates.TemplateResponse(request, "registro_elegir.html", {})


# --- Registro de taxista --------------------------------------------------


@router.get("/registro/taxista", response_class=HTMLResponse)
def registro_taxista_form(request: Request):
    return templates.TemplateResponse(
        request, "registro_taxista.html", {"error": None, "valores": {}}
    )


@router.post("/registro/taxista", response_class=HTMLResponse)
def registro_taxista(
    request: Request,
    nombre: str = Form(...),
    nif: str = Form(...),
    num_licencia: str = Form(...),
    matricula: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    valores = {
        "nombre": nombre, "nif": nif, "num_licencia": num_licencia,
        "matricula": matricula, "email": email,
    }

    def error(mensaje: str):
        return templates.TemplateResponse(
            request, "registro_taxista.html",
            {"error": mensaje, "valores": valores}, status_code=422,
        )

    nombre, email = nombre.strip(), email.strip().lower()
    if len(password) < 8:
        return error("La contraseña necesita al menos 8 caracteres")
    if not re.fullmatch(r"\d{1,5}", num_licencia.strip()):
        return error("El número de licencia debe ser numérico")
    if db.execute(select(Tenant.id).where(Tenant.email == email)).first():
        return error("Ya existe una cuenta de taxista con ese email")

    tenant = Tenant(
        slug=_slug_libre(db, _slugify(nombre)),
        nombre=nombre,
        nif=nif.strip().upper(),
        num_licencia=num_licencia.strip(),
        matricula=matricula.strip().upper(),
        email=email,
        password_hash=hash_password(password),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    request.session.clear()
    request.session["tenant_id"] = str(tenant.id)
    return RedirectResponse("/panel", status_code=303)


# --- Cuenta del pasajero --------------------------------------------------


@router.get("/registro/usuario", response_class=HTMLResponse)
def registro_usuario_form(request: Request):
    return templates.TemplateResponse(
        request, "registro_usuario.html", {"error": None, "valores": {}}
    )


@router.post("/registro/usuario", response_class=HTMLResponse)
def registro_usuario(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    valores = {"nombre": nombre, "telefono": telefono, "email": email}

    def error(mensaje: str):
        return templates.TemplateResponse(
            request, "registro_usuario.html",
            {"error": mensaje, "valores": valores}, status_code=422,
        )

    nombre, telefono = nombre.strip(), telefono.strip()
    email = email.strip().lower()
    if len(password) < 8:
        return error("La contraseña necesita al menos 8 caracteres")
    if not re.fullmatch(r"[+0-9 ]{9,20}", telefono):
        return error("El teléfono no parece válido")
    if db.execute(select(Usuario.id).where(Usuario.email == email)).first():
        return error("Ya existe una cuenta con ese email")

    usuario = Usuario(
        nombre=nombre,
        telefono=telefono.replace(" ", ""),
        email=email,
        password_hash=hash_password(password),
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    request.session.clear()
    request.session["usuario_id"] = str(usuario.id)
    return RedirectResponse("/mis-reservas", status_code=303)


@router.get("/usuario/login", response_class=HTMLResponse)
def usuario_login_form(request: Request):
    return templates.TemplateResponse(request, "usuario_login.html", {"error": None})


@router.post("/usuario/login")
def usuario_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    usuario = db.execute(
        select(Usuario).where(Usuario.email == email.strip().lower())
    ).scalar_one_or_none()
    if usuario is None or not verify_password(password, usuario.password_hash):
        return templates.TemplateResponse(
            request, "usuario_login.html",
            {"error": "Email o contraseña incorrectos"}, status_code=401,
        )
    request.session.clear()
    request.session["usuario_id"] = str(usuario.id)
    return RedirectResponse("/mis-reservas", status_code=303)


@router.post("/usuario/logout")
def usuario_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/mis-reservas", response_class=HTMLResponse)
def mis_reservas(request: Request, db: Session = Depends(get_db)):
    usuario = usuario_sesion(request, db)
    if usuario is None:
        return RedirectResponse("/usuario/login", status_code=303)
    reservas = (
        db.execute(
            select(Reserva)
            .join(ClienteFinal, Reserva.cliente_id == ClienteFinal.id)
            .where(ClienteFinal.telefono == usuario.telefono)
            .order_by(Reserva.creada_en.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request, "mis_reservas.html", {"usuario": usuario, "reservas": reservas}
    )
