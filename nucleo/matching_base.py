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
DIAS_TOLERANCIA_DEFAULT = 5
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


# =====================================================================
# PASADA 3: UNO CONTRA VARIOS
# =====================================================================
# Un movimiento de un lado que coincide con la suma de 2 a 4 del otro: el
# libro registra en un solo asiento lo que el banco muestra separado (dos
# pagos de tarjeta VISA, dos operaciones de fondo común), o al revés.
#
# Las reglas se eligieron midiendo sobre ocho meses reales (marzo a junio
# 2026): todos los grupos encontrados tenían una única combinación posible
# y los seis eran correctos al revisarlos uno por uno.
MAX_PARTES_GRUPO = 4
MAX_CANDIDATOS_GRUPO = 80
PARTE_MINIMA_RELATIVA = 0.01


def pasada_3_uno_contra_varios(crm, banco, matches,
                               dias_tolerancia=DIAS_TOLERANCIA_DEFAULT):
    """
    Empareja un movimiento pendiente con la suma (hasta 1 centavo) de 2 a 4
    movimientos pendientes del otro lado, del mismo signo y dentro de la
    ventana de días.

    Es deliberadamente conservadora, porque buscar sumas entre muchos
    montos encuentra coincidencias por casualidad:

      - Solo empareja si existe UNA única combinación posible. Si hay dos,
        no puede saber cuál es la correcta y no empareja.
      - Cada parte tiene que ser al menos el 1% del total, para que unos
        impuestos chicos no "formen" un asiento grande por azar.
      - Si hay demasiados candidatos, no lo intenta.

    Se ejecuta al final, sobre lo que ninguna otra pasada pudo emparejar.
    """
    next_id = len(matches)
    lados = (("crm", crm, banco), ("banco", banco, crm))

    # Primero los montos grandes: son los que más importa explicar.
    objetivos = []
    for nombre, uno, otro in lados:
        for i in uno.index:
            if uno.at[i, "estado"] == "pendiente":
                objetivos.append((abs(uno.at[i, "monto"]), nombre, i))
    objetivos.sort(reverse=True)

    for _, nombre, i in objetivos:
        uno, otro = (crm, banco) if nombre == "crm" else (banco, crm)
        if uno.at[i, "estado"] != "pendiente":
            continue
        objetivo = uno.at[i, "monto"]
        fecha = uno.at[i, "fecha"]
        if abs(objetivo) < 0.01 or fecha is None:
            continue

        candidatos = [
            j for j in otro.index
            if otro.at[j, "estado"] == "pendiente"
            and (otro.at[j, "monto"] > 0) == (objetivo > 0)
            and PARTE_MINIMA_RELATIVA * abs(objetivo) <= abs(otro.at[j, "monto"]) < abs(objetivo)
            and otro.at[j, "fecha"] is not None
            and dias_diff(otro.at[j, "fecha"], fecha) <= dias_tolerancia
        ]
        if len(candidatos) < 2 or len(candidatos) > MAX_CANDIDATOS_GRUPO:
            continue

        soluciones = _combinaciones_que_suman(
            [(j, otro.at[j, "monto"]) for j in candidatos], objetivo, hasta=2
        )
        if len(soluciones) != 1:
            continue

        partes = list(soluciones[0])
        mid = f"M{next_id:04d}"
        next_id += 1
        uno.at[i, "match_id"] = mid
        uno.at[i, "estado"] = "conciliado"
        for j in partes:
            otro.at[j, "match_id"] = mid
            otro.at[j, "estado"] = "conciliado"

        i_crm_lista, i_bco_lista = ([i], partes) if nombre == "crm" else (partes, [i])
        lado_uno = "libro" if nombre == "crm" else "banco"
        lado_varios = "banco" if nombre == "crm" else "libro"
        matches.append({
            "match_id": mid,
            "tipo": f"Uno contra varios (1 {lado_uno} ↔ {len(partes)} {lado_varios})",
            "confianza": "Alta",
            "i_crm": i_crm_lista[0],
            "i_crm_lista": i_crm_lista,
            "i_bco": i_bco_lista[0],
            "i_bco_lista": i_bco_lista,
            "diferencia_monto": 0.0,
            "diferencia_dias": max(dias_diff(otro.at[j, "fecha"], fecha) for j in partes),
        })


def _combinaciones_que_suman(items, objetivo, hasta=2):
    """
    Grupos de 2 a 4 elementos de `items` [(id, monto), ...] cuya suma
    coincide con `objetivo` con hasta 1 centavo de diferencia: las
    operaciones de fondos comunes suelen venir redondeadas así (un rescate
    de 34.999.999,99 registrado como 20.000.000 + 15.000.000). Devuelve
    como mucho `hasta` grupos: alcanza con saber si hay uno solo o más.

    Trabaja en centavos enteros para que la comparación sea exacta, y arma
    primero las sumas de a pares para combinarlas entre sí. Así, con 80
    candidatos son unas 3.000 sumas de pares en lugar de 1,6 millones de
    combinaciones de a cuatro.
    """
    objetivo_c = round(abs(objetivo) * 100)
    vals = [(i, round(abs(m) * 100)) for i, m in items]
    n = len(vals)
    encontradas = []

    def agregar(grupo):
        encontradas.append(tuple(vals[k][0] for k in grupo))
        return len(encontradas) >= hasta

    # Posiciones por valor, para buscar el complemento
    por_valor = {}
    for k, (_, v) in enumerate(vals):
        por_valor.setdefault(v, []).append(k)

    # Cada grupo tiene una sola suma, así que no puede contarse dos veces
    # entre los tres desvíos posibles.
    desvios = (0, -1, 1)

    # De a 2
    for a in range(n):
        for dv in desvios:
            for b in por_valor.get(objetivo_c + dv - vals[a][1], []):
                if b > a and agregar((a, b)):
                    return encontradas

    # De a 3
    for a in range(n):
        for b in range(a + 1, n):
            for dv in desvios:
                for c in por_valor.get(objetivo_c + dv - vals[a][1] - vals[b][1], []):
                    if c > b and agregar((a, b, c)):
                        return encontradas

    # De a 4: pares contra pares, con a < b < c < d para no contar dos veces
    pares = {}
    for c in range(n):
        for d in range(c + 1, n):
            pares.setdefault(vals[c][1] + vals[d][1], []).append((c, d))
    for a in range(n):
        for b in range(a + 1, n):
            for dv in desvios:
                for c, d in pares.get(objetivo_c + dv - vals[a][1] - vals[b][1], []):
                    if c > b and agregar((a, b, c, d)):
                        return encontradas

    return encontradas
