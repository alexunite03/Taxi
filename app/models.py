"""Modelo de datos v2 (plan §10), multi-tenant.

Tipos portables entre PostgreSQL (producción) y SQLite (desarrollo/tests).
En producción se añade Row Level Security por tenant_id (migración SQL aparte).

Desviación documentada respecto al plan: `cotizaciones.calculo_payload`
guarda el desglose del cálculo desde la cotización, y se copia a
`calculos_precio` al aceptar, para garantizar que el justificante refleja
exactamente el cálculo ofertado (sin recomputar).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    nif: Mapped[str] = mapped_column(String(15))
    num_licencia: Mapped[str] = mapped_column(String(20))
    matricula: Mapped[str] = mapped_column(String(15), default="")

    email: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    serie_justificante: Mapped[str] = mapped_column(String(10), default="A")
    contador_justificante: Mapped[int] = mapped_column(Integer, default=0)

    antelacion_min: Mapped[int] = mapped_column(Integer, default=30)  # minutos
    antelacion_max_dias: Mapped[int] = mapped_column(Integer, default=30)

    sms_activado: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_contaminacion: Mapped[bool] = mapped_column(Boolean, default=False)
    disponible_bolsa: Mapped[bool] = mapped_column(Boolean, default=True)

    # Perfil público
    bio: Mapped[str] = mapped_column(String(500), default="")
    foto_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Política de precio del taxista: descuento comercial (el precio cerrado
    # es un máximo, rebajarlo siempre es legal) y suplemento de recogida
    # entre 0 y el máximo reglamentario de 5,00 €.
    descuento_pct: Mapped[int] = mapped_column(Integer, default=0)
    recogida_eur: Mapped[float] = mapped_column(Numeric(3, 2), default=5.00)

    # Avisos por Telegram (chat con el bot de la plataforma)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Código de un solo uso para vincular el chat con /start <código>
    telegram_codigo: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Última ubicación compartida por Telegram (modo Uber): con ella solo
    # recibe los viajes de la bolsa a menos de `bolsa_radio_km`.
    ubicacion_lat: Mapped[float | None] = mapped_column(nullable=True)
    ubicacion_lng: Mapped[float | None] = mapped_column(nullable=True)
    ubicacion_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Radio en km para aceptar viajes de la bolsa; NULL = valor global
    radio_km: Mapped[float | None] = mapped_column(nullable=True)

    estado_suscripcion: Mapped[str] = mapped_column(String(20), default="activa")
    # DSA art. 30: None = alta anterior al KYC (se mantiene listado),
    # False = pendiente de verificar (NO listado), True = verificado
    verificado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    fecha_alta: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    reservas: Mapped[list["Reserva"]] = relationship(back_populates="tenant")


class ClienteFinal(Base):
    __tablename__ = "clientes_finales"
    __table_args__ = (UniqueConstraint("tenant_id", "telefono"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    telefono: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    nombre: Mapped[str] = mapped_column(String(120))
    consentimiento_rgpd: Mapped[bool] = mapped_column(Boolean, default=True)
    fecha_consentimiento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora
    )


class Cotizacion(Base):
    """Oferta previa; caduca a los 15 minutos sin aceptar."""

    __tablename__ = "cotizaciones"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)

    origen_texto: Mapped[str] = mapped_column(String(255))
    origen_lat: Mapped[float]
    origen_lng: Mapped[float]
    destino_texto: Mapped[str] = mapped_column(String(255))
    destino_lat: Mapped[float]
    destino_lng: Mapped[float]

    fecha_hora_recogida: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    con_peaje: Mapped[bool] = mapped_column(Boolean, default=False)
    importe_peaje: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    dist_km: Mapped[float] = mapped_column(Numeric(7, 2))
    ruta_geojson: Mapped[list | None] = mapped_column(JSON, nullable=True)
    precio: Mapped[float] = mapped_column(Numeric(7, 2))
    descuento_contaminacion: Mapped[bool] = mapped_column(Boolean, default=False)
    calculo_payload: Mapped[dict] = mapped_column(JSON)
    version_tarifas: Mapped[str] = mapped_column(String(30))

    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


CANALES = ("web", "telefono_asistida", "telegram", "bolsa")
ESTADOS_RESERVA = ("aceptada", "recordada", "completada", "cancelada")


class Reserva(Base):
    __tablename__ = "reservas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    cliente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clientes_finales.id"))
    cotizacion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cotizaciones.id"))

    token_publico: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    canal: Mapped[str] = mapped_column(String(20), default="web")

    precio_cerrado: Mapped[float] = mapped_column(Numeric(7, 2))
    descuento_contaminacion: Mapped[bool] = mapped_column(Boolean, default=False)
    estado: Mapped[str] = mapped_column(String(20), default="aceptada")

    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)
    aceptada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant: Mapped[Tenant] = relationship(back_populates="reservas")
    cliente: Mapped[ClienteFinal] = relationship()
    cotizacion: Mapped[Cotizacion] = relationship()
    justificante: Mapped["Justificante | None"] = relationship(
        back_populates="reserva", uselist=False
    )


class CalculoPrecio(Base):
    """Inmutable, uno por cotización aceptada. Es la respuesta a una
    auditoría del algoritmo."""

    __tablename__ = "calculos_precio"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reserva_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservas.id"), unique=True)
    version_tarifas: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    precio_resultante: Mapped[float] = mapped_column(Numeric(7, 2))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class Justificante(Base):
    __tablename__ = "justificantes"
    __table_args__ = (UniqueConstraint("tenant_id", "serie", "numero"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    reserva_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservas.id"), unique=True)

    serie: Mapped[str] = mapped_column(String(10))
    numero: Mapped[int] = mapped_column(Integer)
    pdf_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    html_path: Mapped[str] = mapped_column(String(255))
    # Copia del documento en la BD: el disco de los PaaS es efímero y el
    # archivo puede desaparecer en cada redeploy; de aquí se regenera.
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash_documento: Mapped[str] = mapped_column(String(64))
    emitido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    reserva: Mapped[Reserva] = relationship(back_populates="justificante")


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reserva_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservas.id"), index=True)
    canal: Mapped[str] = mapped_column(String(10))  # push | email | sms
    tipo: Mapped[str] = mapped_column(String(15))  # confirmacion | recordatorio | llegada | cancelacion
    estado: Mapped[str] = mapped_column(String(15), default="pendiente")
    coste: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    enviada_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PushSuscripcion(Base):
    __tablename__ = "push_suscripciones"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cliente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clientes_finales.id"))
    endpoint: Mapped[str] = mapped_column(String(500))
    claves: Mapped[dict] = mapped_column(JSON)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class SuscripcionSaas(Base):
    __tablename__ = "suscripciones_saas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    proveedor: Mapped[str] = mapped_column(String(15), default="manual")  # gocardless | stripe | manual
    ref_externa: Mapped[str | None] = mapped_column(String(100), nullable=True)
    importe: Mapped[float] = mapped_column(Numeric(6, 2))
    estado: Mapped[str] = mapped_column(String(20), default="activa")
    proximo_cobro: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Usuario(Base):
    """Cuenta opcional del pasajero: reservar nunca exige registro (plan §3),
    pero con cuenta puede consultar sus reservas en /mis-reservas.
    Las reservas se asocian por teléfono, que es la clave natural que ya
    usa `clientes_finales`."""

    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class Favorito(Base):
    """Taxistas guardados por un pasajero registrado."""

    __tablename__ = "favoritos"
    __table_args__ = (UniqueConstraint("usuario_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    tenant: Mapped[Tenant] = relationship()


class SolicitudViaje(Base):
    """Bolsa de viajes: el pasajero publica un trayecto y los taxistas
    disponibles pueden aceptarlo. El primero que acepta se lo lleva y la
    solicitud se convierte en una reserva normal de su agenda.

    Nota regulatoria: esta pieza acerca la plataforma a la intermediación
    (el resto del producto es marca blanca por taxista). Revisar su encaje
    antes del lanzamiento comercial.
    """

    __tablename__ = "solicitudes_viaje"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_publico: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    nombre: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)

    origen_texto: Mapped[str] = mapped_column(String(255))
    origen_lat: Mapped[float]
    origen_lng: Mapped[float]
    destino_texto: Mapped[str] = mapped_column(String(255))
    destino_lat: Mapped[float]
    destino_lng: Mapped[float]
    fecha_hora_recogida: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    intermediario_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("intermediarios.id"), nullable=True
    )
    # Si apunta a un taxista, es una reserva directa pendiente de que ESE
    # taxista la acepte (no sale en la bolsa general)
    tenant_destino_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True
    )
    # Cotización de origen de una reserva directa: impide solicitar dos
    # veces la misma oferta
    cotizacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cotizaciones.id"), nullable=True
    )

    precio_estimado: Mapped[float] = mapped_column(Numeric(7, 2))
    # precio_cerrado (por defecto) | taximetro: el pasajero paga lo que
    # marque el taxímetro (única opción en trayectos de aeropuerto, donde
    # el precio cerrado no aplica). Sin cotización, reserva ni justificante:
    # se confirma la propia solicitud. NULL en filas antiguas = precio_cerrado.
    modo: Mapped[str | None] = mapped_column(String(15), nullable=True,
                                             default="precio_cerrado")
    estado: Mapped[str] = mapped_column(String(15), default="abierta")  # abierta | asignada | cancelada | rechazada | caducada
    reserva_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reservas.id"), nullable=True
    )
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    reserva: Mapped["Reserva | None"] = relationship()
    intermediario: Mapped["Intermediario | None"] = relationship()
    ofertas: Mapped[list["OfertaViaje"]] = relationship(
        back_populates="solicitud", order_by="OfertaViaje.creada_en"
    )
    tenant_destino: Mapped["Tenant | None"] = relationship(
        foreign_keys=[tenant_destino_id]
    )


class OfertaViaje(Base):
    """Oferta de un taxista a una solicitud de la bolsa: se postula con su
    precio (siempre ≤ su máximo oficial) y EL PASAJERO ELIGE. La plataforma
    no asigna ni subasta: lista las ofertas por orden de llegada."""

    __tablename__ = "ofertas_viaje"
    __table_args__ = (UniqueConstraint("solicitud_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    solicitud_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("solicitudes_viaje.id"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))

    # Lo que verá el pasajero y los parámetros para reproducirlo al elegir
    precio: Mapped[float] = mapped_column(Numeric(7, 2))
    descuento_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recogida_eur: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    precio_pactado: Mapped[float | None] = mapped_column(Numeric(7, 2), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    tenant: Mapped[Tenant] = relationship()
    solicitud: Mapped["SolicitudViaje"] = relationship(back_populates="ofertas")


class Valoracion(Base):
    """Valoración del pasajero tras un servicio completado (una por reserva)."""

    __tablename__ = "valoraciones"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reserva_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservas.id"), unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    puntuacion: Mapped[int] = mapped_column(Integer)  # 1..5
    comentario: Mapped[str] = mapped_column(String(400), default="")
    autor: Mapped[str] = mapped_column(String(120), default="")
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class Intermediario(Base):
    """Cuenta para hoteles, restaurantes y conserjerías que piden taxis
    para sus clientes a través de la bolsa."""

    __tablename__ = "intermediarios"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(120))  # establecimiento
    contacto: Mapped[str] = mapped_column(String(120), default="")
    telefono: Mapped[str] = mapped_column(String(20))
    email: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    direccion_texto: Mapped[str] = mapped_column(String(255), default="")
    direccion_lat: Mapped[float | None] = mapped_column(nullable=True)
    direccion_lng: Mapped[float | None] = mapped_column(nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class Queja(Base):
    """Quejas y reclamaciones (ORT art. 47: el Ayuntamiento puede pedirlas).
    También sirve de mecanismo de notificación/retirada del DSA."""

    __tablename__ = "quejas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    texto: Mapped[str] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(String(64), nullable=True)  # token de reserva/solicitud
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(15), default="nueva")  # nueva | atendida
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)
