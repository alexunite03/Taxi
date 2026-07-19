"""Hoja de ruta en PDF para el taxista (se adjunta al email y a Telegram).

Generada con fpdf2 (Python puro, sin dependencias de sistema): funciona en
cualquier PaaS. Fuentes básicas → sin emojis y con «EUR» en vez de «€».
"""
from __future__ import annotations

from fpdf import FPDF

_ROJO = (200, 16, 46)  # rojo taxi de Madrid
_GRIS = (100, 100, 105)


def _fila(pdf: FPDF, etiqueta: str, valor: str) -> None:
    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(*_GRIS)
    pdf.cell(42, 8, etiqueta)
    pdf.set_font("helvetica", "", 11)
    pdf.set_text_color(20, 20, 25)
    pdf.multi_cell(0, 8, valor, new_x="LMARGIN", new_y="NEXT")


def pdf_hoja_de_ruta(solicitud, reserva=None) -> bytes:
    """PDF de una solicitud (pendiente) o de la reserva ya confirmada."""
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("helvetica", "B", 18)
    pdf.set_text_color(*_ROJO)
    pdf.cell(0, 10, "Hoja de ruta", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(*_GRIS)
    estado = "RESERVA CONFIRMADA" if reserva is not None else "PENDIENTE DE ACEPTAR"
    pdf.cell(0, 6, f"TaxiMad - {estado}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_ROJO)
    pdf.set_line_width(0.8)
    pdf.line(10, pdf.get_y() + 2, 200, pdf.get_y() + 2)
    pdf.ln(8)

    _fila(pdf, "Recogida", solicitud.fecha_hora_recogida.strftime("%d/%m/%Y a las %H:%M"))
    _fila(pdf, "Origen", solicitud.origen_texto)
    _fila(pdf, "Destino", solicitud.destino_texto)
    _fila(pdf, "Pasajero", f"{solicitud.nombre} - Tel. {solicitud.telefono}")
    if solicitud.intermediario is not None:
        _fila(pdf, "Pedido por", solicitud.intermediario.nombre)

    pdf.ln(4)
    if reserva is not None:
        _fila(pdf, "Precio cerrado", f"{reserva.precio_cerrado} EUR (IVA incluido)")
        j = reserva.justificante
        if j is not None:
            _fila(pdf, "Justificante", f"{j.serie}-{j.numero:06d}")
        from app.config import settings

        _fila(pdf, "Enlace", f"{settings.base_url}/r/{reserva.token_publico}")
    else:
        _fila(pdf, "Precio máximo", f"{solicitud.precio_estimado} EUR (IVA incluido)")
        pdf.ln(2)
        pdf.set_font("helvetica", "I", 9)
        pdf.set_text_color(*_GRIS)
        pdf.multi_cell(0, 5, "El pasajero ha visto este precio como máximo. "
                             "Al aceptar puedes confirmarlo o mejorarlo con un descuento.",
                       new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("helvetica", "I", 8)
    pdf.set_text_color(*_GRIS)
    pdf.multi_cell(0, 4, "Precio calculado con las tarifas oficiales del taxi de Madrid "
                         "(precio cerrado, BOCM). El pasajero paga el menor entre este "
                         "importe y el del taxímetro.",
                   new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
