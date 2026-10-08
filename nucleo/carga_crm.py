"""
Carga del CRM (libro mayor de GBP).

El formato del libro mayor es el mismo para cualquier banco que se esté
conciliando (BBVA, Santander, etc.). Lo único que cambia es el número
de cuenta contable que se filtra al exportar.

Esta función carga el Excel exportado por GBP y lo normaliza al formato
interno que usa el resto del sistema.
"""

import pandas as pd
from nucleo.utilidades import leer_excel, normalizar_texto, parse_monto


# ---------------------------------------------------------------------
# CONFIGURACIÓN DE COLUMNAS
# ---------------------------------------------------------------------
# Mapeo entre las columnas del Excel exportado por GBP y los campos
# internos que usa el sistema.
COLS_CRM = {
    "fecha": "Fecha",
    "debe": "Debe",
    "haber": "Haber",
    "descripcion": "Concepto",
    "contraparte": "Leyenda",
    "referencia": "",          # GBP no expone un campo de referencia directo
    "comprobante": "Asiento",
}

# Fila donde están los encabezados (0-indexed: fila 4 en Excel → header=3)
CRM_HEADER_ROW = 3


# ---------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------------------------------------------------
def cargar_crm(path, eliminar_dobles=True):
    """
    Carga el Excel del CRM (libro mayor GBP) y lo normaliza a formato interno.

    Devuelve un DataFrame con las columnas:
        fecha, debe, haber, monto, descripcion_orig, contraparte_orig,
        referencia, comprobante, descripcion_norm, referencia_norm,
        categoria_especial, origen, fila_origen, match_id, estado

    Y guarda como attrs:
        - saldo_crm_detectado: saldo final del período leído de "Acumulado Mensual"
        - saldo_crm_arranque: saldo con el que abre el mes, antes del primer
          movimiento. Sirve para derivar el desfase heredado del mes anterior.
    """
    df = leer_excel(path, header=CRM_HEADER_ROW)
    df = df.dropna(how="all").reset_index(drop=True)

    # === Detectar saldo CRM final del archivo (Acumulado Mensual) ===
    # GBP exporta una columna "Acumulado Mensual" que contiene el saldo después
    # de cada movimiento. El valor del último movimiento es el saldo final del
    # mes según GBP, considerando el arrastre del mes anterior.
    # Este es el número que la contadora usa en su planilla.
    saldo_crm_acumulado = _detectar_acumulado_mensual(df)

    # === Detectar con qué saldo ABRE el mes ===
    # El acumulado del primer movimiento ya lo tiene sumado, así que se lo
    # resta para obtener el saldo de apertura del libro.
    saldo_crm_arranque = _detectar_arranque_acumulado(df)

    # FILTRO IMPORTANTE: solo procesar filas con número de asiento válido.
    # Esto excluye filas de totales/cierre como "Total correspondiente al mes...",
    # "Totales de Cuenta", "Impreso el...", "Mayor de la cuenta".
    col_asiento = COLS_CRM["comprobante"]
    if col_asiento in df.columns:
        asiento_num = pd.to_numeric(df[col_asiento], errors="coerce")
        df = df[asiento_num.notna()].reset_index(drop=True)

    # === Normalizar columnas al formato interno ===
    out = pd.DataFrame()
    out["fecha"] = pd.to_datetime(
        df[COLS_CRM["fecha"]], dayfirst=True, errors="coerce"
    ).dt.date
    out["debe"] = df[COLS_CRM["debe"]].apply(parse_monto)
    out["haber"] = df[COLS_CRM["haber"]].apply(parse_monto)

    # En el Libro Mayor de la cuenta bancaria:
    #   Debe  = entrada de dinero (cobranza)  → equivale a Crédito en el extracto
    #   Haber = salida de dinero  (pago)      → equivale a Débito en el extracto
    # Por eso monto positivo = ingreso.
    out["monto"] = out["debe"] - out["haber"]

    out["descripcion_orig"] = df[COLS_CRM["descripcion"]].fillna("").astype(str)
    out["contraparte_orig"] = df[COLS_CRM["contraparte"]].fillna("").astype(str)

    if COLS_CRM.get("referencia") and COLS_CRM["referencia"] in df.columns:
        out["referencia"] = df[COLS_CRM["referencia"]].fillna("").astype(str).str.strip()
    else:
        out["referencia"] = ""

    if COLS_CRM.get("comprobante") and COLS_CRM["comprobante"] in df.columns:
        out["comprobante"] = df[COLS_CRM["comprobante"]].fillna("").astype(str)
    else:
        out["comprobante"] = ""

    out["descripcion_norm"] = (
        out["descripcion_orig"] + " " + out["contraparte_orig"]
    ).apply(normalizar_texto)
    out["referencia_norm"] = out["referencia"].apply(normalizar_texto)

    # Eliminar filas sin fecha válida
    out = out[out["fecha"].notna()].reset_index(drop=True)

    # === Detectar categoría especial de cada movimiento ===
    out["categoria_especial"] = out.apply(_detectar_categoria_movimiento, axis=1)

    # === Eliminar partidas dobles del CRM (para el matching, no del saldo) ===
    # El orquestador pide no hacerlo acá (eliminar_dobles=False) para poder
    # decidirlo después de leer el extracto: ver eliminar_partidas_dobles.
    if eliminar_dobles:
        out = _eliminar_partidas_dobles(out)

    out["origen"] = "CRM"
    out["fila_origen"] = out.index + 2
    out["match_id"] = None
    out["estado"] = "pendiente"

    # Guardar el saldo detectado como attr
    out.attrs["saldo_crm_detectado"] = saldo_crm_acumulado
    out.attrs["saldo_crm_arranque"] = saldo_crm_arranque

    return out


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def _detectar_acumulado_mensual(df):
    """
    Busca el saldo final del CRM en la columna 'Acumulado Mensual'.
    Devuelve None si no la encuentra.
    """
    if "Acumulado Mensual" not in df.columns:
        return None
    col_acum = pd.to_numeric(df["Acumulado Mensual"], errors="coerce")
    col_asiento = COLS_CRM["comprobante"]
    if col_asiento not in df.columns:
        return None
    # Solo tomar filas con número de asiento válido (para excluir totales/cierre)
    mask_validos = pd.to_numeric(df[col_asiento], errors="coerce").notna()
    acumulados_mov = col_acum[mask_validos]
    if len(acumulados_mov) == 0:
        return None
    return float(acumulados_mov.iloc[-1])


def _detectar_arranque_acumulado(df):
    """
    Devuelve el saldo con el que abre el mes en el libro mayor.

    La columna "Acumulado Mensual" trae el saldo DESPUÉS de cada movimiento,
    así que el saldo de apertura es el acumulado del primer asiento menos el
    importe de ese mismo asiento.

    Este dato es la base para derivar el desfase heredado del mes anterior:
    si el libro abre en el mismo saldo con el que cerró el extracto anterior,
    no hay nada heredado; si difiere, esa diferencia es el arrastre.

    Devuelve None si el archivo no trae la información necesaria.
    """
    if "Acumulado Mensual" not in df.columns:
        return None

    col_asiento = COLS_CRM["comprobante"]
    if col_asiento not in df.columns:
        return None

    mask_validos = pd.to_numeric(df[col_asiento], errors="coerce").notna()
    if not mask_validos.any():
        return None

    validos = df[mask_validos]
    acumulados = pd.to_numeric(validos["Acumulado Mensual"], errors="coerce")
    if acumulados.isna().all():
        return None

    debe = validos[COLS_CRM["debe"]].apply(parse_monto)
    haber = validos[COLS_CRM["haber"]].apply(parse_monto)
    primer_movimiento = float(debe.iloc[0]) - float(haber.iloc[0])

    return float(acumulados.iloc[0]) - primer_movimiento


def _detectar_categoria_movimiento(row):
    """
    Detecta si un movimiento del CRM tiene una categoría especial.
    Algunos movimientos no tienen contrapartida directa en el banco:
    - LIQ-TC: liquidaciones de tarjeta que se asientan agrupadas.
    """
    desc_upper = (row["descripcion_orig"] + " " + row["contraparte_orig"]).upper()
    if "LIQ-TC" in desc_upper or "LIQ TC" in desc_upper or "LIQUIDACION" in desc_upper:
        return "LIQ-TC"
    return "normal"


def eliminar_partidas_dobles(crm, banco=None, dias=5):
    """
    Saca del matching los pares espejados del libro, salvo los que son
    movimientos bancarios reales.

    Un par espejado es un mismo asiento con el mismo importe de un lado y
    del otro. Casi siempre es una corrección dentro del libro (se registró
    algo y se anuló) y no tiene nada que ver con el banco: dejarlo aparece
    como dos pendientes ficticios.

    Pero a veces contabilidad registra en un solo asiento dos movimientos
    bancarios reales que se compensan: una transferencia que entra y la
    suscripción a un fondo que sale el mismo día por el mismo monto
    (Santander julio 2026, ±30 M y ±16 M; Galicia mayo 2026, ±8 M). Si el
    extracto tiene esa ida y vuelta en los días cercanos, el par se
    conserva para que se empareje con el banco.

    Sin extracto (banco=None) se comporta como antes: elimina todos.
    """
    pares = _pares_espejados(crm)
    if not pares:
        return crm

    a_eliminar = set()
    for i, j in pares:
        if banco is not None and _banco_tiene_ida_y_vuelta(banco, crm.at[i, "monto"], crm.at[i, "fecha"], dias):
            continue
        a_eliminar.update((i, j))

    if not a_eliminar:
        return crm
    attrs = dict(crm.attrs)
    out = crm.drop(list(a_eliminar)).reset_index(drop=True)
    out.attrs.update(attrs)
    return out


def _banco_tiene_ida_y_vuelta(banco, monto, fecha, dias):
    """¿El extracto tiene un crédito y un débito por ese importe cerca de esa fecha?"""
    importe = abs(float(monto))
    cerca = banco[
        ((banco["monto"].abs() - importe).abs() < 0.01)
        & banco["fecha"].apply(lambda f: f is not None and abs((f - fecha).days) <= dias)
    ]
    return (cerca["monto"] > 0).any() and (cerca["monto"] < 0).any()


def _eliminar_partidas_dobles(out):
    """Elimina todos los pares espejados (comportamiento al leer el mayor)."""
    return eliminar_partidas_dobles(out, banco=None)


def _pares_espejados(out):
    """
    Pares de filas del mismo asiento y la misma contraparte con importes
    opuestos. Devuelve una lista de (índice, índice).
    """
    if "comprobante" not in out.columns or not out["comprobante"].any():
        return []

    pares = []
    for (comp, cp), grupo in out.groupby(["comprobante", "contraparte_orig"]):
        if len(grupo) < 2 or not comp:
            continue
        indices = grupo.index.tolist()
        usados = set()
        for idx_i, i in enumerate(indices):
            if i in usados:
                continue
            monto_i = out.at[i, "monto"]
            if abs(monto_i) < 0.01:
                continue
            for j in indices[idx_i+1:]:
                if j in usados:
                    continue
                monto_j = out.at[j, "monto"]
                # Misma cantidad absoluta, signos opuestos → partida doble
                if abs(monto_i + monto_j) < 0.01 and (monto_i * monto_j) < 0:
                    pares.append((i, j))
                    usados.add(i)
                    usados.add(j)
                    break
    return pares
