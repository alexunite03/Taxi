"""Retención de datos (RGPD, minimización): pasado el plazo configurado
(`TAXI_RETENCION_MESES`, 12 por defecto) se anonimizan los datos personales
de las solicitudes y de los clientes sin actividad. Los justificantes y los
payloads de cálculo se conservan (obligación de trazabilidad y plazo
fiscal); lo que se borra es el vínculo con la persona.

Se ejecuta desde /api/cron: es idempotente y barato."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ClienteFinal, Reserva, SolicitudViaje

ANONIMO = "[anonimizado]"


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def anonimizar_datos_antiguos(db: Session) -> dict:
    limite = datetime.now(timezone.utc) - timedelta(days=30 * settings.retencion_meses)
    solicitudes = 0
    for s in db.execute(
        select(SolicitudViaje).where(SolicitudViaje.nombre != ANONIMO)
    ).scalars():
        if _utc(s.creada_en) < limite:
            s.nombre, s.telefono, s.email = ANONIMO, "", None
            solicitudes += 1

    clientes = 0
    for c in db.execute(
        select(ClienteFinal).where(ClienteFinal.nombre != ANONIMO)
    ).scalars():
        ultima = db.execute(
            select(func.max(Reserva.creada_en)).where(Reserva.cliente_id == c.id)
        ).scalar()
        referencia = ultima or c.fecha_consentimiento
        if _utc(referencia) < limite:
            c.nombre = ANONIMO
            c.telefono = f"anon-{c.id.hex[:10]}"
            c.email = None
            clientes += 1

    if solicitudes or clientes:
        db.commit()
    return {"solicitudes_anonimizadas": solicitudes, "clientes_anonimizados": clientes}
