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
                     max_height=None, tip_cols=None, total_inline=False,
                     heat_from=None, heat_max=None, class_cols=None,
                     rownum_col=None, col_w=None, hdr_center=False,
                     fixed_layout=False, hdr_class_cols=None):
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

    # total_inline: keep the Total row in the body (at its dataframe position) and
    # style it in place, instead of pinning it to the footer. Lets memo rows placed
    # AFTER the Total in the frame render below it.
    if total_inline:
        data_df  = df.copy()
        total_df = df.iloc[0:0].copy()
    else:
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
    # tip_cols: {display_col: source_col} — display_col's cell gets a native title=
    # from the (hidden) source_col value on that row (e.g. the list of deal names).
    tip_map = {}
    if tip_cols:
        for display_col, src_col in tip_cols.items():
            if display_col in cols and src_col in cols:
                tip_map[display_col] = src_col
                hidden_cols.add(src_col)
    # heat_from: {display_col: numeric_source_col} — shade display_col from the
    # (hidden) source column's value instead of parsing the cell text. Needed when
    # the cell shows something other than the value being shaded (e.g. a cohort
    # cell printing "16 (94%)" or just "16", but shaded by the 94% retention).
    # Pair with heat_max for a FIXED scale (e.g. 100) so the same % is the same
    # colour everywhere instead of being rescaled per column.
    heat_from_map = {}
    if heat_from:
        for display_col, src_col in heat_from.items():
            if display_col in cols and src_col in cols:
                heat_from_map[display_col] = src_col
                hidden_cols.add(src_col)
    # class_cols: {display_col: source_col} — the (hidden) source column holds a CSS
    # class name applied to display_col's cell (blank = no class). Lets a cell be
    # styled from a condition computed in Python (e.g. an Int Date shown orange
    # while the customer is still inside their initial-milestone window).
    class_map = {}
    if class_cols:
        for display_col, src_col in class_cols.items():
            if display_col in cols and src_col in cols:
                class_map[display_col] = src_col
                hidden_cols.add(src_col)
    hidden = [bool(cols[i] in hidden_cols) for i in range(len(cols))]
    date_set = set(date_cols or [])    # cols of "dd-MMM-yy" strings -> sort chronologically
    date_flag = [bool(cols[i] in date_set) for i in range(len(cols))]
    _htips = header_tips or {}         # {column: hover title on the header cell}
    header_tip_list = [str(_htips.get(cols[i], "")) for i in range(len(cols))]

    payload = {"columns": cols, "numeric": numeric, "center": center,
               "hdr_center": bool(hdr_center), "fixedLayout": bool(fixed_layout),
               "rows": rows, "total": total, "bars": bars, "fixed": bool(fixed),
               "streak": streak, "barColor": default_bc, "barColors": bar_colors,
               "sortable": bool(sortable), "searchIdx": search_idx,
               "statusCol": status_flag, "heat": heat, "autosize": bool(autosize),
               "firstW": first_col_w, "rowHeat": row_heat_cols or {},
               "heatByRow": bool(heat_by_row), "linkCols": link_map,
               "hidden": hidden, "dateCols": date_flag, "headerTips": header_tip_list,
               "maxHeight": max_height, "tipCols": tip_map,
               "inlineTotal": bool(total_inline),
               "heatFrom": heat_from_map, "heatMax": heat_max,
               "classCols": class_map,
               # rownum_col is re-numbered 1..N in DISPLAY order by the front-end
               # (after sorting / searching), so the serial always reads top-to-
               # bottom instead of travelling with its row when the user re-sorts.
               "rowNumCol": (cols.index(rownum_col)
                             if (rownum_col and rownum_col in cols) else -1),
               # col_w: {column: px} — preferred width for specific columns. The
               # table is auto-layout, so the slack goes to the columns left auto
               # instead of being soaked up by the widest text column.
               "colW": [(col_w or {}).get(cols[i]) for i in range(len(cols))],
               # hdr_class_cols: {display_col: css_class} -- a CSS class applied to the
               # HEADER <th> itself (not the body cells), e.g. to band a group of
               # related columns in a shared colour (see Self Onboarding Tracker).
               "hdrClass": {cols[i]: (hdr_class_cols or {}).get(cols[i], "")
                            for i in range(len(cols))}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


@grid_bp.route("/grid/<name>")
def grid_page(name):
    return Response(_GRID_HTML.replace("__NAME__", json.dumps(name)),
                    mimetype="text/html")


# Streak-token field index -> label, in the SAME order/labels as the streak-dot
# hover tooltip (see the tooltip builder above), so Activity Notes read identically
# to what a human sees hovering the dots. Only non-zero metrics are emitted.
_NOTE_FIELDS = [
    (2, "Accounting Syncs"), (3, "Items Synced"), (1, "Uploads"),
    (18, "Bot Queries"), (19, "Bot Uploads"), (14, "Line Items"),
    (15, "Tnx Lines"), (16, "Reviewed"), (17, "Needs Review"),
    (13, "Transaction status"), (5, "Transactions updated"), (6, "Entities created"),
    (10, "Invoices created / bulk-edited"), (7, "Recon processed"),
    (8, "Vendor mismatches resolved"), (9, "Mapping completed"),
    (11, "Deletes"), (12, "Logins"), (4, "Dashboard Viewed"),
]


def _streak_to_notes(streak, anchor):
    """Render a 28-day streak token (index 0 = today) into dated activity notes:
    one block per ACTIVE day, most-recent first, blank line between blocks:

        26-Aug-2026
        Accounting Syncs: 2
        Uploads: 3

        24-Aug-2026
        Dashboard Viewed: 5
    """
    import pandas as _pd
    blocks = []
    for i, day in enumerate((streak or "").split(";")):
        p = day.split(",")
        lines = []
        for idx, label in _NOTE_FIELDS:
            v = int(p[idx]) if (idx < len(p) and p[idx].strip()) else 0
            if v:
                lines.append(f"{label}: {v}")
        if lines:
            d = (anchor - _pd.Timedelta(days=i)).strftime("%d-%b-%Y")
            blocks.append(d + "\n" + "\n".join(lines))
    return "\n\n".join(blocks)


@grid_bp.route("/api/ft-activity-scores")
def ft_activity_scores():
    """JSON feed for the FT Live Tracker daily sync. For every Free-Trial deal (the
    same population + numbers as the dashboard's Free Trial Usage & Health table),
    returns record_id, Activity Score (28-day weighted, anchored to today) and
    Activity Notes (the streak-dot activities per active day). An external job (n8n)
    matches record_id -> the tracker's Deal ID and writes the two columns.

    Also returns the FT Activated inputs, using the SAME rule as the AIA Ops KPI card
    (main._ft_activated: >=2 real-usage days = green/amber streak dots in the last 28):
    `stage` (current deal stage), `real_usage_days` and `ft_activated`. The n8n job
    that moves deals to "Free Trial Activated" reads these instead of re-implementing
    the rule, so the stage and the dashboard can't drift apart."""
    from datetime import date as _date
    import main
    df = main._build_ft_health_df()           # cached; record_id + Activity Score + streak
    anchor = pd.Timestamp(_date.today()).normalize()   # today
    out = []
    if df is not None and len(df):
        for _, r in df.iterrows():
            rid = str(r.get("record_id") or "").strip()
            if not rid:
                continue
            streak = r.get("Usage Streak Last 28D (desc)", "")
            real_days = int(main._real_usage_days_from_streak(streak))
            out.append({
                "record_id": rid,
                "deal_name": str(r.get("Deal Name", "")),
                "stage": str(r.get("Stage", "") or ""),
                "activity_score": int(r.get("Activity Score", 0) or 0),
                "real_usage_days": real_days,
                "ft_activated": real_days >= 2,
                "activity_notes": _streak_to_notes(streak, anchor),
            })
    return Response(json.dumps(out), mimetype="application/json")


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


def pie_payload_b64(df, label_col, value_col, money=False):
    """base64 JSON {labels, values, colors, money} for the interactive pie iframe.
    `money` prefixes the hover value with ₹ — set it for spend/revenue pies and
    leave it off for headcount ones (Deals, Count), which aren't currency."""
    if df is None or len(df) == 0 or value_col not in df.columns:
        payload = {"labels": [], "values": [], "colors": []}
    else:
        labels = df[label_col].astype(str).tolist()
        values = [float(v) for v in df[value_col].tolist()]
        colors = [_CHANNEL_COLORS.get(str(lab).strip().lower(),
                                      _PIE_COLORS[i % len(_PIE_COLORS)])
                  for i, lab in enumerate(labels)]
        payload = {"labels": labels, "values": values, "colors": colors}
    payload["money"] = bool(money)
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
    // .0f drops the paise on Spend (values arrive as floats); Deals are already
    // whole so it's a no-op there. &nbsp; keeps real space either side of the
    // bullet — plain spaces collapse in the SVG hover label.
    hovertemplate:"<b>%{label}</b><br>"+(data.money?"₹":"")+
                  "%{value:,.0f}&nbsp; • &nbsp;%{percent}<extra></extra>",
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
  /* fixed_layout: columns get exactly their col_w ratios (scaled to 100%), so a
     clipped cell ellipsizes right at its own edge instead of at a fixed px cap. */
  table.fl{ table-layout:fixed; }
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
  .dot.on{ background:#eab308; }     /* amber: a work event (upload/txn/invoice/bot…) */
  .dot.off{ background:#d8dee6; }    /* grey: no activity */
  .dot.sync{ background:#16a34a; }   /* green: accounting sync or recon */
  .dot.seen{ background:#7dd3fc; }   /* light blue: login / dashboard only — NOT real usage */
  td.streakcell{ text-align:left; }
  /* custom per-day tooltip — uniform white text, stays put for screenshots */
  #streaktip{ position:fixed; display:none; z-index:99999; background:#1a3a6b;
    color:#ffffff; font-size:12px; line-height:1.55; padding:7px 10px;
    border-radius:6px; box-shadow:0 4px 16px rgba(0,0,0,0.28);
    white-space:nowrap; pointer-events:none;
    max-height:calc(100vh - 10px); overflow-y:auto; }
  .st-active{ color:#16a34a; font-weight:700; }
  .st-risk{ color:#ea580c; font-weight:700; }
  .st-inactive{ color:#dc2626; font-weight:700; }
  .st-churned{ color:#94a3b8; font-weight:700; }
  .st-black{ color:#0f172a; font-weight:700; }
  /* a cell flagged from Python via class_cols (e.g. Int Date during the
     initial-milestone window) — orange, but lighter than the status labels */
  .cell-orange{ color:#ea580c; font-weight:600; }
  /* Marketing funnel: immature-cohort fade, low-denominator "n<25", partial-period badge */
  .cell-faded{ opacity:.42; }
  .cell-muted{ color:#9aa4b2; font-style:italic; }
  .cell-zero{ color:#cdd2d9; font-style:italic; }   /* cell-muted, ~50% lighter -- for a 0 value standing out less */
  /* hdr_class_cols: bands a HEADER cell in a group colour (e.g. Self Onboarding
     Tracker's two funnel paths), overriding the default navy via class specificity. */
  thead th.hdr-self{ background:#15803d; }
  thead th.hdr-self:hover{ background:#0f5c2c; }
  thead th.hdr-acct{ background:#c2410c; }
  thead th.hdr-acct:hover{ background:#9a3409; }
  /* hdr-acct-light: a muted tint of hdr-acct for a downstream subset of a
     concern/drop-off column (e.g. Instant Demo Funnel Tracker's Did Not Join
     -> Later Engaged via WhatsApp -- the partial recovery from it). */
  thead th.hdr-acct-light{ background:#b5651d; }
  thead th.hdr-acct-light:hover{ background:#954f16; }
  /* hdr-joined / hdr-joined-light: Instant Demo Funnel Tracker's Joined Demo
     and its 5+ Min Demo subset -- same hue, dark for the full group, light
     for the subset, so the relationship reads at a glance. */
  thead th.hdr-joined{ background:#15803d; }
  thead th.hdr-joined:hover{ background:#0f5c2c; }
  thead th.hdr-joined-light{ background:#6b9b7a; }
  thead th.hdr-joined-light:hover{ background:#5a8568; }
  td.cell-partial::after{ content:"Partial"; display:inline-block; margin-left:6px;
    font-size:9px; font-weight:700; line-height:1; padding:2px 5px; border-radius:8px;
    background:#fde68a; color:#92400e; vertical-align:middle; }
  /* col_w: cap a column by clipping its content — an inner block with a max-width
     bounds the cell's max-content, which is what auto-layout sizes the column to.
     Overlong values ellipsize and keep the full text in a hover title. */
  /* inline-block (not block) so a clipped cell still obeys the cell's text-align:
     centered columns (matrices) stay centered, left columns (Deal Name) stay left. */
  .clip{ display:inline-block; vertical-align:middle; max-width:100%;
         overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  tfoot td{
    position:sticky; bottom:0; z-index:2;
    background:var(--tot); color:var(--tottxt); font-weight:700;
    padding:8px 12px; border-top:2px solid var(--totb); white-space:nowrap;
    text-align:center;
  }
  tfoot td.left{ text-align:left; }
  /* inline Total row (total_inline): same look as the footer total, but rendered
     in place in the body so memo rows can sit below it. */
  tr.totrow td{ background:var(--tot); color:var(--tottxt); font-weight:700;
    border-top:2px solid var(--totb); border-bottom:2px solid var(--totb); }
  .empty{ padding:24px; color:#94a3b8; font-size:13px; text-align:center; }
  /* per-column filter: the vertical-ellipsis in each header + its popup */
  thead th.hasf{ padding-right:24px; }
  thead th .cf{ position:absolute; right:4px; top:50%; transform:translateY(-50%); padding:3px 4px;
    font-size:14px; line-height:1; font-weight:700; opacity:.55; border-radius:3px; cursor:pointer; }
  thead th:hover .cf{ opacity:1; }
  thead th .cf:hover{ background:rgba(255,255,255,.22); }
  thead th .cf.on{ opacity:1; color:#fbbf24; background:rgba(255,255,255,.16); }
  #cfpop{ display:none; position:fixed; z-index:50; min-width:190px; padding:10px 12px; background:#fff;
    color:#1e293b; border:1px solid #cbd5e1; border-radius:8px; box-shadow:0 8px 24px rgba(15,23,42,.22);
    font-size:12px; font-weight:400; text-transform:none; letter-spacing:0; }
  #cfpop .cft{ font-weight:700; margin-bottom:6px; color:#1a3a6c; }
  #cfpop label{ display:block; margin:6px 0 2px; color:#64748b; font-size:11px; }
  #cfpop input{ width:100%; box-sizing:border-box; padding:5px 7px; border:1px solid #cbd5e1;
    border-radius:5px; font-size:12px; }
  #cfpop input:focus{ outline:none; border-color:#1a7fc4; }
  #cfbar{ display:none; text-align:right; padding:2px 4px 4px; }
  #cfbar a{ display:inline-block; padding:3px 10px; border:1px solid #cbd5e1; border-radius:999px; background:#fff;
    color:#1a3a6c; font-size:11px; font-weight:600; cursor:pointer; }
  #cfbar a:hover{ background:#eef4fb; border-color:#1a7fc4; }
  #cfpop.wide{ min-width:300px; max-width:360px; padding:8px; }
  #cfpop.wide .cft{ display:none; }
  #cfpop .cfs{ border:2px solid #1a7fc4; border-radius:6px; padding:7px 9px; font-size:13px; }
  #cfpop .cfl{ max-height:260px; overflow-y:auto; margin-top:6px; border-top:1px solid #e2e8f0; }
  #cfpop .cfi{ display:flex; align-items:center; gap:9px; margin:0; padding:7px 6px; font-size:13px;
    color:#1e293b; cursor:pointer; white-space:nowrap; }
  #cfpop .cfi:hover{ background:#f1f5f9; }
  #cfpop .cfi input{ width:auto; margin:0; accent-color:#1a7fc4; }
  #cfpop .cfb{ margin-top:8px; text-align:right; }
  #cfpop .cfb a{ color:#1a7fc4; cursor:pointer; font-size:11px; text-decoration:underline; }
</style></head>
<body>
  <div id="cfpop"></div>
  <div id="searchbar"><input id="search" placeholder="Search…" oninput="body()"></div>
  <div id="cfbar"><a id="cfclrall"></a></div>
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
  const hdrClass=DATA.hdrClass||{};
  cols.forEach((c,i)=>{
    if(hidden[i]) return;
    const s=sortable?SORT.find(s=>s.col===i):null;
    let arr="", pri="";
    if(s){ arr='<span class="arr">'+(s.dir===1?"▲":"▼")+'</span>';
           if(SORT.length>1) pri='<span class="pri">'+(SORT.indexOf(s)+1)+'</span>'; }
    let c2 = (DATA.hdr_center || DATA.center[i]) ? "" : "left";
    if(!sortable) c2 += " nosort";
    if(hdrClass[c]) c2 += " "+hdrClass[c];
    const tips=DATA.headerTips||[];
    const tip = tips[i] ? ' title="'+String(tips[i]).replace(/"/g,"&quot;")+'"' : '';
    const cw=(DATA.colW||[])[i];
    const wsty = cw ? ' style="width:'+cw+'px"' : '';
    const ck=cfKind(i);
    const cf=(ck && !isSnap()) ? '<span class="cf'+(cfOn(CF[c])?' on':'')+'" data-i="'+i+'" title="Filter this column">&#8942;</span>' : '';
    if(cf) c2+=" hasf";
    tr+='<th class="'+c2.trim()+'" data-i="'+i+'"'+tip+wsty+'>'+c+arr+pri+cf+'</th>';
  });
  h.innerHTML=tr+"</tr>";
  if(sortable) h.querySelectorAll("th").forEach(th=>th.onclick=e=>onSort(+th.dataset.i, e.shiftKey));
  h.querySelectorAll(".cf").forEach(el=>el.onclick=e=>{ e.stopPropagation(); cfToggle(+el.dataset.i, el); });
}
// ── Per-column filters (HubSpot-style ⋮ menu) ───────────────────────────────
// text column -> search box; date column -> From/To; number column -> greater/less
// than; bubble (streak) / hidden / serial columns -> none. Client-side only and
// kept across the 1s data poll. Not shown in the PDF snapshot.
const CF = {};                 // column name -> {txt, gt, lt, from, to}  ("" = unset)
let CFOPEN = null;             // column index whose popup is open
function isSnap(){ try{ return !!(window.parent && window.parent.__SNAPSHOT__); }catch(e){ return false; } }
function cfOn(f){ return !!f && !!((f.sel && f.sel.length) || f.gt || f.lt || f.from || f.to); }
function isTotRow(r){ return !!DATA.inlineTotal && (r[0]==null?'':String(r[0]).trim()).indexOf("Total")===0; }
function cfKind(i){
  if((DATA.hidden||[])[i] || (DATA.streak||[])[i] || i===DATA.rowNumCol) return "";
  if(!DATA.rows || !DATA.rows.length) return "";
  // matrix / cohort tables (heat-mapped grids): ONLY the first (Cohort / row-label) column
  // is filterable, whatever the row count
  const isMatrix = !!(DATA.heatByRow || DATA.heatMax);
  if(isMatrix && i!==0) return "";
  // other small tables (< 8 data rows, "Total" rows not counted) don't need column filters
  if(!isMatrix && DATA.rows.filter(r=>(r[0]==null?'':String(r[0]).trim()).indexOf("Total")!==0).length<8) return "";
  if((DATA.dateCols||[])[i]) return "date";
  if(DATA.numeric[i]) return "num";
  // auto-detect dd-MMM-yy date columns that weren't declared in date_cols
  // (month-only labels like "Sep 25" are better as a chronological tick-list than a day picker)
  let seen=0, allMonth=true;
  for(const r of DATA.rows){
    const v=r[i]; if(v===null||v===undefined||v==="") continue;
    const p=parseDateEx(v);
    if(!p) return "str";
    if(!p.m) allMonth=false;
    if(++seen>=50) break;
  }
  return (seen && !allMonth) ? "date" : "str";
}
function cfTs(s){ return s ? new Date(s+"T00:00:00").getTime() : null; }
function cfApply(rows){
  const act=[];
  DATA.columns.forEach((c,i)=>{ const f=CF[c]; if(cfOn(f)){ const k=cfKind(i); if(k) act.push([i,k,f]); } });
  if(!act.length) return rows;
  act.forEach(a=>{ if(a[1]==="str") a.push(new Set(a[2].sel)); });
  return rows.filter(r=>{
    if(isTotRow(r)) return true;
    for(const a of act){
      const i=a[0], k=a[1], f=a[2], v=r[i];
      if(k==="str"){
        if(!a[3].has(String(v==null?"":v))) return false;
      } else if(k==="num"){
        const n=numOf(v);
        if(f.gt!=="" && f.gt!=null && !(n!==null && n>parseFloat(f.gt))) return false;
        if(f.lt!=="" && f.lt!=null && !(n!==null && n<parseFloat(f.lt))) return false;
      } else {
        const t=parseDMY(v), lo=cfTs(f.from), hi=cfTs(f.to);
        if(lo!==null && !(t!==null && t>=lo)) return false;
        if(hi!==null && !(t!==null && t<=hi)) return false;
      }
    }
    return true;
  });
}
function cfClose(){
  const p=document.getElementById("cfpop"); if(p) p.style.display="none";
  CFOPEN=null;
  try{ if(window.frameElement) window.frameElement.style.minHeight=""; }catch(e){}
}
function cfToggle(i, anchor){ if(CFOPEN===i){ cfClose(); } else { cfOpen(i, anchor); } }
function cfOpen(i, anchor){
  const p=document.getElementById("cfpop"), c=DATA.columns[i], k=cfKind(i), f=CF[c]||{};
  const esc=s=>String(s==null?"":s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
  let h='<div class="cft">'+esc(c)+'</div>';
  let vals=null; const selSet=new Set(f.sel||[]);
  if(k==="str"){
    // distinct values, ticked ones pinned on top (A-Z within each group)
    const seen=new Set(); let hasBlank=false;
    for(const r of DATA.rows){ if(isTotRow(r)) continue; const v=r[i]; if(v===null||v===undefined||v===""){ hasBlank=true; continue; } seen.add(String(v)); }
    const az=(x,y)=>x.toLowerCase().localeCompare(y.toLowerCase());
    // order: matrix row-labels keep the table's own (chronological) order; date-like labels
    // sort by date; everything else A-Z
    let all=[...seen];
    if(!(DATA.heatByRow || DATA.heatMax)){
      all = all.every(v=>parseDateEx(v)) ? all.sort((x,y)=>parseDateEx(x).t-parseDateEx(y).t) : all.sort(az);
    }
    if(hasBlank) all.push("");                       // "(Blank)" option, listed last
    vals=[...all.filter(v=>selSet.has(v)), ...all.filter(v=>!selSet.has(v))];
    h+='<input id="cf1" class="cfs" type="text" placeholder="Search…" autocomplete="off">'
      +'<div class="cfl"><label class="cfi"><input type="checkbox" id="cfall"'+(selSet.size?'':' checked')+'> All</label>'
      +vals.map((v,n)=>{ const lb=(v==="")?"(Blank)":v;
          return '<label class="cfi" data-t="'+esc(lb.toLowerCase())+'"><input type="checkbox" class="cfv" data-n="'+n+'"'+(selSet.has(v)?' checked':'')+'> '+esc(lb)+'</label>'; }).join("")
      +'</div>';
    p.className="wide";
  }
  else { p.className="";
  if(k==="num") h+='<label>Greater than</label><input id="cf1" type="number" step="any" value="'+esc(f.gt)+'">'
                       +'<label>Less than</label><input id="cf2" type="number" step="any" value="'+esc(f.lt)+'">';
  else               h+='<label>From</label><input id="cf1" type="date" value="'+esc(f.from)+'">'
                       +'<label>To</label><input id="cf2" type="date" value="'+esc(f.to)+'">';
  }
  h+='<div class="cfb"><a id="cfclr">Clear</a></div>';
  p.innerHTML=h; p.style.display="block"; CFOPEN=i;
  const apply=n=>{ if(cfOn(n)) CF[c]=n; else delete CF[c]; render(); };
  if(k==="str"){
    const allBox=document.getElementById("cfall"), boxes=[...p.querySelectorAll(".cfv")];
    document.getElementById("cf1").oninput=e=>{
      const q=e.target.value.toLowerCase().trim();
      p.querySelectorAll("label[data-t]").forEach(l=>{ l.style.display=(!q||l.dataset.t.includes(q))?"":"none"; });
    };
    allBox.onchange=()=>{ selSet.clear(); boxes.forEach(b=>b.checked=false); allBox.checked=true; apply({sel:[]}); };
    boxes.forEach(b=>b.onchange=()=>{
      const v=vals[+b.dataset.n];
      if(b.checked) selSet.add(v); else selSet.delete(v);
      allBox.checked=selSet.size===0;
      apply({sel:[...selSet]});
    });
  } else {
    const set=()=>{
      const a=document.getElementById("cf1"), b=document.getElementById("cf2");
      const n={sel:[],gt:"",lt:"",from:"",to:""};
      if(k==="num"){ n.gt=a.value; n.lt=b.value; } else { n.from=a.value; n.to=b.value; }
      apply(n);
    };
    p.querySelectorAll("input").forEach(el=>el.oninput=set);
  }
  document.getElementById("cfclr").onclick=()=>{ delete CF[c]; render(); cfOpen(i, document.querySelector('.cf[data-i="'+i+'"]')||anchor); };
  const r=anchor.getBoundingClientRect();
  const left=Math.max(4, Math.min(r.left, window.innerWidth-p.offsetWidth-4));
  p.style.left=left+"px"; p.style.top=(r.bottom+4)+"px";
  // a short table shrinks its iframe to fit the rows; keep the frame tall enough to hold the popup
  try{ if(window.frameElement) window.frameElement.style.minHeight=(r.bottom+4+p.offsetHeight+10)+"px"; }catch(e){}
  const first=document.getElementById("cf1"); if(first) first.focus();
}
function cfClearAll(){ Object.keys(CF).forEach(k=>delete CF[k]); cfClose(); render(); }
document.getElementById("cfclrall").onclick=cfClearAll;
// Alt+Shift+R is the page-wide "reset filters" shortcut; when focus is inside this iframe
// the page never sees the key, so hand it up (the page handler also clears every table's ⋮ filters)
document.addEventListener("keydown", e=>{
  if(e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.key==="R"){
    e.preventDefault();
    try{ window.parent.document.dispatchEvent(new KeyboardEvent("keydown",{key:"R",altKey:true,shiftKey:true,bubbles:true,cancelable:true})); }catch(x){ cfClearAll(); }
  }
});
document.addEventListener("mousedown", e=>{
  if(CFOPEN!==null && !e.target.closest("#cfpop") && !e.target.closest(".cf")) cfClose();
});
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && CFOPEN!==null) cfClose(); });
// a click anywhere OUTSIDE this iframe (the page around it, or another table's iframe)
// never reaches this document, so close on focus loss and on parent-page clicks too
window.addEventListener("blur", ()=>{ if(CFOPEN!==null) cfClose(); });
try{ window.parent.document.addEventListener("mousedown", ()=>{ if(CFOPEN!==null) cfClose(); }, true); }catch(e){}
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
function parseDateEx(s){
  // Recognised: "20-Jun-26" / "20 Jun 2026" (full), "21 Nov" (no year -> the latest such
  // date not in the future), "Sep 25" / "Sep-2025" (month only -> 1st of that month, m:true).
  // Returns {t: timestamp, m: monthOnly} or null.
  s=String(s==null?"":s).trim(); if(!s) return null;
  const cap=w=>w.charAt(0).toUpperCase()+w.slice(1).toLowerCase();
  let m, mon, d, y=null, mo=false;
  if((m=s.match(/^(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{2}|\d{4})$/))){ d=+m[1]; mon=_MON.indexOf(cap(m[2])); y=+m[3]; if(y<100) y+=2000; }
  else if((m=s.match(/^(\d{1,2})[- ]([A-Za-z]{3})$/))){ d=+m[1]; mon=_MON.indexOf(cap(m[2])); }
  else if((m=s.match(/^([A-Za-z]{3})[- ](\d{2}|\d{4})$/))){ d=1; mon=_MON.indexOf(cap(m[1])); y=+m[2]; if(y<100) y+=2000; mo=true; }
  else return null;
  if(mon<0 || d<1 || d>31) return null;
  if(y===null){
    const now=new Date(); y=now.getFullYear();
    if(new Date(y,mon,d).getTime() > now.getTime()+86400000) y--;
  }
  return {t:new Date(y,mon,d).getTime(), m:mo};
}
function parseDMY(s){ const p=parseDateEx(s); return p ? p.t : null; }   // timestamp or null
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
  const d=new Date(); d.setHours(0,0,0,0); d.setDate(d.getDate()-offset);   // index 0 = today
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
    const on=p[0]==="1";                       // active: ANY event that day (incl. login)
    const g=(+(p[2]||0))>0||(+(p[7]||0))>0;    // accounting sync OR recon -> GREEN
    const a=(+(p[1]||0))>0||(+(p[5]||0))>0||(+(p[6]||0))>0||(+(p[8]||0))>0||(+(p[9]||0))>0
          ||(+(p[10]||0))>0||(+(p[11]||0))>0||(+(p[13]||0))>0||(+(p[18]||0))>0||(+(p[19]||0))>0;   // work -> AMBER
    const b=(+(p[12]||0))>0||(+(p[4]||0))>0;    // login / dashboard only -> BLUE (not real usage)
    const cls = g?"sync":(a?"on":(b?"seen":"off"));
    copytxt += (g||a) ? "●" : (b ? "◐" : "○");   // ● real usage · ◐ login-only · ○ none
    h+='<span class="dot '+cls+'" data-i="'+i+'" data-on="'+(on?1:0)
      +'" data-up="'+(p[1]||0)+'" data-syncs="'+(p[2]||0)+'" data-items="'+(p[3]||0)
      +'" data-views="'+(p[4]||0)+'" data-txns="'+(p[5]||0)+'" data-entities="'+(p[6]||0)
      +'" data-recon="'+(p[7]||0)+'" data-vmr="'+(p[8]||0)+'" data-mapping="'+(p[9]||0)
      +'" data-invoices="'+(p[10]||0)+'" data-deletes="'+(p[11]||0)+'" data-logins="'+(p[12]||0)
      +'" data-txnstatus="'+(p[13]||0)
      +'" data-lineitems="'+(p[14]||0)+'" data-txnlines="'+(p[15]||0)
      +'" data-reviewed="'+(p[16]||0)+'" data-needsreview="'+(p[17]||0)
      +'" data-botq="'+(p[18]||0)+'" data-botu="'+(p[19]||0)
      +'"></span>';
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
    var on=dot.getAttribute("data-on")==="1";
    html+=metricLine("Accounting Syncs", dot.getAttribute("data-syncs"))   // ungated & on top so a purple (sync) dot always explains itself first
         +(on?metricLine("Items Synced", dot.getAttribute("data-items")):"")   // right under Accounting Syncs
         +(on?metricLine("Uploads", dot.getAttribute("data-up")):"")
         +metricLine("Bot Queries", dot.getAttribute("data-botq"))     // WhatsApp bot (ungated: real signal)
         +metricLine("Bot Uploads", dot.getAttribute("data-botu"))
         +metricLine("Line Items", dot.getAttribute("data-lineitems"))     // 4 lines below, from user_daily_upload_summary; shown only when nonzero
         +metricLine("Tnx Lines", dot.getAttribute("data-txnlines"))
         +metricLine("Reviewed", dot.getAttribute("data-reviewed"))
         +metricLine("Needs Review", dot.getAttribute("data-needsreview"))
         +metricLine("Transaction status", dot.getAttribute("data-txnstatus"))
         +metricLine("Transactions updated", dot.getAttribute("data-txns"))
         +metricLine("Entities created", dot.getAttribute("data-entities"))
         +metricLine("Invoices created / bulk-edited", dot.getAttribute("data-invoices"))
         +metricLine("Recon processed", dot.getAttribute("data-recon"))
         +metricLine("Vendor mismatches resolved", dot.getAttribute("data-vmr"))
         +metricLine("Mapping completed", dot.getAttribute("data-mapping"))
         +metricLine("Deletes", dot.getAttribute("data-deletes"))
         +metricLine("Logins", dot.getAttribute("data-logins"))
         +metricLine("Dashboard Viewed", dot.getAttribute("data-views"));
    t.innerHTML=html;
    t.style.display="block";
    var r=dot.getBoundingClientRect(), tr=t.getBoundingClientRect();
    var top=r.top-tr.height-8; if(top<4) top=r.bottom+8;
    top=Math.max(4, Math.min(top, window.innerHeight-tr.height-4));   // keep inside the iframe (tall pending tips)
    var left=r.left+r.width/2-tr.width/2;
    left=Math.max(4, Math.min(left, window.innerWidth-tr.width-4));
    t.style.top=top+"px"; t.style.left=left+"px";
  }
  function hide(){ var t=el(); if(t) t.style.display="none"; }
  // Generic text tooltip for any cell carrying data-tip (from grid tip_cols) — a
  // JS bubble so it works on mobile-with-mouse, unlike a native title=. Deal names
  // can contain <>&, so escape before injecting; \n -> <br> for multi-line.
  function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
  function showText(elm){
    var t=el(); if(!t) return; clearTimeout(hideT);
    var txt=elm.getAttribute("data-tip"); if(!txt) return;
    t.innerHTML=esc(txt).replace(/\n/g,"<br>");
    t.style.display="block";
    var r=elm.getBoundingClientRect(), tr=t.getBoundingClientRect();
    var top=r.top-tr.height-8; if(top<4) top=r.bottom+8;
    top=Math.max(4, Math.min(top, window.innerHeight-tr.height-4));   // keep inside the iframe (tall pending tips)
    var left=r.left+r.width/2-tr.width/2;
    left=Math.max(4, Math.min(left, window.innerWidth-tr.width-4));
    t.style.top=top+"px"; t.style.left=left+"px";
  }
  document.addEventListener("mouseover", function(e){
    var dot=e.target.closest ? e.target.closest(".dot") : null;
    if(dot){ show(dot); return; }
    var te=e.target.closest ? e.target.closest("[data-tip]") : null;
    if(te) showText(te);
  });
  document.addEventListener("mouseout", function(e){
    var hit=e.target.closest ? (e.target.closest(".dot") || e.target.closest("[data-tip]")) : null;
    if(hit){ clearTimeout(hideT); hideT=setTimeout(hide, 600); }
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
  const tipCols=DATA.tipCols||{};
  const heatFrom=DATA.heatFrom||{};
  const classCols=DATA.classCols||{};
  const colW=DATA.colW||[];
  function clipWrap(i, inner, raw){    // cap a col_w column's content (+ full text on hover)
    const w=colW[i]; if(!w) return inner;
    const t=String(raw==null?"":raw).trim().replace(/"/g,"&quot;");
    // fixed-layout: fill the cell (which is already sized to col_w) so text
    // ellipsizes at the real column edge, not at a fixed px cap short of it.
    const mw = DATA.fixedLayout ? "100%" : (w+"px");
    return '<span class="clip" style="max-width:'+mw+'"'+(t?' title="'+t+'"':'')+'>'+inner+'</span>';
  }
  function cellCls(colName, r){        // CSS class from a hidden source column
    const src=classCols[colName]; if(!src) return "";
    const idx=colIdx[src]; if(idx===undefined) return "";
    return String(r[idx]==null?"":r[idx]).trim();
  }
  function tipAttr(colName, r){        // JS bubble (see streaktip IIFE) from a hidden
    const src=tipCols[colName]; if(!src) return "";   // source column. NOT native title=,
    const idx=colIdx[src]; if(idx===undefined) return "";  // which mobile browsers ignore.
    const t=String(r[idx]==null?"":r[idx]).trim();
    return t ? ' data-tip="'+t.replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/\n/g,"&#10;")+'"' : "";
  }
  const maxes={};
  bars.forEach((on,i)=>{ if(on) maxes[i]=colMax(i); });
  let rows=sortRows(cfApply(DATA.rows));
  const si=DATA.searchIdx||[];
  const sb=document.getElementById("search");
  const q=(si.length && sb)?(sb.value||"").toLowerCase().trim():"";
  if(q) rows=rows.filter(r=>si.some(i=>String(r[i]).toLowerCase().includes(q)));
  const stat=DATA.statusCol||[];
  const heat=DATA.heat||[];
  const hmax={};
  heat.forEach((c,i)=>{ if(c) hmax[i]=heatMax(i); });
  const inlineTot=DATA.inlineTotal;
  const rowNumIdx=(DATA.rowNumCol===undefined)?-1:DATA.rowNumCol;
  let html="";
  let rn=0;
  for(const r of rows){
    const isTot = inlineTot && (r[0]==null?'':String(r[0]).trim()).indexOf("Total")===0;
    if(!isTot) rn++;
    html+= isTot ? '<tr class="totrow">' : "<tr>";
    r.forEach((v,i)=>{
      if(hidden[i]) return;
      // serial column: always the row's position in the CURRENT view
      if(i===rowNumIdx && !isTot){ html+='<td class="'+cls(i)+'">'+rn+'</td>'; return; }
      let style="";
      if(heat[i] && !isTot){
        // heatFrom: shade from a hidden numeric column (e.g. the cohort %) rather
        // than the printed cell, so the colour tracks the metric regardless of
        // what the cell is showing.
        const hfSrc=heatFrom[cols[i]];
        const hfIdx=(hfSrc!==undefined)?colIdx[hfSrc]:undefined;
        const n=(hfIdx!==undefined)?numOf(r[hfIdx]):numOf(v);
        if(n!==null && n>0){
          const rl=(r[0]==null?'':String(r[0]).trim());
          const ro=(DATA.rowHeat && DATA.rowHeat[rl]) ? DATA.rowHeat[rl] : null;
          // heatMax pins a FIXED scale (e.g. 0-100%) so the same value is the same
          // colour everywhere; otherwise matrices (heatByRow) scale each row to its
          // OWN max, and the default scales each column to its column max.
          const denom = DATA.heatMax ? DATA.heatMax
                      : ((DATA.heatByRow || ro) ? rowHeatMax(r) : hmax[i]);
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
          html+='<td class="'+cls(i)+'"'+style+'>'+clipWrap(i,'<a href="'+href+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;cursor:pointer">'+inner+'</a>',v)+'</td>';
        } else {
          html+='<td class="'+cls(i)+'"'+style+'>'+clipWrap(i,inner,v)+'</td>';
        }
      } else if(strk[i] && typeof v==="string"){
        html+='<td class="streakcell">'+streakHtml(v)+'</td>';
      } else if(stat[i]){
        const sv=String(v);
        const SMAP={"Active":"st-active","Collected":"st-active",
                    "On Track":"st-active","Renewed":"st-active",
                    "Risk of Churn":"st-risk","Pending":"st-risk","Due Soon":"st-risk",
                    "Inactive":"st-inactive","Overdue":"st-inactive",
                    "Upcoming":"st-black","Churned":"st-churned","Parked":"st-churned"};
        const c=SMAP[sv]||"";
        html+='<td class="'+cls(i)+'"><span class="'+c+'">'+fmt(v,num[i])+'</span></td>';
      } else if(bars[i] && typeof v==="number" && v>0 && maxes[i]>0){
        const w=Math.max(3, Math.round(v/maxes[i]*100));
        const bc=(DATA.barColors && DATA.barColors[i]) ? DATA.barColors[i] : (DATA.barColor||"#c5e07a");
        html+='<td class="'+cls(i)+'"><span class="bar-wrap">'
             +'<span class="bar-fill" style="width:'+w+'%;background:'+bc+'"></span>'
             +'<span class="bar-val">'+fmt(v,num[i])+'</span></span></td>';
      } else {
        const cc=cellCls(colName,r);
        const inner=cc ? '<span class="'+cc+'">'+fmt(v,num[i])+'</span>' : fmt(v,num[i]);
        html+='<td class="'+cls(i)+'"'+style+tipAttr(colName,r)+'>'+clipWrap(i,inner,v)+'</td>';
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
  tbl.className = ((DATA.fixed ? "fixed" : "") + (DATA.fixedLayout ? " fl" : "")).trim();
  if(DATA.firstW) tbl.style.setProperty("--firstw", DATA.firstW+"px");
  else tbl.style.removeProperty("--firstw");
  document.getElementById("searchbar").style.display =
      (DATA.searchIdx && DATA.searchIdx.length) ? "block" : "none";
  header(); body(); foot();
  // "Clear filters (n)" pill: only while at least one column filter is active
  const nf=DATA.columns.filter(c=>cfOn(CF[c])).length, bar=document.getElementById("cfbar");
  bar.style.display=(nf && !isSnap()) ? "block" : "none";
  document.getElementById("cfclrall").textContent="✕ Clear filters ("+nf+")";
  var FORCE_FULL=false;
  try{ FORCE_FULL = !!(window.parent && window.parent.__SNAPSHOT__); }catch(e){}
  if(DATA.autosize || FORCE_FULL){
    try{
      if(window.frameElement){
        // measure the ACTUAL content (table + search bar), not the iframe viewport
        const sb=document.getElementById("searchbar");
        const cb=document.getElementById("cfbar");
        const sbh=((sb && sb.style.display!=="none") ? sb.offsetHeight : 0)
                 +((cb && cb.style.display!=="none") ? cb.offsetHeight : 0);
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
