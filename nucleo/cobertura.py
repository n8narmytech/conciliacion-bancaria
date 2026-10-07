"""
Clasificación de los movimientos que quedaron sin pareja individual.

Que un movimiento no tenga pareja uno a uno no significa que esté sin
justificar. Los dos casos más comunes:

  - El banco cobra decenas de impuestos y comisiones durante el mes y el
    libro los registra en un solo asiento de cierre ("- Proveedores"). No
    hay forma de emparejarlos uno a uno, pero los dos lados suman lo
    mismo y el saldo cierra.

  - Una partida pendiente se declara como ajuste en la conciliación
    (Payway, un débito sin contabilizar). El movimiento queda suelto en
    el listado, pero su explicación está en el ajuste.

Este módulo separa esos casos de lo que realmente quedó sin explicación,
que es lo único que hay que investigar cuando la conciliación no cierra.
"""

# Tolerancia para dar por compensado un grupo contra su asiento agrupado.
TOLERANCIA_GRUPO = 1.0

# Cómo se llama en el libro el asiento que agrupa los gastos del banco.
CONCEPTOS_AGRUPADORES = ("- Proveedores",)

# Tipos de discrepancia que corresponden a cargos del banco (impuestos,
# comisiones, percepciones) que el libro absorbe en el asiento agrupado.
TIPOS_GASTO_BANCARIO = (
    "COMISIÓN/IMPUESTO NO REGISTRADO",
    "INTERESES NO REGISTRADOS",
)


def clasificar_cobertura(discrepancias, ajustes_manuales=None, banco_obj=None):
    """
    Marca cada discrepancia según cómo queda cubierta y devuelve un resumen.

    A cada movimiento le agrega:
        cobertura         "agrupada" | "ajuste" | "ninguna"
        cobertura_detalle  texto que explica por qué está cubierto

    Devuelve un dict con los totales de cada grupo, para que la interfaz
    pueda mostrar por separado lo que está explicado de lo que no.
    """
    ajustes_manuales = ajustes_manuales or []

    for d in discrepancias:
        d["cobertura"] = "ninguna"
        d["cobertura_detalle"] = ""

    _marcar_grupo_gastos(discrepancias, banco_obj)
    _marcar_cubiertos_por_ajustes(discrepancias, ajustes_manuales)

    agrupados = [d for d in discrepancias if d["cobertura"] == "agrupada"]
    ajustados = [d for d in discrepancias if d["cobertura"] == "ajuste"]
    sin_cubrir = [d for d in discrepancias if d["cobertura"] == "ninguna"]

    return {
        "cubiertos_agrupados": len(agrupados),
        "cubiertos_agrupados_monto": round(sum(d["monto"] for d in agrupados), 2),
        "cubiertos_por_ajuste": len(ajustados),
        "cubiertos_por_ajuste_monto": round(sum(d["monto"] for d in ajustados), 2),
        "sin_justificar": len(sin_cubrir),
        "sin_justificar_monto": round(sum(d["monto"] for d in sin_cubrir), 2),
    }


def _es_gasto_bancario(discrepancia, banco_obj):
    """
    Indica si el movimiento es un cargo del banco de los que el libro
    agrupa al cierre. Se apoya en la clasificación que ya hizo el sistema
    y, si el perfil del banco expone su propio criterio, también en ese.
    """
    if discrepancia.get("origen") != "BANCO":
        return False
    if discrepancia.get("tipo") in TIPOS_GASTO_BANCARIO:
        return True
    if banco_obj is not None and hasattr(banco_obj, "es_gasto_bancario"):
        try:
            return bool(banco_obj.es_gasto_bancario(discrepancia.get("descripcion", "")))
        except Exception:
            return False
    return False


def _marcar_grupo_gastos(discrepancias, banco_obj):
    """
    Compensa los cargos del banco contra el asiento que los agrupa en el
    libro ("- Proveedores").

    Que las dos sumas coincidan al centavo es el caso ideal, pero en la
    práctica aparecen tres desvíos y ninguno debería impedir reconocer el
    grupo:

      - Sobra un asiento en el libro: hay más de un "- Proveedores" y uno
        es de otro mes. Se usa la combinación de asientos que mejor
        coincide con los cargos.
      - Sobra un cargo en el banco: una comisión que el libro registró
        aparte del asiento agrupado. Se lo aparta y queda sin justificar,
        con una nota que dice por qué.
      - Queda un residuo chico, repartido en el redondeo de cientos de
        cargos. Se compensa el grupo y el residuo se muestra como una
        sola línea sin justificar, en vez de dejar todos los cargos
        sueltos.

    Si después de eso el residuo sigue por encima del umbral, el grupo no
    se compensa: es una diferencia real y tiene que verse completa.
    """
    gastos = [d for d in discrepancias if _es_gasto_bancario(d, banco_obj)]
    agrupadores = [
        d for d in discrepancias
        if d.get("origen") == "CRM"
        and any(c in str(d.get("descripcion", "")) for c in CONCEPTOS_AGRUPADORES)
    ]
    if not gastos or not agrupadores:
        return

    suma_gastos = sum(d["monto"] for d in gastos)

    # 1. Qué asientos agrupadores corresponden a estos cargos
    usados = _mejor_combinacion(agrupadores, suma_gastos)
    suma_asientos = sum(d["monto"] for d in usados)
    residuo = suma_gastos - suma_asientos

    # 2. Si no alcanza, apartar el cargo que sobra
    apartado = None
    if abs(residuo) > _umbral(suma_asientos):
        candidato = min(gastos, key=lambda d: abs(residuo - d["monto"]))
        if abs(residuo - candidato["monto"]) <= _umbral(suma_asientos):
            apartado = candidato
            residuo -= candidato["monto"]

    # 3. ¿Se reconoce el grupo?
    if abs(residuo) > _umbral(suma_asientos):
        return

    compensados = [d for d in gastos if d is not apartado]
    detalle = (
        f"Compensado: {len(compensados)} cargo(s) del banco por "
        f"${abs(sum(d['monto'] for d in compensados)):,.2f} contra el asiento "
        f"agrupado del libro."
    )
    if abs(residuo) > 0.01:
        detalle += f" Entre ambos quedan ${residuo:,.2f} de diferencia, que se muestran aparte."
    for d in compensados + usados:
        d["cobertura"] = "agrupada"
        d["cobertura_detalle"] = detalle

    if apartado is not None:
        apartado["cobertura_detalle"] = (
            "No forma parte del asiento agrupado de gastos: el libro no lo "
            "incluyó ahí. Verificá si está contabilizado por separado."
        )

    for d in agrupadores:
        if d not in usados:
            d["cobertura_detalle"] = (
                "Asiento agrupado que no corresponde a los cargos de este mes: "
                "verificá si es del mes anterior o posterior."
            )

    if abs(residuo) > 0.01:
        referencia = usados[-1]
        discrepancias.append({
            "tipo": "DIFERENCIA DE GRUPO",
            "origen": "GRUPO",
            "fila": None,
            "fecha": referencia.get("fecha"),
            "monto": round(residuo, 2),
            "descripcion": "Diferencia entre los cargos del banco y el asiento agrupado de gastos",
            "contraparte": "",
            "cobertura": "ninguna",
            "cobertura_detalle": (
                f"Los cargos de impuestos y comisiones del banco suman "
                f"${abs(suma_gastos - (apartado['monto'] if apartado else 0)):,.2f} "
                f"y el asiento agrupado ${abs(suma_asientos):,.2f}. "
                f"La diferencia suele ser redondeo o un cargo clasificado distinto."
            ),
        })


# Margen para reconocer el grupo: el residuo tiene que ser menor al 1% del
# asiento. Los residuos reales observados están entre 0,02% y 0,06%; una
# diferencia mayor no es redondeo y se deja visible completa.
UMBRAL_GRUPO_RELATIVO = 0.01


def _umbral(suma_asientos):
    return max(TOLERANCIA_GRUPO, abs(suma_asientos) * UMBRAL_GRUPO_RELATIVO)


def _mejor_combinacion(agrupadores, objetivo):
    """
    De los asientos agrupadores, la combinación cuya suma más se acerca a la
    de los cargos. Casi siempre hay uno o dos, así que se prueban todas.
    """
    from itertools import combinations

    candidatos = agrupadores[:8]
    mejor, mejor_dif = list(candidatos), None
    for n in range(1, len(candidatos) + 1):
        for combo in combinations(candidatos, n):
            dif = abs(objetivo - sum(d["monto"] for d in combo))
            if mejor_dif is None or dif < mejor_dif:
                mejor, mejor_dif = list(combo), dif
    return mejor


def _marcar_cubiertos_por_ajustes(discrepancias, ajustes_manuales):
    """
    Marca los movimientos que quedan explicados por un ajuste cargado.

    Dos formas, en este orden:

      1. El ajuste trae la lista de movimientos que lo componen. Es el caso
         de los que sugiere el sistema: el de gastos bancarios de Galicia,
         por ejemplo, suma decenas de cargos, y sin la lista no habría
         forma de saber cuáles cubre.
      2. Si no la trae (un ajuste cargado a mano), se busca un movimiento
         con el mismo importe. Se compara por valor absoluto porque el
         ajuste puede llevar el signo invertido respecto del movimiento:
         un cobro del libro que todavía no entró se neutraliza con un
         ajuste negativo.
    """
    if not ajustes_manuales:
        return

    por_clave = {}
    for d in discrepancias:
        if d.get("fila") is not None:
            por_clave[(d.get("origen"), _clave_fila(d.get("fila")))] = d

    sin_lista = []
    for a in ajustes_manuales:
        concepto = a.get("concepto", "ajuste")
        movimientos = a.get("movimientos") or []
        marcados = 0
        for m in movimientos:
            d = por_clave.get((m.get("origen"), _clave_fila(m.get("fila"))))
            if d is not None and d["cobertura"] == "ninguna":
                d["cobertura"] = "ajuste"
                marcados += 1
        if marcados:
            detalle = f"Incluido en el ajuste «{concepto}» ({len(movimientos)} movimiento(s))."
            for m in movimientos:
                d = por_clave.get((m.get("origen"), _clave_fila(m.get("fila"))))
                if d is not None and d["cobertura"] == "ajuste" and not d["cobertura_detalle"]:
                    d["cobertura_detalle"] = detalle
        else:
            sin_lista.append(a)

    disponibles = [
        (a.get("concepto", "ajuste"), abs(float(a.get("monto", 0))))
        for a in sin_lista
        if abs(float(a.get("monto", 0))) > 0.01
    ]
    usados = set()

    for d in discrepancias:
        if d["cobertura"] != "ninguna":
            continue
        monto = abs(float(d.get("monto", 0)))
        for i, (concepto, valor) in enumerate(disponibles):
            if i in usados:
                continue
            if abs(valor - monto) < 0.01:
                d["cobertura"] = "ajuste"
                d["cobertura_detalle"] = f"Declarado en el ajuste «{concepto}»."
                usados.add(i)
                break


def _clave_fila(fila):
    """Normaliza el número de fila: puede llegar como int, float o texto
    después de pasar por la interfaz."""
    try:
        return int(float(fila))
    except (TypeError, ValueError):
        return fila


