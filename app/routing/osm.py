"""Proveedores gratuitos basados en OpenStreetMap (plan §12, fallback/demo).

- Nominatim para geocodificar con varias coincidencias (autocompletar).
- OSRM para la ruta con su trazado (sin tráfico en tiempo real: para el
  cálculo regulado con tráfico de día equivalente, usar `google`).

Política de uso de Nominatim: peticiones moderadas y User-Agent
identificable. El endpoint de autocompletar aplica debounce en cliente,
mínimo de 3 caracteres y esta caché en memoria.
"""
from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal

import httpx

from app.pricing import Tramo

from .base import Lugar, RutaCalculada

# Caja de la Comunidad de Madrid para sesgar resultados
_VIEWBOX = "-4.6,41.2,-3.0,39.9"  # lon1,lat1,lon2,lat2
_CACHE_TTL_S = 24 * 3600


class NominatimGeocoder:
    def __init__(self, base_url: str, contacto: str):
        self.base_url = base_url.rstrip("/")
        self.user_agent = f"taxi-saas/0.1 ({contacto})"
        self._cache: dict[str, tuple[float, list[Lugar]]] = {}

    def geocodificar(self, texto: str) -> list[Lugar]:
        clave = texto.strip().lower()
        if not clave:
            return []
        en_cache = self._cache.get(clave)
        if en_cache and time.monotonic() - en_cache[0] < _CACHE_TTL_S:
            return en_cache[1]

        resp = httpx.get(
            f"{self.base_url}/search",
            params={
                "q": texto,
                "format": "jsonv2",
                "limit": 5,
                "accept-language": "es",
                "countrycodes": "es",
                "viewbox": _VIEWBOX,
                "bounded": 0,
            },
            headers={"User-Agent": self.user_agent},
            timeout=10,
        )
        resp.raise_for_status()
        lugares = [
            Lugar(
                texto=r.get("display_name", texto),
                lat=float(r["lat"]),
                lng=float(r["lon"]),
            )
            for r in resp.json()
        ]
        self._cache[clave] = (time.monotonic(), lugares)
        return lugares


class OSRMRouteProvider:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def calcular(
        self, origen: Lugar, destino: Lugar, dt_salida: datetime, con_peaje: bool
    ) -> RutaCalculada:
        resp = httpx.get(
            f"{self.base_url}/route/v1/driving/"
            f"{origen.lng},{origen.lat};{destino.lng},{destino.lat}",
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "true",
                "alternatives": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        datos = resp.json()
        if datos.get("code") != "Ok" or not datos.get("routes"):
            raise RuntimeError(f"OSRM: {datos.get('code', 'sin ruta')}")
        ruta = datos["routes"][0]

        tramos: list[Tramo] = []
        for leg in ruta.get("legs", []):
            for paso in leg.get("steps", []):
                metros = Decimal(str(paso.get("distance", 0)))
                segundos = Decimal(str(paso.get("duration", 0)))
                if metros == 0 and segundos == 0:
                    continue
                tramos.append(
                    Tramo(dist_km=metros / 1000, tiempo_h=segundos / 3600)
                )

        dist_total = Decimal(str(ruta.get("distance", 0))) / 1000
        tiempo_total = Decimal(str(ruta.get("duration", 0))) / 3600
        if not tramos:
            tramos = [Tramo(dist_km=dist_total, tiempo_h=tiempo_total)]

        geometria = [
            [lat, lon]
            for lon, lat in ruta.get("geometry", {}).get("coordinates", [])
        ] or None

        return RutaCalculada(
            tramos=tramos,
            dist_km_total=dist_total.quantize(Decimal("0.01")),
            tiempo_h_total=tiempo_total.quantize(Decimal("0.0001")),
            peaje_estimado=None,  # OSRM no informa de peajes
            geometria=geometria,
        )
