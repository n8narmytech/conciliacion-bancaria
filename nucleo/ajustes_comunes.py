"""
Ajustes que se detectan de la misma forma para cualquier banco.

Estas funciones no dependen del banco específico:
    - `posibles_debitos_pendientes_generico`: recibos del CRM huérfanos
      cuyo monto no aparece exacto en el banco (candidatos a débito pendiente).
    - `huerfanos_banco_para_carrito`: movimientos huérfanos del banco con
      una categorización heurística por descripción.

Cada banco puede usar esta base tal cual, o sobreescribir con lógica
propia si lo necesita.
"""

import pandas as pd


# =====================================================================
# CONFIGURACIÓN POR DEFECTO
# =====================================================================
# Contrapartes internas del CRM que representan transferencias entre
# cuentas propias (por lo tanto NO son débitos pendientes).
CONTRAPARTES_INTERNAS_DEFAULT = [
    "BANCO BBVA",
    "BANCO FRANCES",
    "BANCO PATAGONIA",
    "BANCO SANTANDER",
]

# Día del mes desde el cual los recibos huérfanos se consideran del
# "último día" (y por lo tanto ya cubiertos por la sugerencia automática
# de "Débitos pendientes de contabilización" de cada banco).
DIA_FIN_DE_MES_DEFAULT = 28


# =====================================================================
# POSIBLES DÉBITOS PENDIENTES
# =====================================================================
def posibles_debitos_pendientes_generico(
    crm_df,
    banco_df,
    contrapartes_internas=None,
    dia_fin_mes=DIA_FIN_DE_MES_DEFAULT,
):
    """
    Identifica recibos del CRM (cobros) cuyo monto exacto NO aparece en el
    extracto del banco en NINGÚN día del período. Esos son candidatos a
    "Débitos pendientes de contabilización" porque probablemente se
    acrediten en el banco al mes siguiente.

    Excluye los recibos del último día del mes (>= dia_fin_mes) porque
    esos ya suelen estar cubiertos por la sugerencia automática de cada
    banco.

    Parámetros
    ----------
    crm_df : DataFrame
        Movimientos del CRM ya matcheados.
    banco_df : DataFrame
        Movimientos del banco ya matcheados.
    contrapartes_internas : list[str] | None
        Nombres de bancos a excluir por ser transferencias internas.
        Si es None usa CONTRAPARTES_INTERNAS_DEFAULT.
    dia_fin_mes : int
        Día del mes a partir del cual se considera "último día".

    Devuelve
    --------
    Lista de dicts:
        [{"id", "fecha", "monto", "descripcion", "contraparte"}, ...]
    """
    if crm_df is None or banco_df is None:
        return []

    if contrapartes_internas is None:
        contrapartes_internas = CONTRAPARTES_INTERNAS_DEFAULT

    candidatos = []
    try:
        # Recibos del CRM huérfanos del matching (positivos)
        huerfanos = crm_df[crm_df["estado"] == "pendiente"].copy()
        huerfanos = huerfanos[huerfanos["monto"] > 0]

        # Filtrar solo recibos (Rc X / RcM X / Recibo)
        mask_rc = huerfanos["descripcion_orig"].astype(str).str.upper().str.contains(
            r"\bRC X\b|\bRCM X\b|\bRECIBO\b", na=False, regex=True
        )
        huerfanos = huerfanos[mask_rc]

        # Excluir movimientos internos entre cuentas propias
        contraparte_up = huerfanos["contraparte_orig"].astype(str).str.upper()
        patron_internos = "|".join(contrapartes_internas)
        mask_externos = ~contraparte_up.str.contains(patron_internos, na=False, regex=True)
        huerfanos = huerfanos[mask_externos]

        # Excluir los del último día (ya cubiertos por sugerencia automática)
        huerfanos["dia"] = pd.to_datetime(
            huerfanos["fecha"], errors="coerce"
        ).dt.day
        huerfanos = huerfanos[huerfanos["dia"] < dia_fin_mes]

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


# =====================================================================
# HUÉRFANOS DEL BANCO (PARA EL CARRITO)
# =====================================================================
def huerfanos_banco_para_carrito(discrepancias):
    """
    Formatea la lista de movimientos huérfanos del banco para mostrarlos
    en el carrito de ajustes.

    Cada movimiento incluye:
        - id único (H0001, H0002, ...)
        - fecha, monto, descripción
        - categoría sugerida (por patrones en la descripción)

    La categorización es una heurística basada en palabras clave típicas
    de extractos bancarios en Argentina (SIRCREB, IIBB, IVA, LEY 25.413,
    HABERES, PAYWAY, etc.). Cada banco puede sobreescribir esta lógica
    si necesita categorías propias.

    Devuelve
    --------
    Lista ordenada por valor absoluto descendente.
    """
    huerfanos = []
    for idx, d in enumerate(discrepancias):
        if d["origen"] != "BANCO":
            continue

        desc = d["descripcion"].upper()
        categoria = _clasificar_movimiento_banco(desc, d.get("tipo", ""))

        huerfanos.append({
            "id": f"H{idx:04d}",
            "fecha": d["fecha"],
            "monto": d["monto"],
            "descripcion": d["descripcion"][:80],
            "categoria_sugerida": categoria,
        })

    huerfanos.sort(key=lambda h: abs(h["monto"]), reverse=True)
    return huerfanos


def _clasificar_movimiento_banco(desc_upper, tipo=""):
    """
    Devuelve una categoría sugerida según patrones en la descripción.
    Usa la misma lógica que estaba en el sistema original.
    """
    if "PAYWAY" in desc_upper or "DEBIN" in desc_upper:
        return "Payway"
    if " MASTER" in desc_upper or " VISA" in desc_upper or "CUPON" in desc_upper \
            or "CUPONES" in tipo:
        return "Liq Master/Visa"
    if "SIRCREB" in desc_upper or "SIRTAC" in desc_upper \
            or "PERCEPCION" in desc_upper or "IIBB" in desc_upper:
        return "IIBB/SIRCREB"
    if "LEY 25" in desc_upper or "IMPUESTO LEY" in desc_upper or "IMP.LEY" in desc_upper:
        return "Impuesto al cheque"
    if "IVA " in desc_upper:
        return "IVA bancario"
    if "COMISION" in desc_upper or "MANTENIMIENTO" in desc_upper:
        return "Comisiones"
    if "INTERES" in desc_upper:
        return "Intereses"
    if "HABERES" in desc_upper or "SUELDO" in desc_upper:
        return "Sueldos"
    return "Otros"
