"""Acceso a la base de datos (SQLAlchemy 2) y migraciones (Alembic)."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import BASE_DIR


class Base(DeclarativeBase):
    pass


class DecimalFijo(TypeDecorator):
    """Decimal exacto guardado como entero escalado (centavos, por ejemplo).

    SQLite no tiene tipo decimal: NUMERIC se guarda como coma flotante. Guardar
    enteros evita cualquier error de redondeo. Rechaza valores con más
    decimales que la escala en lugar de redondearlos en silencio.
    """

    impl = BigInteger
    cache_ok = True

    def __init__(self, escala: int = 2):
        super().__init__()
        self.escala = escala
        self._factor = Decimal(10) ** escala
        self._cuanto = Decimal(1).scaleb(-escala)

    def process_bind_param(self, valor, dialecto):
        if valor is None:
            return None
        valor = Decimal(str(valor)) if not isinstance(valor, Decimal) else valor
        escalado = valor * self._factor
        if escalado != escalado.to_integral_value():
            raise ValueError(f"{valor} tiene más de {self.escala} decimales")
        return int(escalado)

    def process_result_value(self, valor, dialecto):
        if valor is None:
            return None
        return (Decimal(valor) / self._factor).quantize(self._cuanto)


def crear_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(conexion, _):
            cur = conexion.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    return engine


def crear_fabrica(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def transaccion(sesion: Session):
    """Confirma si todo sale bien; si hay cualquier error, revierte."""
    try:
        yield sesion
        sesion.commit()
    except BaseException:
        sesion.rollback()
        raise


def migrar(engine: Engine) -> None:
    """Aplica las migraciones de Alembic pendientes (``upgrade head``)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conexion:
        cfg.attributes["connection"] = conexion
        command.upgrade(cfg, "head")
