"""Avisos por Telegram al taxista (Bot API, gratuito).

El taxista habla una vez con el bot de la plataforma y guarda su chat_id en
su perfil. Proveedores conmutables como email/push: `console` (desarrollo)
y `telegram` (requiere TAXI_TELEGRAM_BOT_TOKEN de @BotFather).
"""
from __future__ import annotations

from typing import Protocol

import httpx


class TelegramSender(Protocol):
    def enviar(self, chat_id: str, texto: str) -> None:
        """Lanza excepción si el envío falla; el llamante decide qué hacer."""
        ...


class ConsoleTelegramSender:
    def enviar(self, chat_id: str, texto: str) -> None:
        print(f"TELEGRAM (console) chat_id={chat_id} texto={texto!r}", flush=True)


class BotTelegramSender:
    def __init__(self, token: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"

    def enviar(self, chat_id: str, texto: str) -> None:
        resp = httpx.post(
            self.url,
            json={"chat_id": chat_id, "text": texto, "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
