"""Tests: política de precios del taxista, Telegram, QR al perfil e
intermediarios."""
from decimal import Decimal

from sqlalchemy import select

from app.main import app
from app.models import Reserva, SolicitudViaje, Tenant

from .test_api import fecha_recogida, pedir_cotizacion
from .test_bolsa import login_panel, publicar_viaje
from .test_notificaciones import espia  # noqa: F401

HOTEL = {
    "nombre": "Hotel Plaza Central",
    "contacto": "Recepción",
    "telefono": "915556677",
    "email": "recepcion@hotelplaza.example",
    "password": "clave-hotel-1",
    "direccion": "Plaza Central 1, Madrid",
}


class TelegramEspia:
    def __init__(self):
        self.enviados: list[tuple[str, str]] = []
        self.botones: list = []  # botones del último envío con teclado
        self.callbacks: list[tuple[str, str]] = []
        self.documentos: list[tuple[str, str, bytes]] = []  # (chat, nombre, pdf)

    def enviar(self, chat_id: str, texto: str, botones=None) -> None:
        self.enviados.append((chat_id, texto))
        if botones:
            self.botones = botones

    def responder_callback(self, callback_id: str, texto: str = "") -> None:
        self.callbacks.append((callback_id, texto))

    def enviar_documento(self, chat_id, nombre, contenido, caption="", botones=None):
        self.documentos.append((chat_id, nombre, contenido))
        self.enviados.append((chat_id, caption))  # el caption hace de mensaje
        if botones:
            self.botones = botones


def con_telegram_espia():
    original = getattr(app.state, "telegram_sender", None)
    app.state.telegram_sender = TelegramEspia()
    return original


def test_politica_de_precios_del_taxista(client, db):
    login_panel(client)
    r = client.post(
        "/panel/perfil",
        data={"bio": "", "descuento_pct": "10", "recogida_eur": "2.5",
              "telegram_chat_id": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # La cotización pública aplica su política: 10 % menos y recogida 2,50
    cot = pedir_cotizacion(client).json()
    db.expire_all()
    tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
    assert tenant.descuento_pct == 10 and float(tenant.recogida_eur) == 2.5
    from app.models import Cotizacion
    import uuid as _uuid

    fila = db.get(Cotizacion, _uuid.UUID(cot["cotizacion_id"]))
    assert fila.calculo_payload["descuento_taxista_pct"] == "10"
    assert fila.calculo_payload["importe_recogida"] in ("2.5", "2.50")


def test_limites_de_politica_rechazados(client):
    login_panel(client)
    r = client.post("/panel/perfil", data={
        "bio": "", "descuento_pct": "50", "recogida_eur": "2", "telegram_chat_id": ""})
    assert r.status_code == 422
    r = client.post("/panel/perfil", data={
        "bio": "", "descuento_pct": "0", "recogida_eur": "9", "telegram_chat_id": ""})
    assert r.status_code == 422


def test_aviso_al_taxista_por_email_y_telegram(client, db, espia):  # noqa: F811
    original = con_telegram_espia()
    try:
        login_panel(client)
        client.post("/panel/perfil", data={
            "bio": "", "descuento_pct": "0", "recogida_eur": "5",
            "telegram_chat_id": "12345678"})

        cot = pedir_cotizacion(client).json()
        client.post("/api/t/demo/reservas", json={
            "cotizacion_id": cot["cotizacion_id"], "nombre": "Eva",
            "telefono": "600555444"})

        # El taxista recibe la reserva PENDIENTE con la hoja de ruta y los
        # botones para aceptar (con descuento) o rechazar
        assert any(e.para == "demo@example.com" and "pendiente" in e.asunto
                   for e in espia.enviados)
        tg = app.state.telegram_sender.enviados
        assert len(tg) == 1 and tg[0][0] == "12345678"
        assert "pendiente" in tg[0][1] and "🟢 Recogida" in tg[0][1]
        botones = app.state.telegram_sender.botones
        textos = [b["texto"] for fila in botones for b in fila]
        assert any("Aceptar" in t for t in textos)
        assert any("Rechazar" in t for t in textos)
        assert any("−10 %" in t for t in textos)
    finally:
        app.state.telegram_sender = original


def test_solicitud_avisa_a_taxistas_disponibles(client, db, espia):  # noqa: F811
    original = con_telegram_espia()
    try:
        login_panel(client)
        client.post("/panel/perfil", data={
            "bio": "", "descuento_pct": "0", "recogida_eur": "5",
            "telegram_chat_id": "999"})
        client.post("/panel/logout")

        publicar_viaje(client)
        assert any("Viaje nuevo en la bolsa" in e.asunto for e in espia.enviados)
        assert any(chat == "999" for chat, _ in app.state.telegram_sender.enviados)
    finally:
        app.state.telegram_sender = original


def test_aceptar_de_bolsa_con_descuento(client, db, espia):  # noqa: F811
    publicar_viaje(client)
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    estimado = Decimal(str(solicitud.precio_estimado))

    login_panel(client)
    r = client.post(
        f"/panel/solicitudes/{solicitud.id}/aceptar",
        data={"descuento_pct": "20", "recogida_eur": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    reserva = db.execute(select(Reserva)).scalar_one()
    # 20 % de descuento y sin recogida: claramente por debajo del estimado
    assert Decimal(str(reserva.precio_cerrado)) < estimado


def test_ajuste_fuera_de_limites_rechazado(client, db):
    publicar_viaje(client)
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    login_panel(client)
    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                    data={"descuento_pct": "0", "recogida_eur": "7"})
    assert r.status_code == 422


def test_qr_apunta_al_perfil(client):
    login_panel(client)
    r = client.get("/panel/qr")
    assert r.status_code == 200
    assert r.headers["x-qr-target"].endswith("/t/demo/perfil")


def test_intermediario_flujo_completo(client, db, espia):  # noqa: F811
    # Registro del hotel → entra a su panel
    r = client.post("/registro/intermediario", data=HOTEL, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/intermediario"

    panel = client.get("/intermediario")
    assert "Hotel Plaza Central" in panel.text

    # Pide un taxi para un cliente (la recogida por defecto es su dirección)
    r = client.post("/intermediario/pedir", data={
        "cliente_nombre": "Sr. Smith", "cliente_telefono": "677889900",
        "origen": HOTEL["direccion"], "destino": "Aeropuerto T4... no, Atocha",
        "fecha_hora": fecha_recogida()}, follow_redirects=False)
    assert r.status_code == 303, r.text

    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.intermediario_id is not None
    assert solicitud.nombre == "Sr. Smith"

    # El taxista la ve identificada con el hotel y la acepta
    login_panel(client)
    bolsa = client.get("/panel/bolsa")
    assert "pedido\n    por Hotel Plaza Central" in bolsa.text or \
        "Hotel Plaza Central" in bolsa.text
    client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                data={"descuento_pct": "0", "recogida_eur": "5"})

    # El hotel ve la asignación con el enlace a la reserva
    client.post("/intermediario/login", data={
        "email": HOTEL["email"], "password": HOTEL["password"]})
    panel = client.get("/intermediario")
    assert "asignado" in panel.text and "Ver reserva" in panel.text


def test_intermediario_email_duplicado_y_login_malo(client):
    client.post("/registro/intermediario", data=HOTEL)
    r = client.post("/registro/intermediario", data=HOTEL)
    assert r.status_code == 422 and "Ya existe" in r.text
    r = client.post("/intermediario/login",
                    data={"email": HOTEL["email"], "password": "mala"})
    assert r.status_code == 401
