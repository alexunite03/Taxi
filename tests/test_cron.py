"""Tests del endpoint /api/cron: caducidad de reservas directas sin
respuesta, aviso al pasajero y aviso de cancelación al taxista."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import SolicitudViaje, Tenant

from .test_bolsa import login_panel
from .test_notificaciones import espia  # noqa: F401
from .test_reserva_directa import solicitar
from .test_social import con_telegram_espia
from .test_telegram_email import _vincular


def _envejecer(db, solicitud, minutos=None):
    solicitud.creada_en = datetime.now(timezone.utc) - timedelta(
        minutes=minutos or settings.solicitud_ttl_min + 1
    )
    db.commit()


def test_cron_requiere_token(client):
    anterior = settings.cron_token
    settings.cron_token = ""
    try:
        assert client.get("/api/cron").status_code == 404  # desactivado
        settings.cron_token = "secreto-cron"
        assert client.get("/api/cron?token=malo").status_code == 404
        r = client.get("/api/cron?token=secreto-cron")
        assert r.status_code == 200
        assert r.json() == {"recordatorios": 0, "solicitudes_caducadas": 0,
                            "solicitudes_anonimizadas": 0,
                            "clientes_anonimizados": 0}
    finally:
        settings.cron_token = anterior


def test_cron_caduca_y_avisa_al_pasajero(client, db, espia):  # noqa: F811
    anterior = settings.cron_token
    settings.cron_token = "secreto-cron"
    try:
        solicitar(client, email="ana@example.com")
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()
        _envejecer(db, solicitud)

        r = client.get("/api/cron?token=secreto-cron")
        assert r.json()["solicitudes_caducadas"] == 1
        db.expire_all()
        assert db.execute(select(SolicitudViaje)).scalar_one().estado == "caducada"
        aviso = [e for e in espia.enviados if e.para == "ana@example.com"]
        assert aviso and "no ha respondido" in aviso[-1].asunto
        # Segunda pasada: no re-caduca ni reenvía
        assert client.get("/api/cron?token=secreto-cron").json()["solicitudes_caducadas"] == 0
    finally:
        settings.cron_token = anterior


def test_caducidad_perezosa_y_reenvio_a_bolsa(client, db, espia):  # noqa: F811
    cuerpo = solicitar(client, email="ana@example.com")
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    _envejecer(db, solicitud)

    # La página de espera la marca caducada sin esperar al cron
    pagina = client.get(f"/s/{cuerpo['solicitud_token']}")
    assert "no ha respondido a tiempo" in pagina.text
    assert "Enviar a la bolsa" in pagina.text
    db.expire_all()
    assert db.execute(select(SolicitudViaje)).scalar_one().estado == "caducada"

    # El taxista ya no puede aceptarla (ni por panel ni por Telegram)
    login_panel(client)
    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                    data={"descuento_pct": "0", "recogida_eur": "5"})
    assert r.status_code == 422 and "caducó" in r.json()["detail"]
    # Y desaparece de sus pendientes
    assert "pendientes de tu confirmación" not in client.get("/panel/bolsa").text
    client.post("/panel/logout")

    # Un clic del pasajero: mismo viaje publicado en la bolsa general
    r = client.post(f"/s/{cuerpo['solicitud_token']}/a-la-bolsa",
                    follow_redirects=False)
    assert r.status_code == 303
    nueva_token = r.headers["location"].split("/s/")[1]
    nueva = db.execute(select(SolicitudViaje).where(
        SolicitudViaje.token_publico == nueva_token)).scalar_one()
    assert nueva.estado == "abierta" and nueva.tenant_destino_id is None
    assert float(nueva.precio_estimado) == float(solicitud.precio_estimado)
    # Los taxistas disponibles reciben el aviso de bolsa
    assert any("Viaje nuevo en la bolsa" in e.asunto for e in espia.enviados)


def test_reenvio_solo_desde_caducada_o_rechazada(client, db, espia):  # noqa: F811
    cuerpo = solicitar(client)
    r = client.post(f"/s/{cuerpo['solicitud_token']}/a-la-bolsa")
    assert r.status_code == 422  # sigue abierta: no procede


def test_cancelacion_del_pasajero_avisa_al_taxista(client, db, espia):  # noqa: F811
    from .test_api import aceptar_pendiente

    original = con_telegram_espia()
    try:
        _vincular(client, db)
        client.post("/panel/logout")
        cuerpo = solicitar(client, email="ana@example.com")
        reserva = aceptar_pendiente(db, cuerpo["solicitud_token"])

        client.post(f"/api/reservas/{reserva['reserva_token']}/cancelar")

        tg = app.state.telegram_sender
        assert any("cancelada por el pasajero" in t for _, t in tg.enviados)
        assert any(e.para == "demo@example.com" and "cancelada" in e.asunto
                   for e in espia.enviados)
    finally:
        app.state.telegram_sender = original
