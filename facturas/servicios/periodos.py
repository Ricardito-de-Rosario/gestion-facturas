"""Períodos mensuales: apertura implícita, cierre con instantánea y reapertura controlada."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import transaccion
from ..errores import ErrorValidacion, NoEncontrado, OperacionNoPermitida, PeriodoCerrado
from ..modelos import CierrePeriodo, Factura, Periodo
from .comun import a_json, auditar

ABIERTO, CERRADO = "ABIERTO", "CERRADO"
MIN_MOTIVO = 10


def parse_clave(clave: str) -> tuple[int, int]:
    try:
        anio, mes = (int(x) for x in str(clave).split("-"))
    except ValueError:
        raise ErrorValidacion({"periodo": "Período inválido (AAAA-MM)"}) from None
    validar_anio_mes(anio, mes)
    return anio, mes


def validar_anio_mes(anio: int, mes: int) -> None:
    if not (2000 <= anio <= 2100 and 1 <= mes <= 12):
        raise ErrorValidacion({"periodo": "Período inválido"})


def obtener(sesion: Session, anio: int, mes: int) -> Periodo | None:
    return sesion.scalar(select(Periodo).where(Periodo.anio == anio, Periodo.mes == mes))


def obtener_o_crear(sesion: Session, anio: int, mes: int) -> Periodo:
    validar_anio_mes(anio, mes)
    periodo = obtener(sesion, anio, mes)
    if periodo is None:
        periodo = Periodo(anio=anio, mes=mes, estado=ABIERTO)
        sesion.add(periodo)
        sesion.flush()
    return periodo


def exigir_abierto(periodo: Periodo) -> None:
    if periodo.estado == CERRADO:
        raise PeriodoCerrado(
            f"El período {periodo.clave} está cerrado. Para modificarlo, reábralo indicando el motivo.")


def _ultimo_cierre(periodo: Periodo) -> CierrePeriodo | None:
    cierres = [c for c in periodo.cierres if c.accion == "CIERRE"]
    return cierres[-1] if cierres else None


def _hash(instantanea: dict) -> str:
    return hashlib.sha256(a_json(instantanea, canonico=True).encode("utf-8")).hexdigest()


def cerrar(sesion: Session, anio: int, mes: int) -> CierrePeriodo:
    from .resumen import instantanea

    with transaccion(sesion):
        periodo = obtener_o_crear(sesion, anio, mes)
        if periodo.estado == CERRADO:
            raise OperacionNoPermitida(f"El período {periodo.clave} ya está cerrado")
        pendientes = sesion.scalar(select(func.count()).select_from(Factura).where(
            Factura.periodo_id == periodo.id, Factura.estado_revision != "VERIFICADA"))
        if pendientes:
            raise OperacionNoPermitida(
                f"Hay {pendientes} factura(s) pendiente(s) de revisión en {periodo.clave}. "
                "Revíselas antes de cerrar el período.")
        foto = instantanea(sesion, periodo)
        version = 1 + sum(1 for c in periodo.cierres if c.accion == "CIERRE")
        cierre = CierrePeriodo(periodo=periodo, version=version, accion="CIERRE",
                               resumen_json=a_json(foto, canonico=True), hash_sha256=_hash(foto))
        sesion.add(cierre)
        periodo.estado = CERRADO
        sesion.flush()
        auditar(sesion, "periodo", periodo.id, "CERRAR",
                {"periodo": periodo.clave, "version": version, "hash": cierre.hash_sha256})
    return cierre


def reabrir(sesion: Session, anio: int, mes: int, motivo: str) -> CierrePeriodo:
    motivo = (motivo or "").strip()
    if len(motivo) < MIN_MOTIVO:
        raise ErrorValidacion({"motivo": f"Indique el motivo de la reapertura (mínimo {MIN_MOTIVO} caracteres)"})
    with transaccion(sesion):
        periodo = obtener(sesion, anio, mes)
        if periodo is None:
            raise NoEncontrado("El período no existe")
        if periodo.estado != CERRADO:
            raise OperacionNoPermitida(f"El período {periodo.clave} no está cerrado")
        ultimo = _ultimo_cierre(periodo)
        registro = CierrePeriodo(periodo=periodo, version=ultimo.version if ultimo else 0,
                                 accion="REAPERTURA", motivo=motivo[:2000])
        sesion.add(registro)
        periodo.estado = ABIERTO
        sesion.flush()
        auditar(sesion, "periodo", periodo.id, "REABRIR", {"periodo": periodo.clave, "motivo": motivo})
    return registro


def historial(sesion: Session, anio: int, mes: int) -> list[dict]:
    periodo = obtener(sesion, anio, mes)
    if periodo is None:
        return []
    return [{"version": c.version, "accion": c.accion, "fecha": c.fecha.isoformat(sep=" "),
             "motivo": c.motivo, "hash": c.hash_sha256} for c in periodo.cierres]


def verificar_integridad(sesion: Session, periodo: Periodo) -> dict:
    """Compara los datos actuales de un período cerrado con la instantánea del cierre."""
    from .resumen import instantanea

    ultimo = _ultimo_cierre(periodo)
    if periodo.estado != CERRADO or ultimo is None:
        return {"cerrado": False}
    actual = _hash(instantanea(sesion, periodo))
    return {"cerrado": True, "version": ultimo.version, "fecha_cierre": ultimo.fecha.isoformat(sep=" "),
            "hash_cierre": ultimo.hash_sha256, "hash_actual": actual,
            "integro": actual == ultimo.hash_sha256}


def resumen_de_cierre(periodo: Periodo) -> dict | None:
    ultimo = _ultimo_cierre(periodo)
    return json.loads(ultimo.resumen_json) if ultimo and ultimo.resumen_json else None


def listar(sesion: Session) -> list[dict]:
    filas = sesion.execute(
        select(Periodo, func.count(Factura.id))
        .outerjoin(Factura, Factura.periodo_id == Periodo.id)
        .group_by(Periodo.id)
        .order_by(Periodo.anio.desc(), Periodo.mes.desc())
    ).all()
    return [{"periodo": p.clave, "anio": p.anio, "mes": p.mes, "estado": p.estado, "cantidad": n}
            for p, n in filas]
