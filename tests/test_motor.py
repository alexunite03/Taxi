"""Tests del motor de precio cerrado con casos calculados a mano."""
from datetime import datetime
from decimal import Decimal

import pytest

from app.pricing import Tramo, precio_cerrado, suplemento_navidad, tarifa_aplicable
from app.pricing.festivos import CalendarioNoDisponible
from app.pricing.motor import TZ_MADRID


def dt(*args) -> datetime:
    return datetime(*args, tzinfo=TZ_MADRID)


# --- tarifa_aplicable -------------------------------------------------------

def test_laborable_diurno_es_t1():
    assert tarifa_aplicable(dt(2026, 7, 14, 10, 0)) == "T1"  # martes


def test_laborable_nocturno_es_t2():
    assert tarifa_aplicable(dt(2026, 7, 14, 22, 0)) == "T2"


def test_limite_2100_es_t2():
    assert tarifa_aplicable(dt(2026, 7, 14, 21, 0)) == "T2"


def test_limite_0700_es_t1():
    assert tarifa_aplicable(dt(2026, 7, 14, 7, 0)) == "T1"


def test_sabado_es_t2():
    assert tarifa_aplicable(dt(2026, 7, 18, 12, 0)) == "T2"


def test_festivo_entre_semana_es_t2():
    # 1 de mayo de 2026 cae en viernes
    assert tarifa_aplicable(dt(2026, 5, 1, 12, 0)) == "T2"


def test_anyo_sin_calendario_falla_explicitamente():
    with pytest.raises(CalendarioNoDisponible):
        tarifa_aplicable(dt(2030, 3, 4, 12, 0))  # lunes


def test_datetime_naive_rechazado():
    with pytest.raises(ValueError):
        tarifa_aplicable(datetime(2026, 7, 14, 10, 0))


# --- precio_cerrado ---------------------------------------------------------

def test_t1_tramo_rapido_cobra_por_distancia():
    # 10 km a 30 km/h > arrastre T1 (19,29): 2,55 + 5,00 + 10·1,40 = 21,55
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0))
    assert r.precio == Decimal("21.55")
    assert r.payload["tramos"][0]["modo"] == "distancia"


def test_t1_tramo_lento_cobra_por_tiempo():
    # 2 km en 0,4 h (5 km/h) < arrastre: 2,55 + 5,00 + 0,4·27 = 18,35
    tramos = [Tramo(dist_km=Decimal("2"), tiempo_h=Decimal("0.4"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0))
    assert r.precio == Decimal("18.35")
    assert r.payload["tramos"][0]["modo"] == "tiempo"


def test_t2_sabado():
    # 3,20 + 5,00 + 10·1,60 = 24,20
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 18, 12, 0))
    assert r.precio == Decimal("24.20")


def test_redondeo_a_multiplo_de_5_centimos():
    # 2,55 + 5,00 + 7,13·1,40 = 17,532 → 17,55
    tramos = [Tramo(dist_km=Decimal("7.13"), tiempo_h=Decimal("7.13") / Decimal("40"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0))
    assert r.precio == Decimal("17.55")


def test_descuento_no2_en_ventana():
    # 21,55 · 0,90 = 19,395 → 19,40
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0), escenario_no2=True)
    assert r.precio == Decimal("19.40")
    assert r.payload["descuento_no2"] == "0.90"


def test_descuento_no2_fuera_de_ventana_no_aplica():
    # Sábado: el flag está activo pero la ventana es L–V 07:00–21:00
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 18, 12, 0), escenario_no2=True)
    assert r.precio == Decimal("24.20")
    assert r.payload["descuento_no2"] is None


def test_cambio_de_tarifa_en_ruta():
    # Sale 20:45 (T1) y el segundo tramo empieza 21:15 (T2).
    # 2,55 + 5,00 + 15·1,40 + 15·1,60 = 52,55
    tramos = [
        Tramo(dist_km=Decimal("15"), tiempo_h=Decimal("0.5")),
        Tramo(dist_km=Decimal("15"), tiempo_h=Decimal("0.5")),
    ]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 20, 45))
    assert r.precio == Decimal("52.55")
    assert [t["tarifa"] for t in r.payload["tramos"]] == ["T1", "T2"]


def test_suplemento_navidad():
    # 24-dic 21:30 (T2): 3,20 + 5,00 + 5·1,60 + 7,00 = 23,20
    tramos = [Tramo(dist_km=Decimal("5"), tiempo_h=Decimal("0.25"))]
    r = precio_cerrado(tramos, dt(2026, 12, 24, 21, 30))
    assert r.precio == Decimal("23.20")
    assert r.payload["suplemento_navidad"] == "7.00"


def test_suplemento_navidad_por_solape():
    # Sale 20:30 del 31-dic y termina 21:10: solapa con la ventana
    assert suplemento_navidad(dt(2026, 12, 31, 20, 30), dt(2026, 12, 31, 21, 10))
    assert not suplemento_navidad(dt(2026, 12, 31, 12, 0), dt(2026, 12, 31, 12, 30))


def test_peaje_consentido_se_suma():
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0), peaje=Decimal("3.10"))
    assert r.precio == Decimal("24.65")
    assert r.payload["peaje"] == "3.10"


def test_payload_auditable_completo():
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0))
    assert r.version_tarifas == "BOCM-290-2025"
    p = r.payload
    for clave in ("version_tarifas", "dt_inicio", "dt_fin_estimado", "tarifa_inicio",
                  "tramos", "total_sin_redondear", "precio"):
        assert p[clave] is not None
    assert p["tramos"][0]["vel_arrastre_kmh"] == "19.29"


def test_sin_tramos_rechazado():
    with pytest.raises(ValueError):
        precio_cerrado([], dt(2026, 7, 14, 10, 0))


def test_recogida_reducida():
    # Recogida de 2,50 en vez del máximo: 2,55 + 2,50 + 14,00 = 19,05
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0), recogida=Decimal("2.50"))
    assert r.precio == Decimal("19.05")
    assert r.payload["importe_recogida"] == "2.50"


def test_descuento_del_taxista():
    # 21,55 con un 10 % de descuento comercial → 19,395 → 19,40
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    r = precio_cerrado(tramos, dt(2026, 7, 14, 10, 0), descuento_pct=Decimal("10"))
    assert r.precio == Decimal("19.40")
    assert r.payload["descuento_taxista_pct"] == "10"


def test_limites_de_recogida_y_descuento():
    tramos = [Tramo(dist_km=Decimal("10"), tiempo_h=Decimal("10") / Decimal("30"))]
    with pytest.raises(ValueError):
        precio_cerrado(tramos, dt(2026, 7, 14, 10, 0), recogida=Decimal("5.50"))
    with pytest.raises(ValueError):
        precio_cerrado(tramos, dt(2026, 7, 14, 10, 0), descuento_pct=Decimal("40"))
