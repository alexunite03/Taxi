"""Tests de Web Push: alta de suscripción, envío y limpieza de caducadas."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Cotizacion, Notificacion, PushSuscripcion, Reserva
from app.notificaciones import SuscripcionCaducada
from app.pricing.motor import TZ_MADRID
from app.services.notificaciones import enviar_recordatorios

from .test_api import pedir_cotizacion
from .test_notificaciones import PushEspia, SenderEspia, espia, reservar  # noqa: F401

SUSCRIPCION = {
    "endpoint": "https://push.example/abc123",
    "keys": {"p256dh": "clave-p256dh", "auth": "clave-auth"},
}


def suscribir(client, token, suscripcion=None):
    return client.post(
        "/api/push/suscripcion",
        json={"token": token, "suscripcion": suscripcion or SUSCRIPCION},
    )


def test_alta_idempotente_por_endpoint(client, db, espia):  # noqa: F811
    cuerpo = reservar(client, email=None)
    assert suscribir(client, cuerpo["reserva_token"]).status_code == 200
    assert suscribir(client, cuerpo["reserva_token"]).status_code == 200
    filas = db.execute(select(PushSuscripcion)).scalars().all()
    assert len(filas) == 1
    assert filas[0].endpoint == SUSCRIPCION["endpoint"]


def test_alta_con_token_falso_o_datos_invalidos(client, espia):  # noqa: F811
    assert suscribir(client, "token-inventado").status_code == 404
    cuerpo = reservar(client, email=None)
    r = suscribir(client, cuerpo["reserva_token"], {"endpoint": "http://inseguro", "keys": {}})
    assert r.status_code == 422


def test_clave_publica_vacia_en_desarrollo(client):
    r = client.get("/api/push/clave-publica")
    assert r.status_code == 200
    assert r.json() == {"clave": None}


def test_cancelacion_envia_push(client, db, espia):  # noqa: F811
    cuerpo = reservar(client, email=None)
    suscribir(client, cuerpo["reserva_token"])
    client.post(f"/api/reservas/{cuerpo['reserva_token']}/cancelar")

    push = app.state.push_sender
    assert len(push.enviados) == 1
    suscripcion, mensaje = push.enviados[0]
    assert suscripcion["endpoint"] == SUSCRIPCION["endpoint"]
    assert mensaje.titulo == "Reserva cancelada"

    notif = db.execute(
        select(Notificacion).where(Notificacion.canal == "push")
    ).scalar_one()
    assert (notif.tipo, notif.estado) == ("cancelacion", "enviada")


def test_recordatorio_envia_push_y_email(client, db, espia):  # noqa: F811
    cuerpo = reservar(client, email="ana@example.com")
    suscribir(client, cuerpo["reserva_token"])
    espia.enviados.clear()

    reserva = db.execute(
        select(Reserva).where(Reserva.token_publico == cuerpo["reserva_token"])
    ).scalar_one()
    cot = db.get(Cotizacion, reserva.cotizacion_id)
    cot.fecha_hora_recogida = datetime.now(TZ_MADRID) + timedelta(minutes=20)
    db.commit()

    push = app.state.push_sender
    assert enviar_recordatorios(db, espia, push) == 1
    assert len(push.enviados) == 1
    assert push.enviados[0][1].titulo == "Tu taxi llega pronto"
    assert len(espia.enviados) == 1  # el email también sale


def test_suscripcion_caducada_se_borra(client, db, espia):  # noqa: F811
    class PushCaducado:
        def enviar(self, suscripcion, mensaje):
            raise SuscripcionCaducada("410 Gone")

    cuerpo = reservar(client, email=None)
    suscribir(client, cuerpo["reserva_token"])
    app.state.push_sender = PushCaducado()

    client.post(f"/api/reservas/{cuerpo['reserva_token']}/cancelar")

    assert db.execute(select(PushSuscripcion)).first() is None
    notif = db.execute(
        select(Notificacion).where(Notificacion.canal == "push")
    ).scalar_one()
    assert notif.estado == "caducada"
