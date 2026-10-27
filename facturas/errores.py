"""Excepciones de negocio. La capa web las traduce a respuestas HTTP."""
from __future__ import annotations


class ErrorAplicacion(Exception):
    codigo_http = 400

    def __init__(self, mensaje: str, **detalle):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalle = detalle


class ArchivoInvalido(ErrorAplicacion):
    codigo_http = 400


class ErrorValidacion(ErrorAplicacion):
    """Datos de entrada mal formados. ``errores`` es {campo: mensaje}."""

    codigo_http = 422

    def __init__(self, errores: dict[str, str], mensaje: str = "Hay datos inválidos"):
        super().__init__(mensaje, errores=errores)
        self.errores = errores


class Duplicado(ErrorAplicacion):
    codigo_http = 409


class PeriodoCerrado(ErrorAplicacion):
    codigo_http = 423


class NoEncontrado(ErrorAplicacion):
    codigo_http = 404


class OperacionNoPermitida(ErrorAplicacion):
    codigo_http = 409
