"""Crea una base SQLite de demostración con comprobantes completamente sintéticos."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from logging.handlers import RotatingFileHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from facturas import create_app
from facturas.dominio.esquema import desde_dict
from facturas.servicios import facturas as servicio_facturas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "facturas-demo.db",
        help="Ruta de salida (por defecto: examples/facturas-demo.db)",
    )
    destino = parser.parse_args().output.resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        raise SystemExit(f"No se sobrescribió la base existente: {destino}")

    with tempfile.TemporaryDirectory(prefix="facturas-demo-") as temporal:
        temporal_path = Path(temporal)
        base_temporal = temporal_path / "facturas-demo.db"
        app = create_app({
            "DATABASE_URL": f"sqlite:///{base_temporal.as_posix()}",
            "DATA_DIR": str(temporal_path / "data"),
            "TESTING": True,
        })
        extensiones = app.extensions["facturas"]
        session = extensiones["fabrica"]()
        try:
            registros = (
                {
                    "fecha_emision": "2026-10-10",
                    "periodo": "2026-10",
                    "tipo_codigo": 1,
                    "tipo_letra": "A",
                    "tipo_clase": "FACTURA",
                    "punto_venta": 1,
                    "numero": 1001,
                    "emisor_razon_social": "Proveedor Ficticio Uno",
                    "emisor_localidad": "Ciudad de Prueba",
                    "emisor_provincia": "Santa Fe",
                    "moneda": "ARS",
                    "neto_gravado": "1000.00",
                    "total": "1210.00",
                    "iva": [{"alicuota": "21.00", "base": "1000.00", "importe": "210.00"}],
                },
                {
                    "fecha_emision": "2026-10-18",
                    "periodo": "2026-10",
                    "tipo_codigo": 11,
                    "tipo_letra": "C",
                    "tipo_clase": "FACTURA",
                    "punto_venta": 2,
                    "numero": 1002,
                    "emisor_razon_social": "Comercio Ficticio Dos",
                    "emisor_localidad": "Localidad de Prueba",
                    "emisor_provincia": "Córdoba",
                    "moneda": "ARS",
                    "no_gravado": "2500.00",
                    "total": "2500.00",
                },
            )
            for registro in registros:
                servicio_facturas.crear(
                    session,
                    extensiones["almacen"],
                    app.config,
                    desde_dict(registro),
                )
            session.commit()
        finally:
            session.close()
            extensiones["engine"].dispose()
            logger = logging.getLogger("facturas")
            for handler in tuple(logger.handlers):
                if isinstance(handler, RotatingFileHandler):
                    logger.removeHandler(handler)
                    handler.close()

        if destino.exists():
            raise SystemExit(f"No se sobrescribió la base existente: {destino}")
        shutil.copy2(base_temporal, destino)

    print(f"Base sintética creada: {destino}")


if __name__ == "__main__":
    main()
