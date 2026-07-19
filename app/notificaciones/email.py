"""Envío de email transaccional (capa 2 del plan §5).

Proveedores conmutables por configuración:
- `console` (desarrollo): imprime el email en el log, no sale nada a la red.
- `brevo`: API HTTP de Brevo (300 emails/día gratis; basta verificar un
  remitente, sin dominio propio). La opción para PaaS que bloquean SMTP
  saliente, como Render.
- `resend`: API HTTP de Resend. Requiere dominio con SPF/DKIM configurados.
- `smtp`: SMTP clásico (Gmail…). OJO: Render bloquea los puertos SMTP
  salientes; ahí usa `brevo` o `resend`.

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


class SMTPEmailSender:
    """SMTP clásico (Gmail con contraseña de aplicación, tu hosting, etc.).
    La vía más rápida para enviar correo real sin dominio propio."""

    def __init__(self, host: str, port: int, usuario: str, password: str, remitente: str):
        self.host, self.port = host, port
        self.usuario, self.password = usuario, password
        self.remitente = remitente

    def enviar(self, email: Email) -> None:
        import mimetypes
        import smtplib
        from email.message import EmailMessage

        m = EmailMessage()
        m["From"] = self.remitente
        m["To"] = email.para
        m["Subject"] = email.asunto
        m.set_content("Este mensaje se ve mejor en un cliente con HTML.")
        m.add_alternative(email.html, subtype="html")
        for a in email.adjuntos:
            tipo, _ = mimetypes.guess_type(a.nombre)
            maintype, _, subtype = (tipo or "application/octet-stream").partition("/")
            m.add_attachment(a.contenido, maintype=maintype, subtype=subtype,
                             filename=a.nombre)
        with smtplib.SMTP(self.host, self.port, timeout=20) as servidor:
            servidor.starttls()
            servidor.login(self.usuario, self.password)
            servidor.send_message(m)


def _partir_remitente(remitente: str) -> tuple[str, str]:
    """'Reservas <yo@gmail.com>' → ('Reservas', 'yo@gmail.com')."""
    if "<" in remitente and remitente.rstrip().endswith(">"):
        nombre, _, resto = remitente.partition("<")
        return nombre.strip() or "Reservas", resto.rstrip(">").strip()
    return "Reservas", remitente.strip()


class BrevoEmailSender:
    """Brevo (antes Sendinblue) por API HTTP: funciona en PaaS que bloquean
    SMTP saliente. El remitente debe estar verificado en la cuenta."""

    URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, api_key: str, remitente: str):
        self.api_key = api_key
        self.nombre, self.email_remitente = _partir_remitente(remitente)

    def enviar(self, email: Email) -> None:
        cuerpo = {
            "sender": {"name": self.nombre, "email": self.email_remitente},
            "to": [{"email": email.para}],
            "subject": email.asunto,
            "htmlContent": email.html,
        }
        if email.adjuntos:
            cuerpo["attachment"] = [
                {"name": a.nombre, "content": base64.b64encode(a.contenido).decode()}
                for a in email.adjuntos
            ]
        resp = httpx.post(
            self.URL,
            json=cuerpo,
            headers={"api-key": self.api_key, "accept": "application/json"},
            timeout=15,
        )
        if resp.status_code >= 400:
            # El detalle de Brevo (p. ej. «sender not valid») vale oro al
            # diagnosticar desde el probador del panel
            raise RuntimeError(f"Brevo {resp.status_code}: {resp.text[:300]}")


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
