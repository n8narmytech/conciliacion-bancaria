"""
Regresión: cada mes disponible tiene que dar exactamente lo mismo que dio
cuando se validó.

Si uno de estos tests falla después de un cambio, el cambio alteró el
resultado de una conciliación. Puede ser intencional (por ejemplo, un
matching nuevo que empareja más movimientos), pero entonces hay que revisar
el caso a mano y actualizar el valor esperado a conciencia, nunca para que
el test pase.

Los cierres de junio (los casos "cerrado") son los acordados con
contabilidad: Santander 1.022,04, BBVA 0,00 y Galicia -7,00.
"""

import pytest

from tests.ayudas import ajuste, conciliar_mes

pytestmark = pytest.mark.datos

# Sin ajustes: lo que devuelve el sistema solo, antes de cualquier decisión.
ESPERADOS = {
    #  banco       mes       apertura         diferencia      emparejados  sin justificar
    ("bbva",      "marzo"): (2_149_232.03,   -1_585_828.04,   208,          12),
    ("bbva",      "abril"): (3_735_060.07,   -1_358_075.22,   175,          21),
    ("bbva",      "mayo"):  (937_834.32,      1_573_515.17,   212,          11),
    ("bbva",      "junio"): (-635_680.85,       457_829.05,   174,           3),
    ("santander", "mayo"):  (13_977_083.28,    -188_682.60,   132,           8),
    ("santander", "junio"): (182_546.06,        401_522.04,   133,           2),
    ("galicia",   "mayo"):  (0.58,              871_931.11,    58,           3),
    ("galicia",   "junio"): (-871_930.53,      -434_244.86,    37,          60),
}


@pytest.mark.parametrize("banco,mes", list(ESPERADOS), ids=lambda v: str(v))
def test_mes_sin_ajustes(banco, mes):
    apertura, diferencia, emparejados, sin_justificar = ESPERADOS[(banco, mes)]
    r = conciliar_mes(banco, mes)
    assert r["error"] is None, r["error"]
    st = r["estadisticas"]

    assert st["apertura_origen"] == "derivada", "la apertura tiene que salir del extracto"
    assert st["saldo_apertura"] == pytest.approx(apertura, abs=0.01)
    assert st["diferencia_final"] == pytest.approx(diferencia, abs=0.01)
    assert st["total_matches"] == emparejados
    assert st["sin_justificar"] == sin_justificar


def test_santander_junio_cerrado():
    r = conciliar_mes("santander", "junio", ajustes_manuales=[
        ajuste("Comisión de originación del préstamo", -400_500.00),
    ])
    st = r["estadisticas"]
    assert st["diferencia_final"] == pytest.approx(1_022.04, abs=0.01)

    # Lo único sin justificar es el residuo entre los cargos del banco y el
    # asiento agrupado: el mismo monto que la diferencia final.
    pendientes = [d for d in r["discrepancias"] if d["cobertura"] == "ninguna"]
    assert len(pendientes) == 1
    assert pendientes[0]["origen"] == "GRUPO"
    assert pendientes[0]["monto"] == pytest.approx(-1_022.04, abs=0.01)


def test_bbva_junio_cerrado():
    r = conciliar_mes("bbva", "junio", ajustes_manuales=[
        ajuste("Transferencia a Diego sin contabilizar", -450_000.00),
        ajuste("Débitos pendientes (RcM X)", -72_913.43),
        ajuste("TRF IN COEL, cobro registrado en mayo", 65_084.38),
    ])
    st = r["estadisticas"]
    assert st["diferencia_final"] == pytest.approx(0.00, abs=0.01)
    assert st["sin_justificar"] == 0


def test_galicia_junio_cerrado_aceptando_la_sugerencia():
    """El flujo real: se acepta la sugerencia de gastos tal como la propone
    el sistema (con su lista de movimientos) y el recibo se carga a mano."""
    sugerido = conciliar_mes("galicia", "junio")["estadisticas"]["ajustes_sugeridos"]
    gastos = next(s for s in sugerido if s["concepto"] == "Gastos bancarios no contabilizados")
    assert gastos["monto"] == pytest.approx(-437_693.25, abs=0.01)
    assert len(gastos["movimientos"]) == 59

    r = conciliar_mes("galicia", "junio", ajustes_manuales=[
        ajuste(gastos["concepto"], gastos["monto"], movimientos=gastos["movimientos"]),
        ajuste("Recibo Badano acreditado en junio", 871_931.11),
    ])
    st = r["estadisticas"]
    assert st["diferencia_final"] == pytest.approx(-7.00, abs=0.01)
    assert st["sin_justificar"] == 0
