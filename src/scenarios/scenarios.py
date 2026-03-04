# =============================================================
# ARCHIVO: src/scenarios/scenarios.py
# PROPÓSITO: Define los 5 escenarios predefinidos (presets) del
#            simulador de despacho económico.
#
# Cada escenario cambia parámetros específicos y produce una
# lección clara sobre el sistema eléctrico.
#
# USO DESDE STREAMLIT:
#   from src.scenarios.scenarios import ESCENARIOS, aplicar_escenario
#   params = aplicar_escenario("fuel_shock")
#   resultado = correr_despacho_completo(demandas, **params)
# =============================================================

# ------------------------------------------------------------------
# COSTOS BASE (USD/MWh) — tabla de referencia del proyecto
# ------------------------------------------------------------------
COSTOS_BASE = {
    "CCGT_Gas":      45.0,
    "Carbon":        60.0,
    "Fuel_Oil":     100.0,
    "Diesel_Peaker": 150.0,
    "Hidro":          5.0,
    "Solar":          2.0,
    "Eolica":         2.0,
    "Bateria":        1.0,
}

VOLL_BASE = 5000.0  # USD/MWh — penalización base por load shedding

# ------------------------------------------------------------------
# DEFINICIÓN DE LOS 5 ESCENARIOS
# ------------------------------------------------------------------
ESCENARIOS = {

    # ── Escenario 1: Fuel Price Shock ─────────────────────────────
    "fuel_shock": {
        "nombre":      "⚡ Fuel Price Shock",
        "descripcion": (
            "Simula un alza de precios de gas y diésel (+80%). "
            "Muestra cómo los combustibles afectan el precio marginal "
            "y qué tecnología 'marca' el precio del mercado."
        ),
        "lección": (
            "Cuando el gas sube, el precio marginal sube aunque "
            "la demanda no cambie. Las renovables no se ven afectadas "
            "pero no hay suficientes para cubrir toda la demanda."
        ),
        "año_capacidad": "2024",
        "costos_override": {
            "CCGT_Gas":      81.0,   # +80% del base
            "Fuel_Oil":     180.0,   # +80%
            "Diesel_Peaker": 270.0,  # +80%
        },
        "voll": VOLL_BASE,
    },

    # ── Escenario 2: Renewables Boom 2026 ─────────────────────────
    "renewables_boom": {
        "nombre":      "🌞 Renewables Boom 2026",
        "descripcion": (
            "Activa capacidades proyectadas para 2026: +40% solar, "
            "+20% eólica. Muestra curtailment y necesidad de "
            "flexibilidad cuando hay exceso de renovables."
        ),
        "lección": (
            "Más MW renovables no siempre reducen costos: si no hay "
            "flexibilidad (almacenamiento, demanda flexible), el exceso "
            "se desperdicia (curtailment). El precio baja en horas solares "
            "pero puede subir en horas pico nocturnas."
        ),
        "año_capacidad": "2026",
        "costos_override": None,
        "voll": VOLL_BASE,
    },

    # ── Escenario 3: Forced Outage ─────────────────────────────────
    "forced_outage": {
        "nombre":      "🔴 Forced Outage",
        "descripcion": (
            "Simula la salida forzada de capacidad térmica clave (-60% CCGT). "
            "Muestra cómo aparece el load shedding y cuánto cuesta "
            "la confiabilidad."
        ),
        "lección": (
            "La pérdida de generación firme obliga a usar peakers caros "
            "o incurrir en shedding. El precio marginal puede llegar al VOLL. "
            "Demuestra el valor de la reserva operativa."
        ),
        "año_capacidad": "2024",
        "costos_override": {
            "CCGT_Gas":      45.0,
            "Fuel_Oil":     100.0,
            "Diesel_Peaker": 150.0,
        },
        "voll": VOLL_BASE,
        "derate_ccgt": 0.40,  # Solo usa 40% de la capacidad CCGT
    },

    # ── Escenario 4: Add Storage ───────────────────────────────────
    "add_storage": {
        "nombre":      "🔋 Add Storage",
        "descripcion": (
            "Duplica la capacidad de batería en todos los sistemas. "
            "Muestra cómo el almacenamiento reduce picos, aprovecha "
            "renovables y baja el costo total."
        ),
        "lección": (
            "La batería hace arbitraje: carga barata (renovables/noche) "
            "y descarga cara (picos). Su valor depende del spread de "
            "precios entre horas. Reduce curtailment y shedding."
        ),
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": VOLL_BASE,
        "battery_multiplier": 3.0,  # Triplica la batería
    },

    # ── Escenario 5: Scarcity Knob (VOLL) ─────────────────────────
    "scarcity_knob": {
        "nombre":      "📊 Scarcity Knob (VOLL)",
        "descripcion": (
            "Sube el VOLL (Value of Lost Load) de $5,000 a $10,000/MWh. "
            "Muestra cómo una política implícita cambia decisiones: "
            "el sistema acepta pagar más por no hacer shedding."
        ),
        "lección": (
            "El VOLL es una decisión de política, no un dato técnico. "
            "Con VOLL alto, el modelo usa peakers muy caros antes que "
            "hacer shedding. Con VOLL bajo, prefiere el shedding. "
            "Define implícitamente cuánto vale la confiabilidad."
        ),
        "año_capacidad": "2024",
        "costos_override": None,
        "voll": 10000.0,  # Doble del base
    },
}

# ------------------------------------------------------------------
# FUNCIÓN PRINCIPAL: Aplicar escenario
# ------------------------------------------------------------------

def aplicar_escenario(nombre_escenario: str, capacidades_df=None) -> dict:
    """
    Prepara los parámetros para correr un escenario específico.

    Parámetros:
        nombre_escenario: clave del escenario (ej: 'fuel_shock')
        capacidades_df  : DataFrame de capacidades (para modificar derate/battery)

    Retorna dict con: año_capacidad, costos_override, voll,
                      y capacidades_df modificado si aplica.
    """
    if nombre_escenario not in ESCENARIOS:
        raise ValueError(
            f"Escenario '{nombre_escenario}' no existe. "
            f"Opciones: {list(ESCENARIOS.keys())}"
        )

    esc = ESCENARIOS[nombre_escenario].copy()

    params = {
        "año_capacidad":  esc["año_capacidad"],
        "costos_override": esc.get("costos_override"),
        "voll":           esc["voll"],
    }

    # Modificar capacidades si el escenario lo requiere
    if capacidades_df is not None:
        import pandas as pd
        cap = capacidades_df.copy()

        # Escenario 3: Derate CCGT
        if "derate_ccgt" in esc:
            factor = esc["derate_ccgt"]
            mask = cap["tecnologia"] == "CCGT_Gas"
            cap.loc[mask, "capacidad_mw"] = cap.loc[mask, "capacidad_mw"] * factor
            params["capacidades_modificadas"] = cap

        # Escenario 4: Multiplicar batería
        elif "battery_multiplier" in esc:
            factor = esc["battery_multiplier"]
            mask = cap["tecnologia"] == "Bateria"
            cap.loc[mask, "capacidad_mw"] = cap.loc[mask, "capacidad_mw"] * factor
            params["capacidades_modificadas"] = cap

    return params


def get_info_escenario(nombre_escenario: str) -> dict:
    """
    Devuelve nombre, descripción y lección de un escenario.
    Útil para mostrar en la UI de Streamlit.
    """
    if nombre_escenario not in ESCENARIOS:
        return {}
    esc = ESCENARIOS[nombre_escenario]
    return {
        "nombre":      esc["nombre"],
        "descripcion": esc["descripcion"],
        "leccion":     esc["lección"],
    }


def listar_escenarios() -> list:
    """Devuelve lista de (clave, nombre) para mostrar en UI."""
    return [(k, v["nombre"]) for k, v in ESCENARIOS.items()]
