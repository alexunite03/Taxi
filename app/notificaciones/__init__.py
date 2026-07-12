from .email import Adjunto, ConsoleEmailSender, Email, EmailSender, ResendEmailSender


def crear_email_sender() -> EmailSender:
    """Fábrica según configuración: 'console' (desarrollo) o 'resend'."""
    from app.config import settings

    if settings.email_provider == "resend":
        if not settings.resend_api_key:
            raise RuntimeError("TAXI_RESEND_API_KEY es obligatoria con email_provider=resend")
        return ResendEmailSender(settings.resend_api_key, settings.email_from)
    return ConsoleEmailSender()


__all__ = [
    "Adjunto",
    "ConsoleEmailSender",
    "Email",
    "EmailSender",
    "ResendEmailSender",
    "crear_email_sender",
]
