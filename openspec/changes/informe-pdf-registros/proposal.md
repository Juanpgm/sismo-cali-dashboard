# Proposal: Per-record PDF report from the detail modal

## Intent

Admins need a shareable, printable artifact for a single inspection record. Today the
record detail modal (`web/js/table.js` → `openDetailModal`) shows all EDE fields, photos,
signatures, and a minimap on screen, but there is no way to export one record as a document
for hand-off, filing, or citizen delivery. Add a one-click "Generar informe PDF" button that
produces a clean, self-contained PDF for that record.

## Scope

### In Scope
- Admin-only "Generar informe PDF" button in the detail-modal toolbar (gated with `isAdmin()`, mirroring `#transito-download`).
- Client-side PDF for ONE record: header (title, record code, generation date, disclaimer) + all fields grouped by `DETAIL_GROUPS` + photos + signatures (`firma*` attachments) + location map.
- Graceful image degradation: any photo/signature/map that cannot be embedded renders an "imagen no disponible" placeholder box with its source URL; the PDF always completes.
- Lazy-load the PDF library from CDN, mirroring `loadXlsx()` in `web/js/main.js`.

### Out of Scope
- Batch / multi-record report generation (explicit NON-GOAL).
- Server-side or backend PDF generation (no new FastAPI/serverless coupling).
- Institutional logos or branded templates.
- Changing the on-screen modal layout or the underlying data.

## Capabilities

### New Capabilities
- `record-pdf-report`: admin-triggered client-side generation of a single-record inspection PDF (fields, photos, signatures, location), with per-image graceful degradation.

### Modified Capabilities
- None.

## Approach

Client-side generation only. Lazy-load a PDF library from CDN (evaluate pdfmake vs jsPDF in
design; pdfmake's declarative content model maps directly onto `DETAIL_GROUPS`). Reuse
`DETAIL_GROUPS` / `labelForField` / `formatValue` for field content, `loadPhotos`' ArcGIS
attachment URLs for photos + `firma*` signatures, and the record coords for the location
image. Embed images via `fetch()→blob→dataURL`; on any failure, substitute a placeholder box.

**Early design task (CORS spike, blocking):** verify `fetch()`-based pixel embedding of
(a) ArcGIS attachment images and (b) the location map. `<img src>` display works today but
canvas/pixel access via `fetch()` is unverified. Fallbacks: reuse the Leaflet minimap canvas
(leaflet-image) or a static-map image for location; a byte-only image proxy if attachment
CORS blocks `fetch()`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `web/js/table.js` | Modified | Add button to `openDetailModal` toolbar; reuse `loadPhotos` URLs / `firma*` split |
| `web/js/main.js` | Modified | New lazy CDN loader + click handler, mirroring `loadXlsx()` |
| `web/js/utils.js` | Reused | `DETAIL_GROUPS`, `labelForField`, `formatValue` for report content |
| `web/js/mapview.js` | Reused | Record coords / `basemapTileUrl()` for the location image |
| `web/index.html` | Modified | New `<script>` CDN tag for the PDF library |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| ArcGIS / map CORS blocks `fetch()` pixel embedding | Med | Blocking CORS spike in design; leaflet-image / static-map / byte proxy fallbacks; placeholder box keeps PDF valid |
| Records with many photos → large/slow client PDF | Med | Photo count/size guard; degrade gracefully |
| Signatures missed (not a schema field, matched by `firma*` filename) | Low | Explicitly documented; reuse existing regex split |
| Net-new UI (no prior PDF code in repo) | Low | Mirror proven `loadXlsx()` precedent; single-record scope |

## Rollback Plan

Feature is additive and isolated: remove the button, its click handler, and the CDN
`<script>` tag. No data, schema, backend, or existing-export changes to revert.

## Dependencies

- One CDN-hosted PDF library (pdfmake or jsPDF), lazy-loaded like the existing xlsx dependency.
- Public ArcGIS FeatureServer attachments endpoint (already consumed by `loadPhotos`).

## Success Criteria

- [ ] Admin clicks "Generar informe PDF" in the detail modal and a single-record PDF downloads.
- [ ] PDF contains all `DETAIL_GROUPS` fields, photos, `firma*` signatures, and a location image.
- [ ] Header shows title, record code, generation date, and disclaimer (no logos).
- [ ] A missing/blocked image yields a placeholder box (with source link), never a failed PDF.
- [ ] Button is hidden/inert for non-admin roles.
