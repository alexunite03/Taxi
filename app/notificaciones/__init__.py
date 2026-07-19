from .email import (
    Adjunto,
    BrevoEmailSender,
    ConsoleEmailSender,
    Email,
    EmailSender,
    ResendEmailSender,
    SMTPEmailSender,
)
from .telegram import BotTelegramSender, ConsoleTelegramSender, TelegramSender
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

    if settings.email_provider == "smtp":
        if not (settings.smtp_user and settings.smtp_password):
            raise RuntimeError(
                "TAXI_SMTP_USER y TAXI_SMTP_PASSWORD son obligatorios con email_provider=smtp"
            )
        return SMTPEmailSender(
            settings.smtp_host, settings.smtp_port,
            settings.smtp_user, settings.smtp_password, settings.email_from,
        )
    if settings.email_provider == "brevo":
        if not settings.brevo_api_key:
            raise RuntimeError("TAXI_BREVO_API_KEY es obligatoria con email_provider=brevo")
        return BrevoEmailSender(settings.brevo_api_key, settings.email_from)
    if settings.email_provider == "resend":
        if not settings.resend_api_key:
            raise RuntimeError("TAXI_RESEND_API_KEY es obligatoria con email_provider=resend")
        return ResendEmailSender(settings.resend_api_key, settings.email_from)
    return ConsoleEmailSender()


def crear_telegram_sender() -> TelegramSender:
    """Fábrica según configuración: 'console' (desarrollo) o 'telegram'."""
    from app.config import settings

    if settings.telegram_provider == "telegram":
        if not settings.telegram_bot_token:
            raise RuntimeError(
                "TAXI_TELEGRAM_BOT_TOKEN es obligatorio con telegram_provider=telegram"
            )
        return BotTelegramSender(settings.telegram_bot_token)
    return ConsoleTelegramSender()


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
    "BrevoEmailSender",
    "ConsoleEmailSender",
    "ConsolePushSender",
    "Email",
    "EmailSender",
    "MensajePush",
    "PushSender",
    "ResendEmailSender",
    "SMTPEmailSender",
    "SuscripcionCaducada",
    "WebPushSender",
    "BotTelegramSender",
    "ConsoleTelegramSender",
    "TelegramSender",
    "crear_email_sender",
    "crear_push_sender",
    "crear_telegram_sender",
]
