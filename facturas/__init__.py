"""Gestión de facturas de compra: lectura de PDF, registro y consolidación mensual."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g

from .config import cargar_config
from .db import crear_engine, crear_fabrica, migrar
from .servicios.documentos import Almacen

log = logging.getLogger(__name__)


def _configurar_logs(data_dir: str) -> None:
    carpeta = Path(data_dir) / "logs"
    carpeta.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger("facturas")
    if any(isinstance(h, RotatingFileHandler) for h in raiz.handlers):
        return
    manejador = RotatingFileHandler(carpeta / "facturas.log", maxBytes=2_000_000, backupCount=5,
                                    encoding="utf-8")
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz.addHandler(manejador)
    raiz.setLevel(logging.INFO)


def create_app(overrides: dict | None = None) -> Flask:
    cfg = cargar_config(overrides)
    app = Flask(__name__, instance_path=str(Path(__file__).resolve().parent.parent / "instance"))
    app.config.update(cfg)
    app.config["MAX_CONTENT_LENGTH"] = int(cfg["MAX_TAMANO_PDF_MB"]) * 1024 * 1024 + 64 * 1024
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    _configurar_logs(cfg["DATA_DIR"])
    engine = crear_engine(cfg["DATABASE_URL"])
    migrar(engine)
    almacen = Almacen(cfg["DATA_DIR"])
    borrados = almacen.limpiar_temporales(int(cfg["HORAS_TEMPORALES"]))
    if borrados:
        log.info("Se eliminaron %s archivos temporales vencidos", borrados)

    app.extensions["facturas"] = {"engine": engine, "fabrica": crear_fabrica(engine), "almacen": almacen}

    @app.teardown_appcontext
    def _cerrar_sesion(_exc):
        sesion = g.pop("sesion", None)
        if sesion is not None:
            sesion.close()

    from .web.api import bp as api_bp
    from .web.ui import bp as ui_bp
    from .web.seguridad import registrar as registrar_seguridad

    registrar_seguridad(app)
    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    log.info("Aplicación iniciada. Base de datos: %s", cfg["DATABASE_URL"])
    return app
