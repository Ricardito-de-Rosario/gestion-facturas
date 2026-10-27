"""Almacenamiento seguro de los PDF.

* Los archivos subidos quedan en ``DATA_DIR/tmp/<token>.pdf`` hasta que el
  usuario confirma la factura; luego pasan a ``DATA_DIR/documentos/aa/<uuid>.pdf``.
* El nombre de archivo en disco nunca deriva del nombre original (que sólo se
  guarda como dato), por lo que no hay riesgo de recorrer directorios.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errores import NoEncontrado
from ..modelos import Documento

log = logging.getLogger(__name__)
_RE_TOKEN = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class Temporal:
    token: str
    ruta: Path
    nombre_original: str
    sha256: str
    tamano: int
    paginas: int
    metodo: str
    qr: dict | None


class Almacen:
    def __init__(self, data_dir: str):
        self.base = Path(data_dir).resolve()
        self.dir_tmp = self.base / "tmp"
        self.dir_docs = self.base / "documentos"
        self.dir_tmp.mkdir(parents=True, exist_ok=True)
        self.dir_docs.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- temporales
    def guardar_temporal(self, contenido: bytes, nombre_original: str, *, paginas: int,
                         metodo: str, qr: dict | None) -> Temporal:
        token = uuid.uuid4().hex
        ruta = self.dir_tmp / f"{token}.pdf"
        ruta.write_bytes(contenido)
        meta = {"nombre_original": Path(nombre_original or "documento.pdf").name[:255],
                "sha256": hashlib.sha256(contenido).hexdigest(), "tamano": len(contenido),
                "paginas": paginas, "metodo": metodo, "qr": qr}
        (self.dir_tmp / f"{token}.json").write_text(json.dumps(meta), encoding="utf-8")
        return Temporal(token=token, ruta=ruta, **meta)

    def obtener_temporal(self, token: str) -> Temporal:
        if not isinstance(token, str) or not _RE_TOKEN.match(token):
            raise NoEncontrado("Documento temporal inválido")
        ruta = self.dir_tmp / f"{token}.pdf"
        ruta_meta = self.dir_tmp / f"{token}.json"
        if not ruta.is_file() or not ruta_meta.is_file():
            raise NoEncontrado("El documento subido expiró o no existe: vuelva a cargarlo")
        meta = json.loads(ruta_meta.read_text(encoding="utf-8"))
        return Temporal(token=token, ruta=ruta, **meta)

    def descartar_temporal(self, token: str) -> None:
        if isinstance(token, str) and _RE_TOKEN.match(token):
            for ext in (".pdf", ".json"):
                (self.dir_tmp / f"{token}{ext}").unlink(missing_ok=True)

    def limpiar_temporales(self, horas: int) -> int:
        limite = time.time() - horas * 3600
        borrados = 0
        for archivo in self.dir_tmp.iterdir():
            if archivo.is_file() and archivo.stat().st_mtime < limite:
                archivo.unlink(missing_ok=True)
                borrados += 1
        return borrados

    # ------------------------------------------------------------- definitivos
    def consolidar(self, sesion: Session, temporal: Temporal) -> tuple[Documento, Path | None]:
        """Copia el temporal al almacén definitivo y crea (o reutiliza) el Documento.

        Devuelve el documento y la ruta del archivo nuevo (para poder borrarlo si
        la transacción falla), o None si el documento ya existía. El temporal se
        conserva: el llamador lo descarta sólo después de confirmar la transacción.
        """
        existente = sesion.scalar(select(Documento).where(Documento.sha256 == temporal.sha256))
        if existente is not None:
            return existente, None
        relativa = Path(temporal.sha256[:2]) / f"{uuid.uuid4().hex}.pdf"
        destino = self.dir_docs / relativa
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(temporal.ruta, destino)
        if hashlib.sha256(destino.read_bytes()).hexdigest() != temporal.sha256:
            destino.unlink(missing_ok=True)
            raise OSError("La copia del documento no coincide con el original")
        doc = Documento(sha256=temporal.sha256, nombre_original=temporal.nombre_original,
                        ruta_relativa=relativa.as_posix(), tamano_bytes=temporal.tamano,
                        paginas=temporal.paginas, metodo_extraccion=temporal.metodo,
                        qr_json=json.dumps(temporal.qr) if temporal.qr else None)
        sesion.add(doc)
        return doc, destino

    def ruta_documento(self, doc: Documento) -> Path:
        ruta = (self.dir_docs / doc.ruta_relativa).resolve()
        if self.dir_docs not in ruta.parents or not ruta.is_file():
            raise NoEncontrado("No se encuentra el archivo PDF del documento")
        return ruta


def borrar_si_existe(ruta: Path | None) -> None:
    if ruta is not None:
        try:
            os.remove(ruta)
        except OSError:
            log.warning("No se pudo borrar %s", ruta)
