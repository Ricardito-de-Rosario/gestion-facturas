"""Catálogos de ARCA (ex AFIP): tipos de comprobante, monedas, alícuotas, provincias."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

CLASES = {
    "FACTURA": "Factura",
    "FACTURA_CREDITO": "Factura de Crédito Electrónica MiPyME",
    "NOTA_DEBITO": "Nota de Débito",
    "NOTA_CREDITO": "Nota de Crédito",
    "RECIBO": "Recibo",
    "NOTA_VENTA": "Nota de Venta al Contado",
    "OTRO": "Otro comprobante",
}

LETRAS = ("A", "B", "C", "E", "M")
# Letras que discriminan IVA (crédito fiscal computable para el receptor).
LETRAS_DISCRIMINAN_IVA = frozenset({"A", "M"})

# Código ARCA -> (letra, clase)
TIPOS: dict[int, tuple[str, str]] = {
    1: ("A", "FACTURA"), 2: ("A", "NOTA_DEBITO"), 3: ("A", "NOTA_CREDITO"),
    4: ("A", "RECIBO"), 5: ("A", "NOTA_VENTA"),
    6: ("B", "FACTURA"), 7: ("B", "NOTA_DEBITO"), 8: ("B", "NOTA_CREDITO"),
    9: ("B", "RECIBO"), 10: ("B", "NOTA_VENTA"),
    11: ("C", "FACTURA"), 12: ("C", "NOTA_DEBITO"), 13: ("C", "NOTA_CREDITO"),
    15: ("C", "RECIBO"),
    19: ("E", "FACTURA"), 20: ("E", "NOTA_DEBITO"), 21: ("E", "NOTA_CREDITO"),
    51: ("M", "FACTURA"), 52: ("M", "NOTA_DEBITO"), 53: ("M", "NOTA_CREDITO"),
    54: ("M", "RECIBO"),
    201: ("A", "FACTURA_CREDITO"), 202: ("A", "NOTA_DEBITO"), 203: ("A", "NOTA_CREDITO"),
    206: ("B", "FACTURA_CREDITO"), 207: ("B", "NOTA_DEBITO"), 208: ("B", "NOTA_CREDITO"),
    211: ("C", "FACTURA_CREDITO"), 212: ("C", "NOTA_DEBITO"), 213: ("C", "NOTA_CREDITO"),
}

ALICUOTAS_IVA = (Decimal("0"), Decimal("2.5"), Decimal("5"), Decimal("10.5"),
                 Decimal("21"), Decimal("27"))

# Códigos de moneda de ARCA -> ISO 4217
MONEDAS_ARCA = {
    "PES": "ARS", "DOL": "USD", "002": "USD", "060": "EUR", "012": "BRL",
    "011": "UYU", "021": "GBP", "019": "JPY", "033": "CLP", "010": "MXN",
    "029": "PYG", "031": "BOB", "064": "CNY", "009": "CHF",
}

TIPOS_TRIBUTO = {
    "PERCEPCION_IVA": "Percepción de IVA",
    "PERCEPCION_IIBB": "Percepción de Ingresos Brutos",
    "PERCEPCION_GANANCIAS": "Percepción de Ganancias",
    "IMPUESTO_INTERNO": "Impuestos internos",
    "IMPUESTO_MUNICIPAL": "Impuesto municipal",
    "OTRO": "Otro tributo",
}

ORIGENES_DESCUENTO = ("ITEM", "GENERAL", "MANUAL")

PROVINCIAS = (
    "Buenos Aires", "Ciudad Autónoma de Buenos Aires", "Catamarca", "Chaco", "Chubut",
    "Córdoba", "Corrientes", "Entre Ríos", "Formosa", "Jujuy", "La Pampa", "La Rioja",
    "Mendoza", "Misiones", "Neuquén", "Río Negro", "Salta", "San Juan", "San Luis",
    "Santa Cruz", "Santa Fe", "Santiago del Estero", "Tierra del Fuego", "Tucumán",
)
_ALIAS_PROVINCIA = {
    "caba": "Ciudad Autónoma de Buenos Aires",
    "c.a.b.a.": "Ciudad Autónoma de Buenos Aires",
    "capital federal": "Ciudad Autónoma de Buenos Aires",
    "ciudad de buenos aires": "Ciudad Autónoma de Buenos Aires",
    "ciudad autonoma buenos aires": "Ciudad Autónoma de Buenos Aires",
    "pcia. de buenos aires": "Buenos Aires",
    "provincia de buenos aires": "Buenos Aires",
    "tierra del fuego, antartida e islas del atlantico sur": "Tierra del Fuego",
}


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def _clave(texto: str) -> str:
    return re.sub(r"\s+", " ", sin_acentos(texto).lower()).strip(" .,-")


_INDICE_PROVINCIAS = {_clave(p): p for p in PROVINCIAS}
_INDICE_PROVINCIAS.update({_clave(k): v for k, v in _ALIAS_PROVINCIA.items()})


def provincia_canonica(texto: str | None) -> str | None:
    if not texto:
        return None
    return _INDICE_PROVINCIAS.get(_clave(texto))


def letra_y_clase(codigo: int | None) -> tuple[str | None, str | None]:
    if codigo is None or codigo not in TIPOS:
        return None, None
    return TIPOS[codigo]


def codigo_desde(letra: str | None, clase: str | None) -> int | None:
    """Código ARCA estándar (no MiPyME) para una letra y clase."""
    if not letra or not clase:
        return None
    for codigo, (l, c) in TIPOS.items():
        if l == letra and c == clase and codigo < 200:
            return codigo
    for codigo, (l, c) in TIPOS.items():
        if l == letra and c == clase:
            return codigo
    return None


def descripcion_tipo(letra: str | None, clase: str | None) -> str:
    nombre = CLASES.get(clase or "", "Comprobante")
    return f"{nombre} {letra}" if letra else nombre


def signo(clase: str | None) -> int:
    """Las notas de crédito restan en los acumulados."""
    return -1 if clase == "NOTA_CREDITO" else 1


def discrimina_iva(letra: str | None) -> bool:
    return letra in LETRAS_DISCRIMINAN_IVA


def condicion_iva_canonica(texto: str | None) -> str | None:
    if not texto:
        return None
    t = _clave(texto)
    if "monotrib" in t:
        return "Responsable Monotributo"
    if "inscripto" in t:
        return "IVA Responsable Inscripto"
    if "exento" in t:
        return "IVA Sujeto Exento"
    if "consumidor final" in t:
        return "Consumidor Final"
    if "no alcanzado" in t:
        return "IVA No Alcanzado"
    return texto.strip()[:60]
