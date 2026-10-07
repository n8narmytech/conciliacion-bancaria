"""
Perfil específico del Banco BBVA.

Implementa toda la lógica particular de BBVA:
    - Lectura de sus dos formatos de extracto:
        * "BBVA clásico" (Crédito + Débito en columnas separadas)
        * "Extracto de Cuenta" (Importe con signo, formato nuevo)
    - Selección automática de la hoja correcta (Movimientos Históricos)
    - Detección de saldos (inicial y final del período)
    - Matching de cupones VISA/MASTER agrupados por ID de lote
    - Ajustes automáticos: Liq Master pendiente, Débitos pendientes,
      Dif gs bancarios (3 estrategias)
"""

import re
import pandas as pd

from bancos.base import Banco
from nucleo.utilidades import leer_excel, normalizar_texto, parse_monto


# =====================================================================
# CONFIGURACIÓN DE COLUMNAS - FORMATO CLÁSICO
# =====================================================================
# Mapeo del formato clásico BBVA (Crédito + Débito en columnas separadas).
COLS_BANCO_CLASICO = {
    "fecha": "Fecha",
    "debito": "Débito",
    "credito": "Crédito",
    "descripcion": "Concepto",
    "referencia": "",
}
BANCO_HEADER_ROW_DEFAULT = 6


# =====================================================================
# CLASE BBVA
# =====================================================================
class BBVA(Banco):
    """Perfil de conciliación para el banco BBVA."""

    nombre = "BBVA"
    codigo = "bbva"
    formatos_esperados = [
        "BBVA clásico (Movimientos Históricos con Crédito/Débito)",
        "Extracto de Cuenta (formato nuevo, columna Importe)",
    ]

    # -----------------------------------------------------------------
    # LECTURA DEL EXTRACTO
    # -----------------------------------------------------------------
    def cargar_extracto(self, path):
        """
        Carga el Excel del banco BBVA. Detecta automáticamente si es:
            - Formato "Extracto de Cuenta" (nuevo, columna única Importe)
            - Formato BBVA clásico (Crédito y Débito en columnas separadas)
        """
        fmt = _detectar_formato_banco(path)
        df = _leer_excel_banco(path, header=fmt["header_row"])
        df = df.dropna(how="all").reset_index(drop=True)

        out = pd.DataFrame()

        if fmt["tipo"] == "extracto_cuenta":
            # === FORMATO NUEVO "Extracto de Cuenta" ===
            # Columnas: Concepto/Cod.Op. | Fecha | Comprobante | Sucursal | Importe |
            #           Descripción | Cod.Op.Bco. | CUIT | Denominación | Saldo
            out["fecha"] = pd.to_datetime(df["Fecha"], dayfirst=True, errors="coerce").dt.date
            # Importe ya viene con signo (+ créditos, - débitos)
            out["monto"] = df["Importe"].apply(parse_monto)

            # Construir descripción: Concepto/Cod.Op. + Comprobante + Descripción
            # Incluimos el Comprobante porque para cupones MASTER/VISA es el ID del
            # lote que se usa para agrupar contra los LIQ-TC del CRM.
            concepto_op = df["Concepto/Cod.Op."].fillna("").astype(str).str.strip()
            descripcion = df["Descripción"].fillna("").astype(str).str.strip()
            if "Comprobante" in df.columns:
                comprobante = df["Comprobante"].fillna("").astype(str).str.strip()
            else:
                comprobante = ""
            out["descripcion_orig"] = (
                concepto_op + " " + comprobante + " " + descripcion
            ).str.strip().str.replace(r"\s+", " ", regex=True)

            # Referencia: usamos el Comprobante (número de operación)
            if "Comprobante" in df.columns:
                out["referencia"] = df["Comprobante"].fillna("").astype(str).str.strip()
            else:
                out["referencia"] = ""

            # Compatibilidad: agregar débito/crédito derivados del monto
            out["credito"] = out["monto"].apply(lambda m: m if m > 0 else 0)
            out["debito"] = out["monto"].apply(lambda m: m if m < 0 else 0)

        else:
            # === FORMATO BBVA CLÁSICO ===
            cols = COLS_BANCO_CLASICO
            out["fecha"] = pd.to_datetime(df[cols["fecha"]], dayfirst=True, errors="coerce").dt.date
            out["debito"] = df[cols["debito"]].apply(parse_monto)
            out["credito"] = df[cols["credito"]].apply(parse_monto)

            # Algunos bancos exportan los débitos como negativos, otros positivos
            if (out["debito"] <= 0).all():
                out["monto"] = out["credito"] + out["debito"]
            else:
                out["monto"] = out["credito"] - out["debito"]

            out["descripcion_orig"] = df[cols["descripcion"]].fillna("").astype(str)
            if cols.get("referencia") and cols["referencia"] in df.columns:
                out["referencia"] = df[cols["referencia"]].fillna("").astype(str).str.strip()
            else:
                out["referencia"] = ""

        out["descripcion_norm"] = out["descripcion_orig"].apply(normalizar_texto)
        out["referencia_norm"] = out["referencia"].apply(normalizar_texto)

        # Filtrar filas sin fecha o sin monto
        out = out[out["fecha"].notna() & (out["monto"].abs() > 0)].reset_index(drop=True)

        out["origen"] = "BANCO"
        out["fila_origen"] = out.index + 2
        out["match_id"] = None
        out["estado"] = "pendiente"

        # Metadata detectada
        saldo_final = _detectar_saldo_final(path, fmt)
        saldo_inicial = fmt["saldo_inicial"]

        # El formato clásico no informa el saldo con que abre el período,
        # pero trae el de cierre ("Saldo Disponible") y TODOS los movimientos
        # del mes, así que la apertura se obtiene restándolos. Verificado
        # contra marzo-junio 2026: la apertura calculada de cada mes coincide
        # al centavo con el cierre del extracto anterior.
        if saldo_inicial is None and saldo_final is not None and len(out):
            saldo_inicial = round(float(saldo_final) - float(out["monto"].sum()), 2)

        out.attrs["formato"] = fmt["tipo"]
        out.attrs["saldo_inicial_detectado"] = saldo_inicial
        out.attrs["path_archivo"] = path
        out.attrs["saldo_final_detectado"] = saldo_final

        return out

    # -----------------------------------------------------------------
    # MATCHING ESPECÍFICO (se completa en el siguiente bloque)
    # -----------------------------------------------------------------
    def es_gasto_bancario(self, descripcion):
        """
        Indica si una descripción del extracto corresponde a un cargo del
        banco (impuestos, comisiones, percepciones). Lo usa el núcleo para
        saber qué movimientos absorbe el asiento agrupado del libro.
        """
        return _es_gasto_bancario_bbva(descripcion)

    def pasadas_matching_especificas(self, crm, banco, matches):
        """
        Ejecuta las pasadas específicas de BBVA:
            1. Cupones VISA/MASTER agrupados por ID de lote ↔ Liq-TC.
            2. Sueldos: varios débitos de haberes del mismo día en el banco
               que suman un asiento único del CRM.
        """
        _pasada_cupones_agrupados(crm, banco, matches)
        _pasada_sueldos_agrupados(crm, banco, matches)

    # -----------------------------------------------------------------
    # AJUSTES SUGERIDOS
    # -----------------------------------------------------------------
    def calcular_ajustes_sugeridos(self, discrepancias, banco_df=None, crm_df=None):
        """
        Identifica ajustes que el sistema puede detectar con confianza analizando
        los archivos del BBVA.

        Sugerencias que puede detectar automáticamente:
            1. Liq Master pendiente: cupones MASTER/VISA huérfanos del matching
               (probablemente se asentarán en el CRM el mes siguiente).
            2. Débitos pendientes de contabilización: recibos del CRM del último
               día del mes sin contrapartida en el banco.
            3. Dif gs bancarios: en 3 variantes según formato del archivo del
               banco (hoja "gs bancarios", "Hoja2", o cálculo por patrones).

        Los demás ajustes (Payway, correcciones, etc.) requieren contexto que
        solo tiene la persona haciendo la conciliación.
        """
        sugeridos = []

        # ---------------------------------------------------------
        # 1. Liq Master pendiente
        # ---------------------------------------------------------
        sug = _detectar_liq_master_pendiente(discrepancias)
        if sug:
            sugeridos.append(sug)

        # ---------------------------------------------------------
        # 2. Débitos pendientes (último día del mes)
        # ---------------------------------------------------------
        sug = _detectar_debitos_pendientes(discrepancias)
        if sug:
            sugeridos.append(sug)

        # ---------------------------------------------------------
        # 3. Dif gs bancarios (3 estrategias)
        # ---------------------------------------------------------
        sug = _detectar_dif_gs_bancarios(banco_df, crm_df)
        if sug:
            sugeridos.append(sug)

        return sugeridos


# =====================================================================
# HELPERS DE LECTURA (privados al módulo)
# =====================================================================
def _leer_excel_banco(archivo, header=0):
    """
    Lee el Excel del banco eligiendo la hoja correcta.

    Si el archivo tiene una hoja "Movimientos Históricos" (BBVA clásico), la
    prefiere sobre "Movimientos del Día" (que solo trae los movimientos del
    día actual y nunca sirve para conciliar un mes completo).
    """
    nombre = ""
    if hasattr(archivo, "name"):
        nombre = archivo.name.lower()
    elif isinstance(archivo, str):
        nombre = archivo.lower()

    try:
        if nombre.endswith(".xls"):
            xl = pd.ExcelFile(archivo, engine="xlrd")
        else:
            xl = pd.ExcelFile(archivo, engine="openpyxl")
        hojas = xl.sheet_names

        hoja_objetivo = None
        for h in hojas:
            if "histórico" in h.lower() or "historico" in h.lower():
                hoja_objetivo = h
                break

        if hoja_objetivo:
            if nombre.endswith(".xls"):
                return pd.read_excel(archivo, sheet_name=hoja_objetivo,
                                     engine="xlrd", header=header)
            else:
                return pd.read_excel(archivo, sheet_name=hoja_objetivo,
                                     engine="openpyxl", header=header)
    except Exception:
        pass

    return leer_excel(archivo, header=header)


def _detectar_formato_banco(archivo):
    """
    Detecta el formato del extracto leyendo las primeras filas.

    Devuelve un dict con:
        - tipo: "extracto_cuenta" | "bbva_clasico"
        - header_row: fila del encabezado (0-indexed)
        - saldo_inicial: float | None
    """
    # IMPORTANTE: leer sin dropna para que los índices coincidan con las filas reales
    df_preview = _leer_excel_banco(archivo, header=None)

    saldo_inicial = None
    header_row = None
    tipo = None

    for i in range(min(20, len(df_preview))):
        fila_valores = [v for v in df_preview.iloc[i].values if pd.notna(v)]
        if not fila_valores:
            continue
        fila_texto = " ".join(str(v) for v in fila_valores)
        fila_upper = fila_texto.upper()

        # Detectar saldo inicial en formato "Extracto de Cuenta"
        if "SALDO INICIAL" in fila_upper:
            for v in df_preview.iloc[i].values:
                try:
                    val = float(v)
                    if abs(val) > 1:
                        saldo_inicial = val
                        break
                except (ValueError, TypeError):
                    continue

        # Header del formato nuevo (tiene "Concepto/Cod.Op." e "Importe")
        if "CONCEPTO/COD" in fila_upper and "IMPORTE" in fila_upper:
            tipo = "extracto_cuenta"
            header_row = i
            break

        # Header del formato BBVA clásico (tiene "Crédito" y "Débito" separados)
        if "CRÉDITO" in fila_upper and "DÉBITO" in fila_upper:
            tipo = "bbva_clasico"
            header_row = i
            break

    if tipo is None:
        # Fallback
        tipo = "bbva_clasico"
        header_row = BANCO_HEADER_ROW_DEFAULT

    return {
        "tipo": tipo,
        "header_row": header_row,
        "saldo_inicial": saldo_inicial,
    }


def _detectar_saldo_final(archivo, fmt):
    """
    Intenta extraer el saldo final del PERÍODO del archivo bancario.

    En el formato BBVA clásico, el saldo final es el "Saldo Disponible" del
    último movimiento del mes (no el "Saldo:" del encabezado, que muestra el
    saldo actual al generar el reporte, posterior al cierre del mes).

    Estrategias en orden:
    1. "Saldo Final:" explícito en las últimas filas.
    2. En formato 'extracto_cuenta': último valor de la columna 'Saldo'.
    3. BBVA clásico: 'Saldo Disponible' del último movimiento del período.
    4. Fallback: 'Saldo:' del encabezado (menos preciso).
    """
    try:
        df_full = _leer_excel_banco(archivo, header=None)

        # Estrategia 1: "Saldo Final:" explícito
        for i in range(len(df_full) - 1, max(0, len(df_full) - 10), -1):
            fila = df_full.iloc[i]
            texto = " ".join(str(v) for v in fila.values if pd.notna(v)).upper()
            if "SALDO FINAL" in texto:
                for v in fila.values:
                    try:
                        val = float(v)
                        if abs(val) > 1:
                            return val
                    except (ValueError, TypeError):
                        continue

        # Estrategia 2: formato "extracto_cuenta" → columna Saldo del último mov
        if fmt["tipo"] == "extracto_cuenta":
            df = _leer_excel_banco(archivo, header=fmt["header_row"])
            if "Saldo" in df.columns:
                saldos = df["Saldo"].dropna()
                if len(saldos) > 0:
                    return float(saldos.iloc[-1])

        # Estrategia 3: BBVA clásico → "Saldo Disponible" del último movimiento
        # En BBVA los movimientos vienen en orden inverso (más reciente primero),
        # entonces buscamos "Saldo Disponible" en las primeras filas de datos.
        if fmt["tipo"] == "bbva_clasico":
            for i in range(fmt["header_row"] + 1, min(fmt["header_row"] + 5, len(df_full))):
                fila = df_full.iloc[i]
                for v in fila.values:
                    if pd.isna(v):
                        continue
                    s = str(v).strip()
                    if "SALDO DISPONIBLE" in s.upper():
                        try:
                            partes = s.split(":")
                            if len(partes) >= 2:
                                num_str = partes[-1].strip()
                                # Formato argentino: 1.107.520,13
                                num_clean = num_str.replace(".", "").replace(",", ".")
                                val = float(num_clean)
                                if abs(val) > 1:
                                    return val
                        except (ValueError, TypeError):
                            continue

        # Estrategia 4 (fallback): "Saldo:" del encabezado
        for i in range(min(10, len(df_full))):
            fila = df_full.iloc[i]
            valores_str = [str(v) for v in fila.values if pd.notna(v)]
            texto = " ".join(valores_str).upper()
            if ("SALDO:" in texto and "DISPONIBLE" not in texto
                    and "INICIAL" not in texto and "FINAL" not in texto):
                for v in fila.values:
                    if pd.isna(v):
                        continue
                    try:
                        if isinstance(v, (int, float)):
                            if abs(v) > 1:
                                return float(v)
                        s = str(v).strip()
                        if s.upper().startswith("SALDO"):
                            continue
                        s_clean = s.replace(".", "").replace(",", ".")
                        val = float(s_clean)
                        if abs(val) > 1:
                            return val
                    except (ValueError, TypeError):
                        continue
    except Exception:
        pass
    return None


# =====================================================================
# MATCHING ESPECÍFICO: CUPONES AGRUPADOS
# =====================================================================
def _pasada_cupones_agrupados(crm, banco, matches):
    """
    Matching especial para asientos LIQ-TC del CRM contra cupones de tarjeta
    del banco.

    Patrón detectado en datos reales: el banco recibe muchos cupones
    individuales (CUPONES PRIS, PAGO VISA) que el contador agrupa en pocos
    asientos LIQ-TC en el CRM. Los cupones del banco tienen un "número
    interno" en su concepto; se agrupan por ese ID y se suman (neto).
    Si la suma cuadra con un LIQ-TC del CRM, matcheamos todos como
    conciliados (1:N).
    """
    # 1. Identificar los LIQ-TC del CRM pendientes
    liq_tc_mask = (crm["estado"] == "pendiente") & (
        crm["descripcion_orig"].str.upper().str.contains(
            "LIQ-TC|LIQ TC|LIQUIDACION", na=False, regex=True)
        | crm["contraparte_orig"].str.upper().str.contains(
            "LIQ-TC|LIQ TC|LIQUIDACION", na=False, regex=True)
    )
    liq_tcs = crm[liq_tc_mask].copy()
    if liq_tcs.empty:
        return

    # 2. Identificar cupones de tarjeta en el banco
    cup_mask = (banco["estado"] == "pendiente") & (
        banco["descripcion_orig"].str.upper().str.contains(
            "CUPONES|VISA|MASTERCARD|MASTER|TARJ", na=False, regex=True
        )
    )
    cupones = banco[cup_mask].copy()
    if cupones.empty:
        return

    # 3. Extraer un identificador numérico del concepto de cada cupón
    def extraer_id_cupon(concepto):
        # Secuencia larga de dígitos (8+) — es el ID interno del lote
        m = re.search(r"\d{8,}", str(concepto))
        return m.group() if m else None

    cupones["cupon_id_full"] = cupones["descripcion_orig"].apply(extraer_id_cupon)
    cupones = cupones[cupones["cupon_id_full"].notna()].copy()
    if cupones.empty:
        return

    # IMPORTANTE: a veces el mismo lote aparece con IDs distintos (créditos
    # vs débitos). Nos quedamos con los últimos 10 dígitos, que identifican
    # el lote sin importar el tipo.
    cupones["cupon_id"] = cupones["cupon_id_full"].str[-10:]

    # 4. Agrupar y sumar
    grupos = cupones.groupby("cupon_id").agg(
        suma=("monto", "sum"),
        cantidad=("monto", "count"),
        indices=("monto", lambda s: list(s.index)),
    ).reset_index()

    print(f"  → Cupones agrupados por ID: {len(grupos)} grupos detectados en el banco")
    for _, g in grupos.iterrows():
        print(f"     ID {g['cupon_id']}: {g['cantidad']} cupones, suma neta {g['suma']:,.2f}")

    # 5. Buscar matches
    next_id = len(matches)
    matcheados_count = 0
    for i_crm, fila_crm in liq_tcs.iterrows():
        if crm.at[i_crm, "estado"] != "pendiente":
            continue
        monto_crm = fila_crm["monto"]

        for _, grupo in grupos.iterrows():
            suma_grupo = grupo["suma"]
            indices_bco = grupo["indices"]

            if any(banco.at[i, "estado"] != "pendiente" for i in indices_bco):
                continue

            diff = abs(monto_crm - suma_grupo)
            max_abs = max(abs(monto_crm), abs(suma_grupo))
            pct = (diff / max_abs * 100) if max_abs > 0 else 0
            if diff <= 1.0 or pct <= 0.1:
                mid = f"M{next_id:04d}"
                crm.at[i_crm, "match_id"] = mid
                crm.at[i_crm, "estado"] = "conciliado"

                for i_bco in indices_bco:
                    banco.at[i_bco, "match_id"] = mid
                    banco.at[i_bco, "estado"] = "conciliado"

                matches.append({
                    "match_id": mid,
                    "tipo": f"LIQ-TC ↔ Cupones banco ({len(indices_bco)} mov)",
                    "confianza": "Alta",
                    "razon_ia": (
                        f"Suma de {len(indices_bco)} cupones del banco "
                        f"(ID {grupo['cupon_id']}) = {suma_grupo:,.2f}, "
                        f"coincide con LIQ-TC del CRM."
                    ),
                    "i_crm": i_crm,
                    "i_crm_lista": [i_crm],
                    "i_bco": indices_bco[0],
                    "i_bco_lista": indices_bco,
                    "diferencia_monto": round(diff, 2),
                    "diferencia_dias": 0,
                    "es_agrupado": True,
                    "tipo_agrupado": "1_a_N",
                    "suma_bco": round(suma_grupo, 2),
                })
                next_id += 1
                matcheados_count += 1
                break

    if matcheados_count > 0:
        print(f"  → {matcheados_count} LIQ-TC matcheados con sus cupones bancarios")


# =====================================================================
# MATCHING ESPECÍFICO: SUELDOS AGRUPADOS (DÍA + SUMA)
# =====================================================================
def _pasada_sueldos_agrupados(crm, banco, matches, dias_tol=5):
    """
    Matchea los pagos de haberes: el banco emite un débito por lote de
    empleados ("OG-DEBITO ... HABERES", "OG-DEB./CRED ... HABERES") y el
    CRM los agrupa en un asiento único ("Op X ... Transferencia Bancaria")
    del mismo día.

    Misma mecánica N banco → 1 CRM que ya usa Santander para sus sueldos,
    adaptada al patrón de texto propio de BBVA.
    """
    mask_bco = (
        (banco["estado"] == "pendiente")
        & banco["descripcion_orig"].str.contains(r"HABERES", regex=True, na=False, case=False)
        & (banco["monto"] < 0)
    )
    grupo_banco = banco[mask_bco]
    if grupo_banco.empty:
        return

    next_id = len(matches)
    total_matcheado = 0

    for fecha, movs_dia in grupo_banco.groupby("fecha"):
        indices_bco = [i for i in movs_dia.index if banco.at[i, "estado"] == "pendiente"]
        if not indices_bco:
            continue
        suma_banco = float(banco.loc[indices_bco, "monto"].sum())
        if abs(suma_banco) < 0.01:
            continue

        # Buscar en el CRM un asiento pendiente por el mismo monto (±dias_tol)
        candidatos = crm[
            (crm["estado"] == "pendiente")
            & ((crm["monto"] - suma_banco).abs() < 0.01)
        ]
        mejor_idx = None
        mejor_dist = None
        for idx, r in candidatos.iterrows():
            try:
                dist = abs((pd.Timestamp(r["fecha"]) - pd.Timestamp(fecha)).days)
            except Exception:
                dist = 999
            if dist > dias_tol:
                continue
            if mejor_dist is None or dist < mejor_dist:
                mejor_dist = dist
                mejor_idx = idx

        if mejor_idx is None:
            continue

        mid = f"M{next_id:04d}"
        crm.at[mejor_idx, "match_id"] = mid
        crm.at[mejor_idx, "estado"] = "conciliado"
        for i_bco in indices_bco:
            banco.at[i_bco, "match_id"] = mid
            banco.at[i_bco, "estado"] = "conciliado"

        matches.append({
            "match_id": mid,
            "tipo": f"Sueldos ({len(indices_bco)} banco ↔ 1 CRM)",
            "confianza": "Alta",
            "razon_ia": (
                f"Suma de {len(indices_bco)} débito(s) de haberes del banco "
                f"= {suma_banco:,.2f}, coincide con asiento del CRM."
            ),
            "i_crm": mejor_idx,
            "i_crm_lista": [mejor_idx],
            "i_bco": indices_bco[0],
            "i_bco_lista": indices_bco,
            "diferencia_monto": 0.0,
            "diferencia_dias": mejor_dist,
            "es_agrupado": True,
            "tipo_agrupado": "N_banco_a_1_crm",
        })
        next_id += 1
        total_matcheado += 1

    if total_matcheado > 0:
        print(f"  → Sueldos (día+suma): {total_matcheado} grupo(s) matcheado(s)")


# =====================================================================
# AJUSTES SUGERIDOS: LIQ MASTER PENDIENTE
# =====================================================================
def _detectar_liq_master_pendiente(discrepancias):
    """
    Detecta cupones MASTER/VISA del banco huérfanos del matching (no tienen
    asiento LIQ-TC asociado en el CRM). Probablemente se asentarán el mes
    siguiente.

    Excluye PAGO VISA / PAGO MASTER porque son débitos del banco a Visa,
    no cupones de tarjeta.
    """
    liq_pendiente_monto = 0
    liq_pendiente_cantidad = 0
    for d in discrepancias:
        if d["origen"] != "BANCO":
            continue
        desc = d["descripcion"].upper()
        tipo = d.get("tipo", "")
        if "PAGO VISA" in desc or "PAGO MASTER" in desc:
            continue
        es_cupon = (
            "CUPONES" in tipo or "CUPON" in desc
            or " MASTER" in desc or " VISA" in desc
            or desc.startswith("MASTER") or desc.startswith("VISA")
        )
        if es_cupon:
            liq_pendiente_monto += d["monto"]
            liq_pendiente_cantidad += 1

    if abs(liq_pendiente_monto) >= 100:
        return {
            "concepto": "Liq Master pendiente",
            "monto": round(liq_pendiente_monto, 2),
            "explicacion": (
                f"Hay {liq_pendiente_cantidad} cupón(es) de tarjeta del banco "
                f"sin asiento LIQ-TC asociado en el CRM. Probablemente se asienten "
                f"en el CRM el mes siguiente. **El monto es referencial — verificalo "
                f"con tu criterio antes de aplicarlo.**"
            ),
            "cantidad_mov": liq_pendiente_cantidad,
        }
    return None


# =====================================================================
# AJUSTES SUGERIDOS: DÉBITOS PENDIENTES DEL ÚLTIMO DÍA
# =====================================================================
def _detectar_debitos_pendientes(discrepancias):
    """
    Recibos del CRM con fecha del último día del mes (28-31) que no tienen
    contrapartida en el banco. Se acreditarán el mes siguiente.

    Se cargan con signo NEGATIVO porque hay que "neutralizar" ese cobro
    del CRM en este mes (la plata aún no entró al banco).
    """
    deb_monto = 0
    deb_cantidad = 0
    for d in discrepancias:
        if d["origen"] != "CRM" or d["tipo"] != "FALTANTE EN BANCO":
            continue
        if d["monto"] <= 0:  # Solo cobros
            continue
        fecha = d.get("fecha")
        if not fecha or not hasattr(fecha, "day"):
            continue
        if fecha.day < 28:
            continue
        # Excluir transferencias internas
        contraparte_up = d.get("contraparte", "").upper()
        if ("BANCO BBVA" in contraparte_up or "BANCO FRANCES" in contraparte_up
                or "BANCO PATAGONIA" in contraparte_up):
            continue
        # Solo recibos
        desc_up = d["descripcion"].upper()
        if not ("RC X" in desc_up or "RCM X" in desc_up or "RECIBO" in desc_up):
            continue
        deb_monto += d["monto"]
        deb_cantidad += 1

    if abs(deb_monto) >= 1000:
        # Signo invertido: cobro CRM positivo → ajuste negativo
        return {
            "concepto": "Débitos pendientes de contabilización",
            "monto": round(-deb_monto, 2),
            "explicacion": (
                f"Hay {deb_cantidad} recibo(s) del CRM con fecha del último día "
                f"del mes que el banco aún no procesó. Probablemente se acrediten "
                f"en el extracto del mes siguiente. **El monto es referencial — "
                f"verificá cada operación antes de aplicar.**"
            ),
            "cantidad_mov": deb_cantidad,
        }
    return None


# =====================================================================
# AJUSTES SUGERIDOS: DIF GS BANCARIOS (3 estrategias)
# =====================================================================
def _detectar_dif_gs_bancarios(banco_df, crm_df):
    """
    Detecta Dif gs bancarios usando 3 estrategias en orden de prioridad:

    1) Hoja "gs bancarios" con columna "Diferencia" (formato Melania clásico):
       - Toma el valor de la columna "Diferencia" en la fila "Total general".

    2) "Hoja2" con etiqueta "dif mes pasado" (formato Melania mayo+):
       - Busca una fila con nota "dif mes pasado" y toma su monto.

    3) Fallback por patrones:
       - Suma cargos del banco identificados como gastos bancarios (según
         patrones de Melania) y los compara contra el asiento agrupado del
         CRM del último día del mes ("- Proveedores" con contraparte
         "Banco BBVA cta cte").
    """
    dif_gs_calculado = None

    # -------------------- ESTRATEGIA 1 y 2: leer hoja auxiliar --------------
    if banco_df is not None:
        path_banco_archivo = banco_df.attrs.get("path_archivo")
        if path_banco_archivo:
            try:
                xl = pd.ExcelFile(path_banco_archivo)

                # Variante 1: hoja "gs bancarios" con columna "Diferencia"
                if "gs bancarios" in xl.sheet_names:
                    df_gs = pd.read_excel(path_banco_archivo, sheet_name="gs bancarios")
                    if "Diferencia" in df_gs.columns and "cod abrev" in df_gs.columns:
                        mask_total = df_gs["cod abrev"].astype(str).str.contains(
                            "Total general", na=False
                        )
                        if mask_total.any():
                            dif_val = df_gs[mask_total].iloc[0]["Diferencia"]
                            if pd.notna(dif_val):
                                dif_gs_calculado = (
                                    "hoja_melania", round(float(dif_val), 2)
                                )

                # Variante 2: "Hoja2" con etiqueta "dif mes pasado"
                if dif_gs_calculado is None and "Hoja2" in xl.sheet_names:
                    df_h2 = pd.read_excel(
                        path_banco_archivo, sheet_name="Hoja2", header=None
                    )
                    if df_h2.shape[1] >= 3:
                        mask_dif = df_h2.iloc[:, 2].astype(str).str.contains(
                            "dif mes pasado", na=False, case=False
                        )
                        if mask_dif.any():
                            monto_dif = df_h2[mask_dif].iloc[0, 1]
                            if pd.notna(monto_dif):
                                dif_gs_calculado = (
                                    "hoja2_melania", round(float(monto_dif), 2)
                                )
            except Exception:
                pass

    # -------------------- ESTRATEGIA 3: fallback por patrones ---------------
    if dif_gs_calculado is None and banco_df is not None and crm_df is not None:
        try:
            mask_gs = banco_df["descripcion_orig"].apply(_es_gasto_bancario_bbva)
            total_banco_gs = float(banco_df[mask_gs]["monto"].sum())
            cantidad_gs = int(mask_gs.sum())

            # Asiento agrupado del CRM: "- Proveedores" + "Banco BBVA cta cte"
            # del último día del mes (28-31)
            mask_asiento = (
                crm_df["descripcion_orig"].astype(str).str.contains(
                    r"^\s*-\s*Proveedores", na=False, regex=True
                )
                & crm_df["contraparte_orig"].astype(str).str.contains(
                    "Banco BBVA", na=False
                )
            )
            asientos = crm_df[mask_asiento].copy()
            if len(asientos) > 0:
                asientos["dia"] = pd.to_datetime(
                    asientos["fecha"], errors="coerce"
                ).dt.day
                ultimo_dia = asientos[asientos["dia"] >= 28]
                asiento_crm = (
                    float(ultimo_dia["monto"].sum()) if len(ultimo_dia) > 0 else 0
                )
            else:
                asiento_crm = 0

            # Solo sugerir si encontramos el asiento del CRM
            if abs(asiento_crm) >= 1000 and cantidad_gs >= 5:
                dif_calc = round(total_banco_gs - asiento_crm, 2)
                dif_gs_calculado = (
                    "calculado", dif_calc, total_banco_gs, asiento_crm, cantidad_gs
                )
        except Exception:
            pass

    # -------------------- FORMATEAR LA SUGERENCIA ---------------------------
    if dif_gs_calculado is None:
        return None

    if dif_gs_calculado[0] == "hoja_melania":
        dif_gs = dif_gs_calculado[1]
        if abs(dif_gs) < 50:
            return None
        return {
            "concepto": "Dif gs bancarios",
            "monto": dif_gs,
            "explicacion": (
                "Extraído de la hoja 'gs bancarios' del archivo del banco "
                "(análisis de Melania)."
            ),
            "cantidad_mov": 0,
        }

    if dif_gs_calculado[0] == "hoja2_melania":
        dif_gs = dif_gs_calculado[1]
        if abs(dif_gs) < 50:
            return None
        return {
            "concepto": "Dif gs bancarios",
            "monto": dif_gs,
            "explicacion": (
                "Extraído de la 'Hoja2' del archivo del banco (análisis de "
                "Melania), fila 'dif mes pasado'. Es el arrastre del residuo "
                "del mes anterior (típicamente el CONS.AP del banco)."
            ),
            "cantidad_mov": 0,
        }

    # Caso "calculado"
    _, dif_gs, total_banco_gs, asiento_crm, cantidad_gs = dif_gs_calculado
    if abs(dif_gs) < 50:
        return None
    return {
        "concepto": "Dif gs bancarios",
        "monto": dif_gs,
        "explicacion": (
            f"Calculado como: {cantidad_gs} cargos del banco "
            f"(${total_banco_gs:,.2f}) − asiento agrupado del CRM "
            f"(${asiento_crm:,.2f}). **Verificá el monto con tu criterio.**"
        ),
        "cantidad_mov": cantidad_gs,
    }


def _es_gasto_bancario_bbva(desc):
    """
    Identifica si un movimiento del banco es un gasto bancario según el
    criterio de Melania (para BBVA):
        - COMISION TRA, COMISION, COM.MANT, COM.TRANS, COM.TRANSF
        - IMPUESTO LEY, IMP.LEY, IVA TASA
        - LEY NRO 25, LEY 25 (impuesto al débito/crédito)
        - PERC.CABA, PERCEPCION
        - REG REC SIRC (retención SIRCREB)
        - OG - DEBITO ... CONS.AP (caso especial)
    """
    desc = str(desc).upper().strip()
    patrones_inicio = [
        "COMISION TRA", "COMISION ", "COM.MANT", "COM.TRANS", "COM.TRANSF",
        "IMPUESTO LEY", "IMP.LEY", "IVA TASA", "LEY NRO 25", "LEY 25",
        "PERC.CABA", "PERCEPCION", "REG REC SIRC",
    ]
    # CONS.AP es un caso especial (no matchea con "starts with")
    if "OG - DEBITO" in desc and "CONS.AP" in desc:
        return True
    return any(desc.startswith(p) for p in patrones_inicio)
