import requests
import numpy as np

def descargar_perfil_vre(lat, lon, tech, año, token, n_horas):
    try:
        if "Solar" in tech:
            url = "https://www.renewables.ninja/api/data/pv"
            params = {"lat":lat,"lon":lon,"date_from":f"{año}-01-01","date_to":f"{año}-12-31","dataset":"merra2","capacity":1.0,"system_loss":0.1,"tracking":0,"tilt":35,"azim":180,"format":"json","raw":"true"}
        else:
            url = "https://www.renewables.ninja/api/data/wind"
            params = {"lat":lat,"lon":lon,"date_from":f"{año}-01-01","date_to":f"{año}-12-31","dataset":"merra2","capacity":1.0,"height":100,"turbine":"Vestas V90 2000","format":"json","raw":"true"}
        headers = {"Authorization": f"Token {token}"}
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        vals = []
        for v in data.get("data",{}).values():
            vals.append(float(v.get("electricity", v.get("cf",0.0)) if isinstance(v,dict) else v))
        if not vals:
            return None
        arr = np.clip(np.array(vals), 0.0, 1.0)
        if len(arr) >= n_horas:
            return arr[:n_horas]
        return np.tile(arr, (n_horas//len(arr))+1)[:n_horas]
    except Exception as e:
        print(f"[VRE] Error: {e}")
        return None
