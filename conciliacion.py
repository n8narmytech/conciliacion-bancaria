"""
Conciliación Bancaria CRM (GBP) vs Extracto Bancario
=====================================================

Compara dos archivos Excel (CRM y banco) y genera un reporte
identificando discrepancias para revisión humana.

Uso:
    python conciliacion.py

Requisitos:
    pip install pandas openpyxl rapidfuzz requests unidecode
    Ollama corriendo en localhost:11434
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from rapidfuzz import fuzz
from unidecode import unidecode


# ============================================================
# CONFIGURACIÓN — Editar acá según necesidad
# ============================================================

@dataclass
class Config:
    # Rutas
    archivo_crm: str = "reporte_crm_gbp_abril2026.xlsx"
    archivo_banco: str = "extracto_banco_galicia_abril2026.xlsx"
    archivo_salida: str = "conciliacion_abril2026.xlsx"

    # Tolerancias para matching
    dias_tolerancia: int = 2           # Sin referencia, ser más estricto con fechas
    monto_tolerancia_abs: float = 1.0
    monto_tolerancia_pct: float = 0.1  # Sin referencia, ser más estricto con montos

    # Umbral de similitud de descripción (0-100) para fuzzy matching
    umbral_similitud_desc: int = 75

    # Ollama
    usar_ia: bool = True
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_modelo: str = "qwen3.5:4b"
    ollama_timeout: int = 180

    # Mapeo de columnas CRM → campos internos
    cols_crm: dict = field(default_factory=lambda: {
        "fecha": "Fecha",
        "debe": "Debe",
        "haber": "Haber",
        "descripcion": "Concepto",
        "contraparte": "Leyenda",
        "referencia": "",        # Sin campo de referencia confiable
        "comprobante": "Asiento",
    })

    # Mapeo de columnas Banco → campos internos
    cols_banco: dict = field(default_factory=lambda: {
        "fecha": "Fecha",
        "debito": "Débito",
        "credito": "Crédito",
        "descripcion": "Concepto",
        "referencia": "",        # Sin campo de referencia confiable
    })

    # Fila de encabezados en cada archivo (0-indexed: fila 4 → header=3, fila 7 → header=6)
    crm_header_row: int = 3
    banco_header_row: int = 6


CFG = Config()


# ============================================================
# UTILIDADES DE NORMALIZACIÓN
# ============================================================

def normalizar_texto(texto):
    """Pone en mayúsculas, saca acentos y caracteres raros para comparar."""
    if pd.isna(texto):
        return ""
    txt = str(texto).strip().upper()
    txt = unidecode(txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt


def parse_monto(valor):
    """Convierte un valor a float. Vacío/NaN → 0."""
    if pd.isna(valor) or valor == "" or valor is None:
        return 0.0
    try:
        return float(valor)
    except (ValueError, TypeError):
        return 0.0


# ============================================================
# CARGA Y NORMALIZACIÓN
# ============================================================

def _leer_excel(archivo, header=0):
    """
    Lee un archivo Excel detectando si es .xlsx o .xls.
    Acepta tanto rutas (str) como objetos tipo file (BytesIO de Streamlit, etc).
    El parámetro header indica en qué fila están los encabezados (0-indexed).
    """
    nombre = ""
    if hasattr(archivo, "name"):
        nombre = archivo.name.lower()
    elif isinstance(archivo, str):
        nombre = archivo.lower()

    if nombre.endswith(".xls"):
        try:
            return pd.read_excel(archivo, engine="xlrd", header=header)
        except ImportError:
            raise RuntimeError(
                "Para leer archivos .xls necesitás instalar xlrd: "
                "pip3 install xlrd"
            )
    else:
        return pd.read_excel(archivo, engine="openpyxl", header=header)


def _leer_excel_banco(archivo, header=0):
    """
    Lee el Excel del banco eligiendo la hoja correcta.

    Si el archivo tiene una hoja "Movimientos Históricos" (BBVA clásico), la
    prefiere sobre "Movimientos del Día" (que solo trae los movimientos del día
    actual y nunca es lo que queremos para conciliar un mes completo).
    """
    # Obtener la lista de hojas sin leer todo el archivo
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

        # Prioridad: "Movimientos Históricos" sobre "Movimientos del Día"
        hoja_objetivo = None
        for h in hojas:
            if "histórico" in h.lower() or "historico" in h.lower():
                hoja_objetivo = h
                break

        # Si encontramos la histórica, usarla; si no, la primera
        if hoja_objetivo:
            if nombre.endswith(".xls"):
                return pd.read_excel(archivo, sheet_name=hoja_objetivo,
                                     engine="xlrd", header=header)
            else:
                return pd.read_excel(archivo, sheet_name=hoja_objetivo,
                                     engine="openpyxl", header=header)
    except Exception:
        pass

    # Fallback: leer normalmente (primera hoja)
    return _leer_excel(archivo, header=header)


def cargar_crm(path):
    """Carga el Excel del CRM y lo normaliza a formato interno."""
    df = _leer_excel(path, header=CFG.crm_header_row)
    df = df.dropna(how="all").reset_index(drop=True)
    cols = CFG.cols_crm

    # === Detectar saldo CRM final del archivo (Acumulado Mensual) ===
    # GBP exporta una columna "Acumulado Mensual" que contiene el saldo después
    # de cada movimiento. El valor del último movimiento es el saldo final del
    # mes según GBP, considerando el arrastre del mes anterior.
    # Este es el número que la contadora usa en su planilla.
    saldo_crm_acumulado = None
    if "Acumulado Mensual" in df.columns:
        # Buscar el último valor numérico en esa columna
        col_acum = pd.to_numeric(df["Acumulado Mensual"], errors="coerce")
        valores_validos = col_acum.dropna()
        if len(valores_validos) > 0:
            # El último valor de la columna es el saldo final del período
            # (no contamos las filas de cierre porque están fuera de los movimientos numerados)
            # Tomamos el ultimo valor de la columna acumulado mensual antes de las filas de totales
            col_asiento_chk = cols.get("comprobante", "Asiento")
            if col_asiento_chk in df.columns:
                mask_validos = pd.to_numeric(df[col_asiento_chk], errors="coerce").notna()
                acumulados_mov = col_acum[mask_validos]
                if len(acumulados_mov) > 0:
                    saldo_crm_acumulado = float(acumulados_mov.iloc[-1])

    # FILTRO IMPORTANTE: solo procesar filas con número de asiento válido (numérico).
    # Esto excluye las filas de totales y cierre del reporte como
    # "Total correspondiente al mes...", "Totales de Cuenta", "Impreso el...", "Mayor de la cuenta".
    col_asiento = cols.get("comprobante", "Asiento")
    if col_asiento in df.columns:
        asiento_num = pd.to_numeric(df[col_asiento], errors="coerce")
        df = df[asiento_num.notna()].reset_index(drop=True)

    out = pd.DataFrame()
    out["fecha"] = pd.to_datetime(df[cols["fecha"]], dayfirst=True, errors="coerce").dt.date
    out["debe"] = df[cols["debe"]].apply(parse_monto)
    out["haber"] = df[cols["haber"]].apply(parse_monto)
    # En el Libro Mayor de la cuenta bancaria:
    #   Debe  = entrada de dinero a la cuenta (cobranza) → equivale a Crédito en el extracto bancario
    #   Haber = salida de dinero de la cuenta (pago)     → equivale a Débito en el extracto bancario
    # Por eso: monto positivo = ingreso = Debe del CRM
    out["monto"] = out["debe"] - out["haber"]
    out["descripcion_orig"] = df[cols["descripcion"]].fillna("").astype(str)
    out["contraparte_orig"] = df[cols["contraparte"]].fillna("").astype(str)
    # Referencia: usar columna si está configurada, sino string vacío
    if cols.get("referencia") and cols["referencia"] in df.columns:
        out["referencia"] = df[cols["referencia"]].fillna("").astype(str).str.strip()
    else:
        out["referencia"] = ""
    if cols.get("comprobante") and cols["comprobante"] in df.columns:
        out["comprobante"] = df[cols["comprobante"]].fillna("").astype(str)
    else:
        out["comprobante"] = ""
    out["descripcion_norm"] = (
        out["descripcion_orig"] + " " + out["contraparte_orig"]
    ).apply(normalizar_texto)
    out["referencia_norm"] = out["referencia"].apply(normalizar_texto)

    # Eliminar filas sin fecha válida (suelen ser totales o filas de cierre del reporte)
    out = out[out["fecha"].notna()].reset_index(drop=True)

    # === Detectar categoría especial de cada movimiento ===
    # Algunos movimientos del CRM no tienen contrapartida directa en el banco:
    # - LIQ-TC: liquidaciones de tarjeta que el contador asienta agrupadas a fin de mes
    # - Asientos puente: contrapartidas dobles que se usan solo para balancear el asiento contable
    def detectar_categoria(row):
        desc_upper = (row["descripcion_orig"] + " " + row["contraparte_orig"]).upper()
        if "LIQ-TC" in desc_upper or "LIQ TC" in desc_upper or "LIQUIDACION" in desc_upper:
            return "LIQ-TC"
        return "normal"

    out["categoria_especial"] = out.apply(detectar_categoria, axis=1)

    # === Eliminar contrapartidas dobles ===
    # Cuando el reporte muestra partida doble, aparecen dos líneas con el mismo asiento,
    # misma contraparte y montos espejados (una en Debe, otra en Haber del mismo importe).
    # === NOTA SOBRE PARTIDAS DOBLES ===
    # La contadora usa el saldo TAL CUAL aparece en GBP, incluyendo asientos
    # contables que parecen "partidas dobles" (pares que se cancelan).
    # Por eso NO los eliminamos: aunque visualmente parezcan ruido, son parte
    # del saldo contable que la contadora compara contra el banco.
    #
    # Si en el futuro se quiere reactivar la eliminación, descomentar el bloque
    # de detección de pares espejo.

    # === Eliminar contrapartidas dobles del MATCHING (no del saldo) ===
    # IMPORTANTE: ya estamos usando el "Acumulado Mensual" del propio archivo como
    # saldo CRM (que es el número que usa la contadora). Por eso, eliminar partidas
    # dobles del DataFrame de movimientos NO altera el saldo final usado en el cálculo.
    #
    # Las partidas dobles del CRM son operaciones contables registradas dos veces
    # (mismo asiento + misma contraparte + montos opuestos) que aparecen como
    # cobros pendientes ficticios en el reporte. Las eliminamos para que el matching
    # con el banco sea limpio y las sugerencias no estén infladas.
    if "comprobante" in out.columns and out["comprobante"].any():
        a_eliminar = set()
        # Agrupar por (asiento, contraparte) y buscar pares que se cancelen mutuamente
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
                        # Eliminamos ambas (no aportan al matching ni al saldo)
                        a_eliminar.add(i)
                        a_eliminar.add(j)
                        usados.add(i)
                        usados.add(j)
                        break
        if a_eliminar:
            out = out.drop(list(a_eliminar)).reset_index(drop=True)

    out["origen"] = "CRM"
    out["fila_origen"] = out.index + 2
    out["match_id"] = None
    out["estado"] = "pendiente"

    # Guardar el saldo final detectado del propio archivo (Acumulado Mensual)
    # como atributo. Si está disponible, es la "verdad" del saldo CRM según GBP
    # (incluye el arrastre del mes anterior automáticamente).
    out.attrs["saldo_crm_detectado"] = saldo_crm_acumulado

    return out


def _detectar_formato_banco(archivo):
    """
    Detecta el formato del extracto bancario leyendo las primeras filas.
    Devuelve un dict con:
        - tipo: "extracto_cuenta" | "bbva_clasico"
        - header_row: fila donde está el encabezado (0-indexed, coincide con la del archivo)
        - saldo_inicial: float o None (si lo detecta en el archivo)
    """
    # IMPORTANTE: leer sin dropna para que el índice del header coincida con la fila real
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

        # Detectar header del formato nuevo (tiene "Concepto/Cod.Op." e "Importe")
        if "CONCEPTO/COD" in fila_upper and "IMPORTE" in fila_upper:
            tipo = "extracto_cuenta"
            header_row = i
            break

        # Detectar header del formato BBVA clásico (tiene "Crédito" y "Débito" separados)
        if "CRÉDITO" in fila_upper and "DÉBITO" in fila_upper:
            tipo = "bbva_clasico"
            header_row = i
            break

    if tipo is None:
        # Fallback: usar la configuración por defecto
        tipo = "bbva_clasico"
        header_row = CFG.banco_header_row

    return {
        "tipo": tipo,
        "header_row": header_row,
        "saldo_inicial": saldo_inicial,
    }


def cargar_banco(path):
    """
    Carga el Excel del banco. Detecta automáticamente si es:
    - Formato "Extracto de Cuenta" (nuevo, columna única Importe con signo)
    - Formato BBVA clásico (Crédito y Débito en columnas separadas)
    """
    # Detectar formato
    fmt = _detectar_formato_banco(path)
    df = _leer_excel_banco(path, header=fmt["header_row"])
    df = df.dropna(how="all").reset_index(drop=True)

    out = pd.DataFrame()

    if fmt["tipo"] == "extracto_cuenta":
        # === FORMATO NUEVO ===
        # Columnas: Concepto/Cod.Op. | Fecha | Comprobante | Sucursal | Importe |
        #           Descripción | Cod.Op.Bco. | CUIT | Denominación | Saldo
        out["fecha"] = pd.to_datetime(df["Fecha"], dayfirst=True, errors="coerce").dt.date
        # Importe ya viene con signo (positivo créditos, negativo débitos)
        out["monto"] = df["Importe"].apply(parse_monto)
        # Construir descripción: Concepto/Cod.Op. + Comprobante + Descripción
        # Incluimos el Comprobante porque para cupones MASTER/VISA es el ID del lote
        # que se usa para agrupar contra los LIQ-TC del CRM.
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
        cols = CFG.cols_banco
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

    # Eliminar filas sin fecha o sin monto válido
    out = out[out["fecha"].notna() & (out["monto"].abs() > 0)].reset_index(drop=True)

    out["origen"] = "BANCO"
    out["fila_origen"] = out.index + 2
    out["match_id"] = None
    out["estado"] = "pendiente"

    # Guardar metadata detectada (saldo inicial y formato) como atributo del DataFrame
    out.attrs["formato"] = fmt["tipo"]
    out.attrs["saldo_inicial_detectado"] = fmt["saldo_inicial"]
    out.attrs["path_archivo"] = path  # Para acceso a hojas auxiliares como 'gs bancarios'

    # Intentar detectar saldo FINAL del archivo:
    # - En formato "extracto_cuenta", suele estar en la última fila con "Saldo Final:" o
    #   en la columna Saldo de la última fila con datos.
    out.attrs["saldo_final_detectado"] = _detectar_saldo_final(path, fmt)

    return out


def _detectar_saldo_final(archivo, fmt):
    """
    Intenta extraer el saldo final del PERÍODO del archivo bancario.

    En el formato BBVA clásico, el saldo final del período es el "Saldo Disponible"
    del último movimiento del mes (no el "Saldo:" del encabezado, que muestra el
    saldo actual al generar el reporte, posterior al cierre del mes).

    Estrategias en orden:
    1. Buscar 'Saldo Final:' explícito en las últimas filas.
    2. En formato 'extracto_cuenta': último valor de la columna 'Saldo'.
    3. En formato BBVA clásico: 'Saldo Disponible' del último movimiento del período.
    4. Fallback: 'Saldo:' del encabezado (menos preciso, suele ser posterior al período).
    """
    try:
        df_full = _leer_excel_banco(archivo, header=None)

        # Estrategia 1: "Saldo Final:" explícito en las últimas filas
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

        # Estrategia 2: formato "extracto_cuenta" → columna 'Saldo' del último mov
        if fmt["tipo"] == "extracto_cuenta":
            df = _leer_excel_banco(archivo, header=fmt["header_row"])
            if "Saldo" in df.columns:
                saldos_no_vacios = df["Saldo"].dropna()
                if len(saldos_no_vacios) > 0:
                    return float(saldos_no_vacios.iloc[-1])

        # Estrategia 3: BBVA clásico → "Saldo Disponible" del último movimiento del mes.
        # En BBVA los movimientos vienen en ORDEN INVERSO (más reciente primero),
        # entonces buscamos el "Saldo Disponible" en las primeras filas de datos.
        # Cada fila tiene la forma: ... Crédito, Débito, Detalle="Saldo Disponible: X,YY", ...
        if fmt["tipo"] == "bbva_clasico":
            # Buscar en filas justo después del header
            for i in range(fmt["header_row"] + 1, min(fmt["header_row"] + 5, len(df_full))):
                fila = df_full.iloc[i]
                for v in fila.values:
                    if pd.isna(v):
                        continue
                    s = str(v).strip()
                    if "SALDO DISPONIBLE" in s.upper():
                        # Extraer el número después de "Saldo Disponible:"
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

        # Estrategia 4 (fallback): "Saldo:" del encabezado del BBVA clásico
        # Este es el saldo actual al momento de generar el reporte, NO el del período.
        # Lo usamos solo si no encontramos las otras estrategias.
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


# ============================================================
# MATCHING
# ============================================================

def montos_iguales(m1, m2):
    """Compara dos montos con tolerancia configurada."""
    diff = abs(m1 - m2)
    if diff <= CFG.monto_tolerancia_abs:
        return True
    if max(abs(m1), abs(m2)) > 0:
        pct = diff / max(abs(m1), abs(m2)) * 100
        if pct <= CFG.monto_tolerancia_pct:
            return True
    return False


def buscar_combinaciones_que_suman(discrepancias, objetivo, tolerancia=500, max_size=5, max_resultados=3):
    """
    Busca combinaciones de hasta 'max_size' movimientos cuya suma se acerca al 'objetivo'.

    Esto es esencialmente "subset sum" — es problema NP, pero como típicamente tenemos
    < 100 discrepancias y limitamos el tamaño a 5, es manejable en milisegundos.

    Estrategia:
    1. Filtrar movimientos con monto significativo (>1% del objetivo).
    2. Probar combinaciones de tamaño 2, 3, 4, 5 (no 1 porque eso ya lo hacemos antes).
    3. Devolver hasta max_resultados combinaciones que cuadren.
    """
    from itertools import combinations

    if abs(objetivo) < 100:
        return []

    # Filtrar movimientos relevantes (no demasiado chicos comparados con el objetivo)
    umbral_relevancia = abs(objetivo) * 0.005  # 0.5% del objetivo
    candidatos = [d for d in discrepancias if abs(d["monto"]) > umbral_relevancia]

    # Si hay demasiados, quedarnos con los más grandes (mejora la búsqueda)
    if len(candidatos) > 40:
        candidatos = sorted(candidatos, key=lambda d: abs(d["monto"]), reverse=True)[:40]

    resultados = []
    # Probar de 2 a max_size movimientos
    for size in range(2, max_size + 1):
        if len(resultados) >= max_resultados:
            break
        for combo in combinations(candidatos, size):
            suma = sum(d["monto"] for d in combo)
            if abs(suma - objetivo) <= tolerancia:
                resultados.append({
                    "movimientos": list(combo),
                    "suma": round(suma, 2),
                    "diferencia": round(suma - objetivo, 2),
                })
                if len(resultados) >= max_resultados:
                    break
    return resultados


def obtener_huerfanos_banco_para_carrito(discrepancias):
    """
    Devuelve la lista de movimientos del banco que no tienen contrapartida en el CRM,
    formateados para mostrar en el "carrito de ajustes" de la app.

    Cada movimiento incluye un ID único, fecha, monto, descripción y categoría sugerida.
    Ordenados por valor absoluto descendente para que los más importantes aparezcan primero.
    """
    huerfanos = []
    for idx, d in enumerate(discrepancias):
        if d["origen"] != "BANCO":
            continue

        desc = d["descripcion"].upper()
        # Sugerir categoría según patrones
        categoria = "Otros"
        if "PAYWAY" in desc or "DEBIN" in desc:
            categoria = "Payway"
        elif " MASTER" in desc or " VISA" in desc or "CUPON" in desc or "CUPONES" in d.get("tipo", ""):
            categoria = "Liq Master/Visa"
        elif "SIRCREB" in desc or "SIRTAC" in desc or "PERCEPCION" in desc or "IIBB" in desc:
            categoria = "IIBB/SIRCREB"
        elif "LEY 25" in desc or "IMPUESTO LEY" in desc or "IMP.LEY" in desc:
            categoria = "Impuesto al cheque"
        elif "IVA " in desc:
            categoria = "IVA bancario"
        elif "COMISION" in desc or "MANTENIMIENTO" in desc:
            categoria = "Comisiones"
        elif "INTERES" in desc:
            categoria = "Intereses"
        elif "HABERES" in desc or "SUELDO" in desc:
            categoria = "Sueldos"

        huerfanos.append({
            "id": f"H{idx:04d}",
            "fecha": d["fecha"],
            "monto": d["monto"],
            "descripcion": d["descripcion"][:80],
            "categoria_sugerida": categoria,
        })

    # Ordenar por monto absoluto descendente
    huerfanos.sort(key=lambda h: abs(h["monto"]), reverse=True)
    return huerfanos


def obtener_posibles_debitos_pendientes(crm_df, banco_df):
    """
    Identifica recibos del CRM (cobros) cuyo monto exacto NO aparece en el extracto
    del banco en NINGÚN día del período. Esos son candidatos a "Débitos pendientes
    de contabilización" porque probablemente se acrediten en el banco al mes siguiente.

    A diferencia de la sugerencia automática de "Débitos pendientes" (que solo busca
    en los últimos días del mes), esta función mira TODO el mes. Pero como puede haber
    falsos positivos (matches perdidos por fechas, errores de redondeo, etc.), devuelve
    una lista para que el usuario revise y elija cuáles agregar.

    Excluye los recibos del último día del mes (28-31) porque esos ya están en la
    sugerencia automática de "Débitos pendientes de contabilización".

    Devuelve lista de dicts:
        [{"id": "...", "fecha": ..., "monto": ..., "descripcion": ..., "contraparte": ...}]
    """
    if crm_df is None or banco_df is None:
        return []

    candidatos = []
    try:
        import pandas as pd_mod

        # Recibos del CRM huérfanos del matching (positivos, no internos)
        huerfanos = crm_df[crm_df["estado"] == "pendiente"].copy()
        huerfanos = huerfanos[huerfanos["monto"] > 0]

        # Filtrar solo recibos (Rc X / RcM X / Recibo)
        mask_rc = huerfanos["descripcion_orig"].astype(str).str.upper().str.contains(
            r"\bRC X\b|\bRCM X\b|\bRECIBO\b", na=False, regex=True
        )
        huerfanos = huerfanos[mask_rc]

        # Excluir movimientos internos entre cuentas propias
        contraparte_up = huerfanos["contraparte_orig"].astype(str).str.upper()
        mask_externos = ~contraparte_up.str.contains(
            "BANCO BBVA|BANCO FRANCES|BANCO PATAGONIA", na=False
        )
        huerfanos = huerfanos[mask_externos]

        # Excluir los del último día del mes (ya están en sugerencias automáticas)
        huerfanos["dia"] = pd_mod.to_datetime(
            huerfanos["fecha"], errors="coerce"
        ).dt.day
        huerfanos = huerfanos[huerfanos["dia"] < 28]

        # Para cada huérfano, verificar si su monto exacto NO aparece en el banco
        for idx, r in huerfanos.iterrows():
            monto = float(r["monto"])
            mask_banco = (
                (banco_df["monto"] >= monto - 0.01)
                & (banco_df["monto"] <= monto + 0.01)
            )
            if mask_banco.sum() == 0:
                candidatos.append({
                    "id": f"crm_{idx}",
                    "fecha": r["fecha"],
                    "monto": monto,
                    "descripcion": str(r["descripcion_orig"])[:80],
                    "contraparte": str(r["contraparte_orig"])[:50],
                })
    except Exception:
        pass

    candidatos.sort(key=lambda c: c["fecha"] if c["fecha"] else "")
    return candidatos


def calcular_ajustes_sugeridos(discrepancias, banco_df=None, crm_df=None):
    """
    Identifica ajustes que el sistema puede detectar con confianza analizando los archivos.

    Por ahora hay UNA sola sugerencia confiable:

    1. Liq Master/Visa pendiente: cupones MASTER/VISA del banco huérfanos del matching
       (no tienen asiento LIQ-TC en el CRM). Probablemente se asentarán en el CRM
       el mes siguiente. Monto positivo porque la plata SÍ entró al banco.

    Los demás ajustes (Payway, Débitos pendientes, Dif gs bancarios) requieren
    contexto profesional del contador y NO se sugieren automáticamente.
    """
    sugeridos = []

    # === Liq Master/Visa pendiente ===
    # Solo cupones tipo CUPONES PRIS (créditos por cupones de tarjeta).
    # NO incluimos PAGO VISA-IN porque son débitos del banco a Visa (no cupones).
    liq_pendiente_monto = 0
    liq_pendiente_cantidad = 0
    for d in discrepancias:
        if d["origen"] != "BANCO":
            continue
        desc = d["descripcion"].upper()
        tipo = d.get("tipo", "")
        # Excluir explícitamente PAGO VISA-IN
        if "PAGO VISA" in desc or "PAGO MASTER" in desc:
            continue
        # Aceptar CUPONES, MASTER (al inicio), VISA (al inicio)
        es_cupon = (
            "CUPONES" in tipo or "CUPON" in desc
            or " MASTER" in desc or " VISA" in desc
            or desc.startswith("MASTER") or desc.startswith("VISA")
        )
        if es_cupon:
            liq_pendiente_monto += d["monto"]
            liq_pendiente_cantidad += 1

    if abs(liq_pendiente_monto) >= 100:
        sugeridos.append({
            "concepto": "Liq Master pendiente",
            "monto": round(liq_pendiente_monto, 2),
            "explicacion": (
                f"Hay {liq_pendiente_cantidad} cupón(es) de tarjeta del banco "
                f"sin asiento LIQ-TC asociado en el CRM. Probablemente se asienten "
                f"en el CRM el mes siguiente. **El monto es referencial — verificalo "
                f"con tu criterio antes de aplicarlo.**"
            ),
            "cantidad_mov": liq_pendiente_cantidad,
        })

    # === Débitos pendientes de contabilización ===
    # Recibos/cobros del CRM con fecha del último día del mes (28-31) que NO
    # tienen contrapartida en el banco. Se acreditarán en el banco el mes siguiente.
    # Se cargan NEGATIVO en la conciliación porque hay que "neutralizar" ese cobro
    # del CRM en este mes (la plata aún no entró al banco).
    deb_pendientes_monto = 0
    deb_pendientes_cantidad = 0
    for d in discrepancias:
        if d["origen"] != "CRM" or d["tipo"] != "FALTANTE EN BANCO":
            continue
        if d["monto"] <= 0:  # Solo cobros (positivos)
            continue
        fecha = d.get("fecha")
        if not fecha or not hasattr(fecha, "day"):
            continue
        # Solo movimientos del último día del mes (28-31)
        if fecha.day < 28:
            continue
        # Excluir movimientos internos del CRM (entre cuentas propias)
        contraparte_up = d.get("contraparte", "").upper()
        if ("BANCO BBVA" in contraparte_up or "BANCO FRANCES" in contraparte_up
            or "BANCO PATAGONIA" in contraparte_up):
            continue
        # Solo recibos (Rc o RcM)
        desc_up = d["descripcion"].upper()
        if not ("RC X" in desc_up or "RCM X" in desc_up or "RECIBO" in desc_up):
            continue
        deb_pendientes_monto += d["monto"]
        deb_pendientes_cantidad += 1

    if abs(deb_pendientes_monto) >= 1000:
        # El signo se invierte: si el CRM tiene un cobro pendiente positivo,
        # el ajuste va negativo (hay que neutralizarlo)
        sugeridos.append({
            "concepto": "Débitos pendientes de contabilización",
            "monto": round(-deb_pendientes_monto, 2),
            "explicacion": (
                f"Hay {deb_pendientes_cantidad} recibo(s) del CRM con fecha del "
                f"último día del mes que el banco aún no procesó. Probablemente "
                f"se acrediten en el extracto del mes siguiente. **El monto es "
                f"referencial — verificá cada operación antes de aplicar.**"
            ),
            "cantidad_mov": deb_pendientes_cantidad,
        })

    # === Dif gs bancarios ===
    # Estrategia en dos pasos:
    #
    # 1) PRIORIDAD: si el archivo del banco trae la hoja "gs bancarios" (el
    #    análisis que arma Melania), extraer el valor de la columna "Diferencia"
    #    de la fila "Total general". Es el dato más confiable.
    #
    # 2) FALLBACK: identificar los cargos del banco que son gastos bancarios
    #    según los patrones de Melania (COMISION, COM.MANT, IMPUESTO LEY, IVA,
    #    LEY NRO 25, PERC, PERCEPCION, REG REC SIRC, CONS.AP) y compararlos
    #    contra el asiento agrupado del CRM ("Banco BBVA cta cte $." +
    #    "- Proveedores" del último día del mes).
    dif_gs_calculado = None

    # Paso 1: leer hoja auxiliar si existe
    if banco_df is not None:
        path_banco_archivo = banco_df.attrs.get("path_archivo")
        if path_banco_archivo:
            try:
                import pandas as pd_mod
                xl = pd_mod.ExcelFile(path_banco_archivo)

                # Variante 1: hoja "gs bancarios" con columna "Diferencia"
                if "gs bancarios" in xl.sheet_names:
                    df_gs = pd_mod.read_excel(path_banco_archivo, sheet_name="gs bancarios")
                    if "Diferencia" in df_gs.columns and "cod abrev" in df_gs.columns:
                        mask_total = df_gs["cod abrev"].astype(str).str.contains(
                            "Total general", na=False
                        )
                        if mask_total.any():
                            dif_val = df_gs[mask_total].iloc[0]["Diferencia"]
                            if pd_mod.notna(dif_val):
                                dif_gs_calculado = ("hoja_melania", round(float(dif_val), 2))

                # Variante 2: "Hoja2" con etiqueta "dif mes pasado" (formato Melania mayo+)
                # En esta variante, hay una fila con nota "dif mes pasado" cuyo monto
                # es exactamente el ajuste "Dif gs bancarios" del mes.
                if dif_gs_calculado is None and "Hoja2" in xl.sheet_names:
                    df_h2 = pd_mod.read_excel(
                        path_banco_archivo, sheet_name="Hoja2", header=None
                    )
                    if df_h2.shape[1] >= 3:
                        # Buscar fila con "dif mes pasado" en la 3ra columna
                        mask_dif = df_h2.iloc[:, 2].astype(str).str.contains(
                            "dif mes pasado", na=False, case=False
                        )
                        if mask_dif.any():
                            monto_dif = df_h2[mask_dif].iloc[0, 1]
                            if pd_mod.notna(monto_dif):
                                dif_gs_calculado = (
                                    "hoja2_melania", round(float(monto_dif), 2)
                                )
            except Exception:
                pass

    # Paso 2: si no encontramos hoja Melania válida, calcular a partir de patrones
    if dif_gs_calculado is None and banco_df is not None and crm_df is not None:
        try:
            import pandas as pd_mod

            # Patrones para identificar gastos bancarios en el banco (criterio Melania)
            def _es_gasto_bancario(desc):
                desc = str(desc).upper().strip()
                patrones_inicio = [
                    "COMISION TRA", "COMISION ", "COM.MANT", "COM.TRANS", "COM.TRANSF",
                    "IMPUESTO LEY", "IMP.LEY", "IVA TASA", "LEY NRO 25", "LEY 25",
                    "PERC.CABA", "PERCEPCION", "REG REC SIRC",
                ]
                if "OG - DEBITO" in desc and "CONS.AP" in desc:
                    return True
                return any(desc.startswith(p) for p in patrones_inicio)

            mask_gs = banco_df["descripcion_orig"].apply(_es_gasto_bancario)
            total_banco_gs = float(banco_df[mask_gs]["monto"].sum())
            cantidad_gs = int(mask_gs.sum())

            # Asiento agrupado del CRM: leyenda "Banco BBVA cta cte $." + concepto
            # "- Proveedores" del último día del mes (28-31)
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
                asientos["dia"] = pd_mod.to_datetime(
                    asientos["fecha"], errors="coerce"
                ).dt.day
                ultimo_dia = asientos[asientos["dia"] >= 28]
                asiento_crm = float(ultimo_dia["monto"].sum()) if len(ultimo_dia) > 0 else 0
            else:
                asiento_crm = 0

            # Solo sugerir si encontramos el asiento del CRM (sino el cálculo
            # no es confiable porque le falta una de las dos partes)
            if abs(asiento_crm) >= 1000 and cantidad_gs >= 5:
                dif_calc = round(total_banco_gs - asiento_crm, 2)
                dif_gs_calculado = ("calculado", dif_calc, total_banco_gs, asiento_crm, cantidad_gs)
        except Exception:
            pass

    # Agregar la sugerencia si encontramos algo
    if dif_gs_calculado is not None:
        if dif_gs_calculado[0] == "hoja_melania":
            dif_gs = dif_gs_calculado[1]
            if abs(dif_gs) >= 50:
                sugeridos.append({
                    "concepto": "Dif gs bancarios",
                    "monto": dif_gs,
                    "explicacion": (
                        "Extraído de la hoja 'gs bancarios' del archivo del banco "
                        "(análisis de Melania)."
                    ),
                    "cantidad_mov": 0,
                })
        elif dif_gs_calculado[0] == "hoja2_melania":
            dif_gs = dif_gs_calculado[1]
            if abs(dif_gs) >= 50:
                sugeridos.append({
                    "concepto": "Dif gs bancarios",
                    "monto": dif_gs,
                    "explicacion": (
                        "Extraído de la 'Hoja2' del archivo del banco (análisis de "
                        "Melania), fila 'dif mes pasado'. Es el arrastre del residuo "
                        "del mes anterior (típicamente el CONS.AP del banco)."
                    ),
                    "cantidad_mov": 0,
                })
        else:
            _, dif_gs, total_banco_gs, asiento_crm, cantidad_gs = dif_gs_calculado
            if abs(dif_gs) >= 50:
                sugeridos.append({
                    "concepto": "Dif gs bancarios",
                    "monto": dif_gs,
                    "explicacion": (
                        f"Calculado como: {cantidad_gs} cargos del banco "
                        f"(${total_banco_gs:,.2f}) − asiento agrupado del CRM "
                        f"(${asiento_crm:,.2f}). **Verificá el monto con tu criterio.**"
                    ),
                    "cantidad_mov": cantidad_gs,
                })

    return sugeridos


def buscar_movimientos_espejo(crm, banco, tolerancia=50):
    """
    Busca pares de movimientos (uno en CRM huérfano, otro en banco huérfano)
    que tienen el MISMO monto exacto pero descripciones distintas.

    Estos pueden ser el mismo movimiento que el algoritmo no logró matchear
    (probablemente por fechas muy desfasadas o descripciones muy diferentes).
    """
    crm_huerfanos = crm[crm["estado"] == "pendiente"].copy()
    banco_huerfanos = banco[banco["estado"] == "pendiente"].copy()

    if crm_huerfanos.empty or banco_huerfanos.empty:
        return []

    pares = []
    # Para cada huérfano del CRM, buscar uno del banco con mismo monto
    for i_crm, fila_crm in crm_huerfanos.iterrows():
        monto_crm = fila_crm["monto"]
        for i_bco, fila_bco in banco_huerfanos.iterrows():
            monto_bco = fila_bco["monto"]
            if abs(monto_crm - monto_bco) <= tolerancia:
                d_dias = abs((fila_crm["fecha"] - fila_bco["fecha"]).days)
                pares.append({
                    "monto": round(monto_crm, 2),
                    "fecha_crm": fila_crm["fecha"],
                    "fecha_bco": fila_bco["fecha"],
                    "dias_diff": d_dias,
                    "desc_crm": fila_crm["descripcion_orig"][:80],
                    "desc_bco": fila_bco["descripcion_orig"][:80],
                })
                break  # Para no listar todos los matches posibles del mismo CRM
    # Ordenar por monto absoluto descendente y limitar a top 5
    pares.sort(key=lambda p: abs(p["monto"]), reverse=True)
    return pares[:5]


def categorizar_finamente(discrepancias):
    """
    Sub-categoriza las discrepancias por palabra clave fina.
    Devuelve dict {categoría: monto_total}
    """
    categorias = {
        "IIBB / Percepciones provinciales": 0,
        "SIRCREB / SIRTAC": 0,
        "Impuesto al cheque (Ley 25.413)": 0,
        "IVA bancario": 0,
        "Comisiones de transferencia": 0,
        "Mantenimiento de cuenta": 0,
        "Intereses acreditados": 0,
        "Cupones de tarjeta sueltos": 0,
        "Débitos Payway-like": 0,
        "TRANSF.BANEL huérfanas": 0,
    }
    for d in discrepancias:
        desc = d["descripcion"].upper()
        monto = d["monto"]
        if "SIRCREB" in desc or "SIRTAC" in desc:
            categorias["SIRCREB / SIRTAC"] += monto
        elif "IIBB" in desc or "PERCEPCION" in desc or "PERC" in desc:
            categorias["IIBB / Percepciones provinciales"] += monto
        elif "LEY 25" in desc or "IMPUESTO LEY" in desc:
            categorias["Impuesto al cheque (Ley 25.413)"] += monto
        elif "IVA" in desc:
            categorias["IVA bancario"] += monto
        elif "COMISION" in desc:
            categorias["Comisiones de transferencia"] += monto
        elif "MANTENIMIENTO" in desc:
            categorias["Mantenimiento de cuenta"] += monto
        elif "INTERES" in desc:
            categorias["Intereses acreditados"] += monto
        elif "CUPON" in desc:
            categorias["Cupones de tarjeta sueltos"] += monto
        elif "PAYWAY" in desc or "DNET" in desc:
            categorias["Débitos Payway-like"] += monto
        elif "BANEL" in desc:
            categorias["TRANSF.BANEL huérfanas"] += monto

    # Quedarnos solo con las que tienen impacto real
    return {k: round(v, 2) for k, v in categorias.items() if abs(v) > 100}


def sugerir_accion(discrepancia):
    """
    Para un movimiento sin match, sugiere qué hacer con él.
    Devuelve un string con la etiqueta de acción.
    """
    desc = discrepancia["descripcion"].upper()
    tipo = discrepancia["tipo"]

    if "COMISIÓN" in tipo or "INTERES" in tipo:
        return "[CARGAR EN CRM]"
    if "LIQ-TC" in tipo:
        return "[REVISAR MANUAL]"
    if "CUPONES" in tipo:
        return "[ASOCIAR A LIQ-TC]"
    if tipo == "FALTANTE EN BANCO" and abs(discrepancia["monto"]) > 100000:
        return "[VERIFICAR CON CLIENTE]"
    if tipo == "FALTANTE EN BANCO":
        return "[VERIFICAR ACREDITACIÓN]"
    if "PAYWAY" in desc or "DNET" in desc:
        return "[AGREGAR AJUSTE]"
    if tipo == "FALTANTE EN CRM":
        return "[CARGAR EN CRM]"
    return "[REVISAR]"


def dias_diff(f1, f2):
    return abs((f1 - f2).days)


def pasada_0_cupones_agrupados(crm, banco, matches):
    """
    Matching especial para asientos LIQ-TC del CRM contra cupones de tarjeta del banco.

    Patrón detectado en datos reales: el banco recibe muchos cupones individuales
    (CUPONES PRIS, PAGO VISA) que el contador agrupa en pocos asientos LIQ-TC en el CRM.
    Los cupones del banco tienen un "número interno" en su concepto; se agrupan por ese ID
    y se suman (neto: créditos - débitos). Si la suma cuadra con un LIQ-TC del CRM,
    los marcamos todos como conciliados (1:N).
    """
    # 1. Identificar los LIQ-TC del CRM que están pendientes
    liq_tc_mask = (crm["estado"] == "pendiente") & (
        crm["descripcion_orig"].str.upper().str.contains("LIQ-TC|LIQ TC|LIQUIDACION", na=False, regex=True)
        | crm["contraparte_orig"].str.upper().str.contains("LIQ-TC|LIQ TC|LIQUIDACION", na=False, regex=True)
    )
    liq_tcs = crm[liq_tc_mask].copy()
    if liq_tcs.empty:
        return

    # 2. Identificar movimientos del banco que son cupones de tarjeta
    cup_mask = (banco["estado"] == "pendiente") & (
        banco["descripcion_orig"].str.upper().str.contains(
            "CUPONES|VISA|MASTERCARD|MASTER|TARJ", na=False, regex=True
        )
    )
    cupones = banco[cup_mask].copy()
    if cupones.empty:
        return

    # 3. Extraer un identificador numérico del concepto de cada cupón (los dígitos del medio).
    #    Patrón típico: "CUPONES PRIS 100000096841515" → extraemos "100000096841515" o similar
    import re
    def extraer_id_cupon(concepto):
        # Buscar secuencia larga de dígitos (8+) — es el ID interno del lote de cupones
        m = re.search(r"\d{8,}", str(concepto))
        return m.group() if m else None

    cupones["cupon_id_full"] = cupones["descripcion_orig"].apply(extraer_id_cupon)
    # Si no se pudo extraer ID, no podemos agrupar
    cupones = cupones[cupones["cupon_id_full"].notna()].copy()
    if cupones.empty:
        return

    # IMPORTANTE: en el banco a veces aparece el mismo lote de cupones con IDs ligeramente
    # distintos para diferenciar créditos vs débitos (ej: "100000096841556" para créditos
    # y "000000096841556" para débitos). Para agruparlos juntos, normalizamos quedándonos
    # con los últimos 10 dígitos (que son los que identifican el lote, no el tipo).
    cupones["cupon_id"] = cupones["cupon_id_full"].str[-10:]

    # 4. Agrupar cupones por ID normalizado y sumar montos netos
    grupos = cupones.groupby("cupon_id").agg(
        suma=("monto", "sum"),
        cantidad=("monto", "count"),
        indices=("monto", lambda s: list(s.index)),
    ).reset_index()

    print(f"  → Cupones agrupados por ID: {len(grupos)} grupos detectados en el banco")
    for _, g in grupos.iterrows():
        print(f"     ID {g['cupon_id']}: {g['cantidad']} cupones, suma neta {g['suma']:,.2f}")

    # 5. Para cada LIQ-TC del CRM, buscar el grupo del banco cuya suma coincida
    next_id = len(matches)
    matcheados_count = 0
    for i_crm, fila_crm in liq_tcs.iterrows():
        if crm.at[i_crm, "estado"] != "pendiente":
            continue
        monto_crm = fila_crm["monto"]

        # Buscar grupo con monto similar (tolerancia: 1 peso o 0.1%)
        for _, grupo in grupos.iterrows():
            suma_grupo = grupo["suma"]
            indices_bco = grupo["indices"]

            # Verificar que ningún cupón del grupo ya esté conciliado
            if any(banco.at[i, "estado"] != "pendiente" for i in indices_bco):
                continue

            # Comparar montos
            diff = abs(monto_crm - suma_grupo)
            max_abs = max(abs(monto_crm), abs(suma_grupo))
            pct = (diff / max_abs * 100) if max_abs > 0 else 0
            if diff <= 1.0 or pct <= 0.1:
                # ¡MATCH! Conciliar el LIQ-TC con todos los cupones del grupo
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
                    "razon_ia": f"Suma de {len(indices_bco)} cupones del banco (ID {grupo['cupon_id']}) = {suma_grupo:,.2f}, coincide con LIQ-TC del CRM.",
                    "i_crm": i_crm,
                    "i_crm_lista": [i_crm],
                    "i_bco": indices_bco[0],  # Para reporte: la primera fila del banco
                    "i_bco_lista": indices_bco,  # Lista completa
                    "diferencia_monto": round(diff, 2),
                    "diferencia_dias": 0,
                    "es_agrupado": True,
                    "tipo_agrupado": "1_a_N",  # 1 CRM → N banco
                    "suma_bco": round(suma_grupo, 2),
                })
                next_id += 1
                matcheados_count += 1
                break  # pasamos al siguiente LIQ-TC

    if matcheados_count > 0:
        print(f"  → {matcheados_count} LIQ-TC matcheados con sus cupones bancarios")


def pasada_1_match_exacto(crm, banco, matches):
    """Match por referencia + monto exacto + fecha exacta."""
    next_id = len(matches)
    for i_crm, fila_crm in crm.iterrows():
        if fila_crm["estado"] != "pendiente":
            continue
        ref_crm = fila_crm["referencia_norm"]
        if not ref_crm:
            continue
        for i_bco, fila_bco in banco.iterrows():
            if fila_bco["estado"] != "pendiente":
                continue
            if fila_bco["referencia_norm"] != ref_crm:
                continue
            if fila_crm["monto"] != fila_bco["monto"]:
                continue
            if fila_crm["fecha"] != fila_bco["fecha"]:
                continue
            # Match perfecto
            mid = f"M{next_id:04d}"
            crm.at[i_crm, "match_id"] = mid
            crm.at[i_crm, "estado"] = "conciliado"
            banco.at[i_bco, "match_id"] = mid
            banco.at[i_bco, "estado"] = "conciliado"
            matches.append({
                "match_id": mid,
                "tipo": "Exacto (referencia+monto+fecha)",
                "confianza": "Alta",
                "i_crm": i_crm,
                "i_bco": i_bco,
                "diferencia_monto": 0.0,
                "diferencia_dias": 0,
            })
            next_id += 1
            break


def pasada_2_match_tolerancia(crm, banco, matches):
    """Match con tolerancia de fecha y/o monto."""
    next_id = len(matches)
    for i_crm, fila_crm in crm.iterrows():
        if fila_crm["estado"] != "pendiente":
            continue

        candidatos = []
        for i_bco, fila_bco in banco.iterrows():
            if fila_bco["estado"] != "pendiente":
                continue
            # Mismo signo: ambos ingresos o ambos egresos
            if (fila_crm["monto"] > 0) != (fila_bco["monto"] > 0):
                continue

            d_dias = dias_diff(fila_crm["fecha"], fila_bco["fecha"])
            if d_dias > CFG.dias_tolerancia:
                continue

            d_monto = abs(fila_crm["monto"] - fila_bco["monto"])
            monto_ok = montos_iguales(fila_crm["monto"], fila_bco["monto"])

            ref_match = (
                fila_crm["referencia_norm"]
                and fila_crm["referencia_norm"] == fila_bco["referencia_norm"]
            )

            # Score: cuanto menor, mejor
            score = d_dias * 10 + d_monto / max(abs(fila_crm["monto"]), 1) * 100
            if ref_match:
                score -= 1000  # referencia coincidente es señal muy fuerte

            # Solo aceptamos si monto está dentro de tolerancia O hay match de referencia
            if monto_ok or ref_match:
                candidatos.append((score, i_bco, d_dias, d_monto, ref_match, monto_ok))

        if not candidatos:
            continue

        candidatos.sort(key=lambda x: x[0])
        score, i_bco, d_dias, d_monto, ref_match, monto_ok = candidatos[0]

        if ref_match and monto_ok and d_dias <= CFG.dias_tolerancia:
            tipo = f"Match con tolerancia (Δ{d_dias}d)"
            confianza = "Alta"
        elif ref_match:
            tipo = f"Misma referencia, monto difiere ${d_monto:,.2f}"
            confianza = "Media"
        else:
            tipo = f"Match aproximado (Δ{d_dias}d, Δ${d_monto:,.2f})"
            confianza = "Media"

        mid = f"M{next_id:04d}"
        crm.at[i_crm, "match_id"] = mid
        crm.at[i_crm, "estado"] = "conciliado" if confianza == "Alta" else "revisar"
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "conciliado" if confianza == "Alta" else "revisar"
        matches.append({
            "match_id": mid,
            "tipo": tipo,
            "confianza": confianza,
            "i_crm": i_crm,
            "i_bco": i_bco,
            "diferencia_monto": round(d_monto, 2),
            "diferencia_dias": d_dias,
        })
        next_id += 1


def pasada_3_match_ia(crm, banco, matches):
    """
    Para los huérfanos restantes, llama a Ollama para que sugiera
    posibles correspondencias por similitud semántica de descripción.
    """
    huerfanos_crm = crm[crm["estado"] == "pendiente"].copy()
    huerfanos_bco = banco[banco["estado"] == "pendiente"].copy()

    if huerfanos_crm.empty or huerfanos_bco.empty:
        return

    if not CFG.usar_ia:
        # Fallback: fuzzy matching tradicional sin IA
        _pasada_3_fuzzy(crm, banco, matches, huerfanos_crm, huerfanos_bco)
        return

    # Armar payload para el LLM con índices secuenciales (C0, C1, C2...)
    # Mantenemos un mapeo: índice_local → índice_original_en_dataframe
    # Esto evita que el modelo confunda IDs entre CRM y banco cuando los rangos
    # originales se superponen o son muy distintos.
    map_crm = {}  # idx_local → idx_original
    items_crm = []
    for idx_local, (idx_orig, fila) in enumerate(huerfanos_crm.iterrows()):
        map_crm[idx_local] = idx_orig
        items_crm.append({
            "id": f"C{idx_local}",
            "fecha": str(fila["fecha"]),
            "monto": round(fila["monto"], 2),
            "descripcion": fila["descripcion_orig"],
            "contraparte": fila["contraparte_orig"],
        })

    map_bco = {}
    items_bco = []
    for idx_local, (idx_orig, fila) in enumerate(huerfanos_bco.iterrows()):
        map_bco[idx_local] = idx_orig
        items_bco.append({
            "id": f"B{idx_local}",
            "fecha": str(fila["fecha"]),
            "monto": round(fila["monto"], 2),
            "descripcion": fila["descripcion_orig"],
        })

    prompt = f"""Sos un asistente experto en conciliación bancaria. Recibís dos listas de movimientos
que no se pudieron matchear automáticamente: una del sistema CRM y otra del extracto bancario.

Tu tarea es identificar correspondencias entre movimientos del CRM y del banco. Existen DOS tipos de match posibles:

TIPO A — MATCH SIMPLE (1 a 1):
Un movimiento del CRM corresponde exactamente a un movimiento del banco. Esto es lo normal cuando alguien hizo un único pago o cobro.

TIPO B — MATCH AGRUPADO (N a 1):
UN movimiento del banco cubre VARIOS movimientos del CRM. Pasa cuando un cliente paga varias facturas con una sola transferencia, o cuando se pagan varios proveedores juntos. La SUMA de los montos del CRM debe ser IGUAL (o casi) al monto del banco.

REGLAS ESTRICTAS — leelas con atención:

1. MONTO: La diferencia de monto NO puede superar el 10%. Diferencias chicas (hasta 3%) son habituales por percepciones de IIBB, SIRCREB o comisiones bancarias y son aceptables. Tené en cuenta que en Argentina los bancos suelen descontar 1-3% sobre las acreditaciones por percepciones de Ingresos Brutos. Diferencias mayores al 10% son INACEPTABLES aunque los nombres se parezcan.

2. SIGNO: Solo se pueden matchear movimientos con el MISMO SIGNO. Un ingreso (+) del CRM solo va con un ingreso del banco. Un egreso (-) del CRM solo va con un egreso del banco.

3. FECHA: Diferencias de hasta 5 días son aceptables.

4. PARA MATCH AGRUPADO (N:1): La SUMA de los montos del CRM agrupados debe igualar el monto del banco con tolerancia del 2%. Verificá la suma matemáticamente antes de sugerir. Solo agrupá si los movimientos del CRM son de la misma contraparte o tienen relación clara entre sí.

5. SI EN DUDA, NO SUGIERAS: es preferible no incluir un match a inventar uno falso. Cada movimiento del CRM o del banco que no tenga un match claro debe quedar fuera de tus sugerencias.

6. CONFIANZA:
   - "alta": match prácticamente seguro (mismo monto, contraparte clara, fecha cercana).
   - "media": señales razonables pero alguna ambigüedad.
   - "baja": NO uses este nivel. Si la confianza sería baja, mejor no sugieras.

MOVIMIENTOS CRM SIN MATCH:
{json.dumps(items_crm, ensure_ascii=False, indent=2)}

MOVIMIENTOS BANCO SIN MATCH:
{json.dumps(items_bco, ensure_ascii=False, indent=2)}

FORMATO DE RESPUESTA (JSON estricto, sin markdown, sin texto adicional):
{{
  "sugerencias": [
    {{
      "tipo": "simple",
      "ids_crm": ["C5"],
      "id_bco": "B12",
      "razon": "explicación breve",
      "confianza": "alta"
    }},
    {{
      "tipo": "agrupado",
      "ids_crm": ["C5", "C8"],
      "id_bco": "B12",
      "suma_crm": 300000.00,
      "monto_bco": 300000.00,
      "razon": "el banco recibió un pago único que cubre dos movimientos del CRM",
      "confianza": "alta"
    }}
  ]
}}

⚠ REGLA CRÍTICA SOBRE LOS IDs ⚠
Los IDs son SIEMPRE el campo "id" exacto que aparece en las listas de arriba. Empiezan con la letra C (movimientos del CRM) o B (movimientos del banco), seguidos de un número entero. Ejemplos VÁLIDOS: "C0", "C15", "B23", "B187".

Ejemplos INVÁLIDOS que NO debés inventar:
- "C-IVA", "C-TRANSFERENCIA-12345" (no inventes IDs basados en el contenido)
- "CRM-14", "BCO-417" (no agregues prefijos extra)
- "C0014C", "B00012G" (no agregues letras o ceros)

Si querés referirte al movimiento que aparece como `"id": "C5"`, escribilo exactamente "C5". Si no estás seguro de qué ID poner, NO sugieras el match.
"""

    print(f"  → Consultando Ollama ({CFG.ollama_modelo}) para {len(items_crm)} CRM × {len(items_bco)} banco...")

    sugerencias = _llamar_ollama_robusto(prompt)
    if sugerencias is None:
        print(f"  → Usando fallback fuzzy matching")
        _pasada_3_fuzzy(crm, banco, matches, huerfanos_crm, huerfanos_bco)
        return

    print(f"  → IA sugirió {len(sugerencias)} matches")

    next_id = len(matches)
    rechazadas_por_validacion = 0
    matches_aceptados = 0
    motivos_rechazo = {
        "sin_ids": 0,
        "id_invalido": 0,
        "signo": 0,
        "monto": 0,
        "fecha": 0,
        "crm_indisponible": 0,
        "bco_indisponible": 0,
    }

    for idx_sug, sug in enumerate(sugerencias):
        # Compatibilidad: aceptar tanto "id_crm" como "ids_crm"
        if "ids_crm" in sug:
            ids_crm_raw = sug["ids_crm"]
            if isinstance(ids_crm_raw, str):
                ids_crm_raw = [ids_crm_raw]
        elif "id_crm" in sug:
            ids_crm_raw = [sug["id_crm"]]
        else:
            motivos_rechazo["sin_ids"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: sin campo ids_crm — keys: {list(sug.keys())}")
            continue

        # Parsear índices LOCALES (los que ve el modelo: C0, C1, ...) y convertirlos
        # a índices ORIGINALES del DataFrame usando los mapeos.
        def extraer_indice(valor):
            if not isinstance(valor, str):
                valor = str(valor)
            m = re.search(r"\d+", valor)
            return int(m.group()) if m else None

        try:
            indices_locales_crm = []
            for s in ids_crm_raw:
                idx = extraer_indice(s)
                if idx is None:
                    raise ValueError(f"no se pudo extraer índice de '{s}'")
                if idx not in map_crm:
                    raise ValueError(f"índice local CRM {idx} no existe (max: {len(map_crm)-1})")
                indices_locales_crm.append(idx)
            i_local_bco = extraer_indice(sug.get("id_bco", ""))
            if i_local_bco is None:
                raise ValueError(f"no se pudo extraer índice de id_bco='{sug.get('id_bco')}'")
            if i_local_bco not in map_bco:
                raise ValueError(f"índice local BCO {i_local_bco} no existe (max: {len(map_bco)-1})")

            # Traducir a índices originales del DataFrame
            indices_crm = [map_crm[idx] for idx in indices_locales_crm]
            i_bco = map_bco[i_local_bco]
        except (KeyError, ValueError, AttributeError) as e:
            motivos_rechazo["id_invalido"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: {e} — sugerencia: {sug}")
            continue

        # Validar que todos los movimientos sigan disponibles
        crm_estados = [crm.at[i, "estado"] for i in indices_crm]
        if any(e != "pendiente" for e in crm_estados):
            motivos_rechazo["crm_indisponible"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: CRM ya conciliado — ids_crm={indices_crm}, estados={crm_estados}")
            continue
        if banco.at[i_bco, "estado"] != "pendiente":
            motivos_rechazo["bco_indisponible"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: BCO ya conciliado — id_bco={i_bco}")
            continue

        es_agrupado = len(indices_crm) > 1

        # === VALIDACIÓN MATEMÁTICA ===
        suma_crm = sum(crm.at[i, "monto"] for i in indices_crm)
        monto_bco = banco.at[i_bco, "monto"]

        # Mismo signo
        if (suma_crm > 0) != (monto_bco > 0):
            rechazadas_por_validacion += 1
            motivos_rechazo["signo"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: signo opuesto — CRM={suma_crm:,.2f} BCO={monto_bco:,.2f}")
            continue

        # Diferencia de monto (10% para IA)
        d_monto = abs(suma_crm - monto_bco)
        max_abs = max(abs(suma_crm), abs(monto_bco))
        pct_diff = (d_monto / max_abs * 100) if max_abs > 0 else 0
        if pct_diff > 10.0:
            rechazadas_por_validacion += 1
            motivos_rechazo["monto"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: monto >10% — CRM={suma_crm:,.2f} BCO={monto_bco:,.2f} ({pct_diff:.1f}%)")
            continue

        # Diferencia de fecha
        d_dias_min = min(dias_diff(crm.at[i, "fecha"], banco.at[i_bco, "fecha"]) for i in indices_crm)
        if d_dias_min > 7:
            rechazadas_por_validacion += 1
            motivos_rechazo["fecha"] += 1
            print(f"     ✗ Sugerencia #{idx_sug}: fecha >7 días — diferencia {d_dias_min} días")
            continue

        matches_aceptados += 1

        # === REGISTRAR MATCH ===
        mid = f"M{next_id:04d}"
        confianza = sug.get("confianza", "media").capitalize()

        # Marcar todos los CRM como en revisión
        for i_crm in indices_crm:
            crm.at[i_crm, "match_id"] = mid
            crm.at[i_crm, "estado"] = "revisar"

        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "revisar"

        if es_agrupado:
            tipo = f"Sugerido por IA - Agrupado ({len(indices_crm)} CRM → 1 Banco)"
        else:
            tipo = "Sugerido por IA"

        # En match agrupado, guardamos la lista de índices del CRM
        # Para la hoja Conciliados se usa el primero pero queda registro completo
        matches.append({
            "match_id": mid,
            "tipo": tipo,
            "confianza": confianza,
            "razon_ia": sug.get("razon", ""),
            "i_crm": indices_crm[0],
            "i_crm_lista": indices_crm,  # Lista completa para reporte detallado
            "i_bco": i_bco,
            "diferencia_monto": round(d_monto, 2),
            "diferencia_dias": d_dias_min,
            "es_agrupado": es_agrupado,
            "suma_crm": round(suma_crm, 2) if es_agrupado else None,
        })
        next_id += 1

    # Resumen SIEMPRE se imprime, aunque no haya rechazos
    print(f"  → RESUMEN: {matches_aceptados} aceptadas / {sum(motivos_rechazo.values())} rechazadas (de {len(sugerencias)} sugerencias)")
    if sum(motivos_rechazo.values()) > 0:
        detalles = []
        if motivos_rechazo["sin_ids"] > 0:
            detalles.append(f"{motivos_rechazo['sin_ids']} sin campo ids_crm")
        if motivos_rechazo["id_invalido"] > 0:
            detalles.append(f"{motivos_rechazo['id_invalido']} con IDs inválidos o fuera de rango")
        if motivos_rechazo["crm_indisponible"] > 0:
            detalles.append(f"{motivos_rechazo['crm_indisponible']} CRM ya conciliado")
        if motivos_rechazo["bco_indisponible"] > 0:
            detalles.append(f"{motivos_rechazo['bco_indisponible']} BCO ya conciliado")
        if motivos_rechazo["signo"] > 0:
            detalles.append(f"{motivos_rechazo['signo']} signo opuesto")
        if motivos_rechazo["monto"] > 0:
            detalles.append(f"{motivos_rechazo['monto']} monto >10%")
        if motivos_rechazo["fecha"] > 0:
            detalles.append(f"{motivos_rechazo['fecha']} fecha >7 días")
        print(f"     Motivos rechazo: {', '.join(detalles)}")


def _extraer_json(texto):
    """
    Extrae un objeto JSON de un string que puede tener texto adicional
    o venir envuelto en bloques markdown tipo ```json ... ```.
    Devuelve dict o None si no se puede parsear.
    """
    if not texto or not texto.strip():
        return None

    # 1. Intentar parseo directo
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    # 2. Sacar bloques markdown ```json ... ``` o ``` ... ```
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", texto, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Buscar la primera "{" y la última "}" balanceadas
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        candidato = texto[inicio:fin + 1]
        try:
            return json.loads(candidato)
        except json.JSONDecodeError:
            pass

    return None


def _llamar_ollama_robusto(prompt):
    """
    Llama a Ollama con manejo robusto de errores y parseo flexible.
    Devuelve la lista de sugerencias o None si falló todo.
    """
    try:
        resp = requests.post(
            CFG.ollama_url,
            json={
                "model": CFG.ollama_modelo,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "think": False,  # Desactiva el razonamiento interno (qwen3, deepseek-r1, etc.)
                "options": {"temperature": 0.1, "num_predict": 4096},
            },
            timeout=CFG.ollama_timeout,
        )
        resp.raise_for_status()
        respuesta_cruda = resp.json().get("response", "")
    except requests.exceptions.ConnectionError:
        print(f"  ⚠ No se pudo conectar a Ollama en {CFG.ollama_url}")
        print(f"     Verificá que esté corriendo: ollama serve")
        return None
    except requests.exceptions.Timeout:
        print(f"  ⚠ Ollama tardó más de {CFG.ollama_timeout}s en responder")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"  ⚠ Ollama devolvió HTTP {e.response.status_code}")
        if e.response.status_code == 404:
            print(f"     El modelo '{CFG.ollama_modelo}' no existe.")
            print(f"     Verificá los modelos disponibles con: ollama list")
        return None
    except Exception as e:
        print(f"  ⚠ Error inesperado: {e}")
        return None

    if not respuesta_cruda.strip():
        print(f"  ⚠ Ollama devolvió respuesta vacía")
        return None

    data = _extraer_json(respuesta_cruda)

    if data is None:
        print(f"  ⚠ La respuesta del modelo no es JSON válido.")
        print(f"     Respuesta cruda (primeros 500 chars):")
        print(f"     {respuesta_cruda[:500]}")
        # Guardar respuesta completa para debug
        try:
            with open("ollama_respuesta_debug.txt", "w", encoding="utf-8") as f:
                f.write(respuesta_cruda)
            print(f"     Respuesta completa guardada en: ollama_respuesta_debug.txt")
        except Exception:
            pass
        return None

    sugerencias = data.get("sugerencias", [])
    if not isinstance(sugerencias, list):
        print(f"  ⚠ El campo 'sugerencias' no es una lista. JSON recibido: {data}")
        return None

    return sugerencias


def _pasada_3_fuzzy(crm, banco, matches, huerfanos_crm, huerfanos_bco):
    """Fallback sin IA: matching por similitud de descripción con rapidfuzz."""
    next_id = len(matches)
    for i_crm, fila_crm in huerfanos_crm.iterrows():
        if crm.at[i_crm, "estado"] != "pendiente":
            continue
        mejor = None
        for i_bco, fila_bco in huerfanos_bco.iterrows():
            if banco.at[i_bco, "estado"] != "pendiente":
                continue
            if (fila_crm["monto"] > 0) != (fila_bco["monto"] > 0):
                continue
            sim = fuzz.token_set_ratio(
                fila_crm["descripcion_norm"], fila_bco["descripcion_norm"]
            )
            if sim < CFG.umbral_similitud_desc:
                continue
            d_dias = dias_diff(fila_crm["fecha"], fila_bco["fecha"])
            d_monto = abs(fila_crm["monto"] - fila_bco["monto"])
            score = sim - d_dias * 5 - (d_monto / max(abs(fila_crm["monto"]), 1)) * 50
            if mejor is None or score > mejor[0]:
                mejor = (score, i_bco, sim, d_dias, d_monto)

        if mejor is None:
            continue

        score, i_bco, sim, d_dias, d_monto = mejor
        mid = f"M{next_id:04d}"
        crm.at[i_crm, "match_id"] = mid
        crm.at[i_crm, "estado"] = "revisar"
        banco.at[i_bco, "match_id"] = mid
        banco.at[i_bco, "estado"] = "revisar"
        matches.append({
            "match_id": mid,
            "tipo": f"Sugerido por similitud ({sim}%)",
            "confianza": "Media" if sim >= 85 else "Baja",
            "i_crm": i_crm,
            "i_bco": i_bco,
            "diferencia_monto": round(d_monto, 2),
            "diferencia_dias": d_dias,
        })
        next_id += 1


# ============================================================
# CLASIFICACIÓN DE DISCREPANCIAS
# ============================================================

def clasificar_huerfanos(crm, banco):
    """Clasifica los movimientos sin match en categorías accionables."""
    discrepancias = []

    # Huérfanos del CRM con estado="pendiente" = faltantes en banco (cobranzas no acreditadas, pagos no efectivizados)
    for i, fila in crm[crm["estado"] == "pendiente"].iterrows():
        discrepancias.append({
            "tipo": "FALTANTE EN BANCO",
            "origen": "CRM",
            "fila": fila["fila_origen"],
            "fecha": fila["fecha"],
            "monto": fila["monto"],
            "descripcion": fila["descripcion_orig"],
            "contraparte": fila["contraparte_orig"],
            "referencia": fila["referencia"],
            "comentario": "Cargado en CRM, no aparece en el extracto bancario.",
        })

    # Asientos LIQ-TC del CRM = revisión manual del contador
    if "categoria_especial" in crm.columns:
        for i, fila in crm[crm["estado"] == "liq_tc"].iterrows():
            discrepancias.append({
                "tipo": "LIQ-TC (REVISIÓN MANUAL)",
                "origen": "CRM",
                "fila": fila["fila_origen"],
                "fecha": fila["fecha"],
                "monto": fila["monto"],
                "descripcion": fila["descripcion_orig"],
                "contraparte": fila["contraparte_orig"],
                "referencia": fila["referencia"],
                "comentario": "Asiento agrupado de liquidación de tarjetas. No tiene contrapartida 1:1 directa en el banco — revisar manualmente que la suma de cupones bancarios cuadre.",
            })

    # Huérfanos del banco = faltantes en CRM
    for i, fila in banco[banco["estado"] == "pendiente"].iterrows():
        desc = fila["descripcion_norm"]
        if any(k in desc for k in ["COMISION", "MANTENIMIENTO", "IIBB", "IVA", "IMPUESTO", "SIRCREB", "SIRTAC", "PERCEPCION"]):
            tipo = "COMISIÓN/IMPUESTO NO REGISTRADO"
            comentario = "Cargo bancario no registrado en CRM. Verificar si corresponde provisionar."
        elif any(k in desc for k in ["INTERES"]) and fila["monto"] > 0:
            tipo = "INTERESES NO REGISTRADOS"
            comentario = "Acreditación de intereses bancarios no registrada en CRM."
        elif any(k in desc for k in ["CUPON", "VISA", "MASTERCARD", "TARJ"]):
            tipo = "CUPONES DE TARJETA (parte de LIQ-TC)"
            comentario = "Movimiento bancario de cupones de tarjeta. Probablemente esté incluido en algún asiento LIQ-TC del CRM."
        else:
            tipo = "FALTANTE EN CRM"
            comentario = "Movimiento bancario sin contrapartida en el CRM. Verificar carga."

        discrepancias.append({
            "tipo": tipo,
            "origen": "BANCO",
            "fila": fila["fila_origen"],
            "fecha": fila["fecha"],
            "monto": fila["monto"],
            "descripcion": fila["descripcion_orig"],
            "contraparte": "",
            "referencia": fila["referencia"],
            "comentario": comentario,
        })

    return discrepancias


# ============================================================
# GENERACIÓN DEL REPORTE EXCEL
# ============================================================

def generar_reporte(crm, banco, matches, discrepancias, path_salida, resumen_contable=None):
    wb = Workbook()

    # Estilos
    header_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", start_color="1F4E78")
    title_font = Font(name="Arial", bold=True, size=16, color="1F4E78")
    section_font = Font(name="Arial", bold=True, size=12, color="1F4E78")
    money_format = '#,##0.00;[Red](#,##0.00);"-"'

    # ----- Hoja 1: Conciliación Contable (lo más importante) -----
    ws = wb.active
    ws.title = "Conciliación"

    if resumen_contable is None:
        resumen_contable = {
            "neto_crm": float(crm["monto"].sum()),
            "neto_banco": float(banco["monto"].sum()),
            "diferencia": 0,
            "composicion": {},
            "diferencia_residual": 0,
            "concilia_ok": False,
            "top_movimientos": [],
        }

    ws["A1"] = "CONCILIACIÓN BANCARIA"
    ws["A1"].font = title_font
    ws.merge_cells("A1:D1")

    ws["A2"] = f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")

    # === Resumen ejecutivo (datos clave en 3 líneas) ===
    saldo_calculado_resumen = resumen_contable.get("saldo_banco_calculado",
                              resumen_contable.get("neto_crm", 0))
    saldo_extracto_resumen = resumen_contable.get("saldo_extracto",
                              resumen_contable.get("neto_banco", 0))
    diferencia_resumen = resumen_contable.get("diferencia_final",
                         resumen_contable.get("diferencia", 0))

    ws["A3"] = (f"Saldo Banco Calculado: ${saldo_calculado_resumen:,.2f}  |  "
                f"Saldo Extracto: ${saldo_extracto_resumen:,.2f}  |  "
                f"Diferencia: ${diferencia_resumen:,.2f}")
    ws["A3"].font = Font(name="Arial", size=10, bold=True, color="333333")
    ws.merge_cells("A3:D3")

    # === Estado de la conciliación (banner verde o rojo) ===
    fila = 5
    if resumen_contable["concilia_ok"]:
        ws.cell(row=fila, column=1, value="✓ CONCILIA — Los movimientos del CRM y del banco se corresponden")
        ws.cell(row=fila, column=1).font = Font(name="Arial", bold=True, size=12, color="FFFFFF")
        ws.cell(row=fila, column=1).fill = PatternFill("solid", start_color="2E7D32")
    else:
        ws.cell(row=fila, column=1, value="⚠ HAY DIFERENCIA — Revisar la composición y los movimientos abajo")
        ws.cell(row=fila, column=1).font = Font(name="Arial", bold=True, size=12, color="FFFFFF")
        ws.cell(row=fila, column=1).fill = PatternFill("solid", start_color="C62828")
    ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=4)
    ws.row_dimensions[fila].height = 22

    # === Bloque: Totales ===
    fila = 6
    ws.cell(row=fila, column=1, value="TOTALES DEL PERÍODO").font = section_font
    fila += 1
    ws.cell(row=fila, column=1, value="Concepto").font = header_font
    ws.cell(row=fila, column=1).fill = header_fill
    ws.cell(row=fila, column=2, value="Monto").font = header_font
    ws.cell(row=fila, column=2).fill = header_fill

    fila += 1
    ws.cell(row=fila, column=1, value="Movimientos netos en CRM (Debe - Haber)")
    c = ws.cell(row=fila, column=2, value=round(resumen_contable["neto_crm"], 2))
    c.number_format = money_format

    fila += 1
    ws.cell(row=fila, column=1, value="Movimientos netos en Banco (Crédito - Débito)")
    c = ws.cell(row=fila, column=2, value=round(resumen_contable["neto_banco"], 2))
    c.number_format = money_format

    fila += 1
    ws.cell(row=fila, column=1, value="DIFERENCIA (CRM - Banco)")
    ws.cell(row=fila, column=1).font = Font(name="Arial", bold=True, size=11)
    c = ws.cell(row=fila, column=2, value=round(resumen_contable["diferencia"], 2))
    c.font = Font(name="Arial", bold=True, size=11)
    c.number_format = money_format
    if abs(resumen_contable["diferencia"]) < 1:
        c.fill = PatternFill("solid", start_color="C8E6C9")
    else:
        c.fill = PatternFill("solid", start_color="FFCDD2")

    # === Bloque: Composición de la diferencia ===
    fila += 3
    ws.cell(row=fila, column=1, value="COMPOSICIÓN DE LA DIFERENCIA").font = section_font

    fila += 1
    ws.cell(row=fila, column=1, value="Categoría").font = header_font
    ws.cell(row=fila, column=1).fill = header_fill
    ws.cell(row=fila, column=2, value="Impacto").font = header_font
    ws.cell(row=fila, column=2).fill = header_fill
    ws.cell(row=fila, column=3, value="Explicación").font = header_font
    ws.cell(row=fila, column=3).fill = header_fill

    explicaciones = {
        "cobranzas_pendientes": "Cobros registrados en CRM que aún no aparecen en el banco (no se acreditó la transferencia todavía).",
        "cargos_bancarios_no_registrados": "Cargos automáticos del banco (comisiones, IIBB, percepciones, impuestos) que no se cargaron en CRM.",
        "cupones_sueltos": "Cupones de tarjeta del banco que no fueron agrupados con ningún asiento LIQ-TC del CRM.",
        "liq_tc_sin_match": "Asientos LIQ-TC del CRM que no pudieron emparejarse con cupones del banco (revisar manualmente).",
        "otros_banco_no_crm": "Otros movimientos del banco sin contrapartida en el CRM (transferencias agrupadas, etc.).",
    }
    nombres_legibles = {
        "cobranzas_pendientes": "Cobranzas pendientes de acreditación",
        "cargos_bancarios_no_registrados": "Cargos bancarios no registrados en CRM",
        "cupones_sueltos": "Cupones de tarjeta sueltos",
        "liq_tc_sin_match": "LIQ-TC sin match",
        "otros_banco_no_crm": "Otros movimientos del banco no en CRM",
    }

    composicion = resumen_contable.get("composicion", {})
    for key, valor in composicion.items():
        fila += 1
        ws.cell(row=fila, column=1, value=nombres_legibles.get(key, key))
        c = ws.cell(row=fila, column=2, value=valor)
        c.number_format = money_format
        ws.cell(row=fila, column=3, value=explicaciones.get(key, ""))
        ws.cell(row=fila, column=3).font = Font(name="Arial", size=9, italic=True, color="555555")

    fila += 1
    ws.cell(row=fila, column=1, value="Suma de impactos explicados")
    ws.cell(row=fila, column=1).font = Font(name="Arial", bold=True)
    c = ws.cell(row=fila, column=2, value=round(sum(composicion.values()), 2))
    c.font = Font(name="Arial", bold=True)
    c.number_format = money_format

    fila += 1
    ws.cell(row=fila, column=1, value="Diferencia residual sin explicación")
    ws.cell(row=fila, column=1).font = Font(name="Arial", bold=True, color="C62828")
    c = ws.cell(row=fila, column=2, value=resumen_contable["diferencia_residual"])
    c.font = Font(name="Arial", bold=True, color="C62828")
    c.number_format = money_format
    ws.cell(row=fila, column=3, value="Este es el valor que el contador debe investigar manualmente.")
    ws.cell(row=fila, column=3).font = Font(name="Arial", size=9, italic=True, color="555555")

    # === Bloque: Top movimientos individuales ===
    top_mov = resumen_contable.get("top_movimientos", [])
    if top_mov:
        fila += 3
        ws.cell(row=fila, column=1, value="MOVIMIENTOS QUE MÁS APORTAN A LA DIFERENCIA").font = section_font
        fila += 1
        ws.cell(row=fila, column=1, value=f"(Top {len(top_mov)} ordenados por impacto absoluto)").font = Font(name="Arial", size=9, italic=True, color="666666")

        fila += 1
        headers_top = ["Origen", "Fecha", "Monto", "Descripción", "Tipo"]
        for col, h in enumerate(headers_top, start=1):
            c = ws.cell(row=fila, column=col, value=h)
            c.font = header_font
            c.fill = header_fill

        for d in top_mov:
            fila += 1
            ws.cell(row=fila, column=1, value=d["origen"])
            ws.cell(row=fila, column=2, value=d["fecha"]).number_format = "dd/mm/yyyy"
            c = ws.cell(row=fila, column=3, value=round(d["monto"], 2))
            c.number_format = money_format
            ws.cell(row=fila, column=4, value=d["descripcion"][:80])
            ws.cell(row=fila, column=5, value=d["tipo"])

    # Anchos de columnas
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 60
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 35

    # Continúa con las hojas detalladas existentes (renombrada la antigua "Resumen" para no chocar)
    ws_detalle = wb.create_sheet("Detalle conteo")
    total_crm = len(crm)
    total_bco = len(banco)
    conciliados_alta = sum(1 for m in matches if m["confianza"] == "Alta")
    a_revisar = sum(1 for m in matches if m["confianza"] != "Alta")
    sin_match_crm = (crm["estado"] == "pendiente").sum()
    sin_match_bco = (banco["estado"] == "pendiente").sum()

    resumen = [
        ("Métrica", "Valor"),
        ("Movimientos en CRM", total_crm),
        ("Movimientos en Banco", total_bco),
        ("", ""),
        ("Pares conciliados (alta confianza)", conciliados_alta),
        ("Pares sugeridos para revisión", a_revisar),
        ("", ""),
        ("Discrepancias - Faltantes en Banco", sum(1 for d in discrepancias if d["tipo"] == "FALTANTE EN BANCO")),
        ("Discrepancias - Faltantes en CRM", sum(1 for d in discrepancias if d["tipo"] == "FALTANTE EN CRM")),
        ("Discrepancias - Comisiones/Impuestos", sum(1 for d in discrepancias if "COMISIÓN" in d["tipo"])),
        ("Discrepancias - Intereses", sum(1 for d in discrepancias if "INTERES" in d["tipo"])),
        ("", ""),
        ("TOTAL DISCREPANCIAS", len(discrepancias)),
        ("TOTAL A REVISAR (sugerencias + discrepancias)", a_revisar + len(discrepancias)),
    ]

    for idx, (k, v) in enumerate(resumen, start=1):
        ws_detalle.cell(row=idx, column=1, value=k).font = Font(name="Arial", bold=(idx == 1 or "TOTAL" in str(k)), size=11)
        ws_detalle.cell(row=idx, column=2, value=v).font = Font(name="Arial", size=11)
        if idx == 1:
            ws_detalle.cell(row=idx, column=1).fill = header_fill
            ws_detalle.cell(row=idx, column=1).font = header_font
            ws_detalle.cell(row=idx, column=2).fill = header_fill
            ws_detalle.cell(row=idx, column=2).font = header_font

    ws_detalle.column_dimensions["A"].width = 50
    ws_detalle.column_dimensions["B"].width = 18

    # ----- Hoja 2: Conciliados OK -----
    ws2 = wb.create_sheet("Conciliados")
    headers = [
        "Match ID", "Tipo", "Confianza",
        "Fecha CRM", "Monto CRM", "Descripción CRM", "Ref CRM",
        "Fecha Banco", "Monto Banco", "Descripción Banco", "Ref Banco",
        "Δ Días", "Δ Monto", "Comentario IA",
    ]
    ws2.append(headers)
    for col_idx in range(1, len(headers) + 1):
        c = ws2.cell(row=1, column=col_idx)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center")

    # Construir filas: si un match es agrupado, una fila por cada CRM
    filas_conciliados = []
    for m in matches:
        fbco = banco.iloc[m["i_bco"]]
        indices_crm = m.get("i_crm_lista", [m["i_crm"]])
        es_agrupado = m.get("es_agrupado", False)

        for k, i_crm in enumerate(indices_crm):
            fcrm = crm.iloc[i_crm]
            # En match agrupado, solo la primera fila muestra los datos del banco
            if es_agrupado and k > 0:
                fila_banco_data = ["", "", "(idem agrupado)", ""]
                comentario = f"(parte {k+1}/{len(indices_crm)} del agrupado — Suma CRM: {m.get('suma_crm', 0):,.2f})"
            else:
                fila_banco_data = [fbco["fecha"], fbco["monto"], fbco["descripcion_orig"], fbco["referencia"]]
                if es_agrupado:
                    comentario = f"{m.get('razon_ia', '')} (Suma CRM: {m.get('suma_crm', 0):,.2f} = Banco: {fbco['monto']:,.2f})"
                else:
                    comentario = m.get("razon_ia", "")

            filas_conciliados.append([
                m["match_id"], m["tipo"], m["confianza"],
                fcrm["fecha"], fcrm["monto"], fcrm["descripcion_orig"], fcrm["referencia"],
                *fila_banco_data,
                m["diferencia_dias"] if (k == 0 or not es_agrupado) else "",
                m["diferencia_monto"] if (k == 0 or not es_agrupado) else "",
                comentario,
            ])

    for fila in filas_conciliados:
        ws2.append(fila)

    # Formato y resaltado por confianza
    for fila_idx in range(2, len(filas_conciliados) + 2):
        confianza = ws2.cell(row=fila_idx, column=3).value
        color = {"Alta": "C6EFCE", "Media": "FFEB9C", "Baja": "FFC7CE"}.get(confianza, "FFFFFF")
        for col_idx in range(1, len(headers) + 1):
            c = ws2.cell(row=fila_idx, column=col_idx)
            c.font = Font(name="Arial", size=10)
            c.fill = PatternFill("solid", start_color=color)
            if col_idx in (4, 8):
                c.number_format = "dd/mm/yyyy"
            if col_idx in (5, 9, 13):
                c.number_format = '#,##0.00;[Red](#,##0.00);"-"'

    anchos = [10, 32, 10, 11, 14, 30, 12, 11, 14, 32, 12, 8, 12, 40]
    for i, a in enumerate(anchos, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = a
    ws2.freeze_panes = "A2"

    # ----- Hoja 3: Discrepancias (la más importante) -----
    ws3 = wb.create_sheet("Discrepancias")
    headers3 = [
        "Tipo", "Origen", "Fila Origen", "Fecha", "Monto",
        "Descripción", "Contraparte", "Referencia", "Comentario",
    ]
    ws3.append(headers3)
    for col_idx in range(1, len(headers3) + 1):
        c = ws3.cell(row=1, column=col_idx)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center")

    # Ordenar por tipo para que la lectura sea más clara
    orden_tipo = {
        "FALTANTE EN BANCO": 1,           # Lo más crítico: cobros pendientes de acreditación
        "FALTANTE EN CRM": 2,             # Movimientos bancarios sin contrapartida contable
        "LIQ-TC (REVISIÓN MANUAL)": 3,    # Asientos del contador para revisar
        "CUPONES DE TARJETA (parte de LIQ-TC)": 4,  # Cupones bancarios que componen los LIQ-TC
        "COMISIÓN/IMPUESTO NO REGISTRADO": 5,
        "INTERESES NO REGISTRADOS": 6,
    }
    discrepancias_ord = sorted(discrepancias, key=lambda d: (orden_tipo.get(d["tipo"], 99), d["fecha"]))

    for d in discrepancias_ord:
        ws3.append([
            d["tipo"], d["origen"], d["fila"], d["fecha"], d["monto"],
            d["descripcion"], d["contraparte"], d["referencia"], d["comentario"],
        ])

    colores_tipo = {
        "FALTANTE EN BANCO": "FFD4A3",                       # naranja claro
        "FALTANTE EN CRM": "FFC7CE",                         # rojo claro
        "LIQ-TC (REVISIÓN MANUAL)": "D9E1F2",                # azul claro
        "CUPONES DE TARJETA (parte de LIQ-TC)": "E2EFDA",    # verde claro
        "COMISIÓN/IMPUESTO NO REGISTRADO": "FFEB9C",         # amarillo
        "INTERESES NO REGISTRADOS": "DDEBF7",                # celeste
    }
    for fila_idx in range(2, len(discrepancias_ord) + 2):
        tipo = ws3.cell(row=fila_idx, column=1).value
        color = colores_tipo.get(tipo, "FFFFFF")
        for col_idx in range(1, len(headers3) + 1):
            c = ws3.cell(row=fila_idx, column=col_idx)
            c.font = Font(name="Arial", size=10)
            c.fill = PatternFill("solid", start_color=color)
            if col_idx == 4:
                c.number_format = "dd/mm/yyyy"
            if col_idx == 5:
                c.number_format = '#,##0.00;[Red](#,##0.00);"-"'

    anchos3 = [32, 9, 11, 11, 14, 36, 26, 14, 50]
    for i, a in enumerate(anchos3, start=1):
        ws3.column_dimensions[get_column_letter(i)].width = a
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = ws3.dimensions

    # ----- Hoja 4: Parámetros -----
    ws4 = wb.create_sheet("Parámetros")
    params = [
        ("Parámetro", "Valor usado"),
        ("Tolerancia días", CFG.dias_tolerancia),
        ("Tolerancia monto absoluta", CFG.monto_tolerancia_abs),
        ("Tolerancia monto %", CFG.monto_tolerancia_pct),
        ("Umbral similitud descripción (%)", CFG.umbral_similitud_desc),
        ("Uso de IA", "Sí" if CFG.usar_ia else "No"),
        ("Modelo Ollama", CFG.ollama_modelo),
        ("URL Ollama", CFG.ollama_url),
    ]
    for row in params:
        ws4.append(row)
    for col_idx in range(1, 3):
        c = ws4.cell(row=1, column=col_idx)
        c.font = header_font
        c.fill = header_fill
    ws4.column_dimensions["A"].width = 35
    ws4.column_dimensions["B"].width = 25

    wb.save(path_salida)


# ============================================================
# FUNCIÓN PRINCIPAL REUTILIZABLE (para CLI o interfaz web)
# ============================================================

def ejecutar_conciliacion(archivo_crm, archivo_banco, callback_progreso=None,
                          saldo_apertura=0.0, ajustes_manuales=None,
                          saldo_extracto_banco=None):
    """
    Ejecuta la conciliación completa con la lógica contable de saldos.

    Args:
        archivo_crm: ruta a archivo .xlsx o BytesIO con el CRM
        archivo_banco: ruta a archivo .xlsx o BytesIO con el extracto bancario
        callback_progreso: función opcional para reportar progreso
        saldo_apertura: saldo de apertura pendiente del mes anterior (sumado al CRM)
        ajustes_manuales: lista de dicts [{"concepto": str, "monto": float}, ...]
                          que ajustan manualmente el saldo CRM (Payway, dif gs bancarios, etc.)
        saldo_extracto_banco: saldo final del extracto bancario. Si es None,
                              se intenta extraer del archivo o se usa la suma neta.

    Returns:
        dict con:
            - excel_bytes: BytesIO del Excel generado
            - estadisticas: dict con métricas del proceso
            - error: None si OK, mensaje de error si falló
    """
    import io

    if ajustes_manuales is None:
        ajustes_manuales = []

    def reportar(etapa, mensaje, pct):
        if callback_progreso:
            callback_progreso(etapa, mensaje, pct)

    try:
        reportar("carga", "Leyendo archivos...", 10)
        crm = cargar_crm(archivo_crm)
        banco = cargar_banco(archivo_banco)

        total_crm = len(crm)
        total_banco = len(banco)

        matches = []

        reportar("matching", "Matching especial: cupones de tarjeta agrupados...", 15)
        pasada_0_cupones_agrupados(crm, banco, matches)
        n_cupones = len(matches)

        reportar("matching", "Buscando matches exactos...", 25)
        pasada_1_match_exacto(crm, banco, matches)
        n_exactos = len(matches) - n_cupones

        reportar("matching", "Buscando matches con tolerancia...", 45)
        n_antes = len(matches)
        pasada_2_match_tolerancia(crm, banco, matches)
        n_tolerancia = len(matches) - n_antes

        reportar("ia", "Consultando IA para casos ambiguos...", 65)
        n_antes = len(matches)
        pasada_3_match_ia(crm, banco, matches)
        n_ia = len(matches) - n_antes

        reportar("reporte", "Clasificando discrepancias...", 85)
        discrepancias = clasificar_huerfanos(crm, banco)

        # === RESUMEN CONTABLE (lógica de la contadora) ===
        #
        #   Saldo según CRM (movimientos netos del mes)
        # + Saldo apertura pendiente (arrastre del mes anterior)
        # + Ajustes manuales (Payway, dif gs bancarios, etc.)
        # = Saldo según Banco (CALCULADO)
        #
        # Saldo según Extracto (real del banco, suma de todos los movimientos del extracto)
        #
        # DIFERENCIA = Saldo Banco calculado - Saldo según Extracto
        # Si la diferencia es ~0 → la conciliación cierra ✓

        # === Saldo CRM ===
        # Si el archivo trae la columna "Acumulado Mensual", usar ese valor
        # (incluye el arrastre del mes anterior y es lo que usa la contadora).
        # Si no, calcular como suma de movimientos.
        saldo_crm_detectado = crm.attrs.get("saldo_crm_detectado")
        if saldo_crm_detectado is not None:
            saldo_crm = float(saldo_crm_detectado)
        else:
            saldo_crm = float(crm["monto"].sum())
        ajustes_total = sum(a.get("monto", 0) for a in ajustes_manuales)

        # Saldos auto-detectados del archivo bancario (formato "Extracto de Cuenta")
        saldo_inicial_detectado = banco.attrs.get("saldo_inicial_detectado")
        saldo_final_detectado = banco.attrs.get("saldo_final_detectado")
        formato_banco = banco.attrs.get("formato", "desconocido")

        # Si el usuario no cargó saldo_apertura pero el archivo trae saldo inicial detectado,
        # usar ese valor automáticamente.
        if saldo_apertura == 0 and saldo_inicial_detectado is not None:
            saldo_apertura = saldo_inicial_detectado

        saldo_banco_calculado = saldo_crm + saldo_apertura + ajustes_total

        # Saldo según extracto:
        # 1. Si el usuario lo pasó, usar ese.
        # 2. Si no, usar el saldo final detectado del archivo.
        # 3. Si tampoco hay, usar la suma neta + saldo inicial (si lo conocemos).
        if saldo_extracto_banco is not None:
            saldo_extracto = float(saldo_extracto_banco)
        elif saldo_final_detectado is not None:
            saldo_extracto = float(saldo_final_detectado)
        elif saldo_inicial_detectado is not None:
            saldo_extracto = saldo_inicial_detectado + float(banco["monto"].sum())
        else:
            saldo_extracto = float(banco["monto"].sum())

        diferencia_final = round(saldo_banco_calculado - saldo_extracto, 2)

        # Tolerancia: ~$1.000 es ruido aceptable en conciliación contable mensual
        TOLERANCIA_CONCILIACION = 1000.0
        concilia_ok = abs(diferencia_final) < TOLERANCIA_CONCILIACION

        # Composición de la diferencia (qué movimientos podrían explicarla)
        impacto_cobranzas_pendientes = sum(
            d["monto"] for d in discrepancias if d["tipo"] == "FALTANTE EN BANCO"
        )
        impacto_cargos_bco = sum(
            d["monto"] for d in discrepancias
            if d["tipo"] in ("COMISIÓN/IMPUESTO NO REGISTRADO", "INTERESES NO REGISTRADOS")
        )
        impacto_cupones_sueltos = sum(
            d["monto"] for d in discrepancias if "CUPONES" in d["tipo"]
        )
        impacto_liq_tc = sum(
            d["monto"] for d in discrepancias if "LIQ-TC" in d["tipo"]
        )
        impacto_otros_crm = sum(
            d["monto"] for d in discrepancias if d["tipo"] == "FALTANTE EN CRM"
        )

        composicion = {
            "cobranzas_pendientes": round(impacto_cobranzas_pendientes, 2),
            "cargos_bancarios_no_registrados": round(-impacto_cargos_bco, 2),
            "cupones_sueltos": round(-impacto_cupones_sueltos, 2),
            "liq_tc_sin_match": round(impacto_liq_tc, 2),
            "otros_banco_no_crm": round(-impacto_otros_crm, 2),
        }

        # TOP 10 movimientos individuales con mayor impacto
        top_movimientos = sorted(
            discrepancias,
            key=lambda d: abs(d["monto"]),
            reverse=True,
        )[:10]

        # === AJUSTES SUGERIDOS (propuestas concretas con monto) ===
        # Esto convierte las categorías en propuestas listas para aplicar
        ajustes_sugeridos = calcular_ajustes_sugeridos(discrepancias, banco, crm)

        # === SUGERENCIAS AUTOMÁTICAS ===
        sugerencias = []

        if not concilia_ok:
            dif_abs = abs(diferencia_final)
            tolerancia_match = max(500, dif_abs * 0.05)

            # PRIMERO: si hay ajustes sugeridos, presentarlos como una propuesta unificada
            if ajustes_sugeridos:
                suma_ajustes_sugeridos = sum(a["monto"] for a in ajustes_sugeridos)
                lineas_ajustes = "\n".join(
                    f"  • **{a['concepto']}**: $ {a['monto']:,.2f}  _({a['cantidad_mov']} mov)_"
                    for a in ajustes_sugeridos[:8]  # Top 8 ajustes
                )
                # Calcular el residuo si se aplican todos estos ajustes
                residuo_si_aplicas = round(diferencia_final - suma_ajustes_sugeridos, 2)
                sugerencias.append({
                    "tipo": "info",
                    "texto": (
                        f"📊 **Movimientos huérfanos del mes agrupados por categoría:**\n\n"
                        f"{lineas_ajustes}\n\n"
                        f"**Suma total: $ {suma_ajustes_sugeridos:,.2f}**\n\n"
                        f"_Esto es información de referencia, no son los ajustes contables "
                        f"que debés cargar. Los ajustes reales (Payway, débitos pendientes, etc.) "
                        f"requieren tu criterio profesional._"
                    ),
                })

            # CASO A: la diferencia coincide con alguna categoría (general)
            categorias_nombres = {
                "cobranzas_pendientes": "cobranzas pendientes de acreditación",
                "cargos_bancarios_no_registrados": "cargos del banco no registrados en CRM",
                "cupones_sueltos": "cupones de tarjeta sueltos",
                "liq_tc_sin_match": "LIQ-TC sin match con cupones",
                "otros_banco_no_crm": "otros movimientos del banco no en CRM",
            }
            for key, valor in composicion.items():
                if abs(valor - diferencia_final) <= tolerancia_match and abs(valor) > 100:
                    sugerencias.append({
                        "tipo": "tip",
                        "texto": (
                            f"La diferencia ($ {diferencia_final:,.2f}) coincide casi exactamente "
                            f"con el total de **{categorias_nombres[key]}** ($ {valor:,.2f}). "
                            f"Es muy probable que esa categoría sea la causa."
                        ),
                    })
                    break

            # MEJORA 2: CATEGORÍAS FINAS — buscar coincidencia con categorías específicas
            categorias_finas = categorizar_finamente(discrepancias)
            for cat, valor in categorias_finas.items():
                if abs(valor - diferencia_final) <= tolerancia_match and abs(valor) > 100:
                    sugerencias.append({
                        "tipo": "tip",
                        "texto": (
                            f"🎯 La diferencia coincide con el total de **{cat}** "
                            f"($ {valor:,.2f}). Considerá agregarla como ajuste manual o "
                            f"verificar si esos cargos están provisionados en el CRM."
                        ),
                    })

            # CASO B: la diferencia coincide con UN movimiento del top
            for d in top_movimientos:
                if abs(d["monto"] - diferencia_final) <= tolerancia_match:
                    accion = sugerir_accion(d)
                    sugerencias.append({
                        "tipo": "tip",
                        "texto": (
                            f"Encontramos un movimiento de **$ {d['monto']:,.2f}** muy parecido a la "
                            f"diferencia. Podría ser la causa: _{d['descripcion'][:80]}_ "
                            f"({d['origen']}, {d['fecha']}) {accion}"
                        ),
                    })
                    break

            # MEJORA 1: COMBINACIONES — buscar grupos de 2-5 movimientos que sumen la diferencia
            combinaciones = buscar_combinaciones_que_suman(
                discrepancias, diferencia_final,
                tolerancia=max(100, dif_abs * 0.01),
                max_size=5, max_resultados=3,
            )
            for combo in combinaciones:
                lista_movs = "\n".join(
                    f"  • $ {m['monto']:>15,.2f} — {m['origen']} | {m['descripcion'][:60]}"
                    for m in combo["movimientos"]
                )
                sugerencias.append({
                    "tipo": "tip",
                    "texto": (
                        f"🧩 **Combinación encontrada**: la suma de estos "
                        f"{len(combo['movimientos'])} movimientos da $ {combo['suma']:,.2f} "
                        f"(diferencia con el objetivo: $ {combo['diferencia']:,.2f}):\n\n{lista_movs}"
                    ),
                })

            # MEJORA 4: MOVIMIENTOS ESPEJO — pares con mismo monto en ambos lados
            espejos = buscar_movimientos_espejo(crm, banco)
            if espejos:
                lineas = []
                for e in espejos[:3]:
                    lineas.append(
                        f"• $ {e['monto']:,.2f} — CRM ({e['fecha_crm']}): _{e['desc_crm']}_ "
                        f"↔ Banco ({e['fecha_bco']}): _{e['desc_bco']}_"
                        + (f" [{e['dias_diff']} días de diferencia]" if e['dias_diff'] > 0 else "")
                    )
                sugerencias.append({
                    "tipo": "tip",
                    "texto": (
                        f"🔍 **Movimientos espejo detectados** (mismo monto en ambos lados con descripciones distintas):\n\n"
                        + "\n".join(lineas)
                        + "\n\nPodrían ser el mismo movimiento que el algoritmo no detectó."
                    ),
                })

            # CASO C: diferencia positiva grande (CRM > Banco)
            if diferencia_final > 10000 and not any(s["tipo"] == "tip" for s in sugerencias):
                sugerencias.append({
                    "tipo": "info",
                    "texto": (
                        f"El **saldo calculado del CRM es mayor** que el extracto en "
                        f"$ {dif_abs:,.2f}. Las causas más comunes son:\n\n"
                        f"• Cobranzas registradas que no se acreditaron todavía.\n"
                        f"• Asientos de ajuste o contables sin contrapartida bancaria.\n"
                        f"• Transferencias emitidas que el banco aún no procesó."
                    ),
                })

            # CASO D: diferencia negativa grande (CRM < Banco)
            if diferencia_final < -10000 and not any(s["tipo"] == "tip" for s in sugerencias):
                sugerencias.append({
                    "tipo": "info",
                    "texto": (
                        f"El **saldo del extracto bancario es mayor** que el calculado en "
                        f"$ {dif_abs:,.2f}. Las causas más comunes son:\n\n"
                        f"• Cargos automáticos del banco no provisionados en CRM "
                        f"(IIBB, SIRCREB, comisiones, débitos automáticos).\n"
                        f"• Transferencias recibidas no registradas todavía.\n"
                        f"• Ingresos extraordinarios no asentados."
                    ),
                })

            # CASO E: diferencia chica
            if 0 < dif_abs <= 10000 and not sugerencias:
                sugerencias.append({
                    "tipo": "info",
                    "texto": (
                        f"La diferencia es pequeña ($ {dif_abs:,.2f}). Suele deberse a:\n\n"
                        f"• Pequeñas comisiones o cargos no clasificados.\n"
                        f"• Errores de redondeo en algún movimiento.\n"
                        f"• Algún ajuste manual menor todavía sin agregar."
                    ),
                })

            # SIEMPRE: alertar sobre LIQ-TC sin match
            if composicion.get("liq_tc_sin_match", 0) != 0:
                sugerencias.append({
                    "tipo": "warning",
                    "texto": (
                        "Hay asientos **LIQ-TC sin matchear** con sus cupones del banco. "
                        "Esto puede pasar cuando los cupones del banco tienen IDs distintos a los "
                        "del CRM. Revisalos manualmente en la hoja Discrepancias del Excel."
                    ),
                })

            # SIEMPRE: si hay muchas cobranzas pendientes
            if abs(saldo_crm) > 0 and abs(composicion.get("cobranzas_pendientes", 0)) > abs(saldo_crm) * 0.10:
                sugerencias.append({
                    "tipo": "warning",
                    "texto": (
                        f"Hay un volumen importante de **cobranzas pendientes de acreditación** "
                        f"($ {composicion['cobranzas_pendientes']:,.2f}). Verificá con tus clientes "
                        f"si esas transferencias efectivamente se realizaron o están pendientes."
                    ),
                })

        # MEJORA 3: agregar acción sugerida a cada movimiento del top
        for d in top_movimientos:
            d["accion_sugerida"] = sugerir_accion(d)

        reportar("reporte", "Generando reporte Excel...", 95)
        excel_bytes = io.BytesIO()
        generar_reporte(crm, banco, matches, discrepancias, excel_bytes,
                        resumen_contable={
                            "saldo_crm": saldo_crm,
                            "saldo_apertura": saldo_apertura,
                            "ajustes_manuales": ajustes_manuales,
                            "ajustes_total": ajustes_total,
                            "saldo_banco_calculado": saldo_banco_calculado,
                            "saldo_extracto": saldo_extracto,
                            "diferencia_final": diferencia_final,
                            "concilia_ok": concilia_ok,
                            "composicion": composicion,
                            "top_movimientos": top_movimientos,
                            "sugerencias": sugerencias,
                            # Compatibilidad con código previo
                            "neto_crm": saldo_crm,
                            "neto_banco": saldo_extracto,
                            "diferencia": diferencia_final,
                            "diferencia_residual": diferencia_final,
                        })
        excel_bytes.seek(0)

        # Estadísticas para la interfaz
        conciliados_alta = sum(1 for m in matches if m["confianza"] == "Alta")
        a_revisar = sum(1 for m in matches if m["confianza"] != "Alta")

        estadisticas = {
            "total_crm": total_crm,
            "total_banco": total_banco,
            "matches_cupones": n_cupones,
            "matches_exactos": n_exactos,
            "matches_tolerancia": n_tolerancia,
            "matches_ia": n_ia,
            "conciliados_alta_confianza": conciliados_alta,
            "a_revisar": a_revisar,
            "total_discrepancias": len(discrepancias),
            "faltantes_en_banco": sum(1 for d in discrepancias if d["tipo"] == "FALTANTE EN BANCO"),
            "faltantes_en_crm": sum(1 for d in discrepancias if d["tipo"] == "FALTANTE EN CRM"),
            "comisiones_impuestos": sum(1 for d in discrepancias if "COMISIÓN" in d["tipo"]),
            "intereses": sum(1 for d in discrepancias if "INTERES" in d["tipo"]),
            "liq_tc": sum(1 for d in discrepancias if "LIQ-TC" in d["tipo"]),
            "cupones_tarjeta": sum(1 for d in discrepancias if "CUPONES" in d["tipo"]),
            # Resumen contable (lógica de la contadora)
            "saldo_crm": saldo_crm,
            "saldo_apertura": saldo_apertura,
            "ajustes_manuales": ajustes_manuales,
            "ajustes_total": ajustes_total,
            "saldo_banco_calculado": saldo_banco_calculado,
            "saldo_extracto": saldo_extracto,
            "diferencia_final": diferencia_final,
            "concilia_ok": concilia_ok,
            "composicion": composicion,
            "top_movimientos": top_movimientos,
            "sugerencias": sugerencias,
            "ajustes_sugeridos": ajustes_sugeridos,
            "huerfanos_banco": obtener_huerfanos_banco_para_carrito(discrepancias),
            "posibles_debitos_pendientes": obtener_posibles_debitos_pendientes(crm, banco),
            # Info del formato del banco detectado automáticamente
            "formato_banco": formato_banco,
            "saldo_inicial_detectado": saldo_inicial_detectado,
            "saldo_final_detectado": saldo_final_detectado,
            # Compatibilidad con código previo
            "neto_crm": saldo_crm,
            "neto_banco": saldo_extracto,
            "diferencia": diferencia_final,
            "diferencia_residual": diferencia_final,
        }

        reportar("done", "Listo", 100)

        return {
            "excel_bytes": excel_bytes,
            "estadisticas": estadisticas,
            "error": None,
        }

    except Exception as e:
        return {
            "excel_bytes": None,
            "estadisticas": None,
            "error": str(e),
        }


# ============================================================
# MAIN (uso por línea de comandos)
# ============================================================

def main():
    print("=" * 60)
    print("CONCILIACIÓN BANCARIA")
    print("=" * 60)

    print(f"\n[1/5] Cargando archivos...")
    crm = cargar_crm(CFG.archivo_crm)
    banco = cargar_banco(CFG.archivo_banco)
    print(f"  CRM:   {len(crm)} movimientos")
    print(f"  Banco: {len(banco)} movimientos")

    matches = []

    print(f"\n[2/5] Pasada 1 — Match exacto (referencia + monto + fecha)...")
    pasada_1_match_exacto(crm, banco, matches)
    print(f"  → {len(matches)} matches exactos")

    print(f"\n[3/5] Pasada 2 — Match con tolerancia...")
    n_antes = len(matches)
    pasada_2_match_tolerancia(crm, banco, matches)
    print(f"  → {len(matches) - n_antes} matches con tolerancia")

    print(f"\n[4/5] Pasada 3 — Match por IA (Ollama)...")
    n_antes = len(matches)
    pasada_3_match_ia(crm, banco, matches)
    print(f"  → {len(matches) - n_antes} matches sugeridos por IA")

    print(f"\n[5/5] Clasificando discrepancias y generando reporte...")
    discrepancias = clasificar_huerfanos(crm, banco)
    print(f"  → {len(discrepancias)} discrepancias detectadas")

    generar_reporte(crm, banco, matches, discrepancias, CFG.archivo_salida)

    print(f"\n✓ Reporte generado: {CFG.archivo_salida}")
    print("=" * 60)


if __name__ == "__main__":
    main()
