"""Procesamiento de documentos PDF de facturas.

Flujo: validar PDF -> extraer texto (u OCR) por página -> descartar copias
(DUPLICADO/TRIPLICADO) -> leer QR fiscal -> interpretar campos.
El contenido del PDF nunca se ejecuta: sólo se lee texto y se rasteriza.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from .documento import OCR, SIN_TEXTO, TEXTO, PaginaExtraida, abrir_pdf, extraer_pagina
from .interprete import Resultado, interpretar
from .layout import SEP
from .qr_arca import DatosQR, leer_qr

log = logging.getLogger(__name__)


@dataclass
class Procesamiento:
    resultado: Resultado
    qr: DatosQR | None
    metodo: str
    paginas_total: int
    paginas_procesadas: int
    sha256: str

    @property
    def texto(self) -> str:
        return "\n".join(ln.texto for ln in self.resultado.lineas)


def _marca_copia(pagina: PaginaExtraida) -> str | None:
    for ln in pagina.lineas[:15]:
        for seg in ln.segmentos:
            t = seg.texto.strip().upper()
            if t in ("ORIGINAL", "DUPLICADO", "TRIPLICADO", "CUADRUPLICADO"):
                return t
    return None


def procesar_pdf(contenido: bytes, cfg: dict, proveedor_conocido=None) -> Procesamiento:
    advertencias: list[str] = []
    doc = abrir_pdf(contenido, int(cfg["MAX_PAGINAS_PDF"]))
    try:
        paginas: list[PaginaExtraida] = []
        marca_inicial = None
        for pagina in doc:
            extraida = extraer_pagina(pagina, cfg, advertencias)
            marca = _marca_copia(extraida)
            if pagina.number == 0:
                marca_inicial = marca
            elif marca and marca != marca_inicial:
                advertencias.append(
                    f"Se procesó sólo el {marca_inicial.lower() if marca_inicial else 'primer ejemplar'}: "
                    f"las {doc.page_count - pagina.number} página(s) siguientes son copias "
                    f"({marca.lower()}, ...).")
                break
            paginas.append(extraida)

        qr = None
        for extraida in paginas:
            qr = leer_qr(doc[extraida.numero])
            if qr:
                break
        if qr is None:
            advertencias.append("No se encontró el código QR fiscal de ARCA: "
                                "los datos se tomaron sólo del texto.")

        resultado = interpretar(paginas, qr, cfg.get("CUIT_PROPIO"), proveedor_conocido)
        resultado.advertencias = advertencias + resultado.advertencias

        metodos = {p.metodo for p in paginas}
        base = OCR if OCR in metodos else TEXTO if TEXTO in metodos else None
        metodo = "+".join(x for x in ("QR" if qr else None, base) if x) or "MANUAL"
        if metodos == {SIN_TEXTO} and not qr:
            resultado.advertencias.append("No se pudo leer ningún dato del documento.")
        return Procesamiento(resultado=resultado, qr=qr, metodo=metodo,
                             paginas_total=doc.page_count, paginas_procesadas=len(paginas),
                             sha256=hashlib.sha256(contenido).hexdigest())
    finally:
        doc.close()


__all__ = ["Procesamiento", "procesar_pdf", "SEP"]
