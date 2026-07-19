"""Avisos por Telegram al taxista (Bot API, gratuito).

El taxista habla una vez con el bot de la plataforma y guarda su chat_id en
su perfil. Proveedores conmutables como email/push: `console` (desarrollo)
y `telegram` (requiere TAXI_TELEGRAM_BOT_TOKEN de @BotFather).
"""
from __future__ import annotations

import json
from typing import Protocol

import httpx


class TelegramSender(Protocol):
    def enviar(self, chat_id: str, texto: str, botones: list | None = None,
               forzar_respuesta: bool = False) -> None:
        """`botones`: filas de botones inline, cada botón
        {"texto": ..., "datos": ...} (callback) o {"texto": ..., "url": ...}.
        `forzar_respuesta`: Telegram abre el teclado citando este mensaje
        (para pedir un dato, p. ej. un precio). Lanza excepción si el envío
        falla; el llamante decide qué hacer."""
        ...

    def editar_botones(self, chat_id: str, message_id, botones: list | None) -> None:
        """Sustituye el teclado inline de un mensaje ya enviado (menús)."""
        ...

    def responder_callback(self, callback_id: str, texto: str = "") -> None:
        """Cierra el «relojito» del botón pulsado."""
        ...

    def enviar_documento(
        self, chat_id: str, nombre: str, contenido: bytes,
        caption: str = "", botones: list | None = None,
    ) -> None:
        """Envía un archivo (p. ej. la hoja de ruta en PDF) con pie de
        mensaje y, opcionalmente, los mismos botones inline."""
        ...


def _teclado(botones: list | None) -> dict | None:
    if not botones:
        return None
    filas = []
    for fila in botones:
        filas.append([
            {"text": b["texto"], "url": b["url"]} if "url" in b
            else {"text": b["texto"], "callback_data": b["datos"]}
            for b in fila
        ])
    return {"inline_keyboard": filas}


class ConsoleTelegramSender:
    def enviar(self, chat_id: str, texto: str, botones: list | None = None,
               forzar_respuesta: bool = False) -> None:
        extra = f" botones={botones}" if botones else ""
        if forzar_respuesta:
            extra += " (force_reply)"
        print(f"TELEGRAM (console) chat_id={chat_id} texto={texto!r}{extra}", flush=True)

    def editar_botones(self, chat_id: str, message_id, botones: list | None) -> None:
        print(f"TELEGRAM (console) editar_botones chat_id={chat_id} "
              f"message_id={message_id} botones={botones}", flush=True)

    def responder_callback(self, callback_id: str, texto: str = "") -> None:
        print(f"TELEGRAM (console) callback={callback_id} texto={texto!r}", flush=True)

    def enviar_documento(
        self, chat_id: str, nombre: str, contenido: bytes,
        caption: str = "", botones: list | None = None,
    ) -> None:
        extra = f" botones={botones}" if botones else ""
        print(f"TELEGRAM (console) chat_id={chat_id} documento={nombre} "
              f"({len(contenido)} bytes) caption={caption!r}{extra}", flush=True)


class BotTelegramSender:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def enviar(self, chat_id: str, texto: str, botones: list | None = None,
               forzar_respuesta: bool = False) -> None:
        cuerpo = {"chat_id": chat_id, "text": texto, "disable_web_page_preview": True}
        teclado = _teclado(botones)
        if teclado:
            cuerpo["reply_markup"] = teclado
        elif forzar_respuesta:
            cuerpo["reply_markup"] = {"force_reply": True,
                                      "input_field_placeholder": "22,50"}
        resp = httpx.post(f"{self.base}/sendMessage", json=cuerpo, timeout=10)
        resp.raise_for_status()

    def editar_botones(self, chat_id: str, message_id, botones: list | None) -> None:
        resp = httpx.post(
            f"{self.base}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id,
                  "reply_markup": _teclado(botones) or {"inline_keyboard": []}},
            timeout=10,
        )
        resp.raise_for_status()

    def responder_callback(self, callback_id: str, texto: str = "") -> None:
        try:
            httpx.post(
                f"{self.base}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": texto[:180]},
                timeout=10,
            )
        except Exception:
            pass  # cosmético: no debe romper el flujo

    def enviar_documento(
        self, chat_id: str, nombre: str, contenido: bytes,
        caption: str = "", botones: list | None = None,
    ) -> None:
        datos = {"chat_id": chat_id}
        if caption:
            datos["caption"] = caption[:1024]  # límite de la Bot API
        teclado = _teclado(botones)
        if teclado:
            datos["reply_markup"] = json.dumps(teclado)
        resp = httpx.post(
            f"{self.base}/sendDocument",
            data=datos,
            files={"document": (nombre, contenido, "application/pdf")},
            timeout=20,
        )
        resp.raise_for_status()
