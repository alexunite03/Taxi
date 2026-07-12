"""Calendario de festivos de Madrid capital, mantenido en tabla.

A efectos tarifarios (T1/T2) los festivos cuentan como T2 todo el día.
Incluye festivos nacionales, de la Comunidad de Madrid y locales de Madrid
capital.

PENDIENTE DE VERIFICACIÓN: contrastar cada año contra el calendario laboral
publicado en el BOCM (diciembre del año anterior) y actualizar esta tabla.
El recordatorio anual está en el checklist legal del plan (§17).
"""
from datetime import date

FESTIVOS_MADRID: dict[int, frozenset[date]] = {
    2026: frozenset(
        {
            date(2026, 1, 1),    # Año Nuevo
            date(2026, 1, 6),    # Epifanía del Señor
            date(2026, 4, 2),    # Jueves Santo
            date(2026, 4, 3),    # Viernes Santo
            date(2026, 5, 1),    # Fiesta del Trabajo
            date(2026, 5, 2),    # Fiesta de la Comunidad de Madrid
            date(2026, 5, 15),   # San Isidro (local, Madrid capital)
            date(2026, 8, 15),   # Asunción de la Virgen
            date(2026, 10, 12),  # Fiesta Nacional de España
            date(2026, 11, 2),   # Traslado de Todos los Santos (verificar BOCM)
            date(2026, 11, 9),   # Nuestra Señora de la Almudena (local)
            date(2026, 12, 7),   # Traslado del Día de la Constitución (verificar BOCM)
            date(2026, 12, 8),   # Inmaculada Concepción
            date(2026, 12, 25),  # Natividad del Señor
        }
    ),
}


def es_festivo(d: date) -> bool:
    """True si `d` es festivo en Madrid capital.

    Si el año no está cargado en la tabla se lanza un error en vez de asumir
    laborable: cotizar con un calendario desconocido produciría tarifas
    (T1/T2) potencialmente incorrectas, y esto es una pieza regulada.
    """
    try:
        return d in FESTIVOS_MADRID[d.year]
    except KeyError:
        raise CalendarioNoDisponible(
            f"No hay calendario de festivos cargado para el año {d.year}. "
            "Actualiza app/pricing/festivos.py con el BOCM correspondiente."
        ) from None


class CalendarioNoDisponible(Exception):
    pass
