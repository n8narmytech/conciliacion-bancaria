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
    Si los cargos del banco suman lo mismo que el asiento agrupado del
    libro, marca a todos como compensados entre sí.
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
    suma_agrupadores = sum(d["monto"] for d in agrupadores)

    if abs(suma_gastos - suma_agrupadores) > TOLERANCIA_GRUPO:
        return

    detalle = (
        f"Compensado: {len(gastos)} cargo(s) del banco por "
        f"${abs(suma_gastos):,.2f} contra el asiento agrupado del libro."
    )
    for d in gastos + agrupadores:
        d["cobertura"] = "agrupada"
        d["cobertura_detalle"] = detalle


def _marcar_cubiertos_por_ajustes(discrepancias, ajustes_manuales):
    """
    Marca los movimientos cuyo importe coincide con un ajuste cargado.

    Se compara por valor absoluto porque el ajuste puede llevar el signo
    invertido respecto del movimiento (un cobro del libro que todavía no
    entró se neutraliza con un ajuste negativo).
    """
    if not ajustes_manuales:
        return

    disponibles = [
        (a.get("concepto", "ajuste"), abs(float(a.get("monto", 0))))
        for a in ajustes_manuales
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
