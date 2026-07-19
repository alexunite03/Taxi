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


def _procesar_callback(request: Request, db: Session, telegram, callback: dict) -> dict:
    """Botones inline de una solicitud: `sol:<id>:a:<pct>` acepta el viaje
    (con descuento opcional) y `sol:<id>:r` lo rechaza. El precio definitivo
    lo pone el taxista al pulsar; el pasajero solo vio el precio máximo."""
    import uuid

    callback_id = callback.get("id") or ""
    datos = callback.get("data") or ""
    chat_id = str(
        ((callback.get("message") or {}).get("chat") or {}).get("id")
        or (callback.get("from") or {}).get("id")
        or ""
    )

    def avisar(t: str) -> None:
        telegram.responder_callback(callback_id, t)

    partes = datos.split(":")
    if len(partes) < 3 or partes[0] != "sol" or not chat_id:
        avisar("")
        return {"ok": True}

    tenant = db.execute(
        select(Tenant).where(Tenant.telegram_chat_id == chat_id)
    ).scalar_one_or_none()
    if tenant is None:
        avisar("Este chat no está vinculado a ninguna cuenta.")
        return {"ok": True}

    try:
        solicitud_id = uuid.UUID(partes[1])
    except ValueError:
        avisar("Botón no reconocido.")
        return {"ok": True}

    from app.services.bolsa import (
        ErrorBolsa,
        ViajeYaAsignado,
        aceptar_solicitud,
        rechazar_solicitud,
    )
    from app.services.notificaciones import (
        notificar_confirmacion,
        notificar_hoja_de_ruta_taxista,
        notificar_rechazo_pasajero,
    )

    def responder_chat(t: str) -> None:
        try:
            telegram.enviar(chat_id, t)
        except Exception:
            logger.exception("No se pudo responder al chat %s", chat_id)

    if partes[2] == "r":
        try:
            solicitud = rechazar_solicitud(db, tenant, solicitud_id)
        except ViajeYaAsignado:
            avisar("Este viaje ya no está pendiente.")
            return {"ok": True}
        except ErrorBolsa as e:
            avisar(str(e))
            return {"ok": True}
        avisar("Viaje rechazado")
        notificar_rechazo_pasajero(db, request.app.state.email_sender, solicitud)
        responder_chat("❌ Viaje rechazado. Hemos avisado al pasajero para que "
                       "busque otro taxista.")
        return {"ok": True}

    try:
        pct = int(partes[3]) if len(partes) > 3 else 0
    except ValueError:
        pct = 0
    if not (0 <= pct <= 30):
        avisar("Descuento fuera de los límites (0–30 %).")
        return {"ok": True}

    try:
        solicitud, reserva = aceptar_solicitud(
            db, tenant, solicitud_id,
            request.app.state.geocoder, request.app.state.rutas,
            descuento_pct=pct if pct > 0 else None,
        )
    except ViajeYaAsignado:
        avisar("Ya lo aceptó otro taxista.")
        return {"ok": True}
    except ErrorBolsa as e:
        avisar(str(e))
        return {"ok": True}
    except Exception:
        logger.exception("Fallo aceptando la solicitud %s desde Telegram", solicitud_id)
        avisar("No se pudo aceptar el viaje. Inténtalo desde tu panel.")
        return {"ok": True}

    notificar_confirmacion(db, request.app.state.email_sender, reserva)
    avisar("✅ Viaje aceptado")
    extra = f" (descuento del {pct} %)" if pct else ""
    try:
        # Hoja de ruta definitiva en PDF por Telegram y por email
        notificar_hoja_de_ruta_taxista(
            db, request.app.state.email_sender, telegram, solicitud, reserva
        )
    except Exception:
        logger.exception("Fallo enviando la hoja de ruta tras aceptar %s", solicitud_id)
        responder_chat(
            f"✅ Viaje aceptado por {reserva.precio_cerrado} €{extra}.\n"
            f"Justificante: {settings.base_url}/r/{reserva.token_publico}"
        )
    return {"ok": True}


VINCULA = "Primero vincula tu cuenta: panel → Mi perfil → «Vincular Telegram»."


def _guardar_ubicacion(db, responder, tenant, ubicacion: dict, en_vivo: bool) -> None:
    from datetime import datetime, timezone

    from app.services.bolsa import radio_de

    tenant.ubicacion_lat = float(ubicacion.get("latitude"))
    tenant.ubicacion_lng = float(ubicacion.get("longitude"))
    tenant.ubicacion_en = datetime.now(timezone.utc)
    db.commit()
    if not en_vivo:  # la ubicación en tiempo real se actualiza en silencio
        responder(
            f"📍 Ubicación guardada. Solo te avisaré de los viajes a menos "
            f"de {radio_de(tenant):g} km (cámbialo con /radio, p. ej. "
            "/radio 10). Envíame otra ubicación cuando cambies de zona, o "
            "comparte tu ubicación en tiempo real y me actualizo solo."
        )


def _cmd_radio(db, responder, tenant, args: str) -> None:
    from app.services.bolsa import radio_de

    if not args:
        responder(
            f"Tu radio actual es de {radio_de(tenant):g} km. Para "
            "cambiarlo escribe, por ejemplo: /radio 10"
        )
        return
    try:
        km = float(args.replace(",", "."))
    except ValueError:
        responder("No he entendido la distancia. Ejemplo: /radio 10")
        return
    if not (1 <= km <= 100):
        responder("El radio debe estar entre 1 y 100 km.")
        return
    tenant.radio_km = km
    db.commit()
    extra = ("" if tenant.ubicacion_lat is not None
             else " Envíame tu 📍 ubicación para activar el filtro.")
    responder(f"✅ Radio guardado: solo viajes a menos de {km:g} km.{extra}")


def _cmd_desconectar(db, responder, tenant, args: str) -> None:
    tenant.disponible_bolsa = False
    db.commit()
    responder("🔴 Desconectado: no recibirás viajes de la bolsa. "
              "Vuelve cuando quieras con /conectar.")


def _cmd_conectar(db, responder, tenant, args: str) -> None:
    tenant.disponible_bolsa = True
    db.commit()
    responder("🟢 Conectado: volverás a recibir los viajes de la bolsa. "
              "Envíame tu 📍 ubicación para recibir solo los cercanos.")


def _cmd_estado(db, responder, tenant, args: str) -> None:
    from app.services.bolsa import radio_de

    estado = "🟢 conectado" if tenant.disponible_bolsa else "🔴 desconectado"
    if tenant.ubicacion_lat is not None:
        zona = (f"📍 con ubicación guardada (aviso de viajes a menos de "
                f"{radio_de(tenant):g} km)")
    else:
        zona = "sin ubicación: recibes todos los viajes de la bolsa"
    responder(f"{tenant.nombre}: {estado} · {zona}")


def _vincular_por_codigo(db, responder, chat_id: str, codigo: str) -> None:
    tenant = db.execute(
        select(Tenant).where(Tenant.telegram_codigo == codigo)
    ).scalar_one_or_none()
    if tenant is None:
        responder("Ese enlace de vinculación no es válido o ya se usó. "
                  "Genera uno nuevo desde tu panel → Mi perfil.")
        return
    tenant.telegram_chat_id = chat_id
    tenant.telegram_codigo = None  # un solo uso
    db.commit()
    responder(
        f"✅ Listo, {tenant.nombre.split()[0]}: este chat queda vinculado. "
        "Recibirás aquí cada reserva nueva y los viajes de la bolsa."
    )


# Comandos que requieren un chat ya vinculado a un taxista
_COMANDOS = {
    "/radio": _cmd_radio,
    "/desconectar": _cmd_desconectar,
    "/conectar": _cmd_conectar,
    "/estado": _cmd_estado,
}


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

    callback = update.get("callback_query")
    if callback is not None:
        return _procesar_callback(request, db, telegram, callback)

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

    comando, _, args = texto.partition(" ")
    comando = comando.split("@", 1)[0]  # en grupos llega "/radio@mibot"

    # Vinculación y ayuda: los únicos mensajes que no requieren cuenta
    if comando == "/start":
        codigo = args.strip()
        if codigo:
            _vincular_por_codigo(db, responder, chat_id, codigo)
        else:
            responder(AYUDA)
        return {"ok": True}
    if comando == "/id":
        responder(f"Tu chat ID es: {chat_id}\nPégalo en tu panel → Mi perfil.")
        return {"ok": True}

    if ubicacion is None and comando not in _COMANDOS:
        responder(AYUDA)
        return {"ok": True}

    tenant = db.execute(
        select(Tenant).where(Tenant.telegram_chat_id == chat_id)
    ).scalar_one_or_none()
    if tenant is None:
        responder(VINCULA)
        return {"ok": True}

    if ubicacion is not None:  # 📍 normal o en tiempo real: modo Uber
        en_vivo = "live_period" in ubicacion or bool(update.get("edited_message"))
        _guardar_ubicacion(db, responder, tenant, ubicacion, en_vivo)
    else:
        _COMANDOS[comando](db, responder, tenant, args.strip())
    return {"ok": True}
