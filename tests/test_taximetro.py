"""Tests de la reserva SIN precio cerrado (modo taxímetro): la opción para
trayectos de aeropuerto (tarifa fija) o para quien prefiera el taxímetro.
La plataforma no interviene en el precio; el taxista solo acepta o rechaza,
y no hay cotización, reserva ni justificante."""
from sqlalchemy import select

from app.main import app
from app.models import Reserva, SolicitudViaje

from .test_api import fecha_recogida
from .test_bolsa import login_panel
from .test_cumplimiento import AEROPUERTO, CENTRO
from .test_notificaciones import espia  # noqa: F811, F401
from .test_social import con_telegram_espia
from .test_telegram_email import _vincular


def pedir_taximetro(client, db, email="ana@example.com"):
    """Flujo web completo hasta la solicitud pendiente (destino aeropuerto)."""
    confirmacion = client.post("/t/demo/cotizar", data={
        "origen": "Calle de Alcalá 100",
        "destino": "Aeropuerto T4",
        "fecha_hora": fecha_recogida(),
        "modo": "taximetro",
        "origen_lat": CENTRO["lat"], "origen_lng": CENTRO["lng"],
        "destino_lat": AEROPUERTO["lat"], "destino_lng": AEROPUERTO["lng"],
    })
    assert confirmacion.status_code == 200
    assert "Según taxímetro" in confirmacion.text
    assert "reservar-taximetro" in confirmacion.text

    r = client.post("/t/demo/reservar-taximetro", data={
        "origen_texto": "Calle de Alcalá 100, Madrid",
        "origen_lat": CENTRO["lat"], "origen_lng": CENTRO["lng"],
        "destino_texto": "Aeropuerto T4, Madrid",
        "destino_lat": AEROPUERTO["lat"], "destino_lng": AEROPUERTO["lng"],
        "fecha_hora": fecha_recogida(),
        "nombre": "Ana", "telefono": "600111222", "email": email,
    }, follow_redirects=False)
    assert r.status_code == 303 and "/s/" in r.headers["location"]
    token = r.headers["location"].split("/s/")[1]
    return db.execute(select(SolicitudViaje)).scalar_one(), token


def test_formulario_ofrece_ambos_modos(client):
    pagina = client.get("/t/demo")
    assert "Precio cerrado por adelantado" in pagina.text
    assert "Con taxímetro" in pagina.text


def test_flujo_taximetro_completo_por_panel(client, db, espia):  # noqa: F811
    solicitud, token = pedir_taximetro(client, db)
    assert solicitud.modo == "taximetro"
    assert solicitud.tenant_destino_id is not None
    assert float(solicitud.precio_estimado) == 0

    # Página de espera sin precio: la tarjeta dice «Según taxímetro»
    espera = client.get(f"/s/{token}")
    assert "Según taxímetro" in espera.text
    assert "Precio máximo" not in espera.text

    # El taxista lo ve en pendientes sin campos de precio y acepta
    login_panel(client)
    bolsa = client.get("/panel/bolsa")
    assert "🕐 Taxímetro" in bolsa.text
    assert "Cobro por taxímetro a bordo" in bolsa.text
    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                    follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.estado == "asignada"
    assert solicitud.reserva_id is None
    # Sin cotización ni reserva ni justificante: la plataforma no puso precio
    assert db.execute(select(Reserva)).first() is None

    # El pasajero ve la confirmación en su enlace (sin redirección a /r/)
    confirmada = client.get(f"/s/{token}")
    assert confirmada.status_code == 200
    assert "Reserva\nconfirmada" in confirmada.text or "Reserva confirmada" in confirmada.text
    assert "taxímetro" in confirmada.text
    # Y recibió el email con la explicación del taxímetro
    aviso = [e for e in espia.enviados if e.para == "ana@example.com"]
    assert aviso and "confirmada" in aviso[-1].asunto
    assert "taxímetro" in aviso[-1].html

    # Cancelación de la reserva confirmada → se avisa al taxista
    original = con_telegram_espia()
    try:
        r = client.post(f"/s/{token}/cancelar", follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        assert db.execute(select(SolicitudViaje)).scalar_one().estado == "cancelada"
        assert any(e.para == "demo@example.com" and "cancelada" in e.asunto
                   for e in espia.enviados)
    finally:
        app.state.telegram_sender = original


def test_flujo_taximetro_por_telegram(client, db, espia):  # noqa: F811
    from .test_reserva_directa import callback

    original = con_telegram_espia()
    try:
        _vincular(client, db)
        client.post("/panel/logout")
        solicitud, token = pedir_taximetro(client, db)
        tg = app.state.telegram_sender

        # Aviso con hoja de ruta de taxímetro y botones SIN menú de precio
        avisos = [t for _, t in tg.enviados if "pendiente" in t]
        assert avisos and "taxímetro" in avisos[0]
        textos = [b["texto"] for fila in tg.botones for b in fila]
        assert any("taxímetro" in t for t in textos)
        assert not any("Ajustar el precio" in t for t in textos)
        # El PDF de la hoja de ruta acompaña al aviso
        assert any(n == "hoja-de-ruta.pdf" for _, n, c in tg.documentos)

        # Acepta con el botón
        callback(client, f"sol:{solicitud.id}:a:0")
        assert any("taxímetro" in t for _, t in tg.callbacks)
        db.expire_all()
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()
        assert solicitud.estado == "asignada" and solicitud.reserva_id is None
        # Hoja de ruta confirmada al taxista con enlace /s/
        assert any(f"/s/{token}" in t for _, t in tg.enviados)
    finally:
        app.state.telegram_sender = original


def test_taximetro_rechazado_no_va_a_bolsa(client, db, espia):  # noqa: F811
    solicitud, token = pedir_taximetro(client, db)
    login_panel(client)
    r = client.post(f"/panel/solicitudes/{solicitud.id}/rechazar",
                    follow_redirects=False)
    assert r.status_code == 303
    pagina = client.get(f"/s/{token}")
    assert "elegir otro taxista del listado" in pagina.text
    assert "Enviar a la bolsa" not in pagina.text
    r = client.post(f"/s/{token}/a-la-bolsa")
    assert r.status_code == 422


def test_aeropuerto_sugiere_taximetro(client, db):
    """El precio cerrado al aeropuerto se rechaza Y se sugiere la
    alternativa con taxímetro en el propio formulario."""
    r = client.post("/t/demo/cotizar", data={
        "origen": "Calle de Alcalá 100",
        "destino": "Aeropuerto T4",
        "fecha_hora": fecha_recogida(),
        "modo": "precio_cerrado",
        "origen_lat": CENTRO["lat"], "origen_lng": CENTRO["lng"],
        "destino_lat": AEROPUERTO["lat"], "destino_lng": AEROPUERTO["lng"],
    })
    assert r.status_code == 200
    assert "tarifa fija" in r.text
    assert "Con taxímetro" in r.text  # el selector queda a mano


def test_export_art47_incluye_taximetro(client, db, espia):  # noqa: F811
    from app.config import settings

    anterior = settings.admin_token
    settings.admin_token = "admin-123"
    try:
        solicitud, _ = pedir_taximetro(client, db)
        login_panel(client)
        client.post(f"/panel/solicitudes/{solicitud.id}/aceptar")
        csv = client.get(
            "/api/admin/export/art47.csv?token=admin-123&conjunto=servicios"
        ).text
        assert "taximetro" in csv and "web_taximetro" in csv
    finally:
        settings.admin_token = anterior


def _publicar_taximetro_bolsa(client, db):
    """Publica un viaje de taxímetro al aeropuerto en la bolsa (/viaje)."""
    from .test_bolsa import login_viajero

    login_viajero(client)
    r = client.post("/viaje", data={
        "origen": "Calle de Alcalá 100", "destino": "Aeropuerto T4",
        "fecha_hora": fecha_recogida(),
        "nombre": "Carlos Vega", "telefono": "699887766",
        "email": "carlos@example.com", "modo": "taximetro",
        "origen_lat": CENTRO["lat"], "origen_lng": CENTRO["lng"],
        "destino_lat": AEROPUERTO["lat"], "destino_lng": AEROPUERTO["lng"],
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    token = r.headers["location"].split("/s/")[1]
    return db.execute(select(SolicitudViaje)).scalar_one(), token


def test_bolsa_ofrece_selector_de_modo(client, db):
    from .test_bolsa import login_viajero

    login_viajero(client)
    pagina = client.get("/viaje")
    assert "Con taxímetro" in pagina.text
    assert "tú eliges" in pagina.text  # texto del modelo de ofertas


def test_aeropuerto_en_bolsa_con_taximetro(client, db, espia):  # noqa: F811
    solicitud, token = _publicar_taximetro_bolsa(client, db)
    assert solicitud.modo == "taximetro"
    assert solicitud.tenant_destino_id is None
    assert float(solicitud.precio_estimado) == 0

    # El taxista se ofrece SIN precio (botón simple)
    login_panel(client)
    bolsa = client.get("/panel/bolsa")
    assert "🕐 Taxímetro" in bolsa.text
    assert "Ofrecerme" in bolsa.text
    r = client.post(f"/panel/solicitudes/{solicitud.id}/ofertar",
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    oferta = solicitud.ofertas[0]
    assert float(oferta.precio) == 0
    client.post("/panel/logout")

    # El pasajero ve «Se ofrece» sin precio y elige
    pagina = client.get(f"/s/{token}")
    assert "Se ofrece" in pagina.text
    assert "Elegir a Taxi" in pagina.text
    r = client.post(f"/s/{token}/elegir",
                    data={"oferta_id": str(oferta.id)}, follow_redirects=False)
    assert r.status_code == 303 and f"/s/{token}" in r.headers["location"]

    db.expire_all()
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.estado == "asignada"
    assert solicitud.reserva_id is None  # sin reserva ni justificante
    assert solicitud.tenant_destino_id is not None  # dirigida al elegido
    assert db.execute(select(Reserva)).first() is None

    confirmada = client.get(f"/s/{token}")
    assert "Reserva" in confirmada.text and "confirmada" in confirmada.text
    assert any("taxímetro" in e.html for e in espia.enviados
               if e.para == "carlos@example.com")
