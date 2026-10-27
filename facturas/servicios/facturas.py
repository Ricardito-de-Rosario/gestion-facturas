"""Alta, modificación, baja, consulta y control de duplicados de facturas."""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..db import transaccion
from ..dominio import calculos
from ..dominio.esquema import DatosFactura, Descuento, LineaIva, Tributo, a_dict
from ..dominio.normalizar import formatear_cuit
from ..errores import Duplicado, ErrorValidacion, NoEncontrado
from ..modelos import (
    Documento, Factura, FacturaDescuento, FacturaIva, FacturaTributo, Periodo, Proveedor,
)
from . import periodos
from .comun import auditar
from .documentos import Almacen, borrar_si_existe

log = logging.getLogger(__name__)


def _opciones():
    return (selectinload(Factura.proveedor), selectinload(Factura.periodo),
            selectinload(Factura.documento), selectinload(Factura.iva),
            selectinload(Factura.descuentos), selectinload(Factura.tributos))


# ----------------------------------------------------------------- conversión
def datos_desde_modelo(f: Factura) -> DatosFactura:
    p = f.proveedor
    return DatosFactura(
        fecha_emision=f.fecha_emision, periodo=f.periodo.clave if f.periodo else None,
        tipo_codigo=f.tipo_codigo, tipo_letra=f.tipo_letra, tipo_clase=f.tipo_clase,
        punto_venta=f.punto_venta, numero=f.numero, cae=f.cae, cae_vencimiento=f.cae_vencimiento,
        emisor_cuit=p.cuit if p else None, emisor_razon_social=p.razon_social if p else None,
        emisor_domicilio=p.domicilio if p else None, emisor_localidad=p.localidad if p else None,
        emisor_provincia=p.provincia if p else None, emisor_condicion_iva=p.condicion_iva if p else None,
        moneda=f.moneda, cotizacion=f.cotizacion, condicion_venta=f.condicion_venta,
        neto_gravado=f.neto_gravado, no_gravado=f.no_gravado, exento=f.exento, total=f.total,
        iva=[LineaIva(alicuota=x.alicuota, importe=x.importe, base=x.base_imponible) for x in f.iva],
        descuentos=[Descuento(importe=x.importe, descripcion=x.descripcion, porcentaje=x.porcentaje,
                              base=x.base, origen=x.origen) for x in f.descuentos],
        tributos=[Tributo(tipo=x.tipo, importe=x.importe, descripcion=x.descripcion,
                          alicuota=x.alicuota, base=x.base) for x in f.tributos],
    )


def qr_de(f: Factura) -> dict | None:
    return json.loads(f.documento.qr_json) if f.documento and f.documento.qr_json else None


def serializar(f: Factura) -> dict:
    d = datos_desde_modelo(f)
    s = d.signo
    cero = calculos.CERO

    def firmado(v):
        return str(s * v + cero) if v is not None else None  # "+ cero" evita "-0.00"

    return {
        "id": f.id,
        "datos": a_dict(d),
        "estado_revision": f.estado_revision,
        "revision_manual": f.revision_manual,
        "observaciones": json.loads(f.observaciones) if f.observaciones else [],
        "campos": json.loads(f.campos_extraccion) if f.campos_extraccion else {},
        "metodo_extraccion": f.metodo_extraccion,
        "periodo": f.periodo.clave,
        "periodo_cerrado": f.periodo.estado == periodos.CERRADO,
        "documento": ({"id": f.documento.id, "nombre_original": f.documento.nombre_original,
                       "paginas": f.documento.paginas} if f.documento else None),
        "creado_en": f.creado_en.isoformat(sep=" "),
        "modificado_en": f.modificado_en.isoformat(sep=" "),
        "signo": s,
        "firmado": {
            "neto_gravado": firmado(d.neto_gravado),
            "no_gravado_exento": firmado((d.no_gravado or cero) + (d.exento or cero)
                                         if d.no_gravado is not None or d.exento is not None else None),
            "iva_total": firmado(d.iva_total),
            "otros_tributos": firmado(d.otros_tributos_total),
            "descuento_total": firmado(d.descuento_total),
            "total": firmado(d.total),
        },
        "emisor_cuit_formateado": formatear_cuit(d.emisor_cuit) if d.emisor_cuit else None,
    }


# ----------------------------------------------------------------- consultas
def obtener(sesion: Session, factura_id: int) -> Factura:
    f = sesion.scalar(select(Factura).options(*_opciones()).where(Factura.id == factura_id))
    if f is None:
        raise NoEncontrado("La factura no existe")
    return f


def listar(sesion: Session, anio: int | None = None, mes: int | None = None) -> list[Factura]:
    q = select(Factura).options(*_opciones()).join(Factura.periodo)
    if anio is not None and mes is not None:
        q = q.where(Periodo.anio == anio, Periodo.mes == mes)
    q = q.order_by(Factura.fecha_emision.desc(), Factura.id.desc())
    return list(sesion.scalars(q))


def proveedor_conocido(sesion: Session):
    def buscar(cuit: str) -> dict | None:
        p = sesion.scalar(select(Proveedor).where(Proveedor.cuit == cuit))
        if p is None:
            return None
        return {"razon_social": p.razon_social, "domicilio": p.domicilio, "localidad": p.localidad,
                "provincia": p.provincia, "condicion_iva": p.condicion_iva}
    return buscar


def buscar_duplicados(sesion: Session, datos: DatosFactura, *, excluir_id: int | None = None,
                      sha256: str | None = None) -> list[dict]:
    """Facturas ya registradas con la misma identificación fiscal o el mismo archivo."""
    encontrados: dict[int, dict] = {}
    if datos.emisor_cuit and datos.punto_venta is not None and datos.numero is not None \
            and (datos.tipo_codigo or datos.tipo_letra):
        q = (select(Factura).options(*_opciones()).join(Factura.proveedor)
             .where(Proveedor.cuit == datos.emisor_cuit, Factura.punto_venta == datos.punto_venta,
                    Factura.numero == datos.numero))
        if datos.tipo_codigo:
            q = q.where(Factura.tipo_codigo == datos.tipo_codigo)
        else:
            q = q.where(Factura.tipo_letra == datos.tipo_letra, Factura.tipo_clase == datos.tipo_clase)
        for f in sesion.scalars(q):
            encontrados[f.id] = {"id": f.id, "motivo": "Mismo emisor, tipo, punto de venta y número"}
    if sha256:
        q = select(Factura).join(Factura.documento).where(Documento.sha256 == sha256)
        for f in sesion.scalars(q):
            encontrados.setdefault(f.id, {"id": f.id, "motivo": "El mismo archivo PDF ya fue registrado"})
    encontrados.pop(excluir_id, None)
    resultado = []
    for item in encontrados.values():
        f = obtener(sesion, item["id"])
        d = datos_desde_modelo(f)
        item.update({"comprobante": f"{d.descripcion_tipo} {d.comprobante}",
                     "proveedor": d.emisor_razon_social, "fecha_emision": str(d.fecha_emision),
                     "periodo": f.periodo.clave})
        resultado.append(item)
    return resultado


# ----------------------------------------------------------------- escritura
def _proveedor(sesion: Session, d: DatosFactura) -> Proveedor | None:
    p = None
    if d.emisor_cuit:
        p = sesion.scalar(select(Proveedor).where(Proveedor.cuit == d.emisor_cuit))
    elif d.emisor_razon_social:
        p = sesion.scalar(select(Proveedor).where(Proveedor.cuit.is_(None),
                                                  Proveedor.razon_social == d.emisor_razon_social))
    else:
        return None
    if p is None:
        p = Proveedor(cuit=d.emisor_cuit)
        sesion.add(p)
    # Los datos más recientes del proveedor prevalecen, pero nunca se borra un dato con None.
    for campo, valor in (("razon_social", d.emisor_razon_social), ("domicilio", d.emisor_domicilio),
                         ("localidad", d.emisor_localidad), ("provincia", d.emisor_provincia),
                         ("condicion_iva", d.emisor_condicion_iva)):
        if valor:
            setattr(p, campo, valor)
    return p


def _aplicar(f: Factura, d: DatosFactura) -> None:
    for campo in ("fecha_emision", "tipo_codigo", "tipo_letra", "tipo_clase", "punto_venta", "numero",
                  "cae", "cae_vencimiento", "moneda", "cotizacion", "condicion_venta",
                  "neto_gravado", "no_gravado", "exento", "total"):
        setattr(f, campo, getattr(d, campo))
    f.iva = [FacturaIva(alicuota=x.alicuota, base_imponible=x.base, importe=x.importe) for x in d.iva]
    f.descuentos = [FacturaDescuento(descripcion=x.descripcion, porcentaje=x.porcentaje, base=x.base,
                                     importe=x.importe, origen=x.origen) for x in d.descuentos]
    f.tributos = [FacturaTributo(tipo=x.tipo, descripcion=x.descripcion, alicuota=x.alicuota,
                                 base=x.base, importe=x.importe) for x in d.tributos]


def _periodo_de(d: DatosFactura) -> tuple[int, int]:
    if d.fecha_emision is None:
        raise ErrorValidacion({"fecha_emision": "Indique la fecha de emisión para poder registrar la factura"})
    return periodos.parse_clave(d.periodo_efectivo)


def _revisar(d: DatosFactura, cuit_propio, qr, aceptar: bool):
    problemas = calculos.validar(d, cuit_propio=cuit_propio, qr=qr)
    estado = calculos.estado_revision(problemas, aceptar)
    manual = estado == "VERIFICADA" and any(p.nivel == calculos.ADVERTENCIA for p in problemas)
    return problemas, estado, manual


def crear(sesion: Session, almacen: Almacen, cfg: dict, d: DatosFactura, *, token: str | None = None,
          aceptar_advertencias: bool = False, campos: dict | None = None) -> Factura:
    anio, mes = _periodo_de(d)
    temporal = almacen.obtener_temporal(token) if token else None
    nuevo_archivo = None
    try:
        with transaccion(sesion):
            periodo = periodos.obtener_o_crear(sesion, anio, mes)
            periodos.exigir_abierto(periodo)
            duplicados = buscar_duplicados(sesion, d, sha256=temporal.sha256 if temporal else None)
            if duplicados:
                raise Duplicado("La factura ya está registrada", duplicados=duplicados)
            problemas, estado, manual = _revisar(d, cfg.get("CUIT_PROPIO"),
                                                 temporal.qr if temporal else None, aceptar_advertencias)
            f = Factura(periodo=periodo, proveedor=_proveedor(sesion, d), estado_revision=estado,
                        revision_manual=manual,
                        observaciones=json.dumps([p.a_dict() for p in problemas], ensure_ascii=False),
                        campos_extraccion=json.dumps(campos, ensure_ascii=False) if campos else None,
                        metodo_extraccion=temporal.metodo if temporal else "MANUAL")
            if temporal:
                f.documento, nuevo_archivo = almacen.consolidar(sesion, temporal)
            _aplicar(f, d)
            sesion.add(f)
            sesion.flush()
            auditar(sesion, "factura", f.id, "CREAR", {"datos": a_dict(d), "estado": estado})
    except IntegrityError as exc:
        borrar_si_existe(nuevo_archivo)
        log.warning("Conflicto de integridad al crear factura: %s", exc.orig)
        raise Duplicado("La factura ya está registrada (control de la base de datos)") from exc
    except BaseException:
        borrar_si_existe(nuevo_archivo)
        raise
    if temporal:
        almacen.descartar_temporal(temporal.token)
    log.info("Factura %s registrada (%s)", f.id, f.estado_revision)
    return obtener(sesion, f.id)


def actualizar(sesion: Session, cfg: dict, factura_id: int, d: DatosFactura, *,
               aceptar_advertencias: bool = False) -> Factura:
    anio, mes = _periodo_de(d)
    try:
        with transaccion(sesion):
            f = obtener(sesion, factura_id)
            periodos.exigir_abierto(f.periodo)
            destino = periodos.obtener_o_crear(sesion, anio, mes)
            periodos.exigir_abierto(destino)
            duplicados = buscar_duplicados(sesion, d, excluir_id=f.id)
            if duplicados:
                raise Duplicado("Ya existe otra factura con esa identificación", duplicados=duplicados)
            antes = a_dict(datos_desde_modelo(f))
            problemas, estado, manual = _revisar(d, cfg.get("CUIT_PROPIO"), qr_de(f), aceptar_advertencias)
            f.periodo = destino
            f.proveedor = _proveedor(sesion, d)
            f.estado_revision = estado
            f.revision_manual = manual
            f.observaciones = json.dumps([p.a_dict() for p in problemas], ensure_ascii=False)
            _aplicar(f, d)
            sesion.flush()
            auditar(sesion, "factura", f.id, "MODIFICAR",
                    {"antes": antes, "despues": a_dict(d), "estado": estado})
    except IntegrityError as exc:
        raise Duplicado("Ya existe otra factura con esa identificación") from exc
    return obtener(sesion, factura_id)


def eliminar(sesion: Session, factura_id: int) -> None:
    with transaccion(sesion):
        f = obtener(sesion, factura_id)
        periodos.exigir_abierto(f.periodo)
        # El PDF original se conserva en el almacén (y en la tabla documentos) para auditoría.
        auditar(sesion, "factura", f.id, "ELIMINAR",
                {"datos": a_dict(datos_desde_modelo(f)), "estado": f.estado_revision,
                 "documento_id": f.documento_id})
        sesion.delete(f)
    log.info("Factura %s eliminada", factura_id)
