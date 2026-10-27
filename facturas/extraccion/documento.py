"""Validación del PDF, extracción de texto digital y OCR de páginas escaneadas."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..errores import ArchivoInvalido
from .layout import Linea, construir_lineas

log = logging.getLogger(__name__)

MIN_CARACTERES_TEXTO = 25

TEXTO = "TEXTO"
OCR = "OCR"
SIN_TEXTO = "SIN_TEXTO"


@dataclass
class PaginaExtraida:
    numero: int  # base 0
    metodo: str
    ancho: float
    alto: float
    lineas: list[Linea] = field(default_factory=list)


def abrir_pdf(contenido: bytes, max_paginas: int) -> pymupdf.Document:
    if not contenido:
        raise ArchivoInvalido("El archivo está vacío")
    if b"%PDF-" not in contenido[:1024]:
        raise ArchivoInvalido("El archivo no es un PDF")
    try:
        doc = pymupdf.open(stream=contenido, filetype="pdf")
    except Exception as exc:  # PyMuPDF lanza varios tipos según el daño del archivo
        raise ArchivoInvalido("El PDF está dañado o no se puede leer") from exc
    if not doc.is_pdf:
        doc.close()
        raise ArchivoInvalido("El archivo no es un PDF")
    if doc.needs_pass:
        doc.close()
        raise ArchivoInvalido("El PDF está protegido con contraseña")
    if doc.page_count == 0:
        doc.close()
        raise ArchivoInvalido("El PDF no tiene páginas")
    if doc.page_count > max_paginas:
        doc.close()
        raise ArchivoInvalido(f"El PDF tiene {doc.page_count} páginas (máximo {max_paginas})")
    return doc


def tessdata_disponible(ruta: str | None) -> str | None:
    """Devuelve la carpeta de datos de Tesseract, o None si no hay OCR disponible."""
    if ruta:
        return ruta if Path(ruta).is_dir() else None
    try:
        return pymupdf.get_tessdata()
    except Exception:
        return None


def _necesita_ocr(pagina: pymupdf.Page) -> bool:
    caracteres = len(pagina.get_text("text").strip())
    if caracteres < MIN_CARACTERES_TEXTO:
        return True
    if caracteres < 300:
        area_pagina = abs(pagina.rect)
        area_imagenes = sum(abs(pymupdf.Rect(i["bbox"]) & pagina.rect)
                            for i in pagina.get_image_info())
        return area_pagina > 0 and area_imagenes / area_pagina > 0.6
    return False


def extraer_pagina(pagina: pymupdf.Page, cfg: dict, advertencias: list[str]) -> PaginaExtraida:
    resultado = PaginaExtraida(numero=pagina.number, metodo=TEXTO,
                               ancho=pagina.rect.width, alto=pagina.rect.height)
    textpage = None
    if _necesita_ocr(pagina):
        tessdata = tessdata_disponible(cfg.get("TESSDATA_DIR")) if cfg.get("OCR_HABILITADO") else None
        if tessdata is None:
            resultado.metodo = SIN_TEXTO
            advertencias.append(
                f"La página {pagina.number + 1} es una imagen escaneada y el OCR no está disponible "
                "(instale Tesseract con el idioma español). Complete los datos manualmente.")
        else:
            try:
                completa = len(pagina.get_text("text").strip()) < MIN_CARACTERES_TEXTO
                textpage = pagina.get_textpage_ocr(language=cfg.get("OCR_IDIOMA", "spa"),
                                                   dpi=int(cfg.get("OCR_DPI", 300)),
                                                   full=completa, tessdata=tessdata)
                resultado.metodo = OCR
            except Exception as exc:
                log.warning("Fallo de OCR en página %s: %s", pagina.number + 1, exc)
                resultado.metodo = SIN_TEXTO
                advertencias.append(f"No se pudo aplicar OCR a la página {pagina.number + 1}: {exc}")
    palabras = pagina.get_text("words", textpage=textpage) if textpage else pagina.get_text("words")
    resultado.lineas = construir_lineas(palabras, pagina.number)
    return resultado


def renderizar_png(doc: pymupdf.Document, numero: int, dpi: int = 110) -> bytes:
    if not 0 <= numero < doc.page_count:
        raise ArchivoInvalido("Número de página inexistente")
    return doc[numero].get_pixmap(dpi=dpi).tobytes("png")
