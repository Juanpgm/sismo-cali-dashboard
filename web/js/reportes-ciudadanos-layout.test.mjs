// CSS/markup contract for the Reportes ciudadanos filter toolbar on phones.
// Run: node --test web/js/reportes-ciudadanos-layout.test.mjs
//
// The rendered result is verified in Chromium (bounding boxes at 320-1440px);
// these source-level pins guard the CSS contract and the markup hooks it needs.
// Bug: the tab is a `margin: 0 auto` flex item, so without a definite width it
// shrink-wrapped to its widest content (the Barrio select, sized to its longest
// option, ~700px) and the whole tab, filters included, sat past a phone screen.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { sectionHtml } from './reportes-ciudadanos.js';

const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const REP_PHONE = /@media \(max-width: 640px\) \{\n\s*\.rep-section \.asignacion-filters[\s\S]*?\n\}\n/;
const SEG_PHONE = /@media \(max-width: 640px\) \{\n\s*\.seg-section \.asignacion-filters[\s\S]*?\n\}\n/;

test('reportes ciudadanos keeps the markup hooks its layout CSS relies on', () => {
  const html = sectionHtml();
  assert.match(html, /<section class="eval-section rep-section"/);
  assert.match(html, /<div class="card-toolbar asignacion-filters" id="rep-filter-selects">/);
  for (const id of ['rep-search', 'rep-filter-selects', 'rep-reset-filters', 'rep-download']) {
    assert.ok(html.includes(`id="${id}"`), `${id} keeps its id`);
  }
});

test('reportes ciudadanos tab has a definite width so it cannot shrink-wrap its widest select', () => {
  const rule = css.match(/#view-reportes-ciudadanos\s*\{([^}]*)\}/);
  assert.ok(rule, 'the tab rule exists');
  assert.match(rule[1], /width:\s*100%/);
  assert.match(rule[1], /max-width:\s*1180px/);
  assert.match(rule[1], /margin:\s*0 auto/);
  assert.match(rule[1], /min-width:\s*0/);
});

test('reportes ciudadanos filter fields are bounded and, on phones, a wrapping row', () => {
  // Every viewport: a select never outgrows its field and truncates its closed label.
  assert.match(css, /\.rep-section \.asignacion-inline-field\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/);
  assert.match(css, /\.rep-section \.asignacion-inline-field select\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*text-overflow:\s*ellipsis/);
  // Phones: never the shared column (multi-line, as wide as the widest select).
  const phone = css.match(REP_PHONE);
  assert.ok(phone, 'the reportes phone toolbar block exists');
  assert.match(phone[0], /\.rep-section \.asignacion-filters\s*\{[^}]*flex-direction:\s*row[^}]*align-items:\s*stretch/);
  assert.match(phone[0], /\.rep-section \.asignacion-inline-field\s*\{[^}]*flex:\s*1 1 100%[^}]*flex-direction:\s*column !important/);
  assert.match(phone[0], /\.rep-section \.asignacion-inline-field select\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/);
  assert.match(phone[0], /\.rep-section \.segmented-btn\s*\{[^}]*min-width:\s*0/);
  assert.doesNotMatch(phone[0], /flex-wrap:\s*nowrap/);
});

test('the reportes phone block is scoped to its own section and leaves the seguimiento block alone', () => {
  const rep = css.match(REP_PHONE);
  const seg = css.match(SEG_PHONE);
  assert.ok(rep && seg, 'both phone blocks exist');
  assert.doesNotMatch(rep[0], /\.seg-section/);
  assert.doesNotMatch(seg[0], /\.rep-section/);
});
