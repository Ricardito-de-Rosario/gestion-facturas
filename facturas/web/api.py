"""API JSON consumida por la interfaz web."""
from __future__ import annotations

import logging
from io import BytesIO

from flask import Blueprint, Response, current_app, g, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from ..dominio import comprobantes as cat
from ..dominio.calculos import validar
from ..dominio.esquema import a_dict, desde_dict
from ..dominio.normalizar import normalizar_alicuota
from ..errores import ArchivoInvalido, ErrorAplicacion, ErrorValidacion, NoEncontrado
from ..extraccion import procesar_pdf
from ..extraccion.documento import abrir_pdf, renderizar_png, tessdata_disponible
from ..informes.excel import generar_excel
from ..informes.pdf import generar_pdf
from ..servicios import facturas as svc
from ..servicios import periodos, resumen
from ..servicios.comun import a_json

log = logging.getLogger(__name__)
bp = Blueprint("api", __name__)

MAX_TEXTO_DEVUELTO = 30000


# ---------------------------------------------------------------- utilidades
def _ext():
    return current_app.extensions["facturas"]


def _sesion():
    if "sesion" not in g:
        g.sesion = _ext()["fabrica"]()
    return g.sesion


def _cfg():
    return current_app.config


def _json(obj, codigo=200):
    return Response(a_json(obj), status=codigo, mimetype="application/json")


def _cuerpo() -> dict:
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        raise ErrorValidacion({"cuerpo": "Se esperaba un objeto JSON"})
    return datos


def _periodo(clave: str) -> tuple[int, int]:
    return periodos.parse_clave(clave)


@bp.errorhandler(ErrorAplicacion)
def _error_aplicacion(exc: ErrorAplicacion):
    return _json({"error": exc.mensaje, **exc.detalle}, exc.codigo_http)


@bp.errorhandler(RequestEntityTooLarge)
def _muy_grande(_exc):
    return _json({"error": f"El archivo supera el máximo de {_cfg()['MAX_TAMANO_PDF_MB']} MB"}, 413)


@bp.errorhandler(HTTPException)
def _http(exc: HTTPException):
    return _json({"error": exc.description}, exc.code or 500)


@bp.errorhandler(Exception)
def _inesperado(exc: Exception):
    log.exception("Error inesperado")
    return _json({"error": "Error interno. Los registros ya confirmados no se modificaron. "
                           "Revise data/logs/facturas.log."}, 500)


# ---------------------------------------------------------------- configuración
@bp.get("/configuracion")
def configuracion():
    c = _cfg()
    ocr = bool(c["OCR_HABILITADO"]) and tessdata_disponible(c.get("TESSDATA_DIR")) is not None
    return _json({
        "cuit_propio": c.get("CUIT_PROPIO"), "ocr_disponible": ocr, "max_tamano_mb": c["MAX_TAMANO_PDF_MB"],
        "catalogos": {
            "letras": list(cat.LETRAS),
            "clases": cat.CLASES,
            "tipos": {str(k): {"letra": l, "clase": cl} for k, (l, cl) in cat.TIPOS.items()},
            "alicuotas": [str(normalizar_alicuota(a)) for a in cat.ALICUOTAS_IVA],
            "tributos": cat.TIPOS_TRIBUTO,
            "provincias": list(cat.PROVINCIAS),
            "condiciones_iva": ["IVA Responsable Inscripto", "Responsable Monotributo", "IVA Sujeto Exento",
                                "Consumidor Final", "IVA No Alcanzado"],
        },
    })


# ---------------------------------------------------------------- extracción
@bp.post("/extraer")
def extraer():
    archivo = request.files.get("archivo")
    if archivo is None:
        raise ArchivoInvalido("No se recibió ningún archivo")
    maximo = int(_cfg()["MAX_TAMANO_PDF_MB"]) * 1024 * 1024
    contenido = archivo.read(maximo + 1)
    if len(contenido) > maximo:
        raise RequestEntityTooLarge()
    s = _sesion()
    proc = procesar_pdf(contenido, _cfg(), svc.proveedor_conocido(s))
    qr = proc.qr.a_dict() if proc.qr else None
    tmp = _ext()["almacen"].guardar_temporal(contenido, archivo.filename or "documento.pdf",
                                              paginas=proc.paginas_total, metodo=proc.metodo, qr=qr)
    datos = proc.resultado.datos
    log.info("PDF '%s' leído (%s, %s pág.)", tmp.nombre_original, proc.metodo, proc.paginas_total)
    return _json({
        "token": tmp.token,
        "nombre": tmp.nombre_original,
        "paginas": proc.paginas_total,
        "paginas_procesadas": proc.paginas_procesadas,
        "metodo": proc.metodo,
        "datos": a_dict(datos),
        "campos": {k: c.a_dict() for k, c in proc.resultado.campos.items()},
        "advertencias": proc.resultado.advertencias,
        "problemas": [p.a_dict() for p in validar(datos, cuit_propio=_cfg().get("CUIT_PROPIO"), qr=qr)],
        "duplicados": svc.buscar_duplicados(s, datos, sha256=proc.sha256),
        "qr": qr,
        "texto": proc.texto[:MAX_TEXTO_DEVUELTO],
    })


@bp.delete("/temporal/<token>")
def descartar_temporal(token):
    _ext()["almacen"].descartar_temporal(token)
    return _json({"ok": True})


def _png(contenido: bytes, numero: int):
    doc = abrir_pdf(contenido, int(_cfg()["MAX_PAGINAS_PDF"]))
    try:
        png = renderizar_png(doc, numero)
    finally:
        doc.close()
    resp = send_file(BytesIO(png), mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@bp.get("/temporal/<token>/pagina/<int:numero>")
def pagina_temporal(token, numero):
    tmp = _ext()["almacen"].obtener_temporal(token)
    return _png(tmp.ruta.read_bytes(), numero)


@bp.get("/temporal/<token>/pdf")
def pdf_temporal(token):
    tmp = _ext()["almacen"].obtener_temporal(token)
    return send_file(tmp.ruta, mimetype="application/pdf", download_name=tmp.nombre_original)


@bp.post("/validar")
def validar_datos():
    cuerpo = _cuerpo()
    datos = desde_dict(cuerpo.get("datos") or {})
    s = _sesion()
    qr, sha = None, None
    factura_id = cuerpo.get("factura_id")
    if cuerpo.get("token"):
        tmp = _ext()["almacen"].obtener_temporal(cuerpo["token"])
        qr, sha = tmp.qr, tmp.sha256
    elif factura_id:
        qr = svc.qr_de(svc.obtener(s, int(factura_id)))
    problemas = validar(datos, cuit_propio=_cfg().get("CUIT_PROPIO"), qr=qr)
    return _json({
        "problemas": [p.a_dict() for p in problemas],
        "duplicados": svc.buscar_duplicados(s, datos, excluir_id=int(factura_id) if factura_id else None,
                                            sha256=sha),
        "derivados": {k: v for k, v in a_dict(datos).items()
                      if k in ("iva_total", "otros_tributos_total", "descuento_total", "descuento_porcentaje",
                               "periodo", "descripcion_tipo")},
    })


# ---------------------------------------------------------------- facturas
@bp.get("/facturas")
def listar_facturas():
    s = _sesion()
    if request.args.get("todos") == "1":
        lista = svc.listar(s)
    else:
        anio, mes = _periodo(request.args.get("periodo", ""))
        lista = svc.listar(s, anio, mes)
    return _json([svc.serializar(f) for f in lista])


@bp.post("/facturas")
def crear_factura():
    cuerpo = _cuerpo()
    datos = desde_dict(cuerpo.get("datos") or {})
    campos = cuerpo.get("campos") if isinstance(cuerpo.get("campos"), dict) else None
    f = svc.crear(_sesion(), _ext()["almacen"], _cfg(), datos, token=cuerpo.get("token") or None,
                  aceptar_advertencias=bool(cuerpo.get("aceptar_advertencias")), campos=campos)
    return _json(svc.serializar(f), 201)


@bp.get("/facturas/<int:factura_id>")
def obtener_factura(factura_id):
    return _json(svc.serializar(svc.obtener(_sesion(), factura_id)))


@bp.put("/facturas/<int:factura_id>")
def actualizar_factura(factura_id):
    cuerpo = _cuerpo()
    datos = desde_dict(cuerpo.get("datos") or {})
    f = svc.actualizar(_sesion(), _cfg(), factura_id, datos,
                       aceptar_advertencias=bool(cuerpo.get("aceptar_advertencias")))
    return _json(svc.serializar(f))


@bp.delete("/facturas/<int:factura_id>")
def eliminar_factura(factura_id):
    svc.eliminar(_sesion(), factura_id)
    return _json({"ok": True})


def _documento(factura_id):
    f = svc.obtener(_sesion(), factura_id)
    if f.documento is None:
        raise NoEncontrado("La factura no tiene un PDF asociado (carga manual)")
    return f.documento, _ext()["almacen"].ruta_documento(f.documento)


@bp.get("/facturas/<int:factura_id>/pdf")
def pdf_factura(factura_id):
    doc, ruta = _documento(factura_id)
    return send_file(ruta, mimetype="application/pdf", download_name=doc.nombre_original)


@bp.get("/facturas/<int:factura_id>/pagina/<int:numero>")
def pagina_factura(factura_id, numero):
    _doc, ruta = _documento(factura_id)
    return _png(ruta.read_bytes(), numero)


# ---------------------------------------------------------------- resumen y períodos
@bp.get("/resumen")
def obtener_resumen():
    anio, mes = _periodo(request.args.get("periodo", ""))
    return _json(resumen.calcular(_sesion(), anio, mes))


@bp.get("/periodos")
def listar_periodos():
    return _json(periodos.listar(_sesion()))


@bp.post("/periodos/<clave>/cerrar")
def cerrar_periodo(clave):
    anio, mes = _periodo(clave)
    cierre = periodos.cerrar(_sesion(), anio, mes)
    return _json({"ok": True, "version": cierre.version, "hash": cierre.hash_sha256})


@bp.post("/periodos/<clave>/reabrir")
def reabrir_periodo(clave):
    anio, mes = _periodo(clave)
    periodos.reabrir(_sesion(), anio, mes, str(_cuerpo().get("motivo") or ""))
    return _json({"ok": True})


@bp.get("/periodos/<clave>/historial")
def historial_periodo(clave):
    anio, mes = _periodo(clave)
    return _json(periodos.historial(_sesion(), anio, mes))


# ---------------------------------------------------------------- informes
@bp.get("/informes/<clave>.xlsx")
def informe_excel(clave):
    anio, mes = _periodo(clave)
    s = _sesion()
    contenido = generar_excel(resumen.calcular(s, anio, mes), resumen.detalle(s, anio, mes))
    return send_file(BytesIO(contenido), as_attachment=True, download_name=f"facturas_{clave}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.get("/informes/<clave>.pdf")
def informe_pdf(clave):
    anio, mes = _periodo(clave)
    s = _sesion()
    contenido = generar_pdf(resumen.calcular(s, anio, mes), resumen.detalle(s, anio, mes))
    return send_file(BytesIO(contenido), as_attachment=True, download_name=f"informe_{clave}.pdf",
                     mimetype="application/pdf")
