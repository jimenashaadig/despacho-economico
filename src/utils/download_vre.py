import requests
import numpy as np
import time

def descargar_perfil_vre(lat, lon, tipo, año, token, n_horas):
    """Descarga perfil VRE de Renewables.ninja. Retorna array numpy."""
    headers = {"Authorization": f"Token {token}"}
    if tipo == "Solar":
        params = {
            "lat": lat, "lon": lon,
            "date_from": f"{año}-01-01", "date_to": f"{año}-12-31",
            "dataset": "merra2", "capacity": 1.0,
            "system_loss": 0.1, "tracking": 0,
            "tilt": 35, "azim": 180, "format": "json",
        }
        url = "https://www.renewables.ninja/api/data/pv"
    else:
        params = {
            "lat": lat, "lon": lon,
            "date_from": f"{año}-01-01", "date_to": f"{año}-12-31",
            "dataset": "merra2", "capacity": 1.0,
            "height": 100, "turbine": "Vestas V90 2000", "format": "json",
        }
        url = "https://www.renewables.ninja/api/data/wind"

    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    raw = r.json()

    # La API puede devolver {"data": {"2024-01-01 00:00": {"electricity": 0.5}, ...}}
    # o {"data": {"2024-01-01 00:00": 0.5, ...}}
    data = raw.get("data", {})
    vals = []
    for v in data.values():
        if isinstance(v, dict):
            vals.append(float(v.get("electricity", v.get("cf", 0.0))))
        else:
            vals.append(float(v))

    perfil = np.array(vals, dtype=float)
    if len(perfil) == 0:
        return None
    if len(perfil) >= n_horas:
        return perfil[:n_horas]
    reps = int(np.ceil(n_horas / len(perfil)))
    return np.tile(perfil, reps)[:n_horas]
