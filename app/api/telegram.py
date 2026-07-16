"""Webhook del bot de Telegram: vinculación de taxistas y comando /id.

Flujo de vinculación de un toque:
1. El perfil del panel muestra un enlace https://t.me/<bot>?start=<código>
   (el código es aleatorio, de un solo uso, guardado en el tenant).
2. Telegram entrega el /start <código> a este webhook.
3. Se busca el tenant por código, se guarda su chat_id y se le confirma
   por el propio chat. A partir de ahí recibe todos los avisos.

El webhook se registra con `python -m app.jobs telegram-webhook` y se
protege con el secreto que Telegram reenvía en cada update
(X-Telegram-Bot-Api-Secret-Token).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import telegram_sender
from app.config import settings
from app.db import get_db
from app.models import Tenant

logger = logging.getLogger("taxi.telegram")

router = APIRouter(prefix="/api/telegram")

AYUDA = (
    "Hola 👋 Soy el bot de avisos de TaxiMad.\n\n"
    "Si eres taxista, vincula tu cuenta desde tu panel → Mi perfil → "
    "«Vincular Telegram». También puedes escribir /id y pegar el número "
    "en tu perfil."
)


@router.post("/webhook")
def webhook(
    request: Request,
    update: dict = Body(...),
    db: Session = Depends(get_db),
    telegram=Depends(telegram_sender),
):
    if settings.telegram_webhook_secret:
        recibido = request.headers.get("x-telegram-bot-api-secret-token", "")
        if recibido != settings.telegram_webhook_secret:
            raise HTTPException(403, "Secreto del webhook incorrecto")

    mensaje = update.get("message") or update.get("edited_message") or {}
    chat_id = str((mensaje.get("chat") or {}).get("id") or "")
    texto = (mensaje.get("text") or "").strip()
    if not chat_id:
        return {"ok": True}

    def responder(t: str) -> None:
        try:
            telegram.enviar(chat_id, t)
        except Exception:
            logger.exception("No se pudo responder al chat %s", chat_id)

    if texto.startswith("/start"):
        partes = texto.split(maxsplit=1)
        if len(partes) == 2:
            codigo = partes[1].strip()
            tenant = db.execute(
                select(Tenant).where(Tenant.telegram_codigo == codigo)
            ).scalar_one_or_none()
            if tenant is not None:
                tenant.telegram_chat_id = chat_id
                tenant.telegram_codigo = None  # un solo uso
                db.commit()
                responder(
                    f"✅ Listo, {tenant.nombre.split()[0]}: este chat queda "
                    "vinculado. Recibirás aquí cada reserva nueva y los "
                    "viajes de la bolsa."
                )
            else:
                responder(
                    "Ese enlace de vinculación no es válido o ya se usó. "
                    "Genera uno nuevo desde tu panel → Mi perfil."
                )
        else:
            responder(AYUDA)
    elif texto.startswith("/id"):
        responder(f"Tu chat ID es: {chat_id}\nPégalo en tu panel → Mi perfil.")
    else:
        responder(AYUDA)
    return {"ok": True}
