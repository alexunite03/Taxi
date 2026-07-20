"""Tareas periódicas por HTTP (planes sin cron, como Render free).

Un cron externo (p. ej. cron-job.org, gratuito) llama a
`GET /api/cron?token=<TAXI_CRON_TOKEN>` cada 5 minutos. Cada llamada:
- envía los recordatorios previos a la recogida,
- caduca las reservas directas sin respuesta del taxista y avisa a sus
  pasajeros para que puedan enviarlas a la bolsa,
- y, de propina, mantiene despierta la instancia (sin arranque en frío).

Sin TAXI_CRON_TOKEN configurado el endpoint no existe (404).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services.bolsa import caducar_solicitudes_directas
from app.services.notificaciones import (
    enviar_recordatorios,
    notificar_caducidad_pasajero,
)

from .deps import email_sender, push_sender

router = APIRouter(prefix="/api")


@router.get("/cron")
def cron(
    token: str = "",
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    push=Depends(push_sender),
):
    if not settings.cron_token or token != settings.cron_token:
        raise HTTPException(404, "No encontrado")

    recordatorios = enviar_recordatorios(db, sender, push)
    caducadas = caducar_solicitudes_directas(db)
    for solicitud in caducadas:
        notificar_caducidad_pasajero(db, sender, solicitud)
    return {"recordatorios": recordatorios, "solicitudes_caducadas": len(caducadas)}
