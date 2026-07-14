"""
Utilidades comunes a todos los bancos:
    - Normalización de texto para matching
    - Parseo de montos
    - Lectura genérica de Excel (.xls y .xlsx)
"""

import re
import pandas as pd
from unidecode import unidecode


def normalizar_texto(texto):
    """
    Normaliza texto para matching: mayúsculas, sin acentos, sin caracteres raros.
    Devuelve string vacío para NaN.
    """
    if pd.isna(texto):
        return ""
    txt = str(texto).strip().upper()
    txt = unidecode(txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt


def parse_monto(valor):
    """Convierte un valor a float. Vacío/NaN → 0."""
    if pd.isna(valor) or valor == "" or valor is None:
        return 0.0
    try:
        return float(valor)
    except (ValueError, TypeError):
        return 0.0


def leer_excel(archivo, header=0):
    """
    Lee un archivo Excel detectando automáticamente si es .xlsx o .xls.
    Acepta tanto rutas (str) como objetos tipo file (BytesIO de Streamlit, etc).
    El parámetro `header` indica la fila con los encabezados (0-indexed).
    """
    nombre = ""
    if hasattr(archivo, "name"):
        nombre = archivo.name.lower()
    elif isinstance(archivo, str):
        nombre = archivo.lower()

    if nombre.endswith(".xls"):
        try:
            return pd.read_excel(archivo, engine="xlrd", header=header)
        except ImportError:
            raise RuntimeError(
                "Para leer archivos .xls necesitás instalar xlrd: "
                "pip3 install xlrd"
            )
    else:
        return pd.read_excel(archivo, engine="openpyxl", header=header)
