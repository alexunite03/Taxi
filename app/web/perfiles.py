"""Perfiles públicos de taxistas: buscador, perfil con foto y valoraciones."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.antifraude import limitar_por_ip
from app.db import get_db
from app.models import Tenant, Valoracion
from app.services.reservas import reserva_por_token

from .cuentas import usuario_sesion

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


def resumen_valoraciones(db: Session, tenant_id) -> tuple[float | None, int]:
    media, total = db.execute(
        select(func.avg(Valoracion.puntuacion), func.count(Valoracion.id)).where(
            Valoracion.tenant_id == tenant_id
        )
    ).one()
    return (round(float(media), 1) if media is not None else None), total


@router.get("/taxistas", response_class=HTMLResponse)
def buscador(request: Request, q: str = "", db: Session = Depends(get_db)):
    consulta = (
        select(Tenant)
        .where(Tenant.estado_suscripcion == "activa")
        .order_by(Tenant.fecha_alta)
        .limit(50)
    )
    q = q.strip()
    if q:
        consulta = consulta.where(Tenant.nombre.ilike(f"%{q}%"))
    taxistas = db.execute(consulta).scalars().all()
    tarjetas = [
        {"tenant": t, "media": media, "total": total}
        for t in taxistas
        for media, total in [resumen_valoraciones(db, t.id)]
    ]
    return templates.TemplateResponse(
        request, "taxistas.html", {"tarjetas": tarjetas, "q": q}
    )


@router.get("/t/{slug}/perfil", response_class=HTMLResponse)
def perfil(request: Request, slug: str, db: Session = Depends(get_db)):
    tenant = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
    if tenant is None:
        return HTMLResponse("<h1>Taxista no encontrado</h1>", status_code=404)
    media, total = resumen_valoraciones(db, tenant.id)
    valoraciones = (
        db.execute(
            select(Valoracion)
            .where(Valoracion.tenant_id == tenant.id)
            .order_by(Valoracion.creada_en.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    from .bolsa import es_favorito

    usuario = usuario_sesion(request, db)
    return templates.TemplateResponse(
        request,
        "perfil.html",
        {
            "tenant": tenant,
            "media": media,
            "total": total,
            "valoraciones": valoraciones,
            "usuario": usuario,
            "es_favorito": es_favorito(db, usuario, tenant),
        },
    )


@router.get("/t/{slug}/foto")
def foto(slug: str, db: Session = Depends(get_db)):
    tenant = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
    if tenant is None or not tenant.foto_path or not Path(tenant.foto_path).exists():
        # SVG neutro para perfiles sin foto
        return Response(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
            '<rect width="96" height="96" fill="#f3f3f5"/>'
            '<circle cx="48" cy="38" r="16" fill="#c9c9cf"/>'
            '<path d="M16 88c4-18 18-26 32-26s28 8 32 26" fill="#c9c9cf"/></svg>',
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=300"},
        )
    tipo = "image/png" if tenant.foto_path.endswith(".png") else "image/jpeg"
    return FileResponse(tenant.foto_path, media_type=tipo,
                        headers={"Cache-Control": "public, max-age=300"})


@router.post("/r/{token}/valorar")
def valorar(
    request: Request,
    token: str,
    puntuacion: int = Form(...),
    comentario: str = Form(""),
    db: Session = Depends(get_db),
):
    limitar_por_ip(request)
    reserva = reserva_por_token(db, token)
    if reserva is None:
        return HTMLResponse("<h1>Reserva no encontrada</h1>", status_code=404)
    puede = (
        reserva.estado == "completada"
        and 1 <= puntuacion <= 5
        and db.execute(
            select(Valoracion.id).where(Valoracion.reserva_id == reserva.id)
        ).first() is None
    )
    if puede:
        db.add(
            Valoracion(
                reserva_id=reserva.id,
                tenant_id=reserva.tenant_id,
                puntuacion=puntuacion,
                comentario=comentario.strip()[:400],
                autor=reserva.cliente.nombre.split()[0] if reserva.cliente.nombre else "",
            )
        )
        db.commit()
    return RedirectResponse(f"/r/{token}", status_code=303)
