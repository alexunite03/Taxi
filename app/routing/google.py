"""Clientes de Google Geocoding y Google Routes (Compute Routes).

Routes se llama con `departureTime` futuro y `TRAFFIC_AWARE` (SKU Pro),
como exigen las instrucciones municipales para reservas con más de una hora
de antelación. Los `steps` de la ruta se convierten en tramos del motor,
lo que permite aplicar el cambio T1/T2 dentro del trayecto.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.pricing import Tramo

from .base import Lugar, RutaCalculada

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


class GoogleGeocoder:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocodificar(self, texto: str) -> list[Lugar]:
        resp = httpx.get(
            _GEOCODE_URL,
            params={
                "address": texto,
                "key": self.api_key,
                "language": "es",
                "region": "es",
                # Sesgo a la Comunidad de Madrid
                "bounds": "40.20,-4.00|40.70,-3.30",
            },
            timeout=10,
        )
        resp.raise_for_status()
        datos = resp.json()
        if datos.get("status") not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(f"Geocoding: {datos.get('status')}")
        return [
            Lugar(
                texto=r["formatted_address"],
                lat=r["geometry"]["location"]["lat"],
                lng=r["geometry"]["location"]["lng"],
            )
            for r in datos.get("results", [])[:5]
        ]

    def invertir(self, lat: float, lng: float) -> Lugar | None:
        resp = httpx.get(
            _GEOCODE_URL,
            params={"latlng": f"{lat},{lng}", "key": self.api_key, "language": "es"},
            timeout=10,
        )
        resp.raise_for_status()
        resultados = resp.json().get("results", [])
        if not resultados:
            return None
        return Lugar(texto=resultados[0]["formatted_address"], lat=lat, lng=lng)


class GoogleRouteProvider:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def calcular(
        self, origen: Lugar, destino: Lugar, dt_salida: datetime, con_peaje: bool
    ) -> RutaCalculada:
        cuerpo = {
            "origin": {"location": {"latLng": {"latitude": origen.lat, "longitude": origen.lng}}},
            "destination": {"location": {"latLng": {"latitude": destino.lat, "longitude": destino.lng}}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "departureTime": dt_salida.astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "computeAlternativeRoutes": False,
            "routeModifiers": {"avoidTolls": not con_peaje},
            "extraComputations": ["TOLLS"] if con_peaje else [],
            "languageCode": "es-ES",
            "units": "METRIC",
        }
        campos = (
            "routes.distanceMeters,routes.duration,"
            "routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,"
            "routes.travelAdvisory.tollInfo"
        )
        resp = httpx.post(
            _ROUTES_URL,
            json=cuerpo,
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": campos,
            },
            timeout=15,
        )
        resp.raise_for_status()
        rutas = resp.json().get("routes", [])
        if not rutas:
            raise RuntimeError("Routes no devolvió ninguna ruta")
        ruta = rutas[0]

        tramos: list[Tramo] = []
        for leg in ruta.get("legs", []):
            for paso in leg.get("steps", []):
                metros = paso.get("distanceMeters", 0)
                segundos = int(paso.get("staticDuration", "0s").rstrip("s") or 0)
                if metros == 0 and segundos == 0:
                    continue
                tramos.append(
                    Tramo(
                        dist_km=Decimal(metros) / 1000,
                        tiempo_h=Decimal(segundos) / 3600,
                    )
                )

        peaje = None
        toll_info = ruta.get("travelAdvisory", {}).get("tollInfo", {})
        for precio in toll_info.get("estimatedPrice", []):
            if precio.get("currencyCode") == "EUR":
                unidades = Decimal(precio.get("units", "0"))
                nanos = Decimal(precio.get("nanos", 0)) / Decimal("1e9")
                peaje = (unidades + nanos).quantize(Decimal("0.01"))

        dist_total = Decimal(ruta.get("distanceMeters", 0)) / 1000
        seg_total = int(ruta.get("duration", "0s").rstrip("s") or 0)
        return RutaCalculada(
            tramos=tramos or [Tramo(dist_km=dist_total, tiempo_h=Decimal(seg_total) / 3600)],
            dist_km_total=dist_total.quantize(Decimal("0.01")),
            tiempo_h_total=(Decimal(seg_total) / 3600).quantize(Decimal("0.0001")),
            peaje_estimado=peaje,
        )
