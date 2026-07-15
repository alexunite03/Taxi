"""Interfaces de geocodificación y cálculo de rutas.

El motor de precio solo entiende de tramos (`app.pricing.Tramo`); estos
proveedores traducen el mundo exterior (Google, OSRM…) a esa entrada cerrada.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.pricing import Tramo


@dataclass(frozen=True)
class Lugar:
    texto: str  # dirección formateada que verá el pasajero y el justificante
    lat: float
    lng: float


@dataclass(frozen=True)
class RutaCalculada:
    tramos: list[Tramo]
    dist_km_total: Decimal
    tiempo_h_total: Decimal
    # Importe estimado del peaje de la ruta más rápida, si existe alternativa
    # de peaje. None → la ruta no admite peaje y no hay que preguntar.
    peaje_estimado: Decimal | None = None
    # Trazado de la ruta como lista de puntos [lat, lng] (orden Leaflet),
    # para pintarla en el mapa. None si el proveedor no la da.
    geometria: list | None = None


class Geocoder(Protocol):
    def geocodificar(self, texto: str) -> list[Lugar]:
        """Devuelve las coincidencias (varias → desambiguación en el paso 1)."""
        ...

    def invertir(self, lat: float, lng: float) -> Lugar | None:
        """Dirección aproximada de unas coordenadas ("usar mi ubicación")."""
        ...


class RouteProvider(Protocol):
    def calcular(
        self, origen: Lugar, destino: Lugar, dt_salida: datetime, con_peaje: bool
    ) -> RutaCalculada:
        """Ruta con `departureTime` futuro y tráfico de día equivalente."""
        ...
