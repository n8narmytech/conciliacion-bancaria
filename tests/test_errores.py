"""
Errores de carga: el sistema tiene que frenar en vez de devolver un número.

Un error que pasa de largo produce un resultado creíble pero equivocado,
que es peor que no tener resultado.
"""

import pytest

from tests.ayudas import archivos, conciliar

pytestmark = pytest.mark.datos


@pytest.mark.parametrize("banco,mes_mayor,mes_extracto", [
    ("bbva", "mayo", "junio"),
    ("bbva", "junio", "mayo"),
    ("santander", "mayo", "junio"),
    ("galicia", "junio", "mayo"),
])
def test_frena_si_los_archivos_son_de_meses_distintos(banco, mes_mayor, mes_extracto):
    crm, _ = archivos(banco, mes_mayor)
    _, ext = archivos(banco, mes_extracto)
    r = conciliar(banco, crm, ext)

    assert r["error"] is not None
    # El mensaje es para quien concilia: dice de qué mes es cada archivo y
    # no arrastra el detalle técnico de Python.
    assert f"libro mayor es de {mes_mayor} 2026" in r["error"]
    assert f"extracto bancario es de {mes_extracto} 2026" in r["error"]
    assert "Traceback" not in r["error"]


def test_frena_con_el_banco_equivocado():
    crm, ext = archivos("bbva", "junio")
    assert conciliar("santander", crm, ext)["error"] is not None


def test_frena_con_los_archivos_invertidos():
    crm, ext = archivos("bbva", "junio")
    assert conciliar("bbva", ext, crm)["error"] is not None


def test_frena_con_el_mismo_archivo_dos_veces():
    crm, _ = archivos("bbva", "junio")
    assert conciliar("bbva", crm, crm)["error"] is not None
