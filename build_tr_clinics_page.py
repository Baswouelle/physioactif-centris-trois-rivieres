#!/usr/bin/env python3
"""Generate cliniques.html: a clean, sortable list of TR-region physio clinics.

Reads data/tr_clinics.json (from build_tr_clinics.py) and writes a self-contained
static page (data embedded) styled in the Physioactif brand, linked from the map.

    python build_tr_clinics_page.py
"""

import json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_FILE = SCRIPT_DIR / "data" / "tr_clinics.json"
OUT_FILE = SCRIPT_DIR / "cliniques.html"


def main():
    clinics = json.load(open(DATA_FILE, encoding="utf-8"))
    clinics.sort(
        key=lambda c: (c["oldest_experience_years"] is not None,
                       c["oldest_experience_years"] or 0),
        reverse=True,
    )
    total = len(clinics)
    total_physios = sum(c["num_physios"] for c in clinics)
    data_json = json.dumps(clinics, ensure_ascii=False)
    today = datetime.now().strftime("%Y-%m-%d")

    html = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cliniques de physio - Trois-Rivieres</title>
<style>
  :root {
    --forest:#243522; --gold:#ddc96a; --mint:#e8efec; --sage:#9ba491; --olive:#6e6724;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Segoe UI',system-ui,sans-serif; background:#f5f5f5; color:#2d2d2d; }
  .header { background:var(--forest); color:#fff; padding:22px 28px; }
  .header h1 { font-size:22px; font-weight:600; }
  .header .sub { color:var(--gold); font-size:13px; margin-top:4px; }
  .header a.back { color:var(--mint); font-size:12px; text-decoration:none; display:inline-block; margin-top:10px; }
  .header a.back:hover { color:#fff; }
  .bar { display:flex; gap:14px; flex-wrap:wrap; padding:14px 28px; background:#fff;
         border-bottom:2px solid var(--gold); align-items:center; }
  .bar input { flex:1; min-width:200px; padding:9px 12px; border:1px solid var(--sage);
               border-radius:8px; font-size:14px; }
  .chip { background:var(--mint); color:var(--forest); border-radius:20px; padding:6px 14px;
          font-size:13px; font-weight:600; white-space:nowrap; }
  .wrap { padding:18px 28px 40px; max-width:1100px; margin:0 auto; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:10px;
          overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.08); }
  thead th { background:var(--forest); color:#fff; font-size:12px; text-transform:uppercase;
             letter-spacing:.4px; text-align:left; padding:12px 14px; cursor:pointer;
             user-select:none; white-space:nowrap; }
  thead th:hover { background:#2f4730; }
  thead th .arrow { color:var(--gold); font-size:10px; margin-left:4px; }
  tbody td { padding:11px 14px; font-size:14px; border-top:1px solid #eee; }
  tbody tr:nth-child(even) { background:#fafbfa; }
  tbody tr:hover { background:var(--mint); }
  .name { font-weight:600; color:var(--forest); }
  .city { color:var(--olive); font-size:13px; }
  .num { text-align:center; font-weight:700; color:var(--forest); }
  .exp { font-weight:700; color:var(--olive); white-space:nowrap; }
  .permit { color:#aaa; font-size:12px; }
  .muted { color:#bbb; }
  .count { padding:10px 4px 0; font-size:12px; color:var(--olive); }
  @media (max-width:680px){ .city,.permit,th.col-permit,td.col-permit{ display:none; } }
</style>
</head>
<body>
<div class="header">
  <h1>Cliniques de physioth&eacute;rapie &middot; Trois-Rivi&egrave;res</h1>
  <div class="sub">__TOTAL__ cliniques &bull; __PHYSIOS__ physioth&eacute;rapeutes &bull; source OPPQ &bull; maj __TODAY__</div>
  <a class="back" href="index.html">&larr; Retour &agrave; la carte</a>
</div>
<div class="bar">
  <input type="text" id="q" placeholder="Rechercher une clinique, une ville, un physio...">
  <span class="chip" id="shown"></span>
</div>
<div class="wrap">
  <table>
    <thead><tr>
      <th data-k="name">Clinique<span class="arrow"></span></th>
      <th data-k="city">Ville<span class="arrow"></span></th>
      <th data-k="num_physios" class="num">Physios<span class="arrow"></span></th>
      <th data-k="oldest_name">Plus ancien physio<span class="arrow"></span></th>
      <th data-k="oldest_grad_year" class="num">Gradu&eacute;<span class="arrow"></span></th>
      <th data-k="oldest_experience_years" class="num">Exp&eacute;rience<span class="arrow"></span></th>
      <th data-k="oldest_permit" class="col-permit">Permis<span class="arrow"></span></th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="count" id="count"></div>
</div>
<script>
const CLINICS = __DATA__;
let sortKey = 'oldest_experience_years', sortDir = -1;
const rowsEl = document.getElementById('rows');
const qEl = document.getElementById('q');
const shownEl = document.getElementById('shown');
const countEl = document.getElementById('count');

function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function render(){
  const q = qEl.value.trim().toLowerCase();
  let list = CLINICS.filter(c =>
    !q || [c.name,c.city,c.oldest_name].some(v => (v||'').toLowerCase().includes(q)));
  list.sort((a,b)=>{
    let x=a[sortKey], y=b[sortKey];
    if(x==null) return 1; if(y==null) return -1;
    if(typeof x==='string') return x.localeCompare(y)*sortDir;
    return (x-y)*sortDir;
  });
  rowsEl.innerHTML = list.map(c => `
    <tr>
      <td class="name">${esc(c.name)}</td>
      <td class="city">${esc(c.city)}</td>
      <td class="num">${c.num_physios}</td>
      <td>${c.oldest_name?esc(c.oldest_name):'<span class="muted">-</span>'}</td>
      <td class="num">${c.oldest_grad_year||'<span class="muted">-</span>'}</td>
      <td class="exp">${c.oldest_experience_years!=null?c.oldest_experience_years+' ans':'<span class="muted">-</span>'}</td>
      <td class="permit col-permit">${c.oldest_permit?esc(c.oldest_permit):''}</td>
    </tr>`).join('');
  shownEl.textContent = list.length + ' / ' + CLINICS.length + ' cliniques';
  countEl.textContent = list.length + ' cliniques affich\\u00e9es';
  document.querySelectorAll('thead th').forEach(th=>{
    const a=th.querySelector('.arrow');
    a.textContent = th.dataset.k===sortKey ? (sortDir<0?'\\u25bc':'\\u25b2') : '';
  });
}
document.querySelectorAll('thead th').forEach(th=>{
  th.addEventListener('click',()=>{
    const k=th.dataset.k;
    if(k===sortKey) sortDir=-sortDir;
    else { sortKey=k; sortDir = (k==='name'||k==='city'||k==='oldest_name')?1:-1; }
    render();
  });
});
qEl.addEventListener('input', render);
render();
</script>
</body>
</html>"""

    html = (html
            .replace("__TOTAL__", str(total))
            .replace("__PHYSIOS__", str(total_physios))
            .replace("__TODAY__", today)
            .replace("__DATA__", data_json))
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_FILE} ({total} clinics)")


if __name__ == "__main__":
    main()
