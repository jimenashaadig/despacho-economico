"""
# =============================================================
# ARCHIVO: src/cenace/cenace_client.py
# PROPÓSITO: Cliente oficial para los Web Services de CENACE.
#
# Implementa:
#   - Batching automático en ventanas de 7 días (límite CENACE)
#   - Caché en disco (JSON) para no repetir llamadas
#   - Reintentos con backoff exponencial ante fallos
#   - Validación de calidad: huecos, duplicados, negativos
#
# Servicio usado: SWCAEZC
#   (Cantidades Asignadas de Energía por Zona de Carga)
# URL base: https://ws01.cenace.gob.mx:8082/SWCAEZC/SIM/
#
# FILOSOFÍA: Si no hay datos reales disponibles, se devuelve
#   DataFrame vacío. NUNCA se inventan datos.
# =============================================================
"""

import os
import json
import time
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import urllib3
urllib3.disable_warnings()  # Suprimir warnings de SSL en Mac

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# CONFIGURACIÓN
# ------------------------------------------------------------------

BASE_URL = "https://ws01.cenace.gob.mx:8082"
MAX_DAYS_PER_REQUEST = 7
MAX_RETRIES = 3
RETRY_BACKOFF = 2
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_REQUESTS = 1.5

SISTEMAS_VALIDOS = ["SIN", "BCA", "BCS"]
PROCESOS_VALIDOS = ["MDA", "MTR"]

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"


# ------------------------------------------------------------------
# CACHÉ EN DISCO
# ------------------------------------------------------------------

def _ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_key(sistema, proceso, fecha_ini, fecha_fin):
    raw = f"SWCAEZC_{sistema}_{proceso}_{fecha_ini}_{fecha_fin}"
    hashed = hashlib.md5(raw.encode()).hexdigest()[:10]
    return f"SWCAEZC_{sistema}_{proceso}_{fecha_ini}_{fecha_fin}_{hashed}.json"

def _load_from_cache(cache_file):
    path = CACHE_DIR / cache_file
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _save_to_cache(cache_file, data):
    _ensure_cache_dir()
    path = CACHE_DIR / cache_file
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"No se pudo guardar caché: {e}")


# ------------------------------------------------------------------
# LLAMADAS A LA API DE CENACE
# ------------------------------------------------------------------

def _build_url(sistema, proceso, fecha_ini, fecha_fin):
    """
    Construye la URL correcta para CENACE.
    Formato: BASE/SWCAEZC/SIM/SISTEMA/PROCESO/YYYY/MM/DD/YYYY/MM/DD/JSON
    """
    return "/".join([
        BASE_URL,
        "SWCAEZC", "SIM",
        sistema, proceso,
        fecha_ini.strftime("%Y"),
        fecha_ini.strftime("%m"),
        fecha_ini.strftime("%d"),
        fecha_fin.strftime("%Y"),
        fecha_fin.strftime("%m"),
        fecha_fin.strftime("%d"),
        "JSON"
    ])

def _fetch_with_retry(url):
    """
    Hace GET a la URL con reintentos y backoff exponencial.
    Devuelve JSON o lanza excepción.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                raise ValueError(f"CENACE status: {data.get('status')}")
            return data
        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise ConnectionError(f"Falló tras {MAX_RETRIES} intentos: {last_error}")

def _date_batches(fecha_ini, fecha_fin):
    """Divide rango en bloques de máximo 7 días."""
    current = fecha_ini
    while current <= fecha_fin:
        batch_end = min(current + timedelta(days=MAX_DAYS_PER_REQUEST - 1), fecha_fin)
        yield (current, batch_end)
        current = batch_end + timedelta(days=1)


# ------------------------------------------------------------------
# PARSEO DE RESPUESTA
# ------------------------------------------------------------------

def _parse_resultados(data):
    """
    Convierte JSON de CENACE a DataFrame limpio.
    CENACE devuelve los valores como strings — los convertimos a float.
    Si no hay datos válidos, devuelve DataFrame vacío (no inventa datos).
    """
    rows = []
    sistema = data.get("sistema", "")
    proceso = data.get("proceso", "")

    for resultado in data.get("Resultados", []):
        zona = resultado.get("zona_carga", "")
        for valor in resultado.get("Valores", []):
            try:
                rows.append({
                    "sistema":              sistema,
                    "proceso":              proceso,
                    "zona_carga":           zona,
                    "fecha":                valor.get("fecha", ""),
                    "hora":                 int(valor.get("hora", 0)),
                    "demanda_mdo_nodales":  float(valor.get("demanda_mdo_nodales", 0)),
                    "demanda_pml_zonales":  float(valor.get("demanda_pml_zonales", 0)),
                    "total_cargas":         float(valor.get("total_cargas", 0)),
                })
            except (ValueError, TypeError):
                continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Hora en CENACE va de 1-24; convertimos a timestamp real
    df["timestamp"] = pd.to_datetime(df["fecha"]) + pd.to_timedelta(df["hora"] - 1, unit="h")
    return df


# ------------------------------------------------------------------
# FUNCIÓN PRINCIPAL: fetch_demanda
# ------------------------------------------------------------------

def fetch_demanda(sistema, fecha_ini, fecha_fin, proceso="MDA",
                  use_cache=True, progress_callback=None):
    """
    Descarga demanda horaria real de CENACE con batching y caché.

    Parámetros:
        sistema     : 'BCS', 'BCA' o 'SIN'
        fecha_ini   : 'YYYY-MM-DD'
        fecha_fin   : 'YYYY-MM-DD'
        proceso     : 'MDA' (default) o 'MTR'
        use_cache   : True = usa caché en disco
        progress_callback: función(actual, total) para mostrar progreso

    Devuelve:
        DataFrame con datos reales, o DataFrame vacío si no hay datos.
        NUNCA devuelve datos inventados.
    """
    sistema = sistema.upper()
    proceso = proceso.upper()

    if sistema not in SISTEMAS_VALIDOS:
        raise ValueError(f"Sistema inválido: {sistema}. Usar: {SISTEMAS_VALIDOS}")
    if proceso not in PROCESOS_VALIDOS:
        raise ValueError(f"Proceso inválido: {proceso}. Usar: {PROCESOS_VALIDOS}")

    dt_ini = datetime.strptime(fecha_ini, "%Y-%m-%d")
    dt_fin = datetime.strptime(fecha_fin, "%Y-%m-%d")

    if dt_ini > dt_fin:
        raise ValueError("fecha_ini debe ser anterior a fecha_fin")

    batches = list(_date_batches(dt_ini, dt_fin))
    total = len(batches)
    all_dfs = []

    for i, (b_ini, b_fin) in enumerate(batches):
        b_ini_str = b_ini.strftime("%Y-%m-%d")
        b_fin_str = b_fin.strftime("%Y-%m-%d")
        cache_file = _cache_key(sistema, proceso, b_ini_str, b_fin_str)

        # Intentar caché primero
        if use_cache:
            cached = _load_from_cache(cache_file)
            if cached is not None:
                df_batch = _parse_resultados(cached)
                if not df_batch.empty:
                    all_dfs.append(df_batch)
                    if progress_callback:
                        progress_callback(i + 1, total)
                    continue

        # Llamar a CENACE
        url = _build_url(sistema, proceso, b_ini, b_fin)
        print(f"  [CENACE] {sistema} {b_ini_str} -> {b_fin_str}")
        try:
            data = _fetch_with_retry(url)
            if use_cache:
                _save_to_cache(cache_file, data)
            df_batch = _parse_resultados(data)
            if not df_batch.empty:
                all_dfs.append(df_batch)
        except ConnectionError as e:
            print(f"  [ERROR] {e}")
            # Fallback a caché aunque sea viejo
            cached = _load_from_cache(cache_file)
            if cached:
                df_batch = _parse_resultados(cached)
                if not df_batch.empty:
                    all_dfs.append(df_batch)

        if progress_callback:
            progress_callback(i + 1, total)

        if i < total - 1:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    if not all_dfs:
        print(f"  [N/A] Sin datos para {sistema}. Se mostrará N/A en la app.")
        return pd.DataFrame()

    df = pd.concat(all_dfs, ignore_index=True)
    return df.sort_values(["zona_carga", "timestamp"]).reset_index(drop=True)


# ------------------------------------------------------------------
# FUNCIÓN AUXILIAR: demanda total del sistema (suma de zonas)
# ------------------------------------------------------------------

def get_demanda_total_sistema(sistema, fecha_ini, fecha_fin,
                               proceso="MDA", use_cache=True,
                               progress_callback=None):
    """
    Devuelve la demanda TOTAL horaria del sistema (suma de todas las zonas).
    Columnas resultado: timestamp, total_cargas_mw, sistema
    """
    df = fetch_demanda(sistema, fecha_ini, fecha_fin, proceso,
                       use_cache, progress_callback)

    if df.empty:
        return pd.DataFrame()

    df_total = (
        df.groupby(["timestamp", "fecha", "hora"], as_index=False)
        .agg(total_cargas_mw=("total_cargas", "sum"))
    )
    df_total["sistema"] = sistema
    return df_total.sort_values("timestamp").reset_index(drop=True)


# ------------------------------------------------------------------
# FUNCIÓN DE CALIDAD: validar datos descargados
# ------------------------------------------------------------------

def validate_demand_data(df):
    """
    Analiza calidad de datos: huecos, duplicados, negativos, NaNs.
    Devuelve diccionario con reporte. No modifica los datos.
    """
    report = {
        "total_rows": len(df),
        "date_range": None,
        "missing_hours": 0,
        "duplicate_hours": 0,
        "negative_values": 0,
        "nan_values": 0,
        "coverage_pct": 0.0,
    }

    if df.empty:
        return report

    report["date_range"] = (
        str(df["timestamp"].min()),
        str(df["timestamp"].max()),
    )

    expected = pd.date_range(df["timestamp"].min(), df["timestamp"].max(), freq="h")
    actual = df["timestamp"].nunique()
    report["missing_hours"]   = max(0, len(expected) - actual)
    report["duplicate_hours"] = int(df.duplicated(subset=["timestamp"]).sum())
    report["coverage_pct"]    = round(actual / len(expected) * 100, 1) if expected.size > 0 else 0

    col = "total_cargas_mw" if "total_cargas_mw" in df.columns else "total_cargas" if "total_cargas" in df.columns else None
    if col:
        report["negative_values"] = int((df[col] < 0).sum())
        report["nan_values"]      = int(df[col].isna().sum())

    return report
