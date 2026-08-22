# Architecture Decision Record — sismo-cali-dashboard

> Registro de arquitectura del proyecto. Espejo versionado del ADR persistido en
> el grafo de codebase-memory (`manage_adr`). Mantener ambos en sync: al cambiar
> este archivo, actualizar también el grafo con `manage_adr(mode='update')`.

## PURPOSE

Plataforma de datos para la evaluación de daños en edificaciones (EDE/EDAN) tras el sismo de Cali, Colombia (agosto 2026). Su núcleo es un dashboard de habitabilidad post-sismo usado por respondedores de emergencia y funcionarios (DAGMA/UNGRD), a menudo en campo y sobre conexiones lentas. Los datos de inspección se refrescan cada hora desde un Google Sheet (EDAN-F3) y APIs oficiales, y se publican como JSON estático. Invariante rectora: la calidad y completitud de los datos NUNCA se sacrifica por rendimiento.

## STACK

- **Dashboard (web/)**: JavaScript vanilla con ES modules cargados directo por el navegador — SIN build step. Librerías desde CDN: Leaflet + leaflet.heat (mapa), Chart.js (gráficos), SheetJS/xlsx (export, lazy-load on click).
- **Auth**: Firebase Auth (proyecto `dagma-85aad`). Rol = proveedor: email/password → admin; Google @cali.gov.co → viewer (deshabilitado por ahora). Firestore aloja la colección separada `inspecciones_israel` (lectura pública).
- **Serverless (api/)**: funciones Node en Vercel — `/api/refresh` (dispara el pipeline, verifica token), `/api/reportados` (proxy a API atencionsismo con caché CDN), `/api/stickers` (gestión de inspectores vía firebase-admin).
- **Pipeline (scripts/, Python)**: `refresh_data.py` y afines generan `web/data/*.json` desde el Sheet EDAN-F3, capas ArcGIS/Survey123 y EXIF GPS de fotos; cron horario (Railway) commitea `chore: refresh dashboard data (auto)`.
- **Deploy**: Vercel git-connected, `outputDirectory: web`, auto-deploy en push a `main`. `vercel.json` define headers Cache-Control.
- **Subproyectos independientes**: `formulario/` (formulario ATC-20 de campo, Firebase, con demo-mock + e2e Playwright) e `integracion_F1/` (repo aparte EDAN+Visitas → Sheets, indexado como proyecto separado).

## ARCHITECTURE

Flujo de datos: **pipeline Python → JSON estático en web/data/ → Store cliente (pub/sub, sin framework) → filtros / KPIs / mapa / gráficos / tabla**. La detección de comunidades sobre el grafo de llamadas confirma los módulos de facto del dashboard:

- **app-shell** (`loadAndRender`, `onStoreChange`, `wireMapControls`, `triggerRefresh`, `switchView`): orquestación y suscripción al Store.
- **data store** (`Store.load`, `applyFilters`): carga meta.json + inspections.json + Firestore Israel en paralelo; deriva campos en carga (`suspension_servicios`, `n_pisos_rango`); índice de búsqueda por registro.
- **charts** (`renderStatistics`, `upsertChart`, `baseOptions`, `themeColor`): ~13 instancias Chart.js.
- **map** (`renderPoints`, `renderChoropleth`, `renderHeat`, `interpolateRamp`): capas Leaflet; puntos = `L.circleMarker` con encoding en radio (size-by) Y color (color-by).
- **KPIs** (`renderKpis`, `computeKpis`, `habCode`), **table** (`renderTable`, `openDetailModal`), **auth + israel-source** (comparten `getFirebaseApp`), **stickers**, y **api** (validación: `isValidCedula`, `isValidCodigo`).

## PATTERNS

- **Store pub/sub minimalista sin framework**: estado global mutable + `subscribe`/`notify`; una sola fuente de verdad para campos filtrables (`FILTER_FIELDS`).
- **Campos derivados en carga**, no en origen: `suspension_servicios` (colapso + no-habitable I1-I3, espejo del pipeline), `n_pisos_rango` (buckets de 3, outliers >60 descartados).
- **Funciones puras en utils.js** testeables con `node:assert` (`bucketNpisos`, `suspensionServicios`, `bustParams`); son la única capa unit-testeable porque data.js arrastra el import del SDK de Firebase por especificador `https://` bare (no resoluble por el ESM loader de Node).
- **Fetch consciente de caché**: sin cache-bust en carga normal (la caché CDN de `vercel.json` manda); `?t=` + `no-store` solo en refresh manual / retry / poll.
- **Carga diferida de librerías pesadas** (xlsx ~1MB) solo al primer clic; init de datos en paralelo con el boot de Firebase Auth.
- **Firebase app centralizada** (`getFirebaseApp`, guardada por `getApps().length`): auth.js e israel-source.js la inicializan en cualquier orden sin doble-init.
- **Render idempotente**: charts se mutan in-place con `update()` en cambio de filtro; `destroy+recreate` reservado para cambio de tema (`resetCharts`) y plugins con closure sobre datos (chart-suspension). Tabla con listener delegado en `tbody` + `content-visibility` en filas.

## TRADEOFFS

- **Sin build step**: máxima simplicidad y deploy trivial, a costa de no tener bundling/minificación/tree-shaking (se mitiga con CDN + lazy-load).
- **JSON estático públicamente accesible por URL**: el login gatea la UI y el trigger de refresh, NO los archivos de datos crudos (ceiling documentado en auth.js).
- **Sin CI test gate**: validación manual + self-checks `node:assert` por archivo (no hay Jest/Vitest ni scripts en package.json). La validación de headers Cache-Control es solo-prod (`curl -sI`).
- **Escala actual ~850 registros**: no se virtualiza la tabla (más allá de content-visibility) ni se clusterizan markers — el clustering aplanaría el encoding color-by/size-by, violando la invariante de datos. Upgrade documentado si el dataset crece a miles.
- **Fechas corruptas en origen** (`##########` en raw_data): la serie de tiempo es parcialmente irrecuperable en código.
- **Workflow push-directo-a-main**: sin ramas de feature; el cron horario y los cambios de app conviven (rebase siempre limpio porque el bot solo toca web/data/*.json).

## PHILOSOPHY

Calidad de datos por encima de rendimiento, siempre. Cambios que preservan comportamiento (misma data mostrada antes y después). Laziness deliberada (YAGNI): la solución más simple que funciona a la escala actual, con techos y rutas de upgrade documentados en comentarios, no construidos por adelantado. Validación que abarca local Y producción. El humano dirige, la IA ejecuta; las decisiones y gotchas se persisten en memoria (engram + el grafo de codebase-memory) para sobrevivir entre sesiones.
