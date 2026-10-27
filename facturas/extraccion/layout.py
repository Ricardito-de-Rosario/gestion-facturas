"""Reconstrucción de líneas visuales a partir de palabras con coordenadas.

Los PDF de facturas guardan el texto en un orden arbitrario. Agrupando las
palabras por su posición vertical se recuperan las líneas tal como se ven, y
los saltos horizontales grandes (cambio de columna) se marcan con ``SEP``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SEP = "‖"
TOLERANCIA_Y = 3.5  # puntos
SALTO_COLUMNA = 12.0  # puntos


@dataclass
class Segmento:
    texto: str
    x0: float
    x1: float


@dataclass
class Linea:
    pagina: int
    y: float
    segmentos: list[Segmento] = field(default_factory=list)

    @property
    def texto(self) -> str:
        return f" {SEP} ".join(s.texto for s in self.segmentos)

    @property
    def x0(self) -> float:
        return self.segmentos[0].x0 if self.segmentos else 0.0


def construir_lineas(palabras, pagina: int) -> list[Linea]:
    """``palabras``: tuplas (x0, y0, x1, y1, texto, ...) como las de PyMuPDF."""
    items = [(float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
             for w in palabras if str(w[4]).strip()]
    items.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))

    grupos: list[tuple[float, list]] = []
    for w in items:
        yc = (w[1] + w[3]) / 2
        if grupos and abs(grupos[-1][0] - yc) <= TOLERANCIA_Y:
            grupos[-1][1].append(w)
        else:
            grupos.append((yc, [w]))

    lineas = []
    for yc, ws in grupos:
        ws.sort(key=lambda w: w[0])
        linea = Linea(pagina=pagina, y=round(yc, 1))
        actual = None
        for w in ws:
            if actual is not None and w[0] - actual.x1 <= SALTO_COLUMNA:
                actual.texto += " " + w[4]
                actual.x1 = w[2]
            else:
                actual = Segmento(texto=w[4], x0=w[0], x1=w[2])
                linea.segmentos.append(actual)
        lineas.append(linea)
    return lineas
