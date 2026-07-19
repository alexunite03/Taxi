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
    ruta = Path(j.pdf_path or j.html_path)
    if not ruta.exists():
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


def notificar_taxista_reserva(db: Session, sender, telegram, reserva: Reserva):
    """Avisa al taxista de una reserva nueva por email y, si tiene chat
    vinculado, por Telegram. Nunca bloquea la reserva."""
    tenant = reserva.tenant
    cot = reserva.cotizacion
    texto = (
        f"Nueva reserva {cot.fecha_hora_recogida.strftime('%d/%m %H:%M')} · "
        f"{cot.origen_texto} → {cot.destino_texto} · "
        f"{reserva.precio_cerrado} € · {reserva.cliente.nombre} "
        f"({reserva.cliente.telefono})"
    )
    notificacion = Notificacion(reserva_id=reserva.id, canal="email", tipo="aviso_taxista")
    try:
        sender.enviar(Email(
            para=tenant.email,
            asunto=f"Nueva reserva: {cot.fecha_hora_recogida.strftime('%d/%m %H:%M')}",
            html=f"<p>{texto}</p><p><a href='{settings.base_url}/panel'>Abrir mi agenda</a></p>",
        ))
        notificacion.estado = "enviada"
        notificacion.enviada_en = datetime.now(timezone.utc)
    except Exception:
        logger.exception("Fallo avisando por email al taxista %s", tenant.id)
        notificacion.estado = "fallida"
    db.add(notificacion)

    if tenant.telegram_chat_id:
        notif_tg = Notificacion(reserva_id=reserva.id, canal="telegram", tipo="aviso_taxista")
        try:
            telegram.enviar(tenant.telegram_chat_id, f"🚕 {texto}")
            notif_tg.estado = "enviada"
            notif_tg.enviada_en = datetime.now(timezone.utc)
        except Exception:
            logger.exception("Fallo avisando por Telegram al taxista %s", tenant.id)
            notif_tg.estado = "fallida"
        db.add(notif_tg)
    db.commit()


def hoja_de_ruta(solicitud) -> str:
    """Resumen del viaje para el taxista (Telegram/email)."""
    lineas = [
        f"🕐 {solicitud.fecha_hora_recogida.strftime('%d/%m/%Y %H:%M')}",
        f"🟢 Recogida: {solicitud.origen_texto}",
        f"🏁 Destino: {solicitud.destino_texto}",
        f"💶 Precio máximo: {solicitud.precio_estimado} € (IVA incluido)",
        f"👤 {solicitud.nombre} · {solicitud.telefono}",
    ]
    if solicitud.intermediario is not None:
        lineas.append(f"🏨 Pedido por {solicitud.intermediario.nombre}")
    return "\n".join(lineas)


def _botones_solicitud(solicitud) -> list:
    sid = str(solicitud.id)
    return [
        [{"texto": f"✅ Aceptar · {solicitud.precio_estimado} €",
          "datos": f"sol:{sid}:a:0"}],
        [{"texto": "💶 Aceptar con −5 %", "datos": f"sol:{sid}:a:5"},
         {"texto": "💶 −10 %", "datos": f"sol:{sid}:a:10"}],
        [{"texto": "❌ Rechazar", "datos": f"sol:{sid}:r"}]
        if solicitud.tenant_destino_id else
        [{"texto": "🌐 Ver bolsa", "url": f"{settings.base_url}/panel/bolsa"}],
    ]


def notificar_solicitud_directa(db: Session, sender, telegram, solicitud):
    """Reserva directa: el taxista destinatario recibe la hoja de ruta con
    botones para aceptar (con o sin descuento) o rechazar."""
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
                  f"<p><a href='{settings.base_url}/panel'>Aceptar o rechazar en tu panel</a></p>"),
        ))
    except Exception:
        logger.exception("Fallo avisando reserva directa por email a %s", tenant.id)
    if tenant.telegram_chat_id:
        try:
            telegram.enviar(
                tenant.telegram_chat_id,
                f"🚕 Reserva nueva pendiente de tu confirmación\n\n{hoja}",
                botones=_botones_solicitud(solicitud),
            )
        except Exception:
            logger.exception("Fallo avisando reserva directa por Telegram a %s", tenant.id)


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
            ))
            enviados += 1
        except Exception:
            logger.exception("Fallo avisando bolsa por email a %s", t.id)
        if t.telegram_chat_id:
            try:
                telegram.enviar(
                    t.telegram_chat_id,
                    f"🚕 {texto}\n\n{hoja_de_ruta(solicitud)}",
                    botones=_botones_solicitud(solicitud),
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
