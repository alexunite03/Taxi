"""Tests de los proveedores OSM (Photon/Nominatim/OSRM) con respuestas simuladas."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.routing.base import Lugar
from app.routing.osm import NominatimGeocoder, OSRMRouteProvider, PhotonGeocoder

NOMINATIM_RESPUESTA = [
    {"display_name": "Calle de Alcalá 200, Madrid", "lat": "40.4300", "lon": "-3.6500"},
    {"display_name": "Calle de Alcalá 200, Alcalá de Henares", "lat": "40.4800", "lon": "-3.3600"},
]

OSRM_RESPUESTA = {
    "code": "Ok",
    "routes": [
        {
            "distance": 5200.0,
            "duration": 780.0,
            "geometry": {"coordinates": [[-3.65, 40.43], [-3.66, 40.42], [-3.70, 40.41]]},
            "legs": [
                {
                    "steps": [
                        {"distance": 2600.0, "duration": 390.0},
                        {"distance": 2600.0, "duration": 390.0},
                    ]
                }
            ],
        }
    ],
}


def _respuesta(json_data):
    return httpx.Response(200, json=json_data, request=httpx.Request("GET", "https://x"))


def test_nominatim_devuelve_opciones_y_cachea(monkeypatch):
    llamadas = []

    def falso_get(url, **kwargs):
        llamadas.append(url)
        return _respuesta(NOMINATIM_RESPUESTA)

    monkeypatch.setattr(httpx, "get", falso_get)
    geo = NominatimGeocoder("https://nominatim.example", "test@example.com")

    lugares = geo.geocodificar("Calle de Alcalá 200")
    assert [l.texto for l in lugares] == [
        "Calle de Alcalá 200, Madrid",
        "Calle de Alcalá 200, Alcalá de Henares",
    ]
    assert lugares[0].lat == 40.43 and lugares[0].lng == -3.65

    # Segunda llamada igual: sale de caché, sin petición HTTP
    geo.geocodificar("calle de alcalá 200")
    assert len(llamadas) == 1


PHOTON_RESPUESTA = {
    "features": [
        {
            "geometry": {"coordinates": [-3.7031, 40.4200]},
            "properties": {"name": "Gran Vía", "city": "Madrid",
                           "state": "Comunidad de Madrid"},
        },
        {
            "geometry": {"coordinates": [-3.7050, 40.4210]},
            "properties": {"name": "Gran Vía 32", "street": "Gran Vía",
                           "housenumber": "32", "district": "Centro",
                           "city": "Madrid"},
        },
        {"geometry": {}, "properties": {"name": "Sin coordenadas"}},
    ]
}


def test_photon_autocompleta_con_sesgo_a_madrid(monkeypatch):
    llamadas = []

    def falso_get(url, **kwargs):
        llamadas.append((url, kwargs.get("params", {})))
        return _respuesta(PHOTON_RESPUESTA)

    monkeypatch.setattr(httpx, "get", falso_get)
    geo = PhotonGeocoder("https://photon.example")

    lugares = geo.geocodificar("gran vi")  # texto a medias, estilo autocompletar
    assert [l.texto for l in lugares] == [
        "Gran Vía, Madrid, Comunidad de Madrid",
        "Gran Vía 32, Centro, Madrid",
    ]
    assert lugares[0].lat == 40.42 and lugares[0].lng == -3.7031
    # Sesgo a Madrid en la petición
    _, params = llamadas[0]
    assert params["lat"] == 40.4168 and params["lon"] == -3.7038

    # Caché: repetir no vuelve a llamar
    geo.geocodificar("Gran Vi")
    assert len(llamadas) == 1


def test_photon_reverse(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _respuesta(PHOTON_RESPUESTA))
    geo = PhotonGeocoder("https://photon.example")
    lugar = geo.invertir(40.42, -3.7031)
    assert lugar.texto.startswith("Gran Vía")
    assert lugar.lat == 40.42


def test_osrm_convierte_pasos_en_tramos_y_da_geometria(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _respuesta(OSRM_RESPUESTA))
    rutas = OSRMRouteProvider("https://osrm.example")

    ruta = rutas.calcular(
        Lugar("A", 40.43, -3.65),
        Lugar("B", 40.41, -3.70),
        datetime.now(timezone.utc) + timedelta(hours=2),
        con_peaje=False,
    )
    assert len(ruta.tramos) == 2
    assert ruta.tramos[0].dist_km == Decimal("2.6")
    assert ruta.dist_km_total == Decimal("5.20")
    # Geometría en orden Leaflet [lat, lng]
    assert ruta.geometria[0] == [40.43, -3.65]
    assert ruta.peaje_estimado is None


def test_osrm_sin_ruta_lanza_error(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: _respuesta({"code": "NoRoute", "routes": []})
    )
    rutas = OSRMRouteProvider("https://osrm.example")
    try:
        rutas.calcular(
            Lugar("A", 40.43, -3.65), Lugar("B", 40.41, -3.70),
            datetime.now(timezone.utc), con_peaje=False,
        )
        assert False, "debería lanzar RuntimeError"
    except RuntimeError as e:
        assert "NoRoute" in str(e)
