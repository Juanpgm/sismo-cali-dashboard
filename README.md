# Sismo Cali — August 2026 · Building Damage Dashboard

Emergency dashboard for EDE (building habitability) inspections after the
August 10, 2026 earthquake in Cali. Pure static frontend (no API/backend)
deployed to Vercel, fed by a Python pipeline that reads the raw survey Excel
from Google Drive.

**Production URL**: https://sismo-cali-dashboard.vercel.app

## Layout

```
scripts/
  prepare_basemaps.py   One-time: simplifies basemaps/ geojsons (12MB -> <1MB),
                        repairs mojibake barrio names. Run BEFORE refresh_data.py.
  refresh_data.py       Downloads the xlsx from Drive, applies the exact cleaning
                        from data_cleaning.ipynb, spatial-joins comuna/barrio,
                        writes web/data/inspections.json + meta.json.
  basemap_utils.py      Shared name extraction + mojibake repair logic.
web/                    Static site (Leaflet, no build step). Deployed as-is.
  data/                 Generated JSON/geojson consumed by the frontend.
basemaps/               Raw source geojsons (comunas, barrios, road axes).
data_cleaning.ipynb     Source of truth for the column cleaning contract.
```

## Refresh the data

```bash
# One-off refresh (uses Drive if reachable, falls back to the local xlsx):
python scripts/refresh_data.py

# Hourly refresh + automatic redeploy to Vercel production:
python scripts/refresh_data.py --loop 3600 --deploy
```

The "Actualizar datos" button in the web UI re-fetches the deployed JSON with
cache-busting; new data appears there after each `--deploy` (or manual
`vercel deploy --prod` from `web/`).

## Google Drive access (required for automatic refresh)

The Drive file is currently **not** shared publicly, so the script falls back
to the local xlsx. Enable one of:

1. **Public link** — share the Drive folder/file as "Anyone with the link can
   view". The script's public download path will then work with no credentials.
2. **Service account** — share the folder with
   `sismo-cali-reader@pmegeocode-1555299726775.iam.gserviceaccount.com`,
   download a JSON key for that service account, and set
   `GOOGLE_APPLICATION_CREDENTIALS=<path-to-key.json>` before running the
   script (requires `pip install google-api-python-client google-auth`).

## Deploy

```bash
cd web
vercel deploy --prod --yes
```

Project: `sismo-cali-dashboard` (Vercel account `juanpgzmz-8162`).

## Privacy

PII columns (`email`, `telefono`, `nombre_contacto`, `cod_predial_catastral`,
`matricula_profesional`) are excluded from the published JSON by the pipeline.
