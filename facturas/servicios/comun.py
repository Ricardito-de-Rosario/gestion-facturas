"""Utilidades compartidas por los servicios: JSON con Decimal y auditoría."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..modelos import Auditoria


def _por_defecto(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    raise TypeError(f"No serializable: {type(valor).__name__}")


def a_json(valor, canonico: bool = False) -> str:
    if canonico:
        return json.dumps(valor, default=_por_defecto, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return json.dumps(valor, default=_por_defecto, ensure_ascii=False)


def auditar(sesion: Session, entidad: str, entidad_id: int | None, accion: str, detalle=None) -> None:
    sesion.add(Auditoria(entidad=entidad, entidad_id=entidad_id, accion=accion,
                         detalle=a_json(detalle) if detalle is not None else None))
