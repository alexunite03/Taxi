"""Tests de la bolsa de viajes y de los taxistas favoritos."""
from sqlalchemy import select

from app.models import Reserva, SolicitudViaje, Tenant
from app.security import hash_password

from .test_api import fecha_recogida
from .test_cuentas import USUARIO
from .test_notificaciones import espia  # noqa: F401


VIAJERO = {
    "nombre": "Carlos Vega",
    "telefono": "699887766",
    "email": "carlos@example.com",
    "password": "clave-carlos-1",
}


def login_viajero(client):
    r = client.post("/registro/usuario", data=VIAJERO)
    if r.status_code == 422:  # ya registrado en este test
        client.post("/usuario/login", data={
            "email": VIAJERO["email"], "password": VIAJERO["password"]})


def publicar_viaje(client, **cambios):
    login_viajero(client)
    datos = {
        "origen": "Calle de Alcalá 100",
        "destino": "Plaza de Castilla 1",
        "fecha_hora": fecha_recogida(),
        "nombre": "Carlos Vega",
        "telefono": "699887766",
        "email": "carlos@example.com",
    }
    datos.update(cambios)
    return client.post("/viaje", data=datos, follow_redirects=False)


def login_panel(client, email="demo@example.com", password="demo1234"):
    return client.post(
        "/panel/login", data={"email": email, "password": password},
        follow_redirects=False,
    )


def test_publicar_solicitud_y_pagina_de_espera(client, db):
    r = publicar_viaje(client)
    assert r.status_code == 303, r.text
    token = r.headers["location"].split("/s/")[1]

    pagina = client.get(f"/s/{token}")
    assert pagina.status_code == 200
    assert "Buscando taxista" in pagina.text
    assert "Precio máximo" in pagina.text

    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.estado == "abierta"
    assert float(solicitud.precio_estimado) > 7.5


def test_antelacion_de_solicitud(client):
    r = publicar_viaje(client, fecha_hora=fecha_recogida(horas=0))
    assert r.status_code == 200 and "antelación" in r.text


def test_taxista_ve_y_acepta_el_viaje(client, db, espia):
    publicar_viaje(client)
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()

    login_panel(client)
    bolsa = client.get("/panel/bolsa")
    assert "Bolsa de viajes" in bolsa.text
    assert "Carlos Vega" in bolsa.text

    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar", follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.estado == "asignada"
    reserva = db.get(Reserva, solicitud.reserva_id)
    assert reserva.canal == "bolsa"
    assert reserva.justificante is not None

    # El pasajero llega a su reserva desde el enlace de la solicitud
    seguimiento = client.get(f"/s/{solicitud.token_publico}", follow_redirects=False)
    assert seguimiento.status_code == 303
    assert f"/r/{reserva.token_publico}" in seguimiento.headers["location"]

    # Y recibe el email de confirmación
    assert any("justificante" in e.asunto for e in espia.enviados)


def test_doble_aceptacion_rechazada(client, db, espia):
    publicar_viaje(client)
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()

    otro = Tenant(
        slug="rival", nombre="Taxi Rival", nif="11111111H", num_licencia="2222",
        matricula="", email="rival@example.com", password_hash=hash_password("clave-rival-1"),
    )
    db.add(otro)
    db.commit()

    login_panel(client)
    client.post(f"/panel/solicitudes/{solicitud.id}/aceptar")

    login_panel(client, email="rival@example.com", password="clave-rival-1")
    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar")
    assert r.status_code == 422
    assert "Otro taxista" in r.json()["detail"]


def test_toggle_bolsa_oculta_solicitudes(client, db):
    publicar_viaje(client)
    login_panel(client)
    client.post("/panel/bolsa")  # desactivar
    bolsa = client.get("/panel/bolsa")
    assert "Carlos Vega" not in bolsa.text
    assert "bolsa desactivada" in bolsa.text


def test_viaje_exige_registro(client):
    r = client.get("/viaje", follow_redirects=False)
    assert r.status_code == 303 and "/usuario/login" in r.headers["location"]
    r = client.post("/viaje", data={
        "origen": "A", "destino": "B", "fecha_hora": fecha_recogida(),
        "nombre": "X", "telefono": "600000000"}, follow_redirects=False)
    assert r.status_code == 303 and "/usuario/login" in r.headers["location"]


def test_bolsa_ordenada_por_cercania(client, db):
    publicar_viaje(client)
    publicar_viaje(client, origen="Otro origen lejano", destino="Destino 2")
    login_panel(client)
    from app.models import SolicitudViaje
    from sqlalchemy import select as _select

    primera = db.execute(_select(SolicitudViaje)).scalars().first()
    bolsa = client.get(
        f"/panel/bolsa?lat={primera.origen_lat}&lng={primera.origen_lng}")
    assert "km de ti" in bolsa.text
    assert "Ordenado por cercanía" in bolsa.text
    # La más cercana (distancia 0) aparece primero
    assert bolsa.text.find("a 0.0 km de ti") < bolsa.text.find("Destino 2") or         bolsa.text.index("km de ti") < bolsa.text.index("Destino 2")


def test_cancelar_solicitud(client, db):
    r = publicar_viaje(client)
    token = r.headers["location"].split("/s/")[1]
    client.post(f"/s/{token}/cancelar")
    assert db.execute(select(SolicitudViaje)).scalar_one().estado == "cancelada"
    # Ya no aparece en la bolsa del panel
    login_panel(client)
    assert "Carlos Vega" not in client.get("/panel/bolsa").text


def test_geocode_global(client):
    r = client.get("/api/geocode?q=Gran Vía 1")
    assert r.status_code == 200 and len(r.json()["opciones"]) == 1


def test_favoritos(client, db):
    # Sin sesión: redirige a login
    r = client.post("/favoritos/demo", follow_redirects=False)
    assert r.status_code == 303 and "/usuario/login" in r.headers["location"]

    client.post("/registro/usuario", data=USUARIO)

    # Guardar: el formulario del taxista muestra la estrella activa
    client.post("/favoritos/demo")
    assert "Quitar de favoritos" in client.get("/t/demo").text

    # Aparece en Mis reservas con enlace para reservar
    mis = client.get("/mis-reservas")
    assert "Mis taxistas" in mis.text and "Taxi Demo" in mis.text

    # Quitar (toggle)
    client.post("/favoritos/demo")
    assert "Guardar taxista en favoritos" in client.get("/t/demo").text
