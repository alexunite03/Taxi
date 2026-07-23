"""Panel del taxista: agenda, reserva telefónica asistida, flag NO₂ y QR."""
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Justificante, Reserva, Tenant
from app.security import verify_password
from app.notificaciones import Email
from app.services.bolsa import (
    ErrorBolsa,
    aceptar_solicitud,
    con_distancia,
    rechazar_solicitud,
    solicitudes_abiertas,
    solicitudes_pendientes_de,
)
from app.services.cotizaciones import DecisionPeajeRequerida, ErrorCotizacion, crear_cotizacion
from app.services.justificantes import asegurar_archivo
from app.services.notificaciones import (
    notificar_cancelacion,
    notificar_confirmacion,
    notificar_hoja_de_ruta_taxista,
    notificar_rechazo_pasajero,
)
from app.services.reservas import ErrorReserva, aceptar_reserva

from .deps import (
    email_sender,
    parsear_fecha_recogida,
    proveedores,
    push_sender,
    telegram_sender,
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


def _reservas_recientes(db: Session, tenant: Tenant) -> list[Reserva]:
    return (
        db.execute(
            select(Reserva)
            .where(Reserva.tenant_id == tenant.id)
            .order_by(Reserva.creada_en.desc())
            .limit(100)
        )
        .scalars()
        .all()
    )


@router.get("", response_class=HTMLResponse)
def agenda(
    request: Request,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
):
    reservas = _reservas_recientes(db, tenant)
    return templates.TemplateResponse(
        request,
        "panel_agenda.html",
        {"tenant": tenant, "reservas": reservas, "oferta": None, "error": None},
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
    reservas = _reservas_recientes(db, tenant)
    return templates.TemplateResponse(
        request,
        "panel_agenda.html",
        {"tenant": tenant, "reservas": reservas, "oferta": cot, "error": error},
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

    enlace = f"{settings.base_url}/t/{tenant.slug}/perfil"
    buf = io.BytesIO()
    segno.make(enlace, error="q").save(buf, kind="png", scale=10, border=2)
    return Response(
        buf.getvalue(),
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="qr-{tenant.slug}.png"',
            "X-QR-Target": enlace,
        },
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
    if j.pdf_path and Path(j.pdf_path).exists():
        return FileResponse(j.pdf_path, media_type="application/pdf")
    return FileResponse(asegurar_archivo(j), media_type="text/html")


@router.get("/bolsa", response_class=HTMLResponse)
def bolsa_pagina(
    request: Request,
    lat: float | None = None,
    lng: float | None = None,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
):
    solicitudes = solicitudes_abiertas(db) if tenant.disponible_bolsa else []
    if solicitudes and lat is not None and lng is not None:
        solicitudes = con_distancia(solicitudes, lat, lng)
    # Marca los viajes a los que este taxista ya ha ofertado
    from app.models import OfertaViaje

    mias = {
        o.solicitud_id: o
        for o in db.execute(
            select(OfertaViaje).where(OfertaViaje.tenant_id == tenant.id)
        ).scalars()
    }
    for s in solicitudes:
        s.mi_oferta = mias.get(s.id)
    return templates.TemplateResponse(
        request,
        "panel_bolsa.html",
        {"tenant": tenant, "solicitudes": solicitudes,
         "pendientes": solicitudes_pendientes_de(db, tenant),
         "con_ubicacion": lat is not None and lng is not None},
    )


def _estado_canales(request: Request) -> list[dict]:
    """Qué proveedor está activo por canal, para verlo desde el panel sin
    tener que bucear en los logs del servidor."""
    filas = []
    for canal, sender in (("email", request.app.state.email_sender),
                          ("telegram", request.app.state.telegram_sender)):
        clase = type(sender).__name__
        filas.append({"canal": canal, "proveedor": clase,
                      "real": not clase.startswith("Console")})
    return filas


@router.get("/perfil", response_class=HTMLResponse)
def perfil_form(
    request: Request,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
):
    import secrets as _secrets

    from app.web.perfiles import resumen_valoraciones

    # Código de vinculación de Telegram listo para el enlace t.me
    if not tenant.telegram_chat_id and not tenant.telegram_codigo:
        tenant.telegram_codigo = _secrets.token_urlsafe(8)
        db.add(tenant)
        db.commit()

    media, total = resumen_valoraciones(db, tenant.id)
    return templates.TemplateResponse(
        request, "panel_perfil.html",
        {"tenant": tenant, "media": media, "total": total, "error": None,
         "telegram_bot": settings.telegram_bot_username,
         "canales": _estado_canales(request), "resultados": None},
    )


@router.post("/avisos/probar", response_class=HTMLResponse)
def probar_avisos(
    request: Request,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    telegram=Depends(telegram_sender),
):
    """Envía un email y un Telegram de prueba AHORA y muestra el resultado
    (o el error exacto del proveedor) en pantalla."""
    from app.web.perfiles import resumen_valoraciones

    resultados = []

    clase = type(sender).__name__
    if clase.startswith("Console"):
        resultados.append({"canal": "email", "ok": False, "detalle": (
            "el canal está en modo consola: no envía emails reales. Configura "
            "TAXI_EMAIL_PROVIDER=smtp con TAXI_SMTP_USER y TAXI_SMTP_PASSWORD "
            "(contraseña de aplicación de Google) y redespliega.")})
    else:
        try:
            sender.enviar(Email(
                para=tenant.email,
                asunto="Prueba de avisos · TaxiMad",
                html="<p>Si estás leyendo esto, el canal de email funciona ✔</p>",
            ))
            resultados.append({"canal": "email", "ok": True,
                               "detalle": f"enviado a {tenant.email} con {clase}. Revisa tu buzón (y el spam)."})
        except Exception as e:
            resultados.append({"canal": "email", "ok": False,
                               "detalle": f"{clase} falló: {type(e).__name__}: {e}"})

    clase_tg = type(telegram).__name__
    if not tenant.telegram_chat_id:
        resultados.append({"canal": "telegram", "ok": False,
                           "detalle": "no tienes el chat vinculado (botón «Vincular Telegram»)."})
    elif clase_tg.startswith("Console"):
        resultados.append({"canal": "telegram", "ok": False, "detalle": (
            "el canal está en modo consola: configura TAXI_TELEGRAM_PROVIDER=telegram "
            "y TAXI_TELEGRAM_BOT_TOKEN y redespliega.")})
    else:
        try:
            telegram.enviar(tenant.telegram_chat_id,
                            "✅ Prueba de avisos: si lees esto, Telegram funciona.")
            resultados.append({"canal": "telegram", "ok": True,
                               "detalle": "mensaje enviado a tu chat."})
        except Exception as e:
            resultados.append({"canal": "telegram", "ok": False,
                               "detalle": f"{clase_tg} falló: {type(e).__name__}: {e}"})

    media, total = resumen_valoraciones(db, tenant.id)
    return templates.TemplateResponse(
        request, "panel_perfil.html",
        {"tenant": tenant, "media": media, "total": total, "error": None,
         "telegram_bot": settings.telegram_bot_username,
         "canales": _estado_canales(request), "resultados": resultados},
    )


@router.post("/perfil")
async def perfil_guardar(
    request: Request,
    bio: str = Form(""),
    descuento_pct: int = Form(0),
    recogida_eur: float = Form(5.0),
    radio_km: float = Form(15.0),
    telegram_chat_id: str = Form(""),
    foto: UploadFile | None = None,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
):
    from app.web.perfiles import resumen_valoraciones

    error = None
    tenant.bio = bio.strip()[:500]
    if not (0 <= descuento_pct <= 30):
        error = "El descuento debe estar entre 0 y 30 %"
    elif not (0 <= recogida_eur <= 5):
        error = "La recogida debe estar entre 0 y 5 €"
    elif not (1 <= radio_km <= 100):
        error = "El radio de la bolsa debe estar entre 1 y 100 km"
    else:
        tenant.descuento_pct = descuento_pct
        tenant.recogida_eur = round(recogida_eur, 2)
        tenant.radio_km = round(radio_km, 1)
    tenant.telegram_chat_id = telegram_chat_id.strip()[:32] or None
    if foto is not None and foto.filename:
        contenido = await foto.read()
        if foto.content_type not in ("image/jpeg", "image/png"):
            error = "La foto debe ser JPG o PNG"
        elif len(contenido) > 2 * 1024 * 1024:
            error = "La foto no puede superar los 2 MB"
        else:
            settings.fotos_dir.mkdir(parents=True, exist_ok=True)
            ext = ".png" if foto.content_type == "image/png" else ".jpg"
            ruta = settings.fotos_dir / f"{tenant.id}{ext}"
            ruta.write_bytes(contenido)
            tenant.foto_path = str(ruta)
    db.add(tenant)
    db.commit()
    if error:
        media, total = resumen_valoraciones(db, tenant.id)
        return templates.TemplateResponse(
            request, "panel_perfil.html",
            {"tenant": tenant, "media": media, "total": total, "error": error,
             "telegram_bot": settings.telegram_bot_username,
             "canales": _estado_canales(request), "resultados": None},
            status_code=422,
        )
    return RedirectResponse("/panel/perfil", status_code=303)


@router.post("/bolsa")
def toggle_bolsa(
    tenant: Tenant = Depends(tenant_sesion), db: Session = Depends(get_db)
):
    tenant.disponible_bolsa = not tenant.disponible_bolsa
    db.add(tenant)
    db.commit()
    return RedirectResponse("/panel/bolsa", status_code=303)


@router.post("/solicitudes/{solicitud_id}/aceptar")
def aceptar_viaje(
    solicitud_id: uuid.UUID,
    descuento_pct: int = Form(0),
    recogida_eur: float = Form(5.0),
    precio_final: str = Form(""),
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
    sender=Depends(email_sender),
    telegram=Depends(telegram_sender),
):
    if not (0 <= descuento_pct <= 30 and 0 <= recogida_eur <= 5):
        raise HTTPException(422, "Ajuste de precio fuera de los límites (0–30 %, 0–5 €)")
    precio_pactado = None
    if precio_final.strip():
        from decimal import Decimal, InvalidOperation

        try:
            precio_pactado = Decimal(precio_final.replace("€", "").replace(",", ".").strip())
        except InvalidOperation:
            raise HTTPException(422, "El precio exacto no es un importe válido")
    geocoder, rutas = provs
    try:
        solicitud, reserva = aceptar_solicitud(
            db, tenant, solicitud_id, geocoder, rutas,
            descuento_pct=descuento_pct, recogida_eur=recogida_eur,
            precio_pactado=precio_pactado,
        )
    except (ErrorBolsa, ErrorCotizacion, ErrorReserva) as e:
        raise HTTPException(422, str(e))
    if reserva is None:  # modo taxímetro: se confirma la propia solicitud
        from app.services.notificaciones import notificar_confirmacion_taximetro

        notificar_confirmacion_taximetro(db, sender, solicitud)
    else:
        notificar_confirmacion(db, sender, reserva)
    notificar_hoja_de_ruta_taxista(db, sender, telegram, solicitud, reserva)
    return RedirectResponse("/panel/bolsa", status_code=303)


@router.post("/solicitudes/{solicitud_id}/ofertar")
def ofertar_viaje(
    solicitud_id: uuid.UUID,
    descuento_pct: int = Form(0),
    recogida_eur: float = Form(5.0),
    precio_final: str = Form(""),
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
    sender=Depends(email_sender),
):
    """Bolsa (modelo neutro): el taxista se postula con su precio y el
    pasajero elige entre las ofertas."""
    from app.services.bolsa import ofertar
    from app.services.notificaciones import notificar_oferta_pasajero

    if not (0 <= descuento_pct <= 30 and 0 <= recogida_eur <= 5):
        raise HTTPException(422, "Ajuste de precio fuera de los límites (0–30 %, 0–5 €)")
    precio_pactado = None
    if precio_final.strip():
        from decimal import Decimal, InvalidOperation

        try:
            precio_pactado = Decimal(precio_final.replace("€", "").replace(",", ".").strip())
        except InvalidOperation:
            raise HTTPException(422, "El precio exacto no es un importe válido")
    _, rutas = provs
    try:
        oferta = ofertar(
            db, tenant, solicitud_id, rutas,
            descuento_pct=descuento_pct, recogida_eur=recogida_eur,
            precio_pactado=precio_pactado,
        )
    except (ErrorBolsa, ErrorCotizacion) as e:
        raise HTTPException(422, str(e))
    from app.models import SolicitudViaje

    solicitud = db.get(SolicitudViaje, oferta.solicitud_id)
    notificar_oferta_pasajero(db, sender, solicitud, oferta)
    return RedirectResponse("/panel/bolsa", status_code=303)


@router.post("/solicitudes/{solicitud_id}/rechazar")
def rechazar_viaje(
    solicitud_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_sesion),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
):
    try:
        solicitud = rechazar_solicitud(db, tenant, solicitud_id)
    except ErrorBolsa as e:
        raise HTTPException(422, str(e))
    notificar_rechazo_pasajero(db, sender, solicitud)
    return RedirectResponse("/panel/bolsa", status_code=303)
