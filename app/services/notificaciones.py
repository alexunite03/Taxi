"""Notificaciones por email: confirmación, cancelación y recordatorio.

Regla de diseño del plan (§5): la reserva nunca depende de que llegue una
notificación. Todo envío va en try/except y deja rastro en `notificaciones`
con estado `enviada` o `fallida`; el enlace /r/{token} es la fuente de verdad.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Notificacion, PushSuscripcion, Reserva
from app.notificaciones import (
    Adjunto,
    Email,
    EmailSender,
    MensajePush,
    PushSender,
    SuscripcionCaducada,
)
from app.pricing.motor import TZ_MADRID

from .justificantes import _env

logger = logging.getLogger("taxi.notificaciones")


def _enlace(reserva: Reserva) -> str:
    return f"{settings.base_url}/r/{reserva.token_publico}"


def _render_email(plantilla: str, reserva: Reserva) -> str:
    return _env.get_template(plantilla).render(
        reserva=reserva,
        cotizacion=reserva.cotizacion,
        cliente=reserva.cliente,
        tenant=reserva.tenant,
        enlace=_enlace(reserva),
    )


def _adjunto_justificante(reserva: Reserva) -> list[Adjunto]:
    j = reserva.justificante
    if j is None:
        return []
    from .justificantes import asegurar_archivo

    if j.pdf_path and Path(j.pdf_path).exists():
        ruta = Path(j.pdf_path)
    else:
        try:
            ruta = asegurar_archivo(j)  # el disco del PaaS es efímero
        except Exception:
            logger.exception("No se pudo recuperar el justificante %s", j.id)
            return []
    return [Adjunto(nombre=f"justificante-{j.serie}-{j.numero:06d}{ruta.suffix}",
                    contenido=ruta.read_bytes())]


def _enviar(
    db: Session,
    sender: EmailSender,
    reserva: Reserva,
    tipo: str,
    asunto: str,
    plantilla: str,
    con_justificante: bool = False,
) -> Notificacion | None:
    if not reserva.cliente.email:
        return None
    email = Email(
        para=reserva.cliente.email,
        asunto=asunto,
        html=_render_email(plantilla, reserva),
        adjuntos=_adjunto_justificante(reserva) if con_justificante else [],
    )
    notificacion = Notificacion(reserva_id=reserva.id, canal="email", tipo=tipo)
    try:
        sender.enviar(email)
        notificacion.estado = "enviada"
        notificacion.enviada_en = datetime.now(timezone.utc)
    except Exception:
        logger.exception("Fallo enviando email %s de la reserva %s", tipo, reserva.id)
        notificacion.estado = "fallida"
    db.add(notificacion)
    db.commit()
    return notificacion


def _enviar_push(
    db: Session,
    push_sender: PushSender,
    reserva: Reserva,
    tipo: str,
    mensaje: MensajePush,
) -> int:
    """Envía a todas las suscripciones del cliente; borra las caducadas.
    Devuelve cuántos envíos salieron bien."""
    suscripciones = (
        db.execute(
            select(PushSuscripcion).where(PushSuscripcion.cliente_id == reserva.cliente_id)
        )
        .scalars()
        .all()
    )
    correctos = 0
    for s in suscripciones:
        notificacion = Notificacion(reserva_id=reserva.id, canal="push", tipo=tipo)
        try:
            push_sender.enviar({"endpoint": s.endpoint, "keys": s.claves}, mensaje)
            notificacion.estado = "enviada"
            notificacion.enviada_en = datetime.now(timezone.utc)
            correctos += 1
        except SuscripcionCaducada:
            db.delete(s)
            notificacion.estado = "caducada"
        except Exception:
            logger.exception("Fallo enviando push %s de la reserva %s", tipo, reserva.id)
            notificacion.estado = "fallida"
        db.add(notificacion)
    db.commit()
    return correctos


def notificar_confirmacion(db: Session, sender: EmailSender, reserva: Reserva):
    j = reserva.justificante
    return _enviar(
        db, sender, reserva,
        tipo="confirmacion",
        asunto=f"Reserva confirmada · justificante {j.serie}-{j.numero:06d}",
        plantilla="email_confirmacion.html",
        con_justificante=True,
    )


def notificar_cancelacion(
    db: Session, sender: EmailSender, push_sender: PushSender, reserva: Reserva
):
    _enviar_push(
        db, push_sender, reserva,
        tipo="cancelacion",
        mensaje=MensajePush(
            titulo="Reserva cancelada",
            cuerpo=f"{reserva.cotizacion.origen_texto} → {reserva.cotizacion.destino_texto}",
            url=_enlace(reserva),
        ),
    )
    return _enviar(
        db, sender, reserva,
        tipo="cancelacion",
        asunto="Tu reserva de taxi ha sido cancelada",
        plantilla="email_cancelacion.html",
    )


def hoja_de_ruta(solicitud, reserva=None) -> str:
    """Resumen del viaje para el taxista (Telegram/email). Con `reserva`,
    muestra el precio cerrado definitivo en vez del máximo."""
    if reserva is not None:
        precio = f"💶 Precio cerrado: {reserva.precio_cerrado} € (IVA incluido)"
    else:
        precio = f"💶 Precio máximo: {solicitud.precio_estimado} € (IVA incluido)"
    lineas = [
        f"🕐 {solicitud.fecha_hora_recogida.strftime('%d/%m/%Y %H:%M')}",
        f"🟢 Recogida: {solicitud.origen_texto}",
        f"🏁 Destino: {solicitud.destino_texto}",
        precio,
        f"👤 {solicitud.nombre} · {solicitud.telefono}",
    ]
    if solicitud.intermediario is not None:
        lineas.append(f"🏨 Pedido por {solicitud.intermediario.nombre}")
    return "\n".join(lineas)


def _pdf_hoja(solicitud, reserva=None) -> list[Adjunto]:
    """Hoja de ruta en PDF para adjuntar; si falla, el aviso sale sin ella."""
    try:
        from .hoja_ruta_pdf import pdf_hoja_de_ruta

        return [Adjunto(nombre="hoja-de-ruta.pdf",
                        contenido=pdf_hoja_de_ruta(solicitud, reserva))]
    except Exception:
        logger.exception("No se pudo generar el PDF de la hoja de ruta")
        return []


def _telegram_con_hoja(
    telegram, chat_id, encabezado, solicitud,
    botones=None, reserva=None, adjuntos=None,
) -> None:
    """Envía por Telegram la hoja de ruta como PDF con el texto y los
    botones; si el documento falla, degrada a mensaje de texto. `adjuntos`
    admite un PDF ya generado (avisos en lote: un solo render)."""
    texto = f"{encabezado}\n\n{hoja_de_ruta(solicitud, reserva)}"
    if adjuntos is None:
        adjuntos = _pdf_hoja(solicitud, reserva)
    if adjuntos:
        try:
            telegram.enviar_documento(
                chat_id, adjuntos[0].nombre, adjuntos[0].contenido,
                caption=texto, botones=botones,
            )
            return
        except Exception:
            logger.exception("Fallo enviando el PDF por Telegram a %s", chat_id)
    telegram.enviar(chat_id, texto, botones=botones)


def _precio_con_descuento(solicitud, pct: int) -> str:
    from decimal import ROUND_HALF_UP, Decimal

    base = Decimal(str(solicitud.precio_estimado))
    con_dto = base * (Decimal(100 - pct) / 100)
    # Redondeo comercial a 0,05 €, como el motor
    return str((con_dto / Decimal("0.05")).quantize(0, ROUND_HALF_UP) * Decimal("0.05"))


def botones_solicitud(solicitud) -> list:
    """Menú principal del aviso: aceptar, ajustar el precio, ver la
    recogida en el mapa y rechazar (o abrir la bolsa)."""
    sid = str(solicitud.id)
    mapa = (f"https://www.google.com/maps/search/?api=1&query="
            f"{solicitud.origen_lat},{solicitud.origen_lng}")
    ultima = ([{"texto": "🗺 Recogida en el mapa", "url": mapa},
               {"texto": "❌ Rechazar", "datos": f"sol:{sid}:r"}]
              if solicitud.tenant_destino_id else
              [{"texto": "🗺 Recogida en el mapa", "url": mapa},
               {"texto": "🌐 Ver bolsa", "url": f"{settings.base_url}/panel/bolsa"}])
    return [
        [{"texto": f"✅ Aceptar · {solicitud.precio_estimado} €",
          "datos": f"sol:{sid}:a:0"}],
        [{"texto": "💶 Ajustar el precio…", "datos": f"sol:{sid}:m"}],
        ultima,
    ]


def botones_precio(solicitud) -> list:
    """Submenú de precio: descuentos con el importe resultante ya calculado
    y opción de escribir un precio exacto."""
    sid = str(solicitud.id)
    def boton(pct):
        return {"texto": f"−{pct} % · {_precio_con_descuento(solicitud, pct)} €",
                "datos": f"sol:{sid}:a:{pct}"}
    return [
        [boton(5), boton(10)],
        [boton(15), boton(20)],
        [boton(25), boton(30)],
        [{"texto": "✏️ Escribir otro precio", "datos": f"sol:{sid}:p"}],
        [{"texto": "⬅ Volver", "datos": f"sol:{sid}:v"}],
    ]


def _nota_distancia(tenant, solicitud) -> str:
    """Línea de ubicación para el aviso: a qué distancia está la recogida
    de la posición del taxista, o cómo activar el dato si no la comparte."""
    if tenant.ubicacion_lat is None or tenant.ubicacion_lng is None:
        return ("📍 Comparte tu ubicación con este chat y te diré a qué "
                "distancia tienes cada recogida.")
    from app.services.bolsa import distancia_km

    d = distancia_km(tenant.ubicacion_lat, tenant.ubicacion_lng,
                     solicitud.origen_lat, solicitud.origen_lng)
    return f"📍 Recogida a {d:.1f} km de tu posición."


def notificar_solicitud_directa(db: Session, sender, telegram, solicitud):
    """Reserva directa: el taxista destinatario recibe la hoja de ruta con
    el menú para aceptar (ajustando el precio si quiere) o rechazar."""
    tenant = solicitud.tenant_destino
    if tenant is None:
        return
    hoja = hoja_de_ruta(solicitud)
    try:
        sender.enviar(Email(
            para=tenant.email,
            asunto=f"Reserva pendiente: {solicitud.fecha_hora_recogida.strftime('%d/%m %H:%M')}",
            html=("<p>Tienes una reserva pendiente de aceptar:</p>"
                  f"<pre>{hoja}</pre>"
                  f"<p><a href='{settings.base_url}/panel/bolsa'>Aceptar o rechazar en tu panel</a></p>"),
            adjuntos=_pdf_hoja(solicitud),
        ))
    except Exception:
        logger.exception("Fallo avisando reserva directa por email a %s", tenant.id)
    if tenant.telegram_chat_id:
        try:
            _telegram_con_hoja(
                telegram, tenant.telegram_chat_id,
                "🚕 Reserva nueva pendiente de tu confirmación\n"
                + _nota_distancia(tenant, solicitud),
                solicitud, botones=botones_solicitud(solicitud),
            )
        except Exception:
            logger.exception("Fallo avisando reserva directa por Telegram a %s", tenant.id)


def notificar_hoja_de_ruta_taxista(db: Session, sender, telegram, solicitud, reserva):
    """Tras aceptar: el taxista recibe la hoja de ruta definitiva (precio
    cerrado y justificante) en PDF por email y por Telegram."""
    tenant = reserva.tenant
    hoja = hoja_de_ruta(solicitud, reserva)
    j = reserva.justificante
    numero = f" · justificante {j.serie}-{j.numero:06d}" if j else ""
    try:
        sender.enviar(Email(
            para=tenant.email,
            asunto=f"Hoja de ruta: {solicitud.fecha_hora_recogida.strftime('%d/%m %H:%M')}{numero}",
            html=(f"<p>Reserva confirmada:</p><pre>{hoja}</pre>"
                  f"<p><a href='{settings.base_url}/r/{reserva.token_publico}'>Ver la reserva</a></p>"),
            adjuntos=_pdf_hoja(solicitud, reserva),
        ))
    except Exception:
        logger.exception("Fallo enviando la hoja de ruta por email a %s", tenant.id)
    if tenant.telegram_chat_id:
        try:
            _telegram_con_hoja(
                telegram, tenant.telegram_chat_id,
                f"✅ Reserva confirmada{numero}\n{settings.base_url}/r/{reserva.token_publico}",
                solicitud, reserva=reserva,
            )
        except Exception:
            logger.exception("Fallo enviando la hoja de ruta por Telegram a %s", tenant.id)


def notificar_rechazo_pasajero(db: Session, sender, solicitud):
    if not solicitud.email:
        return
    try:
        sender.enviar(Email(
            para=solicitud.email,
            asunto="Tu solicitud de taxi no ha podido ser atendida",
            html=(f"<p>Hola {solicitud.nombre}: el taxista no puede atender tu "
                  f"viaje del {solicitud.fecha_hora_recogida.strftime('%d/%m %H:%M')}. "
                  f"Puedes buscar otro taxista disponible aquí: "
                  f"<a href='{settings.base_url}/viaje'>{settings.base_url}/viaje</a></p>"),
        ))
    except Exception:
        logger.exception("Fallo avisando rechazo al pasajero %s", solicitud.id)


# --- Envío en segundo plano (la petición del pasajero no espera al SMTP) ---

def tarea_solicitud_directa(solicitud_id, sender, telegram):
    from app.db import SessionLocal
    from app.models import SolicitudViaje

    with SessionLocal() as db:
        solicitud = db.get(SolicitudViaje, solicitud_id)
        if solicitud is not None:
            notificar_solicitud_directa(db, sender, telegram, solicitud)


def tarea_avisar_bolsa(solicitud_id, sender, telegram):
    from app.db import SessionLocal
    from app.models import SolicitudViaje

    with SessionLocal() as db:
        solicitud = db.get(SolicitudViaje, solicitud_id)
        if solicitud is not None:
            avisar_bolsa_nueva_solicitud(db, sender, telegram, solicitud)


def avisar_bolsa_nueva_solicitud(db: Session, sender, telegram, solicitud) -> int:
    """Avisa a los taxistas con la bolsa activada de que hay un viaje nuevo
    (email siempre, Telegram si tienen chat vinculado). Devuelve cuántos
    avisos salieron."""
    from app.models import Tenant

    disponibles = (
        db.execute(
            select(Tenant).where(
                Tenant.disponible_bolsa.is_(True),
                Tenant.estado_suscripcion == "activa",
            )
        )
        .scalars()
        .all()
    )
    base = (
        f"Viaje nuevo en la bolsa: {solicitud.fecha_hora_recogida.strftime('%d/%m %H:%M')} · "
        f"{solicitud.origen_texto} → {solicitud.destino_texto} · "
        f"estimado {solicitud.precio_estimado} €"
    )
    from app.services.bolsa import distancia_km, radio_de

    # El PDF y los botones son iguales para todos: se generan una sola vez
    adjuntos = _pdf_hoja(solicitud)
    botones = botones_solicitud(solicitud)

    enviados = 0
    for t in disponibles:
        # Modo Uber: si el taxista compartió su ubicación por Telegram, solo
        # se le avisa de los viajes con recogida dentro de su radio.
        texto = base
        if t.ubicacion_lat is not None and t.ubicacion_lng is not None:
            d = distancia_km(
                t.ubicacion_lat, t.ubicacion_lng,
                solicitud.origen_lat, solicitud.origen_lng,
            )
            if d > radio_de(t):
                continue
            texto = f"{base} · a {d:.1f} km de ti"
        try:
            sender.enviar(Email(
                para=t.email,
                asunto="Viaje nuevo en la bolsa",
                html=f"<p>{texto}</p><p><a href='{settings.base_url}/panel/bolsa'>Ver la bolsa</a></p>",
                adjuntos=adjuntos,
            ))
            enviados += 1
        except Exception:
            logger.exception("Fallo avisando bolsa por email a %s", t.id)
        if t.telegram_chat_id:
            try:
                _telegram_con_hoja(
                    telegram, t.telegram_chat_id, f"🚕 {texto}",
                    solicitud, botones=botones, adjuntos=adjuntos,
                )
                enviados += 1
            except Exception:
                logger.exception("Fallo avisando bolsa por Telegram a %s", t.id)
    return enviados


def suscribir_push(db: Session, reserva: Reserva, suscripcion: dict) -> PushSuscripcion:
    """Alta (o refresco) de una PushSubscription del navegador para el
    cliente de la reserva. Idempotente por endpoint."""
    endpoint = suscripcion.get("endpoint") or ""
    claves = suscripcion.get("keys") or {}
    if not endpoint.startswith("https://") or "p256dh" not in claves or "auth" not in claves:
        raise ValueError("Suscripción push no válida")

    existente = db.execute(
        select(PushSuscripcion).where(
            PushSuscripcion.cliente_id == reserva.cliente_id,
            PushSuscripcion.endpoint == endpoint,
        )
    ).scalar_one_or_none()
    if existente is not None:
        existente.claves = claves
        db.commit()
        return existente

    alta = PushSuscripcion(cliente_id=reserva.cliente_id, endpoint=endpoint, claves=claves)
    db.add(alta)
    db.commit()
    return alta


def _recogida_utc(reserva: Reserva) -> datetime:
    """SQLite devuelve la hora de recogida naive (hora de Madrid)."""
    dt = reserva.cotizacion.fecha_hora_recogida
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_MADRID)
    return dt.astimezone(timezone.utc)


def enviar_recordatorios(
    db: Session, sender: EmailSender, push_sender: PushSender
) -> int:
    """Envía el recordatorio T-{recordatorio_min} de las reservas aceptadas
    y las pasa a estado 'recordada'. Pensado para ejecutarse por cron
    (`python -m app.jobs recordatorios`). Devuelve cuántos envió."""
    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(minutes=settings.recordatorio_min)

    candidatas = (
        db.execute(select(Reserva).where(Reserva.estado == "aceptada"))
        .scalars()
        .all()
    )
    enviados = 0
    for reserva in candidatas:
        recogida = _recogida_utc(reserva)
        if not (ahora <= recogida <= limite):
            continue
        _enviar_push(
            db, push_sender, reserva,
            tipo="recordatorio",
            mensaje=MensajePush(
                titulo="Tu taxi llega pronto",
                cuerpo=(
                    f"{recogida.astimezone(TZ_MADRID).strftime('%H:%M')} · "
                    f"{reserva.cotizacion.origen_texto} · {reserva.precio_cerrado} €"
                ),
                url=_enlace(reserva),
            ),
        )
        _enviar(
            db, sender, reserva,
            tipo="recordatorio",
            asunto="Recordatorio: tu taxi llega pronto",
            plantilla="email_recordatorio.html",
        )
        # Se marca aunque el cliente no tenga email: el recordatorio no se
        # reintenta en cada pasada y la agenda del taxista es el respaldo.
        reserva.estado = "recordada"
        db.commit()
        enviados += 1
    return enviados
