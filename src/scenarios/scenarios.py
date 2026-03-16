VOLL_BASE = 5000.0
ESCENARIOS = {
    "fuel_shock": {
        "nombre": "Fuel Price Shock",
        "descripcion": "Alza extrema de gas (x2.5), fuel oil (x2) y diesel (x2). Crisis de combustibles como Europa 2022.",
        "lección": "Cuando los fosiles se encarecen, el PML sube dramaticamente. Renovables no se ven afectadas.",
        "año_capacidad": "2024",
        "costos_override": {"CCGT_Gas":112.0,"Fuel_Oil":200.0,"Combustion_Interna":220.0,"Turbogas":250.0,"Diesel_Peaker":350.0,"Carbon":120.0},
        "voll": VOLL_BASE,
    },
    "renewables_boom": {
        "nombre": "Renewables Boom 2026",
        "descripcion": "Capacidades proyectadas 2026: +40% solar, +20% eolica, nuevas baterias, CCGT en BCS.",
        "lección": "Mas MW renovables no siempre bajan costos: sin flexibilidad hay curtailment.",
        "año_capacidad": "2026",
        "costos_override": None,
        "voll": VOLL_BASE,
    },
    "forced_outage": {
        "nombre": "Forced Outage",
        "descripcion": "Salida forzada del 60% de CCGT y 50% de combustion interna. Falla masiva termica.",
        "lección": "Sin generacion firme se usan peakers caros o shedding. El PML puede llegar al VOLL.",
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": VOLL_BASE,
        "derate": {"CCGT_Gas":0.40,"Combustion_Interna":0.50},
    },
    "add_storage": {
        "nombre": "Add Storage",
        "descripcion": "Baterias adicionales: BCS 50 MW, BCA 100 MW, SIN 500 MW.",
        "lección": "La bateria carga barato y descarga caro. Su valor depende del spread de precios.",
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": VOLL_BASE,
        "battery_override": {"BCS":50,"BCA":100,"SIN":500},
    },
    "scarcity_knob": {
        "nombre": "Scarcity Knob (VOLL alto)",
        "descripcion": "Salida forzada -50% CCGT con VOLL $12,000/MWh. Escasez real.",
        "lección": "El VOLL es politica. Alto: usa peakers carisimos. Bajo: prefiere shedding.",
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": 12000.0,
        "derate": {"CCGT_Gas":0.50},
    },
    "critical_minerals": {
        "nombre": "Boom Renovable sin Minerales Criticos",
        "descripcion": "Expansion 2026 solar/eolica sin baterias por escasez de litio.",
        "lección": "Sin almacenamiento la expansion renovable tiene rendimientos decrecientes.",
        "año_capacidad": "2026",
        "costos_override": None,
        "voll": VOLL_BASE,
        "battery_override": {"BCS":0,"BCA":0,"SIN":0},
    },
}

def aplicar_escenario(nombre_escenario, capacidades_df=None):
    if nombre_escenario not in ESCENARIOS:
        raise ValueError(f"Escenario '{nombre_escenario}' no existe.")
    esc = ESCENARIOS[nombre_escenario]
    params = {"año_capacidad":esc["año_capacidad"],"costos_override":esc.get("costos_override"),"voll":esc["voll"]}
    if capacidades_df is None:
        return params
    import pandas as pd
    cap = capacidades_df.copy()
    if "derate" in esc:
        for tech, factor in esc["derate"].items():
            cap.loc[cap["tecnologia"]==tech,"capacidad_mw"] *= factor
    if "battery_override" in esc:
        for sistema, mw in esc["battery_override"].items():
            mask = (cap["sistema"]==sistema) & (cap["tecnologia"]=="Bateria")
            if mask.any():
                cap.loc[mask,"capacidad_mw"] = mw
            else:
                cap = pd.concat([cap, pd.DataFrame([{"sistema":sistema,"tecnologia":"Bateria","capacidad_mw":mw,"costo_var_usd_mwh":1.0,"tipo":"almacenamiento"}])], ignore_index=True)
    params["capacidades_modificadas"] = cap
    return params

def listar_escenarios():
    return [(k, v["nombre"]) for k, v in ESCENARIOS.items()]
