# =============================================================
# ARCHIVO: src/cenace/fetcher.py
# PROPÓSITO: Conectarse a la API real de CENACE y descargar
#            demanda horaria para BCS, BCA y SIN.
#
# FUENTE OFICIAL: api.energia-mexico.org (SWEDREZC)
#   Servicio web que replica datos reales de CENACE sobre
#   Estimación de Demanda Real de Energía por Zona de Carga.
#
# FILOSOFÍA DE DATOS:
#   - Si API responde con datos reales → los usamos
#   - Si hay caché en disco → usamos el caché
#   - Si no hay nada → devolvemos None (NUNCA inventamos datos)
#   - En la app se mostrará "N/A" si no hay datos disponibles
# =============================================================

import requests
import pandas as pd
import os
from datetime import datetime, timedelta

# ------------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# ------------------------------------------------------------------

# URL base de la API (datos reales de CENACE, acceso público)
# SWEDREZC = Estimación de Demanda Real de Energía por Zona de Carga
API_BASE = "https://api.energia-mexico.org/SWEDREZC"

# Carpeta donde guardamos el caché (para no re-descargar siempre)
CACHE_DIR = "data/cache"

# Zonas de carga de CENACE que corresponden a cada sistema.
# Cada sistema eléctrico tiene una zona de carga principal.
ZONAS_POR_SISTEMA = {
    "BCS": "BAJA CALIFORNIA SUR",
    "BCA": "BAJA CALIFORNIA",
    "SIN": "PENINSULAR",   # zona representativa del SIN
}

# ------------------------------------------------------------------
# FUNCIÓN PRINCIPAL: Descargar demanda horaria
# ------------------------------------------------------------------

def descargar_demanda(sistema, fecha_inicio, fecha_fin):
    """
    Descarga demanda horaria real de CENACE para un sistema eléctrico.

    Parámetros:
        sistema     : 'BCS', 'BCA' o 'SIN'
        fecha_inicio: fecha en formato 'YYYY-MM-DD'
        fecha_fin   : fecha en formato 'YYYY-MM-DD'

    Regresa:
        DataFrame con columnas [timestamp, demanda_mw, sistema, fuente]
        o None si no hay datos (NUNCA inventa datos)
    """

    if sistema not in ZONAS_POR_SISTEMA:
        print(f"[ERROR] Sistema '{sistema}' no reconocido. Usa: {list(ZONAS_POR_SISTEMA.keys())}")
        return None

    # 1. Intentar cargar desde caché primero
    datos_cache = _cargar_cache(sistema, fecha_inicio, fecha_fin)
    if datos_cache is not None:
        print(f"[CACHE] Datos de {sistema} cargados desde caché ({fecha_inicio} -> {fecha_fin})")
        return datos_cache

    # 2. Si no hay caché, descargar de la API en bloques de 7 días
    print(f"[API] Descargando datos reales para {sistema} ({fecha_inicio} -> {fecha_fin})...")
    datos = _descargar_por_bloques(sistema, fecha_inicio, fecha_fin)

    if datos is None or datos.empty:
        print(f"[AVISO] No se obtuvieron datos reales.")
        print(f"        La app mostrara N/A en lugar de datos inventados.")
        return None

    # 3. Guardar en caché para la próxima vez
    _guardar_cache(sistema, fecha_inicio, fecha_fin, datos)
    print(f"[OK] {len(datos)} registros horarios descargados para {sistema}")
    return datos


# ------------------------------------------------------------------
# FUNCIÓN: Descargar en bloques de máximo 7 días
# ------------------------------------------------------------------

def _descargar_por_bloques(sistema, fecha_inicio, fecha_fin):
    """
    La API de CENACE permite máximo 7 días por consulta.
    Esta función divide el rango en bloques y los une.
    """
    inicio = datetime.strptime(fecha_inicio, "%Y-%m-%d")
    fin    = datetime.strptime(fecha_fin,    "%Y-%m-%d")

    todos = []
    actual = inicio

    while actual <= fin:
        bloque_fin = min(actual + timedelta(days=6), fin)

        bloque = _llamar_api(
            sistema,
            actual.strftime("%Y-%m-%d"),
            bloque_fin.strftime("%Y-%m-%d")
        )

        if bloque is not None and not bloque.empty:
            todos.append(bloque)
        else:
            print(f"  [AVISO] Sin datos para {actual.date()} -> {bloque_fin.date()}")

        actual = bloque_fin + timedelta(days=1)

    if not todos:
        return None

    resultado = pd.concat(todos, ignore_index=True)
    resultado = resultado.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    return resultado


# ------------------------------------------------------------------
# FUNCIÓN: Llamar a la API para un bloque específico
# ------------------------------------------------------------------

def _llamar_api(sistema, fecha_inicio, fecha_fin):
    """
    Hace la llamada HTTP real a la API.
    Si falla por cualquier razón, devuelve None. Nunca inventa datos.
    """
    zona = ZONAS_POR_SISTEMA[sistema]

    # Formato de fechas para la API: YYYY/MM/DD
    fi = fecha_inicio.replace("-", "/")
    ff = fecha_fin.replace("-", "/")

    url = f"{API_BASE}/{zona}/{fi}/{ff}/JSON"

    try:
        print(f"  -> Llamando: {url}")
        resp = requests.get(url, timeout=30)

        if resp.status_code != 200:
            print(f"  [ERROR HTTP] Codigo {resp.status_code}")
            return None

        datos_json = resp.json()
        return _parsear_respuesta(datos_json, sistema)

    except requests.exceptions.Timeout:
        print(f"  [ERROR] Timeout al conectar")
        return None
    except requests.exceptions.ConnectionError:
        print(f"  [ERROR] Sin conexion a internet")
        return None
    except Exception as e:
        print(f"  [ERROR] Error inesperado: {e}")
        return None


# ------------------------------------------------------------------
# FUNCIÓN: Convertir JSON de la API a DataFrame limpio
# ------------------------------------------------------------------

def _parsear_respuesta(datos_json, sistema):
    """
    Convierte el JSON de la API a un DataFrame estándar.
    Si no puede parsear, devuelve None (nunca inventa datos).
    """
    try:
        resultados = datos_json.get("Resultados", [])

        if not resultados:
            print(f"  [AVISO] Respuesta vacia de la API")
            return None

        filas = []
        for registro in resultados:
            fecha  = registro.get("fecha", "")
            hora   = registro.get("hora", "")
            valor  = registro.get("demanda", None)

            if fecha and hora and valor is not None:
                try:
                    ts = pd.to_datetime(f"{fecha} {int(float(hora)):02d}:00")
                    filas.append({
                        "timestamp":  ts,
                        "demanda_mw": float(valor),
                        "sistema":    sistema,
                        "fuente":     "CENACE_via_API"
                    })
                except (ValueError, TypeError):
                    continue

        if not filas:
            print(f"  [AVISO] No se pudieron parsear registros validos")
            return None

        return pd.DataFrame(filas)

    except Exception as e:
        print(f"  [ERROR] Error al parsear: {e}")
        return None


# ------------------------------------------------------------------
# FUNCIONES DE CACHÉ
# ------------------------------------------------------------------

def _nombre_cache(sistema, fecha_inicio, fecha_fin):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return f"{CACHE_DIR}/demanda_{sistema}_{fecha_inicio}_{fecha_fin}.csv"

def _guardar_cache(sistema, fecha_inicio, fecha_fin, datos):
    archivo = _nombre_cache(sistema, fecha_inicio, fecha_fin)
    datos.to_csv(archivo, index=False)
    print(f"[CACHE] Guardado en: {archivo}")

def _cargar_cache(sistema, fecha_inicio, fecha_fin):
    archivo = _nombre_cache(sistema, fecha_inicio, fecha_fin)
    if os.path.exists(archivo):
        df = pd.read_csv(archivo)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    return None


# ------------------------------------------------------------------
# FUNCIÓN DE DIAGNÓSTICO: Calidad de datos
# ------------------------------------------------------------------

def verificar_calidad(df):
    """
    Analiza la calidad de los datos descargados.
    Devuelve un reporte con estadísticas clave.
    Nunca modifica los datos originales.
    """
    if df is None or df.empty:
        return {"estado": "SIN_DATOS", "mensaje": "No hay datos para analizar"}

    horas_esperadas = pd.date_range(
        start=df["timestamp"].min(),
        end=df["timestamp"].max(),
        freq="H"
    )

    reporte = {
        "estado":            "OK",
        "total_registros":   len(df),
        "fecha_inicio":      str(df["timestamp"].min()),
        "fecha_fin":         str(df["timestamp"].max()),
        "valores_nulos":     int(df["demanda_mw"].isna().sum()),
        "valores_negativos": int((df["demanda_mw"] < 0).sum()),
        "duplicados":        int(df.duplicated(subset=["timestamp"]).sum()),
        "huecos_horarios":   max(0, len(horas_esperadas) - len(df)),
        "demanda_min_mw":    round(float(df["demanda_mw"].min()), 1),
        "demanda_max_mw":    round(float(df["demanda_mw"].max()), 1),
        "demanda_prom_mw":   round(float(df["demanda_mw"].mean()), 1),
    }

    if reporte["valores_nulos"] > 0 or reporte["huecos_horarios"] > 0:
        reporte["estado"] = "CON_ADVERTENCIAS"

    return reporte
