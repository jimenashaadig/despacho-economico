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

st.set_page_config(page_title="Despacho Economico - Mexico", page_icon="⚡", layout="wide")
COLORES = {"Nuclear":"#1B2A4A","Geotermia":"#8B2252","Carbon":"#5C4033","Cogeneracion":"#D4760A","CCGT_Gas":"#2E75B6","Combustion_Interna":"#B85C1F","Fuel_Oil":"#C44E52","Turbogas":"#E8963E","Diesel_Peaker":"#D62728","Hidro":"#1A8754","Solar":"#F5C518","Eolica":"#17BECF","Bateria":"#9467BD"}
ORDEN_STACK = ["Nuclear","Geotermia","Carbon","Cogeneracion","CCGT_Gas","Combustion_Interna","Fuel_Oil","Turbogas","Diesel_Peaker","Hidro","Eolica","Solar","Bateria"]
CO2_FACTOR = {"Nuclear":0,"Geotermia":50,"Carbon":820,"Cogeneracion":400,"CCGT_Gas":370,"Combustion_Interna":650,"Fuel_Oil":650,"Turbogas":500,"Diesel_Peaker":700,"Hidro":4,"Solar":0,"Eolica":0,"Bateria":0}
SITIOS_VRE = {"BCS":{"Solar":{"lat":24.1,"lon":-110.3},"Eolica":{"lat":24.8,"lon":-111.9}},"BCA":{"Solar":{"lat":32.5,"lon":-115.5},"Eolica":{"lat":31.8,"lon":-116.6}},"SIN":{"Solar":{"lat":29.1,"lon":-110.9},"Eolica":{"lat":16.5,"lon":-95.0}}}

with st.sidebar:
    st.title("Despacho Economico")
    st.caption("Sistemas Electricos de Mexico  |  BCS - BCA - SIN")
    st.divider()
    st.subheader("Periodo de analisis")
    fecha_ini = st.date_input("Fecha inicio", value=date(2024,6,1), min_value=date(2023,1,1), max_value=date(2024,12,31))
    fecha_fin = st.date_input("Fecha fin", value=date(2024,6,7), min_value=date(2023,1,1), max_value=date(2024,12,31))
    st.subheader("Sistemas a simular")
    c1,c2,c3 = st.columns(3)
    sistemas = [s for s,ok in [("BCS",c1.checkbox("BCS",True)),("BCA",c2.checkbox("BCA",True)),("SIN",c3.checkbox("SIN",True))] if ok]
    st.subheader("Perfiles de generacion renovable")
    usar_vre_real = st.toggle("Usar datos reales (Renewables.ninja)", value=False)
    token_ninja = ""
    if usar_vre_real:
        token_ninja = st.text_input("Token API", type="password", placeholder="Tu token de renewables.ninja")
        if token_ninja:
            st.success("Token configurado")
    st.subheader("Escenario de simulacion")
    opciones_esc = [("base","Caso Base (2024)")] + listar_escenarios()
    clave_esc = st.selectbox("Selecciona escenario", options=[c for c,_ in opciones_esc], format_func=lambda c: dict(opciones_esc)[c])
    if clave_esc != "base" and clave_esc in ESCENARIOS:
        st.info(ESCENARIOS[clave_esc]["descripcion"])
    with st.expander("Ajuste manual de costos variables (USD/MWh)"):
        costo_gas = st.slider("CCGT Gas",10,300,45)
        costo_carbon = st.slider("Carbon",20,250,60)
        costo_oil = st.slider("Fuel Oil",50,400,100)
        costo_ci = st.slider("Combustion Interna",50,400,110)
        costo_diesel = st.slider("Diesel Peaker",50,500,150)
        usar_sliders = st.checkbox("Aplicar costos manuales")
    voll = st.slider("VOLL (USD/MWh)",1000,15000,5000,step=500)
    correr = st.button("EJECUTAR DESPACHO", type="primary", use_container_width=True)

tab1,tab2,tab3,tab4,tab5 = st.tabs(["Inicio","Demanda CENACE","Despacho Economico","Escenarios","Metodologia"])

with tab1:
    st.title("Simulador de Despacho Economico")
    st.markdown("**Sistemas Electricos de Mexico: BCS, BCA y SIN**")
    st.markdown("Datos reales **CENACE** · Optimizacion **PyPSA + HiGHS** · VRE **Renewables.ninja** · 11 tecnologias")
    st.divider()
    c1,c2,c3 = st.columns(3)
    c1.metric("BCS — Baja California Sur","~900 MW","Comb. interna + Turbogas + Solar")
    c2.metric("BCA — Baja California","~3,200 MW","Geotermia (Cerro Prieto) + Gas + Renovable")
    c3.metric("SIN — Sistema Nacional","~77,000 MW","Nuclear + Gas + Hidro + Renovable")
    st.divider()
    st.subheader("Capacidad instalada por tecnologia y sistema")
    cap_2024 = cargar_capacidades("2024")
    fig_cap = px.bar(cap_2024[cap_2024.capacidad_mw>0], x="sistema", y="capacidad_mw", color="tecnologia", color_discrete_map=COLORES, barmode="stack", title="Capacidad instalada 2024 (MW) — Fuente: PRODESEN 2024-2038", labels={"capacidad_mw":"MW","sistema":"Sistema","tecnologia":"Tecnologia"})
    fig_cap.update_layout(height=420, legend=dict(orientation="h",y=-0.3))
    st.plotly_chart(fig_cap, use_container_width=True, key="cap_inicio")
    st.subheader("Escenarios disponibles")
    for clave,esc in ESCENARIOS.items():
        with st.expander(f"{esc['nombre']}"):
            st.write(esc['descripcion'])
            st.markdown(f"**Leccion:** {esc['lección']}")

with tab2:
    st.header("Demanda Real — CENACE")
    st.markdown("Datos horarios del MDA obtenidos del web service SWCAEZC de CENACE.")
    if st.button("Descargar datos de demanda CENACE", use_container_width=True):
        fi,ff = fecha_ini.strftime("%Y-%m-%d"),fecha_fin.strftime("%Y-%m-%d")
        demandas_raw = {}
        with st.spinner("Consultando API de CENACE..."):
            for s in sistemas:
                df = get_demanda_total_sistema(s,fi,ff)
                if not df.empty:
                    demandas_raw[s] = df
        if demandas_raw:
            cols = st.columns(len(demandas_raw))
            for i,(s,df) in enumerate(demandas_raw.items()):
                rep = validate_demand_data(df)
                cols[i].metric(f"{s} — Demanda pico",f"{df['total_cargas_mw'].max():.0f} MW")
                cols[i].metric("Cobertura temporal",f"{rep['coverage_pct']}%")
            fig = go.Figure()
            for s,df in demandas_raw.items():
                fig.add_trace(go.Scatter(y=df['total_cargas_mw'].values,name=s,mode='lines'))
            fig.update_layout(title="Demanda horaria real (CENACE MDA)",xaxis_title="Hora",yaxis_title="MW",height=380,hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True, key="dem_horaria")
            st.subheader("Curva de duracion de carga")
            fig2 = go.Figure()
            for s,df in demandas_raw.items():
                sv = np.sort(df['total_cargas_mw'].values)[::-1]
                fig2.add_trace(go.Scatter(x=np.linspace(0,100,len(sv)),y=sv,name=s,mode='lines'))
            fig2.update_layout(xaxis_title="% del tiempo excedido",yaxis_title="MW",title="Curva de duracion de carga",height=350)
            st.plotly_chart(fig2, use_container_width=True, key="dem_duracion")
        else:
            st.error("No se obtuvieron datos de CENACE. Revisa la conexion a internet.")

with tab3:
    st.header("Despacho Economico")
    if correr:
        if not sistemas:
            st.error("Selecciona al menos un sistema.")
            st.stop()
        fi,ff = fecha_ini.strftime("%Y-%m-%d"),fecha_fin.strftime("%Y-%m-%d")
        demandas = {}
        with st.spinner("Descargando demanda de CENACE..."):
            for s in sistemas:
                df = get_demanda_total_sistema(s,fi,ff)
                if not df.empty and df['total_cargas_mw'].sum() > 0:
                    demandas[s] = df['total_cargas_mw'].reset_index(drop=True)
                    st.write(f"✅ {s}: {len(df)} horas | Pico: {df['total_cargas_mw'].max():.0f} MW | Prom: {df['total_cargas_mw'].mean():.0f} MW")
                else:
                    st.warning(f"⚠️ {s}: Sin datos de CENACE")
        if not demandas:
            st.error("No se obtuvieron datos de demanda. Verifica tu conexion a internet.")
            st.stop()
        perfiles_vre = {}
        if usar_vre_real and token_ninja:
            prog = st.progress(0, text="Descargando VRE...")
            sitios_lista = [(s,t,info) for s,techs in SITIOS_VRE.items() if s in demandas for t,info in techs.items()]
            for idx,(s,tech,info) in enumerate(sitios_lista):
                try:
                    perfil = descargar_perfil_vre(info["lat"],info["lon"],tech,fecha_ini.year,token_ninja,len(demandas[s]))
                    if perfil is not None:
                        if s not in perfiles_vre: perfiles_vre[s] = {}
                        perfiles_vre[s][tech] = perfil
                    time.sleep(3)
                except Exception:
                    pass
                prog.progress((idx+1)/len(sitios_lista), text=f"VRE: {s} {tech}...")
            prog.empty()
        año_cap = "2024"
        costos_override = None
        voll_usar = voll
        capacidades_para_modelo = None
        if usar_sliders:
            costos_override = {"CCGT_Gas":costo_gas,"Carbon":costo_carbon,"Fuel_Oil":costo_oil,"Combustion_Interna":costo_ci,"Diesel_Peaker":costo_diesel}
        elif clave_esc != "base":
            cap_esc = cargar_capacidades(ESCENARIOS[clave_esc]["año_capacidad"])
            params = aplicar_escenario(clave_esc, cap_esc)
            costos_override = params.get("costos_override")
            voll_usar = params.get("voll", voll)
            año_cap = params.get("año_capacidad","2024")
            capacidades_para_modelo = params.get("capacidades_modificadas", cap_esc)
        with st.spinner("Resolviendo despacho optimo con HiGHS..."):
            resultados = correr_despacho_completo(demandas, año_capacidad=año_cap, costos_override=costos_override, voll=voll_usar, perfiles_vre=perfiles_vre if perfiles_vre else None, capacidades_df=capacidades_para_modelo)
            st.session_state["resultados"] = resultados
            st.session_state["perfiles_vre"] = perfiles_vre
            st.session_state["cap_usada"] = capacidades_para_modelo if capacidades_para_modelo is not None else cargar_capacidades(año_cap)
            st.session_state["escenario_usado"] = clave_esc
        st.success("Optimizacion completada.")

    if "resultados" in st.session_state:
        resultados = st.session_state["resultados"]
        perfiles_vre = st.session_state.get("perfiles_vre",{})
        cap_usada = st.session_state.get("cap_usada", cargar_capacidades("2024"))
        esc_usado = st.session_state.get("escenario_usado","base")
        if esc_usado != "base":
            st.info(f"Escenario activo: **{ESCENARIOS[esc_usado]['nombre']}**")

        st.subheader("Resumen ejecutivo")
        cols = st.columns(len(resultados))
        for i,(s,r) in enumerate(resultados.items()):
            if r["exito"]:
                pm = r["precio_marginal"]
                shed = r["shedding_mw"].sum()
                co2_total = sum(r["despacho"].get(t,np.zeros(1)).sum()*CO2_FACTOR.get(t,0) for t in CO2_FACTOR)/1000
                with cols[i]:
                    st.metric(f"{s} — Costo total",f"${r['costo_total_usd']/1e6:.2f}M USD")
                    st.metric("Precio marginal promedio",f"{pm.mean():.1f} USD/MWh" if pm is not None else "N/A")
                    st.metric("Emisiones CO2",f"{co2_total:,.0f} ton")
                    st.metric("Load Shedding",f"{shed:.1f} MWh" if shed>1 else "0 MWh")

        st.divider()
        st.subheader("Curva de orden de merito (Merit Order)")
        sis_sel = st.selectbox("Sistema", options=list(resultados.keys()), key="mo_sel")
        cap_sis = cap_usada[(cap_usada["sistema"]==sis_sel) & (cap_usada["capacidad_mw"]>0) & (cap_usada["tipo"]!="almacenamiento")].sort_values("costo_var_usd_mwh")
        fig_mo = go.Figure()
        x_pos = 0
        for _,row in cap_sis.iterrows():
            tech,width,cost = row["tecnologia"],row["capacidad_mw"],row["costo_var_usd_mwh"]
            fig_mo.add_trace(go.Bar(x=[width],y=[cost],base=x_pos,orientation="h",name=tech,marker_color=COLORES.get(tech,"#aaa"),showlegend=True,hovertemplate=f"<b>{tech}</b><br>Cap: {width:.0f} MW<br>Costo: {cost} USD/MWh<extra></extra>"))
            x_pos += width
        if sis_sel in resultados and resultados[sis_sel]["exito"]:
            dem_prom = resultados[sis_sel]["demanda_mw"].mean()
            fig_mo.add_vline(x=dem_prom,line_dash="dash",line_color="black",annotation_text=f"Demanda prom: {dem_prom:.0f} MW")
        fig_mo.update_layout(title=f"Merit Order — {sis_sel}",xaxis_title="Capacidad acumulada (MW)",yaxis_title="Costo variable (USD/MWh)",barmode="stack",height=400,legend=dict(orientation="h",y=-0.3))
        st.plotly_chart(fig_mo, use_container_width=True, key="merit_order")

        for s,r in resultados.items():
            if not r["exito"]:
                st.error(f"{s}: {r['error']}")
                continue
            st.divider()
            st.subheader(f"Sistema {s}")
            n_h = r["n_horas"]
            horas = list(range(n_h))
            x_range = [0, n_h-1]

            # ── Grafica de despacho + pie chart lado a lado ──
            col_izq,col_der = st.columns([2,1])
            with col_izq:
                fig = go.Figure()
                for tech in [t for t in ORDEN_STACK if t in r["despacho"]] + [t for t in r["despacho"] if t not in ORDEN_STACK]:
                    vals = r["despacho"].get(tech)
                    if vals is not None and vals.sum()>0.1:
                        fig.add_trace(go.Scatter(x=horas,y=vals,name=tech,stackgroup="gen",fillcolor=COLORES.get(tech,"#aaa"),line=dict(width=0),hovertemplate=f"{tech}: %{{y:.1f}} MW<extra></extra>"))
                fig.add_trace(go.Scatter(x=horas,y=r["demanda_mw"],name="Demanda",line=dict(color="black",width=2,dash="dash"),hovertemplate="Demanda: %{y:.1f} MW<extra></extra>"))
                fig.update_layout(title=f"Despacho horario — {s}",xaxis_title="Hora",yaxis_title="MW",height=400,hovermode="x unified",legend=dict(orientation="h",y=-0.3),xaxis=dict(range=x_range))
                st.plotly_chart(fig, use_container_width=True, key=f"despacho_{s}")
            with col_der:
                # Pie chart de mix de generacion
                labels = [t for t,v in r["despacho"].items() if v.sum()>0.1]
                values = [r["despacho"][t].sum() for t in labels]
                if labels:
                    total_gen = sum(values)
                    pcts = [v/total_gen*100 for v in values]
                    fig_pie = go.Figure(go.Pie(
                        labels=labels, values=values,
                        marker_colors=[COLORES.get(t,"#aaa") for t in labels],
                        hole=0.4,
                        texttemplate="%{label}<br>%{percent}",
                        hovertemplate="<b>%{label}</b><br>%{value:,.0f} MWh<br>%{percent}<extra></extra>"
                    ))
                    fig_pie.update_layout(
                        title=f"Mix de generacion — {s}<br><sup>Total: {total_gen/1000:.1f} GWh</sup>",
                        height=420,
                        showlegend=False,
                        margin=dict(t=60,b=20,l=20,r=20)
                    )
                    st.plotly_chart(fig_pie, use_container_width=True, key=f"pie_{s}")

            # ── PML alineado con despacho ──
            pm = r["precio_marginal"]
            if pm is not None:
                fig_pm = make_subplots(specs=[[{"secondary_y":True}]])
                fig_pm.add_trace(go.Scatter(x=horas,y=r["demanda_mw"],name="Demanda (MW)",line=dict(color="gray",width=1,dash="dot"),fill="tozeroy",fillcolor="rgba(200,200,200,0.15)",hovertemplate="Demanda: %{y:.1f} MW<extra></extra>"),secondary_y=True)
                fig_pm.add_trace(go.Scatter(x=horas,y=pm,name="PML (USD/MWh)",line=dict(color="#1B2A4A",width=2),fill="tozeroy",fillcolor="rgba(27,42,74,0.12)",hovertemplate="PML: %{y:.2f} USD/MWh<extra></extra>"),secondary_y=False)
                fig_pm.update_layout(title=f"Precio Marginal vs Demanda — {s}",height=300,hovermode="x unified",xaxis_title="Hora",xaxis=dict(range=x_range))
                fig_pm.update_yaxes(title_text="USD/MWh",secondary_y=False)
                fig_pm.update_yaxes(title_text="MW",secondary_y=True)
                st.plotly_chart(fig_pm, use_container_width=True, key=f"pml_{s}")
                with st.expander(f"Tabla detallada: PML y despacho horario — {s}"):
                    tabla_data = {"Hora":horas,"Demanda_MW":np.round(r["demanda_mw"],1),"PML_USD_MWh":np.round(pm,2)}
                    for tech,vals in r["despacho"].items():
                        if vals.sum()>0.1: tabla_data[f"{tech}_MW"] = np.round(vals,1)
                    st.dataframe(pd.DataFrame(tabla_data), use_container_width=True, height=300)

            # ── CO2 alineado ──
            co2_horario = sum(r["despacho"].get(t,np.zeros(n_h))*CO2_FACTOR.get(t,0)/1000 for t in CO2_FACTOR)
            fig_co2 = go.Figure()
            fig_co2.add_trace(go.Scatter(x=horas,y=co2_horario,name="CO2",fill="tozeroy",fillcolor="rgba(180,0,0,0.15)",line=dict(color="darkred",width=1),hovertemplate="CO2: %{y:.1f} tonCO2/h<extra></extra>"))
            fig_co2.update_layout(title=f"Emisiones CO2 — {s}",xaxis_title="Hora",yaxis_title="tonCO2/h",height=220,xaxis=dict(range=x_range))
            st.plotly_chart(fig_co2, use_container_width=True, key=f"co2_{s}")

            # ── Bateria ──
            if r["bateria_soc"] is not None and r["bateria_soc"].sum()>0:
                fig_bat = make_subplots(specs=[[{"secondary_y":True}]])
                fig_bat.add_trace(go.Bar(x=horas,y=r["bateria_carga"],name="Carga",marker_color="rgba(26,135,84,0.7)"),secondary_y=False)
                fig_bat.add_trace(go.Bar(x=horas,y=-r["bateria_descarga"],name="Descarga",marker_color="rgba(214,39,40,0.7)"),secondary_y=False)
                fig_bat.add_trace(go.Scatter(x=horas,y=r["bateria_soc"],name="SOC",line=dict(color="#9467BD",width=2)),secondary_y=True)
                fig_bat.update_layout(title=f"Bateria — {s}",height=260,barmode="relative",xaxis_title="Hora",xaxis=dict(range=x_range))
                st.plotly_chart(fig_bat, use_container_width=True, key=f"bat_{s}")

        # ── Tabla comparativa ──
        if len(resultados)>1:
            st.divider()
            st.subheader("Comparativo entre sistemas")
            rows = []
            for s,r in resultados.items():
                if r["exito"]:
                    pm = r["precio_marginal"]
                    co2 = sum(r["despacho"].get(t,np.zeros(1)).sum()*CO2_FACTOR.get(t,0) for t in CO2_FACTOR)/1000
                    ren = sum(r["despacho"].get(t,np.zeros(1)).sum() for t in ["Solar","Eolica","Hidro"])
                    total = sum(v.sum() for v in r["despacho"].values())
                    rows.append({"Sistema":s,"Costo (MUSD)":round(r["costo_total_usd"]/1e6,2),"PM prom (USD/MWh)":round(pm.mean(),1) if pm is not None else "N/A","CO2 (ton)":round(co2,0),"% Renovable":round(ren/total*100,1) if total>0 else 0,"Shedding (MWh)":round(r["shedding_mw"].sum(),1)})
            if rows:
                st.dataframe(pd.DataFrame(rows).set_index("Sistema"), use_container_width=True)
    else:
        st.info("Configura los parametros en el panel lateral y presiona EJECUTAR DESPACHO.")

with tab4:
    st.header("Analisis de escenarios")
    for clave,esc in ESCENARIOS.items():
        with st.expander(f"{esc['nombre']}"):
            c1,c2 = st.columns(2)
            c1.markdown(f"**Descripcion:**\n\n{esc['descripcion']}")
            c2.markdown(f"**Leccion:**\n\n{esc['lección']}")

with tab5:
    st.header("Metodologia y Stack Tecnico")
    st.markdown("""
### Stack tecnico
| Componente | Herramienta | Detalle |
|------------|-------------|---------|
| Datos de demanda | CENACE SWCAEZC API | MDA horario, batching 7 dias |
| Perfiles VRE | Renewables.ninja MERRA-2 | Solar PV + Wind |
| Optimizacion | PyPSA + Linopy | LP multi-periodo |
| Solver | HiGHS | Open-source |
| Interfaz | Streamlit + Plotly | Interactivo |

### Precio Marginal Local (PML)
Dual value de la restriccion Bus-nodal-balance. Costo de oportunidad de 1 MWh adicional.

### Fuentes de datos
- **Cerro Prieto**: 570 MW geotermia en Mexicali, BCA
- **Tres Virgenes**: 10 MW geotermia en BCS
- **Laguna Verde**: 1,552 MW nuclear (SIN)
- **Los Azufres + Los Humeros**: 282 MW geotermia (SIN)
""")
