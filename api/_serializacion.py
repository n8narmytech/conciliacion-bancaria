"""
Conversión de los resultados de la conciliación a JSON.

El orquestador devuelve estructuras de pandas/numpy (DataFrames, int64,
float64, Timestamp, NaN) que `json.dumps` no sabe serializar. Este módulo
las normaliza a tipos nativos de Python para poder responderlas por HTTP.

Se mantiene aparte del endpoint para poder testearlo por separado.
"""

import math
from datetime import date, datetime


def limpiar(valor):
    """
    Convierte recursivamente un valor a algo serializable a JSON.

    - Fechas y timestamps → "YYYY-MM-DD"
    - Tipos numpy (int64, float64) → int / float nativos
    - NaN e infinitos → None (JSON no los admite)
    - dict / list / tuple → se recorren en profundidad
    """
    # Fechas primero: datetime es subclase de date
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()[:10]

    if isinstance(valor, dict):
        return {str(k): limpiar(v) for k, v in valor.items()}

    if isinstance(valor, (list, tuple, set)):
        return [limpiar(v) for v in valor]

    if isinstance(valor, bool):
        return valor

    # numpy expone .item() para bajar a tipo nativo
    if hasattr(valor, "item") and not isinstance(valor, (str, bytes)):
        try:
            valor = valor.item()
        except (ValueError, AttributeError):
            return str(valor)

    if isinstance(valor, float):
        # NaN / inf no son JSON válido
        if math.isnan(valor) or math.isinf(valor):
            return None
        return round(valor, 2)

    if isinstance(valor, int):
        return valor

    if valor is None or isinstance(valor, str):
        return valor

    return str(valor)


def movimientos_a_json(df, limite=None):
    """
    Convierte un DataFrame de movimientos (CRM o banco) a una lista de
    dicts liviana: solo las columnas que la interfaz necesita mostrar.
    """
    if df is None or len(df) == 0:
        return []

    columnas = [
        "fecha", "monto", "descripcion_orig", "contraparte_orig",
        "estado", "match_id", "categoria_especial", "grupo_concepto",
    ]
    presentes = [c for c in columnas if c in df.columns]

    filas = df[presentes]
    if limite:
        filas = filas.head(limite)

    out = []
    for idx, fila in filas.iterrows():
        registro = {"id": int(idx)}
        for col in presentes:
            registro[col] = limpiar(fila[col])
        out.append(registro)
    return out


def armar_respuesta(resultado, incluir_movimientos=True):
    """
    Arma el payload JSON completo que consume el frontend a partir del
    dict que devuelve `ejecutar_conciliacion`.
    """
    if resultado.get("error"):
        return {"ok": False, "error": resultado["error"]}

    stats = resultado.get("estadisticas", {})

    payload = {
        "ok": True,
        "estadisticas": limpiar(stats),
        "discrepancias": limpiar(resultado.get("discrepancias", [])),
        "matches": limpiar(resultado.get("matches", [])),
    }

    if incluir_movimientos:
        payload["crm"] = movimientos_a_json(resultado.get("crm"))
        payload["banco"] = movimientos_a_json(resultado.get("banco"))

    return payload
