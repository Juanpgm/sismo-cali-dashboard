# Setup — trabajar el proyecto en otra máquina

Esta carpeta contiene **dos repos de GitHub distintos**. Un solo `git pull` solo
trae el dashboard; el subrepo de integración es independiente y hay que clonarlo
aparte.

| Componente | Repo GitHub | Ubicación en la carpeta |
| --- | --- | --- |
| Dashboard (principal) | `Juanpgm/sismo-cali-dashboard` | raíz |
| Integración F1 (subrepo separado) | `Juanpgm/normalizador_data_sismo_cali` | `integracion_F1/` (gitignoreado por el repo padre) |

## 1. Clonar (máquina nueva)

```bash
cd ~/Documents/workspace          # o donde prefieras trabajar

# Repo principal (dashboard)
git clone https://github.com/Juanpgm/sismo-cali-dashboard.git seismic_disaster_data_analisys_cali
cd seismic_disaster_data_analisys_cali

# Subrepo de integración — va ANIDADO en integracion_F1/
git clone https://github.com/Juanpgm/normalizador_data_sismo_cali.git integracion_F1
```

## 2. Actualizar (máquina ya clonada)

```bash
# En la raíz (dashboard)
git pull

# En el subrepo
cd integracion_F1 && git pull && cd ..
```

## 3. Lo que NO viaja por git (recrear/copiar a mano)

Son gitignoreados a propósito (secretos y config local). **No están en GitHub** y
deben copiarse por un canal seguro, nunca commitearse:

- `.env.local` — variables de entorno / secretos.
- `credenciales_inspectores.csv` — datos sensibles.
- `*service-account*.json` / `*credentials*.json` — credenciales de Google/Firebase.
- `.vercel/` y `web/.vercel/` — vínculo del proyecto Vercel. Alternativa: correr
  `vercel link` en la máquina nueva para re-vincular en vez de copiar.
- `*.xlsx` con PII (el `.gitignore` ya permite las excepciones publicadas:
  `web/data/asignaciones.xlsx` y `web/data/inspections.xlsx`).

## 4. Estado de tooling de IA (por máquina, no viaja por git)

- **Memoria (engram)** e **índice de código (codebase-memory)** son locales a cada
  máquina. Tras clonar, re-indexar con `index_repository(repo_path='.', mode='full')`.
- El grafo del codebase se regenera con:
  `python scripts/build_codebase_graph.py <export.json>` (ver docstring del script;
  el export sale de `query_graph` de codebase-memory). Salida: `docs/codebase-graph.html`.
- El ADR del proyecto está en `docs/ADR.md` (espejo versionado del ADR del grafo).

## 5. Ejecutar

- **Dashboard**: es estático sin build. Servir `web/` con cualquier servidor
  (`python -m http.server` desde `web/`) o desplegar en Vercel (`outputDirectory: web`).
- **Tests JS** (patrón `node:assert`, sin framework): `node web/js/data.test.mjs`,
  `node web/js/charts.test.mjs`, `node web/js/utils.test.mjs`.
- **Pipeline / funciones serverless / integración**: ver el README de cada área y el
  subrepo `integracion_F1/`.

> Detalle de arquitectura, patrones y tradeoffs: **`docs/ADR.md`**.
