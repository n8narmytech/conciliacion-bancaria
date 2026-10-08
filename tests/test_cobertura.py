"""
Cobertura de los movimientos sin pareja individual, con datos inventados.

Cada caso reproduce en miniatura una situación real que apareció en los
archivos: no necesita los archivos bancarios y corre en cualquier lado.
"""

from datetime import date

import pytest

from nucleo.cobertura import clasificar_cobertura

GASTO = "COMISIÓN/IMPUESTO NO REGISTRADO"


def cargo(monto, fila, descripcion="IMPUESTO LEY 25413"):
    """Cargo de impuestos o comisiones que cobró el banco."""
    return {"origen": "BANCO", "tipo": GASTO, "fila": fila, "fecha": date(2026, 6, 10),
            "monto": monto, "descripcion": descripcion}


def asiento_proveedores(monto, fila, dia=30):
    """Asiento del libro que agrupa los gastos del banco."""
    return {"origen": "CRM", "tipo": "FALTANTE EN BANCO", "fila": fila, "fecha": date(2026, 6, dia),
            "monto": monto, "descripcion": "- Proveedores"}


def movimiento(origen, monto, fila, descripcion="TRANSFERENCIA"):
    tipo = "FALTANTE EN CRM" if origen == "BANCO" else "FALTANTE EN BANCO"
    return {"origen": origen, "tipo": tipo, "fila": fila, "fecha": date(2026, 6, 5),
            "monto": monto, "descripcion": descripcion}


def _por_estado(discrepancias, estado):
    return [d for d in discrepancias if d["cobertura"] == estado]


# --- Grupo de gastos contra el asiento agrupado -----------------------

def test_grupo_que_coincide_exacto():
    disc = [cargo(-100_000, 1), cargo(-200_000, 2), cargo(-300_000, 3), asiento_proveedores(-600_000, 50)]
    resumen = clasificar_cobertura(disc)
    assert resumen["cubiertos_agrupados"] == 4
    assert resumen["sin_justificar"] == 0
    assert len(disc) == 4, "sin residuo no se agrega ninguna línea"


def test_sobra_un_cargo_en_el_banco():
    # Santander junio: la comisión de originación no está en el asiento agrupado.
    originacion = cargo(-400_500, 4, "COMISION DE ORIGINACION")
    disc = [cargo(-100_000, 1), cargo(-200_000, 2), cargo(-300_000, 3), originacion,
            asiento_proveedores(-600_000, 50)]
    resumen = clasificar_cobertura(disc)
    assert resumen["cubiertos_agrupados"] == 4
    assert _por_estado(disc, "ninguna") == [originacion]
    assert "No forma parte del asiento agrupado" in originacion["cobertura_detalle"]


def test_sobra_un_asiento_en_el_libro():
    # BBVA abril: dos "- Proveedores" y uno es de otro mes.
    del_mes = asiento_proveedores(-600_000, 50, dia=30)
    de_otro_mes = asiento_proveedores(-32_952.04, 51, dia=1)
    disc = [cargo(-100_000, 1), cargo(-200_000, 2), cargo(-300_000, 3), del_mes, de_otro_mes]
    clasificar_cobertura(disc)
    assert del_mes["cobertura"] == "agrupada"
    assert de_otro_mes["cobertura"] == "ninguna"
    assert "no corresponde a los cargos de este mes" in de_otro_mes["cobertura_detalle"]


def test_residuo_chico_queda_como_una_sola_linea():
    # BBVA mayo: 640,50 de diferencia repartidos en el redondeo.
    disc = [cargo(-100_000, 1), cargo(-200_000, 2), cargo(-300_000, 3), asiento_proveedores(-600_640.50, 50)]
    resumen = clasificar_cobertura(disc)
    assert resumen["cubiertos_agrupados"] == 4
    pendientes = _por_estado(disc, "ninguna")
    assert len(pendientes) == 1
    assert pendientes[0]["origen"] == "GRUPO"
    assert pendientes[0]["monto"] == pytest.approx(640.50, abs=0.01)


def test_residuo_grande_no_se_compensa():
    # Más del 1% de diferencia no es redondeo: se deja todo a la vista.
    disc = [cargo(-100_000, 1), cargo(-200_000, 2), cargo(-300_000, 3), asiento_proveedores(-700_000, 50)]
    resumen = clasificar_cobertura(disc)
    assert resumen["cubiertos_agrupados"] == 0
    assert resumen["sin_justificar"] == 4
    assert all(d["origen"] != "GRUPO" for d in disc)


# --- Ajustes cargados -------------------------------------------------

def test_ajuste_con_lista_cubre_todos_sus_movimientos():
    # Galicia junio: el ajuste de gastos suma varios cargos y trae la lista.
    a, b, otro = movimiento("BANCO", -100, 10), movimiento("BANCO", -250, 11), movimiento("BANCO", -999, 12)
    disc = [a, b, otro]
    # La fila puede volver de la interfaz como número decimal.
    lista = [{"origen": "BANCO", "fila": 10.0}, {"origen": "BANCO", "fila": "11"}]
    resumen = clasificar_cobertura(disc, [{"concepto": "Gastos", "monto": -350, "movimientos": lista}])
    assert a["cobertura"] == b["cobertura"] == "ajuste"
    assert otro["cobertura"] == "ninguna"
    assert resumen["cubiertos_por_ajuste"] == 2


def test_ajuste_a_mano_se_reconoce_por_monto_con_el_signo_invertido():
    # Un cobro del libro que el banco todavía no acreditó se neutraliza con un ajuste negativo.
    cobro = movimiento("CRM", 72_913.43, 20, "RcM X 00009")
    clasificar_cobertura([cobro], [{"concepto": "Débitos pendientes", "monto": -72_913.43}])
    assert cobro["cobertura"] == "ajuste"


def test_un_ajuste_a_mano_cubre_un_solo_movimiento():
    primero, segundo = movimiento("BANCO", -450_000, 30), movimiento("BANCO", -450_000, 31)
    clasificar_cobertura([primero, segundo], [{"concepto": "Diego", "monto": -450_000}])
    assert [primero["cobertura"], segundo["cobertura"]] == ["ajuste", "ninguna"]


def test_ajuste_sin_coincidencia_no_cubre_nada():
    mov = movimiento("BANCO", -450_000, 30)
    resumen = clasificar_cobertura([mov], [{"concepto": "Otra cosa", "monto": -123}])
    assert resumen["sin_justificar"] == 1
