"""Tests de la capa de email: confirmación, cancelación y recordatorios."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Cotizacion, Notificacion, Reserva
from app.notificaciones import Email
from app.pricing.motor import TZ_MADRID
from app.services.notificaciones import enviar_recordatorios

from .test_api import fecha_recogida, pedir_cotizacion


class SenderEspia:
    def __init__(self, fallar: bool = False):
        self.enviados: list[Email] = []
        self.fallar = fallar

    def enviar(self, email: Email) -> None:
        if self.fallar:
            raise RuntimeError("proveedor caído")
        self.enviados.append(email)


@pytest.fixture()
def espia():
    original = app.state.email_sender
    app.state.email_sender = SenderEspia()
    yield app.state.email_sender
    app.state.email_sender = original


def reservar(client, email=None, telefono="600111222"):
    cot = pedir_cotizacion(client).json()
    datos = {"cotizacion_id": cot["cotizacion_id"], "nombre": "Ana", "telefono": telefono}
    if email:
        datos["email"] = email
    r = client.post("/api/t/demo/reservas", json=datos)
    assert r.status_code == 200, r.text
    return r.json()


def test_confirmacion_con_email_adjunta_justificante(client, db, espia):
    reservar(client, email="ana@example.com")
    assert len(espia.enviados) == 1
    email = espia.enviados[0]
    assert email.para == "ana@example.com"
    assert "A-000001" in email.asunto
    assert "/r/" in email.html
    assert email.adjuntos and email.adjuntos[0].nombre.startswith("justificante-A-000001")

    notif = db.execute(select(Notificacion)).scalar_one()
    assert (notif.canal, notif.tipo, notif.estado) == ("email", "confirmacion", "enviada")


def test_sin_email_no_envia_pero_reserva_ok(client, db, espia):
    reservar(client, email=None)
    assert espia.enviados == []
    assert db.execute(select(Notificacion)).first() is None


def test_fallo_del_proveedor_no_rompe_la_reserva(client, db, espia):
    espia.fallar = True
    cuerpo = reservar(client, email="ana@example.com")  # no lanza: la reserva se crea
    assert cuerpo["justificante"]["numero"] == 1
    notif = db.execute(select(Notificacion)).scalar_one()
    assert notif.estado == "fallida"


def test_cancelacion_envia_email(client, db, espia):
    cuerpo = reservar(client, email="ana@example.com")
    client.post(f"/api/reservas/{cuerpo['reserva_token']}/cancelar")
    tipos = [e.asunto for e in espia.enviados]
    assert len(espia.enviados) == 2
    assert "cancelada" in tipos[1]
    # Cancelar de nuevo no reenvía el email
    client.post(f"/api/reservas/{cuerpo['reserva_token']}/cancelar")
    assert len(espia.enviados) == 2


def test_recordatorios(client, db, espia):
    cuerpo = reservar(client, email="ana@example.com")
    espia.enviados.clear()

    # Aún lejos de la recogida: no toca
    assert enviar_recordatorios(db, espia) == 0

    # Adelantamos la recogida a dentro de 20 minutos
    reserva = db.execute(
        select(Reserva).where(Reserva.token_publico == cuerpo["reserva_token"])
    ).scalar_one()
    cot = db.get(Cotizacion, reserva.cotizacion_id)
    cot.fecha_hora_recogida = datetime.now(TZ_MADRID) + timedelta(minutes=20)
    db.commit()

    assert enviar_recordatorios(db, espia) == 1
    assert len(espia.enviados) == 1
    assert "Recordatorio" in espia.enviados[0].asunto
    db.refresh(reserva)
    assert reserva.estado == "recordada"

    # Segunda pasada: no duplica
    assert enviar_recordatorios(db, espia) == 0
    assert len(espia.enviados) == 1
