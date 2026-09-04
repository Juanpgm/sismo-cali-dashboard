# Design: Per-record PDF report from the detail modal

## Technical Approach

Client-side only, mirroring the existing `loadXlsx()` lazy-CDN precedent (`web/js/utils.js:825`). A new self-contained `web/js/report.js` exposes one entry point `generarInformePdf(record)`. `openDetailModal` (`web/js/table.js:412`) renders an admin-only "Generar informe PDF" button in the modal toolbar and wires it to that entry point. The report reuses `DETAIL_GROUPS`/`labelForField`/`formatValue`/`barrioVeredaDisplay` for fields, the ArcGIS attachments endpoint (same URLs as `loadPhotos`) for photos + `firma*` signatures, and record `x`/`y` coords for a composed locator image. PDF assembly uses pdfmake; every image path degrades to a placeholder so the PDF always completes.

## Architecture Decisions

| Decision | Choice | Alternatives rejected | Rationale |
|---|---|---|---|
| PDF library | **pdfmake 0.2.20** via jsDelivr, lazy-loaded (`pdfmake.min.js` + `vfs_fonts.js`) | jsPDF (manual x/y layout); print-CSS `window.print()`; server-side | Declarative content model (`content[]`, tables, auto page breaks, `{image: dataURL}`) maps 1:1 onto `DETAIL_GROUPS`, shrinking the diff. jsPDF needs hand-rolled layout. Lazy load = zero cost until clicked. |
| Module structure | New `web/js/report.js`, one export `generarInformePdf(record)`; pure `buildReportDocDefinition(record, assets)` inside | Inline in `table.js`/`main.js` | Keeps `table.js` diff to a button + one call; isolates net-new code; pure builder is unit-testable. |
| Image embedding | `fetch()→blob→FileReader.readAsDataURL`, then canvas downscale | `<img>`+canvas (taint risk); byte proxy | Reuses public no-auth endpoint already used by `loadPhotos`. Spike verifies CORS pixel access first. |
| Locator map | Hand-composed 3×3 CARTO tile grid on offscreen canvas (`crossOrigin='anonymous'`), marker drawn, `toDataURL` | leaflet-image (new dep, needs crossOrigin tiles); static-map API (new key/CORS); Leaflet DOM snapshot | ~30 lines, no new dependency. `basemaps.cartocdn.com` sends `ACAO: *`. Reuses `basemapTileUrl()` + tile math. |
| Admin gating | Render button only when `isAdmin()`; CSS `body:not([data-role="admin"]) #detail-report-btn{display:none}`; guard in handler | Ungated (like `#datos-download`) | Mirrors `#transito-download` precedent (`styles.css:1627`, `main.js:584`). Defense in depth. |

## Data Flow

    record ──► buildReportDocDefinition(record, assets)
       │           header: title, código, fecha (downloadStamp), disclaimer
       │           groups: DETAIL_GROUPS → labelForField/formatValue → tables
       │           fotos:  gatherAssets() ─┐
       │           firmas: firma* split ───┤ fetch→blob→dataURL→downscale
       │           mapa:   composeTiles ───┘ (each: dataURL | placeholder)
       └──► pdfmake.createPdf(def).download(filename)

`gatherAssets(objectId)` hits `{SURVEY_LAYER_URL}/{objectId}/attachments?f=json` (extract the URL/regex from `loadPhotos` into a shared helper to avoid duplication), splits `firma*` (regex `/^firma/i`) from photos, and resolves each to `{dataURL|null, sourceUrl}`. Null → placeholder box with source link.

## File Changes

| File | Action | Description |
|---|---|---|
| `web/js/report.js` | Create | `generarInformePdf`, `buildReportDocDefinition`, tile compose, image→dataURL, downscale guard, lazy `loadPdfmake()` |
| `web/js/report.test.mjs` | Create | node:assert unit tests of the pure builder |
| `web/js/table.js` | Modify | Admin-gated button in `openDetailModal` toolbar → `generarInformePdf(record)`; export shared attachment-URL helper from `loadPhotos` |
| `web/index.html` | Modify | (Optional) comment only — pdfmake stays lazy-loaded, no `<script>` tag needed |
| `web/styles.css` | Modify | Hide `#detail-report-btn` for non-admin + loading state |

## Interfaces / Contracts

```js
// Pure, no I/O — unit-testable
buildReportDocDefinition(record, { photos, signatures, mapImage }) → pdfmakeDocDefinition
// photos/signatures: [{ dataURL|null, sourceUrl }]; mapImage: { dataURL|null, lat, lon }
generarInformePdf(record) → Promise<void>   // orchestrates fetch+build+download
```

Guard: `MAX_PHOTOS = 12`; each photo downscaled to ≤1000px width, JPEG q0.7 via canvas; overflow noted as "N fotos adicionales no incluidas". Filename: `informe_EDE_${record.codigo || record.ObjectID}_${downloadStamp().slug}.pdf`.

Failure UX: button → disabled + "Generando…" during run; `try/catch` surfaces `showToast('No se pudo generar el informe PDF.', 'error')` and restores the button; modal untouched.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `buildReportDocDefinition` given a record fixture | `web/js/report.test.mjs` (node:assert): header has código+fecha+disclaimer; a section per non-empty DETAIL_GROUP; null image → placeholder node; photo cap respected |
| Manual/Spike | CORS pixel access (ArcGIS attachments + CARTO tiles), real download | **First implementation task** — blocking spike; decides which fallback rungs ship |
| Manual | Admin gating, non-admin hidden, many-photo record | Browser check across roles |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. Client-side `fetch` of public image bytes only.

## Migration / Rollout

No migration. Additive/isolated — rollback removes `report.js`, the button, and the CSS rule.

## Open Questions

- [x] Spike outcome: does ArcGIS attachments + CARTO allow `fetch()`/canvas pixel access? — see "CORS Spike Result" below. **PASS.**

## CORS Spike Result

**Conclusion: PASS.** Both image sources send `Access-Control-Allow-Origin: *`, so client-side `fetch()` → `blob` → dataURL (or `<img crossOrigin="anonymous">` → canvas → `toDataURL()`) will not taint the canvas from any origin. Phase 2 proceeds with client-side fetch→dataURL embedding for photos/firma/map as designed; no proxy needed.

Evidence (real HTTP responses, `curl -D -` with an explicit cross-origin `Origin` header, 2026-09-04):

- ArcGIS attachment (`{SURVEY_LAYER_URL}/1/attachments/1`, real `firma-*.jpg` from ObjectID 1):
  ```
  HTTP/1.1 200 OK
  Content-Type: image/jpeg
  Access-Control-Allow-Origin: *
  Access-Control-Allow-Headers: Content-Type, Authorization, X-Esri-Authorization
  Access-Control-Allow-Credentials: true
  ```
- CARTO tile (`https://a.basemaps.cartocdn.com/dark_all/10/300/380@2x.png`, matching `basemapTileUrl()`):
  ```
  HTTP/1.1 200 OK
  Content-Type: image/png
  Access-Control-Allow-Origin: *
  Access-Control-Allow-Credentials: true
  ```

No browser was available in this environment to run the literal `toDataURL()` call, but a wildcard `Access-Control-Allow-Origin: *` on a simple cross-origin GET (no auth header, no preflight-triggering custom header) is sufficient per the Fetch/CORS spec for the response to be treated as CORS-safe and for a canvas painted from it to remain untainted — this is the same guarantee `crossOrigin='anonymous'` + `ACAO: *` relies on for `leaflet-image`-style tile capture.
