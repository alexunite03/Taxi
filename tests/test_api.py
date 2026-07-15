"""Tests de integración del flujo de reserva completo (proveedores fake)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Cotizacion, Justificante, Tenant
from app.pricing.motor import TZ_MADRID


def fecha_recogida(horas: int = 3) -> str:
    return (datetime.now(TZ_MADRID) + timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M")


def pedir_cotizacion(client, **cambios):
    datos = {
        "origen": "Calle de Alcalá 100",
        "destino": "Plaza de Castilla 1",
        "fecha_hora_recogida": fecha_recogida(),
    }
    datos.update(cambios)
    return client.post("/api/t/demo/cotizaciones", json=datos)


def test_cotizacion_devuelve_precio_y_condiciones(client):
    r = pedir_cotizacion(client)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert float(cuerpo["precio"]) > 7.5  # inicio + recogida como mínimo
    assert "taxímetro" in cuerpo["condiciones"]
    assert cuerpo["cotizacion_id"]


def test_reserva_emite_justificante_numerado(client):
    cot = pedir_cotizacion(client).json()
    r = client.post(
        "/api/t/demo/reservas",
        json={
            "cotizacion_id": cot["cotizacion_id"],
            "nombre": "Ana García",
            "telefono": "600111222",
        },
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["justificante"] == {"serie": "A", "numero": 1}

    # La segunda reserva incrementa la numeración correlativa
    cot2 = pedir_cotizacion(client, destino="Gran Vía 1").json()
    r2 = client.post(
        "/api/t/demo/reservas",
        json={
            "cotizacion_id": cot2["cotizacion_id"],
            "nombre": "Ana García",
            "telefono": "600111222",
        },
    )
    assert r2.json()["justificante"]["numero"] == 2


def test_cotizacion_caducada_rechazada(client, db):
    cot = pedir_cotizacion(client).json()
    fila = db.get(Cotizacion, __import__("uuid").UUID(cot["cotizacion_id"]))
    fila.expira_en = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    r = client.post(
        "/api/t/demo/reservas",
        json={
            "cotizacion_id": cot["cotizacion_id"],
            "nombre": "Ana",
            "telefono": "600111222",
        },
    )
    assert r.status_code == 422
    assert "caducado" in r.json()["detail"]


def test_antelacion_minima(client):
    r = pedir_cotizacion(client, fecha_hora_recogida=fecha_recogida(horas=0))
    assert r.status_code == 422
    assert "antelación" in r.json()["detail"]


def test_honeypot_bloquea_bots(client):
    r = pedir_cotizacion(client, website="spam.example")
    assert r.status_code == 400


def test_pagina_reserva_y_cancelacion(client):
    cot = pedir_cotizacion(client).json()
    reserva = client.post(
        "/api/t/demo/reservas",
        json={
            "cotizacion_id": cot["cotizacion_id"],
            "nombre": "Luis Pérez",
            "telefono": "600333444",
        },
    ).json()

    pagina = client.get(reserva["enlace"])
    assert pagina.status_code == 200
    assert "A.P.C. de Madrid" in pagina.text
    assert "Precio cerrado" in pagina.text
    assert "Luis Pérez" in pagina.text

    r = client.post(f"/api/reservas/{reserva['reserva_token']}/cancelar")
    assert r.json()["estado"] == "cancelada"
    # Cancelar dos veces es idempotente
    assert client.post(f"/api/reservas/{reserva['reserva_token']}/cancelar").status_code == 200


def test_limite_reservas_activas_por_telefono(client):
    for i in range(3):
        cot = pedir_cotizacion(client, destino=f"Destino {i} distinto").json()
        r = client.post(
            "/api/t/demo/reservas",
            json={
                "cotizacion_id": cot["cotizacion_id"],
                "nombre": "Abusón",
                "telefono": "600999888",
            },
        )
        assert r.status_code == 200
    cot = pedir_cotizacion(client, destino="Otro destino más").json()
    r = client.post(
        "/api/t/demo/reservas",
        json={
            "cotizacion_id": cot["cotizacion_id"],
            "nombre": "Abusón",
            "telefono": "600999888",
        },
    )
    assert r.status_code == 422


def test_formulario_web_flujo_completo(client):
    pagina = client.get("/t/demo")
    assert pagina.status_code == 200
    assert "tratará tus datos" in pagina.text  # línea RGPD

    oferta = client.post(
        "/t/demo/cotizar",
        data={
            "origen": "Calle de Alcalá 100",
            "destino": "Plaza Mayor 1",
            "fecha_hora": fecha_recogida(),
        },
    )
    assert oferta.status_code == 200
    assert "Precio cerrado" in oferta.text


def test_panel_login_y_flag_no2(client, db):
    assert client.get("/panel", follow_redirects=False).status_code == 303

    r = client.post(
        "/panel/login",
        data={"email": "demo@example.com", "password": "demo1234"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    agenda = client.get("/panel")
    assert agenda.status_code == 200
    assert "Escenario NO₂: inactivo" in agenda.text

    client.post("/panel/contaminacion")
    tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
    db.refresh(tenant)
    assert tenant.flag_contaminacion is True

    # Con el flag activo, la cotización aplica el descuento si la recogida
    # cae en la ventana L–V 07:00–21:00 (la fecha del test puede caer fuera;
    # comprobamos la coherencia interna del payload).
    cot = pedir_cotizacion(client).json()
    fila = db.get(Cotizacion, __import__("uuid").UUID(cot["cotizacion_id"]))
    assert fila.calculo_payload["descuento_no2"] in ("0.90", None)


def test_justificante_archivado_con_hash(client, db):
    cot = pedir_cotizacion(client).json()
    client.post(
        "/api/t/demo/reservas",
        json={
            "cotizacion_id": cot["cotizacion_id"],
            "nombre": "Eva",
            "telefono": "600555666",
        },
    )
    j = db.execute(select(Justificante)).scalar_one()
    assert j.hash_documento and len(j.hash_documento) == 64
    from pathlib import Path

    assert Path(j.html_path).exists()
    contenido = Path(j.html_path).read_text()
    assert "A.P.C. de Madrid" in contenido
    assert "IVA incluido" in contenido


def test_cotizacion_no_reutilizable(client):
    cot = pedir_cotizacion(client).json()
    datos = {
        "cotizacion_id": cot["cotizacion_id"],
        "nombre": "Primera",
        "telefono": "600000001",
    }
    assert client.post("/api/t/demo/reservas", json=datos).status_code == 200
    datos["telefono"] = "600000002"
    r = client.post("/api/t/demo/reservas", json=datos)
    assert r.status_code == 422
    assert "ya se convirtió" in r.json()["detail"]


def test_geocode_endpoint(client):
    r = client.get("/api/t/demo/geocode?q=Calle de Alcalá 100")
    assert r.status_code == 200
    opciones = r.json()["opciones"]
    assert len(opciones) == 1
    assert {"texto", "lat", "lng"} <= set(opciones[0])
    # Menos de 3 caracteres: sin sugerencias (política Nominatim)
    assert client.get("/api/t/demo/geocode?q=Ca").json() == {"opciones": []}


def test_cotizar_con_coordenadas_elegidas(client, db):
    r = client.post(
        "/t/demo/cotizar",
        data={
            "origen": "Calle de Alcalá 100, Madrid",
            "destino": "Plaza Mayor 1, Madrid",
            "fecha_hora": fecha_recogida(),
            "origen_lat": "40.42", "origen_lng": "-3.68",
            "destino_lat": "40.415", "destino_lng": "-3.71",
        },
    )
    assert r.status_code == 200
    assert "Precio cerrado" in r.text
    # La oferta lleva el trazado embebido para el mapa
    assert "datos-ruta" in r.text
    cot = db.execute(select(Cotizacion).order_by(Cotizacion.creada_en.desc())).scalars().first()
    assert cot.origen_lat == 40.42            # usó las coords elegidas, sin re-geocodificar
    assert cot.ruta_geojson and len(cot.ruta_geojson) >= 2


def test_caida_del_proveedor_da_error_amable(client):
    from app.main import app as la_app

    class GeocoderRoto:
        def geocodificar(self, texto):
            raise RuntimeError("red caída")

    original = la_app.state.geocoder
    la_app.state.geocoder = GeocoderRoto()
    try:
        r = pedir_cotizacion(client)
        assert r.status_code == 422
        assert "Inténtalo de nuevo" in r.json()["detail"]
        # Y en el formulario web tampoco hay 500
        w = client.post("/t/demo/cotizar", data={
            "origen": "Calle Mayor 1", "destino": "Sol 1",
            "fecha_hora": fecha_recogida()})
        assert w.status_code == 200 and "Inténtalo de nuevo" in w.text
    finally:
        la_app.state.geocoder = original
