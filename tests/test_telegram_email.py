"""Tests del webhook de Telegram (vinculación de un toque) y del SMTP."""
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Tenant
from app.notificaciones import Adjunto, Email, SMTPEmailSender

from .test_bolsa import login_panel
from .test_social import TelegramEspia, con_telegram_espia


def update_telegram(client, texto, chat_id="777000", headers=None):
    return client.post(
        "/api/telegram/webhook",
        json={"message": {"chat": {"id": int(chat_id)}, "text": texto}},
        headers=headers or {},
    )


def test_vinculacion_de_un_toque(client, db):
    original = con_telegram_espia()
    try:
        # El perfil genera el código de vinculación
        login_panel(client)
        client.get("/panel/perfil")
        db.expire_all()
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        assert tenant.telegram_codigo

        # El taxista pulsa el enlace → Telegram entrega /start <código>
        r = update_telegram(client, f"/start {tenant.telegram_codigo}")
        assert r.status_code == 200

        db.expire_all()
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        assert tenant.telegram_chat_id == "777000"
        assert tenant.telegram_codigo is None  # un solo uso

        confirmaciones = app.state.telegram_sender.enviados
        assert confirmaciones and "✅" in confirmaciones[-1][1]

        # El mismo código ya no vale
        update_telegram(client, "/start codigo-viejo", chat_id="888000")
        assert "no es válido" in app.state.telegram_sender.enviados[-1][1]
    finally:
        app.state.telegram_sender = original


def test_comando_id_y_ayuda(client):
    original = con_telegram_espia()
    try:
        update_telegram(client, "/id")
        assert "777000" in app.state.telegram_sender.enviados[-1][1]
        update_telegram(client, "hola")
        assert "TaxiMad" in app.state.telegram_sender.enviados[-1][1]
    finally:
        app.state.telegram_sender = original


def test_webhook_con_secreto(client):
    original = con_telegram_espia()
    anterior = settings.telegram_webhook_secret
    settings.telegram_webhook_secret = "secreto-123"
    try:
        assert update_telegram(client, "/id").status_code == 403
        r = update_telegram(
            client, "/id",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secreto-123"},
        )
        assert r.status_code == 200
    finally:
        settings.telegram_webhook_secret = anterior
        app.state.telegram_sender = original


def test_smtp_sender(monkeypatch):
    enviados = []

    class SMTPFalso:
        def __init__(self, host, port, timeout=None):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            self.credenciales = (user, password)

        def send_message(self, mensaje):
            enviados.append(mensaje)

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    sender = SMTPEmailSender(
        "smtp.gmail.com", 587, "yo@gmail.com", "clave-app", "Reservas <yo@gmail.com>"
    )
    sender.enviar(Email(
        para="cliente@example.com",
        asunto="Reserva confirmada",
        html="<p>Hola</p>",
        adjuntos=[Adjunto(nombre="justificante-A-000001.html", contenido=b"<html/>")],
    ))
    assert len(enviados) == 1
    m = enviados[0]
    assert m["To"] == "cliente@example.com"
    assert m["Subject"] == "Reserva confirmada"
    adjuntos = [p.get_filename() for p in m.iter_attachments()]
    assert adjuntos == ["justificante-A-000001.html"]


def test_canal_mal_configurado_no_tumba_el_arranque():
    from app.main import crear_app
    from app.notificaciones import ConsoleTelegramSender

    anterior = settings.telegram_provider
    settings.telegram_provider = "telegram"  # sin token: configuración rota
    try:
        aplicacion = crear_app()
        assert isinstance(aplicacion.state.telegram_sender, ConsoleTelegramSender)
    finally:
        settings.telegram_provider = anterior
