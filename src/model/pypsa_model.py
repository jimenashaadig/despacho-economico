# =============================================================
# ARCHIVO: src/model/pypsa_model.py
# PROPÓSITO: Despacho económico con PyPSA para BCS, BCA, SIN.
#
# PRECIO MARGINAL (shadow price):
#   Se extrae del dual value de la restricción "Bus-nodal-balance"
#   del modelo Linopy. Representa el costo de servir 1 MWh
#   adicional en cada hora — el precio que "marca el mercado".
#
# NOTA TÉCNICA: PyPSA 0.26 + linopy en Python 3.9 tiene un bug
#   donde network.objective falla con AttributeError. La solución
#   es capturar ese error y calcular el costo manualmente.
# =============================================================

import pypsa
import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
VOLL_DEFAULT = 5000.0
BATTERY_EFFICIENCY = 0.9


def _perfil_solar(n):
    """Perfil solar sintético: 0 de noche, pico al mediodía."""
    h = np.arange(n) % 24
    return np.clip(np.where((h>=6)&(h<=18), np.sin(np.pi*(h-6)/12), 0.0), 0, 1)

def _perfil_eolico(n):
    """Perfil eólico sintético: más viento de noche."""
    h = np.arange(n) % 24
    return np.clip(0.5 + 0.3*np.cos(np.pi*h/12), 0.1, 0.9)

def cargar_capacidades(año="2024"):
    """Carga CSV de capacidades instaladas por sistema y tecnología."""
    archivo = DATA_DIR / f"capacity_{año}.csv"
    if not archivo.exists():
        raise FileNotFoundError(f"No se encontró: {archivo}")
    return pd.read_csv(archivo, comment="#")

def _extraer_precio_marginal(net, sistema, n):
    """
    Extrae el precio marginal (shadow price) del balance de energía.
    El dual value de Bus-nodal-balance ES el precio marginal horario.
    Si no está disponible, lo aproxima desde el generador marginal.
    """
    try:
        dual = net.model.constraints["Bus-nodal-balance"].dual
        if hasattr(dual, 'values'):
            vals = dual.values.flatten()
            if len(vals) == n:
                return np.abs(vals)
        # Intentar extraer por bus
        if sistema in dual.coords.get("Bus-nodal-balance_dim_0", []):
            return np.abs(dual.sel(**{"Bus-nodal-balance_dim_0": sistema}).values)
        # Tomar el primer bus disponible
        return np.abs(dual.values.reshape(-1, n)[0])
    except Exception:
        pass

    # Fallback: precio marginal = costo del generador más caro despachado
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
    """
    Construye red PyPSA para un sistema y resuelve el despacho.

    Parámetros:
        sistema       : 'BCS', 'BCA' o 'SIN'
        demanda_mw    : Serie con demanda horaria en MW
        capacidades_df: DataFrame de capacity_2024.csv
        costos_override: dict {'CCGT_Gas': 55, ...} para sliders UI
        voll          : Penalización load shedding (USD/MWh)

    Retorna dict con: despacho, costo, precio_marginal, shedding, batería
    """
    n = len(demanda_mw)
    net = pypsa.Network()
    net.set_snapshots(pd.RangeIndex(n))

    # Bus independiente por sistema (sin interconexiones)
    net.add("Bus", sistema, carrier="AC")
    net.add("Load", f"Dem_{sistema}", bus=sistema, p_set=demanda_mw.values)

    # Load shedding: generador ficticio muy caro (último recurso)
    net.add("Generator", f"Shedding_{sistema}", bus=sistema,
            p_nom=float(demanda_mw.max())*2,
            marginal_cost=voll, carrier="shedding")

    # Agregar generadores y batería desde CSV de capacidades
    cap = capacidades_df[capacidades_df["sistema"]==sistema].copy()
    for _, row in cap.iterrows():
        tech  = str(row["tecnologia"])
        cap_mw = float(row["capacidad_mw"])
        costo  = float(row["costo_var_usd_mwh"])
        tipo   = str(row["tipo"])

        if costos_override and tech in costos_override:
            costo = float(costos_override[tech])
        if cap_mw == 0:
            continue

        if tipo == "baseload":
            # Nuclear y Geotermia: costo muy bajo, PyPSA las despacha primero.
            # Sin p_min_pu para evitar infeasibility en sistemas pequeños (BCS).
            fp = 0.85 if "Nuclear" in tech else 0.80
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo,
                    carrier=tech,
                    p_max_pu=fp)  # máximo = factor de planta

        elif tipo == "renovable":
            if perfiles_vre and sistema in perfiles_vre and tech in perfiles_vre.get(sistema,{}):
                perfil = perfiles_vre[sistema][tech][:n]
            elif "Solar" in tech:
                perfil = _perfil_solar(n)
            else:
                perfil = _perfil_eolico(n)
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo,
                    carrier=tech, p_max_pu=perfil)

        elif tipo == "hidro":
            # Hidro regulable: puede variar entre 30% y 90% de capacidad
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo,
                    carrier="hydro",
                    p_max_pu=0.90,
                    p_min_pu=0.30)

        elif tipo == "almacenamiento":
            net.add("StorageUnit", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, max_hours=4,
                    efficiency_store=BATTERY_EFFICIENCY,
                    efficiency_dispatch=BATTERY_EFFICIENCY,
                    cyclic_state_of_charge=True,
                    marginal_cost=costo, carrier="battery")

        else:
            # Térmicas: sin mínimo técnico para simplicidad del modelo LP
            net.add("Generator", f"{tech}_{sistema}", bus=sistema,
                    p_nom=cap_mw, marginal_cost=costo, carrier=tech)

    # Resolver optimización con HiGHS
    try:
        net.optimize(solver_name="highs")
    except AttributeError as e:
        if "objective_value" not in str(e):
            return {"exito": False, "error": str(e), "sistema": sistema}
        # Bug conocido de PyPSA 0.26 — la solución está disponible igual
    except Exception as e:
        return {"exito": False, "error": str(e), "sistema": sistema}

    if net.generators_t.p.empty:
        return {"exito": False, "error": "Sin resultados", "sistema": sistema}

    # ── Extraer resultados ──────────────────────────────────────────
    gen_t      = net.generators_t.p
    costos_gen = net.generators["marginal_cost"]

    # Costo total = suma(generacion × costo_marginal) todas las horas
    costo_total = float((gen_t * costos_gen).sum().sum())

    # Despacho por tecnología
    despacho    = {}
    shedding_mw = np.zeros(n)
    for col in gen_t.columns:
        nombre = col.replace(f"_{sistema}", "").replace("Shedding_", "")
        if "Shedding" in col:
            shedding_mw = gen_t[col].values
        else:
            despacho[nombre] = gen_t[col].values

    # Curtailment (renovable disponible pero no usado)
    curtailment = {}
    if not net.generators_t.p_max_pu.empty:
        for col in net.generators_t.p_max_pu.columns:
            nombre     = col.replace(f"_{sistema}", "")
            disponible = (net.generators_t.p_max_pu[col].values
                          * net.generators.loc[col, "p_nom"])
            usado  = gen_t[col].values if col in gen_t.columns else np.zeros(n)
            curt   = np.maximum(0, disponible - usado)
            if curt.sum() > 0.1:
                curtailment[nombre] = curt

    # Batería (SOC, carga, descarga)
    bat_soc = bat_carga = bat_descarga = None
    if not net.storage_units_t.p.empty:
        for col in net.storage_units_t.p.columns:
            flujo        = net.storage_units_t.p[col].values
            bat_carga    = np.maximum(0, -flujo)
            bat_descarga = np.maximum(0,  flujo)
            if not net.storage_units_t.state_of_charge.empty:
                bat_soc = net.storage_units_t.state_of_charge[col].values

    # Precio marginal (shadow price del balance de energía)
    precio_marginal = _extraer_precio_marginal(net, sistema, n)

    return {
        "exito":            True,
        "sistema":          sistema,
        "n_horas":          n,
        "costo_total_usd":  costo_total,
        "despacho":         despacho,
        "curtailment":      curtailment,
        "shedding_mw":      shedding_mw,
        "bateria_soc":      bat_soc,
        "bateria_carga":    bat_carga,
        "bateria_descarga": bat_descarga,
        "precio_marginal":  precio_marginal,
        "demanda_mw":       demanda_mw.values,
    }

def correr_despacho_completo(demandas, año_capacidad="2024",
                              costos_override=None, voll=VOLL_DEFAULT,
                              perfiles_vre=None):
    """
    Corre despacho económico para BCS, BCA y SIN.
    Parámetros:
        demandas      : dict {'BCS': Series, 'BCA': Series, 'SIN': Series}
        año_capacidad : '2024' o '2026'
        costos_override: dict de costos a modificar (sliders de UI)
        voll          : penalización de load shedding
    """
    capacidades = cargar_capacidades(año_capacidad)
    resultados  = {}
    for sistema, demanda in demandas.items():
        print(f"[PyPSA] Resolviendo {sistema}...")
        r = construir_y_resolver(sistema, demanda, capacidades,
                                  costos_override, voll,
                                  perfiles_vre=perfiles_vre)
        if r["exito"]:
            pm  = r["precio_marginal"]
            pm_str = f"{pm.mean():.1f}" if pm is not None else "N/A"
            print(f"  OK — Costo: USD {r['costo_total_usd']:,.0f} | "
                  f"PM prom: {pm_str} USD/MWh")
        else:
            print(f"  ERROR: {r['error']}")
        resultados[sistema] = r
    return resultados
