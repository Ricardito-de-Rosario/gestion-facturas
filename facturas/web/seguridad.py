"""Protecciones para una aplicación local de un solo usuario.

* Sólo acepta peticiones dirigidas a los hosts permitidos (evita DNS rebinding).
* Las operaciones que modifican datos exigen la cabecera ``X-Requested-With``:
  un sitio web ajeno no puede enviarla sin una verificación CORS que aquí no se
  concede, lo que bloquea ataques CSRF.
* Cabeceras de seguridad (CSP estricta, sin contenido de terceros).
"""
from __future__ import annotations

from flask import Flask, abort, jsonify, request

CABECERA_CSRF = "X-Requested-With"
VALOR_CSRF = "facturas"


def registrar(app: Flask) -> None:
    hosts = {h.lower() for h in app.config["HOSTS_PERMITIDOS"]}

    @app.before_request
    def _verificar():
        host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
        if hosts and host not in hosts:
            abort(400, description="Host no permitido")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get(CABECERA_CSRF) != VALOR_CSRF:
                return jsonify({"error": "Petición rechazada (falta cabecera de seguridad)"}), 403
        return None

    @app.after_request
    def _cabeceras(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if resp.mimetype == "text/html":
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                "object-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
        return resp
