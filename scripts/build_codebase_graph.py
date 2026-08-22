#!/usr/bin/env python3
"""Genera docs/codebase-graph.html: un visor interactivo autocontenido del grafo
de llamadas del codebase (nodos = funciones del proyecto, aristas = CALLS).

No consulta codebase-memory directamente (es un servidor MCP, no un CLI). Toma
como entrada un export JSON del grafo y lo transforma en el HTML.

USO
  1) Refrescar el indice (tras cambios de codigo):
       index_repository(repo_path='.', mode='full')     # via codebase-memory MCP
  2) Exportar las aristas CALLS a un JSON, con esta consulta:
       query_graph(
         "MATCH (a)-[:CALLS]->(b) WHERE a.file_path IS NOT NULL AND "
         "b.file_path IS NOT NULL RETURN a.name AS src, a.file_path AS sf, "
         "b.name AS dst, b.file_path AS df")
     Guardar la respuesta (objeto con claves 'columns' y 'rows') en un archivo,
     p.ej. graph_export.json.
  3) Generar el visor:
       python scripts/build_codebase_graph.py graph_export.json
       python scripts/build_codebase_graph.py graph_export.json docs/codebase-graph.html

El HTML es autocontenido (sin CDN): se abre con doble clic. docs/ no lo publica
Vercel (outputDirectory: web), asi que es un artefacto de desarrollo seguro.
"""
import json
import sys
from pathlib import Path

DEFAULT_OUT = "docs/codebase-graph.html"

# Area de un archivo -> color/filtro en el visor. None = builtin/externo (se descarta).
def area_of(fp):
    if not fp or fp.startswith("<"):
        return None
    if fp.startswith("web/"):
        return "dashboard"
    if fp.startswith("api/"):
        return "api"
    if fp.startswith("scripts/"):
        return "pipeline"
    if fp.startswith("formulario/"):
        return "formulario"
    if fp.startswith("knowledge/"):
        return "knowledge"
    if fp.endswith(".py"):  # python de nivel raiz (israel_to_cali.py, etc.)
        return "pipeline"
    return "other"


def build(rows):
    """rows: lista de [src, sf, dst, df]. Devuelve {nodes, edges} para el visor."""
    nodes = {}       # id -> {id,name,file,area}
    edge_set = set()
    edges = []       # (sid, did)

    def nid(name, file):
        return f"{file}::{name}"

    for row in rows:
        src, sf, dst, df = row[0], row[1], row[2], row[3]
        sa, da = area_of(sf), area_of(df)
        if not sa or not da:      # descarta aristas que tocan builtins/externos
            continue
        sid, did = nid(src, sf), nid(dst, df)
        if sid == did:
            continue
        if sid not in nodes:
            nodes[sid] = {"id": sid, "name": src, "file": sf, "area": sa}
        if did not in nodes:
            nodes[did] = {"id": did, "name": dst, "file": df, "area": da}
        key = f"{sid}->{did}"
        if key in edge_set:
            continue
        edge_set.add(key)
        edges.append((sid, did))

    deg = {k: 0 for k in nodes}
    for s, t in edges:
        deg[s] += 1
        deg[t] += 1

    node_arr = []
    for n in nodes.values():
        m = dict(n)
        m["deg"] = deg[n["id"]]
        node_arr.append(m)
    idx = {n["id"]: i for i, n in enumerate(node_arr)}
    edge_arr = [[idx[s], idx[t]] for (s, t) in edges]
    return {"nodes": node_arr, "edges": edge_arr}


def main():
    if len(sys.argv) < 2:
        sys.exit("uso: python scripts/build_codebase_graph.py <graph_export.json> [salida.html]")
    export = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rows = export["rows"] if isinstance(export, dict) else export
    out = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    data = build(rows)
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    Path(out).write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
    areas = {}
    for n in data["nodes"]:
        areas[n["area"]] = areas.get(n["area"], 0) + 1
    print(f"wrote {out} — nodes: {len(data['nodes'])} edges: {len(data['edges'])} areas: {areas}")


TEMPLATE = r'''<!doctype html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Grafo del codebase — sismo-cali-dashboard</title>
<style>
  :root{
    --bg:#0b1220; --panel:#111c30; --panel2:#16233b; --border:#25324c;
    --text:#e6edf7; --muted:#93a1b8; --accent:#FFC400;
    --dashboard:#4f9dff; --api:#f97362; --pipeline:#57c98a; --formulario:#c07cf0; --knowledge:#ffce54; --other:#8895ad;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;overflow:hidden}
  #app{display:flex;height:100vh}
  aside{width:300px;flex:0 0 300px;background:var(--panel);border-right:1px solid var(--border);padding:16px;overflow-y:auto}
  aside h1{font-size:15px;margin:0 0 2px}
  aside .sub{color:var(--muted);font-size:12px;margin:0 0 16px}
  .field{margin-bottom:14px}
  .field label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:6px}
  input[type=search]{width:100%;background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:8px 10px;font-size:13px}
  input[type=search]:focus{outline:none;border-color:var(--accent)}
  .chips{display:flex;flex-wrap:wrap;gap:6px}
  .chip{display:flex;align-items:center;gap:6px;background:var(--panel2);border:1px solid var(--border);border-radius:20px;padding:4px 10px;font-size:12px;cursor:pointer;user-select:none}
  .chip .dot{width:9px;height:9px;border-radius:50%}
  .chip.off{opacity:.4}
  .chip .n{color:var(--muted);font-size:11px}
  .stat{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);padding:2px 0}
  .stat b{color:var(--text)}
  .hint{font-size:11px;color:var(--muted);margin-top:6px}
  button.reset{width:100%;margin-top:8px;background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:8px;cursor:pointer;font-size:13px}
  button.reset:hover{border-color:var(--accent)}
  main{flex:1;position:relative}
  canvas{display:block;width:100%;height:100%;cursor:grab}
  canvas:active{cursor:grabbing}
  #tip{position:absolute;pointer-events:none;background:#0d1526f2;border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:12px;max-width:320px;display:none;z-index:5}
  #tip .t-name{font-weight:600;color:var(--accent);word-break:break-all}
  #tip .t-file{color:var(--muted);font-size:11px;word-break:break-all;margin-top:2px}
  #tip .t-deg{margin-top:4px}
  #detail{position:absolute;top:12px;right:12px;width:280px;background:#0d1526f2;border:1px solid var(--border);border-radius:10px;padding:12px 14px;font-size:12px;display:none;z-index:6}
  #detail .t-name{font-weight:600;color:var(--accent);word-break:break-all}
  #detail .t-file{color:var(--muted);font-size:11px;margin:2px 0 8px;word-break:break-all}
  #detail h4{margin:8px 0 4px;font-size:11px;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}
  #detail ul{margin:0;padding-left:16px;max-height:120px;overflow:auto}
  #detail li{cursor:pointer;word-break:break-all}
  #detail li:hover{color:var(--accent)}
  #detail .close{position:absolute;top:8px;right:10px;cursor:pointer;color:var(--muted);font-size:16px}
  .legend-foot{font-size:11px;color:var(--muted);margin-top:16px;border-top:1px solid var(--border);padding-top:10px}
</style></head>
<body>
<div id="app">
  <aside>
    <h1>Grafo del codebase</h1>
    <p class="sub">sismo-cali-dashboard · grafo de llamadas (CALLS)</p>
    <div class="field">
      <label>Buscar función</label>
      <input type="search" id="search" placeholder="nombre de función…" autocomplete="off">
    </div>
    <div class="field">
      <label>Áreas</label>
      <div class="chips" id="chips"></div>
    </div>
    <div class="field">
      <div class="stat"><span>Nodos visibles</span><b id="s-nodes">0</b></div>
      <div class="stat"><span>Enlaces visibles</span><b id="s-edges">0</b></div>
      <button class="reset" id="reset">Reencuadrar + reiniciar layout</button>
    </div>
    <p class="hint">Rueda = zoom · arrastrar fondo = desplazar · arrastrar nodo = mover · click = ver vecinos.</p>
    <div class="legend-foot">Tamaño del nodo = grado (nº de conexiones). Solo se muestran llamadas entre funciones del proyecto (builtins externos excluidos).</div>
  </aside>
  <main>
    <canvas id="cv"></canvas>
    <div id="tip"></div>
    <div id="detail"></div>
  </main>
</div>
<script>
const DATA = __DATA__;
const AREA_COLORS = {dashboard:'#4f9dff',api:'#f97362',pipeline:'#57c98a',formulario:'#c07cf0',knowledge:'#ffce54',other:'#8895ad'};
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip'), detail = document.getElementById('detail');
let W=0,H=0,DPR=Math.min(window.devicePixelRatio||1,2);
const N = DATA.nodes, E = DATA.edges;
// adjacency for neighbor highlight
const adj = N.map(()=>[]);
for (const [s,t] of E){ adj[s].push(t); adj[t].push(s); }

// layout state
for (const n of N){ n.x=(Math.random()-0.5)*800; n.y=(Math.random()-0.5)*800; n.vx=0; n.vy=0; n.r=3+Math.sqrt(n.deg)*1.6; }
let view={x:0,y:0,k:1};
let activeAreas = new Set(Object.keys(AREA_COLORS));
let query='';
let selected=null, hover=null;

function visible(n){ if(!activeAreas.has(n.area)) return false; if(query && !n.name.toLowerCase().includes(query)) return false; return true; }

function resize(){ W=cv.clientWidth; H=cv.clientHeight; cv.width=W*DPR; cv.height=H*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); }
window.addEventListener('resize',resize);

// force sim
let ticks=0, MAXTICKS=320;
function step(){
  const k=0.02, rep=1400, cl=0.02;
  for(let i=0;i<N.length;i++){
    const a=N[i]; let fx=0,fy=0;
    for(let j=0;j<N.length;j++){ if(i===j) continue; const b=N[j];
      let dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy+0.01; if(d2>90000) continue;
      const f=rep/d2; fx+=dx*f; fy+=dy*f; }
    a.vx=(a.vx+fx*0.0009)*0.85; a.vy=(a.vy+fy*0.0009)*0.85;
  }
  for(const [s,t] of E){ const a=N[s],b=N[t]; let dx=b.x-a.x,dy=b.y-a.y; const d=Math.sqrt(dx*dx+dy*dy)||1; const f=(d-60)*k; dx/=d;dy/=d; a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f; }
  for(const a of N){ a.vx-=a.x*cl*0.01; a.vy-=a.y*cl*0.01; a.x+=Math.max(-8,Math.min(8,a.vx)); a.y+=Math.max(-8,Math.min(8,a.vy)); }
}

function tx(x){ return (x-view.x)*view.k + W/2; }
function ty(y){ return (y-view.y)*view.k + H/2; }
function inv(px,py){ return {x:(px-W/2)/view.k+view.x, y:(py-H/2)/view.k+view.y}; }

function draw(){
  ctx.clearRect(0,0,W,H);
  const hl = selected!=null ? new Set([selected,...adj[selected]]) : null;
  // edges
  ctx.lineWidth=Math.max(0.4,0.6*view.k);
  for(const [s,t] of E){ const a=N[s],b=N[t]; if(!visible(a)||!visible(b)) continue;
    const on = hl ? (hl.has(s)&&hl.has(t)) : true;
    ctx.strokeStyle = on ? (hl?'rgba(255,196,0,0.5)':'rgba(120,140,175,0.18)') : 'rgba(120,140,175,0.05)';
    ctx.beginPath(); ctx.moveTo(tx(a.x),ty(a.y)); ctx.lineTo(tx(b.x),ty(b.y)); ctx.stroke();
  }
  // nodes
  for(const n of N){ if(!visible(n)) continue;
    const dim = hl && !hl.has(N.indexOf(n));
    const x=tx(n.x),y=ty(n.y),r=Math.max(2,n.r*view.k);
    ctx.beginPath(); ctx.arc(x,y,r,0,7);
    ctx.fillStyle=AREA_COLORS[n.area]||'#8895ad'; ctx.globalAlpha=dim?0.15:1;
    ctx.fill();
    if(n===hover||N.indexOf(n)===selected){ ctx.lineWidth=2; ctx.strokeStyle='#fff'; ctx.stroke(); }
    ctx.globalAlpha=1;
    if(view.k>1.6 && r>4 && !dim){ ctx.fillStyle='#cdd8ea'; ctx.font='10px system-ui'; ctx.fillText(n.name, x+r+2, y+3); }
  }
}

function loop(){ if(ticks<MAXTICKS){ step(); ticks++; } draw(); requestAnimationFrame(loop); }

// --- interaction ---
let dragNode=null, panning=false, last={x:0,y:0}, moved=false;
function nodeAt(px,py){ let best=null,bd=1e9; for(const n of N){ if(!visible(n)) continue; const dx=tx(n.x)-px,dy=ty(n.y)-py; const d=dx*dx+dy*dy; const rr=Math.max(6,n.r*view.k+4); if(d<rr*rr&&d<bd){bd=d;best=n;} } return best; }

cv.addEventListener('mousedown',(e)=>{ const n=nodeAt(e.offsetX,e.offsetY); moved=false; if(n){dragNode=n;} else {panning=true;} last={x:e.offsetX,y:e.offsetY}; });
window.addEventListener('mousemove',(e)=>{
  const rect=cv.getBoundingClientRect(); const px=e.clientX-rect.left, py=e.clientY-rect.top;
  if(dragNode){ const p=inv(px,py); dragNode.x=p.x; dragNode.y=p.y; dragNode.vx=0;dragNode.vy=0; moved=true; ticks=Math.min(ticks,200); return; }
  if(panning){ view.x-=(px-last.x)/view.k; view.y-=(py-last.y)/view.k; last={x:px,y:py}; moved=true; return; }
  const n=nodeAt(px,py); hover=n;
  if(n){ tip.style.display='block'; tip.style.left=Math.min(px+14,W-260)+'px'; tip.style.top=(py+14)+'px';
    tip.innerHTML='<div class="t-name">'+n.name+'</div><div class="t-file">'+n.file+'</div><div class="t-deg">'+n.deg+' conexiones · '+n.area+'</div>'; }
  else tip.style.display='none';
});
window.addEventListener('mouseup',(e)=>{
  if(dragNode&&!moved) selectNode(dragNode);
  else if(panning&&!moved){ selected=null; detail.style.display='none'; }
  dragNode=null; panning=false;
});
cv.addEventListener('click',(e)=>{ if(moved) return; const n=nodeAt(e.offsetX,e.offsetY); if(n) selectNode(n); });
cv.addEventListener('wheel',(e)=>{ e.preventDefault(); const p=inv(e.offsetX,e.offsetY); const f=e.deltaY<0?1.15:1/1.15; view.k=Math.max(0.15,Math.min(6,view.k*f)); const p2=inv(e.offsetX,e.offsetY); view.x+=p.x-p2.x; view.y+=p.y-p2.y; },{passive:false});

function selectNode(n){ selected=N.indexOf(n);
  const outs=[], ins=[];
  for(const [s,t] of E){ if(s===selected) outs.push(N[t]); if(t===selected) ins.push(N[s]); }
  const list=(arr)=>arr.length?arr.map(x=>'<li data-i="'+N.indexOf(x)+'">'+x.name+'</li>').join(''):'<li style="cursor:default;color:var(--muted)">—</li>';
  detail.innerHTML='<span class="close">×</span><div class="t-name">'+n.name+'</div><div class="t-file">'+n.file+'</div>'+
    '<h4>Llama a ('+outs.length+')</h4><ul>'+list(outs)+'</ul>'+
    '<h4>Llamada por ('+ins.length+')</h4><ul>'+list(ins)+'</ul>';
  detail.style.display='block';
  detail.querySelector('.close').onclick=()=>{selected=null;detail.style.display='none';};
  detail.querySelectorAll('li[data-i]').forEach(li=>li.onclick=()=>{ const i=+li.dataset.i; selected=i; view.x=N[i].x;view.y=N[i].y; selectNode(N[i]); });
}

// controls
const chipsEl=document.getElementById('chips');
const counts={}; for(const n of N) counts[n.area]=(counts[n.area]||0)+1;
for(const area of Object.keys(AREA_COLORS)){ if(!counts[area]) continue;
  const c=document.createElement('div'); c.className='chip'; c.dataset.area=area;
  c.innerHTML='<span class="dot" style="background:'+AREA_COLORS[area]+'"></span>'+area+' <span class="n">'+counts[area]+'</span>';
  c.onclick=()=>{ if(activeAreas.has(area)){activeAreas.delete(area);c.classList.add('off');} else {activeAreas.add(area);c.classList.remove('off');} updateStats(); };
  chipsEl.appendChild(c);
}
document.getElementById('search').addEventListener('input',(e)=>{ query=e.target.value.toLowerCase().trim(); updateStats(); });
document.getElementById('reset').onclick=()=>{ view={x:0,y:0,k:1}; ticks=0; };
function updateStats(){ let nv=0; for(const n of N) if(visible(n)) nv++; let ev=0; for(const [s,t] of E) if(visible(N[s])&&visible(N[t])) ev++; document.getElementById('s-nodes').textContent=nv; document.getElementById('s-edges').textContent=ev; }

resize(); updateStats(); loop();
</script>
</body></html>'''


if __name__ == "__main__":
    main()
