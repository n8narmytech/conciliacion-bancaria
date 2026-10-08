"""
Saldos de apertura: se obtienen de los extractos, así que se pueden
verificar encadenando meses. La apertura de un mes tiene que ser, al
centavo, el saldo de cierre del extracto del mes anterior.

Este es el chequeo que respalda que el sistema no pida ningún saldo a mano.
"""

import pytest

from bancos import obtener_banco
from tests.ayudas import archivos, conciliar_mes

pytestmark = pytest.mark.datos

CADENAS = [
    ("bbva", ["marzo", "abril", "mayo", "junio"]),
    ("santander", ["mayo", "junio"]),
    ("galicia", ["mayo", "junio"]),
]
TRANSICIONES = [
    (banco, anterior, actual)
    for banco, meses in CADENAS
    for anterior, actual in zip(meses, meses[1:])
]


def _extracto(banco, mes):
    _, ext = archivos(banco, mes)
    return obtener_banco(banco).cargar_extracto(ext)


@pytest.mark.parametrize("banco,anterior,actual", TRANSICIONES)
def test_la_apertura_es_el_cierre_del_mes_anterior(banco, anterior, actual):
    cierre_anterior = _extracto(banco, anterior).attrs["saldo_final_detectado"]
    apertura_actual = _extracto(banco, actual).attrs["saldo_inicial_detectado"]
    assert apertura_actual == pytest.approx(cierre_anterior, abs=0.01)


@pytest.mark.parametrize("banco,anterior,actual", TRANSICIONES)
def test_detectar_la_apertura_da_lo_mismo_que_cargarla_a_mano(banco, anterior, actual):
    cierre_anterior = _extracto(banco, anterior).attrs["saldo_final_detectado"]
    solo = conciliar_mes(banco, actual)["estadisticas"]
    a_mano = conciliar_mes(banco, actual, saldo_extracto_anterior=cierre_anterior)["estadisticas"]
    assert solo["diferencia_final"] == pytest.approx(a_mano["diferencia_final"], abs=0.01)
