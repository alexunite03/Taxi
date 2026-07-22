"""Proveedores deterministas para desarrollo y tests (sin red ni API key).

Geocodifica dentro del término municipal de Madrid con coordenadas derivadas
del hash del texto, y calcula rutas por distancia haversine con un factor de
red viaria. Suficiente para ejercitar todo el flujo de reserva.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from decimal import Decimal

from app.pricing import Tramo

from .base import Lugar, RutaCalculada

# Caja aproximada de Madrid capital, recortada para no pisar la zona
# excluida del aeropuerto (los tests de exclusión usan coordenadas fijas)
_LAT = (40.35, 40.52)
_LNG = (-3.80, -3.64)

FACTOR_RED_VIARIA = Decimal("1.35")
VELOCIDAD_MEDIA_KMH = Decimal("22")


class FakeGeocoder:
    def geocodificar(self, texto: str) -> list[Lugar]:
        texto = texto.strip()
        if not texto:
            return []
        h = hashlib.sha256(texto.lower().encode()).digest()
        lat = _LAT[0] + (h[0] * 256 + h[1]) / 65535 * (_LAT[1] - _LAT[0])
        lng = _LNG[0] + (h[2] * 256 + h[3]) / 65535 * (_LNG[1] - _LNG[0])
        return [Lugar(texto=f"{texto}, Madrid", lat=round(lat, 6), lng=round(lng, 6))]

    def invertir(self, lat: float, lng: float) -> Lugar | None:
        return Lugar(
            texto=f"Mi ubicación ({round(lat, 5)}, {round(lng, 5)})",
            lat=lat,
            lng=lng,
        )


def _haversine_km(a: Lugar, b: Lugar) -> Decimal:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians(b.lng - a.lng)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return Decimal(str(round(r * 2 * math.asin(math.sqrt(x)), 3)))


class FakeRouteProvider:
    def calcular(
        self, origen: Lugar, destino: Lugar, dt_salida: datetime, con_peaje: bool
    ) -> RutaCalculada:
        dist = (_haversine_km(origen, destino) * FACTOR_RED_VIARIA).quantize(
            Decimal("0.01")
        )
        dist = max(dist, Decimal("0.50"))
        tiempo = (dist / VELOCIDAD_MEDIA_KMH).quantize(Decimal("0.0001"))
        # Dos tramos iguales: ejercita el cambio de tarifa en ruta
        mitad_d = (dist / 2).quantize(Decimal("0.01"))
        mitad_t = (tiempo / 2).quantize(Decimal("0.0001"))
        tramos = [
            Tramo(dist_km=mitad_d, tiempo_h=mitad_t),
            Tramo(dist_km=dist - mitad_d, tiempo_h=tiempo - mitad_t),
        ]
        return RutaCalculada(
            tramos=tramos,
            dist_km_total=dist,
            tiempo_h_total=tiempo,
            peaje_estimado=None,
            geometria=[[origen.lat, origen.lng], [destino.lat, destino.lng]],
        )
