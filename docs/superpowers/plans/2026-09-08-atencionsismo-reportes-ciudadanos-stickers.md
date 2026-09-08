# Atención Sismo como fuente de Reportes ciudadanos y Stickers — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nueva pestaña "Reportes ciudadanos" alimentada por `GET /api/informe/json`, y la pestaña "Stickers" alimentada por `GET /api/informe/stickers` con Fase I/II derivada del código de inspector.

**Architecture:** El backend FastAPI (Railway) agrega un cliente paginado para `informe/stickers` y una ruta cacheada `GET /stickers-atencionsismo` que devuelve la misma forma que `GET /evaluaciones`, enriquecida con NP desde Firestore. El job de refresh publica un snapshot proyectado `reportes_ciudadanos.json` al Blob. El frontend vanilla agrega un selector de fuente en Stickers y una pestaña nueva que sigue el patrón de `evaluaciones.js`.

**Tech Stack:** Python 3 + FastAPI + httpx + pytest (backend/tests); JavaScript ESM sin bundler + Leaflet 1.9.4 + `node:assert` self-checks (web/js/*.test.mjs).

**Spec:** `docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md`

## Global Constraints

- Strict TDD: cada comportamiento nuevo nace con su test en rojo antes del código.
- Copy de UI en español. Comentarios de código en inglés. Identificadores siguen el estilo del módulo que se extiende.
- Commits: `tipo(scope): asunto en español`, sin trailers de atribución.
- Nunca publicar un snapshot vacío sobre uno bueno (`_meta_guard`, `ApiEmptyResultError`).
- Nada de PII (`nombre, telefono, cedula, correo, matriculaProfesional`) ni `fotografiasEvaluacion`/`mensajes` en archivos públicos del Blob.
- Backend: sin red real en tests; `httpx.MockTransport` y Firestore falso en memoria.
- Comandos desde la raíz del repo: `python -m pytest backend/tests/<archivo> -v` y `node web/js/<archivo>.test.mjs`.
- Presupuesto de revisión: 400 líneas por PR. Se sugieren 4 PRs encadenados: (1) tareas 1 a 4, (2) tarea 5, (3) tarea 6, (4) tareas 7 y 8.

---

### Task 0: Exploración con credenciales reales (gate manual)

**Files:**
- Create: `scripts/explore_atencionsismo.py`

**Interfaces:**
- Consumes: variables de entorno `VISITADOS_API_USER`, `VISITADOS_API_PASS`.
- Produces: un archivo JSON en el scratchpad con muestras enmascaradas, y una decisión registrada al final de esta tarea.

Prerrequisito humano: pedir la cuenta `personal` con `api: read` (contacto en la spec de la API), crear la contraseña en `https://atencionsismo.cali.gov.co/ingresar`, y exportar las dos variables en la shell local. Sin esto la tarea no puede correr; el resto del plan puede avanzar en paralelo con datos falsos, pero el join por código (Task 2) queda sin verificar hasta cerrar esta tarea.

- [ ] **Step 1: Escribir el script de exploración**

```python
"""One-shot probe of the atencionsismo v2 API with real credentials.

Prints counts and masked samples so the shape assumptions in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md
can be confirmed before any join code is written. Never commits data:
output goes to the path given as argv[1].

Run: python scripts/explore_atencionsismo.py <out.json>
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from collections import Counter

import httpx

BASE = "https://atencionsismo.cali.gov.co"
CODIGO_RE = re.compile(r"^\d{5}-\d-\d{3}\d{4,}$")
PII = {"nombre", "cedula", "telefono", "personaAfectada"}


def _auth() -> dict[str, str]:
    user = os.environ.get("VISITADOS_API_USER", "").strip()
    password = os.environ.get("VISITADOS_API_PASS", "").strip()
    if not user or not password:
        sys.exit("Set VISITADOS_API_USER and VISITADOS_API_PASS first.")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "User-Agent": "sismo-cali-explore/1.0"}


def _mask(row: dict) -> dict:
    return {k: ("***" if k in PII and v else v) for k, v in row.items()}


def _page(client: httpx.Client, path: str, **params) -> dict:
    resp = client.get(f"{BASE}{path}", params=params, timeout=90)
    resp.raise_for_status()
    return resp.json()


def main(out_path: str) -> None:
    client = httpx.Client(headers=_auth())
    report: dict = {}

    first = _page(client, "/api/informe/json", kpis=1, offset=0, limit=200)
    report["informe_json"] = {
        "kpis": first.get("kpis"),
        "cantidad_lote": first.get("cantidad"),
        "done": first.get("done"),
        "keys_reporte": sorted(first["reportes"][0].keys()) if first.get("reportes") else [],
        "tiene_sticker": all("sticker" in r for r in first.get("reportes", [])),
        "sticker_origen": Counter((r.get("sticker") or {}).get("origen", "") for r in first.get("reportes", [])),
        "muestra": [_mask(r) for r in first.get("reportes", [])[:3]],
    }

    stickers: list[dict] = []
    offset = 0
    while True:
        body = _page(client, "/api/informe/stickers", offset=offset, limit=200)
        stickers.extend(body.get("stickers", []))
        if body.get("done", True):
            break
        offset = body["nextOffset"]
    por_origen = Counter(s.get("origen", "") for s in stickers)
    firebase = [s for s in stickers if s.get("origen") == "firebase"]
    report["informe_stickers"] = {
        "total": len(stickers),
        "por_origen": por_origen,
        "por_color": Counter(s.get("color", "") for s in stickers),
        "firebase_numero_con_formato_nuestro": sum(1 for s in firebase if CODIGO_RE.match(s.get("numero", ""))),
        "firebase_numero_muestra": [s.get("numero") for s in firebase[:10]],
        "sistema_numero_muestra": [s.get("numero") for s in stickers if s.get("origen") == "sistema"][:10],
        "sin_coords": sum(1 for s in stickers if not s.get("latitud") or not s.get("longitud")),
        "muestra": [_mask(s) for s in stickers[:3]],
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=dict)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "muestra"} for k, v in report.items()},
                     ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
```

- [ ] **Step 2: Correr el script**

Run: `python scripts/explore_atencionsismo.py "%TEMP%\atencionsismo_explore.json"`
Expected: JSON en consola con `tiene_sticker`, `por_origen`, `firebase_numero_con_formato_nuestro`.

- [x] **Step 3: Registrar la decisión** (hecho el 2026-09-08, ver spec §2.1)

1. `informe/json` incluye `sticker` en el 100 % de las filas. Task 6 conserva los defaults por robustez.
2. 1 436 de 1 470 stickers de origen `firebase` usan nuestro formato; `CODIGO_RE` de la Task 2 queda como está. 34 de 783 de origen `sistema` también lo usan.
3. `descripcion` trae teléfonos y cédulas sueltas en cerca del 2 % de los textos. Task 6 enmascara secuencias de 7 o más dígitos (no se retira el campo).

- [ ] **Step 4: Commit**

```bash
git add scripts/explore_atencionsismo.py docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md
git commit -m "chore(scripts): sonda de exploracion de la API atencionsismo v2"
```

---

### Task 1: Cliente paginado `fetch_stickers` en `atencionsismo.py`

**Files:**
- Modify: `backend/app/services/atencionsismo.py` (constantes junto a `API_URL`, línea 35; funciones nuevas al final)
- Test: `backend/tests/services/test_atencionsismo.py` (agregar al final)

**Interfaces:**
- Consumes: `_headers(user, password)`, `MAX_ATTEMPTS`, `RETRY_SLEEP_S`, `REQUEST_TIMEOUT_S`, `ApiUnavailableError`, `ApiEmptyResultError` (ya existen).
- Produces: `STICKERS_URL: str`, `PAGE_LIMIT = 200`, `async fetch_stickers(client, user, password, *, desde_utc: int | None = None, hasta_utc: int | None = None) -> list[dict]` que devuelve las filas crudas de `stickers[]`.

- [ ] **Step 1: Escribir los tests en rojo**

Agregar al final de `backend/tests/services/test_atencionsismo.py`:

```python
# ── fetch_stickers: offset pagination over GET /api/informe/stickers ──────


def _sticker(i: int, origen: str = "firebase") -> dict:
    return {"id": f"ev-{i}", "direccion": f"Calle {i}", "latitud": "3.45", "longitud": "-76.53",
            "numero": f"76001-1-004{i:04d}", "personaAfectada": "X", "origen": origen,
            "color": "verde", "colorEtiqueta": "Habitable"}


def _pages_handler(pages: dict[int, dict], seen: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/informe/stickers"
        assert request.headers["Authorization"].startswith("Basic ")
        params = dict(request.url.params)
        if seen is not None:
            seen.append(params)
        offset = int(params.get("offset", "0"))
        return httpx.Response(200, json=pages[offset])
    return handler


def test_fetch_stickers_follows_next_offset_until_done(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {
        0: {"ok": True, "stickers": [_sticker(1), _sticker(2)], "nextOffset": 200, "done": False},
        200: {"ok": True, "stickers": [_sticker(3)], "nextOffset": 400, "done": True},
    }
    rows = _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS))
    assert [r["id"] for r in rows] == ["ev-1", "ev-2", "ev-3"]
    assert [p["offset"] for p in seen] == ["0", "200"]
    assert all(p["limit"] == "200" for p in seen)


def test_fetch_stickers_forwards_date_params(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {0: {"ok": True, "stickers": [_sticker(1)], "nextOffset": 200, "done": True}}
    _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS,
                                      desde_utc=1000, hasta_utc=2000))
    assert seen[0]["desde_utc"] == "1000" and seen[0]["hasta_utc"] == "2000"


def test_fetch_stickers_401_raises_unavailable_without_retry(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "no"})

    with pytest.raises(atencionsismo.ApiUnavailableError) as exc:
        _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert exc.value.status == 503
    assert calls == 1


def test_fetch_stickers_retries_504_then_succeeds(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(504)
        return httpx.Response(200, json={"ok": True, "stickers": [_sticker(1)], "done": True})

    rows = _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert len(rows) == 1 and attempts["n"] == 2


def test_fetch_stickers_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    with pytest.raises(atencionsismo.ApiUnavailableError):
        _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert attempts["n"] == atencionsismo.MAX_ATTEMPTS


def test_fetch_stickers_malformed_json_retries(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(200, text="<html>maintenance</html>")
        return httpx.Response(200, json={"ok": True, "stickers": [_sticker(1)], "done": True})

    rows = _run(atencionsismo.fetch_stickers(_client(handler), FAKE_USER, FAKE_PASS))
    assert len(rows) == 1 and attempts["n"] == 2


def test_fetch_stickers_empty_universe_raises(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    pages = {0: {"ok": True, "stickers": [], "nextOffset": 0, "done": True}}
    with pytest.raises(atencionsismo.ApiEmptyResultError):
        _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages)), FAKE_USER, FAKE_PASS))


def test_fetch_stickers_stops_on_non_advancing_cursor(monkeypatch):
    monkeypatch.setattr(atencionsismo, "RETRY_SLEEP_S", 0)
    seen: list[dict] = []
    pages = {0: {"ok": True, "stickers": [_sticker(1)], "nextOffset": 0, "done": False}}
    rows = _run(atencionsismo.fetch_stickers(_client(_pages_handler(pages, seen)), FAKE_USER, FAKE_PASS))
    assert len(rows) == 1 and len(seen) == 1
```

- [ ] **Step 2: Verificar que fallan**

Run: `python -m pytest backend/tests/services/test_atencionsismo.py -k fetch_stickers -v`
Expected: 8 FAILED con `AttributeError: module 'app.services.atencionsismo' has no attribute 'fetch_stickers'`.

- [ ] **Step 3: Implementar**

En `backend/app/services/atencionsismo.py`, agregar `import asyncio` junto a los imports de arriba, y debajo de `API_URL`:

```python
STICKERS_URL = "https://atencionsismo.cali.gov.co/api/informe/stickers"
PAGE_LIMIT = 200  # v2 contract: 1..200 rows per page
MAX_PAGES = 500  # hard stop (100k rows) against a server that never reports done
```

Al final del archivo:

```python
async def _get_stickers_page(
    client: httpx.AsyncClient, headers: dict[str, str], params: dict[str, int]
) -> dict:
    """One page of GET /api/informe/stickers with MAX_ATTEMPTS retries on
    transport/5xx/malformed-body errors. 401/403 are credential problems
    (unset password, account without `api: read`, password not yet created
    at /ingresar) — retrying cannot fix them, so they raise immediately."""
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = await client.get(STICKERS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code in (401, 403):
                raise ApiUnavailableError(
                    f"informe/stickers HTTP {resp.status_code}: credenciales rechazadas", status=503
                )
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("el cuerpo no es un objeto JSON")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1 and RETRY_SLEEP_S:
                await asyncio.sleep(RETRY_SLEEP_S)
    raise ApiUnavailableError(f"informe/stickers sin respuesta valida: {last_exc}", status=503)


async def fetch_stickers(
    client: httpx.AsyncClient,
    user: str,
    password: str,
    *,
    desde_utc: int | None = None,
    hasta_utc: int | None = None,
) -> list[dict]:
    """Full offset-paginated read of GET /api/informe/stickers (v2 contract:
    `offset`/`limit` <= 200, follow `nextOffset` until `done`). Unlike the
    `informe/json` day-walk there is no date-window failure mode to split
    on, so each page is simply retried. Returns the raw `stickers[]` rows.
    Zero rows over the whole walk raises ApiEmptyResultError — never cache
    an empty universe over a transient failure (same rule as
    `fetch_reportados`)."""
    headers = _headers(user, password)
    rows: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        params: dict[str, int] = {"offset": offset, "limit": PAGE_LIMIT}
        if desde_utc is not None:
            params["desde_utc"] = desde_utc
        if hasta_utc is not None:
            params["hasta_utc"] = hasta_utc
        body = await _get_stickers_page(client, headers, params)
        rows.extend(r for r in (body.get("stickers") or []) if isinstance(r, dict))
        if body.get("done", True):
            break
        next_offset = body.get("nextOffset")
        if not isinstance(next_offset, int) or next_offset <= offset:
            break  # malformed cursor: stop rather than loop forever
        offset = next_offset
    if not rows:
        raise ApiEmptyResultError("informe/stickers devolvio 0 filas")
    return rows
```

- [ ] **Step 4: Verificar que pasan**

Run: `python -m pytest backend/tests/services/test_atencionsismo.py -v`
Expected: todos PASS, incluidos los 8 nuevos.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/atencionsismo.py backend/tests/services/test_atencionsismo.py
git commit -m "feat(atencionsismo): cliente paginado fetch_stickers para informe/stickers"
```

---

### Task 2: Servicio de normalización `stickers_atencionsismo.py`

**Files:**
- Create: `backend/app/services/stickers_atencionsismo.py`
- Test: `backend/tests/services/test_stickers_atencionsismo.py`

**Interfaces:**
- Consumes: filas crudas de `fetch_stickers` (Task 1).
- Produces:
  - `CODIGO_RE`, `COLOR_TO_CLASE`, `PLACEHOLDERS`
  - `parse_codigo(numero: object) -> dict | None` con claves `municipio, area, codigo_inspector, consecutivo`
  - `normalize_sticker(row: dict, *, np_by_codigo: dict[str, str], evaluacion_by_codigo: dict[str, dict]) -> dict | None`
  - `build_evaluaciones(rows: list[dict], *, np_by_codigo: dict[str, str], evaluaciones_firestore: list[dict]) -> list[dict]`
  - La forma de salida es la de `stickers.list_evaluaciones` más `fuente`, `origen`, `color_etiqueta`.

- [ ] **Step 1: Escribir los tests en rojo**

```python
"""Pure normalization of atencionsismo `informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2). No I/O."""
from __future__ import annotations

from app.services import stickers_atencionsismo as sa


def _row(**over) -> dict:
    base = {"id": "ev-1", "direccion": "Calle 1 # 2-3", "latitud": "3.4516", "longitud": "-76.5320",
            "numero": "76001-1-0040007", "personaAfectada": "Juan", "origen": "firebase",
            "color": "rojo", "colorEtiqueta": "No habitable"}
    base.update(over)
    return base


def _eval_firestore(**over) -> dict:
    base = {"id": "fs-1", "codigo_edificacion": "76001-1-0040007", "consecutivo": 7, "municipio": "76001",
            "area": "1", "area_nombre": "Norte", "clasificacion": "INSEGURO", "alcance": "Exterior",
            "coords": {"lat": 3.4516, "lng": -76.532, "accuracy": 5},
            "inspector": {"uid": "u1", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                          "entidad": "E", "np": "P4"},
            "descripcion": {"nombre": "Torre A", "direccion": "Calle 1 # 2-3"},
            "restricciones": "r", "acciones_posteriores": {"barricadas": True, "evaluacion_detallada": False},
            "comentarios": "c", "fotos": ["https://x/1.jpg"], "fecha": "2026-08-20T10:00:00"}
    base.update(over)
    return base


# ── parse_codigo ──────────────────────────────────────────────────────────

def test_parse_codigo_our_format():
    assert sa.parse_codigo("76001-1-0040007") == {
        "municipio": "76001", "area": "1", "codigo_inspector": "004", "consecutivo": 7}


def test_parse_codigo_wide_consecutivo():
    assert sa.parse_codigo("76001-2-00410000")["consecutivo"] == 10000


def test_parse_codigo_rejects_other_formats():
    for bad in ("76001001-123-0001", "Sin código", "", None, 42, "76001-1-004", "76001-x-0040001"):
        assert sa.parse_codigo(bad) is None


# ── normalize_sticker ─────────────────────────────────────────────────────

def test_normalize_uses_firestore_evaluacion_when_code_matches():
    out = sa.normalize_sticker(_row(), np_by_codigo={}, evaluacion_by_codigo={"76001-1-0040007": _eval_firestore()})
    assert out["fuente"] == "atencionsismo"
    assert out["origen"] == "firebase"
    assert out["id"] == "ev-1"  # atencionsismo id wins: it is the row identity in this source
    assert out["codigo_edificacion"] == "76001-1-0040007"
    assert out["clasificacion"] == "INSEGURO"
    assert out["inspector"]["np"] == "P4"
    assert out["inspector"]["nombre_completo"] == "Ana"
    assert out["fotos"] == ["https://x/1.jpg"]
    assert out["fecha"] == "2026-08-20T10:00:00"
    assert out["alcance"] == "Exterior"
    assert out["color_etiqueta"] == "No habitable"


def test_normalize_falls_back_to_roster_np_by_inspector_code():
    out = sa.normalize_sticker(_row(), np_by_codigo={"004": "P2"}, evaluacion_by_codigo={})
    assert out["inspector"] == {"uid": "", "codigo": "004", "nombre_completo": "", "identificacion": "",
                                "entidad": "", "np": "P2"}
    assert out["fecha"] is None and out["fotos"] == []


def test_normalize_sistema_origin_has_no_np():
    out = sa.normalize_sticker(_row(origen="sistema", numero="76001001-123-0001"), np_by_codigo={"004": "P4"},
                               evaluacion_by_codigo={})
    assert out["inspector"]["codigo"] == "" and out["inspector"]["np"] == ""
    assert out["codigo_edificacion"] == "76001001-123-0001"
    assert out["consecutivo"] is None and out["area"] is None


def test_normalize_color_to_clase():
    assert sa.normalize_sticker(_row(color="verde"), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "INSPECCIONADA"
    assert sa.normalize_sticker(_row(color="amarillo"), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "USO_RESTRINGIDO"
    assert sa.normalize_sticker(_row(color=""), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == ""
    assert sa.normalize_sticker(_row(color="Rojo "), np_by_codigo={}, evaluacion_by_codigo={})["clasificacion"] == "INSEGURO"


def test_normalize_placeholders_become_empty():
    out = sa.normalize_sticker(_row(numero="Sin código", direccion="Sin dirección", personaAfectada="Sin identificar",
                                    colorEtiqueta="Sin clasificación"), np_by_codigo={}, evaluacion_by_codigo={})
    assert out["codigo_edificacion"] == ""
    assert out["descripcion"] == {"nombre": "", "direccion": ""}
    assert out["color_etiqueta"] == "Sin clasificación"  # kept: it is a real label the UI shows


def test_normalize_coords():
    assert sa.normalize_sticker(_row(), np_by_codigo={}, evaluacion_by_codigo={})["coords"] == {
        "lat": 3.4516, "lng": -76.532, "accuracy": None}
    for lat, lng in (("", ""), ("abc", "-76"), ("0", "0"), (None, None)):
        assert sa.normalize_sticker(_row(latitud=lat, longitud=lng), np_by_codigo={}, evaluacion_by_codigo={})["coords"] is None


def test_normalize_without_id_is_dropped():
    assert sa.normalize_sticker(_row(id=""), np_by_codigo={}, evaluacion_by_codigo={}) is None
    assert sa.normalize_sticker({}, np_by_codigo={}, evaluacion_by_codigo={}) is None


# ── build_evaluaciones ────────────────────────────────────────────────────

def test_build_indexes_firestore_by_code_and_sorts_by_fecha_desc():
    rows = [_row(id="a", numero="76001-1-0040001"), _row(id="b", numero="76001-1-0040002"), _row(id="c", numero="Sin código")]
    fs = [_eval_firestore(codigo_edificacion="76001-1-0040001", fecha="2026-08-01T00:00:00"),
          _eval_firestore(codigo_edificacion="76001-1-0040002", fecha="2026-08-05T00:00:00")]
    out = sa.build_evaluaciones(rows, np_by_codigo={}, evaluaciones_firestore=fs)
    assert [e["id"] for e in out] == ["b", "a", "c"]  # newest first, no-date rows last


def test_build_tolerates_bad_rows():
    out = sa.build_evaluaciones([_row(), {"id": ""}, "not-a-dict", None], np_by_codigo={}, evaluaciones_firestore=[])
    assert len(out) == 1
```

- [ ] **Step 2: Verificar que fallan**

Run: `python -m pytest backend/tests/services/test_stickers_atencionsismo.py -v`
Expected: FAIL en colección con `ModuleNotFoundError: No module named 'app.services.stickers_atencionsismo'`.

- [ ] **Step 3: Implementar**

```python
"""Normalize atencionsismo `GET /api/informe/stickers` rows into the
dashboard's evaluaciones shape (design D1/D2 in
docs/superpowers/specs/2026-09-08-atencionsismo-reportes-ciudadanos-stickers-design.md).

Pure functions, no I/O. Fase I/II input (`inspector.np`) is derived in
priority order: matched Firestore evaluación -> roster NP by the 3-digit
inspector code embedded in our sticker code -> "" (the UI renders "sin
dato" for this source when np is empty; see web/js/evaluaciones.js faseDe).
"""
from __future__ import annotations

import re
from typing import Any

# Our field-form code: 76001-{area}-{inspector 3 digits}{consecutivo 4+ digits}
# (formulario/js/logic.js buildCodigo). atencionsismo re-exports it verbatim
# as `numero` for stickers it imported from our Firebase (origen "firebase").
CODIGO_RE = re.compile(r"^(?P<municipio>\d{5})-(?P<area>\d)-(?P<inspector>\d{3})(?P<consecutivo>\d{4,})$")

COLOR_TO_CLASE = {"verde": "INSPECCIONADA", "amarillo": "USO_RESTRINGIDO", "rojo": "INSEGURO"}

# Values the API substitutes when a field is missing (contract §Stickers JSON).
PLACEHOLDERS = frozenset({"Sin código", "Sin dirección", "Sin identificar"})


def parse_codigo(numero: object) -> dict[str, Any] | None:
    if not isinstance(numero, str):
        return None
    m = CODIGO_RE.match(numero.strip())
    if not m:
        return None
    return {
        "municipio": m.group("municipio"),
        "area": m.group("area"),
        "codigo_inspector": m.group("inspector"),
        "consecutivo": int(m.group("consecutivo")),
    }


def _clean(value: object) -> str:
    text = str(value or "").strip()
    return "" if text in PLACEHOLDERS else text


def _float_or_none(value: object) -> float | None:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out


def _coords(row: dict) -> dict[str, Any] | None:
    lat = _float_or_none(row.get("latitud"))
    lng = _float_or_none(row.get("longitud"))
    if lat is None or lng is None or (lat == 0 and lng == 0):
        return None
    return {"lat": lat, "lng": lng, "accuracy": None}


def normalize_sticker(
    row: dict,
    *,
    np_by_codigo: dict[str, str],
    evaluacion_by_codigo: dict[str, dict],
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    sticker_id = str(row.get("id") or "").strip()
    if not sticker_id:
        return None

    codigo = _clean(row.get("numero"))
    parsed = parse_codigo(codigo)
    match = evaluacion_by_codigo.get(codigo) if codigo else None
    insp_match = (match or {}).get("inspector") or {}
    codigo_inspector = str(insp_match.get("codigo") or (parsed or {}).get("codigo_inspector") or "")
    np_value = str(insp_match.get("np") or "").strip() or np_by_codigo.get(codigo_inspector, "")

    clase = COLOR_TO_CLASE.get(str(row.get("color") or "").strip().lower(), "")
    desc_match = (match or {}).get("descripcion") or {}
    acc_match = (match or {}).get("acciones_posteriores") or {}

    return {
        "id": sticker_id,
        "fuente": "atencionsismo",
        "origen": str(row.get("origen") or "").strip(),
        "color_etiqueta": str(row.get("colorEtiqueta") or "").strip(),
        "codigo_edificacion": codigo,
        "consecutivo": (match or {}).get("consecutivo") if match else (parsed or {}).get("consecutivo"),
        "municipio": (match or {}).get("municipio") or (parsed or {}).get("municipio") or "",
        "area": (match or {}).get("area") if match else (parsed or {}).get("area"),
        "area_nombre": (match or {}).get("area_nombre") or "",
        "clasificacion": (match or {}).get("clasificacion") or clase,
        "alcance": (match or {}).get("alcance") or "",
        "coords": _coords(row) or (match or {}).get("coords"),
        "inspector": {
            "uid": str(insp_match.get("uid") or ""),
            "codigo": codigo_inspector,
            "nombre_completo": str(insp_match.get("nombre_completo") or ""),
            "identificacion": str(insp_match.get("identificacion") or ""),
            "entidad": str(insp_match.get("entidad") or ""),
            "np": np_value,
        },
        "descripcion": {
            "nombre": _clean(desc_match.get("nombre")) or _clean(row.get("personaAfectada")),
            "direccion": _clean(row.get("direccion")) or _clean(desc_match.get("direccion")),
        },
        "restricciones": (match or {}).get("restricciones") or "",
        "acciones_posteriores": {
            "barricadas": bool(acc_match.get("barricadas")),
            "evaluacion_detallada": bool(acc_match.get("evaluacion_detallada")),
        },
        "comentarios": (match or {}).get("comentarios") or "",
        "fotos": list((match or {}).get("fotos") or []),
        "fecha": (match or {}).get("fecha"),
    }


def build_evaluaciones(
    rows: list,
    *,
    np_by_codigo: dict[str, str],
    evaluaciones_firestore: list[dict],
) -> list[dict[str, Any]]:
    by_codigo = {
        str(e.get("codigo_edificacion") or ""): e
        for e in evaluaciones_firestore
        if isinstance(e, dict) and e.get("codigo_edificacion")
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        normalized = normalize_sticker(row, np_by_codigo=np_by_codigo, evaluacion_by_codigo=by_codigo)
        if normalized is not None:
            out.append(normalized)
    out.sort(key=lambda e: str(e.get("fecha") or ""), reverse=True)
    return out
```

Nota sobre `test_normalize_placeholders_become_empty`: `descripcion.nombre` con `personaAfectada = "Sin identificar"` queda `""` porque `_clean` lo filtra. Correcto.

- [ ] **Step 4: Verificar que pasan**

Run: `python -m pytest backend/tests/services/test_stickers_atencionsismo.py -v`
Expected: 12 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/stickers_atencionsismo.py backend/tests/services/test_stickers_atencionsismo.py
git commit -m "feat(stickers): normalizacion de informe/stickers a la forma de evaluaciones con NP por codigo"
```

---

### Task 3: `np_by_codigo` en `stickers.py` y cache parametrizable

**Files:**
- Modify: `backend/app/routers/stickers.py` (`EvaluacionesCache.__init__`, `get_or_fetch`, `_persist_last_good`; función nueva `np_by_codigo` junto a `_np_by_uid`, línea 353)
- Test: `backend/tests/routers/test_stickers.py` (agregar al final)

**Interfaces:**
- Consumes: `INSPECTORES_COLLECTION`, `EVALUACIONES_LKG_BLOB`, `_redact_for_blob`, `blob_lkg`.
- Produces:
  - `np_by_codigo(db) -> dict[str, str]` (código de 3 dígitos → NP).
  - `EvaluacionesCache(lkg_blob: str = EVALUACIONES_LKG_BLOB, redact: Callable[[list], list] = _redact_for_blob)`; el comportamiento sin argumentos es idéntico al actual.

- [ ] **Step 1: Escribir los tests en rojo**

Agregar al final de `backend/tests/routers/test_stickers.py`, usando el Firestore falso y los helpers que el archivo ya define (`_FakeSnapshot`, la clase de colección falsa y `credentials.sismo` instrumentado). Ajustar el nombre del helper que construye la colección al que exista en el archivo:

```python
# ── np_by_codigo: roster NP keyed by 3-digit brigade code ─────────────────

def test_np_by_codigo_reads_roster(fake_sismo):
    db = fake_sismo.firestore
    db.seed("inspectores", {
        "u1": {"codigo": "004", "NP": "P4", "activo": True},
        "u2": {"codigo": "007", "NP": "", "activo": False},
        "u3": {"NP": "P9"},  # no code: unreachable from a sticker number, skipped
        "u4": {"codigo": " 010 ", "NP": " P1 "},
    })
    assert stickers.np_by_codigo(db) == {"004": "P4", "007": "", "010": "P1"}


def test_np_by_codigo_empty_roster(fake_sismo):
    assert stickers.np_by_codigo(fake_sismo.firestore) == {}


# ── EvaluacionesCache: parametrized Blob pathname + redaction ────────────

def test_cache_defaults_keep_evaluaciones_blob(monkeypatch):
    saved: dict[str, object] = {}
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "t")
    monkeypatch.setattr(stickers.blob_lkg, "save_json", lambda path, payload: saved.update({path: payload}) or True)
    cache = stickers.EvaluacionesCache()
    cache.get_or_fetch(lambda: [{"id": "1", "inspector": {"np": "P4"}, "descripcion": {}}])
    cache._persist_thread.join(timeout=2)
    assert list(saved) == [stickers.EVALUACIONES_LKG_BLOB]
    assert saved[stickers.EVALUACIONES_LKG_BLOB][0]["inspector"]["np"] == ""


def test_cache_custom_blob_and_redact(monkeypatch):
    saved: dict[str, object] = {}
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "t")
    monkeypatch.setattr(stickers.blob_lkg, "save_json", lambda path, payload: saved.update({path: payload}) or True)
    cache = stickers.EvaluacionesCache(lkg_blob="data/custom.json", redact=lambda p: [{"id": e["id"]} for e in p])
    cache.get_or_fetch(lambda: [{"id": "1", "secreto": "x"}])
    cache._persist_thread.join(timeout=2)
    assert saved == {"data/custom.json": [{"id": "1"}]}


def test_cache_cold_start_restores_from_custom_blob(monkeypatch):
    monkeypatch.setattr(stickers.blob_lkg, "load_json", lambda path, t: [{"id": "old"}] if path == "data/custom.json" else None)
    cache = stickers.EvaluacionesCache(lkg_blob="data/custom.json", redact=lambda p: p)

    def boom():
        raise RuntimeError("api down")

    assert cache.get_or_fetch(boom) == [{"id": "old"}]
    assert cache.degraded is True
```

Si el fixture `fake_sismo` no existe con ese nombre, usar el que el archivo ya usa para `credentials.sismo()` y el método de siembra de la colección falsa que ya exista (`seed`, `add`, o asignación directa al dict interno). No crear un segundo Firestore falso.

- [ ] **Step 2: Verificar que fallan**

Run: `python -m pytest backend/tests/routers/test_stickers.py -k "np_by_codigo or cache_" -v`
Expected: FAIL con `AttributeError: module ... has no attribute 'np_by_codigo'` y `TypeError: __init__() got an unexpected keyword argument 'lkg_blob'`.

- [ ] **Step 3: Implementar**

En `EvaluacionesCache`:

```python
    def __init__(
        self,
        lkg_blob: str = EVALUACIONES_LKG_BLOB,
        redact: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> None:
        # Parametrized so a sibling dataset (atencionsismo stickers, see
        # routers/stickers_atencionsismo.py) reuses the serve-stale +
        # Blob-restore + degraded semantics with its OWN pathname/redaction.
        self._lkg_blob = lkg_blob
        self._redact = redact or _redact_for_blob
        self._at: float | None = None
        self._payload: list[dict[str, Any]] | None = None
        self._blob_hash: str | None = None
        self._persist_thread: threading.Thread | None = None
        self._degraded: bool = False
```

Agregar `from typing import Any, Callable` en los imports. En `get_or_fetch` reemplazar `blob_lkg.load_json(EVALUACIONES_LKG_BLOB, list)` por `blob_lkg.load_json(self._lkg_blob, list)`. En `_persist_last_good` reemplazar `_redact_for_blob(self._payload or [])` por `self._redact(self._payload or [])` y `blob_lkg.save_json(EVALUACIONES_LKG_BLOB, redacted)` por `blob_lkg.save_json(self._lkg_blob, redacted)`.

Debajo de `_np_by_uid`:

```python
def np_by_codigo(db: Any) -> dict[str, str]:
    """Roster `inspectores/{uid}.NP` keyed by the 3-digit brigade `codigo`
    — the segment our sticker codes embed (76001-1-`004`0001), so an
    atencionsismo sticker of origin "firebase" can reach the inspector's NP
    without a matching evaluación doc (design D1 step 2). Docs without a
    code are unreachable from a sticker number and are skipped."""
    out: dict[str, str] = {}
    for snap in db.collection(INSPECTORES_COLLECTION).get():
        d = snap.to_dict() or {}
        codigo = str(d.get("codigo") or "").strip()
        if codigo:
            out[codigo] = str(d.get("NP") or "").strip()
    return out
```

- [ ] **Step 4: Verificar que pasan**

Run: `python -m pytest backend/tests/routers/test_stickers.py -v`
Expected: todos PASS (los existentes siguen verdes porque los defaults no cambian).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/stickers.py backend/tests/routers/test_stickers.py
git commit -m "feat(stickers): np_by_codigo y EvaluacionesCache parametrizable por blob y redaccion"
```

---

### Task 4: Ruta `GET /stickers-atencionsismo`

**Files:**
- Create: `backend/app/routers/stickers_atencionsismo.py`
- Modify: `backend/app/main.py` (`_ROUTERS`, línea 44 a 62; y donde se crea `app.state.stickers_evaluaciones_cache`, ubicar con `rg -n stickers_evaluaciones_cache backend/app/main.py`)
- Test: `backend/tests/routers/test_stickers_atencionsismo.py`

**Interfaces:**
- Consumes: `atencionsismo.credentials_from_env`, `atencionsismo.fetch_stickers` (Task 1), `stickers_atencionsismo.build_evaluaciones` (Task 2), `stickers.np_by_codigo`, `stickers.EvaluacionesCache`, `stickers.list_evaluaciones` (Task 3), `require_role`, `credentials.sismo()`.
- Produces: `GET /stickers-atencionsismo` → `{"ok": true, "fuente": "atencionsismo", "evaluaciones": [...], "degraded": bool}`; `app.state.stickers_atencionsismo_cache`; `STICKERS_LKG_BLOB = "data/stickers_atencionsismo_last_good.json"`; `redact_for_blob(payload)`.

- [ ] **Step 1: Escribir los tests en rojo**

```python
"""GET /stickers-atencionsismo (RED first) — design D4. Same fixture
conventions as test_stickers.py: TestClient(create_app()), auth via
dependency override of current_claims, in-memory Firestore fake, and the
atencionsismo client monkeypatched wholesale (no network)."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_claims
from app.main import create_app
from app.routers import stickers_atencionsismo as router_mod
from app.services import atencionsismo

# Reuse the fake Firestore/credentials fixtures from test_stickers.py by
# importing them (pytest collects fixtures from imported modules only when
# re-exported here):
from tests.routers.test_stickers import (  # noqa: F401
    FAKE_CLAIMS_ADMIN, FAKE_CLAIMS_INSTITUCIONAL, FAKE_CLAIMS_VIEWER, fake_sismo,
)

ROWS = [
    {"id": "ev-1", "direccion": "Calle 1", "latitud": "3.45", "longitud": "-76.53", "numero": "76001-1-0040001",
     "personaAfectada": "Juan", "origen": "firebase", "color": "rojo", "colorEtiqueta": "No habitable"},
    {"id": "ev-2", "direccion": "Calle 2", "latitud": "3.46", "longitud": "-76.52", "numero": "76001001-123-0001",
     "personaAfectada": "Ana", "origen": "sistema", "color": "verde", "colorEtiqueta": "Habitable"},
]


@pytest.fixture
def client(monkeypatch, fake_sismo):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(atencionsismo, "credentials_from_env", lambda: ("u", "p"))

    async def fake_fetch(client, user, password, **kw):
        return list(ROWS)

    monkeypatch.setattr(atencionsismo, "fetch_stickers", fake_fetch)
    fake_sismo.firestore.seed("inspectores", {"u1": {"codigo": "004", "NP": "P4"}})
    app = create_app()
    app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_ADMIN
    return TestClient(app)


def test_rejects_role_otro(client):
    client.app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_VIEWER
    assert client.get("/stickers-atencionsismo").status_code == 403


def test_viewer_can_read(client):
    client.app.dependency_overrides[current_claims] = lambda: FAKE_CLAIMS_INSTITUCIONAL
    assert client.get("/stickers-atencionsismo").status_code == 200


def test_shape_and_np_join(client):
    body = client.get("/stickers-atencionsismo").json()
    assert body["ok"] is True and body["fuente"] == "atencionsismo" and body["degraded"] is False
    by_id = {e["id"]: e for e in body["evaluaciones"]}
    assert by_id["ev-1"]["inspector"]["np"] == "P4"
    assert by_id["ev-1"]["clasificacion"] == "INSEGURO"
    assert by_id["ev-2"]["inspector"]["np"] == ""
    assert by_id["ev-2"]["origen"] == "sistema"


def test_api_failure_serves_stale(client, monkeypatch):
    assert client.get("/stickers-atencionsismo").status_code == 200

    async def boom(*a, **kw):
        raise atencionsismo.ApiUnavailableError("down")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom)
    client.app.state.stickers_atencionsismo_cache._at = 0  # force TTL expiry
    body = client.get("/stickers-atencionsismo").json()
    assert len(body["evaluaciones"]) == 2 and body["degraded"] is False


def test_api_failure_cold_start_without_blob_is_503(client, monkeypatch):
    async def boom(*a, **kw):
        raise atencionsismo.ApiUnavailableError("down")

    monkeypatch.setattr(atencionsismo, "fetch_stickers", boom)
    monkeypatch.setattr(router_mod.blob_lkg, "load_json", lambda *a: None)
    assert client.get("/stickers-atencionsismo").status_code == 503


def test_missing_password_is_503(client, monkeypatch):
    def no_creds():
        raise atencionsismo.ApiCredentialsError("VISITADOS_API_PASS is not set")

    monkeypatch.setattr(atencionsismo, "credentials_from_env", no_creds)
    resp = client.get("/stickers-atencionsismo")
    assert resp.status_code == 503 and "VISITADOS_API_PASS" in resp.json()["detail"]


def test_redaction_blanks_persona_and_np():
    payload = [{"id": "1", "fuente": "atencionsismo", "origen": "firebase", "color_etiqueta": "Habitable",
                "codigo_edificacion": "c", "consecutivo": 1, "municipio": "76001", "area": "1", "area_nombre": "",
                "clasificacion": "INSPECCIONADA", "alcance": "", "coords": {"lat": 1, "lng": 2, "accuracy": None},
                "restricciones": "", "acciones_posteriores": {"barricadas": False, "evaluacion_detallada": False},
                "fecha": None, "descripcion": {"nombre": "Juan", "direccion": "Calle 1"},
                "inspector": {"uid": "u", "codigo": "004", "nombre_completo": "Ana", "identificacion": "1",
                              "entidad": "E", "np": "P4"}, "comentarios": "c", "fotos": ["x"]}]
    out = router_mod.redact_for_blob(payload)[0]
    assert out["descripcion"] == {"nombre": "", "direccion": "Calle 1"}
    assert out["inspector"] == {"uid": "u", "codigo": "004", "entidad": "E", "nombre_completo": "", "identificacion": "", "np": ""}
    assert out["comentarios"] == "" and out["fotos"] == []
    assert out["fuente"] == "atencionsismo" and out["origen"] == "firebase" and out["color_etiqueta"] == "Habitable"
    assert payload[0]["descripcion"]["nombre"] == "Juan"  # never mutates input
```

Si `tests/routers/test_stickers.py` no puede importarse como módulo (sin `__init__.py`), mover los fakes compartidos a `backend/tests/routers/conftest.py` en este mismo paso y dejar ambos archivos consumiéndolos desde ahí.

- [ ] **Step 2: Verificar que fallan**

Run: `python -m pytest backend/tests/routers/test_stickers_atencionsismo.py -v`
Expected: FAIL en colección con `ImportError: cannot import name 'stickers_atencionsismo' from 'app.routers'`.

- [ ] **Step 3: Implementar el router**

```python
"""GET /stickers-atencionsismo — the Stickers tab's Evaluaciones list sourced
from atencionsismo `GET /api/informe/stickers` instead of Firestore
`evaluaciones` (design D1..D4). Same response shape as GET /evaluaciones
plus `fuente`/`origen`/`color_etiqueta` per record, so web/js/evaluaciones.js
renders both sources with one code path.

Cache: `stickers.EvaluacionesCache` parametrized with its own Blob
last-known-good pathname and an allowlist redaction that blanks the
affected person's name and the inspector NP (same sensitivity class as the
evaluaciones copy). A Firestore failure (roster or evaluaciones) fails the
whole fetch on purpose: without NP there is no Fase, and a silently empty
Fase is worse than a stale payload.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_role
from app.credentials import clients as credentials
from app.routers import stickers
from app.services import atencionsismo, blob_lkg
from app.services.stickers_atencionsismo import build_evaluaciones

router = APIRouter()
REQUIRED_CLIENTS: tuple[str, ...] = ("sismo",)

STICKERS_LKG_BLOB = "data/stickers_atencionsismo_last_good.json"

_BLOB_ALLOWED_FIELDS = stickers._BLOB_ALLOWED_FIELDS + ("fuente", "origen", "color_etiqueta")


def redact_for_blob(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlist projection for the PUBLIC Blob copy — `descripcion.nombre`
    is the affected person's name here (personaAfectada), so it is blanked
    along with `inspector.np`; everything else mirrors
    `stickers._redact_for_blob`. Never mutates the input."""
    out: list[dict[str, Any]] = []
    for e in payload:
        insp = e.get("inspector") or {}
        desc = e.get("descripcion") or {}
        out.append({
            **{k: e.get(k) for k in _BLOB_ALLOWED_FIELDS},
            "descripcion": {"nombre": "", "direccion": desc.get("direccion") or ""},
            "inspector": {**{k: insp.get(k) or "" for k in stickers._BLOB_ALLOWED_INSPECTOR},
                          "nombre_completo": "", "identificacion": "", "np": ""},
            "comentarios": "",
            "fotos": [],
        })
    return out


def build_payload(db: Any, evaluaciones_cache: stickers.EvaluacionesCache) -> list[dict[str, Any]]:
    user, password = atencionsismo.credentials_from_env()

    async def _pull() -> list[dict]:
        async with httpx.AsyncClient() as client:
            return await atencionsismo.fetch_stickers(client, user, password)

    rows = asyncio.run(_pull())  # sync route runs in the threadpool: no running loop here
    np_map = stickers.np_by_codigo(db)
    firestore_evals = evaluaciones_cache.get_or_fetch(lambda: stickers.list_evaluaciones(db))
    return build_evaluaciones(rows, np_by_codigo=np_map, evaluaciones_firestore=firestore_evals)


@router.get("/stickers-atencionsismo")
def get_stickers_atencionsismo(
    request: Request,
    claims: dict[str, Any] = Depends(require_role("admin", "viewer")),
) -> JSONResponse:
    cache: stickers.EvaluacionesCache = request.app.state.stickers_atencionsismo_cache
    evaluaciones_cache: stickers.EvaluacionesCache = request.app.state.stickers_evaluaciones_cache
    db = credentials.sismo().firestore
    try:
        payload = cache.get_or_fetch(lambda: build_payload(db, evaluaciones_cache))
    except HTTPException:
        raise
    except (atencionsismo.ApiUnavailableError, atencionsismo.ApiCredentialsError,
            atencionsismo.ApiEmptyResultError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - same CORS-preserving catch-all as GET /evaluaciones
        logging.exception("stickers-atencionsismo: fallo no clasificado")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "fuente": "atencionsismo", "evaluaciones": payload, "degraded": cache.degraded})
```

Confirmar el nombre real de la excepción de credenciales en `atencionsismo.py` (`ApiCredentialsError` según el docstring de `credentials_from_env`); si difiere, usar el nombre real en el router y en el test.

- [ ] **Step 4: Registrar el router y el cache**

En `backend/app/main.py`: importar `stickers_atencionsismo` junto a los demás routers, agregarlo al final de `_ROUTERS`, y junto a la línea que crea `app.state.stickers_evaluaciones_cache` agregar:

```python
    app.state.stickers_atencionsismo_cache = stickers.EvaluacionesCache(
        lkg_blob=stickers_atencionsismo.STICKERS_LKG_BLOB,
        redact=stickers_atencionsismo.redact_for_blob,
    )
```

- [ ] **Step 5: Verificar que pasan**

Run: `python -m pytest backend/tests/routers/test_stickers_atencionsismo.py backend/tests/routers/test_stickers.py backend/tests/invariants -v`
Expected: todos PASS. Los tests de invariantes confirman que el módulo nuevo no toca literales de colecciones protegidas.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/stickers_atencionsismo.py backend/app/main.py backend/tests/routers/test_stickers_atencionsismo.py
git commit -m "feat(stickers): ruta GET /stickers-atencionsismo cacheada con Fase I/II por codigo de inspector"
```

---

### Task 5: Frontend Stickers — selector de fuente y Fase "sin dato"

**Files:**
- Modify: `web/js/api-config.js` (`API_CONFIG`)
- Modify: `web/js/evaluaciones.js` (`FASES`, `faseDe`, `COLOR_MODES`, banner líneas 233 a 237, `detailHtml`, retorno de `initEvaluaciones` líneas 979 a 985)
- Modify: `web/js/stickers.js` (completo)
- Test: `web/js/evaluaciones.test.mjs`

**Interfaces:**
- Consumes: `GET /stickers-atencionsismo` (Task 4), `faseInspector` (utils.js).
- Produces: `API_CONFIG.stickersAtencionsismo`; `FASE_SIN_DATO` exportado; `faseDe(e)` devuelve `FASE_SIN_DATO` cuando `e.fuente === 'atencionsismo'` y `inspector.np` vacío; `initEvaluaciones(...)` devuelve `{ invalidate, reload }`.

- [ ] **Step 1: Escribir los tests en rojo**

Agregar al final de `web/js/evaluaciones.test.mjs`:

```javascript
// ── Fase "sin dato" for the atencionsismo source (design D1 step 3) ─────
import { FASE_SIN_DATO } from './evaluaciones.js';

assert.strictEqual(FASE_SIN_DATO.key, 'SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: '' } }).key, 'SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: '  ' } }).key, 'SIN_DATO');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: 'P4' } }).key, 'FASE_II');
assert.strictEqual(faseDe({ fuente: 'atencionsismo', inspector: { np: 'P1' } }).key, 'FASE_I');
// Firestore source keeps today's rule: unknown NP defaults to Fase I.
assert.strictEqual(faseDe({ fuente: 'firestore', inspector: { np: '' } }).key, 'FASE_I');
assert.strictEqual(faseDe({ inspector: { np: '' } }).key, 'FASE_I');
assert.strictEqual(faseDe({}).key, 'FASE_I');

const sinDato = { fuente: 'atencionsismo', inspector: { np: '', nombre_completo: '', codigo: '' },
  descripcion: { nombre: '', direccion: 'Calle 9' }, codigo_edificacion: '', clasificacion: 'INSEGURO' };
assert.strictEqual(applyFilters([sinDato], { fase: 'SIN_DATO' }).length, 1);
assert.strictEqual(applyFilters([sinDato], { fase: 'FASE_I' }).length, 0);
assert.strictEqual(applyFilters([sinDato], { search: 'calle 9' }).length, 1);
console.log('evaluaciones.test.mjs: fase sin dato OK');
```

- [ ] **Step 2: Verificar que fallan**

Run: `node web/js/evaluaciones.test.mjs`
Expected: `SyntaxError: The requested module './evaluaciones.js' does not provide an export named 'FASE_SIN_DATO'`.

- [ ] **Step 3: Implementar en `evaluaciones.js`**

Reemplazar el bloque `FASES`/`faseDe` (líneas 56 a 66) por:

```javascript
export const FASES = [
  { key: 'FASE_II', label: 'fase II', color: COLORS.accent },
  { key: 'FASE_I', label: 'fase I', color: COLORS.unknown },
];
// Atención Sismo stickers of origin "sistema" carry no inspector at all, so
// their Fase is unknowable — surfaced as its own state instead of the
// Firestore default (Fase I), which would be a lie for that source.
export const FASE_SIN_DATO = { key: 'SIN_DATO', label: 'sin dato', color: '#9AA5B1' };
const FASE_BY_KEY = new Map(FASES.map((f) => [f.key, f]));

/** Fase I/II of one evaluation's inspector, derived from inspector.np. For
 *  the atencionsismo source an empty np means "sin dato" (design D1). */
export function faseDe(evaluacion) {
  const np = String((evaluacion && evaluacion.inspector && evaluacion.inspector.np) || '').trim();
  if (evaluacion && evaluacion.fuente === 'atencionsismo' && !np) return FASE_SIN_DATO;
  return FASE_BY_KEY.get(faseInspector(np));
}
```

En `COLOR_MODES.fase` cambiar `entries: FASES` por `entries: [...FASES, FASE_SIN_DATO]`. Donde se generan los chips de filtro de Fase (buscar `FASES.map` en el archivo) incluir también `FASE_SIN_DATO`.

Banner (líneas 233 a 237), texto nuevo:

```html
      <div class="eval-degraded-banner" id="eval-degraded-banner" hidden role="status">
        Mostrando una copia de respaldo porque no hay conexión con la fuente en vivo:
        la clasificación Fase I/Fase II puede no ser correcta, y los nombres, fotos y
        comentarios no están disponibles hasta que se reconecte.
      </div>
```

En `detailHtml`, dentro de la lista de pares `['Consecutivo', e.consecutivo]` (línea 582), agregar antes:

```javascript
      ['Fuente', e.fuente === 'atencionsismo' ? 'Atención Sismo' : 'Formulario'],
      ['Origen del sticker', e.origen === 'firebase' ? 'Importado de Firebase' : (e.origen === 'sistema' ? 'App Atención Sismo' : 'Sin dato')],
      ['Etiqueta', e.color_etiqueta || 'Sin dato'],
```

En el retorno de `initEvaluaciones` (línea 979) agregar `reload: () => load(false),` junto a `invalidate`. `load` es la función interna existente cuyo primer parámetro es `silent`.

Fila del export xlsx (línea 916 en adelante): agregar `fuente: e.fuente || 'firestore', origen_sticker: e.origen || '', etiqueta_sticker: e.color_etiqueta || '',` después de `id: e.id,`.

- [ ] **Step 4: Implementar en `api-config.js`**

Debajo de la entrada `evaluaciones`:

```javascript
  // Stickers tab, Evaluaciones sourced from atencionsismo informe/stickers
  // (backend/app/routers/stickers_atencionsismo.py). New endpoint, no
  // legacy Vercel twin: born on Railway (same rationale as panelRepresentante).
  stickersAtencionsismo: `${RAILWAY_BASE_URL}/stickers-atencionsismo`,
```

- [ ] **Step 5: Implementar en `stickers.js`**

Reemplazar `fetchEvaluacionesOnce`, `shellHtml` y el cuerpo de `initStickers` por:

```javascript
// Two interchangeable sources for the Evaluaciones section, same response
// shape (design D2/D3). atencionsismo is the default; the Formulario
// (Firestore) source stays as the safety valve while the code join is
// being validated in production.
const FUENTES = {
  atencionsismo: { label: 'Atención Sismo', endpoint: 'stickersAtencionsismo' },
  firestore: { label: 'Formulario', endpoint: 'evaluaciones' },
};
let fuente = 'atencionsismo';

async function fetchEvaluacionesOnce(getToken, endpoint) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  const res = await fetch(apiUrl(endpoint), { headers: { Authorization: `Bearer ${token}` } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
  const fuenteRespuesta = data.fuente || 'firestore';
  return {
    evaluaciones: (data.evaluaciones || []).map((e) => ({ fuente: fuenteRespuesta, ...e })),
    degraded: Boolean(data.degraded),
  };
}

function fuenteSegmentedHtml() {
  return Object.entries(FUENTES).map(([key, def]) => `
      <button type="button" class="asignacion-segment${key === fuente ? ' is-active' : ''}"
        data-sticker-fuente="${key}" role="tab" aria-selected="${key === fuente}">${def.label}</button>`).join('');
}

function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Operación de campo</h2>
      <p class="sticker-lead">Lo que registran las brigadas y quién puede registrarlo.</p>
    </header>

    <div class="asignacion-segmented" role="tablist" aria-label="Sección de Stickers">
      <button type="button" class="asignacion-segment is-active" data-sticker-segment="evaluaciones" role="tab" aria-selected="true">Evaluaciones</button>
      <button type="button" class="asignacion-segment" data-sticker-segment="asignacion" role="tab" aria-selected="false">Asignación</button>
    </div>

    <div data-sticker-section="evaluaciones">
      <div class="asignacion-segmented eval-fuente" role="tablist" aria-label="Fuente de datos">
        <span class="eval-fuente-label">Fuente</span>${fuenteSegmentedHtml()}
      </div>
      ${evalSectionHtml()}
    </div>
    <div data-sticker-section="asignacion" hidden></div>`;
}

export function initStickers(root, { getToken }) {
  root.innerHTML = shellHtml();
  const segmentButtons = root.querySelectorAll('[data-sticker-segment]');
  const fuenteButtons = root.querySelectorAll('[data-sticker-fuente]');
  const sections = {
    evaluaciones: root.querySelector('[data-sticker-section="evaluaciones"]'),
    asignacion: root.querySelector('[data-sticker-section="asignacion"]'),
  };
  let asignacionHandle = null;

  const evaluacionesHandle = initEvaluaciones(root.querySelector('.eval-section'), {
    fetchEvaluaciones: async () => {
      const endpoint = FUENTES[fuente].endpoint;
      try {
        return await fetchEvaluacionesOnce(getToken, endpoint);
      } catch (err) {
        await new Promise((r) => setTimeout(r, 500));
        return await fetchEvaluacionesOnce(getToken, endpoint);
      }
    },
  });

  fuenteButtons.forEach((btn) => btn.addEventListener('click', () => {
    if (btn.dataset.stickerFuente === fuente) return;
    fuente = btn.dataset.stickerFuente;
    fuenteButtons.forEach((b) => {
      const active = b.dataset.stickerFuente === fuente;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-selected', String(active));
    });
    evaluacionesHandle.reload();
  }));

  function showSegment(name) {
    for (const [key, el] of Object.entries(sections)) el.hidden = key !== name;
    segmentButtons.forEach((btn) => {
      const active = btn.dataset.stickerSegment === name;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    if (name === 'evaluaciones' && evaluacionesHandle) {
      setTimeout(() => evaluacionesHandle.invalidate(), 60);
    }
    if (name !== 'asignacion') return;
    if (asignacionHandle) {
      asignacionHandle.reload();
    } else {
      asignacionHandle = initStickersAsignacion(sections.asignacion, { getToken });
    }
  }
  segmentButtons.forEach((btn) => btn.addEventListener('click', () => showSegment(btn.dataset.stickerSegment)));
}
```

En `web/styles.css`, junto al bloque `.eval-*`:

```css
.eval-fuente { margin: 0 0 12px; align-items: center; gap: 6px; }
.eval-fuente-label { font-size: 12px; color: var(--muted, #6b7280); margin-right: 4px; }
```

- [ ] **Step 6: Verificar**

Run: `node web/js/evaluaciones.test.mjs`
Expected: PASS con `evaluaciones.test.mjs: fase sin dato OK` al final.

Verificación manual: abrir el dashboard local, pestaña Stickers. Con credenciales cargadas en Railway, la fuente "Atención Sismo" muestra stickers con pills de Fase y el modal con "Fuente" y "Origen del sticker". Cambiar a "Formulario" recarga la lista de Firestore sin recargar la página. Sin credenciales en Railway, la fuente "Atención Sismo" muestra el error 503 del backend en el estado vacío de la lista y "Formulario" sigue funcionando.

- [ ] **Step 7: Commit**

```bash
git add web/js/api-config.js web/js/evaluaciones.js web/js/stickers.js web/js/evaluaciones.test.mjs web/styles.css
git commit -m "feat(stickers): fuente Atencion Sismo por defecto con selector y Fase 'sin dato'"
```

---

### Task 6: Snapshot `reportes_ciudadanos.json` en el job de refresh

**Files:**
- Create: `backend/app/services/reportes_ciudadanos.py`
- Modify: `backend/app/jobs/dashboard_refresh.py` (`_PUBLISH_FILES` línea 100; `fetch_reportes` línea 288, donde se escribe `reportes.json`; ubicar con `rg -n "reportes.json" backend/app/jobs/dashboard_refresh.py`)
- Test: `backend/tests/services/test_reportes_ciudadanos.py`, `backend/tests/jobs/test_dashboard_refresh.py` (agregar)

**Interfaces:**
- Consumes: registros ya mapeados por `_raw_record_mapper` (tienen `lat`/`lng` numéricos y sin PII).
- Produces: `parse_fecha_es_co(texto) -> str | None` (ISO 8601 con offset -05:00), `project_reporte(rep: dict) -> dict | None`, `build_snapshot(records) -> list[dict]`, `DESCRIPCION_MAX = 500`; archivo `web/data/reportes_ciudadanos.json` publicado como `data/reportes_ciudadanos.json`.

- [ ] **Step 1: Escribir los tests en rojo**

```python
"""Projection of informe/json records into the Reportes ciudadanos
snapshot (design D5). Pure functions."""
from __future__ import annotations

from app.services import reportes_ciudadanos as rc


def _rep(**over) -> dict:
    base = {"id": "r1", "direccion": "Calle 1 # 2-3", "barrio": "San Antonio", "comuna": "Comuna 3",
            "estadoVerificacion": "Visitado crítico", "afectacion": "COLAPSO PARCIAL", "tipoInmueble": "Casa",
            "nombreEdificio": "", "lat": 3.45, "lng": -76.53, "latitud": "3.45", "longitud": "-76.53",
            "fechaCreacion": "martes, 18 de agosto de 2026, 06:33 p. m.", "habitabilidad": "No habitable",
            "visitado": "Sí", "pudoEvaluar": "Sí", "alcanceInspeccion": "En el interior",
            "descripcion": "Grieta en muro", "conceptoTecnico": "largo...", "danosCubierta": "x",
            "sticker": {"numero": "76001-1-0040001", "color": "rojo", "colorEtiqueta": "No habitable",
                        "origen": "firebase", "clasificacion": "peligro_colapso"}}
    base.update(over)
    return base


# ── parse_fecha_es_co ─────────────────────────────────────────────────────

def test_parse_fecha_pm():
    assert rc.parse_fecha_es_co("martes, 18 de agosto de 2026, 06:33 p. m.") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_am_and_noon_midnight():
    assert rc.parse_fecha_es_co("jueves, 20 de agosto de 2026, 07:19 a. m.") == "2026-08-20T07:19:00-05:00"
    assert rc.parse_fecha_es_co("lunes, 3 de agosto de 2026, 12:05 p. m.") == "2026-08-03T12:05:00-05:00"
    assert rc.parse_fecha_es_co("lunes, 3 de agosto de 2026, 12:05 a. m.") == "2026-08-03T00:05:00-05:00"


def test_parse_fecha_without_weekday_and_nbsp():
    assert rc.parse_fecha_es_co("18 de agosto de 2026, 6:33 p. m.") == "2026-08-18T18:33:00-05:00"


def test_parse_fecha_invalid():
    for bad in ("", None, "ayer", "32 de agosto de 2026, 06:33 p. m.", "18 de brumario de 2026, 06:33 p. m.", 123):
        assert rc.parse_fecha_es_co(bad) is None


# ── project_reporte ───────────────────────────────────────────────────────

def test_project_keeps_only_tab_fields():
    out = rc.project_reporte(_rep())
    assert set(out) == {"id", "direccion", "barrio", "comuna", "estado", "afectacion", "tipo_inmueble",
                        "nombre_edificio", "lat", "lng", "creado", "creado_texto", "habitabilidad", "visitado",
                        "pudo_evaluar", "alcance", "descripcion", "sticker"}
    assert out["estado"] == "Visitado crítico" and out["visitado"] is True
    assert out["creado"] == "2026-08-18T18:33:00-05:00"
    assert out["sticker"] == {"numero": "76001-1-0040001", "color": "rojo", "etiqueta": "No habitable",
                              "origen": "firebase", "clasificacion": "peligro_colapso"}


def test_project_never_leaks_pii_or_heavy_fields():
    out = rc.project_reporte(_rep(nombre="Juan", cedula="1", telefono="3", fotografiasEvaluacion=[{}], mensajes=[{}]))
    for k in ("nombre", "cedula", "telefono", "fotografiasEvaluacion", "mensajes", "conceptoTecnico"):
        assert k not in out


def test_project_defaults_when_sticker_missing():
    out = rc.project_reporte(_rep(sticker=None))
    assert out["sticker"] == {"numero": "", "color": "", "etiqueta": "", "origen": "", "clasificacion": ""}
    out = rc.project_reporte({k: v for k, v in _rep().items() if k != "sticker"})
    assert out["sticker"]["color"] == ""


def test_project_truncates_descripcion():
    out = rc.project_reporte(_rep(descripcion="x" * 1000))
    assert len(out["descripcion"]) == rc.DESCRIPCION_MAX


def test_project_masks_long_digit_runs_in_descripcion():
    # Task 0 found phone numbers and cedulas typed into the free text.
    out = rc.project_reporte(_rep(descripcion="Llamar al 3001234567 o cc 1234567890, apto 302, calle 12 # 3-45"))
    assert out["descripcion"] == "Llamar al *** o cc ***, apto 302, calle 12 # 3-45"
    assert rc.mask_digit_runs("tel 300 123 4567") == "tel 300 123 4567"  # spaced digits are not a run
    assert rc.mask_digit_runs("") == ""


def test_project_visitado_false_and_coords_fallback():
    out = rc.project_reporte(_rep(visitado="", lat=None, lng=None))
    assert out["visitado"] is False
    assert out["lat"] == 3.45 and out["lng"] == -76.53  # parsed from latitud/longitud strings


def test_project_drops_rows_without_id():
    assert rc.project_reporte(_rep(id="")) is None
    assert rc.project_reporte("nope") is None


def test_build_snapshot_filters_and_keeps_order():
    rows = rc.build_snapshot([_rep(id="a"), {"id": ""}, _rep(id="b")])
    assert [r["id"] for r in rows] == ["a", "b"]
```

Agregar a `backend/tests/jobs/test_dashboard_refresh.py`, siguiendo el patrón de los tests existentes de `fetch_reportes` (mismo stub de `day_walk` y directorio temporal):

```python
def test_fetch_reportes_also_writes_reportes_ciudadanos(tmp_path, monkeypatch, stub_day_walk_ok):
    # `stub_day_walk_ok` is whatever fixture the existing fetch_reportes tests
    # use to fake atencionsismo.day_walk with >=1 record; reuse it verbatim.
    dashboard_refresh.fetch_reportes(tmp_path)
    data = json.loads((tmp_path / "reportes_ciudadanos.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and data and "estado" in data[0] and "nombre" not in data[0]
    assert ("reportes_ciudadanos.json", "data/reportes_ciudadanos.json") in dashboard_refresh._PUBLISH_FILES
```

Adaptar la firma de `fetch_reportes` y el nombre del fixture a lo que ya exista en ese archivo de tests.

- [ ] **Step 2: Verificar que fallan**

Run: `python -m pytest backend/tests/services/test_reportes_ciudadanos.py backend/tests/jobs/test_dashboard_refresh.py -v`
Expected: `ModuleNotFoundError` para el servicio y FAIL del test nuevo del job.

- [ ] **Step 3: Implementar el servicio**

```python
"""Projection of atencionsismo informe/json records into the lightweight
public snapshot the "Reportes ciudadanos" tab reads (design D5). Input is
the already PII/heavy-stripped record `dashboard_refresh._raw_record_mapper`
produces; this narrows it further to what the tab renders and parses the
es-CO formatted creation date into ISO 8601 (Bogota, UTC-5)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

DESCRIPCION_MAX = 500
BOGOTA = timezone(timedelta(hours=-5))
_DIGIT_RUN_RE = re.compile(r"\d{7,}")


def mask_digit_runs(texto: str) -> str:
    """Blank phone numbers / cedulas citizens type into the free text (7+
    consecutive digits) before the text reaches the PUBLIC snapshot. Task 0
    measured ~2% of descriptions carrying one."""
    return _DIGIT_RUN_RE.sub("***", texto or "")

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
          "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}

# "martes, 18 de agosto de 2026, 06:33 p. m." — weekday optional, NBSP
# tolerated, "a. m."/"p. m." with or without the inner space/dots.
_FECHA_RE = re.compile(
    r"^(?:[a-záéíóúü]+,\s*)?(?P<d>\d{1,2})\s+de\s+(?P<m>[a-záéíóúü]+)\s+de\s+(?P<y>\d{4}),?\s*"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})\s*(?P<ap>[ap])\.?\s*m\.?$",
    re.IGNORECASE,
)

_STICKER_KEYS = (("numero", "numero"), ("color", "color"), ("colorEtiqueta", "etiqueta"),
                 ("origen", "origen"), ("clasificacion", "clasificacion"))


def parse_fecha_es_co(texto: object) -> str | None:
    if not isinstance(texto, str):
        return None
    m = _FECHA_RE.match(texto.replace(" ", " ").strip())
    if not m:
        return None
    mes = _MESES.get(m.group("m").lower())
    if mes is None:
        return None
    hora = int(m.group("h")) % 12
    if m.group("ap").lower() == "p":
        hora += 12
    try:
        dt = datetime(int(m.group("y")), mes, int(m.group("d")), hora, int(m.group("mi")), tzinfo=BOGOTA)
    except ValueError:
        return None
    return dt.isoformat()


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    return str(value or "").strip()


def project_reporte(rep: object) -> dict[str, Any] | None:
    if not isinstance(rep, dict):
        return None
    rid = _text(rep.get("id"))
    if not rid:
        return None
    sticker = rep.get("sticker") if isinstance(rep.get("sticker"), dict) else {}
    lat = _float_or_none(rep.get("lat"))
    lng = _float_or_none(rep.get("lng"))
    if lat is None or lng is None:
        lat = _float_or_none(rep.get("latitud"))
        lng = _float_or_none(rep.get("longitud"))
    creado_texto = _text(rep.get("fechaCreacion"))
    return {
        "id": rid,
        "direccion": _text(rep.get("direccion")),
        "barrio": _text(rep.get("barrio")),
        "comuna": _text(rep.get("comuna")),
        "estado": _text(rep.get("estadoVerificacion")),
        "afectacion": _text(rep.get("afectacion")),
        "tipo_inmueble": _text(rep.get("tipoInmueble")),
        "nombre_edificio": _text(rep.get("nombreEdificio")),
        "lat": lat,
        "lng": lng,
        "creado": parse_fecha_es_co(creado_texto),
        "creado_texto": creado_texto,
        "habitabilidad": _text(rep.get("habitabilidad")),
        "visitado": _text(rep.get("visitado")).lower() in ("sí", "si", "true", "1"),
        "pudo_evaluar": _text(rep.get("pudoEvaluar")),
        "alcance": _text(rep.get("alcanceInspeccion")),
        "descripcion": mask_digit_runs(_text(rep.get("descripcion")))[:DESCRIPCION_MAX],
        "sticker": {out_key: _text(sticker.get(in_key)) for in_key, out_key in _STICKER_KEYS},
    }


def build_snapshot(records: Iterable[object]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rep in records:
        row = project_reporte(rep)
        if row is not None:
            out.append(row)
    return out
```

- [ ] **Step 4: Cablear en `dashboard_refresh.py`**

Agregar `("reportes_ciudadanos.json", "data/reportes_ciudadanos.json"),` a `_PUBLISH_FILES` después de la entrada de `reportes_meta.json`. Importar `from app.services.reportes_ciudadanos import build_snapshot`. En `fetch_reportes`, inmediatamente después de la escritura de `reportes.json` (misma guarda de "0 registros → no escribir"), agregar:

```python
    # Lightweight public projection for the "Reportes ciudadanos" tab
    # (design D5); same never-publish-empty guard as reportes.json above.
    ciudadanos = build_snapshot(records)
    _write_json(out_dir / "reportes_ciudadanos.json", ciudadanos)
```

Usar el helper de escritura JSON que el módulo ya use para `reportes.json` (nombre y firma reales; `_write_json(path, payload)` es ilustrativo).

- [ ] **Step 5: Verificar que pasan**

Run: `python -m pytest backend/tests/services/test_reportes_ciudadanos.py backend/tests/jobs/test_dashboard_refresh.py -v`
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/reportes_ciudadanos.py backend/app/jobs/dashboard_refresh.py backend/tests/services/test_reportes_ciudadanos.py backend/tests/jobs/test_dashboard_refresh.py
git commit -m "feat(refresh): snapshot reportes_ciudadanos.json proyectado desde informe/json"
```

---

### Task 7: Lógica pura de la pestaña Reportes ciudadanos

**Files:**
- Create: `web/js/reportes-ciudadanos.js` (solo la parte pura en esta tarea; la UI se agrega en Task 8 en el mismo archivo)
- Test: `web/js/reportes-ciudadanos.test.mjs`

**Interfaces:**
- Consumes: filas de `reportes_ciudadanos.json` (Task 6), `COLORS`, `normalize` de `utils.js`.
- Produces: `ESTADOS`, `estadoDe(r)`, `AFECTACIONES`, `colorSticker(r)`, `contarPor(list, keyFn)`, `opcionesDe(list, campo)`, `applyFiltrosReportes(list, filtros)`, `COLOR_MODES_REPORTES`.

- [ ] **Step 1: Escribir los tests en rojo**

```javascript
// Self-check for the pure logic behind the Reportes ciudadanos tab.
// Run: node web/js/reportes-ciudadanos.test.mjs
import assert from 'node:assert';
import {
  ESTADOS, estadoDe, contarPor, opcionesDe, applyFiltrosReportes, colorSticker, COLOR_MODES_REPORTES,
} from './reportes-ciudadanos.js';

const r = (over = {}) => ({
  id: 'r1', direccion: 'Calle 1', barrio: 'San Antonio', comuna: 'Comuna 3', estado: 'Reportado',
  afectacion: 'DAÑO ESTRUCTURAL', tipo_inmueble: 'Casa', nombre_edificio: '', lat: 3.4, lng: -76.5,
  creado: '2026-08-18T18:33:00-05:00', creado_texto: '', habitabilidad: '', visitado: false, pudo_evaluar: '',
  alcance: '', descripcion: 'Grieta', sticker: { numero: '', color: '', etiqueta: '', origen: '', clasificacion: '' },
  ...over,
});

// Six API states, worst-known first for the KPI row.
assert.deepStrictEqual(ESTADOS.map((e) => e.key), [
  'Visitado crítico', 'Evaluación especializada', 'Visitado', 'Visita fallida', 'Asignado', 'Reportado']);
assert.strictEqual(estadoDe(r()).key, 'Reportado');
assert.strictEqual(estadoDe(r({ estado: 'visitado crítico' })).key, 'Visitado crítico');
assert.strictEqual(estadoDe(r({ estado: '' })).key, 'SIN_DATO');
assert.strictEqual(estadoDe(r({ estado: 'Otro' })).key, 'SIN_DATO');

// Counting by any key function; unknown buckets are kept.
const counts = contarPor([r(), r({ estado: 'Asignado' }), r({ estado: '' })], (x) => estadoDe(x).key);
assert.deepStrictEqual(counts, { Reportado: 1, Asignado: 1, SIN_DATO: 1 });

// Option lists are sorted, deduped, and skip blanks.
assert.deepStrictEqual(opcionesDe([r({ comuna: 'Comuna 3' }), r({ comuna: 'Comuna 19' }), r({ comuna: '' }), r({ comuna: 'Comuna 3' })], 'comuna'),
  ['Comuna 19', 'Comuna 3']);

// Filters: every dimension, AND-combined, free text over address/name/id.
const list = [r(), r({ id: 'r2', estado: 'Visitado', afectacion: 'COLAPSO TOTAL', tipo_inmueble: 'Edificio', comuna: 'Comuna 19',
  barrio: 'Granada', nombre_edificio: 'Torre Sol', sticker: { numero: 'x', color: 'rojo', etiqueta: 'No habitable', origen: 'sistema', clasificacion: '' } })];
assert.strictEqual(applyFiltrosReportes(list, {}).length, 2);
assert.deepStrictEqual(applyFiltrosReportes(list, { estado: 'Visitado' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { afectacion: 'COLAPSO TOTAL' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { tipo: 'Casa' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes(list, { comuna: 'Comuna 19' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { barrio: 'Granada' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { sticker: 'rojo' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { sticker: 'SIN_STICKER' }).map((x) => x.id), ['r1']);
assert.deepStrictEqual(applyFiltrosReportes(list, { search: 'torre sol' }).map((x) => x.id), ['r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { search: 'CALLE' }).map((x) => x.id), ['r1', 'r2']);
assert.deepStrictEqual(applyFiltrosReportes(list, { estado: 'Visitado', comuna: 'Comuna 3' }), []);
assert.deepStrictEqual(applyFiltrosReportes([], { estado: 'Visitado' }), []);

// Sticker colour mode: API colour names to the Panel ramp; blank is "unknown".
assert.notStrictEqual(colorSticker(r({ sticker: { color: 'rojo' } })), colorSticker(r()));
assert.strictEqual(colorSticker(r({ sticker: { color: 'ROJO' } })), colorSticker(r({ sticker: { color: 'rojo' } })));
assert.strictEqual(colorSticker(r({ sticker: null })), colorSticker(r()));
assert.deepStrictEqual(Object.keys(COLOR_MODES_REPORTES), ['estado', 'afectacion', 'sticker']);
console.log('reportes-ciudadanos.test.mjs OK');
```

- [ ] **Step 2: Verificar que fallan**

Run: `node web/js/reportes-ciudadanos.test.mjs`
Expected: `Error [ERR_MODULE_NOT_FOUND]: Cannot find module '.../web/js/reportes-ciudadanos.js'`.

- [ ] **Step 3: Implementar la parte pura**

```javascript
// Reportes ciudadanos tab: every citizen report from atencionsismo
// informe/json, read from the public Blob snapshot reportes_ciudadanos.json
// (backend/app/services/reportes_ciudadanos.py, design D5/D6). Same shape
// of module as evaluaciones.js: pure classification/filter helpers exported
// for the Node self-check, then the DOM/Leaflet section below.
import { COLORS, escapeHtml, basemapTileUrl, normalize, loadXlsx, downloadStamp, showToast } from './utils.js';

const TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
const CALI_CENTER = [3.42, -76.53];
const CALI_ZOOM = 12;
const CALI_BBOX = { latMin: 3.30, latMax: 3.55, lngMin: -76.60, lngMax: -76.40 };
const PAGE_SIZE = 100;

// API `estadoVerificacion` labels, worst-known first (KPI row order).
export const ESTADOS = [
  { key: 'Visitado crítico', label: 'visitado crítico', color: COLORS.status.i2 },
  { key: 'Evaluación especializada', label: 'evaluación especializada', color: COLORS.accent },
  { key: 'Visitado', label: 'visitado', color: COLORS.status.h },
  { key: 'Visita fallida', label: 'visita fallida', color: '#9AA5B1' },
  { key: 'Asignado', label: 'asignado', color: '#5B8DEF' },
  { key: 'Reportado', label: 'reportado', color: COLORS.unknown },
];
const SIN_ESTADO = { key: 'SIN_DATO', label: 'sin dato', color: COLORS.unknown };
const ESTADO_BY_NORM = new Map(ESTADOS.map((e) => [normalize(e.key), e]));

export function estadoDe(reporte) {
  return ESTADO_BY_NORM.get(normalize(String((reporte && reporte.estado) || ''))) || SIN_ESTADO;
}

// Self-declared damage at intake, worst first. Used for the distribution bar
// and the "afectación" colour mode; NOT a technical severity (see the
// exploratory report: use habitabilidad / sticker for that).
export const AFECTACIONES = [
  { key: 'COLAPSO TOTAL', color: COLORS.status.i2 },
  { key: 'COLAPSO PARCIAL', color: '#E0603C' },
  { key: 'RIESGO COLAPSO', color: COLORS.status.r2 },
  { key: 'DAÑO ESTRUCTURAL', color: '#E8B04B' },
  { key: 'DAÑO MAMPOSTERÍA', color: '#B8C56B' },
  { key: 'NO SE EVIDENCIA NINGÚN DAÑO', color: COLORS.status.h },
];
const AFECTACION_BY_NORM = new Map(AFECTACIONES.map((a) => [normalize(a.key), a]));
const colorAfectacion = (r) => (AFECTACION_BY_NORM.get(normalize(String((r && r.afectacion) || ''))) || SIN_ESTADO).color;

const STICKER_COLORS = { verde: COLORS.status.h, amarillo: COLORS.status.r2, rojo: COLORS.status.i2 };
export function colorSticker(reporte) {
  const c = String((reporte && reporte.sticker && reporte.sticker.color) || '').trim().toLowerCase();
  return STICKER_COLORS[c] || COLORS.unknown;
}

export const COLOR_MODES_REPORTES = {
  estado: { label: 'Estado', colorOf: (r) => estadoDe(r).color },
  afectacion: { label: 'Afectación', colorOf: colorAfectacion },
  sticker: { label: 'Sticker', colorOf: colorSticker },
};

export function contarPor(list, keyFn) {
  const out = {};
  for (const item of list) {
    const k = keyFn(item);
    out[k] = (out[k] || 0) + 1;
  }
  return out;
}

export function opcionesDe(list, campo) {
  const set = new Set();
  for (const r of list) {
    const v = String((r && r[campo]) || '').trim();
    if (v) set.add(v);
  }
  return [...set].sort((a, b) => a.localeCompare(b, 'es'));
}

export function applyFiltrosReportes(list, filtros) {
  const f = filtros || {};
  const q = f.search ? normalize(f.search) : '';
  return list.filter((r) => {
    if (f.estado && estadoDe(r).key !== f.estado) return false;
    if (f.afectacion && normalize(r.afectacion || '') !== normalize(f.afectacion)) return false;
    if (f.tipo && r.tipo_inmueble !== f.tipo) return false;
    if (f.comuna && r.comuna !== f.comuna) return false;
    if (f.barrio && r.barrio !== f.barrio) return false;
    if (f.sticker) {
      const color = String((r.sticker && r.sticker.color) || '').trim().toLowerCase();
      if (f.sticker === 'SIN_STICKER' ? color !== '' : color !== f.sticker) return false;
    }
    if (q) {
      const hay = normalize([r.direccion, r.nombre_edificio, r.barrio, r.id, r.sticker && r.sticker.numero]
        .filter(Boolean).join(' '));
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}
```

- [ ] **Step 4: Verificar que pasan**

Run: `node web/js/reportes-ciudadanos.test.mjs`
Expected: `reportes-ciudadanos.test.mjs OK`. Si `utils.js` o `data.js` no se pueden importar en Node por acceso a `document`/`window` en el nivel superior, mover los imports de `fetchData` y helpers DOM a la parte de UI de la Task 8 dejando en la parte pura solo `COLORS` y `normalize` (que `evaluaciones.test.mjs` ya prueba importables).

- [ ] **Step 5: Commit**

```bash
git add web/js/reportes-ciudadanos.js web/js/reportes-ciudadanos.test.mjs
git commit -m "feat(reportes): logica pura de estados, filtros y colores para Reportes ciudadanos"
```

---

### Task 8: UI de la pestaña Reportes ciudadanos

**Files:**
- Modify: `web/index.html` (nav líneas 70 a 78; secciones líneas 278 a 280)
- Modify: `web/js/main.js` (imports líneas 12 a 18; `switchView` líneas 224 a 247)
- Modify: `web/js/reportes-ciudadanos.js` (agregar la sección de UI al final)
- Modify: `web/styles.css` (bloque `.rep-*`, reutilizando `.eval-*`)
- Test: verificación manual con Playwright (`npm run test:e2e` si existe un spec de humo; si no, revisión manual guiada abajo)

**Interfaces:**
- Consumes: `fetchData('reportes_ciudadanos.json')`, `fetchData('reportes_meta.json')`, helpers de Task 7.
- Produces: `initReportesCiudadanos(root, { fetchReportes }) -> { invalidate }`; `sectionHtml()`.

- [ ] **Step 1: Registrar la pestaña**

`web/index.html`, después del botón `stickers` (línea 73):

```html
      <button type="button" class="view-tab" data-view="reportes-ciudadanos" role="tab" aria-selected="false">Reportes ciudadanos</button>
```

Después de la sección `view-stickers` (línea 279):

```html
    <!-- ============ REPORTES CIUDADANOS VIEW ============ -->
    <section id="view-reportes-ciudadanos" data-view-panel="reportes-ciudadanos" aria-label="Reportes ciudadanos" hidden></section>
```

`web/js/main.js`: import `import { initReportesCiudadanos } from './reportes-ciudadanos.js';` junto a `initStickers`, y en `switchView` después del bloque de `stickers`:

```javascript
  // Reportes ciudadanos reads the public Blob snapshot (no token) — (re)load
  // it each time it opens, same lifecycle as Stickers.
  if (view === 'reportes-ciudadanos') {
    initReportesCiudadanos(document.getElementById('view-reportes-ciudadanos'), {
      fetchReportes: async () => {
        const [datos, meta] = await Promise.all([
          fetchData('reportes_ciudadanos.json').then((r) => (r.ok ? r.json() : [])),
          fetchData('reportes_meta.json').then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ]);
        return { reportes: Array.isArray(datos) ? datos : [], meta };
      },
    });
  }
```

- [ ] **Step 2: Implementar la UI en `reportes-ciudadanos.js`**

Agregar al final del archivo:

```javascript
// ── DOM / Leaflet section ─────────────────────────────────────────────────

let map = null;
let baseTile = null;
let pointsLayer = null;
let lastFitBounds = null;
let colorMode = 'estado';

function teardownMap() {
  if (map) { map.remove(); map = null; baseTile = null; pointsLayer = null; }
}

function formatFecha(iso, fallback) {
  if (!iso) return fallback || 'Sin fecha';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? (fallback || String(iso)) : d.toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' });
}

const pct = (part, total) => (total ? Math.round((part / total) * 1000) / 10 : 0);

function selectHtml(id, label, opciones, todos) {
  return `<label class="eval-filter"><span>${escapeHtml(label)}</span>
    <select id="${id}"><option value="">${escapeHtml(todos)}</option>
    ${opciones.map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join('')}</select></label>`;
}

export function sectionHtml() {
  return `
    <section class="eval-section rep-section">
      <header class="sticker-page-head">
        <h2 class="sticker-h1">Reportes ciudadanos</h2>
        <p class="sticker-lead" id="rep-freshness">Reportes de ingreso vía atencionsismo.cali.gov.co.</p>
      </header>
      <div class="eval-kpis" id="rep-kpis"></div>
      <div class="eval-bar" id="rep-bar" aria-label="Distribución por afectación"></div>
      <div class="eval-toolbar">
        <div class="eval-filters" id="rep-filters"></div>
        <div class="asignacion-segmented" role="tablist" aria-label="Colorear por" id="rep-color-mode"></div>
        <button type="button" class="btn" id="rep-download">Descargar .xlsx</button>
      </div>
      <div class="eval-workspace">
        <div class="eval-map" id="rep-map" role="region" aria-label="Mapa de reportes"></div>
        <aside class="eval-list-wrap">
          <p class="eval-count" id="rep-count"></p>
          <ol class="eval-list" id="rep-list"></ol>
          <button type="button" class="btn" id="rep-more" hidden>Mostrar más</button>
        </aside>
      </div>
      <dialog class="eval-detail" id="rep-detail"></dialog>
    </section>`;
}

function kpisHtml(list) {
  const counts = contarPor(list, (r) => estadoDe(r).key);
  return [...ESTADOS, SIN_ESTADO].map((e) => `
    <div class="eval-kpi"><span class="eval-kpi-dot" style="background:${e.color}"></span>
      <strong>${(counts[e.key] || 0).toLocaleString('es-CO')}</strong><span>${escapeHtml(e.label)}</span></div>`).join('');
}

function barHtml(list) {
  const counts = contarPor(list, (r) => normalize(r.afectacion || ''));
  const total = list.length;
  return AFECTACIONES.map((a) => {
    const n = counts[normalize(a.key)] || 0;
    return `<span class="eval-bar-seg" style="width:${pct(n, total)}%;background:${a.color}" title="${escapeHtml(a.key)}: ${n}"></span>`;
  }).join('');
}

function listItemHtml(r) {
  const e = estadoDe(r);
  return `<li class="eval-row" data-rep-id="${escapeHtml(r.id)}">
    <span class="eval-dot" style="background:${COLOR_MODES_REPORTES[colorMode].colorOf(r)}"></span>
    <div class="eval-row-main">
      <strong>${escapeHtml(r.direccion || 'Sin dirección')}</strong>
      <span class="eval-meta">${escapeHtml(r.barrio || 'Sin barrio')} · ${escapeHtml(r.comuna || 'Sin comuna')} · ${escapeHtml(r.tipo_inmueble || '')}</span>
      <span class="eval-pill" style="border-color:${e.color}">${escapeHtml(e.label)}</span>
      <span class="eval-pill">${escapeHtml(r.afectacion || 'sin afectación')}</span>
      ${r.sticker && r.sticker.color ? `<span class="eval-pill" style="border-color:${colorSticker(r)}">sticker ${escapeHtml(r.sticker.etiqueta || r.sticker.color)}</span>` : ''}
    </div>
    <button type="button" class="sticker-action" data-rep-detail="${escapeHtml(r.id)}">Ver detalle</button>
  </li>`;
}

function popupHtml(r) {
  return `<strong>${escapeHtml(r.direccion || 'Sin dirección')}</strong><br>${escapeHtml(estadoDe(r).label)} · ${escapeHtml(r.afectacion || '')}
    <br><button type="button" class="link" data-rep-detail="${escapeHtml(r.id)}">Ver detalle</button>`;
}

function detailHtml(r) {
  const rows = [
    ['Estado', estadoDe(r).label], ['Afectación declarada', r.afectacion], ['Tipo de inmueble', r.tipo_inmueble],
    ['Edificio', r.nombre_edificio], ['Barrio', r.barrio], ['Comuna', r.comuna],
    ['Creado', formatFecha(r.creado, r.creado_texto)], ['Visitado', r.visitado ? 'Sí' : 'No'],
    ['Pudo evaluar', r.pudo_evaluar], ['Alcance', r.alcance], ['Habitabilidad', r.habitabilidad],
    ['Sticker', r.sticker && r.sticker.numero ? `${r.sticker.numero} · ${r.sticker.etiqueta} · ${r.sticker.origen}` : 'Sin sticker'],
    ['Coordenadas', r.lat != null && r.lng != null ? `${r.lat}, ${r.lng}` : ''],
    ['Descripción', r.descripcion],
  ];
  return `<form method="dialog" class="eval-detail-body">
    <header><h3>${escapeHtml(r.direccion || 'Sin dirección')}</h3><span class="eval-detail-code">${escapeHtml(r.id)}</span>
      <button type="submit" class="btn" aria-label="Cerrar">×</button></header>
    <dl>${rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v || 'Sin dato'))}</dd>`).join('')}</dl>
  </form>`;
}

function renderMap(containerId, list, onDetail) {
  teardownMap();
  map = L.map(containerId, { zoomControl: true, minZoom: 10, maxZoom: 18, preferCanvas: true }).setView(CALI_CENTER, CALI_ZOOM);
  baseTile = L.tileLayer(basemapTileUrl(), { attribution: TILE_ATTRIBUTION, subdomains: 'abcd', maxZoom: 20 }).addTo(map);
  pointsLayer = L.layerGroup().addTo(map);
  const inCali = [];
  for (const r of list) {
    if (r.lat == null || r.lng == null) continue;
    const marker = L.circleMarker([r.lat, r.lng], {
      radius: 5, color: '#0B1D33', weight: 0.5, fillColor: COLOR_MODES_REPORTES[colorMode].colorOf(r), fillOpacity: 0.85,
    });
    marker.bindPopup(popupHtml(r), { maxWidth: 260 });
    marker.on('popupopen', (ev) => {
      const btn = ev.popup.getElement().querySelector('[data-rep-detail]');
      if (btn) btn.addEventListener('click', () => onDetail(r.id));
    });
    marker.addTo(pointsLayer);
    if (r.lat >= CALI_BBOX.latMin && r.lat <= CALI_BBOX.latMax && r.lng >= CALI_BBOX.lngMin && r.lng <= CALI_BBOX.lngMax) inCali.push([r.lat, r.lng]);
  }
  lastFitBounds = inCali.length ? L.latLngBounds(inCali) : null;
  if (lastFitBounds) map.fitBounds(lastFitBounds, { padding: [30, 30], maxZoom: 15 });
}

// One module-level theme listener (not per render): the tab owns its own
// Leaflet instance, so mapview.applyMapTheme() never reaches this tile layer.
// Guarded so the Node self-check can import the module.
if (typeof document !== 'undefined') {
  document.addEventListener('themechange', () => { if (baseTile) baseTile.setUrl(basemapTileUrl()); });
}

export function initReportesCiudadanos(root, { fetchReportes }) {
  root.innerHTML = sectionHtml();
  const $ = (id) => root.querySelector(`#${id}`);
  const filtros = { estado: '', afectacion: '', tipo: '', comuna: '', barrio: '', sticker: '', search: '' };
  let todos = [];
  let visibles = [];
  let shown = 0;
  const byId = new Map();

  function openDetail(id) {
    const r = byId.get(id);
    if (!r) return;
    const dlg = $('rep-detail');
    dlg.innerHTML = detailHtml(r);
    dlg.showModal();
  }

  function renderList(reset) {
    const list = $('rep-list');
    if (reset) { list.innerHTML = ''; shown = 0; }
    const next = visibles.slice(shown, shown + PAGE_SIZE);
    list.insertAdjacentHTML('beforeend', next.map(listItemHtml).join(''));
    shown += next.length;
    $('rep-more').hidden = shown >= visibles.length;
    $('rep-count').textContent = `${visibles.length.toLocaleString('es-CO')} de ${todos.length.toLocaleString('es-CO')} reportes`;
  }

  function render() {
    visibles = applyFiltrosReportes(todos, filtros);
    $('rep-kpis').innerHTML = kpisHtml(visibles);
    $('rep-bar').innerHTML = barHtml(visibles);
    renderList(true);
    renderMap('rep-map', visibles, openDetail);
  }

  function renderFilters() {
    const opt = (values) => values.map((v) => ({ value: v, label: v }));
    $('rep-filters').innerHTML = [
      `<label class="eval-filter eval-filter-search"><span>Buscar</span><input id="rep-search" type="search" placeholder="Dirección, edificio, barrio, sticker"></label>`,
      selectHtml('rep-estado', 'Estado', ESTADOS.map((e) => ({ value: e.key, label: e.label })), 'Todos'),
      selectHtml('rep-afectacion', 'Afectación', AFECTACIONES.map((a) => ({ value: a.key, label: a.key.toLowerCase() })), 'Todas'),
      selectHtml('rep-tipo', 'Inmueble', opt(opcionesDe(todos, 'tipo_inmueble')), 'Todos'),
      selectHtml('rep-comuna', 'Comuna', opt(opcionesDe(todos, 'comuna')), 'Todas'),
      selectHtml('rep-barrio', 'Barrio', opt(opcionesDe(todos, 'barrio')), 'Todos'),
      selectHtml('rep-sticker', 'Sticker', [{ value: 'verde', label: 'verde' }, { value: 'amarillo', label: 'amarillo' }, { value: 'rojo', label: 'rojo' }, { value: 'SIN_STICKER', label: 'sin sticker' }], 'Todos'),
    ].join('');
    const bind = (id, key) => $(id).addEventListener('change', (ev) => { filtros[key] = ev.target.value; render(); });
    bind('rep-estado', 'estado'); bind('rep-afectacion', 'afectacion'); bind('rep-tipo', 'tipo');
    bind('rep-comuna', 'comuna'); bind('rep-barrio', 'barrio'); bind('rep-sticker', 'sticker');
    let t = null;
    $('rep-search').addEventListener('input', (ev) => { clearTimeout(t); t = setTimeout(() => { filtros.search = ev.target.value; render(); }, 250); });
  }

  function renderColorMode() {
    $('rep-color-mode').innerHTML = Object.entries(COLOR_MODES_REPORTES).map(([key, def]) => `
      <button type="button" class="asignacion-segment${key === colorMode ? ' is-active' : ''}" data-rep-color="${key}" role="tab" aria-selected="${key === colorMode}">${escapeHtml(def.label)}</button>`).join('');
    root.querySelectorAll('[data-rep-color]').forEach((btn) => btn.addEventListener('click', () => {
      colorMode = btn.dataset.repColor;
      renderColorMode();
      render();
    }));
  }

  root.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-rep-detail]');
    if (btn) openDetail(btn.dataset.repDetail);
  });
  $('rep-more').addEventListener('click', () => renderList(false));
  $('rep-download').addEventListener('click', async () => {
    let XLSX;
    try { XLSX = await loadXlsx(); } catch { showToast('No se pudo cargar el generador de Excel.', 'error'); return; }
    const rows = visibles.map((r) => ({
      id: r.id, estado: r.estado, afectacion: r.afectacion, tipo_inmueble: r.tipo_inmueble, direccion: r.direccion,
      nombre_edificio: r.nombre_edificio, barrio: r.barrio, comuna: r.comuna, lat: r.lat, lng: r.lng, creado: r.creado,
      habitabilidad: r.habitabilidad, visitado: r.visitado ? 'Sí' : 'No', pudo_evaluar: r.pudo_evaluar, alcance: r.alcance,
      sticker_numero: r.sticker.numero, sticker_color: r.sticker.color, sticker_etiqueta: r.sticker.etiqueta,
      sticker_origen: r.sticker.origen, descripcion: r.descripcion,
    }));
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), 'Reportes');
    XLSX.writeFile(wb, `reportes_ciudadanos_${downloadStamp().slug}.xlsx`);
  });

  (async () => {
    $('rep-count').textContent = 'Cargando…';
    try {
      const { reportes, meta } = await fetchReportes();
      todos = reportes.filter((r) => r && r.id);
      byId.clear();
      for (const r of todos) byId.set(r.id, r);
      if (meta && meta.generated_at) {
        $('rep-freshness').textContent = `Reportes de ingreso vía atencionsismo.cali.gov.co · actualizado ${formatFecha(meta.generated_at)} · ${Number(meta.row_count || todos.length).toLocaleString('es-CO')} registros`;
      }
      renderFilters();
      renderColorMode();
      render();
    } catch (err) {
      $('rep-count').textContent = `No se pudieron cargar los reportes: ${err.message || err}`;
    }
  })();

  return {
    invalidate: () => {
      if (!map) return;
      map.invalidateSize();
      if (lastFitBounds) map.fitBounds(lastFitBounds, { padding: [30, 30], maxZoom: 15 });
    },
  };
}
```

`web/styles.css`, al final del bloque `.eval-*`:

```css
.rep-section .eval-list-wrap { max-height: 70vh; overflow: auto; }
.rep-section .eval-filter-search input { min-width: 220px; }
#view-reportes-ciudadanos { max-width: 1180px; margin: 0 auto; }
```

- [ ] **Step 3: Verificar que el self-check sigue verde**

Run: `node web/js/reportes-ciudadanos.test.mjs`
Expected: `reportes-ciudadanos.test.mjs OK`. Si la importación de `data.js` rompe en Node, aplicar la nota de la Task 7 Step 4.

- [ ] **Step 4: Verificación manual en navegador**

Servir `web/` (el mismo comando que se use hoy para el dashboard local, por ejemplo `npx serve web`) y comprobar:

1. La pestaña "Reportes ciudadanos" aparece después de "Stickers" y abre sin errores en consola.
2. KPIs por estado suman el total de la línea "N de M reportes".
3. Cambiar "Colorear por" recolorea mapa y lista.
4. Cada filtro reduce la lista y el mapa; "Mostrar más" agrega 100 filas.
5. "Ver detalle" desde la lista y desde un popup abre el diálogo con "Creado" formateado.
6. Descargar .xlsx produce un archivo con las columnas del export.
7. Sin `reportes_ciudadanos.json` en el Blob (antes del primer refresh), el estado vacío muestra el mensaje de error y no rompe la página.
8. Vista móvil: sin scroll horizontal.

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/js/main.js web/js/reportes-ciudadanos.js web/styles.css
git commit -m "feat(reportes): pestaña Reportes ciudadanos con mapa, KPIs, filtros, detalle y export"
```

---

### Task 9: Documentación y cierre

**Files:**
- Modify: `docs/informe-exploratorio-api-atencionsismo.md` (sección 6 "Próximos pasos")
- Modify: `web/js/analista.js` (`rowAtencionsismo`, línea 76: la descripción)

- [ ] **Step 1: Actualizar la descripción de la fila en Analista**

Cambiar el texto `descripcion` de `rowAtencionsismo` a: `'Reportes ciudadanos vía atencionsismo.cali.gov.co; alimenta el KPI "Reportados", la pestaña Reportes ciudadanos y la fuente Atención Sismo de Stickers.'`.

- [ ] **Step 2: Cerrar el informe exploratorio**

Reemplazar la sección 6 por el resultado de la Task 0 (tres respuestas) y una línea que apunte a la spec y a este plan.

- [ ] **Step 3: Commit**

```bash
git add docs/informe-exploratorio-api-atencionsismo.md web/js/analista.js
git commit -m "docs(atencionsismo): cierre del informe exploratorio y descripcion de fuente en Analista"
```

---

## Despliegue

1. Cargar `VISITADOS_API_USER` y `VISITADOS_API_PASS` (cuenta v2) en Railway (servicio web y cron) y en Vercel.
2. Desplegar el backend (PR 1) y verificar `GET /stickers-atencionsismo` con un token de admin: 200, `degraded: false`, filas con `inspector.np` no vacío para origen `firebase`.
3. Desplegar `web/` (PR 2) y comprobar Stickers con ambas fuentes.
4. Desplegar el job (PR 3), disparar un refresh manual y confirmar `data/reportes_ciudadanos.json` en el Blob.
5. Desplegar `web/` (PR 4).
