"""Resumen mensual calculado siempre desde los registros guardados en la base."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..dominio.calculos import RegistroResumen, consolidar
from ..dominio.esquema import a_dict
from ..modelos import Periodo
from . import facturas as svc_facturas
from . import periodos


def _registros(sesion: Session, anio: int, mes: int):
    lista = svc_facturas.listar(sesion, anio, mes)
    lista.sort(key=lambda f: (f.fecha_emision, f.id))
    return lista


def calcular(sesion: Session, anio: int, mes: int) -> dict:
    facturas = _registros(sesion, anio, mes)
    resumen = consolidar([RegistroResumen(id=f.id, datos=svc_facturas.datos_desde_modelo(f),
                                          estado=f.estado_revision) for f in facturas])
    periodo = periodos.obtener(sesion, anio, mes)
    resumen["periodo"] = f"{anio:04d}-{mes:02d}"
    resumen["estado"] = periodo.estado if periodo else periodos.ABIERTO
    resumen["integridad"] = periodos.verificar_integridad(sesion, periodo) if periodo else {"cerrado": False}
    return resumen


def detalle(sesion: Session, anio: int, mes: int) -> list[dict]:
    return [svc_facturas.serializar(f) for f in _registros(sesion, anio, mes)]


def instantanea(sesion: Session, periodo: Periodo) -> dict:
    """Datos que identifican el estado del período (sin marcas de tiempo)."""
    facturas = _registros(sesion, periodo.anio, periodo.mes)
    registros = [RegistroResumen(id=f.id, datos=svc_facturas.datos_desde_modelo(f), estado=f.estado_revision)
                 for f in facturas]
    return {
        "periodo": periodo.clave,
        "resumen": consolidar(registros),
        "facturas": [{"id": r.id, "estado": r.estado, "datos": a_dict(r.datos)} for r in registros],
    }
