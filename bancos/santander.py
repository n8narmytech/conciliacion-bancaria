"""
Perfil específico del Banco Santander.

Implementa la lógica particular del banco Santander:
    - Lectura del formato Interbanking (Extracto de Cuenta)
    - Matching COMEX (Op X + Proveedores = COB.IMPORT)
    - Matching de certificaciones CO.CERT.VA
    - Matching de préstamos (2 asientos CRM = 1 DEBITOS COBRO DE PRESTAMO)
    - Detección del asiento agrupado de gastos bancarios de fin de mes

NOTA: Este perfil está en construcción. Los patrones fueron validados
con datos reales de mayo 2026 pero pueden requerir ajustes según los
meses que se agreguen.
"""

import re
import pandas as pd

from bancos.base import Banco
from nucleo.utilidades import leer_excel, normalizar_texto, parse_monto


# =====================================================================
# CLASE SANTANDER
# =====================================================================
class Santander(Banco):
    """Perfil de conciliación para el banco Santander (formato Interbanking)."""

    nombre = "Santander"
    codigo = "santander"
    formatos_esperados = [
        "Extracto Interbanking (Excel con columnas Fecha, Descripción, Débito, Crédito, Saldo)",
    ]

    # -----------------------------------------------------------------
    # LECTURA DEL EXTRACTO
    # -----------------------------------------------------------------
    def cargar_extracto(self, path):
        """
        Carga el Excel del banco Santander en formato Interbanking.

        Estructura esperada del archivo:
            - Filas 0-12: metadatos (empresa, cuenta, período, saldos)
            - Fila con "Concepto/Cod.Op." + "Fecha" + "Importe" + "Descripción"
              → header de la tabla
            - Filas siguientes: movimientos
        """
        fmt = _detectar_formato_santander(path)

        df = leer_excel(path, header=fmt["header_row"])
        df = df.dropna(how="all").reset_index(drop=True)

        out = pd.DataFrame()

        # === Fecha ===
        col_fecha = _buscar_columna(df, ["Fecha", "Fecha Concertación", "Fecha Op"])
        out["fecha"] = pd.to_datetime(df[col_fecha], dayfirst=True, errors="coerce").dt.date

        # === Monto ===
        # En Interbanking del Santander viene UNA sola columna "Importe" con signo.
        col_importe = _buscar_columna(df, ["Importe"], obligatorio=False)
        col_debito = _buscar_columna(df, ["Débito", "Debito"], obligatorio=False)
        col_credito = _buscar_columna(df, ["Crédito", "Credito"], obligatorio=False)

        if col_importe:
            out["monto"] = df[col_importe].apply(parse_monto)
            out["credito"] = out["monto"].apply(lambda m: m if m > 0 else 0)
            out["debito"] = out["monto"].apply(lambda m: -m if m < 0 else 0)
        elif col_debito and col_credito:
            out["debito"] = df[col_debito].apply(parse_monto)
            out["credito"] = df[col_credito].apply(parse_monto)
            if (out["debito"] >= 0).all():
                out["monto"] = out["credito"] - out["debito"]
            else:
                out["monto"] = out["credito"] + out["debito"]
        else:
            raise ValueError(
                "No se encontraron columnas de monto en el archivo del Santander "
                "(esperado: Importe o Débito+Crédito)."
            )

        # === Descripción ===
        # La descripción REAL de cada movimiento se arma concatenando:
        #   Concepto/Cod.Op. + Comprobante + Descripción
        # Por ejemplo:
        #   "COB.IMPORT" + "29063661" + "COMEX - TRANSFERENCIA INT"
        col_concepto = _buscar_columna(df, ["Concepto/Cod.Op.", "Concepto"], obligatorio=False)
        col_desc = _buscar_columna(df, ["Descripción", "Descripcion"], obligatorio=False)
        col_comp = _buscar_columna(df, ["Comprobante"], obligatorio=False)

        partes = []
        if col_concepto:
            partes.append(df[col_concepto].fillna("").astype(str).str.strip())
        if col_comp:
            partes.append(df[col_comp].fillna("").astype(str).str.strip())
        if col_desc:
            partes.append(df[col_desc].fillna("").astype(str).str.strip())

        if not partes:
            raise ValueError(
                "No se encontraron columnas de descripción en el archivo del Santander."
            )

        # Concatenar y limpiar dobles espacios
        out["descripcion_orig"] = partes[0]
        for p in partes[1:]:
            out["descripcion_orig"] = out["descripcion_orig"] + " " + p
        out["descripcion_orig"] = (
            out["descripcion_orig"].str.strip().str.replace(r"\s+", " ", regex=True)
        )

        # === Referencia (comprobante como referencia) ===
        if col_comp:
            out["referencia"] = df[col_comp].fillna("").astype(str).str.strip()
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

        # Metadata
        out.attrs["formato"] = "santander_interbanking"
        out.attrs["saldo_inicial_detectado"] = fmt["saldo_inicial"]
        out.attrs["saldo_final_detectado"] = fmt["saldo_final"]
        out.attrs["path_archivo"] = path

        return out

    # -----------------------------------------------------------------
    # MATCHING ESPECÍFICO
    # -----------------------------------------------------------------
    def pasadas_matching_especificas(self, crm, banco, matches):
        """
        Corre las pasadas específicas de Santander en orden:
            1. COMEX (COB.IMPORT ↔ Op X + Proveedores)
            2. Certificaciones (CO.CERT.VA ↔ - Proveedores por monto exacto)
            3. Préstamo (DEBITOS COBRO DE PRESTAMO ↔ 2 asientos CRM)
            4. Agrupaciones día+suma (sueldos, transferencias, TR SNP):
               varios movimientos del banco del mismo día que suman un
               asiento único del CRM.
            5. Cheques clearing / asientos de movimientos bancarios por
               monto exacto (con tolerancia de fecha).
        """
        _pasada_comex(crm, banco, matches)
        _pasada_certificaciones(crm, banco, matches)
        _pasada_prestamo(crm, banco, matches)
        _pasada_cupones_visa_master(crm, banco, matches)
        _pasada_agrupaciones_dia(crm, banco, matches)
        _pasada_cheques_clearing(crm, banco, matches)

    # -----------------------------------------------------------------
    # AJUSTES SUGERIDOS
    # -----------------------------------------------------------------
    def calcular_ajustes_sugeridos(self, discrepancias, banco_df=None, crm_df=None):
        """
        Detecta ajustes automáticos para Santander:

        1. COMEX pendiente de contabilización: operaciones COB.IMPORT que el
           banco ya procesó pero cuyo asiento Op X todavía NO está cargado en
           el CRM. Es un desfase de timing normal en importaciones: la
           operación se registra en el CRM el mes siguiente cuando llega la
           documentación.

           A diferencia de BBVA (que tiene Payway, Liq Master, Dif gs
           bancarios), en Santander este suele ser el único ajuste automático
           relevante, y solo aparece en meses con importaciones sin cerrar.
        """
        sugeridos = []

        sug = _detectar_comex_pendiente(banco_df, crm_df)
        if sug:
            sugeridos.append(sug)

        return sugeridos

    # -----------------------------------------------------------------
    # POSIBLES RECIBOS HUÉRFANOS PARA REVISIÓN MANUAL
    # -----------------------------------------------------------------
    def obtener_posibles_debitos_pendientes(self, crm_df, banco_df):
        """
        Devuelve recibos del CRM huérfanos que podrían ser ajustes reales
        (típicamente casos como Rivero: recibos que aparecen en el CRM pero
        cuya contrapartida bancaria está en otra cuenta o mal clasificada).

        NO incluye asientos "- Proveedores" (gastos bancarios agrupados),
        porque esos YA están registrados en el saldo del CRM y agregarlos
        como ajuste sería duplicar el registro.
        """
        # Usa la lógica genérica del núcleo (busca solo recibos)
        from nucleo.ajustes_comunes import posibles_debitos_pendientes_generico
        return posibles_debitos_pendientes_generico(crm_df, banco_df)


# =====================================================================
# HELPERS DE LECTURA
# =====================================================================
def _buscar_columna(df, nombres, obligatorio=True):
    """Devuelve el primer nombre de columna del DataFrame que matchee."""
    cols_lower = {c.lower().strip(): c for c in df.columns}
    for n in nombres:
        c = cols_lower.get(n.lower().strip())
        if c is not None:
            return c
    if obligatorio:
        raise ValueError(f"No se encontró ninguna de las columnas: {nombres}")
    return None


def _detectar_formato_santander(archivo):
    """
    Detecta la fila del header en un archivo Interbanking de Santander.
    También intenta extraer saldos inicial y final del encabezado.
    """
    df_preview = leer_excel(archivo, header=None)

    header_row = None
    saldo_inicial = None
    saldo_final = None

    for i in range(min(30, len(df_preview))):
        fila_valores = [v for v in df_preview.iloc[i].values if pd.notna(v)]
        if not fila_valores:
            continue
        fila_upper = " ".join(str(v) for v in fila_valores).upper()

        # Saldo inicial en el encabezado
        if header_row is None and "SALDO INICIAL" in fila_upper:
            for v in df_preview.iloc[i].values:
                try:
                    val = float(v)
                    if abs(val) > 1:
                        saldo_inicial = val
                        break
                except (ValueError, TypeError):
                    continue

        # Saldo final en el encabezado
        if header_row is None and "SALDO FINAL" in fila_upper:
            for v in df_preview.iloc[i].values:
                try:
                    val = float(v)
                    if abs(val) > 1:
                        saldo_final = val
                        break
                except (ValueError, TypeError):
                    continue

        # Header de la tabla: tiene FECHA + IMPORTE (formato Interbanking)
        if ("FECHA" in fila_upper and "IMPORTE" in fila_upper
                and ("CONCEPTO" in fila_upper or "DESCRIPCIÓN" in fila_upper
                     or "DESCRIPCION" in fila_upper)):
            header_row = i
            break

    if header_row is None:
        # Fallback: primera fila
        header_row = 0

    # Si no encontramos saldo final en el encabezado, probamos con la columna Saldo
    if saldo_final is None:
        try:
            df = leer_excel(archivo, header=header_row)
            if "Saldo" in df.columns:
                saldos = df["Saldo"].dropna()
                if len(saldos) > 0:
                    saldo_final = float(saldos.iloc[-1])
        except Exception:
            pass

    return {
        "header_row": header_row,
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
    }


# =====================================================================
# MATCHING ESPECÍFICO: COMEX
# =====================================================================
def _pasada_comex(crm, banco, matches):
    """
    Matchea operaciones COMEX (importaciones).

    Patrones detectados en datos reales:

    Caso simple (1:N — una operación COMEX el día):
        En el banco: 1 movimiento "COB.IMPORT ####### COMEX - TRANSFERENCIA INT"
        En el CRM del mismo día:
            - 1 asiento "Op X ####" (pago al proveedor)
            - 1 o más asientos "- Proveedores" (comisiones)
        Suma exacta al céntimo.

    Caso agrupado (M:N — varias operaciones COMEX el mismo día compartiendo
    comisiones):
        En el banco: varios COB.IMPORT del mismo día
        En el CRM: varios Op X + un asiento "- Proveedores" agrupado con
        los gastos de todas las operaciones
        La suma total de un lado = suma total del otro.

    Se prueba primero el caso simple y después el agrupado con los que
    quedaron sin match.
    """
    # 1. COB.IMPORT del banco pendientes
    cob_import_mask = (banco["estado"] == "pendiente") & (
        banco["descripcion_orig"].str.upper().str.contains("COB.IMPORT", na=False)
    )
    cob_imports = banco[cob_import_mask]
    if cob_imports.empty:
        return

    next_id = len(matches)
    matcheados_simple = 0
    matcheados_grupo = 0

    # -------------------- ESTRATEGIA A: caso simple (1:N) -----------------
    for i_bco, fila_bco in cob_imports.iterrows():
        if banco.at[i_bco, "estado"] != "pendiente":
            continue
        monto_bco = fila_bco["monto"]
        fecha_bco = fila_bco["fecha"]

        crm_dia = crm[(crm["fecha"] == fecha_bco) & (crm["estado"] == "pendiente")]

        ops = crm_dia[crm_dia["descripcion_orig"].str.contains(r"\bOp X\b", regex=True, na=False)]
        provs = crm_dia[crm_dia["descripcion_orig"].str.contains(r"^\s*-\s*Proveedores", regex=True, na=False)]

        if ops.empty:
            continue

        match_encontrado = False
        for i_op, op in ops.iterrows():
            monto_op = op["monto"]
            faltante = monto_bco - monto_op

            if abs(faltante) < 0.01:
                _marcar_match_comex(
                    crm, banco, matches, next_id,
                    [i_op], i_bco, monto_bco, "1_a_1"
                )
                next_id += 1
                matcheados_simple += 1
                match_encontrado = True
                break

            provs_disponibles = provs[provs["estado"] == "pendiente"]
            if provs_disponibles.empty:
                continue

            combo = _combinar_provs_para_llegar_a(provs_disponibles, faltante)
            if combo is not None:
                indices_crm = [i_op] + combo
                _marcar_match_comex(
                    crm, banco, matches, next_id,
                    indices_crm, i_bco, monto_bco, "N_a_1"
                )
                next_id += 1
                matcheados_simple += 1
                match_encontrado = True
                break

    # -------------------- ESTRATEGIA B: agrupado por día (M:N) ------------
    # Para cada día que quedaron COB.IMPORT sin match, ver si sumando todos
    # los COB.IMPORT del día contra todos los Op X + Proveedores del CRM del
    # mismo día se llega a coincidencia exacta.
    cob_pendientes = banco[
        (banco["estado"] == "pendiente")
        & (banco["descripcion_orig"].str.upper().str.contains("COB.IMPORT", na=False))
    ]

    for fecha, grupo_dia_banco in cob_pendientes.groupby("fecha"):
        if len(grupo_dia_banco) < 1:
            continue

        suma_banco = grupo_dia_banco["monto"].sum()

        # Del CRM del mismo día: Op X + - Proveedores pendientes
        crm_dia = crm[
            (crm["fecha"] == fecha)
            & (crm["estado"] == "pendiente")
        ]
        ops = crm_dia[crm_dia["descripcion_orig"].str.contains(r"\bOp X\b", regex=True, na=False)]
        provs = crm_dia[crm_dia["descripcion_orig"].str.contains(r"^\s*-\s*Proveedores", regex=True, na=False)]

        if ops.empty or provs.empty:
            continue

        # Todos los Op X del día
        suma_ops = ops["monto"].sum()
        faltante = suma_banco - suma_ops

        # Buscar combinación de Proveedores que cierre exactamente
        combo_provs = _combinar_provs_para_llegar_a(provs, faltante)
        if combo_provs is None:
            continue

        indices_crm = list(ops.index) + combo_provs
        indices_bco = list(grupo_dia_banco.index)

        # Verificación final
        suma_crm_total = crm.loc[indices_crm, "monto"].sum()
        if abs(suma_crm_total - suma_banco) > 0.01:
            continue

        mid = f"M{next_id:04d}"
        for i_crm in indices_crm:
            crm.at[i_crm, "match_id"] = mid
            crm.at[i_crm, "estado"] = "conciliado"
        for i_bco in indices_bco:
            banco.at[i_bco, "match_id"] = mid
            banco.at[i_bco, "estado"] = "conciliado"

        matches.append({
            "match_id": mid,
            "tipo": f"COMEX agrupado ({len(indices_crm)} CRM ↔ {len(indices_bco)} COB.IMPORT)",
            "confianza": "Alta",
            "razon_ia": (
                f"Suma de {len(indices_crm)} asientos del CRM (Op X + Proveedores) "
                f"= {suma_crm_total:,.2f}, coincide con {len(indices_bco)} COB.IMPORT "
                f"del banco del mismo día."
            ),
            "i_crm": indices_crm[0],
            "i_crm_lista": indices_crm,
            "i_bco": indices_bco[0],
            "i_bco_lista": indices_bco,
            "diferencia_monto": 0.0,
            "diferencia_dias": 0,
            "es_agrupado": True,
            "tipo_agrupado": "M_a_N",
        })
        next_id += 1
        matcheados_grupo += 1

    if matcheados_simple > 0 or matcheados_grupo > 0:
        print(
            f"  → COMEX: {matcheados_simple} operaciones simples + "
            f"{matcheados_grupo} agrupadas"
        )


def _combinar_provs_para_llegar_a(provs_df, objetivo, tolerancia=0.01, max_combos=6):
    """
    Busca subconjunto de filas de provs_df cuya suma == objetivo.

    Devuelve lista de índices o None. Limita a subsets de tamaño <= max_combos
    para evitar explosión combinatoria.
    """
    from itertools import combinations
    indices = list(provs_df.index)
    montos = provs_df["monto"].tolist()

    # Primero probar con 1 solo elemento (caso más común)
    for i, m in enumerate(montos):
        if abs(m - objetivo) <= tolerancia:
            return [indices[i]]

    # Después combinaciones de 2 en 2, 3 en 3, hasta max_combos
    for size in range(2, min(len(indices), max_combos) + 1):
        for combo in combinations(range(len(indices)), size):
            suma = sum(montos[j] for j in combo)
            if abs(suma - objetivo) <= tolerancia:
                return [indices[j] for j in combo]

    return None


def _marcar_match_comex(crm, banco, matches, next_id, i_crm_lista, i_bco, monto_bco, tipo):
    mid = f"M{next_id:04d}"
    for i_crm in i_crm_lista:
        crm.at[i_crm, "match_id"] = mid
        crm.at[i_crm, "estado"] = "conciliado"
    banco.at[i_bco, "match_id"] = mid
    banco.at[i_bco, "estado"] = "conciliado"

    matches.append({
        "match_id": mid,
        "tipo": f"COMEX ({len(i_crm_lista)} asiento(s) CRM ↔ 1 COB.IMPORT)",
        "confianza": "Alta",
        "razon_ia": (
            f"Suma de {len(i_crm_lista)} asientos del CRM (Op X + Proveedores) "
            f"= {monto_bco:,.2f}, coincide con COB.IMPORT del banco."
        ),
        "i_crm": i_crm_lista[0],
        "i_crm_lista": i_crm_lista,
        "i_bco": i_bco,
        "i_bco_lista": [i_bco],
        "diferencia_monto": 0.0,
        "diferencia_dias": 0,
        "es_agrupado": True,
        "tipo_agrupado": tipo,
    })


# =====================================================================
# MATCHING ESPECÍFICO: CERTIFICACIONES (CO.CERT.VA)
# =====================================================================
def _pasada_certificaciones(crm, banco, matches):
    """
    Matchea certificaciones de importación:
        - Banco: CO.CERT.VA ####### COMIS NOMINAC DESP IMPORT
        - CRM: "- Proveedores" con contraparte "Banco Santander cta cte"

    Estrategias en orden:
        A) 1:1 por monto exacto y mismo día (típico: 1 certificación = 1 asiento).
        B) N:1 por suma exacta y mismo día (típico: varias certificaciones del
           mismo monto agrupadas en un solo asiento del CRM).
    """
    cert_mask = (banco["estado"] == "pendiente") & (
        banco["descripcion_orig"].str.upper().str.contains("CO.CERT.VA", na=False)
    )
    certs = banco[cert_mask]
    if certs.empty:
        return

    next_id = len(matches)
    matcheados_count = 0
    matcheados_grupo = 0

    # -------------------- ESTRATEGIA A: 1:1 por monto exacto --------------
    for i_bco, fila_bco in certs.iterrows():
        if banco.at[i_bco, "estado"] != "pendiente":
            continue
        monto_bco = fila_bco["monto"]
        fecha_bco = fila_bco["fecha"]

        mask_crm = (
            (crm["fecha"] == fecha_bco)
            & (crm["estado"] == "pendiente")
            & (crm["descripcion_orig"].str.contains(r"^\s*-\s*Proveedores", regex=True, na=False))
            & ((crm["monto"] - monto_bco).abs() < 0.01)
        )
        candidatos = crm[mask_crm]
        if candidatos.empty:
            continue

        i_crm = candidatos.index[0]
        mid = f"M{next_id:04d}"
        crm.at[i_crm, "match_id"] = mid
        crm.at[i_crm, "estado"] = "conciliado"
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "conciliado"

        matches.append({
            "match_id": mid,
            "tipo": "CO.CERT.VA ↔ - Proveedores",
            "confianza": "Alta",
            "i_crm": i_crm,
            "i_bco": i_bco,
            "diferencia_monto": 0.0,
            "diferencia_dias": 0,
        })
        next_id += 1
        matcheados_count += 1

    # -------------------- ESTRATEGIA B: N:1 por suma exacta ---------------
    # Reagrupamos CO.CERT.VA que quedaron pendientes por fecha, y para cada día
    # buscamos si su suma coincide con algún "- Proveedores" del CRM.
    certs_pendientes = banco[
        (banco["estado"] == "pendiente")
        & (banco["descripcion_orig"].str.upper().str.contains("CO.CERT.VA", na=False))
    ]
    if certs_pendientes.empty:
        if matcheados_count > 0:
            print(f"  → CO.CERT.VA: {matcheados_count} certificaciones matcheadas (1:1)")
        return

    for fecha, grupo_dia in certs_pendientes.groupby("fecha"):
        if len(grupo_dia) < 2:
            continue  # con solo una ya se hubiera resuelto en la estrategia A

        suma_grupo = grupo_dia["monto"].sum()

        # Buscar en el CRM un "- Proveedores" del mismo día con ese monto
        mask_crm = (
            (crm["fecha"] == fecha)
            & (crm["estado"] == "pendiente")
            & (crm["descripcion_orig"].str.contains(r"^\s*-\s*Proveedores", regex=True, na=False))
            & ((crm["monto"] - suma_grupo).abs() < 0.01)
        )
        candidatos = crm[mask_crm]
        if candidatos.empty:
            continue

        i_crm = candidatos.index[0]
        indices_bco = list(grupo_dia.index)

        mid = f"M{next_id:04d}"
        crm.at[i_crm, "match_id"] = mid
        crm.at[i_crm, "estado"] = "conciliado"
        for i_bco in indices_bco:
            banco.at[i_bco, "match_id"] = mid
            banco.at[i_bco, "estado"] = "conciliado"

        matches.append({
            "match_id": mid,
            "tipo": f"CO.CERT.VA agrupadas ({len(indices_bco)} certificaciones ↔ 1 - Proveedores)",
            "confianza": "Alta",
            "razon_ia": (
                f"Suma de {len(indices_bco)} CO.CERT.VA del banco = {suma_grupo:,.2f}, "
                f"coincide con asiento - Proveedores del CRM."
            ),
            "i_crm": i_crm,
            "i_crm_lista": [i_crm],
            "i_bco": indices_bco[0],
            "i_bco_lista": indices_bco,
            "diferencia_monto": 0.0,
            "diferencia_dias": 0,
            "es_agrupado": True,
            "tipo_agrupado": "N_a_1_inverso",  # N banco → 1 CRM
        })
        next_id += 1
        matcheados_grupo += 1

    if matcheados_count > 0 or matcheados_grupo > 0:
        print(
            f"  → CO.CERT.VA: {matcheados_count} 1:1 + "
            f"{matcheados_grupo} agrupadas (N:1)"
        )


# =====================================================================
# MATCHING ESPECÍFICO: PRÉSTAMO
# =====================================================================
def _pasada_prestamo(crm, banco, matches):
    """
    Matchea cuotas de préstamo del Santander:
        - Banco: DEBITOS ####### COBRO DE PRESTAMO
        - CRM del mismo día:
            * Un asiento sin concepto (o vacío) con contraparte "Banco Santander"
            * Un asiento "- Proveedores" (los impuestos/intereses)
          La suma de ambos coincide con el monto del banco.
    """
    prestamos_mask = (banco["estado"] == "pendiente") & (
        banco["descripcion_orig"].str.upper().str.contains("COBRO DE PRESTAMO", na=False)
    )
    prestamos = banco[prestamos_mask]
    if prestamos.empty:
        return

    next_id = len(matches)
    matcheados_count = 0

    for i_bco, fila_bco in prestamos.iterrows():
        if banco.at[i_bco, "estado"] != "pendiente":
            continue
        monto_bco = fila_bco["monto"]
        fecha_bco = fila_bco["fecha"]

        # Buscar en CRM del mismo día: cualquier subconjunto que sume monto_bco
        crm_dia = crm[
            (crm["fecha"] == fecha_bco)
            & (crm["estado"] == "pendiente")
            & (crm["monto"] < 0)  # ambos son débitos
        ]
        if crm_dia.empty:
            continue

        combo = _combinar_provs_para_llegar_a(crm_dia, monto_bco, max_combos=4)
        if combo is None:
            continue

        mid = f"M{next_id:04d}"
        for i_crm in combo:
            crm.at[i_crm, "match_id"] = mid
            crm.at[i_crm, "estado"] = "conciliado"
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "conciliado"

        matches.append({
            "match_id": mid,
            "tipo": f"PRÉSTAMO ({len(combo)} asientos CRM ↔ 1 DEBITOS)",
            "confianza": "Alta",
            "razon_ia": (
                f"Suma de {len(combo)} asientos del CRM = {monto_bco:,.2f}, "
                f"coincide con DEBITOS COBRO DE PRESTAMO del banco."
            ),
            "i_crm": combo[0],
            "i_crm_lista": combo,
            "i_bco": i_bco,
            "i_bco_lista": [i_bco],
            "diferencia_monto": 0.0,
            "diferencia_dias": 0,
            "es_agrupado": True,
            "tipo_agrupado": "N_a_1",
        })
        next_id += 1
        matcheados_count += 1

    if matcheados_count > 0:
        print(f"  → PRÉSTAMO: {matcheados_count} cuotas matcheadas")



# =====================================================================
# MATCHING: CUPONES VISA/MASTER ↔ LIQ-TC
# =====================================================================
def _pasada_cupones_visa_master(crm, banco, matches):
    """
    Matchea los cupones VISA/MASTER del banco (uno por comercio/día,
    "... ACREDITACION A COMERCIO ...") contra los asientos Liq-TC del CRM.

    A diferencia de BBVA, en Santander no hay un "ID de lote" por
    liquidación en la descripción del banco: la tarjeta se liquida en
    bloque a fin de mes, y el CRM registra varios asientos Liq-TC (uno
    por lote) el último día. Por eso se matchea por SUMA TOTAL del
    período: si la suma de todos los cupones pendientes del banco
    coincide (al peso) con la suma de todos los Liq-TC pendientes del
    CRM, se marcan todos como conciliados en un solo grupo.

    Los movimientos "DEBITO COMERCIO" (reversas/contracargos) se excluyen
    porque no forman parte de la liquidación regular del mes.
    """
    mask_bco = (
        (banco["estado"] == "pendiente")
        & banco["descripcion_orig"].str.contains(r"VISA|MASTER", regex=True, na=False, case=False)
        & ~banco["descripcion_orig"].str.contains(r"DEBITO", regex=True, na=False, case=False)
    )
    cupones = banco[mask_bco]

    mask_crm = (crm["estado"] == "pendiente") & (crm["categoria_especial"] == "LIQ-TC")
    liqtc = crm[mask_crm]

    if cupones.empty or liqtc.empty:
        return

    suma_cupones = float(cupones["monto"].sum())
    suma_liqtc = float(liqtc["monto"].sum())

    if abs(suma_cupones - suma_liqtc) > 1.0:
        return

    mid = f"M{len(matches):04d}"
    for i_bco in cupones.index:
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "conciliado"
    for i_crm in liqtc.index:
        crm.at[i_crm, "match_id"] = mid
        crm.at[i_crm, "estado"] = "conciliado"

    matches.append({
        "match_id": mid,
        "tipo": f"Cupones VISA/MASTER ↔ Liq-TC ({len(cupones)} banco ↔ {len(liqtc)} CRM)",
        "confianza": "Alta",
        "razon_ia": (
            f"Suma de {len(cupones)} cupones VISA/MASTER del banco = "
            f"{suma_cupones:,.2f}, coincide con la suma de {len(liqtc)} "
            f"asientos Liq-TC del CRM."
        ),
        "i_crm": liqtc.index[0],
        "i_crm_lista": list(liqtc.index),
        "i_bco": cupones.index[0],
        "i_bco_lista": list(cupones.index),
        "diferencia_monto": round(abs(suma_cupones - suma_liqtc), 2),
        "diferencia_dias": 0,
        "es_agrupado": True,
        "tipo_agrupado": "cupones_visa_master",
    })
    print(f"  → Cupones VISA/MASTER: {len(cupones)} banco ↔ {len(liqtc)} Liq-TC CRM "
          f"matcheados (suma ${suma_cupones:,.2f})")


# =====================================================================
# MATCHING: AGRUPACIONES DÍA + SUMA
# =====================================================================
def _pasada_agrupaciones_dia(crm, banco, matches):
    """
    Matchea casos donde el banco tiene VARIOS movimientos del mismo día
    y el mismo tipo, que juntos suman UN asiento único del CRM.

    Patrones cubiertos (todos siguen la misma mecánica N banco → 1 CRM):

      - SUELDOS: el banco emite un pago por empleado ("SUELDOS ####### PAGO
        HABERES"), el CRM lo agrupa en un asiento único "Op X ... Otros Pagos".

      - TR SNP MIN: pagos "TR SNP MIN ####### PAGO CCI" desagregados en el
        banco, agrupados en un asiento del CRM.

      - TRANSFERENCIAS ENTRE CUENTAS PROPIAS: varias "TRF ... MISMO TITULAR"
        del día en el banco, contra asientos del CRM con contraparte
        "Banco Santander cta cte" (que a veces vienen sin concepto).

    Para cada grupo del banco (por día + patrón) se busca en el CRM del mismo
    día (o ±5 días) un movimiento del mismo signo cuyo monto coincida con
    la suma del grupo. Si la suma del día completo no coincide con ningún
    asiento, prueba subconjuntos: puede pasar que solo parte de los
    movimientos del día pertenezcan a un asiento y el resto a otro (ej.
    2 de 4 pagos "TR SNP MIN" del mismo día).
    """
    import itertools
    import pandas as pd

    def _subconjunto_que_suma(indices, montos_por_indice, objetivo,
                              tolerancia=0.01, max_grupo=8):
        """
        Busca un subconjunto de `indices` (de 2 elementos en adelante) cuyos
        montos sumen `objetivo`. Devuelve la lista de índices o None.
        Se acota a grupos chicos para que la búsqueda sea rápida.
        """
        pool = indices[:max_grupo] if len(indices) > max_grupo else indices
        for r in range(2, len(pool)):
            for combo in itertools.combinations(pool, r):
                if abs(sum(montos_por_indice[i] for i in combo) - objetivo) < tolerancia:
                    return list(combo)
        return None

    # Definición de los patrones: (nombre, regex del banco, signo esperado)
    patrones = [
        ("SUELDOS", r"SUELDOS|PAGO HABERES|PAGO DE HABERES", "neg"),
        ("TR SNP MIN", r"TR SNP MIN", "neg"),
        ("Transf. mismo titular", r"MISMO TITULAR|CVU MISMO", "pos"),
        ("Rescate FCI", r"RESCATE FONDOS|RES\.SF", "pos"),
    ]

    next_id = len(matches)
    total_matcheado = 0

    for nombre_patron, regex, signo in patrones:
        # Movimientos del banco pendientes que matchean el patrón
        mask_bco = (
            (banco["estado"] == "pendiente")
            & banco["descripcion_orig"].str.contains(regex, regex=True, na=False, case=False)
        )
        if signo == "neg":
            mask_bco = mask_bco & (banco["monto"] < 0)
        else:
            mask_bco = mask_bco & (banco["monto"] > 0)

        grupo_banco = banco[mask_bco]
        if grupo_banco.empty:
            continue

        # Agrupar por fecha
        for fecha, movs_dia in grupo_banco.groupby("fecha"):
            indices_bco = [i for i in movs_dia.index if banco.at[i, "estado"] == "pendiente"]
            if not indices_bco:
                continue
            suma_banco = float(banco.loc[indices_bco, "monto"].sum())
            if abs(suma_banco) < 0.01:
                continue

            # Buscar en el CRM un asiento del mismo signo cuyo monto coincida
            # con la suma del grupo completo (mismo día o ±5 días).
            match_crm = _buscar_asiento_crm_para_suma(
                crm, suma_banco, fecha, dias_tol=5
            )

            # Si la suma del día completo no matchea, puede ser que solo
            # PARTE del grupo corresponda a un asiento (y el resto a otro).
            # Probamos subconjuntos contra los asientos del CRM cercanos.
            if match_crm is None and len(indices_bco) > 2:
                montos_por_indice = {i: banco.at[i, "monto"] for i in indices_bco}
                crm_cand = crm[
                    (crm["estado"] == "pendiente")
                    & ((crm["monto"] > 0) == (signo == "pos"))
                ]
                crm_cand = crm_cand[crm_cand["fecha"].apply(
                    lambda f: abs((pd.Timestamp(f) - pd.Timestamp(fecha)).days) <= 5
                )]
                for i_crm_cand, r_crm in crm_cand.iterrows():
                    subconjunto = _subconjunto_que_suma(
                        indices_bco, montos_por_indice, r_crm["monto"]
                    )
                    if subconjunto:
                        match_crm = i_crm_cand
                        indices_bco = subconjunto
                        break

            if match_crm is not None:
                i_crm = match_crm
                mid = f"M{next_id:04d}"
                crm.at[i_crm, "match_id"] = mid
                crm.at[i_crm, "estado"] = "conciliado"
                for i_bco in indices_bco:
                    banco.at[i_bco, "match_id"] = mid
                    banco.at[i_bco, "estado"] = "conciliado"

                matches.append({
                    "match_id": mid,
                    "tipo": f"{nombre_patron} ({len(indices_bco)} banco ↔ 1 CRM)",
                    "confianza": "Alta",
                    "razon_ia": (
                        f"Suma de {len(indices_bco)} movimientos '{nombre_patron}' "
                        f"del banco = {suma_banco:,.2f}, coincide con asiento del CRM."
                    ),
                    "i_crm": i_crm,
                    "i_crm_lista": [i_crm],
                    "i_bco": indices_bco[0],
                    "i_bco_lista": indices_bco,
                    "diferencia_monto": 0.0,
                    "diferencia_dias": 0,
                    "es_agrupado": True,
                    "tipo_agrupado": "N_banco_a_1_crm",
                })
                next_id += 1
                total_matcheado += 1

    if total_matcheado > 0:
        print(f"  → Agrupaciones día+suma: {total_matcheado} grupos matcheados")


def _buscar_asiento_crm_para_suma(crm, suma_objetivo, fecha, dias_tol=5):
    """
    Busca en el CRM un asiento pendiente cuyo monto coincida (al céntimo)
    con `suma_objetivo`, dentro de una ventana de ±dias_tol días respecto
    de `fecha`. Devuelve el índice o None.

    Prioriza el match del mismo día; si no hay, amplía la ventana.
    """
    import pandas as pd

    fecha_ts = pd.Timestamp(fecha)

    # Candidatos del mismo signo y monto coincidente
    mask = (
        (crm["estado"] == "pendiente")
        & ((crm["monto"] - suma_objetivo).abs() < 0.01)
    )
    candidatos = crm[mask]
    if candidatos.empty:
        return None

    # Ordenar por cercanía de fecha
    mejor_idx = None
    mejor_dist = None
    for idx, r in candidatos.iterrows():
        try:
            dist = abs((pd.Timestamp(r["fecha"]) - fecha_ts).days)
        except Exception:
            dist = 999
        if dist > dias_tol:
            continue
        if mejor_dist is None or dist < mejor_dist:
            mejor_dist = dist
            mejor_idx = idx

    return mejor_idx


# =====================================================================
# MATCHING: CHEQUES CLEARING / ASIENTOS DE MOVIMIENTOS BANCARIOS
# =====================================================================
def _pasada_cheques_clearing(crm, banco, matches):
    """
    Matchea movimientos de cheques (clearing / depósitos echeq) del banco
    contra los "Asiento de Movimientos Bancarios" del CRM.

    En el banco aparecen como:
        - "CLE.REC.48 ####### ECHEQ CLEARING RECIBIDO"
        - "CR.FIL.24 ####### DEPOSITO ECHEQ CANJE"
    En el CRM aparecen como "Asiento de Movimientos Bancarios" (a veces sin
    concepto) por el mismo monto.

    Se matchea 1:1 por monto exacto, con tolerancia de fecha de ±3 días
    (el clearing puede acreditarse uno o dos días después del depósito).
    """
    import pandas as pd

    mask_bco = (
        (banco["estado"] == "pendiente")
        & banco["descripcion_orig"].str.contains(
            r"CLE\.REC|DEPOSITO ECHEQ|ECHEQ CLEARING|CANJE", regex=True, na=False, case=False
        )
    )
    cheques = banco[mask_bco]
    if cheques.empty:
        return

    next_id = len(matches)
    matcheados = 0

    for i_bco, fila_bco in cheques.iterrows():
        if banco.at[i_bco, "estado"] != "pendiente":
            continue
        monto_bco = fila_bco["monto"]
        fecha_bco = pd.Timestamp(fila_bco["fecha"])

        # Buscar en el CRM un asiento pendiente del mismo monto exacto (±3 días)
        mask_crm = (
            (crm["estado"] == "pendiente")
            & ((crm["monto"] - monto_bco).abs() < 0.01)
        )
        candidatos = crm[mask_crm]
        if candidatos.empty:
            continue

        # Elegir el más cercano en fecha dentro de ±3 días
        mejor_idx = None
        mejor_dist = None
        for idx, r in candidatos.iterrows():
            try:
                dist = abs((pd.Timestamp(r["fecha"]) - fecha_bco).days)
            except Exception:
                dist = 999
            if dist > 5:
                continue
            if mejor_dist is None or dist < mejor_dist:
                mejor_dist = dist
                mejor_idx = idx

        if mejor_idx is None:
            continue

        mid = f"M{next_id:04d}"
        crm.at[mejor_idx, "match_id"] = mid
        crm.at[mejor_idx, "estado"] = "conciliado"
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "conciliado"

        matches.append({
            "match_id": mid,
            "tipo": "Cheque clearing ↔ Asiento Mov. Bancarios",
            "confianza": "Alta",
            "i_crm": mejor_idx,
            "i_bco": i_bco,
            "diferencia_monto": 0.0,
            "diferencia_dias": mejor_dist,
        })
        next_id += 1
        matcheados += 1

    if matcheados > 0:
        print(f"  → Cheques clearing: {matcheados} matcheados")


# =====================================================================
# AJUSTES SUGERIDOS: COMEX PENDIENTE DE CONTABILIZACIÓN
# =====================================================================
def _detectar_comex_pendiente(banco_df, crm_df):
    """
    Detecta operaciones COB.IMPORT del banco que quedaron huérfanas después
    del matching (no tienen su Op X correspondiente en el CRM).

    Esto ocurre cuando el banco ya ejecutó la transferencia de importación
    pero contabilidad todavía no cargó el asiento en el CRM (se cargará el
    mes siguiente al llegar la documentación).

    Como el banco YA descontó esa plata (movimiento negativo) pero el CRM no
    lo refleja, hay que sumar ese monto como ajuste para neutralizar la
    diferencia. El COB.IMPORT es negativo, entonces el ajuste va con el mismo
    signo (negativo) para que el "saldo banco calculado" baje y coincida con
    el extracto.
    """
    if banco_df is None:
        return None

    try:
        # COB.IMPORT que quedaron pendientes (sin matchear)
        mask = (
            (banco_df["estado"] == "pendiente")
            & banco_df["descripcion_orig"].str.upper().str.contains("COB.IMPORT", na=False)
        )
        comex_huerfanos = banco_df[mask]

        if comex_huerfanos.empty:
            return None

        total = float(comex_huerfanos["monto"].sum())
        cantidad = len(comex_huerfanos)

        if abs(total) < 1000:
            return None

        return {
            "concepto": "COMEX pendiente de contabilización",
            "monto": round(total, 2),
            "explicacion": (
                f"Hay {cantidad} operación(es) COB.IMPORT que el banco procesó "
                f"pero que aún no están cargadas en el CRM (se cargarán el mes "
                f"siguiente al llegar la documentación). **El monto es referencial "
                f"— verificá cada operación antes de aplicar.**"
            ),
            "cantidad_mov": cantidad,
        }
    except Exception:
        return None
