"""La cotización no molesta con «varias coincidencias» salvo ambigüedad
real (misma calle en otra ciudad). Varias direcciones de la misma zona se
resuelven con la mejor coincidencia."""
import pytest

from app.routing.base import Lugar
from app.services.cotizaciones import (
    DesambiguacionRequerida,
    DireccionNoEncontrada,
    _geocodificar,
)


class GeocoderFalso:
    def __init__(self, lugares):
        self._lugares = lugares

    def geocodificar(self, texto):
        return self._lugares


def test_varias_coincidencias_cercanas_no_desambiguan():
    # Dos «Calle de Alcalá» en Madrid, a unos cientos de metros
    geo = GeocoderFalso([
        Lugar("Calle de Alcalá 100, Madrid", 40.4231, -3.6790),
        Lugar("Calle de Alcalá 200, Madrid", 40.4256, -3.6712),
    ])
    lugar = _geocodificar(geo, "origen", "Calle de Alcalá")
    assert lugar.texto == "Calle de Alcalá 100, Madrid"  # la mejor, sin preguntar


def test_misma_calle_en_otra_ciudad_si_desambigua():
    # «Gran Vía» en Madrid vs en Vigo (>400 km): ahí sí conviene elegir
    geo = GeocoderFalso([
        Lugar("Gran Vía, Madrid", 40.4200, -3.7050),
        Lugar("Gran Vía, Vigo", 42.2350, -8.7130),
    ])
    with pytest.raises(DesambiguacionRequerida):
        _geocodificar(geo, "origen", "Gran Vía")


def test_sin_coincidencias_error():
    with pytest.raises(DireccionNoEncontrada):
        _geocodificar(GeocoderFalso([]), "origen", "asdfghjkl")


def test_una_coincidencia_directa():
    geo = GeocoderFalso([Lugar("Puerta del Sol, Madrid", 40.4169, -3.7033)])
    assert _geocodificar(geo, "destino", "Sol").texto == "Puerta del Sol, Madrid"
