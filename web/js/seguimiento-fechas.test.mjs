// Timezone-independence self-check for the Bogotá date/hour helpers in
// seguimiento.js (bogotaParts/bogotaToday). Run: node web/js/seguimiento-fechas.test.mjs
//
// Every timezone-sensitive case is computed inside a CHILD process spawned
// under three different `TZ` values (UTC, America/Bogota, Europe/Madrid) —
// spawning is required (not just setting process.env.TZ in-process) because
// V8/ICU reads TZ once at startup and caches it; reassigning process.env.TZ
// mid-process does not reliably change already-created Date behavior on
// every platform, but a FRESH process always picks up the TZ it was spawned
// with. This file doubles as both the orchestrator (default invocation) and
// the worker (when SEG_FECHAS_WORKER=1 is set, by the orchestrator itself)
// so there is exactly one file to maintain, matching the plan's naming.
//
// Verified upfront on this machine (see the mem_save note / session log):
// `spawnSync(process.execPath, [...], { env: { ...process.env, TZ } })`
// DOES change `new Date().getTimezoneOffset()` in the child on Windows —
// unlike a bare shell `TZ=... node ...` prefix under Git Bash, which this
// investigation found does NOT reach node.exe's environment block. The probe
// below re-verifies this at test-run time rather than trusting that finding
// blindly — if a future machine/Node build ignores TZ entirely, the probe
// assertion here fails loudly instead of the cross-zone comparison silently
// passing for the wrong reason (every zone stuck on one real offset).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { BOGOTA_UTC_OFFSET_MIN, bogotaParts, bogotaToday } from './seguimiento.js';

const SELF = fileURLToPath(import.meta.url);
const ZONES = ['UTC', 'America/Bogota', 'Europe/Madrid'];

// All the timezone-sensitive edge cases from the plan (W5), computed once
// per zone. `tzOffsetProbe` is NOT part of the bogotaParts/bogotaToday
// contract — it is the sanity probe described above.
function computeCases() {
  return {
    tzOffsetProbe: new Date().getTimezoneOffset(),
    offsetConstant: BOGOTA_UTC_OFFSET_MIN,

    // T02:30Z -> día anterior en Bogotá (02:30 - 5h = 21:30 del día anterior).
    beforeMidnightUtc: bogotaParts('2026-01-02T02:30:00Z'),
    // Límites exactos alrededor de la medianoche Bogotá (05:00 UTC = 00:00 Bogotá).
    boundaryBefore: bogotaParts('2026-01-02T04:59:00+00:00'), // 2026-01-01 23:59 Bogotá
    boundaryAt: bogotaParts('2026-01-02T05:00:00+00:00'), // 2026-01-02 00:00 Bogotá
    // Offset explícito -05:00 (ya es Bogotá) debe pasar sin desplazamiento adicional.
    explicitBogotaOffset: bogotaParts('2026-01-02T10:15:00-05:00'),
    // Offset explícito distinto (no Bogotá) también se resuelve correctamente.
    explicitOtherOffset: bogotaParts('2026-01-02T10:15:00+02:00'),
    // Fecha inválida (overflow) -> null, nunca un rollover silencioso.
    invalidOverflow: bogotaParts('2026-02-30'),
    invalidOverflowIso: bogotaParts('2026-02-30T10:00:00+00:00'),
    // Basura / vacío / valores no-fecha -> null, nunca throw.
    garbageText: bogotaParts('no-es-una-fecha'),
    emptyString: bogotaParts(''),
    nullValue: bogotaParts(null),
    undefinedValue: bogotaParts(undefined),
    numberValue: bogotaParts(123),

    // Survey `fecha_hora` ('YYYY-MM-DDTHH:MM', ya local Bogotá, SIN offset):
    // parseada como texto, nunca con `new Date()` sobre un string naive.
    surveyMidnightFive: bogotaParts('2026-01-01T00:05'),
    surveyNoonFive: bogotaParts('2026-01-01T12:05'),
    surveyMissing: bogotaParts(undefined),
    surveyBadHour: bogotaParts('2026-01-01T25:00'), // hora fuera de rango -> sin hora, sin throw
    surveyBadMinuteDigits: bogotaParts('2026-01-01T07:5'), // minuto de 1 dígito, no matchea -> null

    // Bare YYYY-MM-DD (Survey fecha_inspeccion) se toma literal, sin conversión.
    barePassthrough: bogotaParts('2026-01-05'),

    // bogotaToday con un `now` FIJO (no Date.now()) -- debe dar el mismo día
    // en las 3 zonas porque solo usa `now` + el offset fijo de Bogotá.
    todayFixed: bogotaToday(Date.UTC(2026, 0, 2, 2, 0, 0)), // 2026-01-02T02:00:00Z -> 2026-01-01 Bogotá
  };
}

if (process.env.SEG_FECHAS_WORKER === '1') {
  process.stdout.write(JSON.stringify(computeCases()));
  process.exit(0);
}

const outputs = ZONES.map((tz) => {
  const r = spawnSync(process.execPath, [SELF], {
    env: { ...process.env, TZ: tz, SEG_FECHAS_WORKER: '1' },
    encoding: 'utf8',
  });
  assert.equal(r.status, 0, `worker bajo TZ=${tz} salió con código ${r.status}: ${r.stderr}`);
  return { tz, data: JSON.parse(r.stdout) };
});

// Sanity probe: confirm TZ switching actually took effect on THIS machine —
// otherwise the cross-zone comparison below would trivially pass for the
// wrong reason (every child silently ignored TZ and ran on one real offset).
const probes = outputs.map((o) => o.data.tzOffsetProbe);
const distinctProbes = new Set(probes);
if (distinctProbes.size < 2) {
  throw new Error(
    `TZ switching did not change new Date().getTimezoneOffset() across ${JSON.stringify(ZONES)} `
    + `(probes: ${JSON.stringify(probes)}) -- Node on this machine appears to ignore a spawned `
    + 'child\'s TZ env var. bogotaParts/bogotaToday never call a local Date getter (grep-checked '
    + 'below as a second line of defense), but the cross-zone equality assertion cannot be trusted '
    + 'as a live proof on this machine.',
  );
}
console.log(`TZ switching confirmed on this machine: offsets seen = ${JSON.stringify([...distinctProbes])}`);

// Cross-zone equality: every case (minus the probe itself) must be BYTE
// IDENTICAL regardless of which TZ the process was spawned under.
const [first, ...rest] = outputs;
for (const other of rest) {
  const a = { ...first.data }; delete a.tzOffsetProbe;
  const b = { ...other.data }; delete b.tzOffsetProbe;
  assert.deepEqual(b, a, `bogotaParts/bogotaToday differ between TZ=${first.tz} and TZ=${other.tz}`);
}
console.log(`seguimiento-fechas.test.mjs: identical results across ${JSON.stringify(ZONES)} OK`);

// Second line of defense (grep-level), per the task instructions: even if a
// future machine/Node build were to ignore TZ (making the live cross-zone
// proof above unreliable), statically confirm the source never reads a
// LOCAL Date getter for these computations — only the UTC-safe ones.
{
  const fs = await import('node:fs');
  const src = fs.readFileSync(new URL('./seguimiento.js', import.meta.url), 'utf8');
  const forbidden = /\.get(FullYear|Month|Date|Hours|Minutes|Day)\(\)/;

  // N11: scan the WHOLE body of each timezone-sensitive function (from its
  // `function`/`export function` declaration line to the next line that is
  // JUST `}` at column 0) instead of a fixed char-count window from a fixed
  // start marker -- a window like `indexOf('function dateOnly(value) {') +
  // 400` silently stops checking partway through if a future edit (a longer
  // doc comment, an added branch, …) pushes real code past the +400 cutoff,
  // with no failure to signal it. Extracting each function's REAL body has
  // no such blind spot regardless of how the surrounding source reflows.
  function extractFunctionBody(source, declarationRe) {
    const m = declarationRe.exec(source);
    assert.ok(m, `declaration not found in seguimiento.js: ${declarationRe}`);
    const closeIdx = source.indexOf('\n}', m.index);
    assert.ok(closeIdx !== -1, `closing brace ('\\n}' at column 0) not found for: ${declarationRe}`);
    return source.slice(m.index, closeIdx + 2);
  }

  const section = [
    extractFunctionBody(src, /export function bogotaParts\(value\) \{/),
    extractFunctionBody(src, /export function bogotaToday\(now = Date\.now\(\)\) \{/),
    extractFunctionBody(src, /function dateOnly\(value\) \{/),
  ].join('\n');

  assert.equal(forbidden.test(section), false, 'bogotaParts/bogotaToday/dateOnly must never call a LOCAL (non-UTC) Date getter');
  assert.equal(/new Date\(`/.test(section), false, 'must never build a Date from a template-string literal (naive-string local parse)');
}
console.log('seguimiento-fechas.test.mjs: grep-level guard scans whole function bodies, not a fixed char window (N11) OK');

console.log('seguimiento-fechas.test.mjs: all assertions passed');
