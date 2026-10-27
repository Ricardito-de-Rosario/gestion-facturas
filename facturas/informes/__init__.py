"""Generación de informes mensuales (Excel y PDF).

Ambos se construyen a partir de ``servicios.resumen.calcular`` y
``servicios.resumen.detalle``: los mismos datos que muestra la interfaz.
"""
from __future__ import annotations

from decimal import Decimal

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre")


def nombre_periodo(clave: str) -> str:
    anio, mes = clave.split("-")
    return f"{MESES[int(mes) - 1].capitalize()} {anio}"


def fmt_monto(valor) -> str:
    if valor is None:
        return "—"
    v = Decimal(str(valor))
    texto = f"{v:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(valor) -> str:
    if valor is None:
        return "—"
    return fmt_monto(valor) + " %"


def dec(valor) -> Decimal | None:
    return None if valor is None else Decimal(str(valor))
