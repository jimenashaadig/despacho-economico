import pypsa
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(_file_).parent.parent.parent / "data"
VOLL_DEFAULT = 5000.0
BATTERY_EFFICIENCY = 0.9


def _perfil_solar(n):
    h = np.arange(n) % 24
    return np.clip(np.where((h >= 6) & (h <= 18), np.sin(np.pi * (h - 6) / 12), 0.0), 0, 1)


def _perfil_eolico(n):
    h = np.arange(n) % 24
    return np.clip(0.5 + 0.3 * np.cos(np.pi * h / 12), 0.1, 0.9)


def cargar_capacidades(año="2024"):
    archivo = DATA_DIR / f"capacity_{año}.csv"
    if not archivo.exists():
        raise FileNotFoundError(f"No se encontró: {archivo}")
    return pd.read_csv(archivo, comment="#")


def _extraer_precio_marginal(net, sistema, n):
    try:
        dual = net.model.constraints["Bus-nodal-balance"].dual
        if hasattr(dual, 'values'):
            vals = dual.values.flatten()
            if len(vals) == n:
                return np.abs(vals)
        if sistema in dual.coords.get("Bus-nodal-balance_dim_0", []):
            return np.abs(dual.sel(**{"Bus-nodal-balance_dim_0": sistema}).values)
        return np.abs(dual.values.reshape(-1, n)[0])
    except Exception:
        pass
    try:
        gen_t = net.generators_t.p
        costos = net.generators["marginal_cost"]
        pm = np.zeros(n)
        for h in range(n):
            activos = gen_t.iloc[h][gen_t.iloc[h] > 0.1].index
            if len(activos) > 0:
                pm[h] = costos[activos].max()
        return pm
    except Exception:
        return None


def construir_y_resolver(sistema, demanda_mw, capacidades_df,
                         costos_override=None, voll=VOLL_DEFAULT,
                         perfiles_vre=None):
    n = len(demanda_mw)
    net = pypsa.Network()
    net.set_snapshots(pd.RangeIndex(n))
    net.add("Bus", sistema, carrier="AC")
    net.add("Load", f"Dem_{sistema}", bus=sistema, p_set=demanda_mw.values)
    net.add("Generator", f"Shedding_{sistema}", bus=sistema,
            p_nom=float(demanda_mw.max()) * 2,
            marginal_cost=voll, carrier="shedding")

    cap = capacidades_df[capacidades_df["sistema"] == sistema].copy()
    for _, row in cap.iterrows():
        tech = str(row["tecnologia"])
        cap_mw = float(row["capacidad_mw"])
        costo = float(row["costo_var_usd_mwh"])
        tipo = str(row["tipo"])
        if costos_override and tech in costos_override:
            costo = float(costos_override[tech])
        if cap_mw <= 0:
            continue
        if tipo == "baseload":
            fp = 0.85 if "Nuclear" in tech else 0.80
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo, carrier=tech, p_max_pu=fp)
        elif tipo == "renovable":
            if perfiles_vre and sistema in perfiles_vre and tech in perfiles_vre.get(sistema, {}):
                perfil = perfiles_vre[sistema][tech][:n]
            elif "Solar" in tech:
                perfil = _perfil_solar(n)
            else:
                perfil = _perfil_eolico(n)
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo, carrier=tech, p_max_pu=perfil)
        elif tipo == "hidro":
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo, carrier="hydro",
                    p_max_pu=0.90, p_min_pu=0.30)
        elif tipo == "almacenamiento":
            net.add("StorageUnit", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, max_hours=4,
                    efficiency_store=BATTERY_EFFICIENCY,
                    efficiency_dispatch=BATTERY_EFFICIENCY,
                    cyclic_state_of_charge=True,
                    marginal_cost=costo, carrier="battery")
        else:
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo, carrier=tech)

    try:
        net.optimize(solver_name="highs")
    except AttributeError as e:
        if "objective_value" not in str(e):
            return {"exito": False, "error": str(e), "sistema": sistema}
    except Exception as e:
        return {"exito": False, "error": str(e), "sistema": sistema}

    if net.generators_t.p.empty:
        return {"exito": False, "error": "Sin resultados", "sistema": sistema}

    gen_t = net.generators_t.p
    costos_gen = net.generators["marginal_cost"]
    costo_total = float((gen_t * costos_gen).sum().sum())

    despacho = {}
    shedding_mw = np.zeros(n)
    for col in gen_t.columns:
        if "Shedding" in col:
            shedding_mw = gen_t[col].values
        else:
            nombre = col.replace(f"_{sistema}", "")
            despacho[nombre] = gen_t[col].values

    curtailment = {}
    if not net.generators_t.p_max_pu.empty:
        for col in net.generators_t.p_max_pu.columns:
            nombre = col.replace(f"_{sistema}", "")
            disponible = (net.generators_t.p_max_pu[col].values * net.generators.loc[col, "p_nom"])
            usado = gen_t[col].values if col in gen_t.columns else np.zeros(n)
            curt = np.maximum(0, disponible - usado)
            if curt.sum() > 0.1:
                curtailment[nombre] = curt

    bat_soc = bat_carga = bat_descarga = None
    if not net.storage_units_t.p.empty:
        for col in net.storage_units_t.p.columns:
            flujo = net.storage_units_t.p[col].values
            bat_carga = np.maximum(0, -flujo)
            bat_descarga = np.maximum(0, flujo)
            if not net.storage_units_t.state_of_charge.empty:
                bat_soc = net.storage_units_t.state_of_charge[col].values

    precio_marginal = _extraer_precio_marginal(net, sistema, n)

    return {
        "exito": True, "sistema": sistema, "n_horas": n,
        "costo_total_usd": costo_total, "despacho": despacho,
        "curtailment": curtailment, "shedding_mw": shedding_mw,
        "bateria_soc": bat_soc, "bateria_carga": bat_carga,
        "bateria_descarga": bat_descarga, "precio_marginal": precio_marginal,
        "demanda_mw": demanda_mw.values,
    }


def correr_despacho_completo(demandas, año_capacidad="2024",
                             costos_override=None, voll=VOLL_DEFAULT,
                             perfiles_vre=None, capacidades_df=None):
    if capacidades_df is None:
        capacidades = cargar_capacidades(año_capacidad)
    else:
        capacidades = capacidades_df

    resultados = {}
    for sistema, demanda in demandas.items():
        print(f"[PyPSA] Resolviendo {sistema}...")
        r = construir_y_resolver(sistema, demanda, capacidades,
                                 costos_override, voll, perfiles_vre=perfiles_vre)
        if r["exito"]:
            pm = r["precio_marginal"]
            pm_str = f"{pm.mean():.1f}" if pm is not None else "N/A"
            print(f"  OK - Costo: USD {r['costo_total_usd']:,.0f} | PM prom: {pm_str} USD/MWh")
        else:
            print(f"  ERROR: {r['error']}")
        resultados[sistema] = r
    return resultados
