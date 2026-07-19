"""Tests del flujo de reserva directa: el pasajero solicita al precio máximo
y el taxista acepta (con descuento opcional) o rechaza, desde el panel o con
los botones de Telegram."""

from sqlalchemy import select

from app.main import app
from app.models import Reserva, SolicitudViaje, Tenant

from .test_api import fecha_recogida, pedir_cotizacion
from .test_bolsa import login_panel
from .test_notificaciones import espia  # noqa: F401
from .test_social import con_telegram_espia
from .test_telegram_email import _vincular


def solicitar(client, email=None, telefono="600111222"):
    cot = pedir_cotizacion(client).json()
    datos = {"cotizacion_id": cot["cotizacion_id"], "nombre": "Ana",
             "telefono": telefono}
    if email:
        datos["email"] = email
    r = client.post("/api/t/demo/reservas", json=datos)
    assert r.status_code == 200, r.text
    return r.json()


def callback(client, datos, chat_id=777000, callback_id="cb-1"):
    return client.post("/api/telegram/webhook", json={"callback_query": {
        "id": callback_id,
        "data": datos,
        "from": {"id": chat_id},
        "message": {"chat": {"id": chat_id}},
    }})


def test_reservar_desde_la_web_crea_solicitud_dirigida(client, db):
    oferta = client.post("/t/demo/cotizar", data={
        "origen": "Calle de Alcalá 100", "destino": "Plaza Mayor 1",
        "fecha_hora": fecha_recogida()})
    assert "Solicitar reserva" in oferta.text
    from app.models import Cotizacion

    cot = db.execute(select(Cotizacion)).scalars().first()
    r = client.post("/t/demo/reservar", data={
        "cotizacion_id": str(cot.id), "nombre": "Luis", "telefono": "600333444",
    }, follow_redirects=False)
    assert r.status_code == 303 and "/s/" in r.headers["location"]

    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    tenant = db.execute(select(Tenant).where(Tenant.slug == "demo")).scalar_one()
    assert solicitud.estado == "abierta"
    assert solicitud.tenant_destino_id == tenant.id
    assert float(solicitud.precio_estimado) == float(cot.precio)
    # Sin aceptación no hay reserva
    assert db.execute(select(Reserva)).first() is None


def test_panel_muestra_pendientes_y_acepta(client, db, espia):  # noqa: F811
    cuerpo = solicitar(client, email="ana@example.com")
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()

    login_panel(client)
    bolsa = client.get("/panel/bolsa")
    assert "pendientes de tu confirmación" in bolsa.text
    assert "Precio máximo" in bolsa.text

    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                    data={"descuento_pct": "0", "recogida_eur": "5"},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.estado == "asignada"
    reserva = db.get(Reserva, solicitud.reserva_id)
    assert reserva is not None and reserva.justificante is not None

    # El pasajero llega a la reserva desde su página de espera
    seguimiento = client.get(f"/s/{cuerpo['solicitud_token']}", follow_redirects=False)
    assert seguimiento.status_code == 303
    assert f"/r/{reserva.token_publico}" in seguimiento.headers["location"]
    # Y recibe la confirmación por email
    assert any(e.para == "ana@example.com" for e in espia.enviados)


def test_panel_rechaza_y_avisa_al_pasajero(client, db, espia):  # noqa: F811
    cuerpo = solicitar(client, email="ana@example.com")
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()

    login_panel(client)
    r = client.post(f"/panel/solicitudes/{solicitud.id}/rechazar",
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.execute(select(SolicitudViaje)).scalar_one().estado == "rechazada"
    assert any("no ha podido ser atendida" in e.asunto for e in espia.enviados)

    pagina = client.get(f"/s/{cuerpo['solicitud_token']}")
    assert "no puede atender" in pagina.text
    assert "/viaje" in pagina.text  # se le ofrece la bolsa


def test_solo_el_destinatario_puede_aceptar_o_rechazar(client, db, espia):  # noqa: F811
    from app.security import hash_password

    solicitar(client)
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()

    rival = Tenant(
        slug="rival", nombre="Taxi Rival", nif="11111111H", num_licencia="2222",
        matricula="", email="rival@example.com",
        password_hash=hash_password("clave-rival-1"),
    )
    db.add(rival)
    db.commit()

    login_panel(client, email="rival@example.com", password="clave-rival-1")
    r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                    data={"descuento_pct": "0", "recogida_eur": "5"})
    assert r.status_code == 422 and "otro taxista" in r.json()["detail"]
    r = client.post(f"/panel/solicitudes/{solicitud.id}/rechazar")
    assert r.status_code == 422

    # Y no aparece en la bolsa general del rival
    bolsa = client.get("/panel/bolsa")
    assert "Ana" not in bolsa.text


def test_telegram_acepta_con_boton(client, db, espia):  # noqa: F811
    original = con_telegram_espia()
    try:
        _vincular(client, db)
        client.post("/panel/logout")
        solicitar(client, email="ana@example.com")
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()

        # El aviso al taxista lleva la hoja de ruta y los botones
        avisos = [t for _, t in app.state.telegram_sender.enviados
                  if "pendiente" in t]
        assert avisos and "🟢 Recogida" in avisos[0] and "💶 Precio máximo" in avisos[0]
        datos_botones = [b.get("datos") for fila in app.state.telegram_sender.botones
                         for b in fila]
        assert f"sol:{solicitud.id}:a:0" in datos_botones
        assert f"sol:{solicitud.id}:r" in datos_botones

        r = callback(client, f"sol:{solicitud.id}:a:0")
        assert r.status_code == 200
        db.expire_all()
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()
        assert solicitud.estado == "asignada"
        reserva = db.get(Reserva, solicitud.reserva_id)
        assert reserva is not None

        # Respuesta del botón + mensaje con justificante, y email al pasajero
        assert any("aceptado" in t for _, t in app.state.telegram_sender.callbacks)
        assert any(f"/r/{reserva.token_publico}" in t
                   for _, t in app.state.telegram_sender.enviados)
        assert any(e.para == "ana@example.com" for e in espia.enviados)
    finally:
        app.state.telegram_sender = original


def test_telegram_acepta_con_descuento(client, db, espia):  # noqa: F811
    from decimal import Decimal

    original = con_telegram_espia()
    try:
        _vincular(client, db)
        client.post("/panel/logout")
        solicitar(client)
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()
        estimado = Decimal(str(solicitud.precio_estimado))

        callback(client, f"sol:{solicitud.id}:a:10")
        db.expire_all()
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()
        reserva = db.get(Reserva, solicitud.reserva_id)
        assert Decimal(str(reserva.precio_cerrado)) < estimado
        # La confirmación lleva el precio cerrado definitivo y el PDF
        assert any(f"Precio cerrado: {reserva.precio_cerrado}" in t
                   for _, t in app.state.telegram_sender.enviados)
        assert any(n == "hoja-de-ruta.pdf" and c.startswith(b"%PDF")
                   for _, n, c in app.state.telegram_sender.documentos)
    finally:
        app.state.telegram_sender = original


def test_telegram_rechaza_con_boton(client, db, espia):  # noqa: F811
    original = con_telegram_espia()
    try:
        _vincular(client, db)
        client.post("/panel/logout")
        solicitar(client, email="ana@example.com")
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()

        callback(client, f"sol:{solicitud.id}:r")
        db.expire_all()
        assert db.execute(select(SolicitudViaje)).scalar_one().estado == "rechazada"
        assert any("rechazado" in t for _, t in app.state.telegram_sender.callbacks)
        assert any("no ha podido ser atendida" in e.asunto for e in espia.enviados)

        # Pulsar de nuevo cualquier botón: ya no está pendiente
        callback(client, f"sol:{solicitud.id}:a:0", callback_id="cb-2")
        assert "Ya lo aceptó" in app.state.telegram_sender.callbacks[-1][1] or \
            "no está pendiente" in app.state.telegram_sender.callbacks[-1][1]
    finally:
        app.state.telegram_sender = original


def test_hoja_de_ruta_en_pdf_por_email_y_telegram(client, db, espia):  # noqa: F811
    original = con_telegram_espia()
    try:
        _vincular(client, db)
        solicitar(client, email="ana@example.com")
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()

        # El aviso de solicitud pendiente lleva el PDF en ambos canales
        tg = app.state.telegram_sender
        assert any(n == "hoja-de-ruta.pdf" and c.startswith(b"%PDF")
                   for _, n, c in tg.documentos)
        aviso = next(e for e in espia.enviados if "pendiente" in e.asunto)
        assert any(a.nombre == "hoja-de-ruta.pdf" and a.contenido.startswith(b"%PDF")
                   for a in aviso.adjuntos)

        # Al aceptar desde el panel, llega la hoja definitiva con justificante
        r = client.post(f"/panel/solicitudes/{solicitud.id}/aceptar",
                        data={"descuento_pct": "0", "recogida_eur": "5"},
                        follow_redirects=False)
        assert r.status_code == 303
        confirmada = next(e for e in espia.enviados if "Hoja de ruta" in e.asunto)
        assert "justificante" in confirmada.asunto
        assert any(a.nombre == "hoja-de-ruta.pdf" for a in confirmada.adjuntos)
        assert len(tg.documentos) >= 2  # pendiente + confirmada
        assert any("Reserva confirmada" in t for _, t in tg.enviados)
    finally:
        app.state.telegram_sender = original


def test_pdf_hoja_de_ruta_contenido(client, db):
    from app.services.hoja_ruta_pdf import pdf_hoja_de_ruta

    solicitar(client)
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    pdf = pdf_hoja_de_ruta(solicitud)
    assert pdf.startswith(b"%PDF") and len(pdf) > 500


def test_callback_de_chat_sin_vincular(client, db, espia):  # noqa: F811
    original = con_telegram_espia()
    try:
        solicitar(client)
        solicitud = db.execute(select(SolicitudViaje)).scalar_one()
        callback(client, f"sol:{solicitud.id}:a:0", chat_id=424242)
        assert "no está vinculado" in app.state.telegram_sender.callbacks[-1][1]
        db.expire_all()
        assert db.execute(select(SolicitudViaje)).scalar_one().estado == "abierta"

        # Datos corruptos: no rompe el webhook
        assert callback(client, "sol:no-es-uuid:a:0", chat_id=424242).status_code == 200
        assert callback(client, "otra-cosa", chat_id=424242).status_code == 200
    finally:
        app.state.telegram_sender = original
