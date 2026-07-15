"""Páginas HTML del pasajero: formulario de reserva, oferta y justificante.

Sin JavaScript obligatorio: formularios clásicos con render en servidor.
Progresivamente mejorable con HTMX en fase 2 sin tocar los servicios.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.antifraude import comprobar_honeypot, limitar_por_ip
from app.db import get_db
from app.models import Tenant
from app.routing import Lugar
from app.services import justificantes
from app.services.cotizaciones import (
    DecisionPeajeRequerida,
    DesambiguacionRequerida,
    ErrorCotizacion,
    crear_cotizacion,
)
from app.services.notificaciones import notificar_cancelacion, notificar_confirmacion
from app.services.reservas import (
    ErrorReserva,
    aceptar_reserva,
    cancelar_reserva,
    reserva_por_token,
)

from app.api.deps import (
    email_sender,
    parsear_fecha_recogida,
    proveedores,
    push_sender,
    tenant_por_slug,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


@router.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    return templates.TemplateResponse(request, "inicio.html", {})


def _proveedor() -> dict:
    from app.config import settings

    return {
        "nombre": settings.proveedor_nombre,
        "nif": settings.proveedor_nif,
        "domicilio": settings.proveedor_domicilio,
        "email": settings.proveedor_email,
        "registro": settings.proveedor_registro,
    }


@router.get("/t/{slug}", response_class=HTMLResponse)
def formulario(request: Request, tenant: Tenant = Depends(tenant_por_slug)):
    return templates.TemplateResponse(
        request, "t_form.html", {"tenant": tenant, "valores": {}, "error": None}
    )


@router.get("/aviso-legal", response_class=HTMLResponse)
def aviso_legal(request: Request):
    return templates.TemplateResponse(
        request, "legal_aviso.html", {"proveedor": _proveedor()}
    )


@router.get("/cookies", response_class=HTMLResponse)
def cookies(request: Request):
    return templates.TemplateResponse(
        request, "legal_cookies.html", {"proveedor": _proveedor()}
    )


@router.get("/t/{slug}/privacidad", response_class=HTMLResponse)
def privacidad(request: Request, tenant: Tenant = Depends(tenant_por_slug)):
    return templates.TemplateResponse(
        request,
        "legal_privacidad.html",
        {"tenant": tenant, "proveedor": _proveedor()},
    )


@router.post("/t/{slug}/cotizar", response_class=HTMLResponse)
def cotizar_web(
    request: Request,
    origen: str = Form(...),
    destino: str = Form(...),
    fecha_hora: str = Form(...),
    con_peaje: str | None = Form(None),  # '', 'si' o 'no'
    origen_lat: str | None = Form(None),
    origen_lng: str | None = Form(None),
    destino_lat: str | None = Form(None),
    destino_lng: str | None = Form(None),
    website: str | None = Form(None),
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
):
    limitar_por_ip(request)
    comprobar_honeypot(website)
    valores = {"origen": origen, "destino": destino, "fecha_hora": fecha_hora}
    geocoder, rutas = provs
    decision_peaje = {"si": True, "no": False}.get(con_peaje or "")

    def lugar(texto, lat, lng):
        try:
            return Lugar(texto=texto.strip(), lat=float(lat), lng=float(lng))
        except (TypeError, ValueError):
            return None

    try:
        cot = crear_cotizacion(
            db,
            tenant,
            geocoder,
            rutas,
            origen,
            destino,
            parsear_fecha_recogida(fecha_hora),
            con_peaje=decision_peaje,
            origen_lugar=lugar(origen, origen_lat, origen_lng),
            destino_lugar=lugar(destino, destino_lat, destino_lng),
        )
        db.commit()
    except DecisionPeajeRequerida as e:
        return templates.TemplateResponse(
            request,
            "t_form.html",
            {"tenant": tenant, "valores": valores, "error": None,
             "pregunta_peaje": str(e.importe)},
        )
    except DesambiguacionRequerida as e:
        mensaje = f"Hay varias coincidencias para el {e.campo}; sé más específico: " + \
            " · ".join(l.texto for l in e.opciones[:3])
        return templates.TemplateResponse(
            request, "t_form.html",
            {"tenant": tenant, "valores": valores, "error": mensaje},
        )
    except ErrorCotizacion as e:
        return templates.TemplateResponse(
            request, "t_form.html",
            {"tenant": tenant, "valores": valores, "error": str(e)},
        )
    from app.web.cuentas import usuario_sesion

    return templates.TemplateResponse(
        request,
        "t_oferta.html",
        {"tenant": tenant, "cot": cot, "error": None, "usuario": usuario_sesion(request, db)},
    )


@router.post("/t/{slug}/reservar", response_class=HTMLResponse)
def reservar_web(
    request: Request,
    cotizacion_id: str = Form(...),
    nombre: str = Form(...),
    telefono: str = Form(...),
    email: str | None = Form(None),
    website: str | None = Form(None),
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
):
    limitar_por_ip(request)
    comprobar_honeypot(website)
    try:
        reserva = aceptar_reserva(
            db, tenant, cotizacion_id, nombre.strip(), telefono.strip(), email or None
        )
    except ErrorReserva as e:
        return templates.TemplateResponse(
            request, "t_form.html",
            {"tenant": tenant, "valores": {}, "error": str(e)},
        )
    notificar_confirmacion(db, sender, reserva)
    return RedirectResponse(f"/r/{reserva.token_publico}", status_code=303)


@router.get("/r/{token}", response_class=HTMLResponse)
def ver_reserva(request: Request, token: str, db: Session = Depends(get_db)):
    reserva = reserva_por_token(db, token)
    if reserva is None:
        return HTMLResponse("<h1>Reserva no encontrada</h1>", status_code=404)
    j = reserva.justificante
    justificante_html = justificantes.render_fragment(reserva, j.serie, j.numero)
    return templates.TemplateResponse(
        request,
        "r_reserva.html",
        {"reserva": reserva, "justificante_html": justificante_html},
    )


@router.post("/r/{token}/cancelar")
def cancelar_web(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    push=Depends(push_sender),
):
    limitar_por_ip(request)
    reserva = reserva_por_token(db, token)
    if reserva is not None and reserva.estado != "cancelada":
        try:
            cancelar_reserva(db, reserva)
            notificar_cancelacion(db, sender, push, reserva)
        except ErrorReserva:
            pass
    return RedirectResponse(f"/r/{token}", status_code=303)
