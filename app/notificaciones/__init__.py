from .email import Adjunto, ConsoleEmailSender, Email, EmailSender, ResendEmailSender
from .push import (
    ConsolePushSender,
    MensajePush,
    PushSender,
    SuscripcionCaducada,
    WebPushSender,
)


def crear_email_sender() -> EmailSender:
    """Fábrica según configuración: 'console' (desarrollo) o 'resend'."""
    from app.config import settings

    if settings.email_provider == "resend":
        if not settings.resend_api_key:
            raise RuntimeError("TAXI_RESEND_API_KEY es obligatoria con email_provider=resend")
        return ResendEmailSender(settings.resend_api_key, settings.email_from)
    return ConsoleEmailSender()


def crear_push_sender() -> PushSender:
    """Fábrica según configuración: 'console' (desarrollo) o 'webpush'."""
    from app.config import settings

    if settings.push_provider == "webpush":
        if not (settings.vapid_private_key and settings.vapid_public_key):
            raise RuntimeError(
                "TAXI_VAPID_PRIVATE_KEY y TAXI_VAPID_PUBLIC_KEY son obligatorias con "
                "push_provider=webpush (genera claves con `python -m app.jobs generar-vapid`)"
            )
        return WebPushSender(settings.vapid_private_key, settings.vapid_email)
    return ConsolePushSender()


__all__ = [
    "Adjunto",
    "ConsoleEmailSender",
    "ConsolePushSender",
    "Email",
    "EmailSender",
    "MensajePush",
    "PushSender",
    "ResendEmailSender",
    "SuscripcionCaducada",
    "WebPushSender",
    "crear_email_sender",
    "crear_push_sender",
]
