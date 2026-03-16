import requests
import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://ws01.cenace.gob.mx:8082/SWCAEZC/SIM"
MAX_DAYS_PER_REQUEST = 7
PROCESO = "MDA"

# SIN tiene mas zonas — timeout mayor
TIMEOUTS = {"SIN": 90, "BCA": 45, "BCS": 45}

def _cache_path(sistema, fecha_ini, fecha_fin):
    return CACHE_DIR / f"SWCAEZC_{sistema}_{fecha_ini}_{fecha_fin}.json"

def _fetch_cenace(sistema, fecha_ini, fecha_fin):
    cache_file = _cache_path(sistema, fecha_ini, fecha_fin)
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                data = json.load(f)
            # Validar que el cache no esta vacio
            if data and data.get("Resultados"):
                return data
            else:
                cache_file.unlink()  # borrar cache invalido
        except Exception:
            cache_file.unlink()

    fi = datetime.strptime(fecha_ini, "%Y-%m-%d")
    ff = datetime.strptime(fecha_fin, "%Y-%m-%d")
    url = (f"{BASE_URL}/{sistema}/{PROCESO}/"
           f"{fi.year}/{fi.month:02d}/{fi.day:02d}/"
           f"{ff.year}/{ff.month:02d}/{ff.day:02d}/JSON")
    
    timeout = TIMEOUTS.get(sistema, 60)
    
    for intento in range(3):  # 3 intentos
        try:
            print(f"[CENACE] {sistema} {fecha_ini}-{fecha_fin} intento {intento+1}...")
            resp = requests.get(url, timeout=timeout, verify=False)
            resp.raise_for_status()
            data = resp.json()
            if data and data.get("Resultados"):
                with open(cache_file, "w") as f:
                    json.dump(data, f)
                return data
            else:
                print(f"[CENACE] Respuesta vacia para {sistema}")
        except requests.exceptions.Timeout:
            print(f"[CENACE] Timeout {sistema} intento {intento+1}")
            timeout += 30  # aumentar timeout en cada intento
        except Exception as e:
            print(f"[CENACE] Error {sistema}: {e}")
    return None

def _parsear_respuesta(data):
    if data is None:
        return pd.DataFrame()
    try:
        resultados = data.get("Resultados", [])
        if not resultados:
            return pd.DataFrame()
        registros = []
        for bloque in resultados:
            for v in bloque.get("Valores", []):
                # Intentar todos los campos posibles de MW
                mw = (v.get("total_cargas") or
                      v.get("carga_mw") or
                      v.get("demanda_mw") or
                      v.get("valor") or 0)
                try:
                    mw_float = float(mw)
                except (TypeError, ValueError):
                    mw_float = 0.0
                registros.append({
                    "fecha": v.get("fecha", ""),
                    "hora": int(v.get("hora", 0)),
                    "total_cargas_mw": mw_float
                })
        if not registros:
            return pd.DataFrame()
        df = pd.DataFrame(registros)
        df_agg = df.groupby(["fecha","hora"])["total_cargas_mw"].sum().reset_index()
        return df_agg.sort_values(["fecha","hora"]).reset_index(drop=True)
    except Exception as e:
        print(f"[CENACE] Parse error: {e}")
        return pd.DataFrame()

def _split_fechas(fecha_ini, fecha_fin):
    fi = datetime.strptime(fecha_ini, "%Y-%m-%d")
    ff = datetime.strptime(fecha_fin, "%Y-%m-%d")
    bloques = []
    actual = fi
    while actual <= ff:
        fin_bloque = min(actual + timedelta(days=MAX_DAYS_PER_REQUEST-1), ff)
        bloques.append((actual.strftime("%Y-%m-%d"), fin_bloque.strftime("%Y-%m-%d")))
        actual = fin_bloque + timedelta(days=1)
    return bloques

def get_demanda_total_sistema(sistema, fecha_ini, fecha_fin):
    dfs = []
    for fi, ff in _split_fechas(fecha_ini, fecha_fin):
        raw = _fetch_cenace(sistema.upper(), fi, ff)
        df = _parsear_respuesta(raw)
        if not df.empty:
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    resultado = pd.concat(dfs, ignore_index=True).sort_values(["fecha","hora"]).reset_index(drop=True)
    # Verificar que los datos son reales (no todos cero)
    if resultado["total_cargas_mw"].max() < 1.0:
        print(f"[CENACE] ADVERTENCIA: todos los valores son ~0 para {sistema}")
        return pd.DataFrame()
    return resultado

def validate_demand_data(df):
    if df.empty:
        return {"coverage_pct": 0, "missing_hours": None, "n_records": 0}
    n = len(df)
    esperadas = df["fecha"].nunique() * 24
    return {
        "coverage_pct": round(n/esperadas*100,1),
        "missing_hours": max(0, esperadas-n),
        "n_records": n
    }
