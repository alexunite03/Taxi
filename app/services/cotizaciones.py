"""Servicio de cotización: geocodifica, calcula la ruta y aplica el motor.

El resultado se persiste como `Cotizacion` con su desglose completo
(`calculo_payload`), que caduca a los `cotizacion_ttl_min` minutos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Cotizacion, Tenant
from app.pricing import precio_cerrado
from app.routing import Geocoder, Lugar, RouteProvider


class ErrorCotizacion(Exception):
    """Base: el mensaje es apto para mostrar al pasajero."""


class AntelacionInvalida(ErrorCotizacion):
    pass


class DireccionNoEncontrada(ErrorCotizacion):
    def __init__(self, campo: str, texto: str):
        self.campo = campo
        super().__init__(f"No encontramos la dirección de {campo}: «{texto}»")


class DesambiguacionRequerida(ErrorCotizacion):
    """Varias coincidencias: el pasajero debe elegir (paso 1 del formulario)."""

    def __init__(self, campo: str, opciones: list[Lugar]):
        self.campo = campo
        self.opciones = opciones
        super().__init__(f"Hay varias coincidencias para {campo}")


class DecisionPeajeRequerida(ErrorCotizacion):
    """La ruta más rápida incluye peaje y el pasajero aún no ha decidido."""

    def __init__(self, importe: Decimal):
        self.importe = importe
        super().__init__("La ruta más rápida incluye un peaje")


def _validar_antelacion(tenant: Tenant, recogida: datetime) -> None:
    ahora = datetime.now(timezone.utc)
    if recogida < ahora + timedelta(minutes=tenant.antelacion_min):
        raise AntelacionInvalida(
            f"La reserva necesita al menos {tenant.antelacion_min} minutos de antelación"
        )
    if recogida > ahora + timedelta(days=tenant.antelacion_max_dias):
        raise AntelacionInvalida(
            f"Solo aceptamos reservas hasta {tenant.antelacion_max_dias} días vista"
        )


def _geocodificar(geocoder: Geocoder, campo: str, texto: str) -> Lugar:
    lugares = geocoder.geocodificar(texto)
    if not lugares:
        raise DireccionNoEncontrada(campo, texto)
    if len(lugares) > 1:
        raise DesambiguacionRequerida(campo, lugares)
    return lugares[0]


def crear_cotizacion(
    db: Session,
    tenant: Tenant,
    geocoder: Geocoder,
    rutas: RouteProvider,
    origen_texto: str,
    destino_texto: str,
    fecha_hora_recogida: datetime,
    con_peaje: bool | None = None,
) -> Cotizacion:
    if fecha_hora_recogida.tzinfo is None:
        raise AntelacionInvalida("La fecha de recogida necesita zona horaria")
    _validar_antelacion(tenant, fecha_hora_recogida)

    origen = _geocodificar(geocoder, "origen", origen_texto)
    destino = _geocodificar(geocoder, "destino", destino_texto)

    ruta = rutas.calcular(
        origen, destino, fecha_hora_recogida, con_peaje=bool(con_peaje)
    )

    # Paso 4 del formulario: si hay peaje posible y el pasajero no ha
    # decidido, se le pregunta antes de ofertar.
    if ruta.peaje_estimado is not None and con_peaje is None:
        raise DecisionPeajeRequerida(ruta.peaje_estimado)

    peaje = ruta.peaje_estimado if (con_peaje and ruta.peaje_estimado) else Decimal("0")
    resultado = precio_cerrado(
        ruta.tramos,
        fecha_hora_recogida,
        peaje=peaje,
        escenario_no2=tenant.flag_contaminacion,
    )

    cotizacion = Cotizacion(
        tenant_id=tenant.id,
        origen_texto=origen.texto,
        origen_lat=origen.lat,
        origen_lng=origen.lng,
        destino_texto=destino.texto,
        destino_lat=destino.lat,
        destino_lng=destino.lng,
        fecha_hora_recogida=fecha_hora_recogida,
        con_peaje=bool(con_peaje and peaje),
        importe_peaje=peaje if peaje else None,
        dist_km=ruta.dist_km_total,
        precio=resultado.precio,
        descuento_contaminacion=resultado.payload["descuento_no2"] is not None,
        calculo_payload=resultado.payload,
        version_tarifas=resultado.version_tarifas,
        expira_en=datetime.now(timezone.utc)
        + timedelta(minutes=settings.cotizacion_ttl_min),
    )
    db.add(cotizacion)
    db.flush()
    return cotizacion
