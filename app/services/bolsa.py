"""Bolsa de viajes: solicitudes abiertas que cualquier taxista disponible
puede aceptar. El primero que acepta gana (bloqueo de fila) y la solicitud
se convierte en una reserva normal con su justificante."""
from __future__ import annotations

import math
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ClienteFinal, Cotizacion, Reserva, SolicitudViaje, Tenant
from app.pricing import precio_cerrado
from app.pricing.motor import TZ_MADRID
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
    tenant_destino_id=None,
    precio_estimado=None,
    cotizacion_id=None,
) -> SolicitudViaje:
    """`tenant_destino_id`: reserva directa que debe aceptar ese taxista.
    `precio_estimado`: si viene de una cotización ya calculada (precio
    máximo mostrado al pasajero), se usa tal cual sin recalcular ruta."""
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

    if precio_estimado is None:
        try:
            origen = origen_lugar or _geocodificar(geocoder, "origen", origen_texto)
            destino = destino_lugar or _geocodificar(geocoder, "destino", destino_texto)
            ruta = rutas.calcular(origen, destino, fecha_hora_recogida, con_peaje=False)
        except ErrorCotizacion:
            raise
        except Exception:
            raise ServicioNoDisponible()

        # Estimación con las tarifas oficiales (iguales para todos);
        # el precio definitivo lo emite el taxista que acepte, con su motor.
        estimacion = precio_cerrado(ruta.tramos, fecha_hora_recogida, peaje=Decimal("0"))
        precio_estimado = estimacion.precio
    else:
        origen, destino = origen_lugar, destino_lugar

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
        precio_estimado=precio_estimado,
        intermediario_id=intermediario_id,
        tenant_destino_id=tenant_destino_id,
        cotizacion_id=cotizacion_id,
    )
    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)
    return solicitud


def solicitar_reserva_directa(
    db: Session, tenant: Tenant, cotizacion_id, nombre: str, telefono: str,
    email: str | None,
) -> SolicitudViaje:
    """El pasajero pide la reserva al precio máximo de su cotización. No se
    crea reserva ni justificante todavía: queda pendiente hasta que el
    taxista la acepte (desde el panel o los botones de Telegram)."""
    try:
        cot_uuid = uuid.UUID(str(cotizacion_id))
    except ValueError:
        raise ErrorBolsa("La cotización no existe")
    cot = db.execute(
        select(Cotizacion).where(
            Cotizacion.id == cot_uuid, Cotizacion.tenant_id == tenant.id
        )
    ).scalar_one_or_none()
    if cot is None:
        raise ErrorBolsa("La cotización no existe")
    expira = cot.expira_en
    if expira.tzinfo is None:  # SQLite pierde el tzinfo
        expira = expira.replace(tzinfo=timezone.utc)
    if expira < datetime.now(timezone.utc):
        raise ErrorBolsa("La oferta ha caducado (15 minutos). Vuelve a pedir precio.")

    previa = db.execute(
        select(SolicitudViaje.estado).where(
            SolicitudViaje.cotizacion_id == cot.id,
            SolicitudViaje.estado.in_(("abierta", "asignada")),
        )
    ).scalars().first()
    if previa == "asignada":
        raise ErrorBolsa(
            "Esta oferta ya se convirtió en una reserva. Pide un precio nuevo."
        )
    if previa is not None:
        raise ErrorBolsa(
            "Ya has solicitado esta reserva; espera la respuesta del taxista."
        )

    activas = db.execute(
        select(func.count())
        .select_from(Reserva)
        .join(ClienteFinal, Reserva.cliente_id == ClienteFinal.id)
        .where(
            Reserva.tenant_id == tenant.id,
            ClienteFinal.telefono == telefono.strip(),
            Reserva.estado.in_(("aceptada", "recordada")),
        )
    ).scalar_one()
    if activas >= settings.max_reservas_activas_por_telefono:
        raise ErrorBolsa(
            "Has alcanzado el máximo de reservas activas con este teléfono. "
            "Llama al taxista para gestionarlo."
        )

    recogida = cot.fecha_hora_recogida
    if recogida.tzinfo is None:  # SQLite: hora de Madrid
        recogida = recogida.replace(tzinfo=TZ_MADRID)

    return crear_solicitud(
        db, None, None,
        nombre=nombre,
        telefono=telefono,
        email=email,
        origen_texto=cot.origen_texto,
        destino_texto=cot.destino_texto,
        fecha_hora_recogida=recogida,
        origen_lugar=Lugar(cot.origen_texto, cot.origen_lat, cot.origen_lng),
        destino_lugar=Lugar(cot.destino_texto, cot.destino_lat, cot.destino_lng),
        tenant_destino_id=tenant.id,
        precio_estimado=cot.precio,
        cotizacion_id=cot.id,
    )


def _sin_respuesta(solicitud: SolicitudViaje, ahora: datetime) -> bool:
    creada = solicitud.creada_en
    if creada.tzinfo is None:  # SQLite pierde el tzinfo (UTC)
        creada = creada.replace(tzinfo=timezone.utc)
    return creada < ahora - timedelta(minutes=settings.solicitud_ttl_min)


def caducar_si_procede(db: Session, solicitud: SolicitudViaje) -> SolicitudViaje:
    """Caducidad perezosa: si una reserva directa lleva demasiado sin
    respuesta del taxista, se marca al consultarla (la página de espera del
    pasajero no depende del cron)."""
    if (solicitud.estado == "abierta" and solicitud.tenant_destino_id
            and _sin_respuesta(solicitud, datetime.now(timezone.utc))):
        solicitud.estado = "caducada"
        db.commit()
    return solicitud


def caducar_solicitudes_directas(db: Session) -> list[SolicitudViaje]:
    """Barrido del cron: marca caducadas las reservas directas sin
    respuesta y las devuelve para avisar a sus pasajeros."""
    ahora = datetime.now(timezone.utc)
    pendientes = (
        db.execute(
            select(SolicitudViaje).where(
                SolicitudViaje.estado == "abierta",
                SolicitudViaje.tenant_destino_id.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    caducadas = [s for s in pendientes if _sin_respuesta(s, ahora)]
    for s in caducadas:
        s.estado = "caducada"
    if caducadas:
        db.commit()
    return caducadas


def reenviar_a_bolsa(db: Session, solicitud: SolicitudViaje) -> SolicitudViaje:
    """El taxista no respondió (o rechazó): el pasajero publica el mismo
    viaje en la bolsa general con un clic."""
    if solicitud.estado not in ("caducada", "rechazada"):
        raise ErrorBolsa("Esta solicitud no se puede enviar a la bolsa")
    return crear_solicitud(
        db, None, None,
        nombre=solicitud.nombre,
        telefono=solicitud.telefono,
        email=solicitud.email,
        origen_texto=solicitud.origen_texto,
        destino_texto=solicitud.destino_texto,
        fecha_hora_recogida=(
            solicitud.fecha_hora_recogida
            if solicitud.fecha_hora_recogida.tzinfo
            else solicitud.fecha_hora_recogida.replace(tzinfo=TZ_MADRID)
        ),
        origen_lugar=Lugar(solicitud.origen_texto, solicitud.origen_lat,
                           solicitud.origen_lng),
        destino_lugar=Lugar(solicitud.destino_texto, solicitud.destino_lat,
                            solicitud.destino_lng),
        intermediario_id=solicitud.intermediario_id,
        precio_estimado=solicitud.precio_estimado,
    )


def solicitudes_abiertas(db: Session) -> list[SolicitudViaje]:
    ahora = datetime.now(timezone.utc)
    abiertas = (
        db.execute(
            select(SolicitudViaje)
            .where(SolicitudViaje.estado == "abierta",
                   SolicitudViaje.tenant_destino_id.is_(None))
            .order_by(SolicitudViaje.fecha_hora_recogida)
        )
        .scalars()
        .all()
    )

    def futura(s: SolicitudViaje) -> bool:
        recogida = s.fecha_hora_recogida
        if recogida.tzinfo is None:  # SQLite pierde el tzinfo (hora Madrid)
            recogida = recogida.replace(tzinfo=TZ_MADRID)
        return recogida > ahora

    return [s for s in abiertas if futura(s)]


def radio_de(tenant) -> float:
    """Radio de la bolsa de este taxista (su ajuste o el global)."""
    return float(tenant.radio_km) if tenant.radio_km else settings.bolsa_radio_km


def distancia_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Distancia haversine en km entre dos puntos."""
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


def solicitudes_pendientes_de(db: Session, tenant: Tenant) -> list[SolicitudViaje]:
    """Reservas directas esperando la aceptación de este taxista (las que
    ya vencieron sin respuesta no se ofrecen)."""
    ahora = datetime.now(timezone.utc)
    pendientes = (
        db.execute(
            select(SolicitudViaje)
            .where(SolicitudViaje.estado == "abierta",
                   SolicitudViaje.tenant_destino_id == tenant.id)
            .order_by(SolicitudViaje.fecha_hora_recogida)
        )
        .scalars()
        .all()
    )
    return [s for s in pendientes if not _sin_respuesta(s, ahora)]


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
    precio_pactado=None,
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
    if solicitud.estado == "caducada":
        raise ErrorBolsa("La solicitud caducó sin respuesta; el pasajero "
                         "puede volver a enviarla desde su enlace")
    if solicitud.estado != "abierta":
        raise ViajeYaAsignado()
    if solicitud.tenant_destino_id and solicitud.tenant_destino_id != tenant.id:
        raise ErrorBolsa("Esta solicitud es de otro taxista")
    if solicitud.tenant_destino_id and _sin_respuesta(solicitud, datetime.now(timezone.utc)):
        solicitud.estado = "caducada"
        db.commit()
        raise ErrorBolsa("La solicitud caducó sin respuesta; el pasajero "
                         "puede volver a enviarla desde su enlace")

    # Se marca ya como asignada: el commit interno de aceptar_reserva la
    # persistirá junto con la reserva y soltará el bloqueo sin ventana para
    # una doble asignación. Si algo falla antes, el rollback la reabre.
    solicitud.estado = "asignada"

    recogida = solicitud.fecha_hora_recogida
    if recogida.tzinfo is None:
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
        precio_pactado=precio_pactado,
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


def rechazar_solicitud(db: Session, tenant: Tenant, solicitud_id) -> SolicitudViaje:
    """Solo el taxista destinatario puede rechazar una reserva directa."""
    solicitud = db.execute(
        select(SolicitudViaje).where(SolicitudViaje.id == solicitud_id).with_for_update()
    ).scalar_one_or_none()
    if solicitud is None:
        raise ErrorBolsa("La solicitud no existe")
    if solicitud.tenant_destino_id != tenant.id:
        raise ErrorBolsa("Esta solicitud no es tuya")
    if solicitud.estado != "abierta":
        raise ViajeYaAsignado()
    solicitud.estado = "rechazada"
    db.commit()
    return solicitud
