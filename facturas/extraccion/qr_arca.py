"""Lectura del código QR fiscal de ARCA (RG 4892/2020).

El QR contiene una URL ``https://www.arca.gob.ar/fe/qr/?p=<base64(JSON)>`` (o
``afip.gob.ar``) con: fecha, CUIT emisor, punto de venta, tipo y número de
comprobante, importe total, moneda, cotización, receptor y CAE.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

import numpy as np
import pymupdf
import zxingcpp

from ..dominio.comprobantes import MONEDAS_ARCA

log = logging.getLogger(__name__)

_DOMINIOS = ("afip.gob.ar", "arca.gob.ar")


@dataclass
class DatosQR:
    fecha: date | None
    cuit: str | None
    punto_venta: int | None
    tipo_codigo: int | None
    numero: int | None
    importe: Decimal | None
    moneda: str | None
    moneda_arca: str | None
    cotizacion: Decimal | None
    tipo_doc_receptor: int | None
    nro_doc_receptor: str | None
    cae: str | None

    def a_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, (Decimal, date)):
                d[k] = str(v) if isinstance(v, Decimal) else v.isoformat()
        return d


def _entero(v):
    try:
        return int(str(v).strip()) if v is not None else None
    except ValueError:
        return None


def _decimal(v):
    try:
        return Decimal(str(v).strip()) if v is not None else None
    except InvalidOperation:
        return None


def decodificar_url(texto: str) -> DatosQR | None:
    try:
        url = urlparse(texto.strip())
    except ValueError:
        return None
    if not any(url.netloc.lower().endswith(d) for d in _DOMINIOS):
        return None
    valores = parse_qs(url.query).get("p")
    if not valores:
        return None
    carga = valores[0].strip().replace("-", "+").replace("_", "/").replace(" ", "+")
    carga += "=" * (-len(carga) % 4)
    try:
        datos = json.loads(base64.b64decode(carga), parse_float=Decimal)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(datos, dict):
        return None
    try:
        fecha = date.fromisoformat(str(datos.get("fecha", ""))[:10])
    except ValueError:
        fecha = None
    moneda_arca = str(datos.get("moneda") or "").strip().upper() or None
    cuit = _entero(datos.get("cuit"))
    cae = _entero(datos.get("codAut"))
    receptor = _entero(datos.get("nroDocRec"))
    return DatosQR(
        fecha=fecha,
        cuit=str(cuit) if cuit else None,
        punto_venta=_entero(datos.get("ptoVta")),
        tipo_codigo=_entero(datos.get("tipoCmp")),
        numero=_entero(datos.get("nroCmp")),
        importe=_decimal(datos.get("importe")),
        moneda=MONEDAS_ARCA.get(moneda_arca, moneda_arca) if moneda_arca else None,
        moneda_arca=moneda_arca,
        cotizacion=_decimal(datos.get("ctz")),
        tipo_doc_receptor=_entero(datos.get("tipoDocRec")),
        nro_doc_receptor=str(receptor) if receptor else None,
        cae=str(cae) if cae else None,
    )


def leer_qr(pagina: pymupdf.Page) -> DatosQR | None:
    for dpi in (200, 300):
        pix = pagina.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        imagen = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        try:
            codigos = zxingcpp.read_barcodes(imagen, formats=zxingcpp.BarcodeFormat.QRCode)
        except Exception as exc:
            log.warning("No se pudo leer QR: %s", exc)
            return None
        for codigo in codigos:
            datos = decodificar_url(codigo.text)
            if datos:
                return datos
    return None
