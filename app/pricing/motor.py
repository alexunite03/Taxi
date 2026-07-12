"""Motor de precio cerrado del taxi de Madrid.

Implementa las Instrucciones municipales (Resolución 22-dic-2022) tal como
las recoge el plan (§9.2):

- Entradas cerradas: origen, destino, fechas y horas, datos de tráfico, red
  viaria y tarifas vigentes. Nada más (sin demanda dinámica).
- Velocidad de arrastre = €/h ÷ €/km. Por tramo: si la velocidad estimada
  supera la de arrastre se cobra por distancia; si no, por tiempo.
- Cambio T1/T2 en ruta: cada tramo con la tarifa de su hora estimada.
- Fórmula: inicio (con recogida) + estimación por tramos + suplementos
  − descuentos, redondeado al múltiplo de 5 céntimos más próximo.
- Descuento obligatorio del 10 % con escenario NO₂ activo (L–V 07:00–21:00).

Todo en Decimal. Cada cálculo devuelve, además del precio, el desglose
completo (payload JSON-serializable) que se persiste en `calculos_precio`
como respuesta a una eventual auditoría del algoritmo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from .festivos import es_festivo
from .tarifas import (
    FACTOR_DESCUENTO_NO2,
    PASO,
    RECOGIDA,
    SUPL_NAVIDAD,
    TARIFAS,
    VERSION_TARIFAS,
)

TZ_MADRID = ZoneInfo("Europe/Madrid")


@dataclass(frozen=True)
class Tramo:
    """Tramo de ruta devuelto por el proveedor de rutas (Routes)."""

    dist_km: Decimal
    tiempo_h: Decimal

    @property
    def vel_kmh(self) -> Decimal:
        if self.tiempo_h == 0:
            return Decimal("0")
        return self.dist_km / self.tiempo_h


@dataclass(frozen=True)
class ResultadoCalculo:
    precio: Decimal
    version_tarifas: str
    payload: dict = field(hash=False)


def _a_madrid(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("El motor exige datetimes con zona horaria")
    return dt.astimezone(TZ_MADRID)


def tarifa_aplicable(dt: datetime) -> str:
    """T1 en laborables L–V 07:00–21:00; T2 el resto (noches, sábados,
    domingos y festivos de Madrid capital)."""
    local = _a_madrid(dt)
    if local.weekday() < 5 and not es_festivo(local.date()) and 7 <= local.hour < 21:
        return "T1"
    return "T2"


def es_lv_7_21(dt: datetime) -> bool:
    """Ventana de aplicación del descuento NO₂ (L–V 07:00–21:00)."""
    local = _a_madrid(dt)
    return local.weekday() < 5 and 7 <= local.hour < 21


def _ventanas_navidad(anyo: int) -> list[tuple[datetime, datetime]]:
    ventanas = []
    for dia in (24, 31):
        ini = datetime.combine(date(anyo, 12, dia), time(21, 0), tzinfo=TZ_MADRID)
        ventanas.append((ini, ini + timedelta(hours=10)))  # hasta las 07:00
    return ventanas


def suplemento_navidad(dt_inicio: datetime, dt_fin: datetime) -> bool:
    """True si el servicio solapa con las ventanas de los días 24 y 31 de
    diciembre entre 21:00 y 07:00."""
    ini, fin = _a_madrid(dt_inicio), _a_madrid(dt_fin)
    for anyo in {ini.year, fin.year}:
        for v_ini, v_fin in _ventanas_navidad(anyo):
            if ini < v_fin and fin > v_ini:
                return True
    return False


def precio_cerrado(
    tramos: list[Tramo],
    dt_inicio: datetime,
    peaje: Decimal = Decimal("0"),
    escenario_no2: bool = False,
) -> ResultadoCalculo:
    if not tramos:
        raise ValueError("Se necesita al menos un tramo de ruta")

    t0 = tarifa_aplicable(dt_inicio)
    inicio = TARIFAS[t0]["inicio"]
    total = inicio + RECOGIDA

    detalle_tramos = []
    t = _a_madrid(dt_inicio)
    for tr in tramos:
        nombre_tarifa = tarifa_aplicable(t)
        tar = TARIFAS[nombre_tarifa]
        arrastre = tar["hora"] / tar["km"]
        if tr.vel_kmh > arrastre:
            modo, importe = "distancia", tr.dist_km * tar["km"]
        else:
            modo, importe = "tiempo", tr.tiempo_h * tar["hora"]
        total += importe
        detalle_tramos.append(
            {
                "inicio_tramo": t.isoformat(),
                "tarifa": nombre_tarifa,
                "dist_km": str(tr.dist_km),
                "tiempo_h": str(tr.tiempo_h),
                "vel_kmh": str(tr.vel_kmh.quantize(Decimal("0.01"))),
                "vel_arrastre_kmh": str(arrastre.quantize(Decimal("0.01"))),
                "modo": modo,
                "importe": str(importe.quantize(Decimal("0.0001"))),
            }
        )
        t += timedelta(hours=float(tr.tiempo_h))

    navidad = suplemento_navidad(dt_inicio, t)
    if navidad:
        total += SUPL_NAVIDAD

    total += peaje  # solo llega aquí si el pasajero consintió la ruta con peaje

    descuento_aplicado = escenario_no2 and es_lv_7_21(dt_inicio)
    if descuento_aplicado:
        total *= FACTOR_DESCUENTO_NO2

    precio = (total / PASO).quantize(Decimal("1"), ROUND_HALF_UP) * PASO

    payload = {
        "version_tarifas": VERSION_TARIFAS,
        "dt_inicio": _a_madrid(dt_inicio).isoformat(),
        "dt_fin_estimado": t.isoformat(),
        "tarifa_inicio": t0,
        "importe_inicio": str(inicio),
        "importe_recogida": str(RECOGIDA),
        "tramos": detalle_tramos,
        "suplemento_navidad": str(SUPL_NAVIDAD) if navidad else None,
        "peaje": str(peaje) if peaje else None,
        "descuento_no2": str(FACTOR_DESCUENTO_NO2) if descuento_aplicado else None,
        "total_sin_redondear": str(total.quantize(Decimal("0.0001"))),
        "paso_redondeo": str(PASO),
        "precio": str(precio),
    }
    return ResultadoCalculo(precio=precio, version_tarifas=VERSION_TARIFAS, payload=payload)
