"""Esquema inicial: proveedores, períodos, documentos, facturas y auditoría.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "proveedores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cuit", sa.String(11), nullable=True),
        sa.Column("razon_social", sa.String(200), nullable=True),
        sa.Column("domicilio", sa.String(300), nullable=True),
        sa.Column("localidad", sa.String(120), nullable=True),
        sa.Column("provincia", sa.String(60), nullable=True),
        sa.Column("condicion_iva", sa.String(60), nullable=True),
        sa.Column("creado_en", sa.DateTime(), nullable=False),
        sa.Column("modificado_en", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cuit"),
    )
    op.create_table(
        "periodos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("anio", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(10), nullable=False),
        sa.UniqueConstraint("anio", "mes", name="uq_periodos_anio_mes"),
        sa.CheckConstraint("mes BETWEEN 1 AND 12", name="ck_periodos_mes"),
        sa.CheckConstraint("estado IN ('ABIERTO', 'CERRADO')", name="ck_periodos_estado"),
    )
    op.create_table(
        "cierres_periodo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("periodo_id", sa.Integer(), sa.ForeignKey("periodos.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("accion", sa.String(12), nullable=False),
        sa.Column("fecha", sa.DateTime(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("resumen_json", sa.Text(), nullable=True),
        sa.Column("hash_sha256", sa.String(64), nullable=True),
        sa.CheckConstraint("accion IN ('CIERRE', 'REAPERTURA')", name="ck_cierres_accion"),
    )
    op.create_index("ix_cierres_periodo_periodo_id", "cierres_periodo", ["periodo_id"])
    op.create_table(
        "documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("nombre_original", sa.String(255), nullable=False),
        sa.Column("ruta_relativa", sa.String(255), nullable=False),
        sa.Column("tamano_bytes", sa.Integer(), nullable=False),
        sa.Column("paginas", sa.Integer(), nullable=False),
        sa.Column("metodo_extraccion", sa.String(20), nullable=False),
        sa.Column("qr_json", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("sha256"),
        sa.UniqueConstraint("ruta_relativa"),
    )
    op.create_table(
        "facturas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proveedor_id", sa.Integer(), sa.ForeignKey("proveedores.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("periodo_id", sa.Integer(), sa.ForeignKey("periodos.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("documento_id", sa.Integer(), sa.ForeignKey("documentos.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("fecha_emision", sa.Date(), nullable=False),
        sa.Column("tipo_codigo", sa.Integer(), nullable=True),
        sa.Column("tipo_letra", sa.String(1), nullable=True),
        sa.Column("tipo_clase", sa.String(20), nullable=True),
        sa.Column("punto_venta", sa.Integer(), nullable=True),
        sa.Column("numero", sa.BigInteger(), nullable=True),
        sa.Column("cae", sa.String(14), nullable=True),
        sa.Column("cae_vencimiento", sa.Date(), nullable=True),
        sa.Column("moneda", sa.String(3), nullable=True),
        sa.Column("cotizacion", sa.BigInteger(), nullable=True),  # escala 6
        sa.Column("condicion_venta", sa.String(100), nullable=True),
        sa.Column("neto_gravado", sa.BigInteger(), nullable=True),  # centavos
        sa.Column("no_gravado", sa.BigInteger(), nullable=True),
        sa.Column("exento", sa.BigInteger(), nullable=True),
        sa.Column("total", sa.BigInteger(), nullable=True),
        sa.Column("estado_revision", sa.String(12), nullable=False),
        sa.Column("revision_manual", sa.Boolean(), nullable=False),
        sa.Column("observaciones", sa.Text(), nullable=True),
        sa.Column("campos_extraccion", sa.Text(), nullable=True),
        sa.Column("metodo_extraccion", sa.String(20), nullable=True),
        sa.Column("creado_en", sa.DateTime(), nullable=False),
        sa.Column("modificado_en", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("documento_id"),
        sa.UniqueConstraint("proveedor_id", "tipo_codigo", "punto_venta", "numero",
                            name="uq_facturas_comprobante"),
        sa.CheckConstraint("estado_revision IN ('PENDIENTE', 'VERIFICADA')", name="ck_facturas_estado"),
    )
    op.create_index("ix_facturas_proveedor_id", "facturas", ["proveedor_id"])
    op.create_index("ix_facturas_periodo_id", "facturas", ["periodo_id"])
    op.create_index("ix_facturas_fecha_emision", "facturas", ["fecha_emision"])
    op.create_table(
        "facturas_iva",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factura_id", sa.Integer(), sa.ForeignKey("facturas.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("alicuota", sa.BigInteger(), nullable=False),  # escala 2 (2100 = 21%)
        sa.Column("base_imponible", sa.BigInteger(), nullable=True),
        sa.Column("importe", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("factura_id", "alicuota", name="uq_facturas_iva_alicuota"),
    )
    op.create_index("ix_facturas_iva_factura_id", "facturas_iva", ["factura_id"])
    op.create_table(
        "facturas_descuentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factura_id", sa.Integer(), sa.ForeignKey("facturas.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("descripcion", sa.String(200), nullable=True),
        sa.Column("porcentaje", sa.BigInteger(), nullable=True),  # escala 4
        sa.Column("base", sa.BigInteger(), nullable=True),
        sa.Column("importe", sa.BigInteger(), nullable=False),
        sa.Column("origen", sa.String(10), nullable=False),
    )
    op.create_index("ix_facturas_descuentos_factura_id", "facturas_descuentos", ["factura_id"])
    op.create_table(
        "facturas_tributos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factura_id", sa.Integer(), sa.ForeignKey("facturas.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("descripcion", sa.String(200), nullable=True),
        sa.Column("alicuota", sa.BigInteger(), nullable=True),  # escala 4
        sa.Column("base", sa.BigInteger(), nullable=True),
        sa.Column("importe", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_facturas_tributos_factura_id", "facturas_tributos", ["factura_id"])
    op.create_table(
        "auditoria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fecha", sa.DateTime(), nullable=False),
        sa.Column("entidad", sa.String(30), nullable=False),
        sa.Column("entidad_id", sa.Integer(), nullable=True),
        sa.Column("accion", sa.String(30), nullable=False),
        sa.Column("detalle", sa.Text(), nullable=True),
    )
    op.create_index("ix_auditoria_entidad", "auditoria", ["entidad", "entidad_id"])


def downgrade():
    op.drop_table("auditoria")
    op.drop_table("facturas_tributos")
    op.drop_table("facturas_descuentos")
    op.drop_table("facturas_iva")
    op.drop_table("facturas")
    op.drop_table("documentos")
    op.drop_table("cierres_periodo")
    op.drop_table("periodos")
    op.drop_table("proveedores")
