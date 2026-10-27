"""Configuración de la aplicación.

Valores por defecto + archivo opcional ``instance/config.py`` + variables de
entorno con prefijo ``FACTURAS_`` (en ese orden de prioridad creciente).
"""
from __future__ import annotations

import os
import runpy
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"

_DEFAULTS = {
    # Carpeta donde se guardan base de datos, PDF, temporales y logs.
    "DATA_DIR": str(BASE_DIR / "data"),
    # Si no se indica, se usa SQLite dentro de DATA_DIR.
    "DATABASE_URL": None,
    # CUIT propio (receptor de las facturas de compra). Sirve para no
    # confundirlo con el CUIT del emisor y para advertir facturas ajenas.
    "CUIT_PROPIO": None,
    # Límites de seguridad para los archivos subidos.
    "MAX_TAMANO_PDF_MB": 20,
    "MAX_PAGINAS_PDF": 50,
    # OCR (Tesseract). Si TESSDATA_DIR es None se intenta autodetectar.
    "OCR_HABILITADO": True,
    "OCR_IDIOMA": "spa",
    "TESSDATA_DIR": None,
    "OCR_DPI": 300,
    # Horas que se conservan los PDF subidos pero no confirmados.
    "HORAS_TEMPORALES": 24,
    "HOSTS_PERMITIDOS": ["127.0.0.1", "localhost"],
}

_TIPOS = {
    "MAX_TAMANO_PDF_MB": int,
    "MAX_PAGINAS_PDF": int,
    "OCR_DPI": int,
    "HORAS_TEMPORALES": int,
    "OCR_HABILITADO": lambda v: str(v).strip().lower() in ("1", "true", "si", "sí", "yes"),
    "HOSTS_PERMITIDOS": lambda v: [h.strip() for h in str(v).split(",") if h.strip()],
}


def cargar_config(overrides: dict | None = None) -> dict:
    cfg = dict(_DEFAULTS)
    archivo = INSTANCE_DIR / "config.py"
    if archivo.is_file():
        valores = runpy.run_path(str(archivo))
        cfg.update({k: v for k, v in valores.items() if k.isupper()})
    for clave in _DEFAULTS:
        valor = os.environ.get(f"FACTURAS_{clave}")
        if valor is not None:
            conv = _TIPOS.get(clave)
            cfg[clave] = conv(valor) if conv else valor
    if overrides:
        cfg.update(overrides)

    data_dir = Path(cfg["DATA_DIR"]).resolve()
    cfg["DATA_DIR"] = str(data_dir)
    if not cfg["DATABASE_URL"]:
        cfg["DATABASE_URL"] = "sqlite:///" + (data_dir / "facturas.db").as_posix()
    if cfg["CUIT_PROPIO"]:
        cfg["CUIT_PROPIO"] = "".join(ch for ch in str(cfg["CUIT_PROPIO"]) if ch.isdigit()) or None
    return cfg
