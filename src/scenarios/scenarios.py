COSTOS_BASE = {
    "CCGT_Gas": 45.0, "Carbon": 60.0, "Fuel_Oil": 100.0,
    "Combustion_Interna": 110.0, "Turbogas": 120.0,
    "Diesel_Peaker": 150.0, "Hidro": 5.0,
    "Solar": 2.0, "Eolica": 2.0, "Bateria": 1.0,
}

VOLL_BASE = 5000.0

ESCENARIOS = {
    "fuel_shock": {
        "nombre": "Fuel Price Shock",
        "descripcion": (
            "Simula un alza extrema de precios de gas natural (x2.5), "
            "fuel oil (x2) y diesel (x2). Refleja crisis de suministro "
            "de combustibles fosiles como la observada en Europa 2022."
        ),
        "lección": (
            "Cuando los combustibles fosiles se encarecen, el precio "
            "marginal sube dramaticamente. Las renovables y nucleares "
            "no se ven afectadas. Demuestra el valor de diversificar."
        ),
        "año_capacidad": "2024",
        "costos_override": {
            "CCGT_Gas": 112.0, "Fuel_Oil": 200.0,
            "Combustion_Interna": 220.0, "Turbogas": 250.0,
            "Diesel_Peaker": 350.0, "Carbon": 120.0,
        },
        "voll": VOLL_BASE,
    },
    "renewables_boom": {
        "nombre": "Renewables Boom 2026",
        "descripcion": (
            "Activa capacidades proyectadas para 2026 del PRODESEN: "
            "+40% solar, +20% eolica, nuevas baterias, y ciclo combinado "
            "de gas en BCS."
        ),
        "lección": (
            "Mas MW renovables no siempre reducen costos: sin flexibilidad "
            "el exceso se desperdicia (curtailment). El precio baja en horas "
            "solares pero puede subir en picos nocturnos."
        ),
        "año_capacidad": "2026",
        "costos_override": None,
        "voll": VOLL_BASE,
    },
    "forced_outage": {
        "nombre": "Forced Outage",
        "descripcion": (
            "Simula la salida forzada del 60% de CCGT y 50% de combustion "
            "interna. Representa una falla masiva del parque termico."
        ),
        "lección": (
            "La perdida de generacion firme obliga a usar peakers caros "
            "o incurrir en shedding. El PML puede alcanzar el VOLL."
        ),
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": VOLL_BASE,
        "derate": {"CCGT_Gas": 0.40, "Combustion_Interna": 0.50},
    },
    "add_storage": {
        "nombre": "Add Storage",
        "descripcion": (
            "Agrega baterias: BCS 50 MW, BCA 100 MW, SIN 500 MW. "
            "Demuestra el valor del arbitraje temporal."
        ),
        "lección": (
            "La bateria carga barato (renovables) y descarga caro (picos). "
            "Su valor depende del spread de precios entre horas."
        ),
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": VOLL_BASE,
        "battery_override": {"BCS": 50, "BCA": 100, "SIN": 500},
    },
    "scarcity_knob": {
        "nombre": "Scarcity Knob (VOLL alto)",
        "descripcion": (
            "Combina salida forzada (-50% CCGT) con VOLL alto ($12,000/MWh). "
            "Crea escasez real para que el VOLL tenga efecto visible."
        ),
        "lección": (
            "El VOLL es decision de politica. Con VOLL alto el modelo usa "
            "peakers carisimos antes de hacer shedding. Con VOLL bajo "
            "prefiere el shedding."
        ),
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": 12000.0,
        "derate": {"CCGT_Gas": 0.50},
    },
    "critical_minerals": {
        "nombre": "Boom Renovable sin Minerales Criticos",
        "descripcion": (
            "Escenario 2026 con expansion solar/eolica pero sin baterias "
            "por escasez de litio y minerales criticos."
        ),
        "lección": (
            "Sin almacenamiento la expansion renovable tiene rendimientos "
            "decrecientes: mas curtailment, mas dependencia de peakers."
        ),
        "año_capacidad": "2026",
        "costos_override": None,
        "voll": VOLL_BASE,
        "battery_override": {"BCS": 0, "BCA": 0, "SIN": 0},
    },
}


def aplicar_escenario(nombre_escenario, capacidades_df=None):
    if nombre_escenario not in ESCENARIOS:
        raise ValueError(f"Escenario '{nombre_escenario}' no existe. Opciones: {list(ESCENARIOS.keys())}")
    esc = ESCENARIOS[nombre_escenario]
    params = {
        "año_capacidad": esc["año_capacidad"],
        "costos_override": esc.get("costos_override"),
        "voll": esc["voll"],
    }
    if capacidades_df is None:
        return params
    import pandas as pd
    cap = capacidades_df.copy()
    if "derate" in esc:
        for tech, factor in esc["derate"].items():
            mask = cap["tecnologia"] == tech
            cap.loc[mask, "capacidad_mw"] = cap.loc[mask, "capacidad_mw"] * factor
    if "battery_override" in esc:
        for sistema, mw in esc["battery_override"].items():
            mask = (cap["sistema"] == sistema) & (cap["tecnologia"] == "Bateria")
            if mask.any():
                cap.loc[mask, "capacidad_mw"] = mw
            else:
                nueva = pd.DataFrame([{"sistema": sistema, "tecnologia": "Bateria",
                                       "capacidad_mw": mw, "costo_var_usd_mwh": 1.0,
                                       "tipo": "almacenamiento"}])
                cap = pd.concat([cap, nueva], ignore_index=True)
    params["capacidades_modificadas"] = cap
    return params


def get_info_escenario(nombre_escenario):
    if nombre_escenario not in ESCENARIOS:
        return {}
    esc = ESCENARIOS[nombre_escenario]
    return {"nombre": esc["nombre"], "descripcion": esc["descripcion"], "leccion": esc["lección"]}


def listar_escenarios():
    return [(k, v["nombre"]) for k, v in ESCENARIOS.items()]
