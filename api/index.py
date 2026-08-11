"""
API HTTP de la conciliación bancaria.

Expone la lógica que hoy usa la interfaz de Streamlit (orquestador +
perfiles de banco) como endpoints JSON, para que la pueda consumir el
frontend en Next.js.

La lógica de negocio NO vive acá: este módulo solo recibe los archivos,
llama a `ejecutar_conciliacion` y traduce el resultado a JSON. Todo lo
contable sigue en `orquestador.py`, `bancos/` y `nucleo/`.

Endpoints:
    GET  /api/bancos     → bancos disponibles y sus formatos esperados
    POST /api/conciliar  → corre la conciliación y devuelve el resultado
"""

import base64
import io
import json
import os
import sys

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

# La lógica de negocio vive en la raíz del repo, un nivel arriba de api/.
# Se agregan ambos directorios al path para que los imports funcionen igual
# corriendo local con uvicorn (`api.index:app`) que en Vercel, donde el
# archivo de la función es el punto de entrada.
_DIR_API = os.path.dirname(os.path.abspath(__file__))
_DIR_RAIZ = os.path.dirname(_DIR_API)
for _ruta in (_DIR_RAIZ, _DIR_API):
    if _ruta not in sys.path:
        sys.path.insert(0, _ruta)

# Los imports de la lógica se hacen tolerantes a fallo a propósito: si en el
# entorno de despliegue falta un módulo o un archivo no viajó en el bundle,
# la función igual levanta y /api/health explica qué pasó. Un ImportError acá
# arriba haría crashear el arranque y el error solo se vería en los logs de la
# plataforma, sin pista de cuál fue la causa.
ERROR_CARGA = None  # type: ignore[var-annotated]  # str cuando falla la carga
listar_bancos = None  # type: ignore[assignment]
ejecutar_conciliacion = None  # type: ignore[assignment]
armar_respuesta = None  # type: ignore[assignment]

try:
    from bancos import listar_bancos  # type: ignore[no-redef] # noqa: E402
    from orquestador import ejecutar_conciliacion  # type: ignore[no-redef] # noqa: E402
    from _serializacion import armar_respuesta  # type: ignore[no-redef] # noqa: E402
except Exception as _e:  # pragma: no cover - solo se activa si el bundle está mal
    import traceback

    ERROR_CARGA = f"{type(_e).__name__}: {_e}\n{traceback.format_exc()}"


app = FastAPI(title="Conciliación Bancaria API", version="1.0.0")

# El frontend se sirve desde el mismo dominio en producción; en desarrollo
# corre en localhost:3000 contra la API en otro puerto.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Tamaño máximo por archivo. Los extractos reales pesan entre 50 y 250 KB;
# 10 MB deja margen de sobra y corta cualquier subida accidental enorme.
MAX_BYTES = 10 * 1024 * 1024


def _verificar_carga():
    """Corta con 500 explicando el problema si la lógica no se pudo importar."""
    if ERROR_CARGA:
        raise HTTPException(
            status_code=500,
            detail=f"La lógica de conciliación no se pudo cargar. {ERROR_CARGA}",
        )


@app.get("/api/bancos")
def bancos_disponibles():
    """Lista los bancos que el sistema sabe conciliar."""
    _verificar_carga()
    return {"bancos": listar_bancos()}


@app.get("/api/health")
def health():
    """
    Estado de la API. Sirve para verificar, después de un despliegue, que
    la función levantó y que encontró los módulos de la lógica contable.
    """
    if ERROR_CARGA:
        return {
            "ok": False,
            "error": ERROR_CARGA,
            "python": sys.version,
            "cwd": os.getcwd(),
            "sys_path": sys.path[:5],
            "archivos_visibles": sorted(os.listdir(_DIR_RAIZ))[:25],
        }
    return {"ok": True, "bancos": len(listar_bancos()), "python": sys.version.split()[0]}


@app.post("/api/conciliar")
async def conciliar(
    banco: str = Form(...),
    archivo_crm: UploadFile = File(...),
    archivo_banco: UploadFile = File(...),
    saldo_apertura: float = Form(0.0),
    saldo_extracto: str = Form(""),
    ajustes: str = Form("[]"),
    incluir_excel: bool = Form(False),
):
    """
    Ejecuta la conciliación completa.

    Recibe los dos Excel (libro mayor del CRM y extracto bancario), el
    código de banco y los ajustes ya cargados, y devuelve las estadísticas,
    las discrepancias y los ajustes que el sistema sugiere.

    `incluir_excel` agrega el reporte .xlsx en base64; se pide solo al
    final del flujo para no generarlo en cada recálculo.
    """
    _verificar_carga()

    # --- Validación de entrada ---
    codigos = {b["codigo"] for b in listar_bancos()}
    if banco not in codigos:
        raise HTTPException(
            status_code=400,
            detail=f"Banco desconocido: '{banco}'. Disponibles: {sorted(codigos)}",
        )

    try:
        ajustes_manuales = json.loads(ajustes) if ajustes else []
        if not isinstance(ajustes_manuales, list):
            raise ValueError("se esperaba una lista")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Ajustes inválidos: {e}")

    contenido_crm = await archivo_crm.read()
    contenido_banco = await archivo_banco.read()

    for nombre, contenido in (("CRM", contenido_crm), ("extracto", contenido_banco)):
        if not contenido:
            raise HTTPException(status_code=400, detail=f"El archivo del {nombre} está vacío.")
        if len(contenido) > MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"El archivo del {nombre} supera el límite de 10 MB.",
            )

    # `leer_excel` decide el motor por la extensión del nombre, así que hay
    # que conservarlo al envolver los bytes en un buffer.
    buffer_crm = _buffer_con_nombre(contenido_crm, archivo_crm.filename)
    buffer_banco = _buffer_con_nombre(contenido_banco, archivo_banco.filename)

    sal_ext = None
    if saldo_extracto.strip():
        try:
            valor = float(saldo_extracto)
            if abs(valor) > 0.01:
                sal_ext = valor
        except ValueError:
            raise HTTPException(status_code=400, detail="El saldo del extracto no es un número.")

    # --- Ejecución ---
    resultado = ejecutar_conciliacion(
        banco,
        buffer_crm,
        buffer_banco,
        saldo_apertura=saldo_apertura,
        ajustes_manuales=ajustes_manuales,
        saldo_extracto_banco=sal_ext,
    )

    if resultado.get("error"):
        # Error de procesamiento (formato inesperado, columna faltante, etc.):
        # es un problema del archivo que subieron, no del servidor.
        raise HTTPException(status_code=422, detail=resultado["error"])

    payload = armar_respuesta(resultado)

    if incluir_excel and resultado.get("excel_bytes") is not None:
        payload["excel_base64"] = base64.b64encode(
            resultado["excel_bytes"].getvalue()
        ).decode("ascii")

    return payload


def _buffer_con_nombre(contenido, filename):
    """
    Envuelve los bytes del archivo en un BytesIO que conserva el nombre
    original, porque la lectura de Excel elige el motor (.xls vs .xlsx)
    según la extensión.
    """
    buffer = io.BytesIO(contenido)
    buffer.name = filename or "archivo.xlsx"
    return buffer
