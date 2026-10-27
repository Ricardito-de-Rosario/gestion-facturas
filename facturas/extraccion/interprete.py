"""Identificación de los campos de una factura argentina.

Fuentes, de mayor a menor confianza:
  1. QR fiscal de ARCA (datos de cabecera e importe total).
  2. Texto con etiqueta explícita ("Fecha de Emisión:", "Importe Total:", ...).
  3. Registro de proveedores ya cargados (mismo CUIT).
  4. Heurísticas de diseño (posición, sufijos societarios) -> confianza baja.
Lo que no se puede identificar queda en None: nunca se inventa un valor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from ..dominio import comprobantes as cat
from ..dominio.esquema import DatosFactura, Descuento, LineaIva, Tributo
from ..dominio.normalizar import (
    cuit_valido, limpiar_texto, normalizar_alicuota, normalizar_cuit, parse_fecha,
    parse_importe, redondear,
)
from .documento import OCR, PaginaExtraida
from .layout import Linea
from .qr_arca import DatosQR

# Separadores admitidos entre una etiqueta y su valor.
S = r"[\s‖$:]*"
NUM = r"(\(?-?\d[\d.,]*\d\)?|\d)(?![\d%\w])"
FECHA = r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})"
VALOR = r"([^‖]+)"

ALTA, MEDIA, BAJA = "alta", "media", "baja"
TOL_ITEM = Decimal("0.015")
TOL = Decimal("0.02")

_RX_RECEPTOR = re.compile(
    r"Se[ñn]or(?:\(?es\)?|/res|es)?\s*:|Apellido\s+y\s+Nombre|Cliente\s*:|Destinatario\s*:"
    r"|Raz[oó]n\s+Social\s*/", re.I)
_RX_ENCABEZADO_ITEMS = re.compile(r"\bCant(?:idad|\.)", re.I)
_RX_ENCABEZADO_ITEMS_2 = re.compile(r"Precio|P\.\s*Unit|Importe|Subtotal", re.I)
_RX_INICIO_TOTALES = re.compile(
    r"(?<!\w)(?:Sub\s*total\s*:|Importe\s+(?:Neto|Total|Otros|Exento)|Neto\s+Gravado|Neto\s*:"
    r"|TOTAL\s*:|Otros\s+Tributos|Total\s+a\s+pagar)", re.I)
_RX_CUIT = re.compile(r"(?<!\d)(\d{2})[-\s]?(\d{8})[-\s]?(\d)(?!\d)")
_RX_COPIA = re.compile(r"^(ORIGINAL|DUPLICADO|TRIPLICADO|CUADRUPLICADO)$", re.I)
_RX_SUFIJO_SOCIETARIO = re.compile(
    r"\b(S\.?\s?R\.?\s?L\.?|S\.?\s?A\.?\s?S\.?|S\.?\s?A\.?|S\.?\s?C\.?|S\.?\s?H\.?|S\.?\s?A\.?\s?U\.?"
    r"|COOP(?:ERATIVA)?\.?|LTDA\.?|E\s+HIJOS?)\s*$", re.I)
_RX_NO_NOMBRE = re.compile(
    r"^(ORIGINAL|DUPLICADO|TRIPLICADO|CUADRUPLICADO|[A-Z]|FACTURA.*|FAC\b.*|NOTA\s+DE.*|RECIBO.*"
    r"|COD.*|P[AÁ]G.*|HOJA.*|DOCUMENTO.*|COMPROBANTE.*|TIQUE.*)$", re.I)


@dataclass
class Campo:
    fuente: str  # qr | texto | registro | heuristica | calculado | defecto
    confianza: str  # alta | media | baja
    detalle: str | None = None

    def a_dict(self):
        return {"fuente": self.fuente, "confianza": self.confianza, "detalle": self.detalle}


@dataclass
class Resultado:
    datos: DatosFactura
    campos: dict[str, Campo] = field(default_factory=dict)
    advertencias: list[str] = field(default_factory=list)
    lineas: list[Linea] = field(default_factory=list)


def _bajar(confianza: str) -> str:
    return {ALTA: MEDIA, MEDIA: BAJA}.get(confianza, BAJA)


class Interprete:
    def __init__(self, paginas: list[PaginaExtraida], qr: DatosQR | None,
                 cuit_propio: str | None = None,
                 proveedor_conocido: Callable[[str], dict | None] | None = None):
        self.paginas = paginas
        self.qr = qr
        self.cuit_propio = cuit_propio
        self.proveedor_conocido = proveedor_conocido
        self.metodo = {p.numero: p.metodo for p in paginas}
        self.ancho = paginas[0].ancho if paginas else 600.0
        self.alto = paginas[0].alto if paginas else 850.0
        self.lineas = [ln for p in paginas for ln in p.lineas]
        self.r = Resultado(datos=DatosFactura(moneda=None), lineas=self.lineas)
        self._zonificar()

    # ------------------------------------------------------------------ utilidades
    def _campo(self, nombre, fuente, confianza, linea: Linea | None = None, detalle=None):
        if linea is not None and self.metodo.get(linea.pagina) == OCR:
            confianza = _bajar(confianza)
        self.r.campos[nombre] = Campo(fuente, confianza, detalle)

    def _adv(self, mensaje):
        if mensaje not in self.r.advertencias:
            self.r.advertencias.append(mensaje)

    @staticmethod
    def _buscar(lineas, patron, ultimo=False):
        rx = re.compile(patron, re.I)
        hallado = None
        for ln in lineas:
            for m in rx.finditer(ln.texto):
                if not ultimo:
                    return m, ln
                hallado = (m, ln)
        return hallado

    def _importe(self, lineas, patrones, ultimo=True):
        for patron in patrones:
            h = self._buscar(lineas, patron, ultimo=ultimo)
            if h:
                valor = parse_importe(h[0].group(h[0].lastindex))
                if valor is not None:
                    return valor, h[1]
        return None, None

    # ------------------------------------------------------------------ zonas
    def _zonificar(self):
        primera = [ln for ln in self.lineas if self.paginas and ln.pagina == self.paginas[0].numero]
        inicio_receptor = None
        for i, ln in enumerate(primera):
            if _RX_RECEPTOR.search(ln.texto):
                inicio_receptor = i
                break
        if inicio_receptor is None and self.cuit_propio:
            for i, ln in enumerate(primera):
                if self.cuit_propio in re.sub(r"[-\s]", "", ln.texto):
                    inicio_receptor = i
                    break
        if inicio_receptor is None:
            self.zona_emisor = [ln for ln in primera if ln.y < self.alto * 0.35]
            inicio_global = len(self.zona_emisor)
        else:
            self.zona_emisor = primera[:inicio_receptor]
            inicio_global = inicio_receptor

        resto = self.lineas[inicio_global:]
        self.zona_receptor = resto
        encabezado = next((i for i, ln in enumerate(resto)
                           if _RX_ENCABEZADO_ITEMS.search(ln.texto)
                           and _RX_ENCABEZADO_ITEMS_2.search(ln.texto)), None)
        if encabezado is None:
            self.zona_items, self.zona_totales = [], resto
            return
        self.zona_receptor = resto[:encabezado]
        posterior = resto[encabezado + 1:]
        fin = next((i for i, ln in enumerate(posterior) if _RX_INICIO_TOTALES.search(ln.texto)), None)
        if fin is None:
            self.zona_items, self.zona_totales = posterior, posterior
        else:
            self.zona_items, self.zona_totales = posterior[:fin], posterior[fin:]

    # ------------------------------------------------------------------ campos
    def interpretar(self) -> Resultado:
        self._tipo()
        self._numeracion()
        self._fecha()
        self._emisor()
        self._condicion_venta()
        self._cae()
        self._moneda()
        self._importes()
        self._descuentos()
        self._receptor()
        return self.r

    def _tipo(self):
        d = self.r.datos
        codigo_txt, linea_cod = None, None
        h = self._buscar(self.zona_emisor,
                         r"(?<!\w)C(?:OD(?:IGO)?|[oó]digo)\.?\s*(?:N\s*[º°o]\.?)?\s*:?\s*0*(\d{1,3})(?!\d)")
        if h and int(h[0].group(1)) in cat.TIPOS:
            codigo_txt, linea_cod = int(h[0].group(1)), h[1]

        letra_txt = None
        for ln in self.zona_emisor:
            for seg in ln.segmentos:
                if seg.texto.strip().upper() in cat.LETRAS:
                    letra_txt = seg.texto.strip().upper()
                    break
            if letra_txt:
                break
        if not letra_txt:
            h = self._buscar(self.zona_emisor,
                             r"(?:FACTURA|FAC\.?\s+ELECTR[OÓ]NICA|NOTA\s+DE\s+(?:CR[EÉ]DITO|D[EÉ]BITO)"
                             r"|RECIBO)\s+([ABCEM])\b")
            if h:
                letra_txt = h[0].group(1).upper()

        clase_txt = None
        texto_emisor = "\n".join(ln.texto for ln in self.zona_emisor)
        for patron, clase in ((r"NOTA\s+DE\s+CR[EÉ]DITO", "NOTA_CREDITO"),
                              (r"NOTA\s+DE\s+D[EÉ]BITO", "NOTA_DEBITO"),
                              (r"FACTURA\s+DE\s+CR[EÉ]DITO|MiPyME", "FACTURA_CREDITO"),
                              (r"\bRECIBO\b", "RECIBO"),
                              (r"\bFAC(?:TURA|\.)?\b", "FACTURA")):
            if re.search(patron, texto_emisor, re.I):
                clase_txt = clase
                break

        if self.qr and self.qr.tipo_codigo in cat.TIPOS:
            d.tipo_codigo = self.qr.tipo_codigo
            self._campo("tipo_codigo", "qr", ALTA)
            if codigo_txt and codigo_txt != d.tipo_codigo:
                self._adv(f"El QR indica comprobante código {d.tipo_codigo} y el texto {codigo_txt}.")
        elif codigo_txt:
            d.tipo_codigo = codigo_txt
            self._campo("tipo_codigo", "texto", ALTA, linea_cod)

        if d.tipo_codigo:
            d.tipo_letra, d.tipo_clase = cat.letra_y_clase(d.tipo_codigo)
            fuente = self.r.campos["tipo_codigo"]
            self.r.campos["tipo_letra"] = self.r.campos["tipo_clase"] = fuente
            if letra_txt and letra_txt != d.tipo_letra:
                self._adv(f"La letra impresa ({letra_txt}) no coincide con el código "
                          f"{d.tipo_codigo} (letra {d.tipo_letra}).")
                self.r.campos["tipo_letra"] = Campo(fuente.fuente, BAJA, "Letra impresa distinta")
        else:
            if letra_txt:
                d.tipo_letra = letra_txt
                self._campo("tipo_letra", "texto", MEDIA)
            if clase_txt:
                d.tipo_clase = clase_txt
                self._campo("tipo_clase", "texto", MEDIA)
            d.tipo_codigo = cat.codigo_desde(d.tipo_letra, d.tipo_clase)
            if d.tipo_codigo:
                self._campo("tipo_codigo", "calculado", MEDIA)

    def _numeracion(self):
        d = self.r.datos
        pv_txt = nro_txt = None
        linea = None
        h_pv = self._buscar(self.zona_emisor, r"Punto\s+de\s+Venta" + S + r"(\d{1,5})(?!\d)")
        h_nro = self._buscar(self.zona_emisor,
                             r"Comp(?:robante)?\.?\s*N(?:ro|[º°o])\.?" + S + r"(\d{1,8})(?!\d)")
        if h_pv and h_nro:
            pv_txt, nro_txt, linea = int(h_pv[0].group(1)), int(h_nro[0].group(1)), h_pv[1]
        else:
            h = (self._buscar(self.zona_emisor,
                              r"N(?:ro|[º°o])\.?\s*:?[\s‖]*(\d{1,5})\s*-\s*(\d{1,8})(?!\d)")
                 or self._buscar(self.zona_emisor, r"(?<![\d-])(\d{4,5})-(\d{8})(?![\d-])"))
            if h:
                pv_txt, nro_txt, linea = int(h[0].group(1)), int(h[0].group(2)), h[1]

        if self.qr and self.qr.punto_venta is not None and self.qr.numero is not None:
            d.punto_venta, d.numero = self.qr.punto_venta, self.qr.numero
            self._campo("punto_venta", "qr", ALTA)
            self._campo("numero", "qr", ALTA)
            if pv_txt is not None and (pv_txt, nro_txt) != (d.punto_venta, d.numero):
                self._adv(f"El QR indica el comprobante {d.punto_venta:05d}-{d.numero:08d} y el texto "
                          f"{pv_txt:05d}-{nro_txt:08d}.")
        elif pv_txt is not None:
            d.punto_venta, d.numero = pv_txt, nro_txt
            self._campo("punto_venta", "texto", ALTA, linea)
            self._campo("numero", "texto", ALTA, linea)

    def _fecha(self):
        d = self.r.datos
        h = self._buscar(self.zona_emisor,
                         r"Fecha(?:\s+de\s+Emisi[oó]n)?\s*:[\s‖]*" + FECHA)
        fecha_txt = parse_fecha(h[0].group(1)) if h else None
        if self.qr and self.qr.fecha:
            d.fecha_emision = self.qr.fecha
            self._campo("fecha_emision", "qr", ALTA)
            if fecha_txt and fecha_txt != d.fecha_emision:
                self._adv(f"El QR indica fecha {d.fecha_emision:%d/%m/%Y} y el texto {fecha_txt:%d/%m/%Y}.")
        elif fecha_txt:
            d.fecha_emision = fecha_txt
            self._campo("fecha_emision", "texto", ALTA, h[1])

    def _cuits(self, lineas):
        for ln in lineas:
            for m in _RX_CUIT.finditer(ln.texto):
                cuit = "".join(m.groups())
                if cuit_valido(cuit) and cuit != self.cuit_propio:
                    precedido = bool(re.search(r"C\.?U\.?I\.?T\.?[\s‖:Nº°.]*$", ln.texto[:m.start()], re.I))
                    yield cuit, ln, precedido

    def _emisor(self):
        d = self.r.datos
        candidatos = list(self._cuits(self.zona_emisor))
        candidatos.sort(key=lambda c: not c[2])
        cuit_txt = candidatos[0] if candidatos else None
        if self.qr and self.qr.cuit and cuit_valido(self.qr.cuit):
            d.emisor_cuit = self.qr.cuit
            self._campo("emisor_cuit", "qr", ALTA)
            if cuit_txt and cuit_txt[0] != d.emisor_cuit:
                self._adv(f"El QR indica CUIT emisor {d.emisor_cuit} y el texto {cuit_txt[0]}.")
        elif cuit_txt:
            d.emisor_cuit = cuit_txt[0]
            self._campo("emisor_cuit", "texto", ALTA if cuit_txt[2] else MEDIA, cuit_txt[1])
        else:
            otros = list(self._cuits(self.lineas))
            if otros:
                d.emisor_cuit = otros[0][0]
                self._campo("emisor_cuit", "heuristica", BAJA, otros[0][1],
                            "CUIT encontrado fuera de la cabecera")

        conocido = (self.proveedor_conocido(d.emisor_cuit)
                    if self.proveedor_conocido and d.emisor_cuit else None)

        # Razón social
        h = self._buscar(self.zona_emisor, r"Raz[oó]n\s+Social\s*:" + S + VALOR)
        if h and limpiar_texto(h[0].group(1), 200):
            d.emisor_razon_social = limpiar_texto(h[0].group(1), 200)
            self._campo("emisor_razon_social", "texto", ALTA, h[1])
        elif conocido and conocido.get("razon_social"):
            d.emisor_razon_social = conocido["razon_social"]
            self._campo("emisor_razon_social", "registro", ALTA, detalle="Proveedor ya registrado")
        else:
            nombre, linea, confianza = self._nombre_por_diseno()
            if nombre:
                d.emisor_razon_social = nombre
                self._campo("emisor_razon_social", "heuristica", confianza, linea,
                            "Deducida por la posición en el encabezado")

        # Domicilio, localidad, provincia
        h = self._buscar(self.zona_emisor, r"Domicilio(?:\s+Comercial)?\s*:" + S + VALOR)
        if h:
            domicilio = h[0].group(1).strip()
            idx = self.zona_emisor.index(h[1])
            if idx + 1 < len(self.zona_emisor):
                sig = self.zona_emisor[idx + 1]
                primero = sig.segmentos[0]
                if (sig.y - h[1].y < 15 and primero.x0 > h[1].x0 + 40 and primero.x0 < self.ancho * 0.5
                        and ":" not in primero.texto and domicilio.endswith(",")):
                    domicilio += " " + primero.texto.strip()
            d.emisor_domicilio = limpiar_texto(domicilio, 300)
            self._campo("emisor_domicilio", "texto", ALTA, h[1])
            loc, prov = self._localidad_provincia(d.emisor_domicilio)
            if loc:
                d.emisor_localidad = limpiar_texto(loc, 120)
                self._campo("emisor_localidad", "texto", MEDIA, h[1], "Tomada del domicilio")
            if prov:
                d.emisor_provincia = prov
                self._campo("emisor_provincia", "texto", MEDIA, h[1], "Tomada del domicilio")
        if not d.emisor_localidad or not d.emisor_provincia:
            loc, prov, linea = self._localidad_por_diseno()
            if loc and not d.emisor_localidad:
                d.emisor_localidad = limpiar_texto(loc, 120)
                self._campo("emisor_localidad", "heuristica", BAJA, linea)
            if prov and not d.emisor_provincia:
                d.emisor_provincia = prov
                self._campo("emisor_provincia", "heuristica", BAJA, linea)
        if conocido:
            for campo in ("domicilio", "localidad", "provincia"):
                atributo = f"emisor_{campo}"
                actual = self.r.campos.get(atributo)
                if conocido.get(campo) and (getattr(d, atributo) is None or actual.confianza == BAJA):
                    setattr(d, atributo, conocido[campo])
                    self._campo(atributo, "registro", ALTA, detalle="Proveedor ya registrado")

        h = self._buscar(self.zona_emisor,
                         r"Condici[oó]n\s+(?:frente\s+al\s+|ante\s+el\s+)?I\.?V\.?A\.?\s*:" + S + VALOR)
        if h:
            d.emisor_condicion_iva = cat.condicion_iva_canonica(h[0].group(1))
            self._campo("emisor_condicion_iva", "texto", ALTA, h[1])
        elif conocido and conocido.get("condicion_iva"):
            d.emisor_condicion_iva = conocido["condicion_iva"]
            self._campo("emisor_condicion_iva", "registro", ALTA)

    def _nombre_por_diseno(self):
        primero = None
        for ln in self.zona_emisor:
            for seg in ln.segmentos:
                t = seg.texto.strip()
                if (seg.x0 > self.ancho * 0.5 or ":" in t or "@" in t or len(t) < 3
                        or _RX_NO_NOMBRE.match(t) or not re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3}", t)
                        or sum(c.isdigit() for c in t) > len(t) / 3 or cat.provincia_canonica(t)):
                    continue
                if _RX_SUFIJO_SOCIETARIO.search(t):
                    return limpiar_texto(t, 200), ln, MEDIA
                if primero is None:
                    primero = (limpiar_texto(t, 200), ln, BAJA)
        return primero or (None, None, None)

    @staticmethod
    def _localidad_provincia(domicilio: str):
        partes = re.split(r"\s+-\s+", domicilio)
        cola = partes[-1] if len(partes) > 1 else domicilio
        if "," in cola:
            loc, prov = cola.rsplit(",", 1)
            prov_c = cat.provincia_canonica(prov)
            if prov_c:
                return (loc.strip() if len(partes) > 1 else None), prov_c
        return None, None

    def _localidad_por_diseno(self):
        localidad = provincia = None
        linea = None
        for ln in self.zona_emisor:
            for seg in ln.segmentos:
                t = seg.texto.strip()
                if seg.x0 > self.ancho * 0.5 or ":" in t:
                    continue
                if provincia is None and cat.provincia_canonica(t):
                    provincia, linea = cat.provincia_canonica(t), ln
                    continue
                m = re.match(r"^(.+?)\s*-\s*(?:CPA?|C\.P\.?)\s*[A-Z]?\d{4}", t, re.I)
                if m and localidad is None:
                    localidad, linea = m.group(1).strip(), ln
                    continue
                m = re.match(r"^(.+?)\s*-\s*(.+)$", t)
                if m and cat.provincia_canonica(m.group(2)) and not re.search(r"\d", m.group(1)):
                    localidad = localidad or m.group(1).strip()
                    provincia = provincia or cat.provincia_canonica(m.group(2))
                    linea = ln
        return localidad, provincia, linea

    def _condicion_venta(self):
        h = self._buscar(self.lineas, r"Condici[oó]n\s+de\s+venta" + S + VALOR)
        if h:
            self.r.datos.condicion_venta = limpiar_texto(h[0].group(1), 100)
            self._campo("condicion_venta", "texto", ALTA, h[1])

    def _cae(self):
        d = self.r.datos
        h = self._buscar(self.lineas, r"C\.?\s?A\.?\s?E\.?\s*(?:N\s*[º°o]\.?)?" + S + r"(\d{14})(?!\d)",
                         ultimo=True)
        cae_txt = h[0].group(1) if h else None
        if self.qr and self.qr.cae and len(self.qr.cae) == 14:
            d.cae = self.qr.cae
            self._campo("cae", "qr", ALTA)
            if cae_txt and cae_txt != d.cae:
                self._adv(f"El QR indica CAE {d.cae} y el texto {cae_txt}.")
        elif cae_txt:
            d.cae = cae_txt
            self._campo("cae", "texto", ALTA, h[1])
        if h:
            i = self.lineas.index(h[1])
            cercanas = self.lineas[i:i + 3]
            hv = self._buscar(cercanas,
                              r"(?:Fecha\s+de\s+)?V(?:to|encimiento)\.?(?:\s+(?:del?\s+)?C\.?A\.?E\.?)?"
                              r"\s*:?[\s‖]*" + FECHA)
            if hv and parse_fecha(hv[0].group(1)):
                d.cae_vencimiento = parse_fecha(hv[0].group(1))
                self._campo("cae_vencimiento", "texto", ALTA, hv[1])

    def _moneda(self):
        d = self.r.datos
        if self.qr and self.qr.moneda:
            d.moneda = self.qr.moneda
            self._campo("moneda", "qr", ALTA)
            if self.qr.cotizacion is not None and d.moneda != "ARS":
                d.cotizacion = self.qr.cotizacion
                self._campo("cotizacion", "qr", ALTA)
            return
        h = self._buscar(self.lineas, r"Moneda\s*:" + S + VALOR)
        if h:
            t = h[0].group(1).upper()
            for patron, iso in ((r"D[OÓ]LAR|USD|U\$S|\bDOL\b", "USD"), (r"EURO|\bEUR\b", "EUR"),
                                (r"PESO|\bARS\b|\bPES\b", "ARS")):
                if re.search(patron, t):
                    d.moneda = iso
                    self._campo("moneda", "texto", ALTA, h[1])
                    return
        texto = "\n".join(ln.texto for ln in self.lineas)
        if re.search(r"U\$S|USD|D[OÓ]LARES", texto, re.I):
            self._adv("El comprobante menciona dólares: verifique la moneda y la cotización.")
            return
        d.moneda = "ARS"
        self._campo("moneda", "defecto", ALTA if re.search(r"Son\s+PESOS", texto, re.I) else MEDIA,
                    detalle="No se informa otra moneda")

    # ------------------------------------------------------------------ importes
    def _importes(self):
        d = self.r.datos
        z = self.zona_totales

        total, ln_total = self._importe(z, [r"Importe\s+Total" + S + NUM,
                                            r"(?<!\w)TOTAL(?:\s+(?:A\s+PAGAR|GENERAL|FACTURA|COMPROBANTE))?"
                                            + S + NUM])
        if self.qr and self.qr.importe is not None:
            d.total = redondear(self.qr.importe)
            self._campo("total", "qr", ALTA)
            if total is not None and abs(total - d.total) > TOL:
                self._adv(f"El QR indica un total de {d.total} y el texto {total}.")
        elif total is not None:
            d.total = redondear(total)
            self._campo("total", "texto", ALTA, ln_total)

        neto, ln_neto = self._importe(z, [r"(?:Importe\s+)?Neto\s+Gravado(?:\s+Total)?" + S + NUM,
                                          r"Sub\s*total\s+(?:Neto\s+)?Gravado" + S + NUM,
                                          r"(?<!\w)Neto\s*:" + S + NUM])
        no_grav, ln_ng = self._importe(z, [r"(?:Importe\s+)?(?:Neto\s+)?No\s+Gravado" + S + NUM,
                                           r"Conceptos\s+no\s+gravados" + S + NUM])
        exento, ln_ex = self._importe(z, [r"(?:Importe\s+)?Exento" + S + NUM,
                                          r"Operaciones\s+Exentas" + S + NUM])
        subtotal, ln_sub = self._importe(z, [r"(?<!\w)Sub\s*total" + S + NUM])

        self._tributos(z)
        otros = d.otros_tributos_total

        if d.tipo_letra and not d.discrimina_iva:
            # Comprobantes B y C: el IVA no se discrimina. Todo el importe va como
            # "no gravado / sin IVA discriminado" (criterio del Libro IVA Compras).
            d.neto_gravado = Decimal("0.00")
            self._campo("neto_gravado", "calculado", ALTA,
                        detalle=f"Comprobante {d.tipo_letra}: sin IVA discriminado")
            d.exento = redondear(exento) if exento is not None else Decimal("0.00")
            self._campo("exento", "texto" if exento is not None else "defecto", ALTA if exento is not None else MEDIA,
                        ln_ex)
            if d.total is not None:
                d.no_gravado = d.total - otros - d.exento
                detalle = f"Comprobante {d.tipo_letra}: total sin IVA discriminado"
                if subtotal is not None and abs(subtotal - d.no_gravado - d.exento) > TOL:
                    self._adv(f"El subtotal impreso ({subtotal}) no coincide con el total menos otros tributos.")
                self._campo("no_gravado", "calculado", ALTA, detalle=detalle)
            elif subtotal is not None:
                d.no_gravado = redondear(subtotal) - d.exento
                self._campo("no_gravado", "texto", MEDIA, ln_sub)
            if re.search(r"IVA\s+Contenido", "\n".join(ln.texto for ln in z), re.I):
                self._adv("El comprobante informa 'IVA contenido' (Régimen de Transparencia Fiscal): "
                          "es informativo y no se registra como IVA discriminado.")
            return

        self._iva(z)
        if neto is not None:
            d.neto_gravado = redondear(neto)
            self._campo("neto_gravado", "texto", ALTA, ln_neto)
        d.no_gravado = redondear(no_grav) if no_grav is not None else Decimal("0.00")
        self._campo("no_gravado", "texto" if no_grav is not None else "defecto",
                    ALTA if no_grav is not None else MEDIA, ln_ng,
                    None if no_grav is not None else "No informado: se asume 0")
        d.exento = redondear(exento) if exento is not None else Decimal("0.00")
        self._campo("exento", "texto" if exento is not None else "defecto",
                    ALTA if exento is not None else MEDIA, ln_ex,
                    None if exento is not None else "No informado: se asume 0")

        # Neto no impreso: se deduce sólo si hay total, IVA y una única alícuota.
        if d.neto_gravado is None and d.total is not None and len(d.iva) == 1:
            d.neto_gravado = d.total - d.iva_total - d.no_gravado - d.exento - otros
            self._campo("neto_gravado", "calculado", BAJA, detalle="Total menos IVA y otros conceptos")

        # Base imponible de cada alícuota: sólo es inequívoca con una única alícuota.
        if len(d.iva) == 1 and d.neto_gravado is not None and d.iva[0].base is None:
            d.iva[0].base = d.neto_gravado

        # Sin líneas de IVA legibles: se deduce si la diferencia corresponde exactamente
        # a una única alícuota válida sobre el neto.
        if not d.iva and d.total is not None and d.neto_gravado:
            diferencia = d.total - d.neto_gravado - d.no_gravado - d.exento - otros
            coincidencias = [a for a in cat.ALICUOTAS_IVA if a > 0
                             and abs(redondear(d.neto_gravado * a / 100) - diferencia) <= TOL]
            if len(coincidencias) == 1:
                alic = normalizar_alicuota(coincidencias[0])
                d.iva.append(LineaIva(alicuota=alic, importe=diferencia, base=d.neto_gravado))
                self._campo("iva", "calculado", BAJA,
                            detalle=f"IVA {alic}% deducido de total − neto; verifique")
                self._adv(f"No se encontró el IVA impreso; se dedujo IVA {alic}% = {diferencia}.")

    def _iva(self, lineas):
        rx_pct = re.compile(r"(?<![\w.])I\.?V\.?A\.?\s*(\d{1,2}(?:[.,]\d{1,3})?)\s*%" + S + NUM, re.I)
        rx_sep = re.compile(r"(?<![\w.])I\.?V\.?A\.?\s*:?[\s‖]*(\d{1,2}[.,]\d{1,3})\s*%?[\s‖]+\$?\s*" + NUM,
                            re.I)
        validas = {normalizar_alicuota(a) for a in cat.ALICUOTAS_IVA}
        encontradas: dict[Decimal, tuple[Decimal, Linea]] = {}
        for ln in lineas:
            coincidencias = list(rx_pct.finditer(ln.texto)) or list(rx_sep.finditer(ln.texto))
            for m in coincidencias:
                previo = ln.texto[:m.start()]
                if re.search(r"(perc\w*|ret\w*|contenid\w*|otros)\.?\s*(?:de\s+)?$", previo, re.I):
                    continue
                alic = normalizar_alicuota(parse_importe(m.group(1)))
                importe = parse_importe(m.group(2))
                if alic is None or importe is None:
                    continue
                if alic not in validas:
                    self._adv(f"Se leyó una alícuota de IVA inexistente ({alic}%): se ignora.")
                    continue
                encontradas[alic] = (redondear(importe), ln)  # la última aparición prevalece
        for alic in sorted(encontradas):
            importe, ln = encontradas[alic]
            if importe != 0:
                self.r.datos.iva.append(LineaIva(alicuota=alic, importe=importe))
        if self.r.datos.iva:
            self._campo("iva", "texto", ALTA, encontradas[self.r.datos.iva[0].alicuota][1])

    def _tributos(self, lineas):
        d = self.r.datos
        patrones = (
            (r"Perc(?:epci[oó]n(?:es)?|\.)?\s*(?:/\s*Ret\.?\s*)?(?:de\s+)?I\.?V\.?A\.?", "PERCEPCION_IVA"),
            (r"Per(?:c(?:epci[oó]n(?:es)?)?)?\.?\s*(?:/\s*Ret\.?\s*)?(?:de\s+)?(?:IIBB|I\.\s?B\.|Ingresos\s+Brutos)",
             "PERCEPCION_IIBB"),
            (r"Perc(?:epci[oó]n(?:es)?|\.)?\s*(?:/\s*Ret\.?\s*)?(?:de\s+)?Ganancias", "PERCEPCION_GANANCIAS"),
            (r"Imp(?:uestos?|\.)?\s*Int(?:ernos?|\.)", "IMPUESTO_INTERNO"),
            (r"(?:Tasa|Imp(?:uesto)?\.?)\s+Municipal", "IMPUESTO_MUNICIPAL"),
        )
        detalle: list[Tributo] = []
        for ln in lineas:
            for patron, tipo in patrones:
                m = re.search(patron, ln.texto, re.I)
                if not m:
                    continue
                numeros = [parse_importe(x) for x in re.findall(NUM, ln.texto[m.end():])]
                numeros = [n for n in numeros if n is not None]
                if numeros and numeros[-1] > 0:
                    detalle.append(Tributo(tipo=tipo, importe=redondear(numeros[-1]),
                                           descripcion=limpiar_texto(ln.segmentos[0].texto, 200)))
                break
        total, ln_total = self._importe(lineas, [r"(?:Importe\s+)?Otros\s+Tributos" + S + NUM,
                                                 r"Total\s+(?:de\s+)?Tributos" + S + NUM])
        suma = sum((t.importe for t in detalle), Decimal("0"))
        if total is not None and total > 0:
            if detalle and abs(suma - total) <= TOL:
                d.tributos = detalle
                self._campo("tributos", "texto", ALTA, ln_total)
            elif not detalle:
                d.tributos = [Tributo(tipo="OTRO", importe=redondear(total),
                                      descripcion="Otros tributos (sin detalle)")]
                self._campo("tributos", "texto", MEDIA, ln_total, "Sin detalle por tributo")
            else:
                d.tributos = detalle
                self._campo("tributos", "texto", BAJA, ln_total)
                self._adv(f"El detalle de otros tributos ({suma}) no coincide con el total informado ({total}).")
        elif detalle:
            d.tributos = detalle
            self._campo("tributos", "texto", MEDIA)

    # ------------------------------------------------------------------ descuentos
    @staticmethod
    def _combinar(numeros: list[Decimal]):
        """Busca cantidad, precio, % bonif. y subtotal que cierren aritméticamente."""
        for i in range(len(numeros) - 1):
            q, p = numeros[i], numeros[i + 1]
            if q <= 0 or p <= 0:
                continue
            bruto = q * p
            for m in range(i + 2, len(numeros)):
                s = numeros[m]
                if s <= 0:
                    continue
                for k in range(i + 2, m):
                    b = numeros[k]
                    if 0 < b < 100 and abs(redondear(bruto * (1 - b / 100)) - s) <= TOL_ITEM:
                        return bruto, s, b
                    if 0 < b < bruto and abs(redondear(bruto - b) - s) <= TOL_ITEM:
                        return bruto, s, None
                if abs(redondear(bruto) - s) <= TOL_ITEM:
                    return bruto, s, Decimal(0)
        return None

    def _descuentos(self):
        d = self.r.datos
        items: list[Descuento] = []
        lineas_ok = 0
        for ln in self.zona_items:
            numeros = [parse_importe(x) for x in re.findall(r"(?<![\w.,])\d[\d.,]*(?![\w])", ln.texto)]
            numeros = [n for n in numeros if n is not None]
            combo = self._combinar(numeros)
            if not combo:
                continue
            lineas_ok += 1
            bruto, subtotal, pct = combo
            importe = redondear(bruto) - subtotal
            if importe > Decimal("0.005"):
                descripcion = next((s.texto for s in ln.segmentos if not re.match(r"^[\d.,\s%]+$", s.texto)), None)
                porcentaje = pct if pct and abs(redondear(bruto * pct / 100) - importe) <= TOL_ITEM else None
                items.append(Descuento(importe=importe, porcentaje=porcentaje, base=redondear(bruto),
                                       descripcion=limpiar_texto(descripcion, 200), origen="ITEM"))

        general = None
        h = self._buscar(self.zona_totales,
                         r"(?<!\w)(?:Bonificaci[oó]n|Descuento)(?:\s+General)?(?:\s*/\s*Recargo)?"
                         r"(?:\s*\(?\s*(\d{1,2}(?:[.,]\d+)?)\s*%\s*\)?)?" + S + NUM, ultimo=True)
        if h:
            valor = parse_importe(h[0].group(2))
            pct_txt = parse_importe(h[0].group(1)) if h[0].group(1) else None
            if valor and valor != 0:
                valor = abs(valor)
                sub, _ = self._importe(self.zona_totales, [r"(?<!\w)Sub\s*total\s*:" + S + NUM])
                sub_gral, _ = self._importe(self.zona_totales, [r"Sub\s*total\s+General" + S + NUM])
                suma_items = sum((x.importe for x in items), Decimal("0"))
                if sub is not None and sub_gral is not None and abs(sub - sub_gral - valor) <= TOL:
                    pct = pct_txt if pct_txt is not None else redondear(valor * 100 / sub)
                    general = Descuento(importe=redondear(valor), porcentaje=pct, base=redondear(sub),
                                        descripcion="Bonificación general", origen="GENERAL")
                elif sub is not None and sub_gral is not None and abs(sub_gral - sub - valor) <= TOL:
                    self._adv(f"Se informa un recargo de {valor} (no es un descuento).")
                elif items and abs(suma_items - valor) <= TOL:
                    pass  # es la suma de las bonificaciones por ítem: no se duplica
                else:
                    general = Descuento(importe=redondear(valor), porcentaje=pct_txt, base=None,
                                        descripcion="Bonificación general", origen="GENERAL")
                    self._adv("Se encontró una bonificación general cuya base no pudo verificarse.")

        d.descuentos = items + ([general] if general else [])
        if d.descuentos:
            self._campo("descuentos", "texto", ALTA if general is None or general.base else BAJA)
        elif lineas_ok:
            self._campo("descuentos", "texto", ALTA, detalle=f"{lineas_ok} ítem(s) sin bonificación")

    def _receptor(self):
        if not self.cuit_propio:
            return
        if self.qr and self.qr.nro_doc_receptor:
            if self.qr.nro_doc_receptor != self.cuit_propio:
                self._adv(f"Según el QR, el receptor es {self.qr.nro_doc_receptor}, no el CUIT propio.")
            return
        texto = re.sub(r"[-\s]", "", "\n".join(ln.texto for ln in self.lineas))
        if self.cuit_propio not in texto:
            self._adv("No se encontró el CUIT propio como receptor del comprobante.")


def interpretar(paginas, qr, cuit_propio=None, proveedor_conocido=None) -> Resultado:
    return Interprete(paginas, qr, cuit_propio, proveedor_conocido).interpretar()
