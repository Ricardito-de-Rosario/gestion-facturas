"""Modelo de datos.

proveedores 1──n facturas n──1 periodos 1──n cierres_periodo
documentos  1──1 facturas 1──n facturas_iva / facturas_descuentos / facturas_tributos
auditoria: registro inmutable de altas, modificaciones, bajas, cierres y reaperturas.

Los totales derivados (IVA total, otros tributos, descuentos) no se guardan en
la factura: se calculan desde las tablas hijas para no duplicar información.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, DecimalFijo


def ahora() -> datetime:
    return datetime.now().replace(microsecond=0)


class Proveedor(Base):
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cuit: Mapped[str | None] = mapped_column(String(11), unique=True)
    razon_social: Mapped[str | None] = mapped_column(String(200))
    domicilio: Mapped[str | None] = mapped_column(String(300))
    localidad: Mapped[str | None] = mapped_column(String(120))
    provincia: Mapped[str | None] = mapped_column(String(60))
    condicion_iva: Mapped[str | None] = mapped_column(String(60))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    modificado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora, onupdate=ahora)

    facturas: Mapped[list[Factura]] = relationship(back_populates="proveedor")


class Periodo(Base):
    __tablename__ = "periodos"
    __table_args__ = (
        UniqueConstraint("anio", "mes", name="uq_periodos_anio_mes"),
        CheckConstraint("mes BETWEEN 1 AND 12", name="ck_periodos_mes"),
        CheckConstraint("estado IN ('ABIERTO', 'CERRADO')", name="ck_periodos_estado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    anio: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(10), default="ABIERTO")

    facturas: Mapped[list[Factura]] = relationship(back_populates="periodo")
    cierres: Mapped[list[CierrePeriodo]] = relationship(
        back_populates="periodo", order_by="CierrePeriodo.id")

    @property
    def clave(self) -> str:
        return f"{self.anio:04d}-{self.mes:02d}"


class CierrePeriodo(Base):
    __tablename__ = "cierres_periodo"
    __table_args__ = (
        CheckConstraint("accion IN ('CIERRE', 'REAPERTURA')", name="ck_cierres_accion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    periodo_id: Mapped[int] = mapped_column(ForeignKey("periodos.id", ondelete="RESTRICT"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    accion: Mapped[str] = mapped_column(String(12))
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    motivo: Mapped[str | None] = mapped_column(Text)
    # Instantánea del resumen al cerrar y su hash, para auditoría posterior.
    resumen_json: Mapped[str | None] = mapped_column(Text)
    hash_sha256: Mapped[str | None] = mapped_column(String(64))

    periodo: Mapped[Periodo] = relationship(back_populates="cierres")


class Documento(Base):
    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    nombre_original: Mapped[str] = mapped_column(String(255))
    # Ruta relativa a DATA_DIR/documentos; el nombre es un UUID, nunca el original.
    ruta_relativa: Mapped[str] = mapped_column(String(255), unique=True)
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    paginas: Mapped[int] = mapped_column(Integer)
    metodo_extraccion: Mapped[str] = mapped_column(String(20))
    qr_json: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    factura: Mapped[Factura | None] = relationship(back_populates="documento")


class Factura(Base):
    __tablename__ = "facturas"
    __table_args__ = (
        UniqueConstraint("proveedor_id", "tipo_codigo", "punto_venta", "numero",
                         name="uq_facturas_comprobante"),
        Index("ix_facturas_fecha_emision", "fecha_emision"),
        CheckConstraint("estado_revision IN ('PENDIENTE', 'VERIFICADA')", name="ck_facturas_estado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proveedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("proveedores.id", ondelete="RESTRICT"), index=True)
    periodo_id: Mapped[int] = mapped_column(ForeignKey("periodos.id", ondelete="RESTRICT"), index=True)
    documento_id: Mapped[int | None] = mapped_column(
        ForeignKey("documentos.id", ondelete="RESTRICT"), unique=True)

    fecha_emision: Mapped[date] = mapped_column(Date)
    tipo_codigo: Mapped[int | None] = mapped_column(Integer)
    tipo_letra: Mapped[str | None] = mapped_column(String(1))
    tipo_clase: Mapped[str | None] = mapped_column(String(20))
    punto_venta: Mapped[int | None] = mapped_column(Integer)
    numero: Mapped[int | None] = mapped_column(BigInteger)
    cae: Mapped[str | None] = mapped_column(String(14))
    cae_vencimiento: Mapped[date | None] = mapped_column(Date)
    moneda: Mapped[str | None] = mapped_column(String(3))
    cotizacion: Mapped[Decimal | None] = mapped_column(DecimalFijo(6))
    condicion_venta: Mapped[str | None] = mapped_column(String(100))

    neto_gravado: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))
    no_gravado: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))
    exento: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))
    total: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))

    estado_revision: Mapped[str] = mapped_column(String(12))
    # True si el usuario aceptó explícitamente advertencias al verificarla.
    revision_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    # Problemas detectados al guardar (JSON), para trazabilidad.
    observaciones: Mapped[str | None] = mapped_column(Text)
    # Origen y confianza de cada campo según el extractor (JSON).
    campos_extraccion: Mapped[str | None] = mapped_column(Text)
    metodo_extraccion: Mapped[str | None] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    modificado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora, onupdate=ahora)

    proveedor: Mapped[Proveedor | None] = relationship(back_populates="facturas")
    periodo: Mapped[Periodo] = relationship(back_populates="facturas")
    documento: Mapped[Documento | None] = relationship(back_populates="factura")
    iva: Mapped[list[FacturaIva]] = relationship(
        back_populates="factura", cascade="all, delete-orphan", order_by="FacturaIva.alicuota")
    descuentos: Mapped[list[FacturaDescuento]] = relationship(
        back_populates="factura", cascade="all, delete-orphan", order_by="FacturaDescuento.id")
    tributos: Mapped[list[FacturaTributo]] = relationship(
        back_populates="factura", cascade="all, delete-orphan", order_by="FacturaTributo.id")


class FacturaIva(Base):
    __tablename__ = "facturas_iva"
    __table_args__ = (UniqueConstraint("factura_id", "alicuota", name="uq_facturas_iva_alicuota"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id", ondelete="CASCADE"), index=True)
    alicuota: Mapped[Decimal] = mapped_column(DecimalFijo(2))
    base_imponible: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))
    importe: Mapped[Decimal] = mapped_column(DecimalFijo(2))

    factura: Mapped[Factura] = relationship(back_populates="iva")


class FacturaDescuento(Base):
    __tablename__ = "facturas_descuentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id", ondelete="CASCADE"), index=True)
    descripcion: Mapped[str | None] = mapped_column(String(200))
    porcentaje: Mapped[Decimal | None] = mapped_column(DecimalFijo(4))
    base: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))
    importe: Mapped[Decimal] = mapped_column(DecimalFijo(2))
    origen: Mapped[str] = mapped_column(String(10))

    factura: Mapped[Factura] = relationship(back_populates="descuentos")


class FacturaTributo(Base):
    __tablename__ = "facturas_tributos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))
    descripcion: Mapped[str | None] = mapped_column(String(200))
    alicuota: Mapped[Decimal | None] = mapped_column(DecimalFijo(4))
    base: Mapped[Decimal | None] = mapped_column(DecimalFijo(2))
    importe: Mapped[Decimal] = mapped_column(DecimalFijo(2))

    factura: Mapped[Factura] = relationship(back_populates="tributos")


class Auditoria(Base):
    __tablename__ = "auditoria"
    __table_args__ = (Index("ix_auditoria_entidad", "entidad", "entidad_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    entidad: Mapped[str] = mapped_column(String(30))
    entidad_id: Mapped[int | None] = mapped_column(Integer)
    accion: Mapped[str] = mapped_column(String(30))
    detalle: Mapped[str | None] = mapped_column(Text)
