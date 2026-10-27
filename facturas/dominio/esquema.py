"""Estructura canónica de los datos de una factura.

La misma estructura viaja entre el extractor, la vista previa editable, la API
y la base de datos. Los importes se representan con Decimal; en JSON viajan
como texto con punto decimal ("160000.00") para no perder precisión.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ..errores import ErrorValidacion
from . import comprobantes as cat
from .normalizar import normalizar_cuit, redondear

MAX_IMPORTE = Decimal("9999999999999.99")
_RE_PERIODO = re.compile(r"^(\d{4})-(\d{2})$")
_RE_MONEDA = re.compile(r"^[A-Z]{3}$")


@dataclass
class LineaIva:
    alicuota: Decimal
    importe: Decimal
    base: Decimal | None = None


@dataclass
class Descuento:
    importe: Decimal
    descripcion: str | None = None
    porcentaje: Decimal | None = None
    base: Decimal | None = None
    origen: str = "MANUAL"


@dataclass
class Tributo:
    tipo: str
    importe: Decimal
    descripcion: str | None = None
    alicuota: Decimal | None = None
    base: Decimal | None = None


@dataclass
class DatosFactura:
    fecha_emision: date | None = None
    periodo: str | None = None  # "AAAA-MM"; por defecto, el mes de emisión
    tipo_codigo: int | None = None
    tipo_letra: str | None = None
    tipo_clase: str | None = None
    punto_venta: int | None = None
    numero: int | None = None
    cae: str | None = None
    cae_vencimiento: date | None = None
    emisor_cuit: str | None = None
    emisor_razon_social: str | None = None
    emisor_domicilio: str | None = None
    emisor_localidad: str | None = None
    emisor_provincia: str | None = None
    emisor_condicion_iva: str | None = None
    moneda: str | None = "ARS"
    cotizacion: Decimal | None = None
    condicion_venta: str | None = None
    neto_gravado: Decimal | None = None
    no_gravado: Decimal | None = None
    exento: Decimal | None = None
    total: Decimal | None = None
    iva: list[LineaIva] = field(default_factory=list)
    descuentos: list[Descuento] = field(default_factory=list)
    tributos: list[Tributo] = field(default_factory=list)

    # --- valores derivados (no se almacenan, se calculan) ---
    @property
    def iva_total(self) -> Decimal:
        return sum((l.importe for l in self.iva), Decimal("0.00"))

    @property
    def otros_tributos_total(self) -> Decimal:
        return sum((t.importe for t in self.tributos), Decimal("0.00"))

    @property
    def descuento_total(self) -> Decimal:
        return sum((d.importe for d in self.descuentos), Decimal("0.00"))

    @property
    def descuento_porcentaje(self) -> Decimal | None:
        """Sólo se calcula si todas las bonificaciones tienen base conocida."""
        if not self.descuentos:
            return None
        if len(self.descuentos) == 1 and self.descuentos[0].porcentaje is not None:
            return self.descuentos[0].porcentaje
        if any(d.base is None or d.base <= 0 for d in self.descuentos):
            return None
        base = sum((d.base for d in self.descuentos), Decimal("0"))
        return redondear(self.descuento_total * 100 / base)

    @property
    def discrimina_iva(self) -> bool:
        return cat.discrimina_iva(self.tipo_letra)

    @property
    def signo(self) -> int:
        return cat.signo(self.tipo_clase)

    @property
    def periodo_efectivo(self) -> str | None:
        if self.periodo:
            return self.periodo
        if self.fecha_emision:
            return f"{self.fecha_emision.year:04d}-{self.fecha_emision.month:02d}"
        return None

    @property
    def descripcion_tipo(self) -> str:
        return cat.descripcion_tipo(self.tipo_letra, self.tipo_clase)

    @property
    def comprobante(self) -> str:
        pv = f"{self.punto_venta:05d}" if self.punto_venta is not None else "?????"
        nro = f"{self.numero:08d}" if self.numero is not None else "????????"
        return f"{pv}-{nro}"


# ----------------------------------------------------------------------------
# Conversión desde / hacia diccionarios JSON (con validación estricta de tipos)
# ----------------------------------------------------------------------------

class _Lector:
    def __init__(self):
        self.errores: dict[str, str] = {}

    def texto(self, d, campo, largo, clave=None):
        v = d.get(campo)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if not isinstance(v, str):
            self.errores[clave or campo] = "Debe ser texto"
            return None
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) > largo:
            self.errores[clave or campo] = f"Máximo {largo} caracteres"
            return None
        return v

    def entero(self, d, campo, minimo, maximo, clave=None):
        v = d.get(campo)
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            self.errores[clave or campo] = "Debe ser un número entero"
            return None
        try:
            n = int(str(v).strip())
        except ValueError:
            self.errores[clave or campo] = "Debe ser un número entero"
            return None
        if not minimo <= n <= maximo:
            self.errores[clave or campo] = f"Debe estar entre {minimo} y {maximo}"
            return None
        return n

    def decimal(self, d, campo, decimales=2, clave=None, minimo=Decimal("0"),
                maximo=MAX_IMPORTE):
        v = d.get(campo)
        if v is None or v == "":
            return None
        if isinstance(v, bool) or not isinstance(v, (str, int, float, Decimal)):
            self.errores[clave or campo] = "Debe ser un número"
            return None
        try:
            n = Decimal(str(v).strip())
        except InvalidOperation:
            self.errores[clave or campo] = "Número inválido (use punto decimal)"
            return None
        if not n.is_finite():
            self.errores[clave or campo] = "Número inválido"
            return None
        if n != n.quantize(Decimal(1).scaleb(-decimales)):
            self.errores[clave or campo] = f"Máximo {decimales} decimales"
            return None
        if n < minimo or n > maximo:
            self.errores[clave or campo] = f"Debe estar entre {minimo} y {maximo}"
            return None
        return n.quantize(Decimal(1).scaleb(-decimales))

    def fecha(self, d, campo, clave=None):
        v = d.get(campo)
        if v is None or v == "":
            return None
        try:
            return date.fromisoformat(str(v))
        except ValueError:
            self.errores[clave or campo] = "Fecha inválida (AAAA-MM-DD)"
            return None

    def lista(self, d, campo, maximo):
        v = d.get(campo) or []
        if not isinstance(v, list):
            self.errores[campo] = "Debe ser una lista"
            return []
        if len(v) > maximo:
            self.errores[campo] = f"Máximo {maximo} elementos"
            return []
        for i, item in enumerate(v):
            if not isinstance(item, dict):
                self.errores[f"{campo}.{i}"] = "Elemento inválido"
                return []
        return v


def desde_dict(d: dict) -> DatosFactura:
    """Valida y convierte un diccionario (JSON) en DatosFactura.

    Lanza ErrorValidacion si algún valor está mal formado. No evalúa la
    coherencia contable: eso lo hace ``calculos.validar``.
    """
    if not isinstance(d, dict):
        raise ErrorValidacion({"datos": "Se esperaba un objeto"})
    r = _Lector()
    f = DatosFactura()
    f.fecha_emision = r.fecha(d, "fecha_emision")
    f.cae_vencimiento = r.fecha(d, "cae_vencimiento")

    periodo = r.texto(d, "periodo", 7)
    if periodo:
        m = _RE_PERIODO.match(periodo)
        if not m or not 1 <= int(m.group(2)) <= 12:
            r.errores["periodo"] = "Período inválido (AAAA-MM)"
        else:
            f.periodo = periodo

    f.tipo_codigo = r.entero(d, "tipo_codigo", 1, 999)
    if f.tipo_codigo is not None and f.tipo_codigo not in cat.TIPOS:
        r.errores["tipo_codigo"] = "Código de comprobante desconocido"
        f.tipo_codigo = None
    letra = r.texto(d, "tipo_letra", 1)
    if letra:
        letra = letra.upper()
        if letra not in cat.LETRAS:
            r.errores["tipo_letra"] = "Letra de comprobante inválida"
            letra = None
    clase = r.texto(d, "tipo_clase", 20)
    if clase and clase not in cat.CLASES:
        r.errores["tipo_clase"] = "Clase de comprobante inválida"
        clase = None
    if f.tipo_codigo is not None:
        l_cod, c_cod = cat.letra_y_clase(f.tipo_codigo)
        if letra and letra != l_cod:
            r.errores["tipo_letra"] = f"La letra no coincide con el código {f.tipo_codigo} ({l_cod})"
        if clase and clase != c_cod:
            r.errores["tipo_clase"] = f"La clase no coincide con el código {f.tipo_codigo}"
        letra, clase = l_cod, c_cod
    elif letra and clase:
        f.tipo_codigo = cat.codigo_desde(letra, clase)
    f.tipo_letra, f.tipo_clase = letra, clase

    f.punto_venta = r.entero(d, "punto_venta", 0, 99999)
    f.numero = r.entero(d, "numero", 0, 99999999)
    cae = r.texto(d, "cae", 14)
    if cae and not re.fullmatch(r"\d{14}", cae):
        r.errores["cae"] = "El CAE debe tener 14 dígitos"
    else:
        f.cae = cae

    cuit = r.texto(d, "emisor_cuit", 13)
    if cuit:
        f.emisor_cuit = normalizar_cuit(cuit)
        if not f.emisor_cuit:
            r.errores["emisor_cuit"] = "El CUIT debe tener 11 dígitos"
    f.emisor_razon_social = r.texto(d, "emisor_razon_social", 200)
    f.emisor_domicilio = r.texto(d, "emisor_domicilio", 300)
    f.emisor_localidad = r.texto(d, "emisor_localidad", 120)
    prov = r.texto(d, "emisor_provincia", 60)
    f.emisor_provincia = cat.provincia_canonica(prov) or prov
    f.emisor_condicion_iva = r.texto(d, "emisor_condicion_iva", 60)
    moneda = r.texto(d, "moneda", 3)
    if moneda:
        moneda = moneda.upper()
        if not _RE_MONEDA.match(moneda):
            r.errores["moneda"] = "Código de moneda inválido (ej.: ARS, USD)"
        f.moneda = moneda
    else:
        f.moneda = None
    f.cotizacion = r.decimal(d, "cotizacion", decimales=6, minimo=Decimal("0.000001"),
                             maximo=Decimal("99999999"))
    f.condicion_venta = r.texto(d, "condicion_venta", 100)

    for campo in ("neto_gravado", "no_gravado", "exento", "total"):
        setattr(f, campo, r.decimal(d, campo))

    for i, item in enumerate(r.lista(d, "iva", 10)):
        alic = r.decimal(item, "alicuota", clave=f"iva.{i}.alicuota", maximo=Decimal("100"))
        imp = r.decimal(item, "importe", clave=f"iva.{i}.importe")
        base = r.decimal(item, "base", clave=f"iva.{i}.base")
        if alic is None:
            r.errores.setdefault(f"iva.{i}.alicuota", "Falta la alícuota")
        if imp is None:
            r.errores.setdefault(f"iva.{i}.importe", "Falta el importe")
        if alic is not None and imp is not None:
            if any(l.alicuota == alic for l in f.iva):
                r.errores[f"iva.{i}.alicuota"] = "Alícuota repetida"
            f.iva.append(LineaIva(alicuota=alic, importe=imp, base=base))

    for i, item in enumerate(r.lista(d, "descuentos", 200)):
        imp = r.decimal(item, "importe", clave=f"descuentos.{i}.importe")
        if imp is None:
            r.errores.setdefault(f"descuentos.{i}.importe", "Falta el importe")
            continue
        origen = r.texto(item, "origen", 10, clave=f"descuentos.{i}.origen") or "MANUAL"
        if origen not in cat.ORIGENES_DESCUENTO:
            r.errores[f"descuentos.{i}.origen"] = "Origen inválido"
        f.descuentos.append(Descuento(
            importe=imp,
            descripcion=r.texto(item, "descripcion", 200, clave=f"descuentos.{i}.descripcion"),
            porcentaje=r.decimal(item, "porcentaje", decimales=4, clave=f"descuentos.{i}.porcentaje",
                                 maximo=Decimal("100")),
            base=r.decimal(item, "base", clave=f"descuentos.{i}.base"),
            origen=origen,
        ))

    for i, item in enumerate(r.lista(d, "tributos", 50)):
        imp = r.decimal(item, "importe", clave=f"tributos.{i}.importe")
        tipo = r.texto(item, "tipo", 30, clave=f"tributos.{i}.tipo") or "OTRO"
        if tipo not in cat.TIPOS_TRIBUTO:
            r.errores[f"tributos.{i}.tipo"] = "Tipo de tributo inválido"
        if imp is None:
            r.errores.setdefault(f"tributos.{i}.importe", "Falta el importe")
            continue
        f.tributos.append(Tributo(
            tipo=tipo, importe=imp,
            descripcion=r.texto(item, "descripcion", 200, clave=f"tributos.{i}.descripcion"),
            alicuota=r.decimal(item, "alicuota", decimales=4, clave=f"tributos.{i}.alicuota",
                               maximo=Decimal("100")),
            base=r.decimal(item, "base", clave=f"tributos.{i}.base"),
        ))

    if r.errores:
        raise ErrorValidacion(r.errores)
    return f


def _s(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def a_dict(f: DatosFactura) -> dict:
    return {
        "fecha_emision": _s(f.fecha_emision),
        "periodo": f.periodo_efectivo,
        "tipo_codigo": f.tipo_codigo,
        "tipo_letra": f.tipo_letra,
        "tipo_clase": f.tipo_clase,
        "punto_venta": f.punto_venta,
        "numero": f.numero,
        "cae": f.cae,
        "cae_vencimiento": _s(f.cae_vencimiento),
        "emisor_cuit": f.emisor_cuit,
        "emisor_razon_social": f.emisor_razon_social,
        "emisor_domicilio": f.emisor_domicilio,
        "emisor_localidad": f.emisor_localidad,
        "emisor_provincia": f.emisor_provincia,
        "emisor_condicion_iva": f.emisor_condicion_iva,
        "moneda": f.moneda,
        "cotizacion": _s(f.cotizacion),
        "condicion_venta": f.condicion_venta,
        "neto_gravado": _s(f.neto_gravado),
        "no_gravado": _s(f.no_gravado),
        "exento": _s(f.exento),
        "total": _s(f.total),
        "iva": [{"alicuota": _s(l.alicuota), "importe": _s(l.importe), "base": _s(l.base)}
                for l in f.iva],
        "descuentos": [{"importe": _s(x.importe), "descripcion": x.descripcion,
                        "porcentaje": _s(x.porcentaje), "base": _s(x.base), "origen": x.origen}
                       for x in f.descuentos],
        "tributos": [{"tipo": t.tipo, "importe": _s(t.importe), "descripcion": t.descripcion,
                      "alicuota": _s(t.alicuota), "base": _s(t.base)} for t in f.tributos],
        # derivados, sólo informativos
        "iva_total": _s(f.iva_total),
        "otros_tributos_total": _s(f.otros_tributos_total),
        "descuento_total": _s(f.descuento_total),
        "descuento_porcentaje": _s(f.descuento_porcentaje),
        "descripcion_tipo": f.descripcion_tipo,
        "comprobante": f.comprobante,
    }
