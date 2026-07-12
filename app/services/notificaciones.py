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
