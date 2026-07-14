"""
Interfaz web para Conciliación Bancaria con lógica contable.

Flujo de tres pantallas:
1. Carga: archivos y saldos.
2. Carrito de ajustes: ver movimientos huérfanos y armar ajustes interactivamente.
3. Resultado: descargar Excel y revisar.

Uso:
    pip3 install streamlit
    python3 -m streamlit run app.py
"""

from datetime import datetime

import streamlit as st

from orquestador import ejecutar_conciliacion
from bancos import listar_bancos


st.set_page_config(
    page_title="Conciliación Bancaria",
    page_icon="📊",
    layout="centered",
)

st.markdown(
    """
    <style>
        .block-container { padding-top: 2.5rem; padding-bottom: 3rem; }
        h1 { font-weight: 600; }
        .stButton > button { font-weight: 500; }
        div[data-testid="stMetricValue"] { font-size: 1.5rem; }
        div[data-testid="stFileUploader"] { padding: 0.5rem 0; }
        .huerfano-row { padding: 0.5rem 0; border-bottom: 1px solid #333; }
    </style>
    """,
    unsafe_allow_html=True,
)


# === Estado de sesión ===
# Pantallas: "carga" -> "carrito" -> "resultado"
if "pantalla" not in st.session_state:
    st.session_state.pantalla = "carga"
if "banco_seleccionado" not in st.session_state:
    st.session_state.banco_seleccionado = None  # código del banco elegido
if "primer_calculo" not in st.session_state:
    st.session_state.primer_calculo = None  # estadísticas de la primera corrida sin ajustes
if "ajustes_carrito" not in st.session_state:
    st.session_state.ajustes_carrito = []  # ajustes armados en el carrito
if "huerfanos_seleccionados" not in st.session_state:
    st.session_state.huerfanos_seleccionados = set()  # IDs de huérfanos seleccionados
if "resultado_final" not in st.session_state:
    st.session_state.resultado_final = None
if "archivos_cargados" not in st.session_state:
    st.session_state.archivos_cargados = None  # tupla (crm, banco, sal_ap, sal_ext)


st.title("Conciliación Bancaria")
st.divider()


# ============================================================
# PANTALLA 1: CARGA DE ARCHIVOS Y SALDOS
# ============================================================
if st.session_state.pantalla == "carga":
    st.markdown("Sistema que asiste el proceso manual de conciliación. "
                "Cargá los archivos y saldos, y después armarás los ajustes "
                "seleccionando los movimientos que los componen.")

    # Paso 0: Selector de banco
    st.markdown("##### 🏦 Elegí el banco a conciliar")
    st.caption(
        "Cada banco tiene su propia lógica de matching y detección de ajustes. "
        "El sistema se adapta al formato de archivos y patrones contables de cada uno."
    )

    bancos_disponibles = listar_bancos()
    opciones = [b["nombre"] for b in bancos_disponibles]
    codigos = [b["codigo"] for b in bancos_disponibles]

    # Índice actual seleccionado (si ya había uno)
    idx_actual = 0
    if st.session_state.banco_seleccionado:
        try:
            idx_actual = codigos.index(st.session_state.banco_seleccionado)
        except ValueError:
            idx_actual = 0

    banco_elegido_nombre = st.radio(
        "Banco",
        options=opciones,
        index=idx_actual,
        horizontal=True,
        label_visibility="collapsed",
    )
    banco_elegido_codigo = codigos[opciones.index(banco_elegido_nombre)]
    st.session_state.banco_seleccionado = banco_elegido_codigo

    # Mostrar formatos esperados para el banco elegido
    banco_info = next(b for b in bancos_disponibles if b["codigo"] == banco_elegido_codigo)
    if banco_info["formatos"]:
        with st.expander(f"ℹ️ Formatos de archivo esperados para {banco_elegido_nombre}"):
            for f in banco_info["formatos"]:
                st.caption(f"• {f}")

    st.markdown("")

    # Paso 1: Archivos
    st.markdown("##### 1️⃣ Subí los archivos")
    st.caption("El reporte del CRM (libro mayor de GBP) y el extracto del banco.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Reporte CRM (GBP)**")
        archivo_crm = st.file_uploader(
            "Subir archivo del CRM",
            type=["xlsx", "xls"],
            key="upload_crm",
            label_visibility="collapsed",
        )
        if archivo_crm:
            st.success(f"✓ {archivo_crm.name}")

    with col2:
        st.markdown("**Extracto Bancario**")
        archivo_banco = st.file_uploader(
            "Subir extracto del banco",
            type=["xlsx", "xls"],
            key="upload_banco",
            label_visibility="collapsed",
        )
        if archivo_banco:
            st.success(f"✓ {archivo_banco.name}")

    st.markdown("")

    # Paso 2: Saldos
    st.markdown("##### 2️⃣ Cargá los saldos")
    st.caption(
        "Estos son **datos fijos** del período: el arrastre del mes anterior "
        "y el saldo final que reporta el banco. Si el archivo es del formato nuevo, "
        "se autodetectan al dejar los campos en cero."
    )

    col1, col2 = st.columns(2)
    with col1:
        saldo_apertura = st.number_input(
            "Saldo apertura pendiente",
            value=0.0,
            format="%.2f",
            help="Arrastre del mes anterior.",
        )
    with col2:
        saldo_extracto = st.number_input(
            "Saldo según extracto bancario",
            value=0.0,
            format="%.2f",
            help="Saldo final del extracto al cierre.",
        )

    st.markdown("")

    # Paso 3: Ajustes manuales (opcionales, los conocidos)
    st.markdown("##### 3️⃣ Ajustes manuales (opcional)")
    st.caption(
        "Si ya sabés qué ajustes vas a aplicar (Payway recurrente, etc.), "
        "cargalos acá. Si no, en la próxima pantalla vas a poder armarlos "
        "seleccionando movimientos del banco."
    )

    # Inicializar estado si no existe
    if "ajustes_iniciales" not in st.session_state:
        st.session_state.ajustes_iniciales = []

    # Mostrar ajustes existentes
    if st.session_state.ajustes_iniciales:
        for idx, aj in enumerate(st.session_state.ajustes_iniciales):
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                st.text_input(
                    f"Concepto #{idx+1}",
                    value=aj["concepto"],
                    key=f"aji_concepto_{idx}",
                    label_visibility="collapsed",
                )
            with c2:
                st.number_input(
                    f"Monto #{idx+1}",
                    value=float(aj["monto"]),
                    format="%.2f",
                    key=f"aji_monto_{idx}",
                    label_visibility="collapsed",
                )
            with c3:
                if st.button("🗑", key=f"borrar_inicial_{idx}", help="Eliminar"):
                    st.session_state.ajustes_iniciales.pop(idx)
                    st.rerun()

    if st.button("+ Agregar ajuste manual", use_container_width=False):
        st.session_state.ajustes_iniciales.append({"concepto": "", "monto": 0.0})
        st.rerun()

    st.markdown("")

    # Botón continuar
    botones_listos = bool(archivo_crm) and bool(archivo_banco)
    if st.button(
        "Continuar a la conciliación →",
        type="primary",
        disabled=not botones_listos,
        use_container_width=True,
    ):
        # Guardar archivos en estado y procesar primera vez sin ajustes
        progress_bar = st.progress(0)
        status_text = st.empty()

        def callback(etapa, mensaje, pct):
            progress_bar.progress(pct)
            status_text.markdown(f"_{mensaje}_")

        sal_ext_param = saldo_extracto if abs(saldo_extracto) > 0.01 else None

        # Capturar los ajustes iniciales ingresados manualmente
        ajustes_iniciales_finales = []
        for idx, aj in enumerate(st.session_state.ajustes_iniciales):
            concepto = st.session_state.get(f"aji_concepto_{idx}", aj["concepto"])
            monto = st.session_state.get(f"aji_monto_{idx}", aj["monto"])
            if concepto.strip() and abs(float(monto)) > 0.01:
                ajustes_iniciales_finales.append({
                    "concepto": concepto.strip(),
                    "monto": float(monto),
                    "cantidad_mov": 0,
                    "huerfanos_ids": [],
                })

        resultado = ejecutar_conciliacion(
            st.session_state.banco_seleccionado,
            archivo_crm,
            archivo_banco,
            callback_progreso=callback,
            saldo_apertura=saldo_apertura,
            ajustes_manuales=[],  # Primera corrida sin ajustes para ver diferencia bruta
            saldo_extracto_banco=sal_ext_param,
        )

        if resultado["error"]:
            st.error(f"Error al procesar: {resultado['error']}")
        else:
            st.session_state.primer_calculo = resultado["estadisticas"]
            st.session_state.archivos_cargados = {
                "crm": archivo_crm,
                "banco": archivo_banco,
                "saldo_apertura": saldo_apertura,
                "saldo_extracto": sal_ext_param,
            }
            # Iniciar el carrito con los ajustes manuales que ya cargó
            st.session_state.ajustes_carrito = ajustes_iniciales_finales
            st.session_state.pantalla = "carrito"
            st.rerun()

    if not botones_listos:
        st.caption("Cargá ambos archivos para continuar.")


# ============================================================
# PANTALLA 2: CARRITO DE AJUSTES (simplificado)
# ============================================================
elif st.session_state.pantalla == "carrito":
    stats_base = st.session_state.primer_calculo

    # Calcular diferencia actual considerando los ajustes del carrito
    suma_ajustes_carrito = sum(a["monto"] for a in st.session_state.ajustes_carrito)
    diferencia_inicial = stats_base["diferencia_final"]
    diferencia_actual = round(diferencia_inicial - suma_ajustes_carrito, 2)
    concilia_ahora = abs(diferencia_actual) < 1000

    # === Banner de estado ===
    if concilia_ahora:
        st.success(f"### ✓ La conciliación cierra")
        st.markdown(f"Diferencia residual: **\\$ {diferencia_actual:,.2f}** (dentro de la tolerancia).")
    else:
        st.warning(f"### ⚙️ Armando ajustes")
        st.markdown(
            f"Diferencia inicial: **\\$ {diferencia_inicial:,.2f}**  \n"
            f"Diferencia residual (con {len(st.session_state.ajustes_carrito)} ajustes): "
            f"**\\$ {diferencia_actual:,.2f}**"
        )

    # Cálculo paso a paso resumido
    with st.expander("Ver cálculo paso a paso", expanded=False):
        tabla = (
            f"| Concepto | Monto |\n"
            f"|---|---:|\n"
            f"| Saldo según GBP | $ {stats_base['saldo_crm']:,.2f} |\n"
            f"| + Saldo apertura pendiente | $ {stats_base['saldo_apertura']:,.2f} |\n"
        )
        for aj in st.session_state.ajustes_carrito:
            signo = "+" if aj['monto'] >= 0 else ""
            tabla += f"| {signo} {aj['concepto']} | $ {aj['monto']:,.2f} |\n"
        tabla += (
            f"| **= Saldo banco calculado** | **$ {stats_base['saldo_crm'] + stats_base['saldo_apertura'] + suma_ajustes_carrito:,.2f}** |\n"
            f"| Saldo extracto | $ {stats_base['saldo_extracto']:,.2f} |\n"
            f"| **Diferencia residual** | **$ {diferencia_actual:,.2f}** |"
        )
        st.markdown(tabla)

    st.divider()

    # === SUGERENCIAS AUTOMÁTICAS ===
    sugerencias_auto = stats_base.get("ajustes_sugeridos", [])
    # Filtrar las que ya están en el carrito (por concepto)
    conceptos_carrito = {a["concepto"].lower() for a in st.session_state.ajustes_carrito}
    sugerencias_pendientes = [s for s in sugerencias_auto if s["concepto"].lower() not in conceptos_carrito]

    if sugerencias_pendientes:
        st.markdown("##### 🤖 Sugerencias automáticas")
        st.caption(
            "El sistema detectó estos ajustes con confianza analizando los archivos. "
            "Revisalos y agregalos con un click si corresponden."
        )

        for idx, sug in enumerate(sugerencias_pendientes):
            with st.container():
                col1, col2 = st.columns([4, 2])
                col1.markdown(f"**{sug['concepto']}**")
                col1.caption(sug.get("explicacion", ""))
                col2.markdown(f"**$ {sug['monto']:,.2f}**")
                col2.caption(f"{sug['cantidad_mov']} movimientos")

                bc1, bc2 = st.columns([1, 1])
                if bc1.button(f"➕ Agregar al carrito", key=f"add_sug_{idx}", type="primary"):
                    st.session_state.ajustes_carrito.append({
                        "concepto": sug["concepto"],
                        "monto": sug["monto"],
                        "cantidad_mov": sug["cantidad_mov"],
                        "huerfanos_ids": [],
                    })
                    st.rerun()
                if bc2.button(f"⏭ Ignorar", key=f"skip_sug_{idx}"):
                    # Marcar como ignorada para esta sesión
                    if "sugerencias_ignoradas" not in st.session_state:
                        st.session_state.sugerencias_ignoradas = set()
                    st.session_state.sugerencias_ignoradas.add(sug["concepto"].lower())
                    st.rerun()

                st.markdown("")
        st.divider()

    # === POSIBLES DÉBITOS PENDIENTES (revisión manual) ===
    posibles = stats_base.get("posibles_debitos_pendientes", [])
    # Filtrar los que ya fueron agregados al carrito en sesiones anteriores
    ids_en_carrito = set()
    for aj in st.session_state.ajustes_carrito:
        for hid in aj.get("debitos_ids", []):
            ids_en_carrito.add(hid)
    posibles_pendientes = [p for p in posibles if p["id"] not in ids_en_carrito]

    if posibles_pendientes:
        st.markdown(f"##### 🔍 Posibles débitos pendientes ({len(posibles_pendientes)})")
        st.caption(
            "Estos son recibos del CRM cuyo monto **no aparece exacto** en el extracto "
            "del banco. Pueden ser débitos pendientes reales (recibos que se acreditarán "
            "el mes siguiente) o matches perdidos por errores de fecha/monto. "
            "**Revisalos uno por uno y marcá los que correspondan.**"
        )

        # Contador para generar keys nuevas tras cada agregado (evita conflicto al resetear)
        gen = st.session_state.get("pos_deb_gen", 0)

        # Separar entre recibos (signo invertido) y asientos "- Proveedores" (signo original)
        # Los IDs con prefijo "crm_prov_" son asientos "- Proveedores" (no invertir signo).
        # Los que empiezan con "crm_" (sin "_prov_") son recibos del CRM (invertir signo).
        seleccionados_ids = []
        seleccionados_recibos = []  # ids que invierten signo
        seleccionados_prov = []     # ids que NO invierten signo
        for p in posibles_pendientes:
            fecha_str = p["fecha"].strftime("%d/%m") if hasattr(p["fecha"], "strftime") else str(p["fecha"])
            etiqueta = (
                f"**{fecha_str}** | $ {p['monto']:,.2f} | "
                f"_{p['descripcion'][:50]}_  →  {p['contraparte'][:35]}"
            )
            checked = st.checkbox(etiqueta, key=f"pos_deb_{gen}_{p['id']}")
            if checked:
                seleccionados_ids.append(p["id"])
                if p["id"].startswith("crm_prov_"):
                    seleccionados_prov.append(p)
                else:
                    seleccionados_recibos.append(p)

        if seleccionados_ids:
            # Calcular montos por tipo
            monto_recibos = sum(p["monto"] for p in seleccionados_recibos)
            monto_prov = sum(p["monto"] for p in seleccionados_prov)

            # Recibos: signo invertido (son cobros del CRM que el banco no procesó)
            # "- Proveedores": signo original (son gastos ya cargados en CRM,
            # se suman como ajuste tal como están)
            monto_total_ajuste = -monto_recibos + monto_prov

            # Mensaje descriptivo según los tipos seleccionados
            partes = []
            if seleccionados_recibos:
                partes.append(
                    f"{len(seleccionados_recibos)} recibo(s) del CRM → ajuste "
                    f"${-monto_recibos:,.2f} (signo invertido)"
                )
            if seleccionados_prov:
                partes.append(
                    f"{len(seleccionados_prov)} asiento(s) '- Proveedores' → ajuste "
                    f"${monto_prov:,.2f} (signo original)"
                )
            detalle = "  \n".join(partes)

            st.markdown(
                f"**{len(seleccionados_ids)} seleccionado(s):**  \n"
                f"{detalle}  \n"
                f"**Total a cargar:** \\$ {monto_total_ajuste:,.2f}"
            )
            if st.button(
                "➕ Agregar como 'Ajustes (revisión manual)'",
                type="primary",
                key=f"agregar_posibles_deb_{gen}"
            ):
                st.session_state.ajustes_carrito.append({
                    "concepto": "Ajustes (revisión manual)",
                    "monto": monto_total_ajuste,
                    "cantidad_mov": len(seleccionados_ids),
                    "huerfanos_ids": [],
                    "debitos_ids": seleccionados_ids,
                })
                # Incrementar generación para que en el próximo render las
                # nuevas keys de los checkboxes empiecen vacías
                st.session_state.pos_deb_gen = gen + 1
                st.rerun()

        st.divider()

    # === AJUSTES EN EL CARRITO ===
    st.markdown(f"##### 🛒 Ajustes en el carrito ({len(st.session_state.ajustes_carrito)})")

    if st.session_state.ajustes_carrito:
        for idx, aj in enumerate(st.session_state.ajustes_carrito):
            col1, col2, col3, col4 = st.columns([4, 2, 1, 1])
            col1.write(f"**{aj['concepto']}**")
            col2.write(f"$ {aj['monto']:,.2f}")
            if col3.button("✏️", key=f"edit_aj_{idx}", help="Editar monto"):
                st.session_state[f"editando_aj_{idx}"] = True
            if col4.button("🗑", key=f"del_aj_{idx}", help="Eliminar ajuste"):
                st.session_state.ajustes_carrito.pop(idx)
                st.rerun()

            # Si está en modo edición
            if st.session_state.get(f"editando_aj_{idx}"):
                nuevo_monto = st.number_input(
                    "Nuevo monto",
                    value=float(aj['monto']),
                    format="%.2f",
                    key=f"nuevo_monto_{idx}",
                )
                col_ok, col_cancel = st.columns(2)
                if col_ok.button("Guardar", key=f"save_{idx}"):
                    st.session_state.ajustes_carrito[idx]["monto"] = float(nuevo_monto)
                    st.session_state[f"editando_aj_{idx}"] = False
                    st.rerun()
                if col_cancel.button("Cancelar", key=f"cancel_{idx}"):
                    st.session_state[f"editando_aj_{idx}"] = False
                    st.rerun()
    else:
        st.caption("_El carrito está vacío. Agregá ajustes desde las sugerencias o manualmente._")

    st.divider()

    # === AGREGAR AJUSTE MANUAL ===
    st.markdown("##### ➕ Agregar ajuste manual")
    st.caption(
        "Para ajustes que el sistema no detecta automáticamente (Payway, débitos pendientes, "
        "ajustes contables específicos, etc.). Cargalos con tu criterio."
    )

    with st.form("ajuste_manual_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 2])
        with c1:
            concepto_manual = st.text_input(
                "Concepto",
                placeholder="Ej: Payway pendiente de devolución",
            )
        with c2:
            monto_manual = st.number_input("Monto", value=0.0, format="%.2f")
        if st.form_submit_button("Agregar ajuste"):
            if concepto_manual.strip() and abs(monto_manual) > 0.01:
                st.session_state.ajustes_carrito.append({
                    "concepto": concepto_manual.strip(),
                    "monto": float(monto_manual),
                    "cantidad_mov": 0,
                    "huerfanos_ids": [],
                })
                st.rerun()
            elif not concepto_manual.strip():
                st.error("Tenés que ponerle un concepto al ajuste.")

    st.divider()

    # === Navegación ===
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Volver a inicio", use_container_width=True):
            for key in ["pantalla", "primer_calculo", "ajustes_carrito",
                        "ajustes_iniciales", "huerfanos_seleccionados",
                        "resultado_final", "archivos_cargados", "sugerencias_ignoradas"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
    with col2:
        if st.button(
            "Finalizar y generar reporte →",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Generando reporte final..."):
                archivos = st.session_state.archivos_cargados
                resultado = ejecutar_conciliacion(
                    st.session_state.banco_seleccionado,
                    archivos["crm"],
                    archivos["banco"],
                    saldo_apertura=archivos["saldo_apertura"],
                    ajustes_manuales=st.session_state.ajustes_carrito,
                    saldo_extracto_banco=archivos["saldo_extracto"],
                )
                st.session_state.resultado_final = resultado
                st.session_state.pantalla = "resultado"
                st.rerun()


# ============================================================
# PANTALLA 3: RESULTADO FINAL
# ============================================================
elif st.session_state.pantalla == "resultado":
    resultado = st.session_state.resultado_final

    if resultado["error"]:
        st.error(f"Error al generar reporte: {resultado['error']}")
        if st.button("Volver al carrito"):
            st.session_state.pantalla = "carrito"
            st.rerun()
    else:
        stats = resultado["estadisticas"]

        # Banner de estado
        if stats["concilia_ok"]:
            st.success("### ✓ Conciliación cerrada")
            st.markdown(f"Diferencia final: **$ {stats['diferencia_final']:,.2f}**")
        else:
            st.warning("### ⚠ Quedó diferencia residual")
            st.markdown(
                f"Diferencia final: **$ {stats['diferencia_final']:,.2f}**  \n"
                f"_Podés volver al carrito a agregar más ajustes si querés cerrarla mejor._"
            )

        st.markdown("")

        # Cálculo final
        st.markdown("##### Cálculo final")
        st.markdown(f"""
        | Concepto | Monto |
        |---|---:|
        | Saldo según GBP | $ {stats['saldo_crm']:,.2f} |
        | + Saldo apertura pendiente | $ {stats['saldo_apertura']:,.2f} |
        """)

        # Listar ajustes
        for aj in stats.get("ajustes_manuales", []):
            signo = "+" if aj['monto'] >= 0 else ""
            st.markdown(f"| {signo} {aj['concepto']} | $ {aj['monto']:,.2f} |")

        st.markdown(f"""
        | **= Saldo banco calculado** | **$ {stats['saldo_banco_calculado']:,.2f}** |
        | Saldo según extracto | $ {stats['saldo_extracto']:,.2f} |
        | **DIFERENCIA FINAL** | **$ {stats['diferencia_final']:,.2f}** |
        """)

        st.divider()

        # Acciones
        nombre_archivo = f"conciliacion_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "📥 Descargar Excel",
                data=resultado["excel_bytes"],
                file_name=nombre_archivo,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )
        with c2:
            if st.button("← Volver al carrito", use_container_width=True):
                st.session_state.pantalla = "carrito"
                st.rerun()
        with c3:
            if st.button("Empezar otra conciliación", use_container_width=True):
                # Reset completo
                for key in ["pantalla", "primer_calculo", "ajustes_carrito",
                            "ajustes_iniciales", "huerfanos_seleccionados",
                            "resultado_final", "archivos_cargados"]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
