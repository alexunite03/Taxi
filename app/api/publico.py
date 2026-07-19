"""API pública del pasajero (plan §11)."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
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
from app.config import settings
from app.services.notificaciones import notificar_cancelacion, suscribir_push
from app.services.reservas import (
    ErrorReserva,
    cancelar_reserva,
    reserva_por_token,
)

from .deps import (
    email_sender,
    telegram_sender,
    parsear_fecha_recogida,
    proveedores,
    push_sender,
    tenant_por_slug,
)

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


@router.get("/geocode")
def geocode_global(q: str, request: Request, provs=Depends(proveedores)):
    """Autocompletar para la bolsa de viajes (sin tenant)."""
    limitar_por_ip(request)
    q = q.strip()
    if len(q) < 3:
        return {"opciones": []}
    geocoder, _ = provs
    try:
        lugares = geocoder.geocodificar(q)
    except Exception:
        return {"opciones": []}
    return {
        "opciones": [
            {"texto": l.texto, "lat": l.lat, "lng": l.lng} for l in lugares[:5]
        ]
    }


@router.get("/reverse")
def reverse_geocode(lat: float, lng: float, request: Request, provs=Depends(proveedores)):
    """Dirección aproximada para el botón «usar mi ubicación»."""
    limitar_por_ip(request)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(422, "Coordenadas no válidas")
    geocoder, _ = provs
    try:
        lugar = geocoder.invertir(lat, lng)
    except Exception:
        lugar = None
    if lugar is None:
        return {}
    return {"texto": lugar.texto, "lat": lugar.lat, "lng": lugar.lng}


@router.get("/t/{slug}/geocode")
def geocode(
    q: str,
    request: Request,
    tenant: Tenant = Depends(tenant_por_slug),
    provs=Depends(proveedores),
):
    """Autocompletar de direcciones para el formulario (paso 1 y 2)."""
    limitar_por_ip(request)
    q = q.strip()
    if len(q) < 3:
        return {"opciones": []}
    geocoder, _ = provs
    try:
        lugares = geocoder.geocodificar(q)
    except Exception:
        return {"opciones": []}  # el formulario sigue funcionando sin sugerencias
    return {
        "opciones": [
            {"texto": l.texto, "lat": l.lat, "lng": l.lng} for l in lugares[:5]
        ]
    }


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
    background: BackgroundTasks,
    tenant: Tenant = Depends(tenant_por_slug),
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    telegram=Depends(telegram_sender),
):
    """Crea una solicitud pendiente al precio máximo de la cotización; la
    reserva y el justificante se emiten cuando el taxista acepta."""
    from app.services.bolsa import ErrorBolsa, solicitar_reserva_directa
    from app.services.notificaciones import tarea_solicitud_directa

    limitar_por_ip(request)
    comprobar_honeypot(datos.website)
    try:
        solicitud = solicitar_reserva_directa(
            db, tenant, datos.cotizacion_id, datos.nombre, datos.telefono, datos.email
        )
    except (ErrorBolsa, ErrorCotizacion) as e:
        raise HTTPException(422, str(e))
    background.add_task(tarea_solicitud_directa, solicitud.id, sender, telegram)
    return {
        "solicitud_token": solicitud.token_publico,
        "enlace": f"/s/{solicitud.token_publico}",
        "precio_maximo": str(solicitud.precio_estimado),
        "estado": solicitud.estado,
    }


@router.post("/reservas/{token}/cancelar")
def cancelar(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    sender=Depends(email_sender),
    push=Depends(push_sender),
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
        notificar_cancelacion(db, sender, push, reserva)
    return {"estado": reserva.estado}


class PushSuscripcionIn(BaseModel):
    token: str
    suscripcion: dict


@router.get("/push/clave-publica")
def clave_publica_vapid():
    """Clave pública VAPID para `pushManager.subscribe` (vacía en desarrollo
    con el proveedor console: el navegador no podrá suscribirse, pero la API
    de alta sigue siendo utilizable en tests)."""
    return {"clave": settings.vapid_public_key or None}


@router.post("/push/suscripcion")
def alta_push(
    datos: PushSuscripcionIn,
    request: Request,
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    reserva = reserva_por_token(db, datos.token)
    if reserva is None:
        raise HTTPException(404, "Reserva no encontrada")
    try:
        suscribir_push(db, reserva, datos.suscripcion)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


def condiciones_texto() -> str:
    return (
        "Precio máximo calculado con las tarifas oficiales del taxi de Madrid "
        "(T1/T2, BOCM 2026) y las instrucciones municipales de precio cerrado. "
        "Pagarás el importe menor entre este precio y el que marque el taxímetro. "
        "Espera de cortesía de 5 minutos en el punto de recogida. Itinerario "
        "directo, sin paradas intermedias. El pago se realiza a bordo (efectivo "
        "o tarjeta). Recibirás el justificante antes de iniciar el viaje."
    )
