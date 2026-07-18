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
    "«Vincular Telegram».\n\n"
    "Comandos:\n"
    "📍 Envíame tu ubicación (o ubicación en tiempo real) y solo te "
    "avisaré de los viajes cercanos\n"
    "/radio 10 — recibir solo viajes a menos de 10 km (elige tu distancia)\n"
    "/desconectar — dejar de recibir viajes de la bolsa\n"
    "/conectar — volver a recibirlos\n"
    "/estado — cómo estás ahora mismo\n"
    "/id — tu chat ID (vinculación manual)"
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
    ubicacion = mensaje.get("location")
    if not chat_id:
        return {"ok": True}

    def responder(t: str) -> None:
        try:
            telegram.enviar(chat_id, t)
        except Exception:
            logger.exception("No se pudo responder al chat %s", chat_id)

    def tenant_del_chat():
        return db.execute(
            select(Tenant).where(Tenant.telegram_chat_id == chat_id)
        ).scalar_one_or_none()

    # 📍 Ubicación (normal o en tiempo real): modo Uber
    if ubicacion is not None:
        tenant = tenant_del_chat()
        if tenant is None:
            responder("Primero vincula tu cuenta: panel → Mi perfil → «Vincular Telegram».")
            return {"ok": True}
        from datetime import datetime, timezone

        tenant.ubicacion_lat = float(ubicacion.get("latitude"))
        tenant.ubicacion_lng = float(ubicacion.get("longitude"))
        tenant.ubicacion_en = datetime.now(timezone.utc)
        db.commit()
        es_directo = "live_period" in ubicacion or update.get("edited_message")
        if not es_directo:
            from app.services.bolsa import radio_de

            responder(
                f"📍 Ubicación guardada. Solo te avisaré de los viajes a menos "
                f"de {radio_de(tenant):g} km (cámbialo con /radio, p. ej. "
                "/radio 10). Envíame otra ubicación cuando cambies de zona, o "
                "comparte tu ubicación en tiempo real y me actualizo solo."
            )
        return {"ok": True}

    if texto.startswith("/radio"):
        tenant = tenant_del_chat()
        if tenant is None:
            responder("Primero vincula tu cuenta: panel → Mi perfil → «Vincular Telegram».")
            return {"ok": True}
        from app.services.bolsa import radio_de

        partes = texto.split(maxsplit=1)
        if len(partes) == 1:
            responder(
                f"Tu radio actual es de {radio_de(tenant):g} km. Para "
                "cambiarlo escribe, por ejemplo: /radio 10"
            )
            return {"ok": True}
        try:
            km = float(partes[1].replace(",", "."))
        except ValueError:
            responder("No he entendido la distancia. Ejemplo: /radio 10")
            return {"ok": True}
        if not (1 <= km <= 100):
            responder("El radio debe estar entre 1 y 100 km.")
            return {"ok": True}
        tenant.radio_km = km
        db.commit()
        extra = ("" if tenant.ubicacion_lat is not None
                 else " Envíame tu 📍 ubicación para activar el filtro.")
        responder(f"✅ Radio guardado: solo viajes a menos de {km:g} km.{extra}")
        return {"ok": True}

    if texto.startswith("/desconectar"):
        tenant = tenant_del_chat()
        if tenant is None:
            responder("Primero vincula tu cuenta: panel → Mi perfil → «Vincular Telegram».")
        else:
            tenant.disponible_bolsa = False
            db.commit()
            responder("🔴 Desconectado: no recibirás viajes de la bolsa. "
                      "Vuelve cuando quieras con /conectar.")
        return {"ok": True}

    if texto.startswith("/conectar"):
        tenant = tenant_del_chat()
        if tenant is None:
            responder("Primero vincula tu cuenta: panel → Mi perfil → «Vincular Telegram».")
        else:
            tenant.disponible_bolsa = True
            db.commit()
            responder("🟢 Conectado: volverás a recibir los viajes de la bolsa. "
                      "Envíame tu 📍 ubicación para recibir solo los cercanos.")
        return {"ok": True}

    if texto.startswith("/estado"):
        tenant = tenant_del_chat()
        if tenant is None:
            responder("Este chat no está vinculado a ninguna cuenta. "
                      "Panel → Mi perfil → «Vincular Telegram».")
        else:
            from app.services.bolsa import radio_de

            estado = "🟢 conectado" if tenant.disponible_bolsa else "🔴 desconectado"
            if tenant.ubicacion_lat is not None:
                zona = (f"📍 con ubicación guardada (aviso de viajes a menos de "
                        f"{radio_de(tenant):g} km)")
            else:
                zona = "sin ubicación: recibes todos los viajes de la bolsa"
            responder(f"{tenant.nombre}: {estado} · {zona}")
        return {"ok": True}

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
