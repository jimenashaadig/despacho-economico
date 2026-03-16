import pypsa
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
VOLL_DEFAULT = 5000.0
BATTERY_EFFICIENCY = 0.9

def _perfil_solar(n):
    h = np.arange(n) % 24
    return np.clip(np.where((h>=6)&(h<=18), np.sin(np.pi*(h-6)/12), 0.0), 0, 1)

def _perfil_eolico(n):
    h = np.arange(n) % 24
    return np.clip(0.5 + 0.3*np.cos(np.pi*h/12), 0.1, 0.9)

def cargar_capacidades(año="2024"):
    archivo = DATA_DIR / f"capacity_{año}.csv"
    if not archivo.exists():
        raise FileNotFoundError(f"No se encontró: {archivo}")
    return pd.read_csv(archivo, comment="#")

def _extraer_precio_marginal(net, sistema, n):
    try:
        dual = net.model.constraints["Bus-nodal-balance"].dual
        vals = dual.values.flatten()
        if len(vals) == n:
            return np.abs(vals)
        return np.abs(dual.values.reshape(-1,n)[0])
    except Exception:
        pass
    try:
        gen_t = net.generators_t.p
        costos = net.generators["marginal_cost"]
        pm = np.zeros(n)
        for h in range(n):
            activos = gen_t.iloc[h][gen_t.iloc[h]>0.1].index
            if len(activos)>0:
                pm[h] = costos[activos].max()
        return pm
    except Exception:
        return None

def construir_y_resolver(sistema, demanda_mw, capacidades_df, costos_override=None, voll=VOLL_DEFAULT, perfiles_vre=None):
    n = len(demanda_mw)
    net = pypsa.Network()
    net.set_snapshots(pd.RangeIndex(n))
    net.add("Bus", sistema, carrier="AC")
    net.add("Load", f"Dem_{sistema}", bus=sistema, p_set=demanda_mw.values)
    net.add("Generator", f"Shedding_{sistema}", bus=sistema, p_nom=float(demanda_mw.max())*2, marginal_cost=voll, carrier="shedding")
    cap = capacidades_df[capacidades_df["sistema"]==sistema].copy()
    for _, row in cap.iterrows():
        tech = str(row["tecnologia"])
        cap_mw = float(row["capacidad_mw"])
        costo = float(costos_override[tech]) if costos_override and tech in costos_override else float(row["costo_var_usd_mwh"])
        tipo = str(row["tipo"])
        if cap_mw <= 0:
            continue
        if tipo == "baseload":
            net.add("Generator", f"{tech}_{sistema}", bus=sistema, p_nom=cap_mw, marginal_cost=costo, carrier=tech, p_max_pu=0.85 if "Nuclear" in tech else 0.80)
        elif tipo == "renovable":
            if perfiles_vre and sistema in perfiles_vre and tech in perfiles_vre.get(sistema,{}):
                perfil = perfiles_vre[sistema][tech][:n]
            elif "Solar" in tech:
                perfil = _perfil_solar(n)
            else:
                perfil = _perfil_eolico(n)
            net.add("Generator", f"{tech}_{sistema}", bus=sistema, p_nom=cap_mw, marginal_cost=costo, carrier=tech, p_max_pu=perfil)
        elif tipo == "hidro":
            net.add("Generator", f"{tech}_{sistema}", bus=sistema, p_nom=cap_mw, marginal_cost=costo, carrier="hydro", p_max_pu=0.90, p_min_pu=0.30)
        elif tipo == "almacenamiento":
            net.add("StorageUnit", f"{tech}_{sistema}", bus=sistema, p_nom=cap_mw, max_hours=4, efficiency_store=BATTERY_EFFICIENCY, efficiency_dispatch=BATTERY_EFFICIENCY, cyclic_state_of_charge=True, marginal_cost=costo, carrier="battery")
        else:
            net.add("Generator", f"{tech}_{sistema}", bus=sistema, p_nom=cap_mw, marginal_cost=costo, carrier=tech)
    try:
        net.optimize(solver_name="highs")
    except AttributeError as e:
        if "objective_value" not in str(e):
            return {"exito":False,"error":str(e),"sistema":sistema}
    except Exception as e:
        return {"exito":False,"error":str(e),"sistema":sistema}
    if net.generators_t.p.empty:
        return {"exito":False,"error":"Sin resultados","sistema":sistema}
    gen_t = net.generators_t.p
    costo_total = float((gen_t * net.generators["marginal_cost"]).sum().sum())
    despacho = {}
    shedding_mw = np.zeros(n)
    for col in gen_t.columns:
        if "Shedding" in col:
            shedding_mw = gen_t[col].values
        else:
            despacho[col.replace(f"_{sistema}","")] = gen_t[col].values
    bat_soc = bat_carga = bat_descarga = None
    if not net.storage_units_t.p.empty:
        for col in net.storage_units_t.p.columns:
            flujo = net.storage_units_t.p[col].values
            bat_carga = np.maximum(0,-flujo)
            bat_descarga = np.maximum(0,flujo)
            if not net.storage_units_t.state_of_charge.empty:
                bat_soc = net.storage_units_t.state_of_charge[col].values
    return {"exito":True,"sistema":sistema,"n_horas":n,"costo_total_usd":costo_total,"despacho":despacho,"curtailment":{},"shedding_mw":shedding_mw,"bateria_soc":bat_soc,"bateria_carga":bat_carga,"bateria_descarga":bat_descarga,"precio_marginal":_extraer_precio_marginal(net,sistema,n),"demanda_mw":demanda_mw.values}

def correr_despacho_completo(demandas, año_capacidad="2024", costos_override=None, voll=VOLL_DEFAULT, perfiles_vre=None, capacidades_df=None):
    capacidades = capacidades_df if capacidades_df is not None else cargar_capacidades(año_capacidad)
    resultados = {}
    for sistema, demanda in demandas.items():
        print(f"[PyPSA] Resolviendo {sistema}...")
        r = construir_y_resolver(sistema, demanda, capacidades, costos_override, voll, perfiles_vre)
        print(f"  {'OK' if r['exito'] else 'ERROR'}")
        resultados[sistema] = r
    return resultados
