"""
Perfil específico del Banco Galicia.

Implementa la lógica particular de Galicia:
    - Lectura del formato "Movimientos" (exportación de Galicia Office)
    - Matching de gastos bancarios agrupados (Impuestos + Comisiones del
      banco ↔ asiento único "- Proveedores" de fin de mes en el CRM)
    - Ajuste automático: gastos bancarios no contabilizados

NOTA sobre este perfil: a diferencia de BBVA y Santander, el extracto de
Galicia trae una columna "Grupo de Conceptos" que ya categoriza cada
movimiento en origen (Impuestos, Transferencias, Inversiones, Comisiones,
Haberes, Pago Proveedores). Eso hace innecesario adivinar la categoría a
partir del texto de la descripción: el perfil se apoya en ese campo, que
es mucho más estable entre meses que los patrones de texto.

El resto de los movimientos (recibos Rc X, pagos Op X, suscripciones y
rescates de FIMA, transferencias entre cuentas propias) matchean 1:1 por
monto exacto y fecha cercana, así que los resuelven las pasadas comunes
del núcleo sin necesidad de lógica propia.

Construido y validado con datos reales de mayo y junio 2026.
"""

import pandas as pd

from bancos.base import Banco
from nucleo.utilidades import leer_excel, normalizar_texto, parse_monto


# =====================================================================
# CONFIGURACIÓN DEL FORMATO
# =====================================================================
# Hoja donde Galicia exporta el detalle de movimientos.
HOJA_MOVIMIENTOS = "Movimientos"

# Grupos de Conceptos (campo nativo del extracto) que corresponden a
# gastos bancarios: se contabilizan agrupados en un único asiento
# "- Proveedores" al cierre del mes.
GRUPOS_GASTOS_BANCARIOS = ("Impuestos", "Comisiones")

# Texto del asiento del CRM que agrupa los gastos bancarios del mes.
CONCEPTO_GASTOS_CRM = "- Proveedores"

# Tolerancia para dar por buena la coincidencia entre la suma de gastos
# bancarios del banco y el asiento agrupado del CRM.
TOLERANCIA_GASTOS = 1.0


# =====================================================================
# CLASE GALICIA
# =====================================================================
class Galicia(Banco):
    """Perfil de conciliación para el Banco Galicia."""

    nombre = "Galicia"
    codigo = "galicia"
    formatos_esperados = [
        "Galicia Office — hoja 'Movimientos' (Débitos/Créditos separados, "
        "con Grupo de Conceptos y Saldo)",
    ]

    # -----------------------------------------------------------------
    # LECTURA DEL EXTRACTO
    # -----------------------------------------------------------------
    def cargar_extracto(self, path):
        """
        Carga el Excel del Banco Galicia y lo normaliza al formato interno.

        El extracto trae Débitos y Créditos en columnas separadas, ambos
        con valor positivo, más una columna Saldo acumulada. El saldo
        inicial del período no viene explícito: se deriva restándole al
        saldo de la primera fila el monto de ese mismo movimiento.
        """
        df = _leer_hoja_movimientos(path)
        df = df.dropna(how="all").reset_index(drop=True)

        out = pd.DataFrame()
        out["fecha"] = pd.to_datetime(
            df["Fecha"], dayfirst=True, errors="coerce"
        ).dt.date

        # Débitos y Créditos vienen ambos positivos → monto con signo:
        # positivo = ingreso, negativo = egreso (igual que el resto del sistema).
        debitos = df["Débitos"].apply(parse_monto)
        creditos = df["Créditos"].apply(parse_monto)
        out["monto"] = creditos - debitos
        out["credito"] = creditos
        out["debito"] = -debitos

        # Descripción: el concepto principal más la contraparte cuando existe.
        # "Leyendas Adicionales 1" suele traer el nombre del titular y
        # "Leyendas Adicionales 2" el CUIT — sirven para identificar al
        # tercero cuando hay que revisar un movimiento a mano.
        descripcion = df["Descripción"].fillna("").astype(str).str.strip()
        leyenda1 = _col_texto(df, "Leyendas Adicionales 1")
        leyenda2 = _col_texto(df, "Leyendas Adicionales 2")
        out["descripcion_orig"] = (
            descripcion + " " + leyenda1 + " " + leyenda2
        ).str.strip().str.replace(r"\s+", " ", regex=True)

        # Referencia: número de comprobante cuando está informado.
        out["referencia"] = _col_texto(df, "Número de Comprobante")

        # Grupo de Conceptos nativo del banco — se conserva porque es la
        # base de la clasificación de gastos bancarios.
        out["grupo_concepto"] = _col_texto(df, "Grupo de Conceptos")

        out["descripcion_norm"] = out["descripcion_orig"].apply(normalizar_texto)
        out["referencia_norm"] = out["referencia"].apply(normalizar_texto)

        # Filtrar filas sin fecha válida o sin importe
        out = out[out["fecha"].notna() & (out["monto"].abs() > 0)].reset_index(drop=True)

        out["origen"] = "BANCO"
        out["fila_origen"] = out.index + 2
        out["match_id"] = None
        out["estado"] = "pendiente"

        # === Saldos ===
        saldos = pd.to_numeric(df["Saldo"], errors="coerce")
        saldo_final = None
        saldo_inicial = None
        if saldos.notna().any():
            saldo_final = float(saldos.dropna().iloc[-1])
            # El saldo inicial no viene explícito: se deriva del primer
            # movimiento (saldo posterior menos el importe del movimiento).
            primer_valido = saldos.first_valid_index()
            if primer_valido is not None:
                monto_primero = (
                    parse_monto(df.at[primer_valido, "Créditos"])
                    - parse_monto(df.at[primer_valido, "Débitos"])
                )
                saldo_inicial = float(saldos[primer_valido]) - monto_primero

        out.attrs["formato"] = "galicia_movimientos"
        out.attrs["saldo_inicial_detectado"] = saldo_inicial
        out.attrs["saldo_final_detectado"] = saldo_final
        out.attrs["path_archivo"] = path

        return out

    # -----------------------------------------------------------------
    # MATCHING ESPECÍFICO
    # -----------------------------------------------------------------
    def pasadas_matching_especificas(self, crm, banco, matches):
        """
        Ejecuta las pasadas específicas de Galicia:

            1. Gastos bancarios agrupados: todos los movimientos del banco
               de los grupos "Impuestos" y "Comisiones" contra el asiento
               único "- Proveedores" que el CRM registra al cierre del mes.

        El resto (recibos, pagos, FIMA, transferencias entre cuentas
        propias) matchea 1:1 por monto y fecha, y lo resuelven las pasadas
        comunes del núcleo.
        """
        _pasada_gastos_bancarios_agrupados(crm, banco, matches)

    # -----------------------------------------------------------------
    # AJUSTES SUGERIDOS
    # -----------------------------------------------------------------
    def calcular_ajustes_sugeridos(self, discrepancias, banco_df=None, crm_df=None):
        """
        Detecta ajustes automáticos para Galicia:

        1. Gastos bancarios no contabilizados: cuando el banco cobró
           impuestos y comisiones durante el mes pero el CRM todavía no
           registró el asiento agrupado "- Proveedores" que los absorbe.
           Es un desfase de timing habitual: el asiento se carga cuando
           contabilidad procesa el resumen del mes.

        2. Débitos pendientes de contabilización: recibos cargados en el
           CRM sobre el cierre del mes que el banco todavía no acreditó.
        """
        sugeridos = []

        sug = _detectar_gastos_bancarios_pendientes(banco_df)
        if sug:
            sugeridos.append(sug)

        sug_deb = _detectar_debitos_pendientes(discrepancias)
        if sug_deb:
            sugeridos.append(sug_deb)

        return sugeridos


# =====================================================================
# HELPERS DE LECTURA
# =====================================================================
def _leer_hoja_movimientos(path):
    """
    Lee la hoja de movimientos del extracto de Galicia.

    El archivo suele traer hojas auxiliares (análisis manual de la
    contadora), por eso se busca la hoja "Movimientos" explícitamente en
    vez de tomar la primera.
    """
    try:
        return pd.read_excel(path, sheet_name=HOJA_MOVIMIENTOS, header=0)
    except (ValueError, KeyError):
        # Si el nombre de la hoja cambió, caer a la primera hoja del archivo.
        return leer_excel(path, header=0)


def _col_texto(df, nombre):
    """Devuelve una columna como texto limpio, o cadena vacía si no existe."""
    if nombre in df.columns:
        return df[nombre].fillna("").astype(str).str.strip()
    return pd.Series([""] * len(df), index=df.index)


def _es_gasto_bancario(grupo_concepto):
    """
    Indica si un movimiento es un gasto bancario según el campo nativo
    "Grupo de Conceptos" del extracto (ej. "000901 - Impuestos").
    """
    texto = str(grupo_concepto)
    return any(g in texto for g in GRUPOS_GASTOS_BANCARIOS)


# =====================================================================
# MATCHING: GASTOS BANCARIOS AGRUPADOS
# =====================================================================
def _pasada_gastos_bancarios_agrupados(crm, banco, matches):
    """
    Matchea todos los gastos bancarios del mes (impuestos y comisiones)
    contra el asiento único "- Proveedores" del CRM.

    Patrón detectado en datos reales: el banco cobra decenas de cargos
    chicos a lo largo del mes (Imp. Ley 25.413 débito y crédito, Ing.
    Brutos s/ Cred, IVA, percepciones, comisión de servicio de cuenta),
    y contabilidad los registra en un solo asiento "- Proveedores" al
    cierre. Se matchean todos como grupo cuando la suma coincide.
    """
    if "grupo_concepto" not in banco.columns:
        return

    mask_bco = (
        (banco["estado"] == "pendiente")
        & banco["grupo_concepto"].apply(_es_gasto_bancario)
    )
    gastos = banco[mask_bco]
    if gastos.empty:
        return

    suma_gastos = float(gastos["monto"].sum())

    # Buscar el asiento agrupado del CRM: "- Proveedores", mismo signo.
    mask_crm = (
        (crm["estado"] == "pendiente")
        & crm["descripcion_orig"].astype(str).str.strip().str.contains(
            CONCEPTO_GASTOS_CRM, na=False, regex=False
        )
    )
    candidatos = crm[mask_crm]
    if candidatos.empty:
        return

    # Elegir el asiento cuyo monto coincida con la suma de los gastos.
    i_crm = None
    for idx, fila in candidatos.iterrows():
        if abs(float(fila["monto"]) - suma_gastos) <= TOLERANCIA_GASTOS:
            i_crm = idx
            break

    if i_crm is None:
        return

    indices_bco = list(gastos.index)
    mid = f"M{len(matches):04d}"
    crm.at[i_crm, "match_id"] = mid
    crm.at[i_crm, "estado"] = "conciliado"
    for i_bco in indices_bco:
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "conciliado"

    matches.append({
        "match_id": mid,
        "tipo": f"Gastos bancarios agrupados ({len(indices_bco)} banco ↔ 1 CRM)",
        "confianza": "Alta",
        "razon_ia": (
            f"Suma de {len(indices_bco)} cargos de impuestos y comisiones "
            f"del banco = {suma_gastos:,.2f}, coincide con el asiento "
            f"'{CONCEPTO_GASTOS_CRM}' del CRM."
        ),
        "i_crm": i_crm,
        "i_crm_lista": [i_crm],
        "i_bco": indices_bco[0],
        "i_bco_lista": indices_bco,
        "diferencia_monto": round(abs(suma_gastos - float(crm.at[i_crm, "monto"])), 2),
        "diferencia_dias": 0,
        "es_agrupado": True,
        "tipo_agrupado": "N_banco_a_1_crm",
    })

    print(f"  → Gastos bancarios: {len(indices_bco)} cargos ↔ 1 asiento CRM "
          f"(suma ${suma_gastos:,.2f})")


# =====================================================================
# AJUSTES SUGERIDOS: GASTOS BANCARIOS PENDIENTES
# =====================================================================
def _detectar_gastos_bancarios_pendientes(banco_df):
    """
    Detecta los gastos bancarios que quedaron huérfanos del matching:
    el banco los cobró pero el CRM todavía no registró el asiento
    agrupado que los absorbe.

    Como el banco ya descontó esa plata y el CRM no lo refleja, el ajuste
    va con el mismo signo (negativo) para que el saldo calculado baje y
    coincida con el extracto.
    """
    if banco_df is None or "grupo_concepto" not in banco_df.columns:
        return None

    try:
        mask = (
            (banco_df["estado"] == "pendiente")
            & banco_df["grupo_concepto"].apply(_es_gasto_bancario)
        )
        pendientes = banco_df[mask]
        if pendientes.empty:
            return None

        total = float(pendientes["monto"].sum())
        cantidad = len(pendientes)

        if abs(total) < 1.0:
            return None

        return {
            "concepto": "Gastos bancarios no contabilizados",
            "monto": round(total, 2),
            "explicacion": (
                f"El banco cobró {cantidad} cargo(s) de impuestos y comisiones "
                f"que el CRM todavía no registró en su asiento agrupado de "
                f"cierre. **El monto es referencial — verificá contra el "
                f"resumen del mes antes de aplicar.**"
            ),
            "cantidad_mov": cantidad,
        }
    except Exception:
        return None


# =====================================================================
# AJUSTES SUGERIDOS: DÉBITOS PENDIENTES DE CONTABILIZACIÓN
# =====================================================================
def _detectar_debitos_pendientes(discrepancias):
    """
    Recibos del CRM sobre el cierre del mes (día 28 en adelante) que no
    tienen contrapartida en el extracto: el cobro ya se registró en el
    CRM pero el banco lo acredita recién el mes siguiente.

    Se cargan con signo NEGATIVO para neutralizar ese cobro en el mes
    corriente, igual criterio que el resto de los bancos del sistema.
    """
    if not discrepancias:
        return None

    total = 0.0
    cantidad = 0
    for d in discrepancias:
        if d.get("origen") != "CRM" or d.get("tipo") != "FALTANTE EN BANCO":
            continue
        if d.get("monto", 0) <= 0:  # solo cobros
            continue
        fecha = d.get("fecha")
        if not fecha or not hasattr(fecha, "day") or fecha.day < 28:
            continue
        # Solo recibos (Rc X / RcM X / Recibo)
        desc_up = str(d.get("descripcion", "")).upper()
        if not ("RC X" in desc_up or "RCM X" in desc_up or "RECIBO" in desc_up):
            continue
        total += d["monto"]
        cantidad += 1

    if cantidad == 0 or abs(total) < 1000:
        return None

    return {
        "concepto": "Débitos pendientes de contabilización",
        "monto": round(-total, 2),
        "explicacion": (
            f"Hay {cantidad} recibo(s) del CRM con fecha del cierre del mes "
            f"que el banco todavía no acreditó. Probablemente aparezcan en el "
            f"extracto del mes siguiente. **El monto es referencial — "
            f"verificá cada operación antes de aplicar.**"
        ),
        "cantidad_mov": cantidad,
    }
