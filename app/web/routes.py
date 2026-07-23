"""Páginas HTML del pasajero: formulario de reserva, oferta y justificante.

Sin JavaScript obligatorio: formularios clásicos con render en servidor.
Progresivamente mejorable con HTMX en fase 2 sin tocar los servicios.
"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
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
    ServicioExcluido,
    _geocodificar,
    crear_cotizacion,
)
from app.services.bolsa import ErrorBolsa, solicitar_reserva_directa
from app.services.notificaciones import (
    notificar_cancelacion,
    notificar_cancelacion_taxista,
    tarea_solicitud_directa,
)
from app.services.reservas import (
    ErrorReserva,
    cancelar_reserva,
    reserva_por_token,
)

from app.api.deps import (
    email_sender,
    telegram_sender,
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
def formulario(
    request: Request,
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
):
    from app.web.bolsa import es_favorito
    from app.web.cuentas import usuario_sesion

    usuario = usuario_sesion(request, db)
    return templates.TemplateResponse(
        request,
        "t_form.html",
        {"tenant": tenant, "valores": {}, "error": None,
         "usuario": usuario, "es_favorito": es_favorito(db, usuario, tenant)},
    )


@router.get("/terminos", response_class=HTMLResponse)
def terminos(request: Request):
    return templates.TemplateResponse(request, "terminos.html", {})


@router.get("/quejas", response_class=HTMLResponse)
def quejas_form(request: Request):
    return templates.TemplateResponse(
        request, "quejas.html", {"enviada": False, "error": None}
    )


@router.post("/quejas", response_class=HTMLResponse)
def quejas_enviar(
    request: Request,
    nombre: str = Form(...),
    texto: str = Form(...),
    email: str | None = Form(None),
    referencia: str | None = Form(None),
    website: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Quejas y reclamaciones (ORT art. 47) y notificaciones DSA."""
    from app.models import Queja

    limitar_por_ip(request)
    comprobar_honeypot(website)
    if len(texto.strip()) < 10:
        return templates.TemplateResponse(
            request, "quejas.html",
            {"enviada": False, "error": "Cuéntanos qué ha pasado con un poco más de detalle."},
            status_code=422,
        )
    db.add(Queja(
        nombre=nombre.strip()[:120],
        email=(email or "").strip().lower()[:120] or None,
        texto=texto.strip()[:4000],
        referencia=(referencia or "").strip()[:64] or None,
    ))
    db.commit()
    return templates.TemplateResponse(
        request, "quejas.html", {"enviada": True, "error": None}
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
    modo: str = Form("precio_cerrado"),
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
    valores = {"origen": origen, "destino": destino, "fecha_hora": fecha_hora,
               "modo": modo}
    geocoder, rutas = provs
    decision_peaje = {"si": True, "no": False}.get(con_peaje or "")

    if modo == "taximetro":
        # Sin precio cerrado: solo se resuelven las direcciones (el
        # aeropuerto está permitido: pagará su tarifa fija a bordo) y se
        # pide confirmación con los datos de contacto.
        from app.web.cuentas import usuario_sesion

        try:
            lugar_origen = (Lugar.opcional(origen, origen_lat, origen_lng)
                            or _geocodificar(geocoder, "origen", origen))
            lugar_destino = (Lugar.opcional(destino, destino_lat, destino_lng)
                             or _geocodificar(geocoder, "destino", destino))
            recogida = parsear_fecha_recogida(fecha_hora)
        except ErrorCotizacion as e:
            return templates.TemplateResponse(
                request, "t_form.html",
                {"tenant": tenant, "valores": valores, "error": str(e)},
            )
        return templates.TemplateResponse(
            request, "t_taximetro.html",
            {"tenant": tenant, "origen": lugar_origen, "destino": lugar_destino,
             "recogida": recogida, "fecha_hora": fecha_hora, "error": None,
             "usuario": usuario_sesion(request, db)},
        )

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
            origen_lugar=Lugar.opcional(origen, origen_lat, origen_lng),
            destino_lugar=Lugar.opcional(destino, destino_lat, destino_lng),
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
    except ServicioExcluido as e:
        return templates.TemplateResponse(
            request, "t_form.html",
            {"tenant": tenant, "valores": valores, "error": str(e),
             "sugerir_taximetro": True},
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
    background: BackgroundTasks,
    cotizacion_id: str = Form(...),
    nombre: str = Form(...),
    telefono: str = Form(...),
    email: str | None = Form(None),
    website: str | None = Form(None),
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    telegram=Depends(telegram_sender),
):
    """El pasajero solicita la reserva al precio máximo de la oferta; la
    reserva y el justificante se crean cuando el taxista acepta (panel o
    Telegram). El aviso al taxista sale en segundo plano."""
    limitar_por_ip(request)
    comprobar_honeypot(website)
    try:
        solicitud = solicitar_reserva_directa(
            db, tenant, cotizacion_id, nombre.strip(), telefono.strip(), email or None
        )
    except (ErrorBolsa, ErrorCotizacion) as e:
        return templates.TemplateResponse(
            request, "t_form.html",
            {"tenant": tenant, "valores": {}, "error": str(e)},
        )
    background.add_task(tarea_solicitud_directa, solicitud.id, sender, telegram)
    return RedirectResponse(f"/s/{solicitud.token_publico}", status_code=303)


@router.post("/t/{slug}/reservar-taximetro", response_class=HTMLResponse)
def reservar_taximetro(
    request: Request,
    background: BackgroundTasks,
    origen_texto: str = Form(...),
    origen_lat: float = Form(...),
    origen_lng: float = Form(...),
    destino_texto: str = Form(...),
    destino_lat: float = Form(...),
    destino_lng: float = Form(...),
    fecha_hora: str = Form(...),
    nombre: str = Form(...),
    telefono: str = Form(...),
    email: str | None = Form(None),
    website: str | None = Form(None),
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    telegram=Depends(telegram_sender),
):
    """Reserva SIN precio cerrado: el importe lo marcará el taxímetro a
    bordo (o la tarifa fija oficial en aeropuerto). La plataforma no
    interviene en el precio; el taxista solo acepta o rechaza."""
    from decimal import Decimal

    from app.services.bolsa import crear_solicitud

    limitar_por_ip(request)
    comprobar_honeypot(website)
    try:
        solicitud = crear_solicitud(
            db, None, None,
            nombre=nombre.strip(),
            telefono=telefono.strip(),
            email=email or None,
            origen_texto=origen_texto,
            destino_texto=destino_texto,
            fecha_hora_recogida=parsear_fecha_recogida(fecha_hora),
            origen_lugar=Lugar(origen_texto, origen_lat, origen_lng),
            destino_lugar=Lugar(destino_texto, destino_lat, destino_lng),
            tenant_destino_id=tenant.id,
            precio_estimado=Decimal("0"),
            modo="taximetro",
        )
    except (ErrorBolsa, ErrorCotizacion) as e:
        return templates.TemplateResponse(
            request, "t_form.html",
            {"tenant": tenant, "valores": {"modo": "taximetro"}, "error": str(e)},
        )
    background.add_task(tarea_solicitud_directa, solicitud.id, sender, telegram)
    return RedirectResponse(f"/s/{solicitud.token_publico}", status_code=303)


@router.get("/r/{token}", response_class=HTMLResponse)
def ver_reserva(request: Request, token: str, db: Session = Depends(get_db)):
    reserva = reserva_por_token(db, token)
    if reserva is None:
        return HTMLResponse("<h1>Reserva no encontrada</h1>", status_code=404)
    j = reserva.justificante
    justificante_html = justificantes.render_fragment(reserva, j.serie, j.numero)
    from sqlalchemy import select as _select

    from app.models import Valoracion

    valoracion = db.execute(
        _select(Valoracion).where(Valoracion.reserva_id == reserva.id)
    ).scalar_one_or_none()
    return templates.TemplateResponse(
        request,
        "r_reserva.html",
        {"reserva": reserva, "justificante_html": justificante_html,
         "valoracion": valoracion},
    )


@router.post("/r/{token}/cancelar")
def cancelar_web(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    push=Depends(push_sender),
    telegram=Depends(telegram_sender),
):
    limitar_por_ip(request)
    reserva = reserva_por_token(db, token)
    if reserva is not None and reserva.estado != "cancelada":
        try:
            cancelar_reserva(db, reserva)
            notificar_cancelacion(db, sender, push, reserva)
            notificar_cancelacion_taxista(db, sender, telegram, reserva)
        except ErrorReserva:
            pass
    return RedirectResponse(f"/r/{token}", status_code=303)
