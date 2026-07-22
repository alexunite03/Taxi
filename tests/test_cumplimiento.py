"""Tests del bloque de cumplimiento: servicios excluidos (aeropuerto),
verificación DSA, exportación art. 47, quejas y retención RGPD."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.models import ClienteFinal, Queja, SolicitudViaje, Tenant

from .test_api import fecha_recogida, pedir_cotizacion, reservar_directa
from .test_notificaciones import espia  # noqa: F401

# Punto dentro de la T4 (zona excluida) y punto en el centro
AEROPUERTO = {"lat": "40.4839", "lng": "-3.5680"}
CENTRO = {"lat": "40.4200", "lng": "-3.7000"}


# --- Servicios excluidos (sección 5.3) -------------------------------------


def test_zona_excluida_del_fichero_de_tarifas():
    from app.pricing.tarifas import VERSION_TARIFAS, zona_excluida

    assert VERSION_TARIFAS == "BOCM-290-2025"
    assert zona_excluida(40.4839, -3.5680).startswith("Aeropuerto")
    assert zona_excluida(40.4200, -3.7000) is None


def test_fichero_de_tarifas_externalizado(tmp_path, monkeypatch):
    """La tabla se actualiza cada enero cambiando el fichero, sin tocar
    código: otra ruta en TAXI_TARIFAS_FICHERO carga otra versión."""
    datos = {
        "version": "BOCM-TEST-2027",
        "tarifas": {"T1": {"inicio": "9.99", "km": "9.99", "hora": "9.99"},
                    "T2": {"inicio": "9.99", "km": "9.99", "hora": "9.99"}},
        "recogida_max": "5.00", "suplemento_navidad": "7.00",
        "paso_redondeo": "0.05", "factor_descuento_no2": "0.90",
        "zonas_excluidas": [],
    }
    ruta = tmp_path / "tarifas-test.json"
    ruta.write_text(json.dumps(datos))
    monkeypatch.setenv("TAXI_TARIFAS_FICHERO", str(ruta))

    from app.pricing import tarifas

    cargado = tarifas._cargar()
    assert cargado["version"] == "BOCM-TEST-2027"


def test_trayecto_al_aeropuerto_sin_precio_cerrado(client, db):
    r = client.post("/t/demo/cotizar", data={
        "origen": "Calle de Alcalá 100",
        "destino": "Aeropuerto T4",
        "fecha_hora": fecha_recogida(),
        "origen_lat": CENTRO["lat"], "origen_lng": CENTRO["lng"],
        "destino_lat": AEROPUERTO["lat"], "destino_lng": AEROPUERTO["lng"],
    })
    assert r.status_code == 200
    assert "tarifa fija" in r.text and "no aplica" in r.text
    # Tampoco en la bolsa
    from .test_bolsa import publicar_viaje

    r = publicar_viaje(client, origen="Terminal 4 llegadas",
                       origen_lat=AEROPUERTO["lat"], origen_lng=AEROPUERTO["lng"],
                       destino_lat=CENTRO["lat"], destino_lng=CENTRO["lng"])
    assert r.status_code == 200 and "tarifa fija" in r.text


# --- DSA art. 30: verificación y endpoints del titular ----------------------


def _alta_taxista(client):
    from .test_cuentas import TAXISTA

    return client.post("/registro/taxista", data=TAXISTA, follow_redirects=False)


def test_admin_pendientes_y_verificar(client, db):
    anterior = settings.admin_token
    settings.admin_token = "admin-123"
    try:
        assert client.get("/api/admin/pendientes").status_code == 404
        assert client.get("/api/admin/pendientes?token=malo").status_code == 404

        _alta_taxista(client)
        r = client.get("/api/admin/pendientes?token=admin-123")
        pendientes = r.json()["pendientes"]
        assert len(pendientes) == 1
        assert pendientes[0]["num_licencia"] == "7777"
        slug = pendientes[0]["slug"]

        r = client.get(f"/api/admin/verificar?token=admin-123&slug={slug}")
        assert r.json()["verificado"] is True
        assert client.get(f"/t/{slug}").status_code == 200
        assert client.get("/api/admin/pendientes?token=admin-123").json()["pendientes"] == []
    finally:
        settings.admin_token = anterior


def test_listado_ordena_por_antiguedad_no_por_valoracion(client, db):
    """Invariante del modelo neutro (línea roja): el orden del listado no
    depende de valoraciones ni de ningún criterio de la plataforma."""
    from app.security import hash_password

    for i, nombre in enumerate(["Bravo Taxi", "Alfa Taxi"]):
        t = Tenant(slug=f"t{i}", nombre=nombre, nif=f"0000000{i}A",
                   num_licencia=str(1000 + i), matricula="",
                   email=f"t{i}@example.com",
                   password_hash=hash_password("clave-123456"))
        db.add(t)
        db.commit()
    pagina = client.get("/taxistas").text
    # Orden por fecha de alta: demo, Bravo, Alfa (no alfabético ni por nota)
    assert pagina.index("Taxi Demo") < pagina.index("Bravo Taxi") < pagina.index("Alfa Taxi")


# --- Quejas y exportación art. 47 ------------------------------------------


def test_quejas_formulario_y_registro(client, db):
    assert "Quejas" in client.get("/quejas").text
    r = client.post("/quejas", data={
        "nombre": "Ana", "email": "ana@example.com",
        "texto": "El taxista no apareció en la recogida.",
        "referencia": "abc123"})
    assert r.status_code == 200 and "Recibida" in r.text
    queja = db.execute(select(Queja)).scalar_one()
    assert queja.estado == "nueva" and queja.referencia == "abc123"

    # Demasiado corta → 422
    assert client.post("/quejas", data={"nombre": "X", "texto": "mal"}).status_code == 422


def test_export_art47(client, db, espia):  # noqa: F811
    anterior = settings.admin_token
    settings.admin_token = "admin-123"
    try:
        # Un servicio contratado, una demanda no atendida y una queja
        cot = pedir_cotizacion(client).json()
        reservar_directa(client, db, cot["cotizacion_id"], email="ana@example.com")
        from .test_reserva_directa import solicitar

        solicitar(client, telefono="600222333")
        db.execute(select(SolicitudViaje).where(
            SolicitudViaje.estado == "abierta")).scalar_one().estado = "rechazada"
        db.commit()
        client.post("/quejas", data={"nombre": "Ana", "texto": "Queja de prueba art47"})

        base = "/api/admin/export/art47.csv?token=admin-123"
        servicios = client.get(f"{base}&conjunto=servicios")
        assert servicios.status_code == 200
        assert "text/csv" in servicios.headers["content-type"]
        assert "Plaza de Castilla" in servicios.text and "1234" in servicios.text

        demandas = client.get(f"{base}&conjunto=demandas").text
        assert "rechazada" in demandas

        quejas = client.get(f"{base}&conjunto=quejas").text
        assert "Queja de prueba art47" in quejas

        # Fuera de rango → vacío (solo cabecera)
        vacio = client.get(f"{base}&conjunto=quejas&desde=2001-01-01&hasta=2001-12-31")
        assert len(vacio.text.strip().splitlines()) == 1
        assert client.get(f"{base}&conjunto=otro").status_code == 422
        assert client.get("/api/admin/export/art47.csv?conjunto=quejas").status_code == 404
    finally:
        settings.admin_token = anterior


# --- RGPD: retención y anonimizado ------------------------------------------


def test_anonimizado_pasado_el_plazo(client, db, espia):  # noqa: F811
    from app.services.rgpd import ANONIMO, anonimizar_datos_antiguos

    cot = pedir_cotizacion(client).json()
    reservar_directa(client, db, cot["cotizacion_id"], nombre="Ana García",
                     email="ana@example.com")
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()

    # Recientes: no se toca nada
    assert anonimizar_datos_antiguos(db) == {
        "solicitudes_anonimizadas": 0, "clientes_anonimizados": 0}

    # Envejecemos solicitud y reserva más allá de la retención
    viejo = datetime.now(timezone.utc) - timedelta(days=30 * settings.retencion_meses + 40)
    solicitud.creada_en = viejo
    from app.models import Reserva

    db.execute(select(Reserva)).scalar_one().creada_en = viejo
    db.commit()

    resultado = anonimizar_datos_antiguos(db)
    assert resultado == {"solicitudes_anonimizadas": 1, "clientes_anonimizados": 1}
    db.expire_all()
    solicitud = db.execute(select(SolicitudViaje)).scalar_one()
    assert solicitud.nombre == ANONIMO and solicitud.email is None
    cliente = db.execute(select(ClienteFinal)).scalar_one()
    assert cliente.nombre == ANONIMO and cliente.email is None
    assert cliente.telefono.startswith("anon-")

    # Idempotente
    assert anonimizar_datos_antiguos(db) == {
        "solicitudes_anonimizadas": 0, "clientes_anonimizados": 0}
