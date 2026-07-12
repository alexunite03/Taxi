"""Emisión del justificante de precontratación (art. 22 bis ORT).

Numeración correlativa por serie de cada taxista con bloqueo de la fila del
tenant (SELECT … FOR UPDATE). Se archiva el HTML (y el PDF si WeasyPrint
está disponible) junto con su hash SHA-256.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Justificante, Reserva, Tenant

_TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=select_autoescape(["html"]),
)

try:  # dependencia opcional: requiere libs de sistema (pango/cairo)
    from weasyprint import HTML as _WeasyHTML  # type: ignore
except Exception:  # pragma: no cover
    _WeasyHTML = None


def _siguiente_numero(db: Session, tenant_id) -> tuple[str, int]:
    """Contador atómico por tenant. En PostgreSQL bloquea la fila; en SQLite
    el escritor único da la misma garantía."""
    tenant = db.execute(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    ).scalar_one()
    tenant.contador_justificante += 1
    return tenant.serie_justificante, tenant.contador_justificante


def _contexto(reserva: Reserva, serie: str, numero: int) -> dict:
    return {
        "tenant": reserva.tenant,
        "reserva": reserva,
        "cotizacion": reserva.cotizacion,
        "cliente": reserva.cliente,
        "serie": serie,
        "numero": numero,
    }


def render_fragment(reserva: Reserva, serie: str, numero: int) -> str:
    """Fragmento para embeber en la página /r/{token}."""
    return _env.get_template("justificante.html").render(_contexto(reserva, serie, numero))


def render_html(reserva: Reserva, serie: str, numero: int) -> str:
    """Documento completo que se archiva (y del que sale el PDF)."""
    return _env.get_template("justificante_doc.html").render(_contexto(reserva, serie, numero))


def emitir_justificante(db: Session, reserva: Reserva) -> Justificante:
    serie, numero = _siguiente_numero(db, reserva.tenant_id)
    html = render_html(reserva, serie, numero)

    carpeta = settings.justificantes_dir / str(reserva.tenant_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    base = carpeta / f"{serie}-{numero:06d}"

    html_path = base.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    pdf_path = None
    if _WeasyHTML is not None:  # pragma: no cover
        pdf_path = base.with_suffix(".pdf")
        _WeasyHTML(string=html).write_pdf(str(pdf_path))

    justificante = Justificante(
        tenant_id=reserva.tenant_id,
        reserva_id=reserva.id,
        serie=serie,
        numero=numero,
        html_path=str(html_path),
        pdf_path=str(pdf_path) if pdf_path else None,
        hash_documento=hashlib.sha256(html.encode()).hexdigest(),
    )
    db.add(justificante)
    db.flush()
    return justificante
