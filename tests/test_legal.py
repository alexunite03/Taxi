"""Tests de las páginas legales (LSSI / RGPD)."""


def test_aviso_legal(client):
    r = client.get("/aviso-legal")
    assert r.status_code == 200
    assert "no intermedia en el servicio de transporte" in r.text
    assert "RAZÓN SOCIAL DEL PROVEEDOR" in r.text  # placeholder hasta tener SL


def test_cookies_sin_banner(client):
    r = client.get("/cookies")
    assert r.status_code == 200
    assert "no utiliza cookies de analítica" in r.text.lower() or \
        "no utiliza cookies de analítica" in r.text


def test_privacidad_por_tenant(client):
    r = client.get("/t/demo/privacidad")
    assert r.status_code == 200
    assert "Taxi Demo" in r.text            # responsable = el taxista
    assert "4 años" in r.text               # conservación
    assert "aepd.es" in r.text              # tutela ante la AEPD
    assert "art. 6.1.b RGPD" in r.text      # base jurídica


def test_privacidad_de_slug_inexistente(client):
    assert client.get("/t/nadie/privacidad").status_code == 404


def test_formulario_enlaza_la_privacidad(client):
    r = client.get("/t/demo")
    assert '/t/demo/privacidad' in r.text


def test_pie_con_enlaces_legales(client):
    r = client.get("/t/demo")
    assert '/aviso-legal' in r.text and '/cookies' in r.text


def test_pagina_raiz_no_da_404(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "precio cerrado" in r.text.lower()
    assert "/registro/taxista" in r.text
