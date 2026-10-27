"""Validación contable de facturas y consolidación de períodos.

Todo con Decimal. Las notas de crédito restan. Nunca se suman monedas distintas.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import comprobantes as cat
from .esquema import DatosFactura
from .normalizar import cuit_valido, formatear_cuit, redondear

# Diferencia máxima admitida por redondeos de los sistemas de facturación.
TOLERANCIA = Decimal("0.02")
CERO = Decimal("0.00")

ERROR = "error"
ADVERTENCIA = "advertencia"


@dataclass
class Problema:
    campo: str
    nivel: str
    mensaje: str

    def a_dict(self):
        return {"campo": self.campo, "nivel": self.nivel, "mensaje": self.mensaje}


def _m(v: Decimal | None) -> str:
    return "—" if v is None else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def validar(f: DatosFactura, *, cuit_propio: str | None = None, qr: dict | None = None,
            hoy: date | None = None) -> list[Problema]:
    """Devuelve la lista de problemas. ``error`` = la factura no puede quedar
    verificada; ``advertencia`` = requiere que el usuario la revise y acepte."""
    p: list[Problema] = []
    hoy = hoy or date.today()

    def err(campo, msg):
        p.append(Problema(campo, ERROR, msg))

    def adv(campo, msg):
        p.append(Problema(campo, ADVERTENCIA, msg))

    # --- campos obligatorios para una factura verificada ---
    if f.fecha_emision is None:
        err("fecha_emision", "Falta la fecha de emisión")
    if not f.tipo_letra:
        err("tipo_letra", "Falta el tipo de comprobante (A, B, C, M, E)")
    if not f.tipo_clase:
        err("tipo_clase", "Falta la clase de comprobante (factura, nota de crédito, ...)")
    if f.punto_venta is None:
        err("punto_venta", "Falta el punto de venta")
    if f.numero is None:
        err("numero", "Falta el número de comprobante")
    if not f.emisor_cuit:
        err("emisor_cuit", "Falta el CUIT del emisor")
    elif not cuit_valido(f.emisor_cuit):
        err("emisor_cuit", f"El CUIT {formatear_cuit(f.emisor_cuit)} no es válido (dígito verificador)")
    elif cuit_propio and f.emisor_cuit == cuit_propio:
        adv("emisor_cuit", "El CUIT del emisor coincide con el CUIT propio: ¿es una factura de venta?")
    if not f.emisor_razon_social:
        err("emisor_razon_social", "Falta la razón social del emisor")
    if not f.moneda:
        err("moneda", "Falta la moneda")
    if f.total is None:
        err("total", "Falta el importe total")

    # --- fechas ---
    if f.fecha_emision and f.fecha_emision > hoy:
        adv("fecha_emision", "La fecha de emisión es posterior a hoy")
    if f.fecha_emision and f.cae_vencimiento and f.cae_vencimiento < f.fecha_emision:
        adv("cae_vencimiento", "El vencimiento del CAE es anterior a la fecha de emisión")
    if f.periodo and f.fecha_emision:
        mes_emision = f"{f.fecha_emision.year:04d}-{f.fecha_emision.month:02d}"
        if f.periodo != mes_emision:
            adv("periodo", f"Se imputa al período {f.periodo} pero fue emitida en {mes_emision}")

    # --- moneda ---
    if f.moneda and f.moneda != "ARS" and f.cotizacion is None:
        adv("cotizacion", f"Comprobante en {f.moneda} sin cotización informada")

    # --- IVA ---
    alicuotas_validas = {a.quantize(Decimal("0.01")) for a in cat.ALICUOTAS_IVA}
    for i, linea in enumerate(f.iva):
        if linea.alicuota not in alicuotas_validas:
            err(f"iva.{i}.alicuota", f"Alícuota de IVA {linea.alicuota}% inexistente en Argentina")
        if linea.base is not None:
            esperado = redondear(linea.base * linea.alicuota / 100)
            if abs(esperado - linea.importe) > TOLERANCIA:
                err(f"iva.{i}.importe",
                    f"IVA {linea.alicuota}%: sobre una base de {_m(linea.base)} corresponde "
                    f"{_m(esperado)}, figura {_m(linea.importe)}")

    if f.tipo_letra:
        if f.discrimina_iva:
            if f.neto_gravado is None:
                err("neto_gravado", "Falta el importe neto gravado")
            elif f.neto_gravado > 0 and not f.iva:
                err("iva", f"Comprobante {f.tipo_letra} con neto gravado pero sin IVA discriminado")
            if f.iva and f.neto_gravado is not None:
                bases = [l.base for l in f.iva]
                if all(b is not None for b in bases):
                    suma_bases = sum(bases, CERO)
                    if abs(suma_bases - f.neto_gravado) > TOLERANCIA:
                        err("iva", f"La suma de bases de IVA ({_m(suma_bases)}) no coincide con "
                                   f"el neto gravado ({_m(f.neto_gravado)})")
        elif f.iva:
            adv("iva", f"Los comprobantes {f.tipo_letra} no discriminan IVA: revise las alícuotas cargadas")

    # --- descuentos ---
    for i, d in enumerate(f.descuentos):
        if d.importe <= 0:
            err(f"descuentos.{i}.importe", "El importe del descuento debe ser mayor a cero")
        if d.porcentaje is not None and d.base is not None:
            esperado = redondear(d.base * d.porcentaje / 100)
            if abs(esperado - d.importe) > TOLERANCIA:
                adv(f"descuentos.{i}.importe",
                    f"El {d.porcentaje}% de {_m(d.base)} es {_m(esperado)}, figura {_m(d.importe)}")
        if d.base is not None and d.importe > d.base:
            err(f"descuentos.{i}.importe", "El descuento supera su base de cálculo")

    # --- ecuación del comprobante ---
    if f.total is not None:
        componentes = ((f.neto_gravado or CERO) + (f.no_gravado or CERO) + (f.exento or CERO)
                       + f.iva_total + f.otros_tributos_total)
        if abs(componentes - f.total) > TOLERANCIA:
            err("total",
                f"Neto gravado + no gravado + exento + IVA + otros tributos = {_m(componentes)}, "
                f"pero el total es {_m(f.total)} (diferencia {_m(f.total - componentes)})")

    # --- coherencia con el QR fiscal ---
    if qr:
        comparaciones = (
            ("emisor_cuit", qr.get("cuit"), f.emisor_cuit, str),
            ("punto_venta", qr.get("punto_venta"), f.punto_venta, str),
            ("numero", qr.get("numero"), f.numero, str),
            ("tipo_codigo", qr.get("tipo_codigo"), f.tipo_codigo, str),
            ("fecha_emision", qr.get("fecha"), f.fecha_emision, str),
            ("total", qr.get("importe"), f.total, lambda v: str(redondear(Decimal(str(v))))),
            ("moneda", qr.get("moneda"), f.moneda, str),
        )
        for campo, valor_qr, valor, conv in comparaciones:
            if valor_qr is None or valor is None:
                continue
            if conv(valor_qr) != conv(valor):
                adv(campo, f"No coincide con el QR fiscal de ARCA (QR: {valor_qr})")
        receptor = qr.get("nro_doc_receptor")
        if cuit_propio and receptor and str(receptor) != cuit_propio:
            adv("receptor", f"El QR indica que el receptor es {receptor}, no el CUIT propio")

    return p


def tiene_errores(problemas: list[Problema]) -> bool:
    return any(x.nivel == ERROR for x in problemas)


def estado_revision(problemas: list[Problema], aceptar_advertencias: bool) -> str:
    if tiene_errores(problemas):
        return "PENDIENTE"
    if any(x.nivel == ADVERTENCIA for x in problemas) and not aceptar_advertencias:
        return "PENDIENTE"
    return "VERIFICADA"


# ----------------------------------------------------------------------------
# Consolidación
# ----------------------------------------------------------------------------

@dataclass
class RegistroResumen:
    id: int
    datos: DatosFactura
    estado: str


class _Acumulador:
    def __init__(self):
        self.cantidad = 0
        self.neto_gravado = CERO
        self.no_gravado = CERO
        self.exento = CERO
        self.iva = CERO
        self.otros_tributos = CERO
        self.descuentos = CERO
        self.total = CERO

    def sumar(self, f: DatosFactura):
        s = f.signo
        self.cantidad += 1
        self.neto_gravado += s * (f.neto_gravado or CERO)
        self.no_gravado += s * (f.no_gravado or CERO)
        self.exento += s * (f.exento or CERO)
        self.iva += s * f.iva_total
        self.otros_tributos += s * f.otros_tributos_total
        self.descuentos += s * f.descuento_total
        self.total += s * (f.total or CERO)

    def a_dict(self):
        return {"cantidad": self.cantidad, "neto_gravado": self.neto_gravado,
                "no_gravado": self.no_gravado, "exento": self.exento, "iva_total": self.iva,
                "otros_tributos": self.otros_tributos, "descuentos": self.descuentos,
                "total": self.total}


def consolidar(registros: list[RegistroResumen]) -> dict:
    """Totales del período, separados por moneda. Los importes quedan como Decimal."""
    por_moneda: dict[str, dict] = {}
    pendientes = 0
    sin_total = 0

    for r in registros:
        f = r.datos
        if r.estado != "VERIFICADA":
            pendientes += 1
        if f.total is None:
            sin_total += 1
        moneda = f.moneda or "SIN MONEDA"
        m = por_moneda.get(moneda)
        if m is None:
            m = por_moneda[moneda] = {
                "general": _Acumulador(),
                "iva": defaultdict(lambda: {"base": CERO, "importe": CERO, "base_completa": True}),
                "tributos": defaultdict(lambda: CERO),
                "desc_base": CERO, "desc_con_base": CERO, "desc_sin_base": 0,
                "sin_iva_discriminado": CERO,
                "proveedores": defaultdict(_Acumulador), "nombres": {},
                "tipos": defaultdict(_Acumulador),
            }
        s = f.signo
        m["general"].sumar(f)
        for linea in f.iva:
            a = m["iva"][linea.alicuota]
            a["importe"] += s * linea.importe
            if linea.base is None:
                a["base_completa"] = False
            else:
                a["base"] += s * linea.base
        for t in f.tributos:
            m["tributos"][t.tipo] += s * t.importe
        for d in f.descuentos:
            if d.base is None:
                m["desc_sin_base"] += 1
            else:
                m["desc_base"] += s * d.base
                m["desc_con_base"] += s * d.importe
        if not f.discrimina_iva:
            m["sin_iva_discriminado"] += s * (f.total or CERO)
        clave_prov = f.emisor_cuit or (f.emisor_razon_social or "(sin identificar)")
        m["proveedores"][clave_prov].sumar(f)
        m["nombres"][clave_prov] = (f.emisor_cuit, f.emisor_razon_social or "(sin identificar)")
        m["tipos"][f.descripcion_tipo].sumar(f)

    monedas = []
    for moneda in sorted(por_moneda, key=lambda x: (x != "ARS", x)):
        m = por_moneda[moneda]
        g = m["general"].a_dict()
        # % de descuento: sólo si todas las bonificaciones tienen base conocida.
        pct = None
        if m["desc_sin_base"] == 0 and m["desc_base"] > 0:
            pct = redondear(m["desc_con_base"] * 100 / m["desc_base"])
        g.update({
            "moneda": moneda,
            "iva_por_alicuota": [
                {"alicuota": alic, "base": v["base"] if v["base_completa"] else None,
                 "importe": v["importe"]}
                for alic, v in sorted(m["iva"].items())
            ],
            "tributos_por_tipo": [
                {"tipo": t, "descripcion": cat.TIPOS_TRIBUTO.get(t, t), "importe": v}
                for t, v in sorted(m["tributos"].items())
            ],
            "descuento_porcentaje": pct,
            "descuentos_sin_base": m["desc_sin_base"],
            "sin_iva_discriminado": m["sin_iva_discriminado"],
            "por_proveedor": sorted(
                ({"cuit": m["nombres"][k][0], "razon_social": m["nombres"][k][1], **acc.a_dict()}
                 for k, acc in m["proveedores"].items()),
                key=lambda x: -abs(x["total"])),
            "por_tipo": sorted(({"tipo": k, **acc.a_dict()} for k, acc in m["tipos"].items()),
                               key=lambda x: x["tipo"]),
        })
        monedas.append(g)

    return {"cantidad": len(registros), "pendientes": pendientes, "sin_total": sin_total,
            "por_moneda": monedas}
