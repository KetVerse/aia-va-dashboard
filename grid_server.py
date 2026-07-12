"""
Custom data-grid server for the dashboard.

Renders sortable HTML grids inside iframes:
  - multi-column sort (shift+click adds a secondary/tertiary key)
  - NO column dragging (plain HTML table)
  - a Total row pinned in <tfoot> that never participates in sorting
  - sticky header + sticky total footer when the body scrolls

MULTI-USER: there is NO server-side data store. Each grid's data is rendered as
base64-encoded JSON into a hidden, per-session DOM element on the Taipy page
(class `gridholder-<name>`). The same-origin iframe reads that element from the
parent document, so every browser session sees its own filtered data with zero
cross-session leakage.
"""
import base64
import json

import pandas as pd
from flask import Blueprint, Response

grid_bp = Blueprint("grid", __name__)


def grid_payload_b64(df, total_id_col=None, sort_default_col="Revenue",
                     center_cols=None, blank_zeros=False, no_sort=False,
                     bar_cols=None, fixed=False, streak_cols=None,
                     bar_color="#c5e07a", sortable=True, center_all=False,
                     search_cols=None, status_cols=None, heat_cols=None,
                     autosize=False, first_col_w=None, row_heat_cols=None,
                     heat_by_row=False, link_cols=None, date_cols=None, header_tips=None,
                     max_height=None):
    """Build the grid payload for a DataFrame and return it base64-encoded.
    The Total row (matched in `total_id_col`) is split out so the front-end can
    pin it in the footer. `center_cols` lists string columns that should be
    centre-aligned instead of left-aligned (e.g. 'Incentive Tier').
    `blank_zeros` renders numeric 0 as an empty cell. `no_sort` keeps the rows
    in the given order without re-sorting by `sort_default_col`."""
    if df is None or len(df) == 0:
        payload = {"columns": [], "numeric": [], "center": [],
                   "rows": [], "total": None}
        return base64.b64encode(json.dumps(payload).encode()).decode()

    cols = [str(c) for c in df.columns]
    numeric = [bool(pd.api.types.is_numeric_dtype(df[c])) for c in df.columns]
    center_set = set(center_cols or [])
    center = [True if center_all else bool(numeric[i] or cols[i] in center_set)
              for i in range(len(cols))]
    search_set = set(search_cols or [])
    search_idx = [i for i in range(len(cols)) if cols[i] in search_set]
    status_set = set(status_cols or [])
    status_flag = [bool(cols[i] in status_set) for i in range(len(cols))]
    heat_map = heat_cols or {}      # {col_name: "green"|"red"|"blue"}
    heat = [heat_map.get(cols[i]) for i in range(len(cols))]

    if total_id_col and total_id_col in df.columns:
        is_tot = df[total_id_col].astype(str) == "Total"
    else:
        is_tot = pd.Series([False] * len(df), index=df.index)

    data_df  = df[~is_tot].copy()
    total_df = df[is_tot].copy()

    if not no_sort and sort_default_col in data_df.columns and len(data_df):
        data_df = data_df.sort_values(sort_default_col, ascending=False)

    def _cell(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return ""
        if isinstance(v, str):
            return v
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        if blank_zeros and f == 0:
            return ""
        return int(f) if f.is_integer() else round(f, 2)

    rows  = [[_cell(v) for v in rec] for rec in data_df.itertuples(index=False, name=None)]
    total = ([_cell(v) for v in total_df.iloc[0]] if len(total_df) else None)

    bar_set = set(bar_cols or [])
    bars = [bool(cols[i] in bar_set) for i in range(len(cols))]
    streak_set = set(streak_cols or [])
    streak = [bool(cols[i] in streak_set) for i in range(len(cols))]
    # bar_color may be a single colour or a {col: colour} map for per-column bars
    if isinstance(bar_color, dict):
        default_bc = "#c5e07a"
        bar_colors = [bar_color.get(cols[i], default_bc) for i in range(len(cols))]
    else:
        default_bc = bar_color
        bar_colors = None

    # link_cols: {display_col: (id_col, base_url)} — renders display_col as a
    # clickable link to base_url+row[id_col]. The id_col is hidden from display.
    link_map = {}
    hidden_cols = set()
    if link_cols:
        for display_col, (id_col, base_url) in link_cols.items():
            if display_col in cols and id_col in cols:
                link_map[display_col] = {"idCol": id_col, "baseUrl": base_url}
                hidden_cols.add(id_col)
    hidden = [bool(cols[i] in hidden_cols) for i in range(len(cols))]
    date_set = set(date_cols or [])    # cols of "dd-MMM-yy" strings -> sort chronologically
    date_flag = [bool(cols[i] in date_set) for i in range(len(cols))]
    _htips = header_tips or {}         # {column: hover title on the header cell}
    header_tip_list = [str(_htips.get(cols[i], "")) for i in range(len(cols))]

    payload = {"columns": cols, "numeric": numeric, "center": center,
               "rows": rows, "total": total, "bars": bars, "fixed": bool(fixed),
               "streak": streak, "barColor": default_bc, "barColors": bar_colors,
               "sortable": bool(sortable), "searchIdx": search_idx,
               "statusCol": status_flag, "heat": heat, "autosize": bool(autosize),
               "firstW": first_col_w, "rowHeat": row_heat_cols or {},
               "heatByRow": bool(heat_by_row), "linkCols": link_map,
               "hidden": hidden, "dateCols": date_flag, "headerTips": header_tip_list,
               "maxHeight": max_height}
    return base64.b64encode(json.dumps(payload).encode()).decode()


@grid_bp.route("/grid/<name>")
def grid_page(name):
    return Response(_GRID_HTML.replace("__NAME__", json.dumps(name)),
                    mimetype="text/html")


_PIE_COLORS = ["#42a5f5", "#16a34a", "#ea580c", "#8b5cf6", "#dc2626",
               "#0891b2", "#ca8a04", "#475569", "#db2777", "#65a30d"]

# Fixed colours for known channels (Google<->LinkedIn swapped, Meta a blue variant)
_CHANNEL_COLORS = {
    "google ads":     "#FBBC05",   # Google yellow
    "linkedin ads":   "#42a5f5",   # light blue
    "linkedin_social":"#42a5f5",
    "linkedin":       "#42a5f5",
    "meta ads":       "#1877F2",   # Meta blue (Facebook blue)
    "meta":           "#1877F2",
    "facebook":       "#1877F2",
}


def pie_payload_b64(df, label_col, value_col):
    """base64 JSON {labels, values, colors} for the interactive pie iframe."""
    if df is None or len(df) == 0 or value_col not in df.columns:
        payload = {"labels": [], "values": [], "colors": []}
    else:
        labels = df[label_col].astype(str).tolist()
        values = [float(v) for v in df[value_col].tolist()]
        colors = [_CHANNEL_COLORS.get(str(lab).strip().lower(),
                                      _PIE_COLORS[i % len(_PIE_COLORS)])
                  for i, lab in enumerate(labels)]
        payload = {"labels": labels, "values": values, "colors": colors}
    return base64.b64encode(json.dumps(payload).encode()).decode()


@grid_bp.route("/pie/<name>")
def pie_page(name):
    return Response(_PIE_HTML.replace("__NAME__", json.dumps(name)),
                    mimetype="text/html")


_PIE_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="https://cdn.plot.ly/plotly-2.30.0.min.js"></script>
<style>
  html,body{ margin:0; padding:0; font-family:'Inter',-apple-system,'Segoe UI',sans-serif; }
  #chart{ width:100%; height:100%; }
  .empty{ padding:24px; color:#94a3b8; font-size:13px; text-align:center; }
</style></head>
<body>
  <div id="chart"></div>
  <div id="empty" class="empty" style="display:none">No data</div>
<script>
const NAME = __NAME__;
let RAW = "", CLICKS = 0;

function setBridge(label){
  // push the clicked slice label into the hidden Taipy input on the parent page,
  // appending a counter so the value always changes (so on_change always fires,
  // enabling click-again-to-clear).
  try{
    const host = window.parent.document.querySelector(".piebridge-"+NAME);
    if(!host) return;
    const input = host.querySelector("input, textarea");
    if(!input) return;
    CLICKS += 1;
    const setter = Object.getOwnPropertyDescriptor(
        window.parent.HTMLInputElement.prototype, "value").set;
    setter.call(input, label + "||" + CLICKS);
    input.dispatchEvent(new Event("input", {bubbles:true}));
  }catch(e){}
}

function draw(data){
  const el = document.getElementById("chart"), em = document.getElementById("empty");
  if(!data.labels.length){ el.style.display="none"; em.style.display="block"; return; }
  el.style.display=""; em.style.display="none";
  const trace = {
    type:"pie", labels:data.labels, values:data.values, sort:false, hole:0.5,
    direction:"clockwise",
    textposition:"outside",
    texttemplate:"<b>%{label}</b>  %{percent}",
    outsidetextfont:{size:12, color:"#334155", family:"Inter,sans-serif"},
    marker:{colors:data.colors, line:{color:"white", width:2}},
    pull:0,
    rotation:20,
    hovertemplate:"<b>%{label}</b><br>%{value:,} • %{percent}<extra></extra>",
    automargin:true,
  };
  const layout = {
    margin:{l:70,r:70,t:40,b:60}, height:el.clientHeight||400,
    paper_bgcolor:"rgba(0,0,0,0)", showlegend:true,
    legend:{orientation:"h", y:-0.05, x:0.5, xanchor:"center",
            font:{size:11, family:"Inter,sans-serif"}},
    font:{family:"Inter,sans-serif", size:12},
    uniformtext:{minsize:10, mode:"hide"},
  };
  Plotly.react(el, [trace], layout,
    {displaylogo:false, responsive:true,
     modeBarButtonsToRemove:["lasso2d","select2d","autoScale2d","zoom2d","pan2d","zoomIn2d","zoomOut2d","resetScale2d"]});
  el.removeAllListeners && el.removeAllListeners("plotly_click");
  el.on("plotly_click", function(ev){
    if(ev && ev.points && ev.points.length){ setBridge(String(ev.points[0].label)); }
  });
}

function poll(){
  try{
    const holder = window.parent.document.querySelector(".pieholder-"+NAME);
    if(!holder) return;
    const raw = (holder.textContent||"").trim();
    if(!raw || raw===RAW) return;
    RAW = raw;
    draw(JSON.parse(atob(raw)));
  }catch(e){}
}
poll();
setInterval(poll, 1000);
</script>
</body></html>"""


_GRID_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  :root{ --hdr:#1a3a6b; --tot:#dde8f4; --totb:#b0c8e8; --txt:#334155; --tottxt:#1a3a6b; }
  *{ box-sizing:border-box; }
  /* overflow:hidden on the iframe document kills any iframe-level scrollbar;
     .wrap (below) is the ONLY scroll region. */
  html,body{ height:100%; margin:0; padding:0; overflow:hidden;
             font-family:'Inter',-apple-system,'Segoe UI',sans-serif; }
  body{ display:flex; flex-direction:column; }
  /* flex:1 1 0 makes .wrap fill the iframe purely from available space (not its
     content height), so it scrolls internally and the iframe itself never gets
     a second scrollbar; the sticky header/total stay pinned. */
  .wrap{ width:100%; overflow:auto; flex:1 1 0; min-height:0; }
  table{ width:100%; border-collapse:collapse; font-size:13px; }
  table.fixed{ table-layout:fixed; }
  table.fixed thead th:first-child,
  table.fixed tbody td:first-child,
  table.fixed tfoot td:first-child{ width:var(--firstw, 210px); }
  thead th{
    position:sticky; top:0; z-index:3;
    background:var(--hdr); color:#fff; font-weight:600; font-size:11px;
    text-transform:uppercase; letter-spacing:.4px; padding:9px 12px;
    text-align:center; white-space:nowrap; cursor:pointer; user-select:none;
    border-right:1px solid rgba(255,255,255,.08);
  }
  thead th.left{ text-align:left; }
  thead th:hover{ background:#244a82; }
  thead th.nosort{ cursor:default; }
  thead th.nosort:hover{ background:var(--hdr); }
  #searchbar{ padding:8px 10px; display:none; }
  #search{ width:260px; max-width:60%; padding:7px 10px; font-size:13px;
           border:1px solid #cbd5e1; border-radius:6px; font-family:inherit; outline:none; }
  #search:focus{ border-color:#42a5f5; }
  thead th .arr{ font-size:10px; margin-left:5px; opacity:.9; }
  thead th .pri{ font-size:8px; vertical-align:super; opacity:.85; margin-left:1px; }
  tbody td{ padding:7px 12px; border-bottom:1px solid #f1f5f9; color:var(--txt);
            white-space:nowrap; text-align:center; }
  tbody td.left{ text-align:left; }
  tbody tr:nth-child(even) td{ background:#f8fafc; }
  tbody tr:hover td{ background:#eff6ff; }
  td.num{ font-variant-numeric:tabular-nums; }
  .bar-wrap{ position:relative; display:block; min-height:18px; }
  .bar-fill{ position:absolute; left:0; top:50%; transform:translateY(-50%);
             height:18px; background:#c5e07a; border-radius:3px; z-index:0; }
  .bar-val{ position:relative; z-index:1; padding:0 4px; }
  .streak{ white-space:nowrap; }
  /* invisible on screen, but included when you drag-select & copy the row */
  .streak-copy{ position:absolute; width:1px; height:1px; padding:0; margin:-1px;
        overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  .dot{ display:inline-block; width:11px; height:11px; border-radius:50%;
        margin:0 1px; vertical-align:middle; cursor:default; }
  .dot.on{ background:#16a34a; }
  .dot.off{ background:#d8dee6; }
  td.streakcell{ text-align:left; }
  /* custom per-day tooltip — uniform white text, stays put for screenshots */
  #streaktip{ position:fixed; display:none; z-index:99999; background:#1a3a6b;
    color:#ffffff; font-size:12px; line-height:1.55; padding:7px 10px;
    border-radius:6px; box-shadow:0 4px 16px rgba(0,0,0,0.28);
    white-space:nowrap; pointer-events:none; }
  .st-active{ color:#16a34a; font-weight:700; }
  .st-risk{ color:#ea580c; font-weight:700; }
  .st-inactive{ color:#dc2626; font-weight:700; }
  .st-churned{ color:#94a3b8; font-weight:700; }
  .st-black{ color:#0f172a; font-weight:700; }
  tfoot td{
    position:sticky; bottom:0; z-index:2;
    background:var(--tot); color:var(--tottxt); font-weight:700;
    padding:8px 12px; border-top:2px solid var(--totb); white-space:nowrap;
    text-align:center;
  }
  tfoot td.left{ text-align:left; }
  .empty{ padding:24px; color:#94a3b8; font-size:13px; text-align:center; }
</style></head>
<body>
  <div id="searchbar"><input id="search" placeholder="Search…" oninput="body()"></div>
  <div class="wrap"><table id="g">
    <thead id="h"></thead><tbody id="b"></tbody><tfoot id="f"></tfoot>
  </table></div>
  <div id="empty" class="empty" style="display:none">No data</div>
  <div id="streaktip"></div>
<script>
const NAME = __NAME__;
let DATA = null;        // last parsed payload
let RAW  = "";          // last raw base64 (change detection)
let SORT = [];          // [{col, dir}] dir: 1 asc, -1 desc

function fmt(v, isNum){
  if(v===null||v===undefined||v==="") return "";
  if(isNum && typeof v==="number") return v.toLocaleString("en-IN");
  return String(v);
}
function cls(i){ return DATA.center[i] ? (DATA.numeric[i]?"num":"") : "left"; }

function header(){
  const h=document.getElementById("h"), cols=DATA.columns;
  const sortable=DATA.sortable!==false;
  const hidden=DATA.hidden||[];
  let tr="<tr>";
  cols.forEach((c,i)=>{
    if(hidden[i]) return;
    const s=sortable?SORT.find(s=>s.col===i):null;
    let arr="", pri="";
    if(s){ arr='<span class="arr">'+(s.dir===1?"▲":"▼")+'</span>';
           if(SORT.length>1) pri='<span class="pri">'+(SORT.indexOf(s)+1)+'</span>'; }
    let c2 = DATA.center[i] ? "" : "left";
    if(!sortable) c2 += " nosort";
    const tips=DATA.headerTips||[];
    const tip = tips[i] ? ' title="'+String(tips[i]).replace(/"/g,"&quot;")+'"' : '';
    tr+='<th class="'+c2.trim()+'" data-i="'+i+'"'+tip+'>'+c+arr+pri+'</th>';
  });
  h.innerHTML=tr+"</tr>";
  if(sortable) h.querySelectorAll("th").forEach(th=>th.onclick=e=>onSort(+th.dataset.i, e.shiftKey));
}
function onSort(col, shift){
  const isNum=DATA.numeric[col];
  if(shift){
    const ex=SORT.find(s=>s.col===col);
    if(ex){ ex.dir*=-1; } else { SORT.push({col, dir:isNum?-1:1}); }
  } else {
    if(SORT.length===1 && SORT[0].col===col){ SORT[0].dir*=-1; }
    else { SORT=[{col, dir:isNum?-1:1}]; }
  }
  render();
}
function parseDMY(s){
  // "dd-MMM-yy" (e.g. 20-Jun-26) -> timestamp; null if not a date
  const m=String(s||"").match(/^(\d{1,2})-([A-Za-z]{3})-(\d{2})$/);
  if(!m) return null;
  const mon=_MON.indexOf(m[2]); if(mon<0) return null;
  return new Date(2000+(+m[3]), mon, +m[1]).getTime();
}
function sortRows(rows){
  if(!SORT.length) return rows;
  const num=DATA.numeric, dcol=DATA.dateCols||[];
  return rows.slice().sort((a,b)=>{
    for(const s of SORT){
      let x=a[s.col], y=b[s.col], c;
      if(dcol[s.col]){
        const dx=parseDMY(x), dy=parseDMY(y);
        if(dx===null && dy===null){ c=0; }
        else if(dx===null){ return 1; }      // blanks always sort last
        else if(dy===null){ return -1; }
        else { c=dx-dy; }
      }
      else if(num[s.col]){ c=(Number(x)||0)-(Number(y)||0); }
      else { c=String(x).toLowerCase().localeCompare(String(y).toLowerCase()); }
      if(c) return c*s.dir;
    }
    return 0;
  });
}
function colMax(i){
  let m=0;
  for(const r of DATA.rows){ const v=Number(r[i])||0; if(v>m) m=v; }
  return m;
}
const _HEATRGB={green:"22,163,74", red:"220,38,38", blue:"26,127,196", amber:"234,88,12", teal:"13,148,136", lightorange:"251,146,60", deepblue:"29,78,216"};
function numOf(v){
  if(v===null||v===undefined||v==="") return null;
  if(typeof v==="number") return v;
  const s=String(v).replace(/[,%₹\s]/g,""); const n=parseFloat(s);
  return isNaN(n)?null:n;
}
function heatMax(i){
  // column max for the default (green) scale — EXCLUDE rows that have their own
  // colour (Fresh Renewals/One-time) so they don't dilute the cohort greens
  let m=0; for(const r of DATA.rows){
    const rl=(r[0]==null?'':String(r[0]).trim());
    if(DATA.rowHeat && DATA.rowHeat[rl]) continue;
    const n=numOf(r[i]); if(n!==null && n>m) m=n;
  } return m;
}
function rowHeatMax(r){
  // max across a specially-coloured row, so it keeps its own gradient
  const heat=DATA.heat||[]; let m=0;
  for(let i=0;i<r.length;i++){ if(!heat[i]) continue; const n=numOf(r[i]); if(n!==null && n>m) m=n; }
  return m;
}
const _MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function dateLabel(offset){
  const d=new Date(); d.setHours(0,0,0,0); d.setDate(d.getDate()-offset);
  return d.getDate()+" "+_MON[d.getMonth()]+" "+d.getFullYear();
}
function streakHtml(s){
  // s = 28 ';'-joined tokens "on,bill,syncs,items,views", index 0 = today .. 27 = today-27d.
  // A custom DOM tooltip (see the streaktip IIFE) reads the data-* attributes —
  // we don't use the native title= so the tooltip survives PrintScreen / Win+Shift+S.
  const days=(s||"").split(";");
  let h='<span class="streak">', copytxt='';
  for(let i=0;i<days.length;i++){
    const p=(days[i]||"").split(",");
    const on=p[0]==="1";
    copytxt += on ? "●" : "○";   // filled / hollow circle for copy-paste
    h+='<span class="dot '+(on?"on":"off")+'" data-i="'+i+'" data-on="'+(on?1:0)
      +'" data-up="'+(p[1]||0)+'" data-syncs="'+(p[2]||0)+'" data-items="'+(p[3]||0)
      +'" data-views="'+(p[4]||0)+'"></span>';
  }
  // hidden but copyable string of the streak (dots are CSS-only and don't copy)
  return h+'<span class="streak-copy">'+copytxt+'</span></span>';
}
// ── Per-day custom tooltip for the streak dots ──────────────────────────────
// A DOM tooltip (not native title=) so it survives PrintScreen / Win+Shift+S.
// Active day -> Date + whichever of Uploads/Accounting Syncs/Items Synced is
// nonzero (each line is dropped entirely when its count is 0, same rule as
// Dashboard Viewed below).
// Dashboard Viewed shows whenever count>0, on ACTIVE OR INACTIVE days — it never
// affects the on/off (active) state of the bubble, but its count is real signal
// even on days with no upload/sync, so it isn't gated behind "on" like the rest.
// Inactive day with 0 views -> Date only. Stays ~0.6s after mouse leaves a dot.
(function(){
  var tip=null, hideT=null;
  function el(){ if(!tip) tip=document.getElementById("streaktip"); return tip; }
  function metricLine(label, val){
    return (val && val!=="0") ? ("<br>"+label+": "+val) : "";
  }
  function show(dot){
    var t=el(); if(!t) return;
    clearTimeout(hideT);
    var i=+dot.getAttribute("data-i");
    var html=dateLabel(i)+(i===0?" (today)":"");
    if(dot.getAttribute("data-on")==="1"){
      html+=metricLine("Uploads", dot.getAttribute("data-up"))
           +metricLine("Accounting Syncs", dot.getAttribute("data-syncs"))
           +metricLine("Items Synced", dot.getAttribute("data-items"));
    }
    html+=metricLine("Dashboard Viewed", dot.getAttribute("data-views"));
    t.innerHTML=html;
    t.style.display="block";
    var r=dot.getBoundingClientRect(), tr=t.getBoundingClientRect();
    var top=r.top-tr.height-8; if(top<4) top=r.bottom+8;
    var left=r.left+r.width/2-tr.width/2;
    left=Math.max(4, Math.min(left, window.innerWidth-tr.width-4));
    t.style.top=top+"px"; t.style.left=left+"px";
  }
  function hide(){ var t=el(); if(t) t.style.display="none"; }
  document.addEventListener("mouseover", function(e){
    var dot=e.target.closest ? e.target.closest(".dot") : null;
    if(dot) show(dot);
  });
  document.addEventListener("mouseout", function(e){
    var dot=e.target.closest ? e.target.closest(".dot") : null;
    if(dot){ clearTimeout(hideT); hideT=setTimeout(hide, 600); }
  });
})();
function body(){
  const b=document.getElementById("b"), num=DATA.numeric;
  const bars=DATA.bars||[], strk=DATA.streak||[];
  const hidden=DATA.hidden||[];
  const linkCols=DATA.linkCols||{};
  const cols=DATA.columns;
  // build a col-name→index map for resolving id columns in link_cols
  const colIdx={};
  cols.forEach((c,i)=>{ colIdx[c]=i; });
  const maxes={};
  bars.forEach((on,i)=>{ if(on) maxes[i]=colMax(i); });
  let rows=sortRows(DATA.rows);
  const si=DATA.searchIdx||[];
  const sb=document.getElementById("search");
  const q=(si.length && sb)?(sb.value||"").toLowerCase().trim():"";
  if(q) rows=rows.filter(r=>si.some(i=>String(r[i]).toLowerCase().includes(q)));
  const stat=DATA.statusCol||[];
  const heat=DATA.heat||[];
  const hmax={};
  heat.forEach((c,i)=>{ if(c) hmax[i]=heatMax(i); });
  let html="";
  for(const r of rows){
    html+="<tr>";
    r.forEach((v,i)=>{
      if(hidden[i]) return;
      let style="";
      if(heat[i]){
        const n=numOf(v);
        if(n!==null && n>0){
          const rl=(r[0]==null?'':String(r[0]).trim());
          const ro=(DATA.rowHeat && DATA.rowHeat[rl]) ? DATA.rowHeat[rl] : null;
          // matrices (heatByRow) scale every cohort row to its OWN acquisition max,
          // so the first-revenue diagonal is consistently the darkest shade.
          const denom = (DATA.heatByRow || ro) ? rowHeatMax(r) : hmax[i];
          if(denom>0){
            // specially-coloured rows (Fresh Renewals/One-time) get a higher floor
            // so even small values stay distinct from the pale Total row
            const floor = ro ? 0.20 : 0.10;
            const a=Math.min(0.50, floor+0.32*(n/denom)).toFixed(3);
            const hc = ro || heat[i];
            style=' style="background:rgba('+(_HEATRGB[hc]||_HEATRGB.green)+','+a+')"';
          }
        }
      }
      const colName=cols[i];
      const lk=linkCols[colName];
      if(lk){
        const idIdx=colIdx[lk.idCol];
        const id=(idIdx!==undefined)?String(r[idIdx]||""):"";
        const href=id ? lk.baseUrl+id : "";
        const inner=fmt(v,num[i]);
        if(href){
          html+='<td class="'+cls(i)+'"'+style+'><a href="'+href+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;cursor:pointer">'+inner+'</a></td>';
        } else {
          html+='<td class="'+cls(i)+'"'+style+'>'+inner+'</td>';
        }
      } else if(strk[i] && typeof v==="string"){
        html+='<td class="streakcell">'+streakHtml(v)+'</td>';
      } else if(stat[i]){
        const sv=String(v);
        const SMAP={"Active":"st-active","Collected":"st-active",
                    "Risk of Churn":"st-risk","Overdue":"st-risk",
                    "Inactive":"st-inactive","Pending":"st-inactive",
                    "Upcoming":"st-black","Churned":"st-churned"};
        const c=SMAP[sv]||"";
        html+='<td class="'+cls(i)+'"><span class="'+c+'">'+fmt(v,num[i])+'</span></td>';
      } else if(bars[i] && typeof v==="number" && v>0 && maxes[i]>0){
        const w=Math.max(3, Math.round(v/maxes[i]*100));
        const bc=(DATA.barColors && DATA.barColors[i]) ? DATA.barColors[i] : (DATA.barColor||"#c5e07a");
        html+='<td class="'+cls(i)+'"><span class="bar-wrap">'
             +'<span class="bar-fill" style="width:'+w+'%;background:'+bc+'"></span>'
             +'<span class="bar-val">'+fmt(v,num[i])+'</span></span></td>';
      } else {
        html+='<td class="'+cls(i)+'"'+style+'>'+fmt(v,num[i])+'</td>';
      }
    });
    html+="</tr>";
  }
  b.innerHTML=html;
}
function foot(){
  const f=document.getElementById("f"), num=DATA.numeric;
  const hidden=DATA.hidden||[];
  if(!DATA.total){ f.innerHTML=""; return; }
  let tr="<tr>";
  DATA.total.forEach((v,i)=>{ if(!hidden[i]) tr+='<td class="'+cls(i)+'">'+fmt(v,num[i])+'</td>'; });
  f.innerHTML=tr+"</tr>";
}
function render(){
  const tbl=document.getElementById("g"), em=document.getElementById("empty");
  if(!DATA || !DATA.columns.length){ tbl.style.display="none"; em.style.display="block"; return; }
  tbl.style.display=""; em.style.display="none";
  tbl.className = DATA.fixed ? "fixed" : "";
  if(DATA.firstW) tbl.style.setProperty("--firstw", DATA.firstW+"px");
  else tbl.style.removeProperty("--firstw");
  document.getElementById("searchbar").style.display =
      (DATA.searchIdx && DATA.searchIdx.length) ? "block" : "none";
  header(); body(); foot();
  var FORCE_FULL=false;
  try{ FORCE_FULL = !!(window.parent && window.parent.__SNAPSHOT__); }catch(e){}
  if(DATA.autosize || FORCE_FULL){
    try{
      if(window.frameElement){
        // measure the ACTUAL content (table + search bar), not the iframe viewport
        const sb=document.getElementById("searchbar");
        const sbh=(sb && sb.style.display!=="none") ? sb.offsetHeight : 0;
        const contentH=tbl.offsetHeight + sbh + 8;
        // maxHeight caps the frame: shrink to content when short, but cap + scroll
        // internally when tall. Snapshot (FORCE_FULL) ignores the cap = full expand.
        const cap=(!FORCE_FULL && DATA.maxHeight) ? DATA.maxHeight : Infinity;
        const h=Math.min(contentH, cap);
        document.querySelector(".wrap").style.maxHeight=(contentH<=cap)?"none":"";
        window.frameElement.style.height=h+"px";
        if(FORCE_FULL){
          // snapshot: also widen the frame to the full table so nothing is
          // cut off horizontally (no internal scroll in the PDF capture).
          var w=tbl.offsetWidth+4;
          if(w>window.frameElement.clientWidth) window.frameElement.style.width=w+"px";
        }
      }
    }catch(e){}
  }
}
function poll(){
  try{
    const el = window.parent.document.querySelector(".gridholder-"+NAME);
    if(!el) return;
    const raw = (el.textContent||"").trim();
    if(!raw || raw===RAW) return;
    RAW = raw;
    DATA = JSON.parse(atob(raw));
    SORT = SORT.filter(s=>s.col<DATA.columns.length);
    render();
  }catch(e){}
}
poll();
setInterval(poll, 1000);
</script>
</body></html>"""
