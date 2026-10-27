from flask import Blueprint, render_template

bp = Blueprint("ui", __name__)


@bp.get("/")
def inicio():
    return render_template("index.html")
