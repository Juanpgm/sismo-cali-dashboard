"""Knowledge-graph tooling for the demolition-criteria pipeline.

Three jobs:

    python knowledge/build_kg.py --extract   # dump fuentes/*.pdf -> fuentes/*.txt
    python knowledge/build_kg.py --check     # validate kg.json integrity
    python knowledge/build_kg.py --docs      # regenerate docs/*.md + embed into grafo.html

`kg.json` is a curated graph (hand-authored from the extracted sources, every
node and edge carries a citation) that `analisis_texto.py` consumes. The
extractor exists so the curation is reproducible and auditable: each page is
marked `[[pag N]]` in the .txt, and citations in the graph reference those
pages.

Schema:
    nodes: [{id, tipo, nombre, descripcion?, fuente: {doc, ref}}]
        tipo in {patologia, elemento, criterio_norma, accion, termino}
        termino nodes also carry `patron` (regex fragment, case-insensitive).
    edges: [{de, a, relacion, peso?, fuente?}]
        relacion in {indica, afecta, sugiere, detecta, pondera}
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
FUENTES = HERE / "fuentes"
DOCS_DIR = HERE / "docs"
KG_PATH = HERE / "kg.json"
GRAFO_HTML = HERE / "grafo.html"

TIPOS = {"patologia", "elemento", "criterio_norma", "accion", "termino"}
RELACIONES = {"indica", "afecta", "sugiere", "detecta", "pondera"}
DOCS = {"NSR-10", "AIS-manual", "ATC-20"}
TIPO_LABEL = {"patologia": "Patologías", "elemento": "Elementos",
              "criterio_norma": "Criterios normativos", "accion": "Acciones",
              "termino": "Términos de detección"}
REL_LABEL = {"detecta": "detecta", "indica": "indica", "sugiere": "sugiere",
             "pondera": "pondera (criticidad)", "afecta": "afecta"}


def extract() -> None:
    from pypdf import PdfReader

    for pdf in sorted(FUENTES.glob("*.pdf")):
        out = pdf.with_suffix(".txt")
        pages = []
        for i, page in enumerate(PdfReader(pdf).pages, 1):
            pages.append(f"[[pag {i}]]\n{page.extract_text() or ''}")
        out.write_text("\n".join(pages), encoding="utf-8")
        print(f"{pdf.name}: {i} pages -> {out.name}")


def check() -> None:
    kg = json.loads(KG_PATH.read_text(encoding="utf-8"))
    nodes, edges = kg["nodes"], kg["edges"]
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids)), "duplicate node ids"
    idset = set(ids)
    for n in nodes:
        assert n["tipo"] in TIPOS, f"{n['id']}: bad tipo {n['tipo']}"
        f = n.get("fuente")
        assert f and f.get("doc") in DOCS and f.get("ref"), \
            f"{n['id']}: missing/invalid fuente"
        if n["tipo"] == "termino":
            assert n.get("patron"), f"{n['id']}: termino sin patron"
            re.compile(n["patron"], re.IGNORECASE)  # must be valid regex
    for e in edges:
        assert e["de"] in idset and e["a"] in idset, f"dangling edge {e}"
        assert e["relacion"] in RELACIONES, f"bad relacion {e}"
        if e["relacion"] in ("indica", "pondera"):
            assert isinstance(e.get("peso"), (int, float)) and 0 <= e["peso"] <= 1, \
                f"{e['de']}->{e['a']}: peso 0-1 requerido"
    # every termino must detect something; every patologia should be detectable
    det_from = {e["de"] for e in edges if e["relacion"] == "detecta"}
    det_to = {e["a"] for e in edges if e["relacion"] == "detecta"}
    for n in nodes:
        if n["tipo"] == "termino":
            assert n["id"] in det_from, f"{n['id']}: termino sin arista detecta"
        if n["tipo"] == "patologia":
            assert n["id"] in det_to, f"{n['id']}: patologia sin termino que la detecte"
    n_by_tipo = {}
    for n in nodes:
        n_by_tipo[n["tipo"]] = n_by_tipo.get(n["tipo"], 0) + 1
    print(f"kg.json ok: {len(nodes)} nodos {n_by_tipo} | {len(edges)} aristas")


def _slug(name: str) -> str:
    """ASCII, filename-safe slug (README.md sorts first via the caller)."""
    label = TIPO_LABEL.get(name, name)
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return ascii_label.lower().replace(" ", "-").replace("(", "").replace(")", "")


def gen_docs() -> None:
    """Regenerate docs/*.md (one per node type, human-readable, every claim
    cited) from kg.json, then embed kg.json + the docs into grafo.html so the
    page stays a single self-contained file."""
    kg = json.loads(KG_PATH.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in kg["nodes"]}
    out_edges: dict[str, list] = {}
    in_edges: dict[str, list] = {}
    for e in kg["edges"]:
        out_edges.setdefault(e["de"], []).append(e)
        in_edges.setdefault(e["a"], []).append(e)

    DOCS_DIR.mkdir(exist_ok=True)
    for f in DOCS_DIR.glob("*.md"):
        f.unlink()

    by_tipo: dict[str, list] = {}
    for n in kg["nodes"]:
        by_tipo.setdefault(n["tipo"], []).append(n)

    docs: dict[str, str] = {}
    idx = ["# Documentación del grafo de conocimiento", "",
           kg["meta"]["descripcion"], "", "## Fuentes primarias", ""]
    for doc, ref in kg["meta"]["fuentes"].items():
        idx.append(f"- **{doc}**: `{ref}`")
    idx += ["", "## Índice", ""]
    for tipo, label in TIPO_LABEL.items():
        idx.append(f"- [{label}](./{_slug(tipo)}.md) — {len(by_tipo.get(tipo, []))} nodos")
    docs["README.md"] = "\n".join(idx)

    for tipo, label in TIPO_LABEL.items():
        items = sorted(by_tipo.get(tipo, []), key=lambda n: n["nombre"])
        lines = [f"# {label}", "",
                 f"{len(items)} nodos del grafo de conocimiento. Fuente: "
                 "NSR-10 Título A (cap. A.10) y Guía AIS / Manual de campo "
                 "para inspección de edificaciones después de un sismo. "
                 "Cada entrada cita su referencia exacta.", ""]
        for n in items:
            lines.append(f"## {n['nombre']}")
            lines.append("")
            if n.get("descripcion"):
                lines.append(n["descripcion"])
                lines.append("")
            lines.append(f"> **Fuente:** {n['fuente']['doc']} — {n['fuente']['ref']}")
            lines.append("")
            if n.get("patron"):
                lines.append(f"**Patrón de detección:** `{n['patron']}`")
                lines.append("")
            rel = []
            for e in out_edges.get(n["id"], []):
                t = nodes[e["a"]]
                suf = f" (peso {e['peso']})" if e.get("peso") is not None else ""
                rel.append(f"- → **{REL_LABEL[e['relacion']]}** {t['nombre']} "
                           f"({TIPO_LABEL[t['tipo']]}){suf}")
            for e in in_edges.get(n["id"], []):
                s = nodes[e["de"]]
                suf = f" (peso {e['peso']})" if e.get("peso") is not None else ""
                rel.append(f"- ← **{REL_LABEL[e['relacion']]}** {s['nombre']} "
                           f"({TIPO_LABEL[s['tipo']]}){suf}")
            if rel:
                lines.append("**Conexiones:**")
                lines.extend(rel)
                lines.append("")
        text = "\n".join(lines)
        fname = f"{_slug(tipo)}.md"
        (DOCS_DIR / fname).write_text(text, encoding="utf-8")
        docs[fname] = text
    (DOCS_DIR / "README.md").write_text(docs["README.md"], encoding="utf-8")

    html = GRAFO_HTML.read_text(encoding="utf-8")
    kg_json = json.dumps({"nodes": kg["nodes"], "edges": kg["edges"]}, ensure_ascii=False)
    docs_json = json.dumps(docs, ensure_ascii=False)
    html = re.sub(
        r'(<script id="kg-data" type="application/json">)(.*?)(</script>)',
        lambda m: m.group(1) + kg_json + m.group(3), html, flags=re.S)
    if 'id="docs-data"' in html:
        html = re.sub(
            r'(<script id="docs-data" type="application/json">)(.*?)(</script>)',
            lambda m: m.group(1) + docs_json + m.group(3), html, flags=re.S)
    else:
        html = html.replace(
            '</body>',
            f'<script id="docs-data" type="application/json">{docs_json}</script>\n</body>')
    GRAFO_HTML.write_text(html, encoding="utf-8")
    print(f"docs: {len(docs)} archivos .md en {DOCS_DIR} | embebidos en {GRAFO_HTML.name}")


if __name__ == "__main__":
    if "--extract" in sys.argv:
        extract()
    elif "--docs" in sys.argv:
        check()
        gen_docs()
    else:
        check()
