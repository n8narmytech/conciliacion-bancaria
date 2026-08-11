"""
Registro de bancos disponibles en el sistema.

Para agregar un nuevo banco:
    1. Crear un archivo nuevo (ej: bancos/patagonia.py) con una clase
       que herede de Banco y defina sus métodos.
    2. Importarlo aquí y agregarlo a BANCOS_DISPONIBLES.
    3. La UI de Streamlit lo va a mostrar automáticamente en el selector.
"""

from bancos.base import Banco
from bancos.bbva import BBVA
from bancos.santander import Santander
from bancos.galicia import Galicia

# Lista de bancos disponibles en la UI (en orden de aparición)
BANCOS_DISPONIBLES = [
    BBVA,
    Santander,
    Galicia,
]


def obtener_banco(codigo):
    """Devuelve una instancia del banco por su código."""
    for BancoClass in BANCOS_DISPONIBLES:
        if BancoClass.codigo == codigo:
            return BancoClass()
    raise ValueError(f"Banco desconocido: {codigo}")


def listar_bancos():
    """Devuelve una lista de dicts para mostrar en la UI: [{codigo, nombre}, ...]"""
    return [
        {"codigo": B.codigo, "nombre": B.nombre, "formatos": B.formatos_esperados}
        for B in BANCOS_DISPONIBLES
    ]
