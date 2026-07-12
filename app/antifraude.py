"""Antifraude básico del MVP (plan §11): rate limit por IP en memoria y
honeypot en el formulario.

En producción con más de un worker esto pasa a Redis; la interfaz se mantiene.
El límite de reservas activas por teléfono vive en el servicio de reservas.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import settings

_ventanas: dict[str, deque[float]] = defaultdict(deque)
_VENTANA_S = 3600


def limitar_por_ip(request: Request) -> None:
    ip = request.client.host if request.client else "desconocida"
    ahora = time.monotonic()
    cola = _ventanas[ip]
    while cola and cola[0] < ahora - _VENTANA_S:
        cola.popleft()
    if len(cola) >= settings.rate_limit_por_ip_hora:
        raise HTTPException(429, "Demasiadas peticiones. Inténtalo más tarde.")
    cola.append(ahora)


def comprobar_honeypot(valor: str | None) -> None:
    """El campo 'website' está oculto para humanos; si llega relleno es un bot."""
    if valor:
        raise HTTPException(400, "Petición no válida")
