"""Exportación del período a Excel (.xlsx)."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import dec, nombre_periodo

FORMATO_MONTO = '#,##0.00;[Red]-#,##0.00'
_NEGRITA = Font(bold=True)
_ENCABEZADO = PatternFill("solid", fgColor="DDE5F0")


def _encabezado(ws, fila, titulos):
    for col, titulo in enumerate(titulos, 1):
        c = ws.cell(row=fila, column=col, value=titulo)
        c.font = _NEGRITA
        c.fill = _ENCABEZADO
        c.alignment = Alignment(wrap_text=True, vertical="center")


def _fila(ws, fila, valores, montos=(), negrita=False):
    for col, valor in enumerate(valores, 1):
        c = ws.cell(row=fila, column=col, value=valor)
        if col in montos:
            c.number_format = FORMATO_MONTO
        if negrita:
            c.font = _NEGRITA


def _anchos(ws, anchos):
    for i, ancho in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = ancho


def generar_excel(resumen: dict, detalle: list[dict]) -> bytes:
    wb = Workbook()

    # ---------------------------------------------------------------- Resumen
    ws = wb.active
    ws.title = "Resumen"
    ws["A1"] = f"Resumen de facturas — {nombre_periodo(resumen['periodo'])}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Período {resumen['periodo']} · Estado: {resumen['estado']} · " \
               f"Generado: {datetime.now():%d/%m/%Y %H:%M}"
    integ = resumen.get("integridad") or {}
    if integ.get("cerrado"):
        ws["A3"] = (f"Cierre versión {integ['version']} ({integ['fecha_cierre']}) · hash {integ['hash_cierre']} · "
                    + ("datos íntegros" if integ["integro"] else "¡LOS DATOS DIFIEREN DEL CIERRE!"))
    ws["A4"] = f"Comprobantes: {resumen['cantidad']} · Pendientes de revisión: {resumen['pendientes']}"
    fila = 6
    for m in resumen["por_moneda"]:
        ws.cell(row=fila, column=1, value=f"Moneda: {m['moneda']}").font = Font(bold=True, size=12)
        fila += 1
        conceptos = [
            ("Cantidad de comprobantes", m["cantidad"]),
            ("Neto gravado", dec(m["neto_gravado"])),
            ("No gravado / sin IVA discriminado", dec(m["no_gravado"])),
            ("Exento", dec(m["exento"])),
            ("IVA total", dec(m["iva_total"])),
            ("Otros tributos", dec(m["otros_tributos"])),
            ("Total de comprobantes", dec(m["total"])),
            ("Descuentos informados", dec(m["descuentos"])),
            ("% de descuento sobre importes bonificados",
             dec(m["descuento_porcentaje"]) if m["descuento_porcentaje"] is not None else "No calculable"),
            ("Total de comprobantes B/C (sin IVA discriminado)", dec(m["sin_iva_discriminado"])),
        ]
        for concepto, valor in conceptos:
            _fila(ws, fila, [concepto, valor], montos=(2,) if concepto != "Cantidad de comprobantes" else ())
            fila += 1
        fila += 1
        ws.cell(row=fila, column=1, value="IVA por alícuota").font = _NEGRITA
        fila += 1
        _encabezado(ws, fila, ["Alícuota %", "Base imponible", "IVA"])
        fila += 1
        for a in m["iva_por_alicuota"]:
            _fila(ws, fila, [dec(a["alicuota"]), dec(a["base"]), dec(a["importe"])], montos=(1, 2, 3))
            fila += 1
        _fila(ws, fila, ["Total IVA", None, dec(m["iva_total"])], montos=(3,), negrita=True)
        fila += 2
        if m["tributos_por_tipo"]:
            ws.cell(row=fila, column=1, value="Otros tributos").font = _NEGRITA
            fila += 1
            _encabezado(ws, fila, ["Tributo", "Importe"])
            fila += 1
            for t in m["tributos_por_tipo"]:
                _fila(ws, fila, [t["descripcion"], dec(t["importe"])], montos=(2,))
                fila += 1
            fila += 1
    _anchos(ws, [48, 20, 20])

    # ---------------------------------------------------------------- Comprobantes
    ws = wb.create_sheet("Comprobantes")
    titulos = ["Fecha", "Tipo", "Comprobante", "CUIT emisor", "Proveedor", "Localidad", "Provincia",
               "Moneda", "Neto gravado", "No gravado / Exento", "Alícuotas IVA", "IVA", "Otros tributos",
               "% Desc.", "Descuentos", "Total", "Estado", "CAE", "ID"]
    _encabezado(ws, 1, titulos)
    fila = 2
    for f in detalle:
        d, s = f["datos"], f["firmado"]
        alicuotas = " / ".join(f"{a['alicuota']}%" for a in d["iva"]) or ("No discrimina" if d["tipo_letra"] in ("B", "C") else "")
        _fila(ws, fila, [
            d["fecha_emision"], d["descripcion_tipo"], d["comprobante"], f["emisor_cuit_formateado"],
            d["emisor_razon_social"], d["emisor_localidad"], d["emisor_provincia"], d["moneda"],
            dec(s["neto_gravado"]), dec(s["no_gravado_exento"]), alicuotas, dec(s["iva_total"]),
            dec(s["otros_tributos"]), dec(d["descuento_porcentaje"]), dec(s["descuento_total"]),
            dec(s["total"]), f["estado_revision"], d["cae"], f["id"],
        ], montos=(9, 10, 12, 13, 14, 15, 16))
        fila += 1
    fila += 1
    for m in resumen["por_moneda"]:
        _fila(ws, fila, [f"TOTAL {m['moneda']}", None, None, None, f"{m['cantidad']} comprobante(s)", None, None,
                         m["moneda"], dec(m["neto_gravado"]), dec(m["no_gravado"]) + dec(m["exento"]), None,
                         dec(m["iva_total"]), dec(m["otros_tributos"]), None, dec(m["descuentos"]),
                         dec(m["total"])], montos=(9, 10, 12, 13, 15, 16), negrita=True)
        fila += 1
    _anchos(ws, [11, 16, 16, 15, 34, 20, 16, 8, 15, 15, 14, 14, 13, 8, 13, 15, 12, 16, 6])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(titulos))}{max(1, len(detalle) + 1)}"

    # ---------------------------------------------------------------- Por proveedor / tipo / IVA
    ws = wb.create_sheet("Por proveedor")
    _encabezado(ws, 1, ["Moneda", "CUIT", "Proveedor", "Cantidad", "Neto gravado", "No gravado",
                        "Exento", "IVA", "Otros tributos", "Descuentos", "Total"])
    fila = 2
    for m in resumen["por_moneda"]:
        for p in m["por_proveedor"]:
            _fila(ws, fila, [m["moneda"], p["cuit"], p["razon_social"], p["cantidad"], dec(p["neto_gravado"]),
                             dec(p["no_gravado"]), dec(p["exento"]), dec(p["iva_total"]),
                             dec(p["otros_tributos"]), dec(p["descuentos"]), dec(p["total"])],
                  montos=(5, 6, 7, 8, 9, 10, 11))
            fila += 1
    _anchos(ws, [8, 14, 36, 9, 15, 15, 13, 14, 13, 13, 15])

    ws = wb.create_sheet("Por tipo")
    _encabezado(ws, 1, ["Moneda", "Tipo de comprobante", "Cantidad", "Neto gravado", "No gravado",
                        "Exento", "IVA", "Otros tributos", "Descuentos", "Total"])
    fila = 2
    for m in resumen["por_moneda"]:
        for t in m["por_tipo"]:
            _fila(ws, fila, [m["moneda"], t["tipo"], t["cantidad"], dec(t["neto_gravado"]),
                             dec(t["no_gravado"]), dec(t["exento"]), dec(t["iva_total"]),
                             dec(t["otros_tributos"]), dec(t["descuentos"]), dec(t["total"])],
                  montos=(4, 5, 6, 7, 8, 9, 10))
            fila += 1
    _anchos(ws, [8, 30, 9, 15, 15, 13, 14, 13, 13, 15])

    salida = BytesIO()
    wb.save(salida)
    return salida.getvalue()
