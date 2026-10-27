"""Informe consolidado del período en PDF (ReportLab)."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import fmt_monto, fmt_pct, nombre_periodo

_ESTILOS = getSampleStyleSheet()
_CHICO = _ESTILOS["BodyText"].clone("chico", fontSize=7, leading=8.5)


def _tabla(filas, anchos, montos_desde=None, total_final=False, fuente=8):
    t = Table(filas, colWidths=anchos, repeatRows=1)
    estilo = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), fuente),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE5F0")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9AA5B1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if montos_desde is not None:
        estilo.append(("ALIGN", (montos_desde, 1), (-1, -1), "RIGHT"))
    if total_final:
        estilo += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                   ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F4F8"))]
    t.setStyle(TableStyle(estilo))
    return t


def _p(texto, estilo=_CHICO):
    return Paragraph(escape(str(texto or "")), estilo)


def generar_pdf(resumen: dict, detalle: list[dict]) -> bytes:
    salida = BytesIO()
    doc = SimpleDocTemplate(salida, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"Informe de facturas {resumen['periodo']}")
    h1, h2, normal = _ESTILOS["Title"], _ESTILOS["Heading2"], _ESTILOS["BodyText"]
    e = [Paragraph(f"Informe mensual de facturas — {nombre_periodo(resumen['periodo'])}", h1),
         Paragraph(f"Período {resumen['periodo']} · Estado: <b>{resumen['estado']}</b> · "
                   f"Generado el {datetime.now():%d/%m/%Y %H:%M}", normal)]
    integ = resumen.get("integridad") or {}
    if integ.get("cerrado"):
        e.append(Paragraph(
            f"Cierre versión {integ['version']} del {integ['fecha_cierre']} · hash SHA-256 "
            f"<font size=7>{integ['hash_cierre']}</font> · "
            + ("<b>datos íntegros</b>" if integ["integro"] else "<b><font color='red'>LOS DATOS DIFIEREN DEL CIERRE</font></b>"),
            normal))
    e.append(Paragraph(f"Comprobantes: <b>{resumen['cantidad']}</b> · Pendientes de revisión: "
                       f"<b>{resumen['pendientes']}</b>", normal))
    if not resumen["por_moneda"]:
        e.append(Paragraph("No hay comprobantes registrados en el período.", normal))

    for m in resumen["por_moneda"]:
        e.append(Spacer(1, 4 * mm))
        e.append(Paragraph(f"Totales en {m['moneda']}", h2))
        filas = [["Concepto", "Importe"],
                 ["Cantidad de comprobantes", str(m["cantidad"])],
                 ["Neto gravado", fmt_monto(m["neto_gravado"])],
                 ["No gravado / sin IVA discriminado", fmt_monto(m["no_gravado"])],
                 ["Exento", fmt_monto(m["exento"])],
                 ["IVA total", fmt_monto(m["iva_total"])],
                 ["Otros tributos", fmt_monto(m["otros_tributos"])],
                 ["Descuentos informados", fmt_monto(m["descuentos"])],
                 ["% de descuento sobre importes bonificados", fmt_pct(m["descuento_porcentaje"])
                  if m["descuento_porcentaje"] is not None else "No calculable"],
                 ["Total B/C (sin IVA discriminado)", fmt_monto(m["sin_iva_discriminado"])],
                 ["TOTAL DE COMPROBANTES", fmt_monto(m["total"])]]
        iva = [["Alícuota", "Base imponible", "IVA"]] + [
            [fmt_pct(a["alicuota"]), fmt_monto(a["base"]), fmt_monto(a["importe"])] for a in m["iva_por_alicuota"]
        ] + [["Total IVA", "", fmt_monto(m["iva_total"])]]
        bloque = Table([[_tabla(filas, [75 * mm, 35 * mm], 1, True),
                         _tabla(iva, [25 * mm, 35 * mm, 30 * mm], 1, True)]],
                       colWidths=[115 * mm, 95 * mm])
        bloque.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        e.append(bloque)
        if m["tributos_por_tipo"]:
            e.append(Spacer(1, 3 * mm))
            e.append(_tabla([["Otros tributos", "Importe"]] +
                            [[t["descripcion"], fmt_monto(t["importe"])] for t in m["tributos_por_tipo"]],
                            [75 * mm, 35 * mm], 1))

        e.append(Spacer(1, 3 * mm))
        prov = [["CUIT", "Proveedor", "Cant.", "Neto gravado", "IVA", "Otros trib.", "Total"]] + [
            [p["cuit"] or "", _p(p["razon_social"]), str(p["cantidad"]), fmt_monto(p["neto_gravado"]),
             fmt_monto(p["iva_total"]), fmt_monto(p["otros_tributos"]), fmt_monto(p["total"])]
            for p in m["por_proveedor"]]
        tipos = [["Tipo", "Cant.", "Neto gravado", "IVA", "Total"]] + [
            [t["tipo"], str(t["cantidad"]), fmt_monto(t["neto_gravado"]), fmt_monto(t["iva_total"]),
             fmt_monto(t["total"])] for t in m["por_tipo"]]
        e.append(KeepTogether([Paragraph("Totales por proveedor", _ESTILOS["Heading3"]),
                               _tabla(prov, [26 * mm, 80 * mm, 12 * mm, 30 * mm, 28 * mm, 25 * mm, 30 * mm], 2)]))
        e.append(KeepTogether([Paragraph("Totales por tipo de comprobante", _ESTILOS["Heading3"]),
                               _tabla(tipos, [60 * mm, 12 * mm, 30 * mm, 28 * mm, 30 * mm], 1)]))

    if detalle:
        e.append(Spacer(1, 4 * mm))
        e.append(Paragraph("Detalle de comprobantes", h2))
        filas = [["Fecha", "Tipo", "Comprobante", "CUIT", "Proveedor", "Mon.", "Neto grav.", "No grav./Ex.",
                  "Alíc.", "IVA", "Otros trib.", "Desc.", "Total", "Estado"]]
        for f in detalle:
            d, s = f["datos"], f["firmado"]
            filas.append([
                d["fecha_emision"], _p(d["descripcion_tipo"]), d["comprobante"], f["emisor_cuit_formateado"] or "",
                _p(d["emisor_razon_social"]), d["moneda"] or "", fmt_monto(s["neto_gravado"]),
                fmt_monto(s["no_gravado_exento"]), " / ".join(f"{a['alicuota']}%" for a in d["iva"]),
                fmt_monto(s["iva_total"]), fmt_monto(s["otros_tributos"]), fmt_monto(s["descuento_total"]),
                fmt_monto(s["total"]), f["estado_revision"]])
        e.append(_tabla(filas, [17 * mm, 22 * mm, 24 * mm, 22 * mm, 47 * mm, 10 * mm, 21 * mm, 21 * mm,
                                13 * mm, 19 * mm, 17 * mm, 15 * mm, 21 * mm, 18 * mm], 6, fuente=7))

    def pie(canvas, documento):
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, f"Página {documento.page}")

    doc.build(e, onFirstPage=pie, onLaterPages=pie)
    return salida.getvalue()
