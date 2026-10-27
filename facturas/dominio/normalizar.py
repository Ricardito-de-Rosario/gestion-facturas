"""Normalización de importes, fechas, porcentajes y CUIT en formato argentino."""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTAVO = Decimal("0.01")

_RE_MILES_PUNTO = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_RE_MILES_COMA = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_RE_ENTERO_AGRUPADO = {
    ".": re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$"),
    ",": re.compile(r"^\d{1,3}(?:,\d{3})*$|^\d+$"),
}


def redondear(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def parse_importe(texto) -> Decimal | None:
    """Convierte un importe escrito en formato argentino (o internacional) a Decimal.

    ``160000,00`` -> 160000.00 · ``343.600,00`` -> 343600.00 · ``1,234.56`` -> 1234.56
    ``1.500`` -> 1500 (punto como separador de miles) · ``(10,00)`` o ``-10,00`` -> -10.00
    Devuelve None si el texto no es un número reconocible (nunca adivina).
    """
    if texto is None:
        return None
    if isinstance(texto, Decimal):
        return texto
    if isinstance(texto, int):
        return Decimal(texto)
    s = str(texto).strip().replace("$", "").replace(" ", "").replace(" ", "")
    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    if s.startswith("-"):
        negativo, s = True, s[1:]
    elif s.endswith("-"):
        negativo, s = True, s[:-1]
    if not s or not re.fullmatch(r"[\d.,]+", s) or not s[0].isdigit() or not s[-1].isdigit():
        return None

    coma, punto = s.rfind(","), s.rfind(".")
    if coma >= 0 and punto >= 0:
        sep_dec = "," if coma > punto else "."
        sep_mil = "." if sep_dec == "," else ","
        entero, _, decimales = s.rpartition(sep_dec)
        if not _RE_ENTERO_AGRUPADO[sep_mil].match(entero):
            return None
        entero = entero.replace(sep_mil, "")
    elif coma >= 0:
        if s.count(",") > 1:
            if not _RE_MILES_COMA.match(s):
                return None
            entero, decimales = s.replace(",", ""), ""
        else:
            # En Argentina la coma es separador decimal: "1,000" = 1.000
            entero, decimales = s.split(",")
    elif punto >= 0:
        if _RE_MILES_PUNTO.match(s):
            entero, decimales = s.replace(".", ""), ""
        elif s.count(".") == 1:
            entero, decimales = s.split(".")
        else:
            return None
    else:
        entero, decimales = s, ""

    try:
        valor = Decimal(entero + ("." + decimales if decimales else ""))
    except InvalidOperation:
        return None
    return -valor if negativo else valor


def parse_porcentaje(texto) -> Decimal | None:
    if texto is None:
        return None
    valor = parse_importe(str(texto).replace("%", "").strip())
    if valor is None:
        return None
    return valor.normalize() if valor == valor.to_integral() else valor


def normalizar_alicuota(valor: Decimal | None) -> Decimal | None:
    """21,000 -> 21.00 ; 10.5 -> 10.50"""
    if valor is None:
        return None
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


_RE_FECHA_DMY = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$")
_RE_FECHA_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def parse_fecha(texto) -> date | None:
    if texto is None:
        return None
    if isinstance(texto, date):
        return texto
    s = str(texto).strip()
    try:
        m = _RE_FECHA_ISO.match(s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _RE_FECHA_DMY.match(s)
        if m:
            anio = int(m.group(3))
            if anio < 100:
                anio += 2000
            return date(anio, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


_PESOS_CUIT = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def normalizar_cuit(texto) -> str | None:
    if texto is None:
        return None
    digitos = re.sub(r"\D", "", str(texto))
    return digitos if len(digitos) == 11 else None


def cuit_valido(cuit: str | None) -> bool:
    c = normalizar_cuit(cuit)
    if not c or c[:2] not in {"20", "23", "24", "25", "26", "27", "30", "33", "34"}:
        return False
    suma = sum(int(d) * p for d, p in zip(c[:10], _PESOS_CUIT))
    verificador = 11 - (suma % 11)
    if verificador == 11:
        verificador = 0
    elif verificador == 10:
        return False
    return verificador == int(c[10])


def formatear_cuit(cuit: str | None) -> str:
    c = normalizar_cuit(cuit)
    return f"{c[:2]}-{c[2:10]}-{c[10]}" if c else (cuit or "")


def limpiar_texto(texto: str | None, largo: int) -> str | None:
    if texto is None:
        return None
    t = re.sub(r"\s+", " ", str(texto)).strip()
    return t[:largo] or None
