# record-pdf-report Specification

## Purpose

Allow an admin viewing the record detail modal to generate and download a single self-contained PDF report for that one inspection record, covering its EDE fields, photos, signatures, and location, with graceful degradation when any image cannot be embedded.

Non-goals: batch/multi-record generation, server-side rendering, institutional branding/logos.

## Requirements

### Requirement: Admin-only report trigger

The system MUST show a "Generar informe PDF" button in the record detail modal toolbar only when the current user is an admin (per the existing `isAdmin()` gate used by other admin-only modal actions).

#### Scenario: Admin sees the button

- GIVEN the current user is authenticated as admin
- WHEN they open the record detail modal for any record
- THEN the "Generar informe PDF" button is visible and enabled in the modal toolbar

#### Scenario: Non-admin does not see the button

- GIVEN the current user is not an admin (or is unauthenticated)
- WHEN they open the record detail modal
- THEN the "Generar informe PDF" button is not rendered, or is rendered hidden/inert and produces no action if triggered

### Requirement: Single-record PDF content

When generation is triggered, the system MUST produce one PDF document scoped to the currently open record, containing:
- a header with report title, the record's code/identifier, the PDF generation date, and a disclaimer;
- all EDE fields for the record, grouped into the same sections as `DETAIL_GROUPS` used by the on-screen modal, using the same field labels and formatted values shown on screen;
- the record's photo attachments;
- the record's signature attachments (files matched by the existing `firma*` filename convention), rendered in a section distinct from the photos section;
- an image of the record's location (map).

#### Scenario: Generate report for a fully populated record

- GIVEN an admin has open a record with fields in every `DETAIL_GROUPS` section, at least one photo, at least one `firma*` signature attachment, and valid coordinates
- WHEN the admin clicks "Generar informe PDF"
- THEN the downloaded PDF contains the header block, every populated `DETAIL_GROUPS` section with its fields, a photos section with the photo(s), a separate signatures section with the signature image(s), and a location map image

#### Scenario: Generate report for a record with sparse data

- GIVEN an admin has open a record with no photos and no signature attachments
- WHEN the admin clicks "Generar informe PDF"
- THEN the PDF still generates successfully with the header, field sections, and location map, and omits or clearly marks the empty photos/signatures sections without error

### Requirement: Per-image graceful degradation

The system MUST NOT fail PDF generation because a photo, signature, or location map image cannot be fetched or embedded. Each image that fails MUST be replaced in the PDF by a placeholder indicating the image is not available, including the original source link/URL.

#### Scenario: One photo fails to load

- GIVEN a record has multiple photo attachments and one of them cannot be fetched (network error, CORS block, 404)
- WHEN the admin generates the PDF
- THEN the PDF completes generation, embeds the photos that succeeded, and shows an "image not available" placeholder with the source link in place of the failed one

#### Scenario: Location map fails to render

- GIVEN the location map image cannot be produced for a record (e.g., missing/invalid coordinates or map fetch failure)
- WHEN the admin generates the PDF
- THEN the PDF completes generation with an "image not available" placeholder (with source link, when one exists) in place of the map, and all other sections render normally

### Requirement: Generation UX and error handling

While a PDF is being generated, the system MUST disable the trigger button and show a loading/busy state, MUST re-enable it once generation finishes or fails, MUST download the resulting file using a filename derived from the record (e.g., including its code), and MUST surface generation errors to the admin without closing or breaking the detail modal.

#### Scenario: Successful generation and download

- GIVEN an admin clicks "Generar informe PDF"
- WHEN generation completes successfully
- THEN a PDF file is downloaded with a filename that identifies the source record, and the button returns to its normal enabled state

#### Scenario: Unexpected generation failure

- GIVEN an admin clicks "Generar informe PDF"
- WHEN an unrecoverable error occurs during generation (not an individual image failure)
- THEN no partial/corrupt file is downloaded, an error is surfaced to the admin, the modal remains open and usable, and the button returns to its normal enabled state
