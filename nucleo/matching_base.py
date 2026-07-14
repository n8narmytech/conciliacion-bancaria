"""
Pasadas de matching genéricas — funcionan igual para cualquier banco.

Estas pasadas se ejecutan DESPUÉS de las pasadas específicas del banco
(que están en cada `bancos/<banco>.py`). Su función es matchear los
movimientos "normales" que no requieren lógica especial.

Pasadas incluidas:
    - Pasada 1: match exacto por referencia + monto + fecha
    - Pasada 2: match con tolerancia (fecha ± X días, monto ± Y%)

La pasada 0 (matching agrupado, ej. cupones VISA/MASTER) es específica
de cada banco y vive en su propio módulo.
"""

# =====================================================================
# CONFIGURACIÓN POR DEFECTO
# =====================================================================
# Estos valores se usan como default si el llamador no pasa parámetros.
# Cualquier banco puede llamar a las pasadas con tolerancias distintas
# si necesita ser más estricto o más laxo.
DIAS_TOLERANCIA_DEFAULT = 2
MONTO_TOLERANCIA_ABS_DEFAULT = 1.0
MONTO_TOLERANCIA_PCT_DEFAULT = 0.1


# =====================================================================
# HELPERS
# =====================================================================
def dias_diff(f1, f2):
    """Diferencia absoluta en días entre dos fechas."""
    return abs((f1 - f2).days)


def montos_iguales(m1, m2, tolerancia_abs=MONTO_TOLERANCIA_ABS_DEFAULT,
                   tolerancia_pct=MONTO_TOLERANCIA_PCT_DEFAULT):
    """
    Compara dos montos con tolerancia. Devuelve True si son "iguales" según:
        - tolerancia_abs: diferencia absoluta máxima (en pesos)
        - tolerancia_pct: diferencia porcentual máxima
    """
    diff = abs(m1 - m2)
    if diff <= tolerancia_abs:
        return True
    if max(abs(m1), abs(m2)) > 0:
        pct = diff / max(abs(m1), abs(m2)) * 100
        if pct <= tolerancia_pct:
            return True
    return False


# =====================================================================
# PASADAS DE MATCHING
# =====================================================================
def pasada_1_match_exacto(crm, banco, matches):
    """
    Match por referencia + monto exacto + fecha exacta.

    Requiere que ambos lados tengan una referencia normalizada NO vacía.
    Si el CRM no expone referencia (como es habitual en GBP), esta pasada
    no matchea nada — es la pasada 2 la que hace el trabajo real.
    """
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


def pasada_2_match_tolerancia(crm, banco, matches,
                              dias_tolerancia=DIAS_TOLERANCIA_DEFAULT,
                              monto_tolerancia_abs=MONTO_TOLERANCIA_ABS_DEFAULT,
                              monto_tolerancia_pct=MONTO_TOLERANCIA_PCT_DEFAULT):
    """
    Match con tolerancia de fecha y monto.

    Para cada movimiento del CRM pendiente, busca el mejor candidato en
    el banco pendiente que:
        - Tenga el mismo signo (ambos ingresos o ambos egresos)
        - Esté dentro de `dias_tolerancia` días
        - Tenga monto dentro de tolerancia (absoluta o porcentual)

    Elige el mejor por score: prioriza menor diferencia de días y menor
    diferencia porcentual de monto. Si hay match de referencia, gana.
    """
    next_id = len(matches)
    for i_crm, fila_crm in crm.iterrows():
        if fila_crm["estado"] != "pendiente":
            continue

        candidatos = []
        for i_bco, fila_bco in banco.iterrows():
            if fila_bco["estado"] != "pendiente":
                continue
            # Mismo signo
            if (fila_crm["monto"] > 0) != (fila_bco["monto"] > 0):
                continue

            d_dias = dias_diff(fila_crm["fecha"], fila_bco["fecha"])
            if d_dias > dias_tolerancia:
                continue

            d_monto = abs(fila_crm["monto"] - fila_bco["monto"])
            monto_ok = montos_iguales(
                fila_crm["monto"], fila_bco["monto"],
                tolerancia_abs=monto_tolerancia_abs,
                tolerancia_pct=monto_tolerancia_pct,
            )

            ref_match = (
                fila_crm["referencia_norm"]
                and fila_crm["referencia_norm"] == fila_bco["referencia_norm"]
            )

            # Score: cuanto menor, mejor
            score = d_dias * 10 + d_monto / max(abs(fila_crm["monto"]), 1) * 100
            if ref_match:
                score -= 1000  # referencia coincidente es señal muy fuerte

            if monto_ok or ref_match:
                candidatos.append((score, i_bco, d_dias, d_monto, ref_match, monto_ok))

        if not candidatos:
            continue

        candidatos.sort(key=lambda x: x[0])
        score, i_bco, d_dias, d_monto, ref_match, monto_ok = candidatos[0]

        if ref_match and monto_ok and d_dias <= dias_tolerancia:
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
