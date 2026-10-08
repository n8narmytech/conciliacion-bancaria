"""
Emparejamiento de uno contra varios, con datos inventados mínimos.

Los casos reproducen en miniatura los que aparecieron en los archivos
reales, más los que la pasada tiene que rechazar para no emparejar por
casualidad.
"""

from datetime import date

import pandas as pd

from nucleo.matching_base import _combinaciones_que_suman, pasada_3_uno_contra_varios


def _movs(*filas):
    """filas: (fecha, monto)"""
    return pd.DataFrame([{"fecha": f, "monto": m, "estado": "pendiente", "match_id": None} for f, m in filas])


D = date(2026, 5, 8)


def _correr(libro, banco):
    matches = []
    pasada_3_uno_contra_varios(libro, banco, matches)
    return matches


def test_un_asiento_contra_dos_pagos_de_tarjeta():
    # BBVA: el libro registra en un asiento los dos PAGO VISA-IN del día.
    libro, banco = _movs((D, -204_882.36)), _movs((D, -81_792.00), (D, -123_090.36), (D, -5_000.00))
    m = _correr(libro, banco)
    assert len(m) == 1 and len(m[0]["i_bco_lista"]) == 2
    assert banco.loc[2, "estado"] == "pendiente"


def test_un_movimiento_del_banco_contra_dos_asientos_con_un_centavo_de_redondeo():
    # Santander mayo: rescate de fondos de 34.999.999,99 registrado como 20 M + 15 M.
    libro = _movs((date(2026, 5, 13), 20_000_000.00), (date(2026, 5, 13), 15_000_000.00))
    banco = _movs((date(2026, 5, 11), 34_999_999.99))
    m = _correr(libro, banco)
    assert len(m) == 1 and len(m[0]["i_crm_lista"]) == 2


def test_dos_combinaciones_posibles_no_empareja():
    libro = _movs((D, -300.00))
    banco = _movs((D, -100.00), (D, -200.00), (D, -150.00), (D, -150.00))
    assert _correr(libro, banco) == []
    assert (banco["estado"] == "pendiente").all()


def test_mas_de_un_centavo_de_diferencia_no_empareja():
    assert _correr(_movs((D, 35_000_000.00)), _movs((D, 20_000_000.00), (D, 14_999_999.98))) == []


def test_fuera_de_la_ventana_de_dias_no_empareja():
    libro = _movs((D, -300.00))
    banco = _movs((D, -100.00), (date(2026, 5, 20), -200.00))
    assert _correr(libro, banco) == []


def test_partes_menores_al_uno_por_ciento_no_cuentan():
    # Impuestos chicos no pueden "formar" un asiento grande.
    libro = _movs((D, -1_000_000.00))
    banco = _movs((D, -999_000.00), (D, -600.00), (D, -400.00))
    assert _correr(libro, banco) == []


def test_distinto_signo_no_empareja():
    assert _correr(_movs((D, 300.00)), _movs((D, -100.00), (D, 400.00))) == []


def test_no_toca_lo_ya_emparejado():
    libro = _movs((D, -300.00))
    banco = _movs((D, -100.00), (D, -200.00))
    banco.loc[1, "estado"] = "conciliado"
    assert _correr(libro, banco) == []


def test_la_busqueda_cuenta_bien_las_combinaciones():
    items = [("a", 10.0), ("b", 20.0), ("c", 30.0), ("d", 40.0)]
    assert sorted(map(sorted, _combinaciones_que_suman(items, 60.0, hasta=99))) == [["a", "b", "c"], ["b", "d"]]
