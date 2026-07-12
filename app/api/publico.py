"""API pública del pasajero (plan §11)."""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.antifraude import comprobar_honeypot, limitar_por_ip
from app.db import get_db
from app.models import Tenant
from app.services.cotizaciones import (
    DecisionPeajeRequerida,
    DesambiguacionRequerida,
    ErrorCotizacion,
    crear_cotizacion,
)
from app.services.notificaciones import notificar_cancelacion, notificar_confirmacion
from app.services.reservas import (
    ErrorReserva,
    aceptar_reserva,
    cancelar_reserva,
    reserva_por_token,
)

from .deps import email_sender, parsear_fecha_recogida, proveedores, tenant_por_slug

router = APIRouter(prefix="/api")


class CotizacionIn(BaseModel):
    origen: str = Field(min_length=3, max_length=255)
    destino: str = Field(min_length=3, max_length=255)
    fecha_hora_recogida: str
    con_peaje: bool | None = None
    website: str | None = None  # honeypot


class ReservaIn(BaseModel):
    cotizacion_id: str
    nombre: str = Field(min_length=2, max_length=120)
    telefono: str = Field(min_length=9, max_length=20)
    email: str | None = None
    website: str | None = None  # honeypot


@router.post("/t/{slug}/cotizaciones")
def cotizar(
    datos: CotizacionIn,
    request: Request,
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    provs=Depends(proveedores),
):
    limitar_por_ip(request)
    comprobar_honeypot(datos.website)
    geocoder, rutas = provs
    try:
        cot = crear_cotizacion(
            db,
            tenant,
            geocoder,
            rutas,
            datos.origen,
            datos.destino,
            parsear_fecha_recogida(datos.fecha_hora_recogida),
            con_peaje=datos.con_peaje,
        )
        db.commit()
    except DesambiguacionRequerida as e:
        return {
            "requiere_desambiguacion": e.campo,
            "opciones": [{"texto": l.texto, "lat": l.lat, "lng": l.lng} for l in e.opciones],
        }
    except DecisionPeajeRequerida as e:
        return {"requiere_decision_peaje": True, "importe_peaje": str(e.importe)}
    except ErrorCotizacion as e:
        raise HTTPException(422, str(e))

    return {
        "cotizacion_id": str(cot.id),
        "precio": str(cot.precio),
        "origen": cot.origen_texto,
        "destino": cot.destino_texto,
        "fecha_hora_recogida": cot.fecha_hora_recogida.isoformat(),
        "dist_km": str(cot.dist_km),
        "con_peaje": cot.con_peaje,
        "importe_peaje": str(cot.importe_peaje) if cot.importe_peaje else None,
        "descuento_contaminacion": cot.descuento_contaminacion,
        "expira_en": cot.expira_en.isoformat(),
        "condiciones": condiciones_texto(),
    }


@router.post("/t/{slug}/reservas")
def reservar(
    datos: ReservaIn,
    request: Request,
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
):
    limitar_por_ip(request)
    comprobar_honeypot(datos.website)
    try:
        reserva = aceptar_reserva(
            db, tenant, datos.cotizacion_id, datos.nombre, datos.telefono, datos.email
        )
    except ErrorReserva as e:
        raise HTTPException(422, str(e))
    notificar_confirmacion(db, sender, reserva)
    j = reserva.justificante
    return {
        "reserva_token": reserva.token_publico,
        "enlace": f"/r/{reserva.token_publico}",
        "precio_cerrado": str(reserva.precio_cerrado),
        "justificante": {"serie": j.serie, "numero": j.numero},
        "estado": reserva.estado,
    }


@router.post("/reservas/{token}/cancelar")
def cancelar(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
):
    limitar_por_ip(request)
    reserva = reserva_por_token(db, token)
    if reserva is None:
        raise HTTPException(404, "Reserva no encontrada")
    ya_cancelada = reserva.estado == "cancelada"
    try:
        cancelar_reserva(db, reserva)
    except ErrorReserva as e:
        raise HTTPException(422, str(e))
    if not ya_cancelada:
        notificar_cancelacion(db, sender, reserva)
    return {"estado": reserva.estado}


def condiciones_texto() -> str:
    return (
        "Precio máximo calculado con las tarifas oficiales del taxi de Madrid "
        "(T1/T2, BOCM 2026) y las instrucciones municipales de precio cerrado. "
        "Pagarás el importe menor entre este precio y el que marque el taxímetro. "
        "Espera de cortesía de 5 minutos en el punto de recogida. Itinerario "
        "directo, sin paradas intermedias. El pago se realiza a bordo (efectivo "
        "o tarjeta). Recibirás el justificante antes de iniciar el viaje."
    )
