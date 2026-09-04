# Tasks: Per-Record PDF Report

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~480-580 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Pure `buildReportDocDefinition` builder + unit tests | PR 1 | `node web/js/report.test.mjs` | N/A — pure function, no DOM/network | Delete `report.js` + `report.test.mjs` |
| 2 | Asset gathering (photos/firma/map dataURLs), downscale guard, pdfmake lazy-load, `generarInformePdf` | PR 2 | `node web/js/report.test.mjs` | Manual: browser console, fetch one ArcGIS attachment + one CARTO tile, `canvas.toDataURL()` | Revert PR 2 additions to `report.js`; PR 1 builder unaffected |
| 3 | UI wiring: toolbar button in `table.js`, admin CSS gating | PR 3 | N/A — no `table.js` test file today | Manual: open detail modal as admin and non-admin, click button | Remove button markup + CSS rule; `report.js` unaffected |

## Phase 0: CORS Spike (blocking, before Phase 1-2)

- [x] 0.1 In a scratch script or browser console: `fetch()` one ArcGIS attachment URL (`{SURVEY_LAYER_URL}/{objectId}/attachments/{id}`) and one CARTO tile URL (`basemapTileUrl()`), draw each to an offscreen `<canvas>`, call `toDataURL()`.
  - PASS (no `SecurityError`, dataURL non-empty): proceed with client-side fetch→dataURL embedding for photos/firma/map in Phase 2 as designed.
  - FAIL (tainted canvas / `SecurityError`): skip dataURL fetch for the blocked source; ship placeholder+source-link only for that image type in Phase 2 and flag to user before continuing (do not silently expand scope to a proxy).
  - **Result: PASS.** No browser available; verified via real `curl -D -` responses with a cross-origin `Origin` header — both the ArcGIS attachment endpoint and the CARTO tile endpoint return `Access-Control-Allow-Origin: *`. See design.md → "CORS Spike Result" for full evidence.

## Phase 1: Pure Builder (PR 1)

- [x] 1.1 Create `web/js/report.js` — `buildReportDocDefinition(record, {photos, signatures, mapImage})`: header (title, codigo/ObjectID, `downloadStamp()` date, disclaimer); one table section per non-empty `DETAIL_GROUPS` entry using `labelForField`/`formatValue`/`barrioVeredaDisplay`; photos section; separate firma/signatures section; map section. Each image slot: `{dataURL}` → `{image:...}`; `{dataURL:null, sourceUrl}` → placeholder text+link node. *(Requirement: Single-record PDF content)*
- [x] 1.2 Guard: cap photos at `MAX_PHOTOS=12`, append "N fotos adicionales no incluidas" note when exceeded.
- [x] 1.3 Create `web/js/report.test.mjs` (node:assert, mirrors `web/js/utils.test.mjs` style): header has codigo+fecha+disclaimer; one section per populated `DETAIL_GROUPS`; null image → placeholder node with `sourceUrl`; >12 photos → capped + note; empty photos/signatures → section omitted/marked, no throw. *(Scenarios: sparse-data record, one photo fails, location map fails)*

## Phase 2: Asset Gathering + PDF Generation (PR 2, depends on Phase 1)

- [ ] 2.1 Extract a shared attachment-URL helper from `loadPhotos` (table.js:241-260); add `gatherAssets(objectId)` in `report.js` — fetch `attachments?f=json`, split `firma*` (regex from table.js:249) vs photos, resolve each to a dataURL via fetch→blob→`FileReader`, or `{dataURL:null, sourceUrl}` on any failure. *(Requirement: per-image graceful degradation)*
- [ ] 2.2 Add downscale helper: canvas resize to ≤1000px width, JPEG quality 0.7, before embedding each resolved dataURL.
- [ ] 2.3 Add locator map: 3x3 CARTO tile grid on offscreen canvas (`crossOrigin='anonymous'`, `basemapTileUrl()` from utils.js:672) + marker, `toDataURL()`; `{dataURL:null}` on failure or missing/invalid coords.
- [ ] 2.4 Add `loadPdfmake()` lazy-loader in `report.js` following `loadXlsx()` (utils.js:825-836): load `pdfmake.min.js` + `vfs_fonts.js` 0.2.20 from jsDelivr on first call, cache the promise.
- [ ] 2.5 Add `generarInformePdf(record)`: `gatherAssets` → `buildReportDocDefinition` → `loadPdfmake()` → `createPdf(def).download(filename)`; filename `informe_EDE_${codigo||ObjectID}_${downloadStamp().slug}.pdf`; whole flow wrapped in try/catch, no partial file on error. *(Requirement: generation UX and error handling)*

## Phase 3: UI Wiring (PR 3, depends on Phase 2)

- [ ] 3.1 `table.js` `openDetailModal` (table.js:412): add `#detail-report-btn` in the modal toolbar, admin-gated, calling `generarInformePdf(record)`; disable + "Generando..." while running, re-enable after; on catch, `showToast('No se pudo generar el informe PDF.','error')` (Spanish), modal stays open. *(Requirement: admin-only trigger, generation UX)*
- [ ] 3.2 `styles.css`: add `body:not([data-role="admin"]) #detail-report-btn{display:none}` next to the existing admin-gating block (styles.css:1627-1633); add busy/disabled style.
- [ ] 3.3 `web/index.html`: add a comment noting pdfmake stays lazy-loaded — no `<script>` tag added.

## Phase 4: Manual Verification

- [ ] 4.1 Generate PDF for a record with photos + firma attachments — verify header, all populated sections, photos section, separate signatures section, map image.
- [ ] 4.2 Generate PDF for a record with no photos/signatures — verify success, sections omitted/marked, no error.
- [ ] 4.3 Simulate one failed image fetch (block one attachment URL) — verify PDF still completes, with placeholder+source link for that image and others intact.
- [ ] 4.4 Confirm button hidden for non-admin role, visible and working for admin.
