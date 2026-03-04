import sys, warnings
sys.path.insert(0, '.')
warnings.filterwarnings('ignore')

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import time
from datetime import date

from src.cenace.cenace_client import get_demanda_total_sistema, validate_demand_data
from src.model.pypsa_model import correr_despacho_completo, cargar_capacidades
from src.scenarios.scenarios import ESCENARIOS, aplicar_escenario, listar_escenarios
from src.utils.download_vre import descargar_perfil_vre

st.set_page_config(page_title="Despacho Económico México", page_icon="⚡", layout="wide")

# Colores y orden por tipo de tecnología
COLORES = {
    "Nuclear": "#2c3e50", "Geotermia": "#6c3483",
    "Carbon": "#7b3f00", "Cogeneracion": "#d35400",
    "CCGT_Gas": "#4e79a7", "Fuel_Oil": "#f28e2b",
    "Turbogas": "#e67e22", "Diesel_Peaker": "#e15759",
    "Hidro": "#1a7a4a",
    "Solar": "#edc948", "Eolica": "#76b7b2",
    "Bateria": "#b07aa1",
}

ORDEN_STACK = [
    "Nuclear","Geotermia",
    "Carbon","Cogeneracion","CCGT_Gas","Fuel_Oil","Turbogas","Diesel_Peaker",
    "Hidro",
    "Eolica","Solar","Bateria",
]

# Emisiones CO2 kg/MWh (factor de emisión por tecnología)
CO2_FACTOR = {
    "Nuclear":0, "Geotermia":50, "Carbon":820, "Cogeneracion":400,
    "CCGT_Gas":370, "Fuel_Oil":650, "Turbogas":500, "Diesel_Peaker":700,
    "Hidro":4, "Solar":0, "Eolica":0, "Bateria":0,
}

SITIOS_VRE = {
    "BCS": {"Solar":{"lat":24.1,"lon":-110.3}, "Eolica":{"lat":24.8,"lon":-111.9}},
    "BCA": {"Solar":{"lat":32.5,"lon":-115.5}, "Eolica":{"lat":31.8,"lon":-116.6}},
    "SIN": {"Solar":{"lat":29.1,"lon":-110.9}, "Eolica":{"lat":16.5,"lon":-95.0}},
}

# ── SIDEBAR ───────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ Despacho Económico")
    st.caption("Sistemas Aislados de México")
    st.divider()

    st.subheader("📅 Periodo")
    fecha_ini = st.date_input("Inicio", value=date(2024,6,1),
                               min_value=date(2023,1,1), max_value=date(2024,12,31))
    fecha_fin = st.date_input("Fin",    value=date(2024,6,7),
                               min_value=date(2023,1,1), max_value=date(2024,12,31))

    st.subheader("🗺️ Sistemas")
    c1,c2,c3 = st.columns(3)
    sistemas = [s for s,ok in [
        ("BCS", c1.checkbox("BCS", True)),
        ("BCA", c2.checkbox("BCA", True)),
        ("SIN", c3.checkbox("SIN", True))
    ] if ok]

    st.subheader("🌞 Perfiles VRE")
    usar_vre_real = st.toggle("Datos reales (Renewables.ninja)", value=False)
    token_ninja = ""
    if usar_vre_real:
        token_ninja = st.text_input("Token API", type="password",
                                     placeholder="Tu token de renewables.ninja")
        if token_ninja:
            st.success("Token listo ✅")

    st.subheader("📋 Escenario")
    opciones_esc = [("base","🔵 Base")] + listar_escenarios()
    clave_esc = st.selectbox("Selecciona",
        options=[c for c,_ in opciones_esc],
        format_func=lambda c: dict(opciones_esc)[c])
    if clave_esc != "base" and clave_esc in ESCENARIOS:
        st.info(ESCENARIOS[clave_esc]["descripcion"])

    with st.expander("🔧 Costos manuales (USD/MWh)"):
        costo_gas    = st.slider("CCGT Gas",      10, 200, 45)
        costo_carbon = st.slider("Carbón",        20, 200, 60)
        costo_oil    = st.slider("Fuel Oil",      50, 300, 100)
        costo_diesel = st.slider("Diesel Peaker", 50, 400, 150)
        usar_sliders = st.checkbox("Aplicar estos costos")

    voll = st.slider("💸 VOLL (USD/MWh)", 1000, 15000, 5000, step=500)
    correr = st.button("▶️ CORRER DESPACHO", type="primary", use_container_width=True)

# ── TABS ──────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏠 Inicio", "📊 Demanda", "⚙️ Despacho", "🔀 Escenarios", "📖 Metodología"
])

# ════════════════════════════════════════════════════════════════
# TAB 1: INICIO
# ════════════════════════════════════════════════════════════════
with tab1:
    st.title("⚡ Simulador de Despacho Económico")
    st.markdown("**Sistemas Aislados de México: BCS · BCA · SIN**")
    st.markdown("Datos reales **CENACE** · Optimización **PyPSA + HiGHS** · VRE **Renewables.ninja** · 11 tecnologías")
    st.divider()

    c1,c2,c3 = st.columns(3)
    c1.metric("🌞 BCS — Baja California Sur","~533 MW pico","Geotermia + Diesel + Solar")
    c2.metric("💨 BCA — Baja California Norte","~2,901 MW pico","Gas + Renovable")
    c3.metric("🏭 SIN — Sistema Nacional","~50,445 MW pico","Nuclear + Gas + Hidro")
    st.divider()

    st.subheader("🏭 Capacidad instalada por tecnología")
    cap_2024 = cargar_capacidades("2024")
    fig_cap = px.bar(
        cap_2024[cap_2024.capacidad_mw > 0],
        x="sistema", y="capacidad_mw", color="tecnologia",
        color_discrete_map=COLORES,
        barmode="stack",
        title="Capacidad instalada 2024 (MW)",
        labels={"capacidad_mw":"MW","sistema":"Sistema","tecnologia":"Tecnología"}
    )
    fig_cap.update_layout(height=400, legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig_cap, use_container_width=True)

    st.subheader("📋 Escenarios disponibles")
    for clave, esc in ESCENARIOS.items():
        with st.expander(f"{esc['nombre']}"):
            st.write(esc['descripcion'])
            st.info(f"💡 {esc['lección']}")

# ════════════════════════════════════════════════════════════════
# TAB 2: DEMANDA
# ════════════════════════════════════════════════════════════════
with tab2:
    st.header("📊 Demanda Real — CENACE")
    if st.button("📡 Descargar datos CENACE", use_container_width=True):
        fi = fecha_ini.strftime("%Y-%m-%d")
        ff = fecha_fin.strftime("%Y-%m-%d")
        demandas_raw = {}
        with st.spinner("Consultando CENACE..."):
            for s in sistemas:
                df = get_demanda_total_sistema(s, fi, ff)
                if not df.empty:
                    demandas_raw[s] = df
                    st.session_state[f"dem_{s}"] = df

        if demandas_raw:
            cols = st.columns(len(demandas_raw))
            for i,(s,df) in enumerate(demandas_raw.items()):
                rep = validate_demand_data(df)
                cols[i].metric(f"{s}", f"{df['total_cargas_mw'].max():.0f} MW pico")
                cols[i].metric("Cobertura", f"{rep['coverage_pct']}%")

            fig = go.Figure()
            for s,df in demandas_raw.items():
                fig.add_trace(go.Scatter(
                    y=df['total_cargas_mw'].values, name=s, mode='lines'))
            fig.update_layout(title="Demanda horaria real (CENACE MDA)",
                xaxis_title="Hora", yaxis_title="MW",
                height=380, hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

            # Curva de duración de carga
            st.subheader("📈 Curva de Duración de Carga")
            fig2 = go.Figure()
            for s,df in demandas_raw.items():
                sv = np.sort(df['total_cargas_mw'].values)[::-1]
                pct = np.linspace(0,100,len(sv))
                fig2.add_trace(go.Scatter(x=pct, y=sv, name=s, mode='lines'))
            fig2.update_layout(
                xaxis_title="% del tiempo", yaxis_title="MW",
                title="Curva de Duración de Carga",
                height=350)
            st.plotly_chart(fig2, use_container_width=True)

# ════════════════════════════════════════════════════════════════
# TAB 3: DESPACHO
# ════════════════════════════════════════════════════════════════
with tab3:
    st.header("⚙️ Despacho Económico")

    if correr:
        if not sistemas:
            st.error("Selecciona al menos un sistema.")
            st.stop()

        fi = fecha_ini.strftime("%Y-%m-%d")
        ff = fecha_fin.strftime("%Y-%m-%d")
        año = fecha_ini.year

        demandas = {}
        with st.spinner("Descargando demanda CENACE..."):
            for s in sistemas:
                df = get_demanda_total_sistema(s, fi, ff)
                if not df.empty:
                    demandas[s] = df['total_cargas_mw'].reset_index(drop=True)

        if not demandas:
            st.error("Sin datos.")
            st.stop()

        # VRE reales si aplica
        perfiles_vre = {}
        if usar_vre_real and token_ninja:
            prog = st.progress(0, text="Descargando perfiles VRE...")
            sitios_lista = [(s,t,info) for s,techs in SITIOS_VRE.items()
                            if s in demandas for t,info in techs.items()]
            for idx,(s,tech,info) in enumerate(sitios_lista):
                try:
                    n = len(demandas[s])
                    perfil = descargar_perfil_vre(
                        info["lat"], info["lon"], tech, año, token_ninja, n)
                    if perfil is not None:
                        if s not in perfiles_vre:
                            perfiles_vre[s] = {}
                        perfiles_vre[s][tech] = perfil
                    time.sleep(3)
                except Exception:
                    pass  # Silenciosamente usa sintético
                prog.progress((idx+1)/len(sitios_lista),
                              text=f"VRE: {s} {tech}...")
            prog.empty()

        # Parámetros
        cap = cargar_capacidades("2024")
        costos_override = None
        año_cap = "2024"
        voll_usar = voll

        if usar_sliders:
            costos_override = {"CCGT_Gas":costo_gas,"Carbon":costo_carbon,
                               "Fuel_Oil":costo_oil,"Diesel_Peaker":costo_diesel}
        elif clave_esc != "base":
            params = aplicar_escenario(clave_esc, cap)
            costos_override = params.get("costos_override")
            voll_usar = params.get("voll", voll)
            año_cap = params.get("año_capacidad","2024")
            cap = params.get("capacidades_modificadas", cap)

        with st.spinner("Optimizando con HiGHS..."):
            resultados = correr_despacho_completo(
                demandas, año_capacidad=año_cap,
                costos_override=costos_override, voll=voll_usar,
                perfiles_vre=perfiles_vre if perfiles_vre else None
            )
            st.session_state["resultados"] = resultados
            st.session_state["perfiles_vre"] = perfiles_vre
            st.session_state["cap_usada"] = cap
        st.success("✅ Optimización completada")

    if "resultados" in st.session_state:
        resultados = st.session_state["resultados"]
        perfiles_vre = st.session_state.get("perfiles_vre", {})
        cap_usada = st.session_state.get("cap_usada", cargar_capacidades("2024"))

        # ── KPIs ──────────────────────────────────────────────────
        st.subheader("📊 Resumen Ejecutivo")
        cols = st.columns(len(resultados))
        for i,(s,r) in enumerate(resultados.items()):
            if r["exito"]:
                pm = r["precio_marginal"]
                shed = r["shedding_mw"].sum()
                # CO2
                co2_total = sum(
                    r["despacho"].get(t, np.zeros(1)).sum() * CO2_FACTOR.get(t,0)
                    for t in CO2_FACTOR
                ) / 1000  # toneladas
                with cols[i]:
                    st.metric(f"💰 {s}", f"${r['costo_total_usd']/1e6:.2f}M USD")
                    st.metric("Precio Marginal Prom.",
                              f"{pm.mean():.1f} USD/MWh" if pm is not None else "N/A")
                    st.metric("CO₂ estimado", f"{co2_total:,.0f} ton")
                    st.metric("Load Shedding",f"{shed:.1f} MWh",
                              delta="⚠️ Déficit" if shed>1 else "✅ OK",
                              delta_color="inverse")

        # ── Curva de orden de mérito ───────────────────────────────
        st.divider()
        st.subheader("📈 Curva de Orden de Mérito (Merit Order)")
        sis_sel = st.selectbox("Sistema para merit order",
                               options=list(resultados.keys()), key="mo_sel")
        cap_sis = cap_usada[cap_usada["sistema"]==sis_sel].copy()
        cap_sis = cap_sis[cap_sis["capacidad_mw"]>0].sort_values("costo_var_usd_mwh")

        fig_mo = go.Figure()
        x_acum = 0
        for _, row in cap_sis.iterrows():
            if row["tipo"] != "almacenamiento":
                fig_mo.add_trace(go.Bar(
                    x=[row["capacidad_mw"]],
                    y=[row["costo_var_usd_mwh"]],
                    name=row["tecnologia"],
                    marker_color=COLORES.get(row["tecnologia"],"#aaa"),
                    base=x_acum,
                    orientation="h",
                    showlegend=True,
                    hovertemplate=f"{row['tecnologia']}: {row['costo_var_usd_mwh']} USD/MWh | {row['capacidad_mw']:.0f} MW"
                ))
                x_acum += row["capacidad_mw"]

        fig_mo.update_layout(
            title=f"Curva de Orden de Mérito — {sis_sel} (costo vs capacidad acumulada)",
            xaxis_title="Capacidad acumulada (MW)",
            yaxis_title="Costo variable (USD/MWh)",
            barmode="stack", height=380,
            legend=dict(orientation="h", y=-0.3)
        )
        st.plotly_chart(fig_mo, use_container_width=True)
        st.caption("💡 El punto donde la demanda corta esta curva determina el precio marginal del mercado.")

        # ── Gráficas por sistema ───────────────────────────────────
        for s,r in resultados.items():
            if not r["exito"]:
                st.error(f"{s}: {r['error']}")
                continue

            st.divider()
            st.subheader(f"🔌 Sistema {s}")

            col_izq, col_der = st.columns([2,1])

            with col_izq:
                # Stack de despacho ordenado
                fig = go.Figure()
                techs_ord = [t for t in ORDEN_STACK if t in r["despacho"]]
                techs_resto = [t for t in r["despacho"] if t not in ORDEN_STACK]
                for tech in techs_ord + techs_resto:
                    vals = r["despacho"].get(tech)
                    if vals is not None and vals.sum() > 0.1:
                        fig.add_trace(go.Scatter(
                            y=vals, name=tech, stackgroup="gen",
                            fillcolor=COLORES.get(tech,"#aaa"),
                            line=dict(width=0),
                            hovertemplate=f"{tech}: %{{y:.1f}} MW"))
                fig.add_trace(go.Scatter(
                    y=r["demanda_mw"], name="Demanda",
                    line=dict(color="black",width=2,dash="dash")))
                tipo_vre = "🌞 Reales" if perfiles_vre.get(s) else "📐 Sintéticos"
                fig.update_layout(
                    title=f"Despacho horario — {s} | VRE: {tipo_vre}",
                    xaxis_title="Hora", yaxis_title="MW",
                    height=380, hovermode="x unified",
                    legend=dict(orientation="h", y=-0.3))
                st.plotly_chart(fig, use_container_width=True)

            with col_der:
                # Pie de mix
                labels = [t for t,v in r["despacho"].items() if v.sum()>0.1]
                values = [r["despacho"][t].sum() for t in labels]
                fig_pie = go.Figure(go.Pie(
                    labels=labels, values=values,
                    marker_colors=[COLORES.get(t,"#aaa") for t in labels],
                    hole=0.4))
                fig_pie.update_layout(
                    title="Mix de generación (MWh)",
                    height=380, legend=dict(font_size=10))
                st.plotly_chart(fig_pie, use_container_width=True)

            # Precio marginal + demanda en subplot
            pm = r["precio_marginal"]
            if pm is not None:
                fig_pm = make_subplots(specs=[[{"secondary_y": True}]])
                fig_pm.add_trace(go.Scatter(
                    y=r["demanda_mw"], name="Demanda",
                    line=dict(color="gray", width=1, dash="dot"),
                    fill="tozeroy", fillcolor="rgba(200,200,200,0.2)"),
                    secondary_y=True)
                fig_pm.add_trace(go.Scatter(
                    y=pm, name="Precio Marginal",
                    line=dict(color="darkblue", width=2),
                    fill="tozeroy", fillcolor="rgba(0,0,139,0.15)"),
                    secondary_y=False)
                fig_pm.update_layout(
                    title=f"Precio Marginal vs Demanda — {s}",
                    height=280, hovermode="x unified")
                fig_pm.update_yaxes(title_text="USD/MWh", secondary_y=False)
                fig_pm.update_yaxes(title_text="MW", secondary_y=True)
                st.plotly_chart(fig_pm, use_container_width=True)

            # CO2 horario
            co2_horario = np.zeros(r["n_horas"])
            for tech, vals in r["despacho"].items():
                co2_horario += vals * CO2_FACTOR.get(tech, 0) / 1000
            fig_co2 = go.Figure()
            fig_co2.add_trace(go.Scatter(
                y=co2_horario, name="CO₂",
                fill="tozeroy", fillcolor="rgba(180,0,0,0.2)",
                line=dict(color="darkred", width=1)))
            fig_co2.update_layout(
                title=f"Emisiones CO₂ estimadas — {s}",
                xaxis_title="Hora", yaxis_title="tonCO₂/h",
                height=220)
            st.plotly_chart(fig_co2, use_container_width=True)

            # Batería
            if r["bateria_soc"] is not None and r["bateria_soc"].sum() > 0:
                fig_bat = make_subplots(specs=[[{"secondary_y": True}]])
                fig_bat.add_trace(go.Bar(
                    y=r["bateria_carga"], name="Carga",
                    marker_color="rgba(0,150,0,0.7)"), secondary_y=False)
                fig_bat.add_trace(go.Bar(
                    y=-r["bateria_descarga"], name="Descarga",
                    marker_color="rgba(200,0,0,0.7)"), secondary_y=False)
                fig_bat.add_trace(go.Scatter(
                    y=r["bateria_soc"], name="SOC",
                    line=dict(color="purple",width=2)), secondary_y=True)
                fig_bat.update_layout(
                    title=f"Operación de Batería — {s}",
                    height=250, barmode="relative")
                fig_bat.update_yaxes(title_text="MW", secondary_y=False)
                fig_bat.update_yaxes(title_text="MWh SOC", secondary_y=True)
                st.plotly_chart(fig_bat, use_container_width=True)

        # ── Comparativo entre sistemas ─────────────────────────────
        if len(resultados) > 1:
            st.divider()
            st.subheader("📊 Comparativo entre sistemas")

            # Tabla resumen
            rows = []
            for s,r in resultados.items():
                if r["exito"]:
                    pm = r["precio_marginal"]
                    co2 = sum(r["despacho"].get(t,np.zeros(1)).sum()*CO2_FACTOR.get(t,0)
                              for t in CO2_FACTOR)/1000
                    ren_mwh = sum(r["despacho"].get(t,np.zeros(1)).sum()
                                  for t in ["Solar","Eolica","Hidro"])
                    total_mwh = sum(v.sum() for v in r["despacho"].values())
                    rows.append({
                        "Sistema": s,
                        "Costo total (MUSD)": round(r["costo_total_usd"]/1e6,2),
                        "PM promedio (USD/MWh)": round(pm.mean(),1) if pm is not None else "N/A",
                        "CO₂ (ton)": round(co2,0),
                        "% Renovable+Hidro": round(ren_mwh/total_mwh*100,1) if total_mwh>0 else 0,
                        "Shedding (MWh)": round(r["shedding_mw"].sum(),1),
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows).set_index("Sistema"),
                             use_container_width=True)

            # Comparativo de costos
            fig_comp = go.Figure()
            for s,r in resultados.items():
                if r["exito"]:
                    labels = [t for t,v in r["despacho"].items() if v.sum()>0.1]
                    values = [r["despacho"][t].sum()*
                              cargar_capacidades("2024")[
                                  (cargar_capacidades("2024")["sistema"]==s) &
                                  (cargar_capacidades("2024")["tecnologia"]==t)
                              ]["costo_var_usd_mwh"].values[0]
                              if len(cargar_capacidades("2024")[
                                  (cargar_capacidades("2024")["sistema"]==s) &
                                  (cargar_capacidades("2024")["tecnologia"]==t)
                              ]) > 0 else 0
                              for t in labels]
                    fig_comp.add_trace(go.Bar(name=s, x=labels, y=values))
            fig_comp.update_layout(
                title="Costo por tecnología y sistema (USD)",
                barmode="group", height=380,
                xaxis_title="Tecnología", yaxis_title="USD")
            st.plotly_chart(fig_comp, use_container_width=True)

    else:
        st.info("👈 Configura los parámetros en el sidebar y presiona **CORRER DESPACHO**.")

# ════════════════════════════════════════════════════════════════
# TAB 4: ESCENARIOS
# ════════════════════════════════════════════════════════════════
with tab4:
    st.header("🔀 Análisis de Escenarios")
    for clave, esc in ESCENARIOS.items():
        with st.expander(f"{esc['nombre']}"):
            c1,c2 = st.columns(2)
            c1.markdown(f"**¿Qué simula?**\n\n{esc['descripcion']}")
            c2.info(f"💡 **Lección:** {esc['lección']}")

# ════════════════════════════════════════════════════════════════
# TAB 5: METODOLOGÍA
# ════════════════════════════════════════════════════════════════
with tab5:
    st.header("📖 Metodología y Stack Técnico")
    st.markdown("""
    ### Stack técnico
    | Componente | Herramienta | Detalle |
    |------------|-------------|---------|
    | Datos demanda | CENACE SWCAEZC API | MDA horario, 3 sistemas |
    | Perfiles VRE | Renewables.ninja MERRA-2 | Solar PV + Wind, coordenadas reales |
    | Optimización | PyPSA 0.26 + Linopy | LP multi-período |
    | Solver | HiGHS | Open-source, <1s por sistema |
    | UI | Streamlit + Plotly | Interactivo, multi-tab |

    ### Formulación del problema (LP)
    **min** Σᵢ Σₜ cᵢ · pᵢₜ

    **sujeto a:**
    - Balance energía: Σᵢ pᵢₜ = dₜ ∀t
    - Límites: 0 ≤ pᵢₜ ≤ pᵢ_max · fᵢₜ
    - SOC batería: sₜ = sₜ₋₁ + η·cₜ - dₜ/η
    - Ciclicidad: s₀ = sₜ_final

    ### 11 Tecnologías modeladas
    | Tipo | Tecnologías |
    |------|-------------|
    | Baseload | Nuclear, Geotermia |
    | Térmica | Carbón, Cogeneración, CCGT Gas, Fuel Oil, Turbogas, Diesel |
    | Hidro | Hidro regulable |
    | Renovable | Solar FV, Eólica |
    | Almacenamiento | Batería Li-ion (4h) |

    ### Precio marginal
    Dual value de la restricción **Bus-nodal-balance** de Linopy.
    Representa el **costo de oportunidad** de servir 1 MWh adicional.
    """)
