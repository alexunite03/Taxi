"""Tests de la capa de email: confirmación, cancelación y recordatorios."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Cotizacion, Notificacion, Reserva
from app.notificaciones import Email
from app.pricing.motor import TZ_MADRID
from app.services.notificaciones import enviar_recordatorios

from .test_api import aceptar_pendiente, pedir_cotizacion


class SenderEspia:
    def __init__(self, fallar: bool = False):
        self.enviados: list[Email] = []
        self.fallar = fallar

    def enviar(self, email: Email) -> None:
        if self.fallar:
            raise RuntimeError("proveedor caído")
        self.enviados.append(email)


class PushEspia:
    def __init__(self):
        self.enviados: list[tuple[dict, object]] = []

    def enviar(self, suscripcion: dict, mensaje) -> None:
        self.enviados.append((suscripcion, mensaje))


@pytest.fixture()
def espia():
    original = app.state.email_sender
    original_push = getattr(app.state, "push_sender", None)
    app.state.email_sender = SenderEspia()
    app.state.push_sender = PushEspia()
    yield app.state.email_sender
    app.state.email_sender = original
    app.state.push_sender = original_push


def reservar(client, db, email=None, telefono="600111222"):
    """Solicitud del pasajero + aceptación del taxista (nuevo flujo)."""
    cot = pedir_cotizacion(client).json()
    datos = {"cotizacion_id": cot["cotizacion_id"], "nombre": "Ana", "telefono": telefono}
    if email:
        datos["email"] = email
    r = client.post("/api/t/demo/reservas", json=datos)
    assert r.status_code == 200, r.text
    return aceptar_pendiente(db, r.json()["solicitud_token"])


def test_confirmacion_con_email_adjunta_justificante(client, db, espia):
    reservar(client, db, email="ana@example.com")
    # Salen dos: el aviso de solicitud pendiente al taxista y, tras aceptar,
    # la confirmación al cliente con el justificante
    al_cliente = [e for e in espia.enviados if e.para == "ana@example.com"]
    assert len(al_cliente) == 1
    email = al_cliente[0]
    assert email.para == "ana@example.com"
    assert "A-000001" in email.asunto
    assert "/r/" in email.html
    assert email.adjuntos and email.adjuntos[0].nombre.startswith("justificante-A-000001")

    notif = db.execute(
        select(Notificacion).where(Notificacion.tipo == "confirmacion")
    ).scalar_one()
    assert (notif.canal, notif.estado) == ("email", "enviada")

    # Y el taxista recibió el aviso de reserva pendiente
    assert any(e.para == "demo@example.com" and "pendiente" in e.asunto
               for e in espia.enviados)


def test_sin_email_no_envia_pero_reserva_ok(client, db, espia):
    reservar(client, db, email=None)
    # Sin email del cliente no hay confirmación, pero el taxista sí recibe aviso
    assert all(e.para == "demo@example.com" for e in espia.enviados)
    tipos = [n.tipo for n in db.execute(select(Notificacion)).scalars()]
    assert "confirmacion" not in tipos


def test_fallo_del_proveedor_no_rompe_la_reserva(client, db, espia):
    espia.fallar = True
    cuerpo = reservar(client, db, email="ana@example.com")  # no lanza: la reserva se crea
    assert cuerpo["justificante"]["numero"] == 1
    estados = {n.estado for n in db.execute(select(Notificacion)).scalars()}
    assert estados == {"fallida"}


def test_cancelacion_envia_email(client, db, espia):
    cuerpo = reservar(client, db, email="ana@example.com")
    client.post(f"/api/reservas/{cuerpo['reserva_token']}/cancelar")
    # Dos avisos: al pasajero y al taxista
    al_pasajero = [e for e in espia.enviados
                   if "cancelada" in e.asunto and e.para == "ana@example.com"]
    assert len(al_pasajero) == 1
    assert any("cancelada" in e.asunto and e.para == "demo@example.com"
               for e in espia.enviados)
    # Cancelar de nuevo no reenvía el email
    total = len(espia.enviados)
    client.post(f"/api/reservas/{cuerpo['reserva_token']}/cancelar")
    assert len(espia.enviados) == total


def test_recordatorios(client, db, espia):
    cuerpo = reservar(client, db, email="ana@example.com")
    espia.enviados.clear()

    # Aún lejos de la recogida: no toca
    assert enviar_recordatorios(db, espia, app.state.push_sender) == 0

    # Adelantamos la recogida a dentro de 20 minutos
    reserva = db.execute(
        select(Reserva).where(Reserva.token_publico == cuerpo["reserva_token"])
    ).scalar_one()
    cot = db.get(Cotizacion, reserva.cotizacion_id)
    cot.fecha_hora_recogida = datetime.now(TZ_MADRID) + timedelta(minutes=20)
    db.commit()

    assert enviar_recordatorios(db, espia, app.state.push_sender) == 1
    assert len(espia.enviados) == 1
    assert "Recordatorio" in espia.enviados[0].asunto
    db.refresh(reserva)
    assert reserva.estado == "recordada"

    # Segunda pasada: no duplica
    assert enviar_recordatorios(db, espia, app.state.push_sender) == 0
    assert len(espia.enviados) == 1
