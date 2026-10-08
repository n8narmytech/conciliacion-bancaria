"""
Utilidades compartidas por los tests.

Los archivos bancarios NO están en el repositorio: se leen de una carpeta
local, organizada como BANCO/MES/. Por defecto es ~/Desktop/bancos; se puede
apuntar a otra con la variable de entorno CONCILIACION_DATOS. Si falta un
archivo, el test que lo necesita se saltea en vez de fallar.
"""

import contextlib
import io
import os
from pathlib import Path

import pytest

from orquestador import ejecutar_conciliacion

DATOS = Path(os.environ.get("CONCILIACION_DATOS", "~/Desktop/bancos")).expanduser()

# Archivos de cada mes: (libro mayor, extracto), relativos a DATOS.
ARCHIVOS = {
    ("bbva", "marzo"): ("BBVA/MARZO/BBVA 2026 03 GBP mayor.xls", "BBVA/MARZO/BBVA 2026 03 movim.xls"),
    ("bbva", "abril"): ("BBVA/ABRIL/2026 04 bbva gbp.xlsx", "BBVA/ABRIL/BBVA 2026 04 MEL.xlsx"),
    ("bbva", "mayo"): ("BBVA/MAYO/MAYOR BBVA 05 2026.xlsx", "BBVA/MAYO/BBVA 2026 05.xls"),
    ("bbva", "junio"): ("BBVA/JUNIO/MAYOR BBVA 06 2026.xlsx", "BBVA/JUNIO/BBVA 2026 06 MEL.xlsx"),
    ("santander", "mayo"): ("SANTANDER/MAYO/Mov. 05 2026 Santander  - GBP.xls",
                            "SANTANDER/MAYO/Santander 05 2026 - EXTRACTO INTERBANKING.xlsx"),
    ("santander", "junio"): ("SANTANDER/JUNIO/MAYOR SANTANDER 06 2026.xlsx",
                             "SANTANDER/JUNIO/EXTRACTO INTERBANKING ARMY - SANTANDER 06 2026.xlsx"),
    ("galicia", "mayo"): ("GALICIA/MAYO/MAYOR GALICIA GBP 05 2026.xls", "GALICIA/MAYO/GALICIA ARMY 05 2026.xlsx"),
    ("galicia", "junio"): ("GALICIA/JUNIO/MAYOR GALICIA GBP 06 2026.xls", "GALICIA/JUNIO/GALICIA ARMY 06 2026.xlsx"),
}


def ruta(relativa):
    """Ruta absoluta de un archivo de datos, o saltea el test si no está."""
    p = DATOS / relativa
    if not p.exists():
        pytest.skip(f"falta el archivo de datos: {relativa}")
    return str(p)


def archivos(banco, mes):
    """(mayor, extracto) de un mes, como rutas absolutas."""
    crm, ext = ARCHIVOS[(banco, mes)]
    return ruta(crm), ruta(ext)


def conciliar(banco, crm, extracto, **kwargs):
    """Corre la conciliación sin el texto de progreso que imprimen los perfiles."""
    with contextlib.redirect_stdout(io.StringIO()):
        return ejecutar_conciliacion(banco, crm, extracto, **kwargs)


def conciliar_mes(banco, mes, **kwargs):
    crm, ext = archivos(banco, mes)
    return conciliar(banco, crm, ext, **kwargs)


def ajuste(concepto, monto, **extra):
    return {"concepto": concepto, "monto": monto, "cantidad_mov": 1, **extra}
