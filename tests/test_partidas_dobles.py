"""
Pares espejados del libro: cuándo son una corrección contable (se sacan
del matching) y cuándo son dos movimientos bancarios reales registrados
en un mismo asiento (se conservan). Datos inventados mínimos.
"""

from datetime import date

import pandas as pd

from nucleo.carga_crm import eliminar_partidas_dobles


def _libro(*filas):
    """filas: (asiento, fecha, monto)"""
    df = pd.DataFrame([{"comprobante": a, "contraparte_orig": "Banco Santander cta cte $",
                        "fecha": f, "monto": m} for a, f, m in filas])
    df.attrs["saldo_crm_detectado"] = 123.0
    return df


def _banco(*filas):
    """filas: (fecha, monto)"""
    return pd.DataFrame([{"fecha": f, "monto": m} for f, m in filas])


PAR_FONDOS = [("39608", date(2026, 7, 22), 30_000_000.0), ("39608", date(2026, 7, 22), -30_000_000.0)]
OTRO = ("39001", date(2026, 7, 10), -5_000.0)


def test_se_conserva_si_el_banco_tiene_la_ida_y_vuelta():
    # Santander julio: transferencia que entra y suscripción a un fondo que sale.
    banco = _banco((date(2026, 7, 22), 30_000_000.0), (date(2026, 7, 22), -30_000_000.0))
    out = eliminar_partidas_dobles(_libro(*PAR_FONDOS, OTRO), banco)
    assert len(out) == 3


def test_se_saca_si_el_banco_no_tiene_nada():
    # Corrección dentro del libro: se registró algo y se anuló.
    out = eliminar_partidas_dobles(_libro(*PAR_FONDOS, OTRO), _banco((date(2026, 7, 10), -5_000.0)))
    assert len(out) == 1


def test_se_saca_si_el_banco_tiene_un_solo_lado():
    # Un único movimiento del mismo monto no alcanza: es coincidencia de importe.
    out = eliminar_partidas_dobles(_libro(*PAR_FONDOS), _banco((date(2026, 7, 22), -30_000_000.0)))
    assert len(out) == 0


def test_la_ida_y_vuelta_tiene_que_estar_cerca_en_fecha():
    lejos = _banco((date(2026, 7, 2), 30_000_000.0), (date(2026, 7, 2), -30_000_000.0))
    assert len(eliminar_partidas_dobles(_libro(*PAR_FONDOS), lejos)) == 0


def test_sin_extracto_saca_todos_los_pares():
    # Comportamiento original, el de la lectura del mayor.
    assert len(eliminar_partidas_dobles(_libro(*PAR_FONDOS, OTRO))) == 1


def test_conserva_los_datos_del_mayor():
    out = eliminar_partidas_dobles(_libro(*PAR_FONDOS, OTRO))
    assert out.attrs["saldo_crm_detectado"] == 123.0
