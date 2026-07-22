"""Tabla oficial de tarifas del taxi de Madrid, cargada de un fichero de
datos versionado (actualizable cada enero SIN tocar código).

Fichero por defecto: `app/pricing/datos/tarifas-2026.json`; se puede apuntar
a otro con la variable de entorno `TAXI_TARIFAS_FICHERO` (ruta absoluta o
relativa al proyecto). El precio cerrado solo admite T1/T2 — nunca T3, T4 ni
T7: los trayectos con origen o destino en las `zonas_excluidas` del fichero
(aeropuerto) quedan fuera y el motor debe rechazarlos.

Regla del proyecto: nunca valores incrustados en el motor; siempre esta
tabla versionada, y cada cálculo persiste `version_tarifas`.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

_POR_DEFECTO = Path(__file__).resolve().parent / "datos" / "tarifas-2026.json"


def _cargar() -> dict:
    ruta = Path(os.environ.get("TAXI_TARIFAS_FICHERO") or _POR_DEFECTO)
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


_DATOS = _cargar()

VERSION_TARIFAS: str = _DATOS["version"]

TARIFAS: dict[str, dict[str, Decimal]] = {
    nombre: {clave: Decimal(valor) for clave, valor in valores.items()}
    for nombre, valores in _DATOS["tarifas"].items()
}

# Recogida en servicios concertados: entre el importe de inicio y este máximo.
# El algoritmo usa el máximo (instrucciones del plan, §9.1).
RECOGIDA = Decimal(_DATOS["recogida_max"])

# Suplemento días 24 y 31 de diciembre entre 21:00 y 07:00.
SUPL_NAVIDAD = Decimal(_DATOS["suplemento_navidad"])

# Salto / redondeo al múltiplo más próximo.
PASO = Decimal(_DATOS["paso_redondeo"])

# Descuento obligatorio del 10 % con escenarios 3, 4 o alerta del protocolo
# NO₂ activados (L–V 07:00–21:00).
FACTOR_DESCUENTO_NO2 = Decimal(_DATOS["factor_descuento_no2"])

# Zonas donde el precio cerrado NO aplica (tarifa fija de aeropuerto, etc.)
ZONAS_EXCLUIDAS: list[dict] = _DATOS.get("zonas_excluidas", [])


def zona_excluida(lat: float, lng: float) -> str | None:
    """Nombre de la zona excluida que contiene el punto, o None."""
    for zona in ZONAS_EXCLUIDAS:
        caja = zona["bbox"]
        if (caja["lat_min"] <= lat <= caja["lat_max"]
                and caja["lng_min"] <= lng <= caja["lng_max"]):
            return zona["nombre"]
    return None
