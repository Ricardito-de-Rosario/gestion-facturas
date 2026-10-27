# Gestión de facturas

Aplicación local para leer facturas PDF, revisar los datos extraídos, registrar comprobantes y consultar resúmenes mensuales.

## Requisitos

- Python 3.11 o posterior.
- Windows PowerShell (las instrucciones de ejecución usan PowerShell).
- Tesseract con el paquete de idioma español para reconocer PDF escaneados. Sin Tesseract se pueden cargar PDF digitales y completar manualmente documentos escaneados.

## Instalación y ejecución

Desde la carpeta del proyecto:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flask --app facturas:create_app run --debug
```

Abre <http://127.0.0.1:5000>. La aplicación crea y actualiza la base de datos local `data/facturas.db` al iniciar. Los PDF y registros locales se guardan bajo `data/`; no se incluyen en el repositorio.

## Datos de demostración

El repositorio incluye `examples/facturas-demo.db`, una base con comprobantes sintéticos para probar la interfaz. Los nombres de proveedores son ficticios, no hay CUIT ni PDF asociados y los registros quedan pendientes de revisión.

Para evitar modificar la base demostrativa versionada, copia primero la base a `data/` y ejecuta la aplicación apuntando a esa copia:

```powershell
$demoDb = Join-Path (Resolve-Path .\data).Path "facturas-demo.db"
if (Test-Path $demoDb) { throw "Ya existe $demoDb; no se sobrescribió." }
Copy-Item .\examples\facturas-demo.db $demoDb
$env:FACTURAS_DATABASE_URL = "sqlite:///" + $demoDb.Replace("\", "/")
python -m flask --app facturas:create_app run --debug
```

Al terminar, puedes limpiar la variable en esa terminal con:

```powershell
Remove-Item Env:FACTURAS_DATABASE_URL
```

La base se puede regenerar con `python .\scripts\crear_base_demo.py`. El comando no sobrescribe un archivo existente.

## Configuración

La configuración opcional se puede definir en `instance/config.py` o mediante variables de entorno con prefijo `FACTURAS_`. Por ejemplo, `FACTURAS_CUIT_PROPIO` define el CUIT del receptor y `FACTURAS_MAX_TAMANO_PDF_MB` cambia el límite de carga.

## Datos y privacidad

`data/`, `pdfs/`, `instance/`, las bases SQLite locales y los documentos son ignorados por Git. No agregues al repositorio facturas reales, información personal, credenciales, archivos `.env` ni copias de la base local.

El servidor de Flask en modo `--debug` es sólo para desarrollo local; no lo expongas a Internet.
