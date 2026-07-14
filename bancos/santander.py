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
        Corre las 3 pasadas específicas de Santander:
            1. COMEX (COB.IMPORT ↔ Op X + Proveedores)
            2. Certificaciones (CO.CERT.VA ↔ - Proveedores por monto exacto)
            3. Préstamo (DEBITOS COBRO DE PRESTAMO ↔ 2 asientos CRM)
        """
        _pasada_comex(crm, banco, matches)
        _pasada_certificaciones(crm, banco, matches)
        _pasada_prestamo(crm, banco, matches)

    # -----------------------------------------------------------------
    # AJUSTES SUGERIDOS
    # -----------------------------------------------------------------
    def calcular_ajustes_sugeridos(self, discrepancias, banco_df=None, crm_df=None):
        """
        Por ahora Santander no tiene ajustes automáticos sugeridos.

        A diferencia de BBVA, en Santander las conciliaciones oficiales de
        Caro (febrero, marzo y abril 2026 validadas) muestran que casi no
        hay ajustes automáticos:
            - No hay "Payway" (Santander no maneja esa integración)
            - No hay "Liq-TC pendiente" (los cupones se acreditan directo)
            - No hay "Dif gs bancarios" como concepto separado (los gastos
              ya vienen agrupados en el CRM en un único asiento fin de mes)

        Cuando aparezcan casos que requieran ajustes automáticos, se
        agregan acá.
        """
        return []

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

