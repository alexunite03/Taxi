"""Tabla oficial de tarifas del taxi de Madrid, versionada.

Fuente: BOCM núm. 290, 5-dic-2025 (tarifas 2026). El precio cerrado solo
admite T1/T2 — nunca T3, T4 ni T7, tampoco en trayectos al aeropuerto.

Regla del proyecto: nunca valores incrustados en el motor; siempre esta
tabla versionada, y cada cálculo persiste `version_tarifas`.
"""
from decimal import Decimal

VERSION_TARIFAS = "BOCM-290-2025"

TARIFAS: dict[str, dict[str, Decimal]] = {
    "T1": {"inicio": Decimal("2.55"), "km": Decimal("1.40"), "hora": Decimal("27.00")},
    "T2": {"inicio": Decimal("3.20"), "km": Decimal("1.60"), "hora": Decimal("29.00")},
}

# Recogida en servicios concertados: entre el importe de inicio y 5,00 € como
# máximo. El algoritmo usa el máximo (instrucciones del plan, §9.1).
RECOGIDA = Decimal("5.00")

# Suplemento días 24 y 31 de diciembre entre 21:00 y 07:00.
SUPL_NAVIDAD = Decimal("7.00")

# Salto / redondeo al múltiplo más próximo.
PASO = Decimal("0.05")

# Descuento obligatorio del 10 % con escenarios 3, 4 o alerta del protocolo
# NO₂ activados (L–V 07:00–21:00).
FACTOR_DESCUENTO_NO2 = Decimal("0.90")
