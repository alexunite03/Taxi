"""Tests del webhook de Telegram (vinculación de un toque) y del SMTP."""
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Tenant
from app.notificaciones import Adjunto, Email, SMTPEmailSender

from .test_bolsa import login_panel
from .test_social import con_telegram_espia


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


def test_probador_de_avisos_en_el_panel(client, db):
    from .test_notificaciones import SenderEspia

    original_tg = con_telegram_espia()
    original_email = app.state.email_sender
    app.state.email_sender = SenderEspia()
    try:
        _vincular(client, db)
        login_panel(client)

        # El perfil muestra el estado de los canales
        perfil = client.get("/panel/perfil")
        assert "Estado de los avisos" in perfil.text

        # Probar: los espías no son Console*, así que "envían de verdad"
        r = client.post("/panel/avisos/probar")
        assert r.status_code == 200
        assert "Prueba de avisos" in app.state.email_sender.enviados[-1].asunto
        assert any("Telegram funciona" in t
                   for _, t in app.state.telegram_sender.enviados)
        assert "✅" in r.text

        # Con un proveedor roto, el error exacto se ve en pantalla
        app.state.email_sender = SenderEspia(fallar=True)
        r = client.post("/panel/avisos/probar")
        assert "falló" in r.text and "proveedor caído" in r.text
    finally:
        app.state.telegram_sender = original_tg
        app.state.email_sender = original_email


def test_brevo_sender(monkeypatch):
    import httpx

    from app.notificaciones import BrevoEmailSender

    peticiones = []

    def falso_post(url, **kwargs):
        peticiones.append((url, kwargs))
        return httpx.Response(201, json={"messageId": "x"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", falso_post)
    sender = BrevoEmailSender("clave-api", "Reservas <yo@gmail.com>")
    sender.enviar(Email(
        para="cliente@example.com", asunto="Hola", html="<p>Hola</p>",
        adjuntos=[Adjunto(nombre="hoja-de-ruta.pdf", contenido=b"%PDF-...")],
    ))
    url, kwargs = peticiones[0]
    assert url == BrevoEmailSender.URL
    assert kwargs["headers"]["api-key"] == "clave-api"
    cuerpo = kwargs["json"]
    assert cuerpo["sender"] == {"name": "Reservas", "email": "yo@gmail.com"}
    assert cuerpo["to"] == [{"email": "cliente@example.com"}]
    assert cuerpo["attachment"][0]["name"] == "hoja-de-ruta.pdf"

    # Un 4xx de Brevo se convierte en un error legible (para el probador)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(
        401, text='{"message":"Key not found"}',
        request=httpx.Request("POST", url)))
    try:
        sender.enviar(Email(para="x@example.com", asunto="a", html="b"))
        assert False, "deberia lanzar RuntimeError"
    except RuntimeError as e:
        assert "Brevo 401" in str(e) and "Key not found" in str(e)


def test_canal_brevo_configurado(monkeypatch):
    from app.notificaciones import BrevoEmailSender, crear_email_sender

    anterior = (settings.email_provider, settings.brevo_api_key)
    settings.email_provider, settings.brevo_api_key = "brevo", "clave"
    try:
        assert isinstance(crear_email_sender(), BrevoEmailSender)
    finally:
        settings.email_provider, settings.brevo_api_key = anterior


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


def _vincular(client, db):
    from .test_bolsa import login_panel

    login_panel(client)
    client.get("/panel/perfil")
    db.expire_all()
    tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
    update_telegram(client, f"/start {tenant.telegram_codigo}")
    db.expire_all()
    return db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()


def test_ubicacion_y_modo_uber(client, db):
    original = con_telegram_espia()
    try:
        tenant = _vincular(client, db)

        # 📍 Ubicación → se guarda y el bot confirma con el radio
        client.post("/api/telegram/webhook", json={"message": {
            "chat": {"id": 777000},
            "location": {"latitude": 40.42, "longitude": -3.70},
        }})
        db.expire_all()
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        assert tenant.ubicacion_lat == 40.42 and tenant.ubicacion_lng == -3.70
        assert "Ubicación guardada" in app.state.telegram_sender.enviados[-1][1]

        # /desconectar y /conectar como en Uber
        update_telegram(client, "/desconectar")
        db.expire_all()
        assert db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one().disponible_bolsa is False
        assert "🔴" in app.state.telegram_sender.enviados[-1][1]

        update_telegram(client, "/conectar")
        db.expire_all()
        assert db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one().disponible_bolsa is True

        update_telegram(client, "/estado")
        assert "🟢 conectado" in app.state.telegram_sender.enviados[-1][1]
    finally:
        app.state.telegram_sender = original


def test_ubicacion_sin_vincular(client, db):
    original = con_telegram_espia()
    try:
        client.post("/api/telegram/webhook", json={"message": {
            "chat": {"id": 999111},
            "location": {"latitude": 40.42, "longitude": -3.70},
        }})
        assert "vincula tu cuenta" in app.state.telegram_sender.enviados[-1][1]
    finally:
        app.state.telegram_sender = original


def test_avisos_de_bolsa_filtrados_por_cercania(client, db):
    from .test_bolsa import publicar_viaje
    from .test_notificaciones import SenderEspia
    from app.models import SolicitudViaje

    original_tg = con_telegram_espia()
    original_email = app.state.email_sender
    app.state.email_sender = SenderEspia()
    try:
        tenant = _vincular(client, db)
        client.post("/panel/logout")

        # El taxista está lejísimos de cualquier recogida en Madrid
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        tenant.ubicacion_lat, tenant.ubicacion_lng = 43.36, -8.41  # A Coruña
        db.commit()

        publicar_viaje(client)
        avisos_bolsa = [t for _, t in app.state.telegram_sender.enviados
                        if "Viaje nuevo" in t]
        assert avisos_bolsa == []  # demasiado lejos: ni telegram ni email

        # Ahora está al lado de la recogida → recibe el aviso con distancia
        solicitud = db.execute(select(SolicitudViaje)).scalars().first()
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        tenant.ubicacion_lat = solicitud.origen_lat
        tenant.ubicacion_lng = solicitud.origen_lng
        db.commit()

        publicar_viaje(client, destino="Otro destino cercano")
        avisos_bolsa = [t for _, t in app.state.telegram_sender.enviados
                        if "Viaje nuevo" in t]
        assert len(avisos_bolsa) == 1
        assert "km de ti" in avisos_bolsa[0]
    finally:
        app.state.telegram_sender = original_tg
        app.state.email_sender = original_email


def test_radio_configurable_por_taxista(client, db):
    original = con_telegram_espia()
    try:
        _vincular(client, db)

        # Consultar sin argumento → radio global por defecto
        update_telegram(client, "/radio")
        assert "15 km" in app.state.telegram_sender.enviados[-1][1]

        # Cambiarlo desde el bot
        update_telegram(client, "/radio 7")
        db.expire_all()
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        assert tenant.radio_km == 7
        assert "7 km" in app.state.telegram_sender.enviados[-1][1]

        # Valores inválidos rechazados
        update_telegram(client, "/radio 500")
        assert "entre 1 y 100" in app.state.telegram_sender.enviados[-1][1]
        update_telegram(client, "/radio abc")
        assert "No he entendido" in app.state.telegram_sender.enviados[-1][1]

        # También desde el panel
        from .test_bolsa import login_panel

        login_panel(client)
        client.post("/panel/perfil", data={
            "bio": "", "descuento_pct": "0", "recogida_eur": "5",
            "radio_km": "25", "telegram_chat_id": tenant.telegram_chat_id or ""})
        db.expire_all()
        assert db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one().radio_km == 25
        r = client.post("/panel/perfil", data={
            "bio": "", "descuento_pct": "0", "recogida_eur": "5",
            "radio_km": "300", "telegram_chat_id": ""})
        assert r.status_code == 422
    finally:
        app.state.telegram_sender = original


def test_avisos_respetan_radio_del_taxista(client, db):
    from .test_bolsa import publicar_viaje
    from .test_notificaciones import SenderEspia
    from app.models import SolicitudViaje
    from app.services.bolsa import distancia_km

    original_tg = con_telegram_espia()
    original_email = app.state.email_sender
    app.state.email_sender = SenderEspia()
    try:
        _vincular(client, db)
        client.post("/panel/logout")

        # Primera solicitud para conocer las coordenadas de recogida
        publicar_viaje(client)
        solicitud = db.execute(select(SolicitudViaje)).scalars().first()
        app.state.telegram_sender.enviados.clear()  # aviso previo, sin filtro aún

        # Taxista a ~6 km de la recogida con radio de 5 → no recibe
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        tenant.ubicacion_lat = solicitud.origen_lat + 0.055  # ≈ 6 km al norte
        tenant.ubicacion_lng = solicitud.origen_lng
        tenant.radio_km = 5
        db.commit()
        d = distancia_km(tenant.ubicacion_lat, tenant.ubicacion_lng,
                         solicitud.origen_lat, solicitud.origen_lng)
        assert 5 < d < 8  # sanity check del escenario

        publicar_viaje(client, destino="Destino B")
        assert not [t for _, t in app.state.telegram_sender.enviados if "Viaje nuevo" in t]

        # Con radio de 10 sí lo recibe
        tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
        tenant.radio_km = 10
        db.commit()
        publicar_viaje(client, destino="Destino C")
        avisos = [t for _, t in app.state.telegram_sender.enviados if "Viaje nuevo" in t]
        assert len(avisos) == 1 and "km de ti" in avisos[0]
    finally:
        app.state.telegram_sender = original_tg
        app.state.email_sender = original_email
