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


def aceptar_pendiente(db, solicitud_token):
    """El taxista destinatario acepta la solicitud (como haría desde el panel
    o Telegram) y devuelve el cuerpo equivalente a la antigua reserva
    instantánea de la API."""
    from app.main import app
    from app.models import SolicitudViaje
    from app.services.bolsa import aceptar_solicitud
    from app.services.notificaciones import notificar_confirmacion

    solicitud = db.execute(
        select(SolicitudViaje).where(SolicitudViaje.token_publico == solicitud_token)
    ).scalar_one()
    destino = db.get(Tenant, solicitud.tenant_destino_id)
    solicitud, reserva = aceptar_solicitud(
        db, destino, solicitud.id, app.state.geocoder, app.state.rutas
    )
    notificar_confirmacion(db, app.state.email_sender, reserva)
    j = reserva.justificante
    return {
        "reserva_token": reserva.token_publico,
        "enlace": f"/r/{reserva.token_publico}",
        "precio_cerrado": str(reserva.precio_cerrado),
        "justificante": {"serie": j.serie, "numero": j.numero},
        "estado": reserva.estado,
    }


def reservar_directa(client, db, cotizacion_id, nombre="Ana García",
                     telefono="600111222", email=None):
    """Flujo completo: el pasajero solicita y el taxista acepta."""
    datos = {"cotizacion_id": cotizacion_id, "nombre": nombre, "telefono": telefono}
    if email:
        datos["email"] = email
    r = client.post("/api/t/demo/reservas", json=datos)
    assert r.status_code == 200, r.text
    return aceptar_pendiente(db, r.json()["solicitud_token"])


def test_cotizacion_devuelve_precio_y_condiciones(client):
    r = pedir_cotizacion(client)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert float(cuerpo["precio"]) > 7.5  # inicio + recogida como mínimo
    assert "taxímetro" in cuerpo["condiciones"]
    assert cuerpo["cotizacion_id"]


def test_reserva_emite_justificante_numerado(client, db):
    cot = pedir_cotizacion(client).json()
    cuerpo = reservar_directa(client, db, cot["cotizacion_id"])
    assert cuerpo["justificante"] == {"serie": "A", "numero": 1}

    # La segunda reserva incrementa la numeración correlativa
    cot2 = pedir_cotizacion(client, destino="Gran Vía 1").json()
    cuerpo2 = reservar_directa(client, db, cot2["cotizacion_id"])
    assert cuerpo2["justificante"]["numero"] == 2


def test_solicitud_queda_pendiente_hasta_que_el_taxista_acepta(client, db):
    from app.models import Reserva, SolicitudViaje

    cot = pedir_cotizacion(client).json()
    r = client.post(
        "/api/t/demo/reservas",
        json={"cotizacion_id": cot["cotizacion_id"], "nombre": "Ana",
              "telefono": "600111222"},
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "abierta"
    assert cuerpo["precio_maximo"] == cot["precio"]

    # Sin aceptación no hay reserva ni justificante
    assert db.execute(select(Reserva)).first() is None
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.tenant_destino_id is not None

    # La página de espera muestra el precio máximo y al taxista
    pagina = client.get(cuerpo["enlace"])
    assert pagina.status_code == 200
    assert "Esperando la confirmación" in pagina.text
    assert "Precio máximo" in pagina.text


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


def test_pagina_reserva_y_cancelacion(client, db):
    cot = pedir_cotizacion(client).json()
    reserva = reservar_directa(
        client, db, cot["cotizacion_id"], nombre="Luis Pérez", telefono="600333444"
    )

    pagina = client.get(reserva["enlace"])
    assert pagina.status_code == 200
    assert "A.P.C. de Madrid" in pagina.text
    assert "Precio cerrado" in pagina.text
    assert "Luis Pérez" in pagina.text

    r = client.post(f"/api/reservas/{reserva['reserva_token']}/cancelar")
    assert r.json()["estado"] == "cancelada"
    # Cancelar dos veces es idempotente
    assert client.post(f"/api/reservas/{reserva['reserva_token']}/cancelar").status_code == 200


def test_limite_reservas_activas_por_telefono(client, db):
    for i in range(3):
        cot = pedir_cotizacion(client, destino=f"Destino {i} distinto").json()
        reservar_directa(
            client, db, cot["cotizacion_id"], nombre="Abusón", telefono="600999888"
        )
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
    assert "Precio máximo" in oferta.text
    assert "Solicitar reserva" in oferta.text


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
    reservar_directa(client, db, cot["cotizacion_id"], nombre="Eva", telefono="600555666")
    j = db.execute(select(Justificante)).scalar_one()
    assert j.hash_documento and len(j.hash_documento) == 64
    from pathlib import Path

    assert Path(j.html_path).exists()
    contenido = Path(j.html_path).read_text()
    assert "A.P.C. de Madrid" in contenido
    assert "IVA incluido" in contenido


def test_justificante_se_regenera_si_el_disco_se_vacia(client, db):
    """En un PaaS el disco es efímero: tras un redeploy los archivos ya no
    están. El documento debe regenerarse desde la copia en la BD."""
    from pathlib import Path

    from app.models import Reserva
    from app.services.notificaciones import _adjunto_justificante

    cot = pedir_cotizacion(client).json()
    reservar_directa(client, db, cot["cotizacion_id"])
    j = db.execute(select(Justificante)).scalar_one()
    original = Path(j.html_path).read_text()
    Path(j.html_path).unlink()  # «redeploy»: el disco se vacía

    client.post("/panel/login", data={"email": "demo@example.com",
                                      "password": "demo1234"})
    r = client.get(f"/panel/reservas/{j.reserva_id}/justificante")
    assert r.status_code == 200
    assert Path(j.html_path).read_text() == original

    # El adjunto del email también se recupera
    Path(j.html_path).unlink()
    reserva = db.get(Reserva, j.reserva_id)
    adjuntos = _adjunto_justificante(reserva)
    assert adjuntos and adjuntos[0].nombre.endswith(".html")


def test_cotizacion_no_reutilizable(client, db):
    cot = pedir_cotizacion(client).json()
    datos = {
        "cotizacion_id": cot["cotizacion_id"],
        "nombre": "Primera",
        "telefono": "600000001",
    }
    r = client.post("/api/t/demo/reservas", json=datos)
    assert r.status_code == 200

    # Con la solicitud pendiente, la misma cotización no se puede repetir
    datos["telefono"] = "600000002"
    repetida = client.post("/api/t/demo/reservas", json=datos)
    assert repetida.status_code == 422
    assert "Ya has solicitado" in repetida.json()["detail"]

    # Y una vez aceptada, tampoco
    aceptar_pendiente(db, r.json()["solicitud_token"])
    repetida = client.post("/api/t/demo/reservas", json=datos)
    assert repetida.status_code == 422
    assert "ya se convirtió" in repetida.json()["detail"]


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
    assert "Precio máximo" in r.text
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
