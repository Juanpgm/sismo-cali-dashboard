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
    reportes = first.get("reportes") or []
    report["informe_json"] = {
        "kpis": first.get("kpis"),
        "cantidad_lote": first.get("cantidad"),
        "done": first.get("done"),
        "nextOffset": first.get("nextOffset"),
        "keys_reporte": sorted(reportes[0].keys()) if reportes else [],
        "tiene_sticker": bool(reportes) and all("sticker" in r for r in reportes),
        "sticker_origen": Counter((r.get("sticker") or {}).get("origen", "") for r in reportes),
        "sticker_color": Counter((r.get("sticker") or {}).get("color", "") for r in reportes),
        "fechaCreacion_muestra": [r.get("fechaCreacion") for r in reportes[:3]],
        "muestra": [_mask(r) for r in reportes[:3]],
    }

    stickers: list[dict] = []
    offset = 0
    while True:
        body = _page(client, "/api/informe/stickers", offset=offset, limit=200)
        stickers.extend(body.get("stickers", []))
        if body.get("done", True):
            break
        offset = body["nextOffset"]
    firebase = [s for s in stickers if s.get("origen") == "firebase"]
    sistema = [s for s in stickers if s.get("origen") == "sistema"]
    report["informe_stickers"] = {
        "total": len(stickers),
        "por_origen": Counter(s.get("origen", "") for s in stickers),
        "por_color": Counter(s.get("color", "") for s in stickers),
        "por_etiqueta": Counter(s.get("colorEtiqueta", "") for s in stickers),
        "firebase_numero_con_formato_nuestro": sum(1 for s in firebase if CODIGO_RE.match(s.get("numero") or "")),
        "firebase_numero_muestra": [s.get("numero") for s in firebase[:10]],
        "sistema_numero_muestra": [s.get("numero") for s in sistema[:10]],
        "sin_codigo": sum(1 for s in stickers if (s.get("numero") or "") in ("", "Sin código")),
        "sin_coords": sum(1 for s in stickers if not s.get("latitud") or not s.get("longitud")),
        "sin_direccion": sum(1 for s in stickers if (s.get("direccion") or "") in ("", "Sin dirección")),
        "keys_sticker": sorted(stickers[0].keys()) if stickers else [],
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
