"""Aceptación, consulta y cancelación de reservas."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ClienteFinal, Cotizacion, Reserva, Tenant

from .justificantes import emitir_justificante


class ErrorReserva(Exception):
    """El mensaje es apto para mostrar al pasajero."""


class CotizacionCaducada(ErrorReserva):
    pass


class LimiteReservasActivas(ErrorReserva):
    pass


def _upsert_cliente(
    db: Session, tenant: Tenant, nombre: str, telefono: str, email: str | None
) -> ClienteFinal:
    cliente = db.execute(
        select(ClienteFinal).where(
            ClienteFinal.tenant_id == tenant.id, ClienteFinal.telefono == telefono
        )
    ).scalar_one_or_none()
    if cliente is None:
        cliente = ClienteFinal(
            tenant_id=tenant.id, telefono=telefono, nombre=nombre, email=email
        )
        db.add(cliente)
        db.flush()
    else:
        cliente.nombre = nombre
        if email:
            cliente.email = email
    return cliente


def aceptar_reserva(
    db: Session,
    tenant: Tenant,
    cotizacion_id,
    nombre: str,
    telefono: str,
    email: str | None = None,
    canal: str = "web",
) -> Reserva:
    try:
        cotizacion_uuid = uuid.UUID(str(cotizacion_id))
    except ValueError:
        raise ErrorReserva("La cotización no existe")
    cotizacion = db.execute(
        select(Cotizacion).where(
            Cotizacion.id == cotizacion_uuid, Cotizacion.tenant_id == tenant.id
        )
    ).scalar_one_or_none()
    if cotizacion is None:
        raise ErrorReserva("La cotización no existe")

    ya_usada = db.execute(
        select(Reserva.id).where(Reserva.cotizacion_id == cotizacion.id)
    ).first()
    if ya_usada:
        raise ErrorReserva(
            "Esta oferta ya se convirtió en una reserva. Pide un precio nuevo."
        )

    ahora = datetime.now(timezone.utc)
    expira = cotizacion.expira_en
    if expira.tzinfo is None:  # SQLite pierde el tzinfo
        expira = expira.replace(tzinfo=timezone.utc)
    if expira < ahora:
        raise CotizacionCaducada(
            "La oferta ha caducado (15 minutos). Vuelve a pedir precio."
        )

    activas = db.execute(
        select(func.count())
        .select_from(Reserva)
        .join(ClienteFinal, Reserva.cliente_id == ClienteFinal.id)
        .where(
            Reserva.tenant_id == tenant.id,
            ClienteFinal.telefono == telefono,
            Reserva.estado.in_(("aceptada", "recordada")),
        )
    ).scalar_one()
    if activas >= settings.max_reservas_activas_por_telefono:
        raise LimiteReservasActivas(
            "Has alcanzado el máximo de reservas activas con este teléfono. "
            "Llama al taxista para gestionarlo."
        )

    cliente = _upsert_cliente(db, tenant, nombre, telefono, email)

    from app.models import CalculoPrecio  # import local para evitar ciclo

    reserva = Reserva(
        tenant_id=tenant.id,
        cliente_id=cliente.id,
        cotizacion_id=cotizacion.id,
        token_publico=secrets.token_urlsafe(24),
        canal=canal,
        precio_cerrado=cotizacion.precio,
        descuento_contaminacion=cotizacion.descuento_contaminacion,
        estado="aceptada",
        aceptada_en=ahora,
    )
    db.add(reserva)
    db.flush()

    db.add(
        CalculoPrecio(
            reserva_id=reserva.id,
            version_tarifas=cotizacion.version_tarifas,
            payload=cotizacion.calculo_payload,
            precio_resultante=cotizacion.precio,
        )
    )
    emitir_justificante(db, reserva)
    db.commit()
    db.refresh(reserva)
    return reserva


def reserva_por_token(db: Session, token: str) -> Reserva | None:
    return db.execute(
        select(Reserva).where(Reserva.token_publico == token)
    ).scalar_one_or_none()


def cancelar_reserva(db: Session, reserva: Reserva) -> Reserva:
    if reserva.estado == "cancelada":
        return reserva
    if reserva.estado == "completada":
        raise ErrorReserva("No se puede cancelar una reserva ya completada")
    reserva.estado = "cancelada"
    reserva.cancelada_en = datetime.now(timezone.utc)
    db.commit()
    return reserva
