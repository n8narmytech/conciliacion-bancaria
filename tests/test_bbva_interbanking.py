"""BBVA descargado desde Interbanking en lugar de BBVA net cash."""

import pytest

from bancos.bbva import _es_gasto_bancario_bbva
from tests.ayudas import ajuste, conciliar, ruta


@pytest.mark.parametrize("descripcion,es_gasto", [
    # Interbanking
    ("IMPUESTO AL DEBITO IMPUESTO LEY N", True),
    ("IMPUESTO AL CREDITO LEY NRO 25.413", True),
    ("AJUSTE IMPUESTO AL CREDITO", True),
    ("RRSIRCREB 129 REG REC SIRCRE", True),
    ("IVA 21% IVA TASA GRAL.", True),
    ("C.MANT.CTA COM.MANT.PQ 18", True),
    ("COM.EM.CCI 11111111 COMISION TRANS", True),
    ("COM.GE.CFU 34281130 COMISION GESTI", True),
    ("NOTA DB PERC.CABA ING.", True),
    ("NOTA DB 99999999 OG-DEB./CRED.H", False),   # débito de haberes
    ("SEGUROS OG - DEBITO DI", False),
    ("SUELDOS 99999999 OG-DEBITO", False),
    ("FDO.COM.IN 71 OPER. FDO.COMU", False),
    # BBVA net cash, sin cambios
    ("LEY NRO 25.4 29/06/26 00012", True),
    ("REG REC SIRC 29/06/26 00015B", True),
    ("COMISION TRA 30710960492", True),
    ("TRANSFERENCI 403424224403", False),
])
def test_reconoce_los_gastos_en_los_dos_formatos(descripcion, es_gasto):
    assert _es_gasto_bancario_bbva(descripcion) is es_gasto


@pytest.mark.datos
def test_julio_desde_interbanking():
    """Interbanking trae los impuestos del primer día hábil del mes
    siguiente (03/08). No cuentan para compensar el grupo contra el asiento
    "- Proveedores", y aparecen en la diferencia de gastos como "Fecha de
    otro mes". Antes de esto, los 202 cargos y el asiento quedaban sueltos."""
    mayor, extracto = ruta("BBVA/JULIO/BBVA GBP 07.2026.xls"), ruta("BBVA/JULIO/BBVA Interbanking 07.2026.xlsx")
    r = conciliar("bbva", mayor, extracto)
    assert r["error"] is None, r["error"]
    pendientes = [d for d in r["discrepancias"] if d["cobertura"] == "ninguna"]
    assert len(pendientes) == 4

    linea = next(d for d in pendientes if d["origen"] == "GRUPO")
    assert linea["monto"] == pytest.approx(-1_020_182.38, abs=0.01)
    marcas = [c["marca"] for c in linea["componentes"]]
    assert marcas.count("Fecha de otro mes") == 10
    assert sum(c["monto"] for c in linea["componentes"]) == pytest.approx(linea["monto"], abs=0.01)

    # Cargando los ajustes posibles, cierra.
    st = conciliar("bbva", mayor, extracto, ajustes_manuales=[
        ajuste(d["descripcion"], d["ajuste_posible"]) for d in pendientes])["estadisticas"]
    assert st["diferencia_final"] == pytest.approx(0.00, abs=0.01)
