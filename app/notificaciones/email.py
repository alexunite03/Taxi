"""Envío de email transaccional (capa 2 del plan §5).

Proveedores conmutables por configuración:
- `console` (desarrollo): imprime el email en el log, no sale nada a la red.
- `resend`: API HTTP de Resend. Requiere dominio con SPF/DKIM configurados.

Añadir SES u otro proveedor es implementar `EmailSender.enviar`.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

logger = logging.getLogger("taxi.email")

_RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class Adjunto:
    nombre: str
    contenido: bytes


@dataclass(frozen=True)
class Email:
    para: str
    asunto: str
    html: str
    adjuntos: list[Adjunto] = field(default_factory=list, hash=False)


class EmailSender(Protocol):
    def enviar(self, email: Email) -> None:
        """Lanza excepción si el envío falla; el llamante decide qué hacer."""
        ...


class ConsoleEmailSender:
    def enviar(self, email: Email) -> None:
        # print y no logger: el nivel INFO queda silenciado bajo uvicorn y
        # este sender existe precisamente para ver los envíos en desarrollo.
        print(
            f"EMAIL (console) para={email.para} asunto={email.asunto!r} "
            f"adjuntos={[a.nombre for a in email.adjuntos]}",
            flush=True,
        )


class ResendEmailSender:
    def __init__(self, api_key: str, remitente: str):
        self.api_key = api_key
        self.remitente = remitente

    def enviar(self, email: Email) -> None:
        cuerpo = {
            "from": self.remitente,
            "to": [email.para],
            "subject": email.asunto,
            "html": email.html,
        }
        if email.adjuntos:
            cuerpo["attachments"] = [
                {
                    "filename": a.nombre,
                    "content": base64.b64encode(a.contenido).decode(),
                }
                for a in email.adjuntos
            ]
        resp = httpx.post(
            _RESEND_URL,
            json=cuerpo,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
