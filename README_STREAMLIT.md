# ⚡ Instrucciones para el equipo Streamlit

## PASO 1: Instalar dependencias
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## PASO 2: Correr la app
```bash
streamlit run app/main_app.py
```

## API del backend (lo que pueden importar)
```python
from src.cenace.cenace_client import get_demanda_total_sistema
from src.model.pypsa_model import correr_despacho_completo, cargar_capacidades
from src.scenarios.scenarios import ESCENARIOS, aplicar_escenario, listar_escenarios
```

## Estructura de resultados
```python
resultados = correr_despacho_completo(demandas)
r = resultados["BCS"]
r["costo_total_usd"]   # float
r["precio_marginal"]   # np.array (MW/hora)
r["despacho"]          # dict {tecnologia: np.array}
r["shedding_mw"]       # np.array
r["bateria_soc"]       # np.array o None
```
