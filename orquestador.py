"""
Orquestador principal de la conciliación.

Es un punto de entrada delgado que:
    1. Recibe qué banco se está conciliando (BBVA, Santander, etc.).
    2. Delega la carga del extracto al banco correspondiente.
    3. Delega las pasadas de matching específicas al banco.
    4. Corre las pasadas comunes (exacto y tolerancia) del núcleo.
    5. Delega el cálculo de ajustes sugeridos al banco.
    6. Devuelve todo lo que la UI necesita.

La lógica del CÁLCULO FINAL (saldo apertura + ajustes = residuo) sigue
en conciliacion.py (por ahora) porque es común a cualquier banco y ya
está probada. En una siguiente iteración se puede extraer al núcleo.
"""

import io

from nucleo.carga_crm import cargar_crm
from nucleo.matching_base import pasada_1_match_exacto, pasada_2_match_tolerancia
from nucleo.ajustes_comunes import (
    posibles_debitos_pendientes_generico,
    huerfanos_banco_para_carrito,
)
from bancos import obtener_banco

# Estas dos funciones siguen viviendo en el módulo original. El import va
# arriba y no dentro de las funciones para que el análisis estático de
# dependencias (el que usa Vercel al armar el bundle de la función) detecte
# que conciliacion.py hace falta y lo incluya.
from conciliacion import clasificar_huerfanos
from nucleo.reporte import generar_reporte_excel
from nucleo.cobertura import clasificar_cobertura


def ejecutar_conciliacion(
    banco_codigo,
    archivo_crm,
    archivo_banco,
    saldo_apertura=0.0,
    ajustes_manuales=None,
    saldo_extracto_banco=None,
    callback_progreso=None,
    saldo_extracto_anterior=None,
):
    """
    Ejecuta la conciliación completa para el banco especificado.

    Parámetros
    ----------
    banco_codigo : str
        Código del banco: "bbva", "santander", etc.
    archivo_crm : ruta o BytesIO
        Excel del libro mayor de GBP.
    archivo_banco : ruta o BytesIO
        Excel del extracto bancario.
    saldo_apertura : float
        Arrastre del mes anterior. Se ignora si se pasa
        `saldo_extracto_anterior`, porque en ese caso se deriva.
    saldo_extracto_anterior : float | None
        Saldo del extracto bancario al cierre del mes anterior. Cuando se
        informa, el saldo de apertura se DERIVA en vez de pedirse:

            apertura = saldo_extracto_anterior - arranque del libro mayor

        Es el desfase heredado: cuánto se llevaban el banco y el libro al
        empezar el mes. Los dos datos salen de los archivos, así que no
        depende de que alguien recuerde un número de meses anteriores.
    ajustes_manuales : list[dict]
        Ajustes cargados a mano en el carrito: [{"concepto", "monto", ...}].
    saldo_extracto_banco : float | None
        Si es None, se autodetecta del archivo del banco.
    callback_progreso : callable | None
        Función para reportar avance (etapa, mensaje, %).

    Devuelve
    --------
    dict con:
        - crm, banco, matches, discrepancias, banco_obj
        - estadisticas: métricas completas
        - error: None o mensaje
    """
    if ajustes_manuales is None:
        ajustes_manuales = []

    def reportar(etapa, mensaje, pct):
        if callback_progreso:
            callback_progreso(etapa, mensaje, pct)

    try:
        # 1. Instanciar el banco elegido
        reportar("carga", f"Preparando perfil de {banco_codigo.upper()}...", 5)
        banco_obj = obtener_banco(banco_codigo)

        # 2. Cargar CRM (común a todos los bancos)
        reportar("carga", "Cargando CRM...", 10)
        crm = cargar_crm(archivo_crm)

        # 3. Cargar extracto (específico del banco)
        reportar("carga", f"Cargando extracto {banco_obj.nombre}...", 15)
        banco = banco_obj.cargar_extracto(archivo_banco)

        # 3b. Derivar el saldo de apertura.
        #
        # El cierre del mes anterior puede venir de dos lados: informado por
        # quien concilia, o leído del propio extracto, porque el saldo con el
        # que abre el extracto de este mes ES el cierre del anterior. Cuando
        # el archivo lo trae (Santander, Galicia) no hay que informar nada;
        # BBVA no lo expone y ahí sí hace falta cargarlo a mano.
        apertura_derivada = None
        arranque_crm = crm.attrs.get("saldo_crm_arranque")
        cierre_anterior = saldo_extracto_anterior
        origen_cierre = "informado"
        if cierre_anterior is None:
            cierre_anterior = banco.attrs.get("saldo_inicial_detectado")
            origen_cierre = "detectado del extracto"

        if cierre_anterior is not None and arranque_crm is not None:
            apertura_derivada = round(float(cierre_anterior) - float(arranque_crm), 2)
            saldo_apertura = apertura_derivada

        # 4. Pasadas de matching
        matches = []

        reportar("matching", "Matching específico del banco...", 25)
        banco_obj.pasadas_matching_especificas(crm, banco, matches)

        reportar("matching", "Match exacto por referencia...", 40)
        pasada_1_match_exacto(crm, banco, matches)

        reportar("matching", "Match con tolerancia...", 55)
        pasada_2_match_tolerancia(crm, banco, matches)

        # 5. Clasificar huérfanos (delegado al viejo por ahora)
        reportar("reporte", "Clasificando discrepancias...", 70)
        discrepancias = clasificar_huerfanos(crm, banco)
        _marcar_partidas_entre_meses(discrepancias)

        # 6. Ajustes sugeridos (específicos del banco)
        reportar("reporte", "Calculando ajustes sugeridos...", 80)
        ajustes_sugeridos = banco_obj.calcular_ajustes_sugeridos(
            discrepancias, banco_df=banco, crm_df=crm
        )

        # 7. Posibles débitos pendientes (común, o sobrescribible por banco)
        posibles_debitos = banco_obj.obtener_posibles_debitos_pendientes(crm, banco)

        # 8. Calcular saldos y residuo final (misma lógica del sistema viejo)
        reportar("reporte", "Calculando saldos...", 85)
        stats = _calcular_saldos_y_residuo(
            crm, banco, matches, discrepancias,
            saldo_apertura, ajustes_manuales, saldo_extracto_banco,
            ajustes_sugeridos, posibles_debitos, banco_obj,
        )

        # Clasificar por qué quedó sin pareja cada movimiento: lo que está
        # compensado contra un asiento agrupado o declarado en un ajuste no
        # es un pendiente, y mezclarlo con lo que sí falta explicar hace que
        # el listado parezca lleno de problemas cuando la conciliación cierra.
        resumen_cobertura = clasificar_cobertura(discrepancias, ajustes_manuales, banco_obj)
        stats.update(resumen_cobertura)

        # Trazabilidad de cómo se obtuvo la apertura, para que la interfaz
        # pueda mostrar el desfase heredado en vez de un número suelto.
        stats["saldo_crm_arranque"] = arranque_crm
        stats["saldo_extracto_anterior"] = cierre_anterior
        stats["cierre_anterior_origen"] = origen_cierre if apertura_derivada is not None else None
        stats["apertura_derivada"] = apertura_derivada
        stats["apertura_origen"] = "derivada" if apertura_derivada is not None else "manual"

        # 9. Generar el archivo Excel de reporte
        reportar("reporte", "Generando reporte Excel...", 95)
        excel_bytes = None
        try:
            excel_bytes = io.BytesIO()
            generar_reporte_excel(
                crm, banco, matches, discrepancias, stats, excel_bytes,
                nombre_banco=banco_obj.nombre,
                periodo=_describir_periodo(banco),
            )
            excel_bytes.seek(0)
        except Exception as e:
            print(f"⚠ No se pudo generar Excel: {e}")

        reportar("done", "Listo.", 100)

        return {
            "crm": crm,
            "banco": banco,
            "matches": matches,
            "discrepancias": discrepancias,
            "banco_obj": banco_obj,
            "estadisticas": stats,
            "excel_bytes": excel_bytes,
            "error": None,
        }

    except Exception as e:
        import traceback
        return {
            "crm": None,
            "banco": None,
            "matches": [],
            "discrepancias": [],
            "banco_obj": None,
            "estadisticas": {},
            "excel_bytes": None,
            "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        }


def _describir_periodo(banco_df):
    """Arma un texto tipo "junio 2026" a partir de las fechas del extracto."""
    MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    try:
        fechas = [f for f in banco_df["fecha"] if f is not None]
        if not fechas:
            return ""
        # El mes predominante, no el ultimo movimiento: los extractos suelen
        # traer algun cargo con fecha del mes siguiente (impuestos que el
        # banco imputa al dia habil posterior) y eso rotularia mal el periodo.
        from collections import Counter
        (anio, mes), _ = Counter((f.year, f.month) for f in fechas).most_common(1)[0]
        return f"{MESES[mes - 1]} {anio}"
    except Exception:
        return ""


def _marcar_partidas_entre_meses(discrepancias):
    """
    Etiqueta las discrepancias que tienen pinta de cruzar de un mes a otro.

    El sistema procesa un mes por vez, así que cuando una operación se
    registra en el libro sobre el cierre y el banco la acredita a principios
    del mes siguiente, queda sin pareja de los dos lados sin que haya nada
    mal. El patrón típico:

      - Movimiento del BANCO en los primeros días del mes: puede ser la
        acreditación de algo que el libro ya registró el mes pasado.
      - Movimiento del CRM sobre el cierre: puede ser un cobro o pago que
        el banco procese recién el mes que viene.

    Es una pista para saber dónde mirar, NO una conclusión: el sistema no
    tiene el mayor del mes anterior, así que no puede confirmarlo. Por eso
    solo agrega una nota y no propone ningún ajuste — un ajuste sugerido
    invita a aceptarlo sin verificar, y ahí es donde se esconden los
    errores que la conciliación tiene que encontrar.
    """
    for d in discrepancias:
        fecha = d.get("fecha")
        if not fecha or not hasattr(fecha, "day"):
            continue

        d["cruza_meses"] = False
        d["nota"] = ""

        if d.get("origen") == "BANCO" and fecha.day <= 3:
            d["cruza_meses"] = True
            d["nota"] = ("Movimiento del banco de los primeros días del mes: "
                         "verificá si ya está asentado en el mes anterior.")
        elif d.get("origen") == "CRM" and fecha.day >= 28:
            d["cruza_meses"] = True
            d["nota"] = ("Asiento del cierre del mes: puede que el banco lo "
                         "procese el mes siguiente.")


def _calcular_saldos_y_residuo(
    crm, banco, matches, discrepancias,
    saldo_apertura, ajustes_manuales, saldo_extracto_banco,
    ajustes_sugeridos, posibles_debitos, banco_obj,
):
    """
    Calcula el resumen contable: saldo CRM + apertura + ajustes vs extracto.

    Estructura del dict que devuelve — compatible con el sistema viejo
    y con la UI Streamlit actual.
    """
    saldo_crm = float(crm.attrs.get("saldo_crm_detectado") or 0)
    formato_banco = banco.attrs.get("formato", "desconocido")
    saldo_inicial_detectado = banco.attrs.get("saldo_inicial_detectado")
    saldo_final_detectado = banco.attrs.get("saldo_final_detectado")

    # Saldo del extracto: prioridad al valor pasado por el usuario, sino
    # el detectado del archivo, sino la suma neta como fallback.
    if saldo_extracto_banco is not None:
        saldo_extracto = float(saldo_extracto_banco)
    elif saldo_final_detectado is not None:
        saldo_extracto = float(saldo_final_detectado)
    elif saldo_inicial_detectado is not None:
        saldo_extracto = saldo_inicial_detectado + float(banco["monto"].sum())
    else:
        saldo_extracto = float(banco["monto"].sum())

    # Suma de ajustes manuales cargados en el carrito
    total_ajustes = sum(a["monto"] for a in ajustes_manuales)

    # Fórmula contable:
    #   Saldo Banco Calculado = Saldo CRM + Apertura + Ajustes
    saldo_banco_calculado = saldo_crm + saldo_apertura + total_ajustes

    # Residuo (diferencia final)
    diferencia_final = round(saldo_banco_calculado - saldo_extracto, 2)
    TOLERANCIA_CONCILIACION = 1000.0
    concilia_ok = abs(diferencia_final) < TOLERANCIA_CONCILIACION

    # Estadísticas de matching
    matches_cupones = sum(1 for m in matches if m.get("es_agrupado"))
    matches_exactos = sum(1 for m in matches if "Exacto" in m.get("tipo", ""))
    matches_tolerancia = sum(1 for m in matches if "tolerancia" in m.get("tipo", "").lower())
    matches_ia = sum(1 for m in matches if "IA" in m.get("tipo", ""))
    conciliados_alta_confianza = sum(1 for m in matches if m.get("confianza") == "Alta")
    a_revisar = sum(1 for m in matches if m.get("confianza") != "Alta")

    # === Composición de la diferencia (qué categorías la explican) ===
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

    # === Categorías de discrepancias ===
    liq_tc = sum(1 for d in discrepancias if "LIQ-TC" in d["tipo"])
    cupones_tarjeta = sum(1 for d in discrepancias if "CUPONES" in d["tipo"])
    comisiones_impuestos = sum(
        1 for d in discrepancias
        if d["tipo"] in ("COMISIÓN/IMPUESTO NO REGISTRADO",)
    )
    intereses = sum(1 for d in discrepancias if d["tipo"] == "INTERESES NO REGISTRADOS")
    faltantes_en_banco = sum(1 for d in discrepancias if d["tipo"] == "FALTANTE EN BANCO")
    faltantes_en_crm = sum(1 for d in discrepancias if d["tipo"] == "FALTANTE EN CRM")

    return {
        # Saldos
        "saldo_crm": saldo_crm,
        "saldo_apertura": saldo_apertura,
        "total_ajustes": total_ajustes,
        "ajustes_total": total_ajustes,  # alias compat
        "saldo_banco_calculado": saldo_banco_calculado,
        "saldo_extracto": saldo_extracto,
        "diferencia_final": diferencia_final,
        "concilia_ok": concilia_ok,

        # Metadata detectada
        "formato_banco": formato_banco,
        "saldo_inicial_detectado": saldo_inicial_detectado,
        "saldo_final_detectado": saldo_final_detectado,

        # Matching
        "total_crm": len(crm),
        "total_banco": len(banco),
        "matches_cupones": matches_cupones,
        "matches_exactos": matches_exactos,
        "matches_tolerancia": matches_tolerancia,
        "matches_ia": matches_ia,
        "total_matches": len(matches),
        "conciliados_alta_confianza": conciliados_alta_confianza,
        "a_revisar": a_revisar,
        "total_discrepancias": len(discrepancias),

        # Categorías de discrepancias (para gráficos de la UI)
        "liq_tc": liq_tc,
        "cupones_tarjeta": cupones_tarjeta,
        "comisiones_impuestos": comisiones_impuestos,
        "intereses": intereses,
        "faltantes_en_banco": faltantes_en_banco,
        "faltantes_en_crm": faltantes_en_crm,

        # Composición y top
        "composicion": composicion,
        "top_movimientos": top_movimientos,

        # Ajustes
        "ajustes_manuales": ajustes_manuales,
        "ajustes_sugeridos": ajustes_sugeridos,
        "sugerencias": [],  # sugerencias narrativas — vacías por ahora
        "posibles_debitos_pendientes": posibles_debitos,

        # Huérfanos del banco para el carrito
        "huerfanos_banco": huerfanos_banco_para_carrito(discrepancias),

        # Compatibilidad con código previo
        "neto_crm": saldo_crm,
        "neto_banco": saldo_extracto,
        "diferencia": diferencia_final,
        "diferencia_residual": diferencia_final,
    }
