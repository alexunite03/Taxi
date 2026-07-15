"""Tests del registro de taxistas y de la cuenta opcional del pasajero."""
from sqlalchemy import select

from app.models import Tenant, Usuario

from .test_api import fecha_recogida, pedir_cotizacion

TAXISTA = {
    "nombre": "José Luis Gómez",
    "nif": "12345678Z",
    "num_licencia": "7777",
    "matricula": "1234BCD",
    "email": "jose@example.com",
    "password": "clave-segura-1",
}

USUARIO = {
    "nombre": "Ana García",
    "telefono": "600111222",
    "email": "ana-cuenta@example.com",
    "password": "otra-clave-1",
}


def test_pagina_de_eleccion(client):
    r = client.get("/registro")
    assert r.status_code == 200
    assert "/registro/usuario" in r.text and "/registro/taxista" in r.text


def test_registro_taxista_crea_tenant_y_entra_al_panel(client, db):
    r = client.post("/registro/taxista", data=TAXISTA, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/panel"

    tenant = db.execute(
        select(Tenant).where(Tenant.email == "jose@example.com")
    ).scalar_one()
    assert tenant.slug == "jose-luis-gomez"
    assert tenant.nif == "12345678Z"

    # Sesión iniciada: el panel responde
    assert client.get("/panel").status_code == 200
    # Y su página pública de reserva existe
    assert client.get(f"/t/{tenant.slug}").status_code == 200


def test_registro_taxista_slug_unico_y_email_duplicado(client, db):
    client.post("/registro/taxista", data=TAXISTA)
    otro = dict(TAXISTA, email="jose2@example.com", num_licencia="8888")
    client.post("/registro/taxista", data=otro)
    slugs = sorted(s for (s,) in db.execute(select(Tenant.slug)) if s.startswith("jose"))
    assert slugs == ["jose-luis-gomez", "jose-luis-gomez-2"]

    r = client.post("/registro/taxista", data=TAXISTA)
    assert r.status_code == 422 and "Ya existe" in r.text


def test_registro_taxista_validaciones(client):
    corto = dict(TAXISTA, password="corta")
    assert client.post("/registro/taxista", data=corto).status_code == 422
    letras = dict(TAXISTA, num_licencia="ABC")
    assert client.post("/registro/taxista", data=letras).status_code == 422


def test_registro_usuario_y_mis_reservas(client, db):
    # Reserva hecha ANTES de registrarse, con el mismo teléfono
    cot = pedir_cotizacion(client).json()
    client.post(
        "/api/t/demo/reservas",
        json={"cotizacion_id": cot["cotizacion_id"], "nombre": "Ana",
              "telefono": USUARIO["telefono"]},
    )

    r = client.post("/registro/usuario", data=USUARIO, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mis-reservas"

    pagina = client.get("/mis-reservas")
    assert pagina.status_code == 200
    assert "Plaza de Castilla" in pagina.text  # la reserva previa aparece
    assert "Ver justificante" in pagina.text


def test_usuario_login_logout(client):
    client.post("/registro/usuario", data=USUARIO)
    client.post("/usuario/logout")

    # Sin sesión, /mis-reservas redirige al login
    r = client.get("/mis-reservas", follow_redirects=False)
    assert r.status_code == 303

    mal = client.post("/usuario/login", data={"email": USUARIO["email"], "password": "no"})
    assert mal.status_code == 401
    bien = client.post(
        "/usuario/login",
        data={"email": USUARIO["email"], "password": USUARIO["password"]},
        follow_redirects=False,
    )
    assert bien.status_code == 303
    assert client.get("/mis-reservas").status_code == 200


def test_email_usuario_duplicado(client):
    client.post("/registro/usuario", data=USUARIO)
    r = client.post("/registro/usuario", data=USUARIO)
    assert r.status_code == 422 and "Ya existe" in r.text


def test_oferta_prerrellenada_con_sesion(client):
    client.post("/registro/usuario", data=USUARIO)
    oferta = client.post(
        "/t/demo/cotizar",
        data={"origen": "Calle de Alcalá 100", "destino": "Plaza Mayor 1",
              "fecha_hora": fecha_recogida()},
    )
    assert oferta.status_code == 200
    assert 'value="Ana García"' in oferta.text
    assert 'value="600111222"' in oferta.text
