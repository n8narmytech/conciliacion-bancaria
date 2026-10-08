"""Validación de período, con datos mínimos inventados (no necesita archivos)."""

from datetime import date

import pandas as pd
import pytest

from orquestador import ErrorDeValidacion, _mes_predominante, _validar_mismo_periodo


def _df(*fechas):
    return pd.DataFrame({"fecha": list(fechas)})


def test_mes_predominante_ignora_un_movimiento_del_mes_siguiente():
    # Como los extractos de Santander, que traen un cargo fechado el 1° del mes siguiente.
    df = _df(date(2026, 6, 1), date(2026, 6, 15), date(2026, 6, 30), date(2026, 7, 1))
    assert _mes_predominante(df) == (2026, 6)


def test_mes_predominante_sin_fechas():
    assert _mes_predominante(_df(None, None)) is None


def test_mismo_mes_no_frena():
    _validar_mismo_periodo(_df(date(2026, 6, 3)), _df(date(2026, 6, 20)))


def test_meses_distintos_frena_con_un_mensaje_claro():
    with pytest.raises(ErrorDeValidacion) as e:
        _validar_mismo_periodo(_df(date(2026, 5, 31)), _df(date(2026, 6, 1)))
    assert "mayo 2026" in str(e.value) and "junio 2026" in str(e.value)


def test_sin_fechas_no_valida():
    # Ese caso falla después con un error más específico.
    _validar_mismo_periodo(_df(None), _df(date(2026, 6, 1)))
