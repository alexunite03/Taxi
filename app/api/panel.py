"""Panel del taxista: agenda, reserva telefónica asistida, flag NO₂ y QR."""
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Justificante, Reserva, Tenant
from app.security import verify_password
from app.services.bolsa import ErrorBolsa, aceptar_solicitud, solicitudes_abiertas
from app.services.cotizaciones import DecisionPeajeRequerida, ErrorCotizacion, crear_cotizacion
from app.services.notificaciones import notificar_cancelacion, notificar_confirmacion
from app.services.reservas import ErrorReserva, aceptar_reserva

from .deps import (
    email_sender,
    parsear_fecha_recogida,
    proveedores,
    push_sender,
    tenant_sesion,
)

router = APIRouter(prefix="/panel")
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "web" / "templates"
)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "panel_login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    tenant = db.execute(select(Tenant).where(Tenant.email == email)).scalar_one_or_none()
    if tenant is None or not verify_password(password, tenant.password_hash):
        return templates.TemplateResponse(
            request, "panel_login.html",
            {"error": "Email o contraseña incorrectos"}, status_code=401,
        )
    request.session["tenant_id"] = str(tenant.id)
    return RedirectResponse("/panel", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/panel/login", status_code=303)


@router.get("", response_class=HTMLResponse)
def agenda(
    request: Request,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
):
    reservas = (
        db.execute(
            select(Reserva)
            .join(Reserva.cotizacion)
            .where(Reserva.tenant_id == tenant.id)
            .order_by(Reserva.creada_en.desc())
            .limit(100)
        )
        .scalars()
        .all()
    )
    abiertas = solicitudes_abiertas(db) if tenant.disponible_bolsa else []
    return templates.TemplateResponse(
        request,
        "panel_agenda.html",
        {"tenant": tenant, "reservas": reservas, "oferta": None, "error": None,
         "solicitudes": abiertas},
    )


@router.post("/cotizar", response_class=HTMLResponse)
def cotizar_asistida(
    request: Request,
    origen: str = Form(...),
    destino: str = Form(...),
    fecha_hora: str = Form(...),
    con_peaje: str | None = Form(None),
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
):
    geocoder, rutas = provs
    decision = {"si": True, "no": False}.get(con_peaje or "")
    error, cot = None, None
    try:
        cot = crear_cotizacion(
            db, tenant, geocoder, rutas, origen, destino,
            parsear_fecha_recogida(fecha_hora), con_peaje=decision,
        )
        db.commit()
    except DecisionPeajeRequerida as e:
        error = (
            f"La ruta más rápida incluye un peaje de {e.importe} €. Pregunta al "
            "cliente y reenvía marcando con o sin peaje."
        )
    except ErrorCotizacion as e:
        error = str(e)
    reservas = (
        db.execute(
            select(Reserva).where(Reserva.tenant_id == tenant.id)
            .order_by(Reserva.creada_en.desc()).limit(100)
        ).scalars().all()
    )
    abiertas = solicitudes_abiertas(db) if tenant.disponible_bolsa else []
    return templates.TemplateResponse(
        request,
        "panel_agenda.html",
        {"tenant": tenant, "reservas": reservas, "oferta": cot, "error": error,
         "solicitudes": abiertas},
    )


@router.post("/reservas")
def reservar_asistida(
    request: Request,
    cotizacion_id: str = Form(...),
    nombre: str = Form(...),
    telefono: str = Form(...),
    email: str | None = Form(None),
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
):
    try:
        reserva = aceptar_reserva(
            db, tenant, cotizacion_id, nombre.strip(), telefono.strip(),
            email or None, canal="telefono_asistida",
        )
    except ErrorReserva as e:
        raise HTTPException(422, str(e))
    notificar_confirmacion(db, sender, reserva)
    return RedirectResponse("/panel", status_code=303)


@router.post("/reservas/{reserva_id}/estado")
def cambiar_estado(
    reserva_id: uuid.UUID,
    estado: str = Form(...),
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    push=Depends(push_sender),
):
    if estado not in ("completada", "cancelada", "recordada"):
        raise HTTPException(422, "Estado no válido")
    reserva = db.get(Reserva, reserva_id)
    if reserva is None or reserva.tenant_id != tenant.id:
        raise HTTPException(404, "Reserva no encontrada")
    avisar = estado == "cancelada" and reserva.estado != "cancelada"
    reserva.estado = estado
    db.commit()
    if avisar:
        notificar_cancelacion(db, sender, push, reserva)
    return RedirectResponse("/panel", status_code=303)


@router.post("/contaminacion")
def toggle_contaminacion(
    tenant: Tenant = Depends(tenant_sesion), db: Session = Depends(get_db)
):
    tenant.flag_contaminacion = not tenant.flag_contaminacion
    db.add(tenant)
    db.commit()
    return RedirectResponse("/panel", status_code=303)


@router.get("/qr")
def qr(tenant: Tenant = Depends(tenant_sesion)):
    import segno

    enlace = f"{settings.base_url}/t/{tenant.slug}"
    buf = io.BytesIO()
    segno.make(enlace, error="q").save(buf, kind="png", scale=10, border=2)
    return Response(
        buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="qr-{tenant.slug}.png"'},
    )


@router.get("/reservas/{reserva_id}/justificante")
def descargar_justificante(
    reserva_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
):
    j = db.execute(
        select(Justificante).where(
            Justificante.reserva_id == reserva_id, Justificante.tenant_id == tenant.id
        )
    ).scalar_one_or_none()
    if j is None:
        raise HTTPException(404, "Justificante no encontrado")
    if j.pdf_path:
        return FileResponse(j.pdf_path, media_type="application/pdf")
    return FileResponse(j.html_path, media_type="text/html")


@router.post("/bolsa")
def toggle_bolsa(
    tenant: Tenant = Depends(tenant_sesion), db: Session = Depends(get_db)
):
    tenant.disponible_bolsa = not tenant.disponible_bolsa
    db.add(tenant)
    db.commit()
    return RedirectResponse("/panel", status_code=303)


@router.post("/solicitudes/{solicitud_id}/aceptar")
def aceptar_viaje(
    solicitud_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
    sender=Depends(email_sender),
):
    geocoder, rutas = provs
    try:
        solicitud, reserva = aceptar_solicitud(db, tenant, solicitud_id, geocoder, rutas)
    except (ErrorBolsa, ErrorCotizacion, ErrorReserva) as e:
        raise HTTPException(422, str(e))
    from app.services.notificaciones import notificar_confirmacion

    notificar_confirmacion(db, sender, reserva)
    return RedirectResponse("/panel", status_code=303)
