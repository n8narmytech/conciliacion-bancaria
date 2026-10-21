"""Galicia descargado desde Interbanking en lugar de Galicia Office."""

import pytest

from bancos.galicia import Galicia, _grupo_por_codigo
from tests.ayudas import conciliar, ruta


@pytest.mark.parametrize("descripcion,grupo", [
    ("IMPUESTO AL DEBITO IMP.DEB25413-AL.G", "Impuestos"),
    ("IMPUESTO AL CREDITO IMP.CRE L25413", "Impuestos"),
    ("ING.BRUTOS REG.RECAU.SIRCREB", "Impuestos"),
    ("IVA Junio 2026", "Impuestos"),
    ("IIBBPERCEP Junio 2026", "Impuestos"),
    ("DEBITOS Junio 2026", "Comisiones"),          # comisión de servicio de cuenta
    ("DEBITOS 104814 FIMA PREMIUM CLASE A", ""),   # suscripción a un fondo: no es gasto
    ("SUELDOS 54235612 ACRED.HABERES", ""),
    ("TR. DE CTA Alejandro Pablo Arena", ""),
])
def test_reconoce_los_gastos_por_el_codigo(descripcion, grupo):
    assert _grupo_por_codigo(descripcion) == grupo


@pytest.mark.datos
def test_lee_el_extracto_de_julio_desde_interbanking():
    b = Galicia().cargar_extracto(ruta("GALICIA/JULIO/Galicia Interbanking 07.2026.xlsx"))
    assert b.attrs["formato"] == "galicia_interbanking"
    assert len(b) == 50
    # El saldo inicial es el cierre de junio, y los saldos cuadran con los movimientos.
    assert b.attrs["saldo_inicial_detectado"] == pytest.approx(596_813.79, abs=0.01)
    assert b.attrs["saldo_inicial_detectado"] + b["monto"].sum() == pytest.approx(
        b.attrs["saldo_final_detectado"], abs=0.01)
    gastos = b[b["grupo_concepto"] != ""]["monto"].sum()
    assert gastos == pytest.approx(-316_729.15, abs=0.01)


@pytest.mark.datos
def test_un_extracto_de_otro_banco_da_un_mensaje_claro():
    mayor = ruta("GALICIA/JUNIO/MAYOR GALICIA GBP 06 2026.xls")
    extracto_bbva = ruta("BBVA/JUNIO/BBVA 2026 06 MEL.xlsx")
    error = conciliar("galicia", mayor, extracto_bbva)["error"]
    assert "no tiene un formato conocido" in error
    assert "Traceback" not in error


@pytest.mark.datos
def test_julio_desde_interbanking_concilia_y_cuadra_los_gastos():
    """Los gastos que el sistema reconoce por código suman exactamente el
    asiento "- Proveedores" de julio: la clasificación coincide con la de
    contabilidad. Lo único sin justificar es el pago del aguinaldo del
    07/07, que no está en el libro."""
    r = conciliar("galicia", ruta("GALICIA/JULIO/Galicia GBP 07.2026.xls"),
                  ruta("GALICIA/JULIO/Galicia Interbanking 07.2026.xlsx"))
    assert r["error"] is None, r["error"]
    st = r["estadisticas"]
    assert st["apertura_origen"] == "derivada"
    assert any(m["tipo"].startswith("Gastos bancarios agrupados") for m in r["matches"])
    pendientes = [d for d in r["discrepancias"] if d["cobertura"] == "ninguna"]
    assert [(d["monto"], "SUELDO ANUAL" in d["descripcion"]) for d in pendientes] == [(-2_388_722.00, True)]
    assert st["diferencia_final"] == pytest.approx(2_388_722.00, abs=0.01)
