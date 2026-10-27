from logging.config import fileConfig

from alembic import context

from facturas import modelos  # noqa: F401  (registra las tablas en Base.metadata)
from facturas.config import cargar_config
from facturas.db import Base, crear_engine

config = context.config
if config.config_file_name and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _configurar(**kwargs):
    context.configure(target_metadata=target_metadata, render_as_batch=True,
                      compare_type=True, **kwargs)


def run_migrations_offline():
    _configurar(url=cargar_config()["DATABASE_URL"], literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    conexion = config.attributes.get("connection")
    if conexion is not None:
        _configurar(connection=conexion)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = crear_engine(cargar_config()["DATABASE_URL"])
    with engine.connect() as conexion:
        _configurar(connection=conexion)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
