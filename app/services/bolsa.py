"""Bolsa de viajes: solicitudes abiertas que cualquier taxista disponible
puede aceptar. El primero que acepta gana (bloqueo de fila) y la solicitud
se convierte en una reserva normal con su justificante."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SolicitudViaje, Tenant
from app.pricing import precio_cerrado
from app.routing import Geocoder, Lugar, RouteProvider

from .cotizaciones import (
    AntelacionInvalida,
    ErrorCotizacion,
    ServicioNoDisponible,
    _geocodificar,
    crear_cotizacion,
)
from .reservas import aceptar_reserva

ANTELACION_MIN_MINUTOS = 30
ANTELACION_MAX_DIAS = 30


class ErrorBolsa(Exception):
    """El mensaje es apto para mostrar al usuario."""


class ViajeYaAsignado(ErrorBolsa):
    def __init__(self):
        super().__init__("Otro taxista ha aceptado este viaje antes")


def crear_solicitud(
    db: Session,
    geocoder: Geocoder,
    rutas: RouteProvider,
    nombre: str,
    telefono: str,
    email: str | None,
    origen_texto: str,
    destino_texto: str,
    fecha_hora_recogida: datetime,
    origen_lugar: Lugar | None = None,
    destino_lugar: Lugar | None = None,
    intermediario_id=None,
) -> SolicitudViaje:
    ahora = datetime.now(timezone.utc)
    if fecha_hora_recogida.tzinfo is None:
        raise AntelacionInvalida("La fecha de recogida necesita zona horaria")
    if fecha_hora_recogida < ahora + timedelta(minutes=ANTELACION_MIN_MINUTOS):
        raise AntelacionInvalida(
            f"La solicitud necesita al menos {ANTELACION_MIN_MINUTOS} minutos de antelación"
        )
    if fecha_hora_recogida > ahora + timedelta(days=ANTELACION_MAX_DIAS):
        raise AntelacionInvalida(
            f"Solo aceptamos solicitudes hasta {ANTELACION_MAX_DIAS} días vista"
        )

    try:
        origen = origen_lugar or _geocodificar(geocoder, "origen", origen_texto)
        destino = destino_lugar or _geocodificar(geocoder, "destino", destino_texto)
        ruta = rutas.calcular(origen, destino, fecha_hora_recogida, con_peaje=False)
    except ErrorCotizacion:
        raise
    except Exception:
        raise ServicioNoDisponible()

    # Estimación con las tarifas oficiales (iguales para todos los taxistas);
    # el precio definitivo lo emite el taxista que acepte, con su motor.
    estimacion = precio_cerrado(ruta.tramos, fecha_hora_recogida, peaje=Decimal("0"))

    solicitud = SolicitudViaje(
        token_publico=secrets.token_urlsafe(24),
        nombre=nombre.strip(),
        telefono=telefono.strip(),
        email=(email or "").strip().lower() or None,
        origen_texto=origen.texto,
        origen_lat=origen.lat,
        origen_lng=origen.lng,
        destino_texto=destino.texto,
        destino_lat=destino.lat,
        destino_lng=destino.lng,
        fecha_hora_recogida=fecha_hora_recogida,
        precio_estimado=estimacion.precio,
        intermediario_id=intermediario_id,
    )
    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)
    return solicitud


def solicitudes_abiertas(db: Session) -> list[SolicitudViaje]:
    ahora = datetime.now(timezone.utc)
    abiertas = (
        db.execute(
            select(SolicitudViaje)
            .where(SolicitudViaje.estado == "abierta")
            .order_by(SolicitudViaje.fecha_hora_recogida)
        )
        .scalars()
        .all()
    )

    def futura(s: SolicitudViaje) -> bool:
        recogida = s.fecha_hora_recogida
        if recogida.tzinfo is None:  # SQLite pierde el tzinfo (hora Madrid)
            from app.pricing.motor import TZ_MADRID

            recogida = recogida.replace(tzinfo=TZ_MADRID)
        return recogida > ahora

    return [s for s in abiertas if futura(s)]


def radio_de(tenant) -> float:
    """Radio de la bolsa de este taxista (su ajuste o el global)."""
    from app.config import settings

    return float(tenant.radio_km) if tenant.radio_km else settings.bolsa_radio_km


def distancia_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Distancia haversine en km entre dos puntos."""
    import math

    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(x))


def con_distancia(
    solicitudes: list[SolicitudViaje], lat: float, lng: float
) -> list[SolicitudViaje]:
    """Anota `distancia_km` (recogida ↔ posición del taxista) y ordena por
    cercanía. Para el listado de la bolsa."""
    for s in solicitudes:
        s.distancia_km = round(distancia_km(lat, lng, s.origen_lat, s.origen_lng), 1)
    return sorted(solicitudes, key=lambda s: s.distancia_km)


def solicitud_por_token(db: Session, token: str) -> SolicitudViaje | None:
    return db.execute(
        select(SolicitudViaje).where(SolicitudViaje.token_publico == token)
    ).scalar_one_or_none()


def aceptar_solicitud(
    db: Session,
    tenant: Tenant,
    solicitud_id,
    geocoder: Geocoder,
    rutas: RouteProvider,
    descuento_pct: int | None = None,
    recogida_eur=None,
):
    """Primer taxista que llega, se lo lleva: bloqueo de la fila y
    comprobación del estado dentro de la transacción."""
    solicitud = db.execute(
        select(SolicitudViaje)
        .where(SolicitudViaje.id == solicitud_id)
        .with_for_update()
    ).scalar_one_or_none()
    if solicitud is None:
        raise ErrorBolsa("La solicitud no existe")
    if solicitud.estado != "abierta":
        raise ViajeYaAsignado()

    # Se marca ya como asignada: el commit interno de aceptar_reserva la
    # persistirá junto con la reserva y soltará el bloqueo sin ventana para
    # una doble asignación. Si algo falla antes, el rollback la reabre.
    solicitud.estado = "asignada"

    recogida = solicitud.fecha_hora_recogida
    if recogida.tzinfo is None:
        from app.pricing.motor import TZ_MADRID

        recogida = recogida.replace(tzinfo=TZ_MADRID)

    cotizacion = crear_cotizacion(
        db,
        tenant,
        geocoder,
        rutas,
        solicitud.origen_texto,
        solicitud.destino_texto,
        recogida,
        con_peaje=False,
        origen_lugar=Lugar(solicitud.origen_texto, solicitud.origen_lat, solicitud.origen_lng),
        destino_lugar=Lugar(solicitud.destino_texto, solicitud.destino_lat, solicitud.destino_lng),
        descuento_pct=descuento_pct,
        recogida_eur=recogida_eur,
    )
    reserva = aceptar_reserva(
        db,
        tenant,
        cotizacion.id,
        solicitud.nombre,
        solicitud.telefono,
        solicitud.email,
        canal="bolsa",
    )
    solicitud.reserva_id = reserva.id
    db.commit()
    db.refresh(reserva)
    return solicitud, reserva
