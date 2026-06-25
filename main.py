"""
AIA + VA Operations Dashboard — 5 Pages
Run: python main.py
"""
import os
import sys
import math
import json
import base64
import unicodedata
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import threading
import time as _time
import pandas as pd
import numpy as np
from datetime import date, datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import psycopg2
import plotly.graph_objects as go
from flask import Flask
from taipy.gui import Gui, navigate

_IST = timezone(timedelta(hours=5, minutes=30))

from grid_server import grid_bp, grid_payload_b64, pie_payload_b64

load_dotenv()

# Flask app hosting the custom sortable data grids (served into iframes).
flask_app = Flask(__name__)
flask_app.register_blueprint(grid_bp)

# ── Freeze the header against browser zoom ──────────────────────────────────
# Browsers scale all content on zoom; this counter-scales the .topbar by the
# inverse of the current zoom (detected via devicePixelRatio vs. load-time
# baseline) so the nav bar + filters stay the same physical size at any zoom.
_ZOOM_LOCK_SCRIPT = """
<script id="zoom-lock">
(function () {
  var BASE = window.devicePixelRatio || 1;
  function fix() {
    var z = (window.devicePixelRatio || 1) / BASE;
    var inv = 1 / z;
    // measure the real content left edge (the page title / first card)
    var ref = document.querySelector('.page-header')
           || document.querySelector('.kpi-card')
           || document.querySelector('.chart-card');
    var L = ref ? ref.getBoundingClientRect().left : 16;
    var vw = document.documentElement.clientWidth;
    var bars = document.getElementsByClassName('topbar');
    for (var i = 0; i < bars.length; i++) {
      var b = bars[i];
      // bar background tracks the content width (never spills).
      // setProperty(..,'important') so it overrides the !important CSS rules.
      b.style.setProperty('left', L + 'px', 'important');
      b.style.setProperty('right', 'auto', 'important');
      b.style.setProperty('width', (vw - 2 * L) + 'px', 'important');
      // freeze the bar height + its contents against browser zoom
      b.style.setProperty('min-height', (84 * inv) + 'px', 'important');
      var kids = b.children;
      for (var j = 0; j < kids.length; j++) kids[j].style.setProperty('zoom', String(inv), 'important');
    }
    var root = document.getElementById('root');
    if (root) root.style.setProperty('padding-top', (104 * inv) + 'px', 'important');
  }
  fix();
  setInterval(fix, 400);
  window.addEventListener('resize', fix);
})();
</script>
"""

# ── Keyboard page navigation ────────────────────────────────────────────────
# Alt+PageDown -> next page, Alt+PageUp -> previous page (Excel-style sheet hop),
# Alt+1..5 -> jump straight to that page.
# (Ctrl+PgDn/PgUp can't be used: browsers reserve those for switching browser tabs.)
_PAGE_NAV_SCRIPT = """
<script id="page-nav">
(function () {
  var ORDER = ["/aia", "/cs", "/marketing", "/va-ops", "/va-finance"];
  function nav(target) {
    var links = Array.prototype.slice.call(document.querySelectorAll(".main-nav a"));
    var link = links.filter(function (a) { return a.pathname.replace(/\\/+$/, "") === target; })[0];
    if (link) link.click();                     // use Taipy's SPA router (no reload)
    else location.href = target;
  }
  function go(delta) {
    var path = (location.pathname || "").replace(/\\/+$/, "");
    var idx = ORDER.indexOf(path);
    if (idx < 0) idx = 0;                        // "/" (root) -> treat as first page
    nav(ORDER[(idx + delta + ORDER.length) % ORDER.length]);
  }
  document.addEventListener("keydown", function (e) {
    // Ctrl+Shift+5 → manual data refresh (re-pull from the databases)
    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && (e.code === "Digit5" || e.key === "%")) {
      e.preventDefault();
      var rbtn = document.querySelector("#manual-refresh-btn button") || document.getElementById("manual-refresh-btn");
      if (rbtn) rbtn.click();
      return;
    }
    // Alt+Shift+R → reset all filters to defaults
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.key === "R") {
      e.preventDefault();
      var btn = document.querySelector("#reset-filters-btn button") || document.getElementById("reset-filters-btn");
      if (btn) btn.click();
      return;
    }
    if (!e.altKey || e.ctrlKey || e.shiftKey || e.metaKey) return;
    if (e.key === "PageDown") { e.preventDefault(); go(1); }
    else if (e.key === "PageUp") { e.preventDefault(); go(-1); }
    else if (/^Digit[1-5]$/.test(e.code || "")) {
      var n = parseInt(e.code.slice(5), 10) - 1;  // Alt+1 -> page 0, ... Alt+5 -> page 4
      if (n < ORDER.length) { e.preventDefault(); nav(ORDER[n]); }
    }
  });
})();
</script>
"""

# ── Custom multi-select dropdowns (checkbox panel + summary label) ──────────
# Enhances each <div class="msc" data-key="..."> in the filter bar into a
# checkbox dropdown. Options + current selection come from the sibling hidden
# <div class="msc-data-KEY"> (JSON written by Taipy). On toggle it writes
# "KEY|<json-list>||<counter>" into the shared .msbridge input → on_ms_change.
# Summary text: 0 selected → "All", 1 → that name, >1 → "Multiple Selections".
# The panel closes only on an outside click.
_MULTISELECT_SCRIPT = """
<script id="ms-dropdowns">
(function () {
  var CTR = 0;
  function lbl(sel){ return sel.length===0 ? "All" : (sel.length===1 ? sel[0] : "Multiple Selections"); }
  function bridge(key, sel){
    try{
      var host = document.querySelector(".msbridge");
      var input = host && host.querySelector("input, textarea");
      if(!input) return;
      CTR += 1;
      var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, key + "|" + JSON.stringify(sel) + "||" + CTR);
      input.dispatchEvent(new Event("input", {bubbles:true}));
    }catch(e){}
  }
  function render(msc, data){
    var key = msc.getAttribute("data-key");
    var sel = (data.sel || []).slice();
    var txt = msc.querySelector(".msc-text");
    var panel = msc.querySelector(".msc-panel");
    if(txt) txt.textContent = lbl(sel);
    if(!panel) return;
    panel.innerHTML = "";
    // search box — filters the option rows as you type (toggles a class, since
    // .msc-opt uses display:flex !important which inline styles can't override)
    var search = document.createElement("input");
    search.className = "msc-search";
    search.type = "text";
    search.placeholder = "Search\\u2026";
    search.addEventListener("input", function(){
      var q = search.value.toLowerCase();
      panel.querySelectorAll(".msc-opt:not(.msc-all)").forEach(function(r){
        var t = (r.getAttribute("data-opt") || "").toLowerCase();
        r.classList.toggle("msc-hidden", t.indexOf(q) < 0);
      });
    });
    panel.appendChild(search);
    // "All" row — clears every chosen option in this list
    var allRow = document.createElement("div");
    allRow.className = "msc-opt msc-all" + (sel.length === 0 ? " sel" : "");
    var acb = document.createElement("span"); acb.className = "msc-cb";
    var alab = document.createElement("span"); alab.className = "msc-optlabel"; alab.textContent = "All";
    allRow.appendChild(acb); allRow.appendChild(alab);
    allRow.addEventListener("click", function(e){
      e.stopPropagation();
      sel.length = 0;
      panel.querySelectorAll(".msc-opt").forEach(function(r){ r.classList.remove("sel"); });
      allRow.classList.add("sel");
      if(txt) txt.textContent = lbl(sel);
      bridge(key, sel);
    });
    panel.appendChild(allRow);
    (data.lov || []).forEach(function(opt){
      var row = document.createElement("div");
      row.className = "msc-opt" + (sel.indexOf(opt) >= 0 ? " sel" : "");
      row.setAttribute("data-opt", opt);
      var cb = document.createElement("span"); cb.className = "msc-cb";
      var t  = document.createElement("span"); t.className = "msc-optlabel"; t.textContent = opt;
      row.appendChild(cb); row.appendChild(t);
      row.addEventListener("click", function(e){
        e.stopPropagation();
        var i = sel.indexOf(opt);
        if(i >= 0){ sel.splice(i,1); row.classList.remove("sel"); }
        else { sel.push(opt); row.classList.add("sel"); }
        allRow.classList.toggle("sel", sel.length === 0);
        if(txt) txt.textContent = lbl(sel);
        bridge(key, sel);
      });
      panel.appendChild(row);
    });
  }
  function dataFor(msc){
    var h = document.querySelector(".msc-data-" + msc.getAttribute("data-key"));
    if(!h) return null;
    var raw = (h.textContent || "").trim();
    if(!raw) return null;
    try{
      var bin = atob(raw);
      var utf8 = decodeURIComponent(Array.prototype.map.call(bin, function(c){
        return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
      }).join(""));
      return {raw: raw, obj: JSON.parse(utf8)};
    }catch(e){ return null; }
  }
  function bindOnce(msc){
    if(msc.__msInit) return;
    msc.__msInit = true;
    var box = msc.querySelector(".msc-box");
    if(box) box.addEventListener("click", function(e){
      e.stopPropagation();
      var open = msc.classList.contains("open");
      document.querySelectorAll(".msc.open").forEach(function(o){ if(o!==msc) o.classList.remove("open"); });
      msc.classList.toggle("open", !open);
    });
    // clicks inside the panel (search box, option rows) must not close it
    var panel = msc.querySelector(".msc-panel");
    if(panel) panel.addEventListener("click", function(e){ e.stopPropagation(); });
  }
  function scan(){
    document.querySelectorAll(".msc").forEach(function(msc){
      bindOnce(msc);
      // don't rebuild while the user has the panel open (avoids scroll reset
      // mid-selection); it re-syncs from the server data once closed.
      if(msc.classList.contains("open")) return;
      var d = dataFor(msc);
      if(d && d.raw !== msc.__msLast){ msc.__msLast = d.raw; render(msc, d.obj); }
    });
  }
  document.addEventListener("click", function(){
    document.querySelectorAll(".msc.open").forEach(function(o){ o.classList.remove("open"); });
  });
  var pending = false;
  function schedule(){ if(pending) return; pending = true; setTimeout(function(){ pending = false; scan(); }, 40); }
  try{ new MutationObserver(schedule).observe(document.body, {childList:true, subtree:true}); }catch(e){}
  if(document.readyState !== "loading") scan();
  else document.addEventListener("DOMContentLoaded", scan);
})();
</script>
"""

@flask_app.after_request
def _inject_zoom_lock(resp):
    try:
        if resp.headers.get("Content-Type", "").startswith("text/html"):
            html = resp.get_data(as_text=True)
            if "</body>" in html and 'id="zoom-lock"' not in html:
                resp.set_data(html.replace(
                    "</body>",
                    _ZOOM_LOCK_SCRIPT + _PAGE_NAV_SCRIPT + _MULTISELECT_SCRIPT + "</body>"))
                resp.headers["Content-Length"] = str(len(resp.get_data()))
    except Exception:
        pass
    return resp

_PIE_COLORS = ["#1a7fc4", "#16a34a", "#ea580c", "#8b5cf6", "#dc2626",
               "#0891b2", "#ca8a04", "#475569", "#db2777", "#65a30d"]

# Per-row heatmap colours for the cohort matrices: the Fresh Renewals row uses a
# deep-blue heatmap (distinct from the pale Total row) and the One-time row a
# light-orange one (instead of the column green).
_MATRIX_ROW_HEAT = {"Fresh Renewals": "deepblue", "One-time": "lightorange"}

def _make_pie(df, label_col, value_col, height=340):
    """Pie with labels+percent pulled OUTSIDE the slices, bold and high-contrast,
    with leader lines. Clicking a legend entry isolates that slice (native Plotly)."""
    if df is None or len(df) == 0 or value_col not in df.columns:
        return go.Figure()
    labels = df[label_col].astype(str).tolist()
    values = df[value_col].tolist()
    fig = go.Figure(go.Pie(
        labels=labels, values=values, sort=False, direction="clockwise",
        hole=0.45,
        textposition="outside",
        textinfo="label+value+percent",
        texttemplate="<b>%{label}</b><br>%{value:,} (%{percent})",
        textfont={"size": 13, "color": "#1a3a6b", "family": "Inter,sans-serif"},
        outsidetextfont={"size": 13, "color": "#1a3a6b", "family": "Inter,sans-serif"},
        marker={"colors": _PIE_COLORS, "line": {"color": "white", "width": 2}},
        pull=[0.02] * len(labels),
        hovertemplate="<b>%{label}</b><br>%{value:,} • %{percent}<extra></extra>",
        automargin=True,
    ))
    fig.update_layout(
        margin={"l": 30, "r": 30, "t": 20, "b": 20}, height=height,
        paper_bgcolor="rgba(0,0,0,0)", showlegend=True,
        legend={"orientation": "h", "y": -0.08, "x": 0.5, "xanchor": "center",
                "font": {"size": 11, "family": "Inter,sans-serif"}},
        font={"family": "Inter,sans-serif", "size": 12},
    )
    return fig

def _make_funnel(stages, values, labels):
    """Horizontal funnel: stage names on the LEFT, value labels INSIDE when they
    fit and OUTSIDE (to the right) for bars too small to hold them."""
    maxv = max(values) if values and max(values) else 1
    tpos = ["outside" if (v / maxv) < 0.22 else "inside" for v in values]
    fig = go.Figure(go.Funnel(
        y=stages, x=values,
        text=labels, textinfo="text", textposition=tpos,
        insidetextfont={"size": 16, "color": "white", "family": "Inter,sans-serif"},
        outsidetextfont={"size": 16, "color": "#1a3a6b", "family": "Inter,sans-serif"},
        marker={"color": ["#90CAF9", "#42A5F5", "#1E88E5", "#1976D2", "#1565C0"]},
        connector={"line": {"color": "#cbd5e1", "width": 1}},
    ))
    fig.update_layout(**aia_funnel_layout)
    return fig


def _make_trend(labels, dc, qual):
    """Line + column combo (Power BI style): DC as blue bars, Qualified as a
    dark-blue line with markers; value labels in soft rounded boxes; bold dates."""
    xb = [f"<b>{l}</b>" for l in labels]   # slightly bold date ticks
    bar_c, line_c = "#1a7fc4", "#1f4e79"
    fig = go.Figure()
    fig.add_bar(x=xb, y=dc, name="DC", marker_color=bar_c, marker_line_width=0,
                text=[str(d) if d else "" for d in dc], textposition="outside",
                textfont={"size": 10, "color": "#1a3a6b", "family": "Inter,sans-serif"},
                cliponaxis=False)
    fig.add_scatter(x=xb, y=qual, name="Qualified", mode="lines+markers",
                    line={"color": line_c, "width": 3, "shape": "spline"},
                    marker={"size": 7, "color": line_c})

    # soft rounded label boxes for the LINE points only
    anns = []
    for x, q in zip(xb, qual):
        if q:
            anns.append(dict(x=x, y=q, text=f"<b>{q}</b>", showarrow=False, yshift=13,
                             bgcolor="#e6edf6", bordercolor="#9fb6d4", borderpad=3,
                             font=dict(size=10, color=line_c, family="Inter,sans-serif")))
    fig.update_layout(
        barmode="group", height=360, annotations=anns,
        margin={"l": 40, "r": 20, "t": 30, "b": 90},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter,sans-serif", "size": 12},
        legend={"orientation": "h", "y": -0.34, "x": 0},
        xaxis={"title": "", "tickangle": -45,
               "tickfont": {"size": 11, "family": "Inter,sans-serif", "color": "#1a3a6b"}},
        yaxis={"title": "", "showgrid": True, "gridcolor": "#eef2f7"},
    )
    return fig


def _usage_28(email):
    """Usage in the last 28 days for a customer's account. Returns
    (active_days_count, streak). `streak` encodes 28 days as ';'-joined tokens
    "on,uploads,syncs,items" (index 0 = today .. 27 = today-27d): on=1 when there was
    any upload OR sync that day; uploads = sum of total_uploads (all upload types,
    NOT syncs); syncs = number of sync events; items = sum of items_count for that
    day. The grid renders dots + a per-day tooltip from this."""
    ac = _EMAIL_ACCT.get(_clean_email(email))
    today = pd.Timestamp(date.today()).normalize()
    blank = ";".join(["0,0,0,0"] * 28)
    if ac is None:
        return 0, blank
    start = today - pd.Timedelta(days=27)
    active = set(); uploads = {}; syncs = {}; items = {}
    if "date" in _UPL.columns:
        u = _UPL[(_UPL["account_id"] == ac) & (_UPL["date"] >= start) & (_UPL["date"] <= today)].copy()
        if len(u) and "total_uploads" in u.columns:
            u["_d"] = u["date"].dt.normalize()
            active |= set(u[u["total_uploads"].fillna(0) > 0]["_d"])
            uploads = u.groupby("_d")["total_uploads"].sum().to_dict()
    if "event_date" in _SYN.columns:
        sy = _SYN[(_SYN["account_id"] == ac) & (_SYN["event_date"] >= start) & (_SYN["event_date"] <= today)].copy()
        if len(sy):
            sy["_d"] = sy["event_date"].dt.normalize()
            if "items_count" in sy.columns:
                active |= set(sy[sy["items_count"].fillna(0) > 0]["_d"])
                items = sy.groupby("_d")["items_count"].sum().to_dict()
            syncs = sy.groupby("_d").size().to_dict()
    toks = []
    for i in range(28):
        d = today - pd.Timedelta(days=i)
        toks.append("%d,%d,%d,%d" % (1 if d in active else 0, int(uploads.get(d, 0) or 0),
                                     int(syncs.get(d, 0) or 0), int(items.get(d, 0) or 0)))
    return len(active), ";".join(toks)


def _va_mrr(record_ids):
    """₹ MRR_VA (DAX): for the given paid VA records, sum unit_price/term over
    their line items (excluding One-time), preferring 'New' lines when present."""
    li = _VA_LI[_VA_LI["record_id"].isin(set(record_ids))]
    if "recurring_type" in li.columns:
        li = li[li["recurring_type"] != "One-time"]
        if (li["recurring_type"] == "New").any():
            li = li[li["recurring_type"] == "New"]
    if len(li) == 0:
        return 0
    term = pd.to_numeric(li["term"], errors="coerce").fillna(1)
    term = term.where(term > 0, 1)
    return int((li["unit_price"] / term).sum())


def _mkt_breakdown(mkt_df, aia_df, li_df, freq, label_name, label_fn, last_n=None,
                   drop_zero_spend=False):
    """Marketing performance broken down by period (month/week): Spend, Leads,
    CPL, DC, DC%, Paid, MRR, CAC, ARPU, Payback — with a pinned Total row."""
    spend_by = (mkt_df.dropna(subset=["day"]).groupby(mkt_df.dropna(subset=["day"])["day"].dt.to_period(freq))["cost"].sum()
                if "day" in mkt_df.columns and len(mkt_df) else pd.Series(dtype=float))
    aa = aia_df
    leads_by = (aa.dropna(subset=["create_date"]).groupby(aa.dropna(subset=["create_date"])["create_date"].dt.to_period(freq))["record_id"].nunique()
                if "create_date" in aa.columns else pd.Series(dtype=float))
    dcs = aa[aa["dc_date"].notna()] if "dc_date" in aa.columns else aa.iloc[0:0]
    dc_by  = dcs.groupby(dcs["dc_date"].dt.to_period(freq))["record_id"].nunique() if len(dcs) else pd.Series(dtype=float)
    pps = aa[aa["payment_date"].notna()] if "payment_date" in aa.columns else aa.iloc[0:0]
    paid_by = pps.groupby(pps["payment_date"].dt.to_period(freq))["record_id"].nunique() if len(pps) else pd.Series(dtype=float)
    lim = li_df.dropna(subset=["date_paid"]) if "date_paid" in li_df.columns else li_df.iloc[0:0]
    mrr_by = lim.groupby(lim["date_paid"].dt.to_period(freq))["mrr"].sum() if len(lim) else pd.Series(dtype=float)

    idxs = [s.index for s in [spend_by, leads_by, dc_by, paid_by, mrr_by] if len(s)]
    if not idxs:
        return pd.DataFrame()
    lo = min(i.min() for i in idxs); hi = max(i.max() for i in idxs)
    full = pd.period_range(lo, hi, freq=freq)
    if drop_zero_spend:
        sp_full = spend_by.reindex(full, fill_value=0)
        full = full[[float(sp_full[p]) > 0 for p in full]]
    if last_n:
        full = full[-last_n:]
    if len(full) == 0:
        return pd.DataFrame()
    g = lambda s: s.reindex(full, fill_value=0)
    spend, leads, dc, paid, mrr = g(spend_by), g(leads_by), g(dc_by), g(paid_by), g(mrr_by)

    def _row(lbl, sp, ld, d, pv, mr):
        return {label_name: lbl, "Spend (₹)": sp, "Leads": ld,
                "CPL": sp // ld if ld else 0, "DC": d,
                "DC %": (f"{round(d/ld*100)}%" if ld else ""), "Paid": pv, "MRR": mr,
                "CAC": sp // pv if pv else 0, "ARPU": mr // pv if pv else 0,
                "Payback (mo)": round((sp/pv)/(mr/pv)) if (pv and mr) else 0}
    rows = [_row(label_fn(p), int(spend[p]), int(leads[p]), int(dc[p]),
                 int(paid[p]), int(mrr[p])) for p in full]
    rows.append(_row("Total", int(spend.sum()), int(leads.sum()), int(dc.sum()),
                     int(paid.sum()), int(mrr.sum())))
    return pd.DataFrame(rows)


def _usage_cohort():
    """Customer Usage Cohort (last 10 integration weeks). Rows = integration-week
    Monday; columns = Integrated (cohort size) + W0..W9 (active accounts that had
    any upload/sync activity in that week-offset window). Returns (counts_df,
    pct_df), each with a pinned Total row. Replicates the DAX cohort measures."""
    base = _AIA[(_AIA["integration_done_date"].notna())
                & (_AIA["login_email_id"].notna())
                & (_AIA["login_email_id"].astype(str).str.strip() != "")
                & (_AIA["module_type"] == "AIA Paid")].copy()
    if len(base) == 0:
        return pd.DataFrame(), pd.DataFrame()
    iw = base["integration_done_date"].dt.normalize()
    base["iw"] = iw - pd.to_timedelta(iw.dt.weekday, unit="D")     # Monday
    weeks = sorted([w for w in base["iw"].dropna().unique()])[-10:]  # last 10 weeks
    if not weeks:
        return pd.DataFrame(), pd.DataFrame()
    today = pd.Timestamp(date.today())
    OFFS = list(range(10))

    cnt_rows, pct_rows = [], []
    tot_int = 0
    tot_act = {o: 0 for o in OFFS}
    tot_size = {o: 0 for o in OFFS}
    tot_valid = {o: False for o in OFFS}

    for wk in weeks:
        wk = pd.Timestamp(wk)
        sub  = base[base["iw"] == wk]
        size = sub["record_id"].nunique()
        tot_int += size
        accts = {_EMAIL_ACCT.get(_clean_email(em)) for em in
                 sub["login_email_id"].dropna().unique()}
        accts.discard(None)
        label = wk.strftime("%d %b")
        crow = {"Integration Week": label, "Integrated": size}
        prow = {"Integration Week": label, "Integrated": size}
        for o in OFFS:
            cws = wk + pd.Timedelta(days=o * 7)
            col = f"W{o}"
            if cws > today:
                crow[col] = ""; prow[col] = ""
                continue
            active = sum(1 for a in accts if (a, cws) in _ACTIVE_WEEKS)
            crow[col] = active
            pct = round(active / size * 100) if size else 0
            prow[col] = (f"{pct}%" if pct else "")   # blank when 0%
            tot_act[o] += active; tot_size[o] += size; tot_valid[o] = True
        cnt_rows.append(crow); pct_rows.append(prow)

    cnt_tot = {"Integration Week": "Total", "Integrated": tot_int}
    pct_tot = {"Integration Week": "Total", "Integrated": tot_int}
    for o in OFFS:
        col = f"W{o}"
        cnt_tot[col] = tot_act[o] if tot_valid[o] else ""
        _tp = round(tot_act[o] / tot_size[o] * 100) if (tot_valid[o] and tot_size[o]) else 0
        pct_tot[col] = (f"{_tp}%" if _tp else "")   # blank when 0%
    cnt_rows.append(cnt_tot); pct_rows.append(pct_tot)
    return pd.DataFrame(cnt_rows), pd.DataFrame(pct_rows)


def _mrr_matrix(li, refund_map, mode, add_onetime=False):
    """Refunds-adjusted billing-to-MRR cohort matrix (replicates the DAX
    total_monthly_collection / #Active Paid Users). Each non-refunded line item
    is recognised across its active term (date_paid month .. +term, exclusive),
    attributed to its cohort row.
      mode="revenue"   -> cell = sum(unit_price / term); Fresh Renewals = sum(unit_price)
      mode="retention" -> cell = distinct active record_ids; Fresh Renewals = distinct record_ids
    Adds a 'Fresh Renewals' row (recurring_type == 'Renewal', by date_paid month)
    and a pinned 'Total' row (column sums of the cohort rows). YYYY-MM labels,
    continuous month span. Returns an empty frame when there is no data."""
    need = {"date_paid", "cohort_month", "term", "unit_price", "record_id", "recurring_type"}
    if li is None or len(li) == 0 or not need.issubset(li.columns):
        return pd.DataFrame()
    li = li.dropna(subset=["date_paid", "cohort_month"]).copy()
    if len(li) == 0:
        return pd.DataFrame()
    li["billing_p"] = li["date_paid"].dt.to_period("M")
    li["cohort_p"]  = li["cohort_month"].dt.to_period("M")
    if refund_map is not None:
        ref = li["record_id"].map(refund_map).astype("string").str.strip().str.lower()
        li = li[(ref != "yes").fillna(True)]
    if len(li) == 0:
        return pd.DataFrame()
    li["term_n"]  = li["term"].fillna(1).where(li["term"].fillna(1) > 0, 1).astype(int)
    # Replicate PBI calculated column: renewal_amount
    # monthly billing → unit_price is a per-month rate, so total = unit_price × term
    # all other frequencies → unit_price is already the full contract amount
    _is_monthly = li.get("billing_frequency", pd.Series("", index=li.index)).str.lower().str.strip() == "monthly"
    li["renewal_amount"] = li["unit_price"].where(~_is_monthly, li["unit_price"] * li["term_n"])
    li["monthly"] = li["renewal_amount"] / li["term_n"]

    recs = []
    for r in li.itertuples(index=False):
        for k in range(int(r.term_n)):
            recs.append((str(r.cohort_p), r.billing_p + k, r.record_id, r.monthly))
    sp = pd.DataFrame(recs, columns=["Cohort", "bp", "rid", "amt"])
    if len(sp) == 0:
        return pd.DataFrame()

    lo = min(li["cohort_p"].min(), li["billing_p"].min())
    hi = li["billing_p"].max()
    full = pd.period_range(lo, hi, freq="M")

    if mode == "revenue":
        piv = sp.pivot_table(index="Cohort", columns="bp", values="amt",
                             aggfunc="sum", fill_value=0)
    else:
        piv = sp.pivot_table(index="Cohort", columns="bp", values="rid",
                             aggfunc=pd.Series.nunique, fill_value=0)
    piv = piv.reindex(columns=full, fill_value=0)
    piv = piv.reindex(sorted(piv.index)).round(0).astype(int)
    cols = [p.strftime("%b %y") for p in full]      # mmm yy column headers
    piv.columns = cols
    out = piv.reset_index()
    out["Cohort"] = out["Cohort"].apply(lambda v: pd.Period(v, freq="M").strftime("%b %y"))

    def _by_month(sub):
        if mode == "revenue":
            b = sub.groupby(sub["date_paid"].dt.to_period("M"))["renewal_amount"].sum()
        else:
            b = sub.groupby(sub["date_paid"].dt.to_period("M"))["record_id"].nunique()
        return b.reindex(full, fill_value=0).round(0).astype(int)

    extra = []
    frb = _by_month(li[li["recurring_type"] == "Renewal"])
    extra.append({"Cohort": "Fresh Renewals", **{c: int(frb[p]) for c, p in zip(cols, full)}})
    if add_onetime:
        otb = _by_month(li[li["recurring_type"] == "One-time"])
        extra.append({"Cohort": "One-time", **{c: int(otb[p]) for c, p in zip(cols, full)}})
    extra.append({"Cohort": "Total", **{c: int(piv[c].sum()) for c in cols}})
    return pd.concat([out, pd.DataFrame(extra)], ignore_index=True)

NEON_URL     = os.getenv("NEON_DATABASE_URL", "")
SUPABASE_URL = os.getenv("SUPABASE_DATABASE_URL", "")

# ═══════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════

def _q(url, sql):
    with psycopg2.connect(url) as conn:
        return pd.read_sql_query(sql, conn)

def _load_all():
    try:
        aia = _q(NEON_URL, "SELECT * FROM public.aia_live WHERE is_deleted IS NULL")
        va  = _q(NEON_URL, "SELECT * FROM public.va_live WHERE is_deleted IS NULL")
        li  = _q(NEON_URL, "SELECT * FROM public.line_items WHERE deleted IS NULL")
        inc = _q(NEON_URL, "SELECT gm_combined, month, monthly_mrr_target, is_gap_carry_forwarded FROM public.incentive_targets ORDER BY month, gm_combined")
        mkt = _q(SUPABASE_URL, "SELECT * FROM public.marketing_spends ORDER BY day ASC")
        upl = _q(SUPABASE_URL, "SELECT * FROM public.user_daily_upload_summary ORDER BY date ASC")
        syn = _q(SUPABASE_URL, "SELECT * FROM public.accounting_sync_mixpanel")
        print(f"[OK] AIA:{len(aia)} VA:{len(va)} LI:{len(li)} INC:{len(inc)} MKT:{len(mkt)} UPL:{len(upl)} SYN:{len(syn)}")
        return aia, va, li, inc, mkt, upl, syn
    except Exception as ex:
        print(f"[WARN] DB error: {ex} -- using empty frames")
        cols_aia = ["record_id","deal_name","deal_stage","deal_owner","deal_source","create_date",
                    "ds_date","dc_date","eta_pay_date","payment_date","integration_done_date",
                    "activation_date","adopted_date","renewed_date","parked_date","discard_date",
                    "closed_lost_date","churned_date","amount_paid","billing_cycle","paid_for",
                    "cs_owner","prospect_score","asked_refund","utm_campaign","utm_source",
                    "login_email_id","aia_discard_reason","aia_parked_reason","aia_lost_reason",
                    "statement_frequency","bill_frequency","amount?","days_extended","poc_number","poc_email"]
        cols_va  = ["record_id","deal_name","deal_stage","deal_owner","deal_source","create_date",
                    "ds_date","dc_date","eta_pay_date","payment_date","amount_paid","billing_cycle",
                    "ot_amount_paid","ot_payment_date","renewed_date","parked_date","discard_date",
                    "closed_lost_date","prospect_score","utm_campaign","utm_source","amount?",
                    "va_discard_reason","va_parked_reason","va_lost_reason","services_bought","poc_number","poc_email"]
        cols_li  = ["record_id","deal_name","line_item_name","term","billing_frequency",
                    "unit_price","recurring_type","date_paid","billing_start_date","pipeline",
                    "days_extended","deleted","due_on"]
        cols_mkt = ["day","ad_campaign","campaign_type","cost","conversions","impressions","channel"]
        cols_upl = ["id","date","email","account_id","total_uploads","bill_uploads","statement_uploads"]
        cols_syn = ["email","items_count","event_date","account_id","sync_type"]
        cols_inc = ["gm_combined","month","monthly_mrr_target","is_gap_carry_forwarded"]
        return (pd.DataFrame(columns=cols_aia), pd.DataFrame(columns=cols_va),
                pd.DataFrame(columns=cols_li),  pd.DataFrame(columns=cols_inc),
                pd.DataFrame(columns=cols_mkt), pd.DataFrame(columns=cols_upl),
                pd.DataFrame(columns=cols_syn))

print("Loading data...")
_RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN = _load_all()

# Timestamp of the last successful data load, shown in each page header (IST).
_LAST_SYNC = datetime.now(_IST)

def _fmt_sync():
    return _LAST_SYNC.strftime("%d %b %Y – %H:%M") if _LAST_SYNC else "—"

last_synced = _fmt_sync()

# ═══════════════════════════════════════════════════════════════════
# COMPUTED COLUMNS
# ═══════════════════════════════════════════════════════════════════

def _dates(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.normalize()
    return df

def _nums(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df

def _prep_aia(df):
    df = df.copy()
    df = _dates(df, ["create_date","ds_date","dc_date","eta_pay_date","payment_date",
                     "integration_done_date","activation_date","adopted_date","renewed_date",
                     "parked_date","discard_date","closed_lost_date","churned_date"])
    df = _nums(df, ["amount_paid","prospect_score","days_extended"])
    if "amount?" in df.columns:
        df["amount_expected"] = pd.to_numeric(df["amount?"], errors="coerce").fillna(0)
    else:
        df["amount_expected"] = 0

    def _mod(p):
        if pd.isna(p) or str(p).strip() == "": return None
        s = str(p)
        if any(m in s for m in ["Bills Module","Transaction Module","Invoice Module","Dashboard Module"]): return "AIA Paid"
        if "GST Module" in s: return "GST Paid"
        return "Other"
    df["module_type"] = df["paid_for"].apply(_mod) if "paid_for" in df.columns else None

    def _src(s):
        if pd.isna(s): return "Others"
        s = str(s)
        if "GAds" in s or "Google" in s: return "Google Ads"
        if "Meta" in s: return "Meta Ads"
        if "LinkedIn" in s: return "LinkedIn Ads"
        if "Organic" in s: return "Organic"
        if "Referral" in s: return "Referral"
        return "Others"
    df["deal_source_group"] = df["deal_source"].apply(_src) if "deal_source" in df.columns else "Others"

    if "utm_campaign" in df.columns and "utm_source" in df.columns:
        df["utm_source_cohort"] = df.apply(
            lambda r: r["utm_source"] if pd.isna(r["utm_campaign"]) or str(r["utm_campaign"]).strip() == ""
            else r["utm_campaign"], axis=1)
    else:
        df["utm_source_cohort"] = None

    def _cadence(row):
        pri = {"Daily":4,"Weekly":3,"Bi weekly":2,"Monthly":1}
        bf = row.get("bill_frequency","") if row.get("bill_frequency","") in pri else None
        sf = row.get("statement_frequency","") if row.get("statement_frequency","") in pri else None
        if pri.get(bf,0) >= pri.get(sf,0): return bf or sf or "Monthly"
        return sf or bf or "Monthly"
    df["cadence"] = df.apply(_cadence, axis=1)

    today = pd.Timestamp(date.today())
    if "integration_done_date" in df.columns:
        df["days_since_int"] = (today - df["integration_done_date"]).dt.days.fillna(-1).astype(int)
    else:
        df["days_since_int"] = -1
    return df

def _prep_va(df):
    df = df.copy()
    df = _dates(df, ["create_date","ds_date","dc_date","eta_pay_date","payment_date",
                     "renewed_date","parked_date","discard_date","closed_lost_date","ot_payment_date"])
    df = _nums(df, ["amount_paid","ot_amount_paid","prospect_score"])
    if "amount?" in df.columns:
        df["amount_expected"] = pd.to_numeric(df["amount?"], errors="coerce").fillna(0)
    else:
        df["amount_expected"] = 0
    def _src(s):
        if pd.isna(s): return "Others"
        s = str(s)
        if "GAds" in s or "Google" in s: return "Google Ads"
        if "Meta" in s: return "Meta Ads"
        return "Others"
    df["deal_source_group"] = df["deal_source"].apply(_src) if "deal_source" in df.columns else "Others"
    if "utm_campaign" in df.columns and "utm_source" in df.columns:
        df["utm_source_cohort"] = df.apply(
            lambda r: r["utm_source"] if pd.isna(r["utm_campaign"]) or str(r["utm_campaign"]).strip() == ""
            else r["utm_campaign"], axis=1)
    else:
        df["utm_source_cohort"] = None
    return df

def _prep_li(raw):
    df = raw.copy()
    df = _dates(df, ["date_paid","billing_start_date","due_on"])
    df = _nums(df, ["unit_price","term","days_extended"])
    if "pipeline" in df.columns:
        df["pipeline"] = df["pipeline"].replace({"106069137":"AIA","1534965463":"Virtual Accounting"})
    freq_map = {"monthly":1,"bi_monthly":2,"quarterly":3,"per_six_months":6,"annually":12}
    df["mrr_divisor"] = df["billing_frequency"].map(freq_map).fillna(1)
    df["mrr"] = df["unit_price"] / df["mrr_divisor"].replace(0,1)
    fp = df.groupby("record_id")["date_paid"].min().reset_index()
    fp.columns = ["record_id","first_purchase_date"]
    df = df.merge(fp, on="record_id", how="left")
    df["cohort_month"] = df["first_purchase_date"].apply(
        lambda d: d.replace(day=1) if pd.notna(d) else pd.NaT)
    aia_li = df[df["pipeline"]=="AIA"].copy()
    va_li  = df[df["pipeline"]=="Virtual Accounting"].copy()
    return aia_li, va_li

_AIA    = _prep_aia(_RAW_AIA)
_VA     = _prep_va(_RAW_VA)
_AIA_LI, _VA_LI = _prep_li(_RAW_LI)
_INCENTIVE_TARGETS = _RAW_INC.copy()
if "month" in _INCENTIVE_TARGETS.columns:
    _INCENTIVE_TARGETS["month"] = pd.to_datetime(_INCENTIVE_TARGETS["month"]).dt.normalize()

_MKT = _RAW_MKT.copy()
if "day" in _MKT.columns:
    _MKT["day"] = pd.to_datetime(_MKT["day"], errors="coerce")
    _MKT = _nums(_MKT, ["cost","conversions","impressions"])

_UPL = _RAW_UPL.copy()
if "date" in _UPL.columns:
    _UPL["date"] = pd.to_datetime(_UPL["date"], errors="coerce")
    _UPL = _nums(_UPL, ["total_uploads","bill_uploads","statement_uploads"])

_SYN = _RAW_SYN.copy()
if "event_date" in _SYN.columns:
    _SYN["event_date"] = pd.to_datetime(_SYN["event_date"], errors="coerce")
    _SYN = _nums(_SYN, ["items_count"])

def _clean_email(v):
    """Normalise an email for lookup: lower-case, trim, and strip any Unicode
    'other/control' characters (e.g. a stray U+2060 word-joiner that some CRM
    exports prepend) which otherwise break email→account matching."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = "".join(c for c in str(v) if unicodedata.category(c)[0] != "C")
    return s.lower().strip()

# ── Usage-cohort lookups (precomputed once) ─────────────────────────────────
# email -> account_id, and the set of (account_id, week-Monday) that had any
# upload OR sync activity. Used to build the Customer Usage Cohort table fast.
def _build_activity_lookups():
    email_acct = {}
    active_weeks = set()
    for src, dcol in [(_UPL, "date"), (_SYN, "event_date")]:
        if "account_id" not in src.columns or dcol not in src.columns:
            continue
        t = src[["account_id", dcol]].dropna(subset=["account_id", dcol]).copy()
        if "email" in src.columns:
            for em, ac in src[["email", "account_id"]].dropna().itertuples(index=False):
                em = _clean_email(em)
                if em and em not in email_acct:
                    email_acct[em] = ac
        d = pd.to_datetime(t[dcol], errors="coerce").dt.normalize()
        mon = d - pd.to_timedelta(d.dt.weekday, unit="D")
        for ac, mm in zip(t["account_id"], mon):
            if pd.notna(mm):
                active_weeks.add((ac, mm))
    return email_acct, active_weeks

_EMAIL_ACCT, _ACTIVE_WEEKS = _build_activity_lookups()

def _build_acct_dates():
    """account_id -> set of normalised dates that had any upload/sync row.
    Matches PBI COUNTROWS approach — any row counts as activity regardless of items_count."""
    m = {}
    for src, dcol in [(_UPL, "date"), (_SYN, "event_date")]:
        if dcol not in src.columns or "account_id" not in src.columns:
            continue
        t = src[["account_id", dcol]].dropna(subset=["account_id", dcol]).copy()
        d = pd.to_datetime(t[dcol], errors="coerce").dt.normalize()
        for ac, dd in zip(t["account_id"], d):
            if pd.notna(dd):
                m.setdefault(ac, set()).add(dd)
    return m

_ACCT_DATES = _build_acct_dates()

def _build_billing_end():
    """record_id -> billing end date (DAX billing_end_date): max over the
    record's line items of billing_start_date shifted by term/frequency, plus
    days_extended."""
    li = _AIA_LI
    need = {"record_id", "billing_start_date"}
    if li is None or len(li) == 0 or not need.issubset(li.columns):
        return {}
    def _end(r):
        sd = r.get("billing_start_date")
        if pd.isna(sd):
            return pd.NaT
        term = r.get("term"); term = 1 if (pd.isna(term) or term <= 0) else int(term)
        f = r.get("billing_frequency")
        if pd.isna(f) or str(f).strip() == "":
            base = sd
        else:
            months = {"monthly": term, "quarterly": 3, "per_six_months": 6,
                      "annually": 12}.get(str(f).strip())
            base = sd + relativedelta(months=months) if months else sd
        de = r.get("days_extended"); de = 0 if pd.isna(de) else int(de)
        return base + pd.Timedelta(days=de)
    tmp = li.copy()
    tmp["_end"] = tmp.apply(_end, axis=1)
    return tmp.groupby("record_id")["_end"].max().to_dict()

_BILLING_END = _build_billing_end()

def _reload_data():
    """Re-pull everything from the databases and rebuild the in-memory frames /
    lookups. Used by the scheduled auto-refresh so the dashboard shows fresh
    data without a restart."""
    global _RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN
    global _AIA, _VA, _AIA_LI, _VA_LI, _INCENTIVE_TARGETS, _MKT, _UPL, _SYN
    global _EMAIL_ACCT, _ACTIVE_WEEKS, _ACCT_DATES, _BILLING_END, _LAST_SYNC
    _RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN = _load_all()
    _AIA = _prep_aia(_RAW_AIA)
    _VA  = _prep_va(_RAW_VA)
    _AIA_LI, _VA_LI = _prep_li(_RAW_LI)
    _INCENTIVE_TARGETS = _RAW_INC.copy()
    if "month" in _INCENTIVE_TARGETS.columns:
        _INCENTIVE_TARGETS["month"] = pd.to_datetime(_INCENTIVE_TARGETS["month"]).dt.normalize()
    _MKT = _RAW_MKT.copy()
    if "day" in _MKT.columns:
        _MKT["day"] = pd.to_datetime(_MKT["day"], errors="coerce")
        _MKT = _nums(_MKT, ["cost", "conversions", "impressions"])
    _UPL = _RAW_UPL.copy()
    if "date" in _UPL.columns:
        _UPL["date"] = pd.to_datetime(_UPL["date"], errors="coerce")
        _UPL = _nums(_UPL, ["total_uploads", "bill_uploads", "statement_uploads"])
    _SYN = _RAW_SYN.copy()
    if "event_date" in _SYN.columns:
        _SYN["event_date"] = pd.to_datetime(_SYN["event_date"], errors="coerce")
        _SYN = _nums(_SYN, ["items_count"])
    _EMAIL_ACCT, _ACTIVE_WEEKS = _build_activity_lookups()
    _ACCT_DATES = _build_acct_dates()
    _BILLING_END = _build_billing_end()
    _LAST_SYNC = datetime.now(_IST)

def _due_on(record_id):
    d = _BILLING_END.get(record_id)
    return "" if (d is None or pd.isna(d)) else pd.Timestamp(d).strftime("%d-%b-%y")

def _acct_for(email):
    return _EMAIL_ACCT.get(_clean_email(email))

def _activity_between(acct, start, end):
    """count of active days for an account within [start, end] inclusive."""
    ds = _ACCT_DATES.get(acct)
    if not ds:
        return 0
    return sum(1 for d in ds if start <= d <= end)

def _activity_to(acct, end):
    """Count all activity dates up to `end` (no lower bound).
    Matches PBI's DISTINCTCOUNT(date <= MilestoneDate) for initial-phase checks."""
    ds = _ACCT_DATES.get(acct)
    if not ds:
        return 0
    return sum(1 for d in ds if d <= end)

# ── CSM health measures (per integrated AIA-paid customer record) ───────────
_CAD_W      = {"Daily": 4, "Weekly": 7, "Bi weekly": 10, "Monthly": 14}
_CAD_INITEND = {"Daily": 15, "Weekly": 20, "Bi weekly": 25, "Monthly": 29}
_CAD_NWIN   = {"Daily": 7, "Weekly": 4, "Bi weekly": 3, "Monthly": 2}
_CAD_PASTINIT = {"Daily": 15, "Weekly": 20, "Bi weekly": 25, "Monthly": 29}
# Initial-phase checkpoint DAYS (the day-offsets at which a milestone is checked).
# The REQUIRED active-day count at each checkpoint is computed dynamically as
# ceil(day / cadence_window) — i.e. one active day per cadence window elapsed,
# matching PBI (the prior hard-coded counts grew far too fast and over-flagged).
_MILESTONES = {
    "Daily":     {3:1, 5:2, 7:3, 9:4, 11:5, 13:6, 15:7},
    "Weekly":    {4:1, 8:2, 12:3, 16:4, 20:5},
    "Bi weekly": {4:1, 9:2, 14:3, 19:4, 25:5},
    "Monthly":   {3:1, 8:2, 15:3, 22:4, 29:5},
}

def _milestone_req(cad, day):
    """Required cumulative active-days by initial-phase day `day` for cadence
    `cad`: ceil(day / window) = number of cadence windows elapsed. Matches PBI."""
    w = _CAD_W.get(cad, 7)
    return math.ceil(day / w) if w else 0

def _cadence_of(row):
    def _norm(v):
        v = str(v).strip() if pd.notna(v) else ""
        return "" if v in ("NA", "") else v
    bf = _norm(row.get("bill_frequency"));  sf = _norm(row.get("statement_frequency"))
    pr = {"Daily": 4, "Weekly": 3, "Bi weekly": 2, "Monthly": 1}
    bp, sp = pr.get(bf, 0), pr.get(sf, 0)
    if bp > sp and bf: return bf
    if sp > bp and sf: return sf
    return bf or sf or "Monthly"

def _continuous_missed(acct, intdate, cad, days_since, today):
    """Replica of Continuous_Missed_measure (post-initial window logic)."""
    W = _CAD_W.get(cad, 7)
    past_initial = days_since > _CAD_PASTINIT.get(cad, 29)
    if past_initial:
        used = []
        for k in range(6):                       # W6 (most recent) .. W1
            wend = today - pd.Timedelta(days=k * W)
            wstart = wend - pd.Timedelta(days=W)
            if wstart < intdate:
                used.append(None)
            else:
                used.append(1 if (acct and _activity_between(acct, wstart, wend - pd.Timedelta(days=1))) else 0)
        miss = [1 if (u is None or u == 0) else 0 for u in used]   # miss6..miss1
        recent = miss[0] + miss[1]
        silent = miss[0] + miss[1] + miss[2] + miss[3]
        total = sum(miss)
        if silent >= 4: return 6
        if recent == 0: return 0
        return total
    # initial phase: count due milestones missed (consecutive from start)
    due = sorted([d for d in _MILESTONES.get(cad, {}) if d <= days_since])
    if not due:
        return 0
    missed = 0
    for day in due:
        req = _milestone_req(cad, day)
        usage = _activity_to(acct, intdate + pd.Timedelta(days=day)) if acct else 0
        if usage < req:
            missed += 1
        else:
            missed = 0   # streak resets on a hit
    return missed

def _customer_status_m(acct, intdate, cad, days_since, today):
    m = _continuous_missed(acct, intdate, cad, days_since, today)
    if m is None: return None
    if m >= 6: return "Inactive"
    if m >= 3: return "Risk of Churn"
    return "Active"

def _total_flags_30d(acct, intdate, cad, today):
    W = _CAD_W.get(cad, 7); n = _CAD_NWIN.get(cad, 4)
    flags = 0
    for i in range(1, n + 1):
        wend = today - pd.Timedelta(days=(i - 1) * W)
        wstart = wend - pd.Timedelta(days=W)
        if wstart >= intdate:
            if not acct or _activity_between(acct, wstart, wend - pd.Timedelta(days=1)) == 0:
                flags += 1
    return flags

def _flagged_yesterday(acct, intdate, cad, days_since, today):
    yest = days_since - 1
    if days_since < 3:
        return False
    W = _CAD_W.get(cad, 7); init_end = _CAD_INITEND.get(cad, 20)
    if yest > init_end:
        post_start = yest - init_end
        if post_start > 0 and post_start % W == 0:
            wend = today - pd.Timedelta(days=1)
            wstart = wend - pd.Timedelta(days=W)
            eff = max(wstart, intdate)
            return (not acct) or _activity_between(acct, eff, wend) == 0
        return False
    if yest not in _MILESTONES.get(cad, {}):
        return False
    milestone = _milestone_req(cad, yest)
    usage = _activity_to(acct, today - pd.Timedelta(days=1)) if acct else 0
    return usage < milestone

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _rng(df, col, s, e):
    if col not in df.columns:
        return df.iloc[0:0]
    try:
        c = pd.to_datetime(df[col], errors="coerce")
        m = c.notna() & (c >= s) & (c <= e)
        return df[m]
    except Exception:
        return df.iloc[0:0]

def _sel(v):
    """Normalise a multi-select filter value to a list of chosen options.
    Empty list / "All" / "" / None all mean 'no filter' (show everything).
    Accepts a scalar too, so it works during the transition from single-select."""
    if v is None or v == "All" or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        return [x for x in v if x not in (None, "All", "")]
    return [v]

# ── Custom multi-select dropdown (checkbox panel + summary label) ────────────
# A JS widget (see _MULTISELECT_SCRIPT) renders a checkbox dropdown for each
# filter and pushes the chosen list back through one shared hidden Taipy input
# (`ms_bridge`). Python keeps the real list var as the source of truth and feeds
# the widget a JSON blob ({lov, sel, label}) per filter via a hidden text holder.
def _ms_label(sel):
    n = len(sel)
    return "All" if n == 0 else (sel[0] if n == 1 else "Multiple Selections")

def _ms_json(lov, sel):
    # base64 so option text containing HTML-special chars (deal names like
    # "… <> AIA") survives raw-HTML rendering; the JS decodes it.
    s = _sel(sel)
    payload = json.dumps({"lov": list(lov), "sel": s, "label": _ms_label(s)})
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")

def _atp_amount(d, s, e):
    """ATP (DAX #Amount? ATP / cAmount?): for High-Intent records whose
    eta_pay_date falls in range, sum the max 'amount?' per record."""
    if "amount?" not in d.columns:
        return 0
    sub = _rng(d, "eta_pay_date", s, e)
    sub = sub[sub["deal_stage"] == "High Intent"]
    if len(sub) == 0:
        return 0
    amt = pd.to_numeric(sub["amount?"], errors="coerce")
    return int(amt.groupby(sub["record_id"]).max().sum())

def _atp_amount_va(d, s, e):
    """ATP for VA (DAX cohAmount?): SUMX(VALUES(record_id), MAX(amount?))
    where deal_stage = 'High Intent' AND eta_pay_date in [s, e]."""
    if "amount?" not in d.columns:
        return 0
    sub = _rng(d, "eta_pay_date", s, e)
    if "deal_stage" in sub.columns:
        sub = sub[sub["deal_stage"] == "High Intent"]
    if len(sub) == 0:
        return 0
    return int(pd.to_numeric(sub["amount?"], errors="coerce")
               .groupby(sub["record_id"]).max().sum())

def _fmt(v):
    v = int(v)
    if v >= 1_00_000: return f"₹{v/1_00_000:.1f}L"
    if v >= 1000:     return f"₹{v//1000}K"
    return f"₹{v}"

def _fmtn(v):
    v = int(v)
    if v >= 1_00_000: return f"{v/1_00_000:.1f}L"
    if v >= 1000:     return f"{v//1000}K"
    return str(v)

def _streak(email, account_id, upl, syn, days=28):
    today = pd.Timestamp(date.today())
    dots = []
    for i in range(days):
        d = today - pd.Timedelta(days=i)
        if pd.isna(account_id):
            dots.append("⚪"); continue
        u = ((upl["account_id"]==account_id) & (upl["date"]==d)).any() if len(upl) else False
        s = ((syn["account_id"]==account_id) & (syn["event_date"]==d)).any() if len(syn) else False
        dots.append("🟢" if (u or s) else "⚪")
    return "".join(dots)

def _customer_status(row, upl, syn):
    if pd.isna(row.get("integration_done_date")) or pd.isna(row.get("login_email_id")):
        return None
    email = _clean_email(row["login_email_id"])
    cadence = row.get("cadence","Monthly")
    days_since = row.get("days_since_int", 0)
    window = {"Daily":4,"Weekly":7,"Bi weekly":10,"Monthly":14}.get(cadence, 7)
    past_initial = {"Daily":days_since>15,"Weekly":days_since>20,"Bi weekly":days_since>25,"Monthly":days_since>29}.get(cadence, False)
    if not past_initial: return "Active"
    today = pd.Timestamp(date.today())
    acct_u = upl[upl["email"]==email]["account_id"].dropna()
    acct_s = syn[syn["email"]==email]["account_id"].dropna()
    account_id = acct_u.iloc[0] if len(acct_u) else (acct_s.iloc[0] if len(acct_s) else None)
    if account_id is None: return "Inactive"
    missed = 0
    for i in range(3):
        ws = today - pd.Timedelta(days=(i+1)*window)
        we = today - pd.Timedelta(days=i*window)
        u = len(upl[(upl["account_id"]==account_id)&(upl["date"]>=ws)&(upl["date"]<we)])
        s = len(syn[(syn["account_id"]==account_id)&(syn["event_date"]>=ws)&(syn["event_date"]<we)])
        if u+s == 0: missed += 1
    if missed >= 3: return "Inactive"
    if missed >= 2: return "Risk of Churn"
    return "Active"

# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — AIA OPS
# ═══════════════════════════════════════════════════════════════════

def _aia_ops_refresh(state):
    s = pd.Timestamp(state.aia_start_date)
    e = pd.Timestamp(state.aia_end_date)
    df = _AIA.copy()
    _o = _sel(state.aia_selected_owner)
    if _o:    df = df[df["deal_owner"].isin(_o)]
    _c = _sel(state.aia_selected_campaign)
    if _c:    df = df[df["utm_campaign"].isin(_c)]
    df_allchan = df  # before channel cross-filter — the pie always shows every channel
    if state.aia_channel_filter != "All" and "deal_source_group" in df.columns:
        df = df[df["deal_source_group"]==state.aia_channel_filter]
    state.aia_filter_label = (f"Channel: {state.aia_channel_filter}  (click pie again or Show All to clear)"
                              if state.aia_channel_filter != "All" else "")

    state.aia_kpi_leads       = _rng(df,"create_date",s,e)["record_id"].nunique()
    state.aia_kpi_ds          = _rng(df,"ds_date",s,e)["record_id"].nunique()
    state.aia_kpi_dc          = _rng(df,"dc_date",s,e)["record_id"].nunique()
    hi = _rng(df,"eta_pay_date",s,e)
    state.aia_kpi_hi          = hi[hi["deal_stage"]=="High Intent"]["record_id"].nunique()
    pd_                       = _rng(df,"payment_date",s,e)
    state.aia_kpi_aia_paid    = pd_[pd_["module_type"]=="AIA Paid"]["record_id"].nunique()
    state.aia_kpi_gst_paid    = pd_[pd_["module_type"]=="GST Paid"]["record_id"].nunique()
    if "asked_refund" in pd_.columns:
        state.aia_kpi_paid    = pd_[pd_["asked_refund"] != "Yes"]["record_id"].nunique()
    else:
        state.aia_kpi_paid    = pd_["record_id"].nunique()
    rd_ = _rng(df, "churned_date", s, e)
    state.aia_kpi_refunds = rd_[rd_["asked_refund"] == "Yes"]["record_id"].nunique() if "asked_refund" in rd_.columns else 0
    state.aia_kpi_parked      = _rng(df,"parked_date",s,e)["record_id"].nunique()
    state.aia_kpi_discards    = _rng(df,"discard_date",s,e)["record_id"].nunique()
    state.aia_kpi_closed_lost = _rng(df,"closed_lost_date",s,e)["record_id"].nunique()
    state.aia_kpi_collected   = _fmt(int(pd_.groupby("record_id")["amount_paid"].max().sum()))
    cycle_map = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}
    if "asked_refund" in pd_.columns:
        mrr_df = pd_[pd_["asked_refund"] != "Yes"].copy()
    else:
        mrr_df = pd_.copy()
    mrr_df["_m"] = mrr_df["billing_cycle"].map(cycle_map)
    state.aia_kpi_mrr = _fmt(int((mrr_df["amount_paid"]/mrr_df["_m"].fillna(1)).sum()))

    # Funnel
    coh   = _rng(df,"create_date",s,e)
    leads = coh["record_id"].nunique()
    ds_n  = coh[coh["ds_date"].notna()&(coh["ds_date"]>=s)&(coh["ds_date"]<=e)]["record_id"].nunique()
    dc_n  = coh[coh["dc_date"].notna()&(coh["dc_date"]>=s)&(coh["dc_date"]<=e)]["record_id"].nunique()
    hi2_mask = (coh["eta_pay_date"].notna()&(coh["eta_pay_date"]>=s)&(coh["eta_pay_date"]<=e)
                &(coh["deal_stage"]=="High Intent")&coh["payment_date"].isna()&coh["parked_date"].isna())
    hi2   = coh[hi2_mask]["record_id"].nunique()
    paid2 = coh[coh["payment_date"].notna()&(coh["payment_date"]>=s)&(coh["payment_date"]<=e)]["record_id"].nunique()
    p = lambda n: f"{n/leads*100:.0f}%" if leads else "0%"
    _labels = [f"<b>{leads}</b>", f"<b>{ds_n} ({p(ds_n)})</b>", f"<b>{dc_n} ({p(dc_n)})</b>",
               f"<b>{hi2} ({p(hi2)})</b>", f"<b>{paid2} ({p(paid2)})</b>"]
    state.aia_funnel_fig = _make_funnel(
        ["Leads", "DS", "DC", "High Intent", "Paid"],
        [leads, ds_n, dc_n, hi2, paid2], _labels)

    # DC vs Qualified trend — bars (DC) + line (Qualified), capped at today
    e_cap  = min(e, pd.Timestamp(date.today()))
    dc_sub = _rng(df,"dc_date",s,e_cap).copy()
    dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    daily_q  = dc_sub[dc_sub["prospect_score"]>=60].groupby("date")["record_id"].nunique().reset_index(name="Qualified")
    trend = pd.DataFrame({"date": pd.date_range(s, e_cap, freq="D")})
    trend = trend.merge(daily_dc,on="date",how="left").merge(daily_q,on="date",how="left").fillna(0)
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    trend = trend.astype({"DC":int,"Qualified":int})
    state.aia_trend_fig = _make_trend(trend["date_label"].tolist(),
                                      trend["DC"].tolist(), trend["Qualified"].tolist())

    # Channel pie — always from the channel-unfiltered frame, sorted desc
    ch = _rng(df_allchan,"create_date",s,e).groupby("deal_source_group")["record_id"].nunique().reset_index()
    ch.columns = ["Channel","Count"]
    ch = ch.sort_values("Count", ascending=False, ignore_index=True)
    state.aia_channel_order = ch["Channel"].astype(str).tolist()
    state.aia_channel_pie_json = pie_payload_b64(ch, "Channel", "Count")

    # GM table
    rows = []
    for owner in sorted(df["deal_owner"].dropna().unique()):
        o   = df[df["deal_owner"]==owner]
        l   = _rng(o,"create_date",s,e)["record_id"].nunique()
        if l == 0: continue
        pd2 = _rng(o,"payment_date",s,e)
        li_sub = _AIA_LI[_AIA_LI["record_id"].isin(pd2["record_id"])
                          &(_AIA_LI["date_paid"]>=s)&(_AIA_LI["date_paid"]<=e)]
        new_li = li_sub[li_sub["recurring_type"]=="New"] if "recurring_type" in li_sub.columns and len(li_sub[li_sub["recurring_type"]=="New"]) else li_sub
        paid_no_refund = pd2[pd2["asked_refund"] != "Yes"] if "asked_refund" in pd2.columns else pd2
        rows.append({
            "GM":         owner,
            "Leads":      l,
            "DC":         _rng(o,"dc_date",s,e)["record_id"].nunique(),
            "HI":         _rng(o,"eta_pay_date",s,e).query("deal_stage=='High Intent'")["record_id"].nunique(),
            "AIA Paid":   pd2[pd2["module_type"]=="AIA Paid"]["record_id"].nunique(),
            "GST Paid":   pd2[pd2["module_type"]=="GST Paid"]["record_id"].nunique(),
            "Active PS60":_rng(o,"dc_date",s,e).query("prospect_score>=60 and deal_stage in ['Demo Conducted','High Intent']")["record_id"].nunique(),
            "Tot Paid":   pd2[pd2["module_type"].isin(["AIA Paid","GST Paid"])]["record_id"].nunique(),
            "Revenue":    int(pd2.groupby("record_id")["amount_paid"].max().sum()),
            "MRR":        int(new_li["mrr"].sum()) if len(new_li) else 0,
            "ATP":        _atp_amount(o, s, e),
        })
    gm = pd.DataFrame(rows)
    if len(gm):
        tot = gm.select_dtypes("number").sum().to_dict(); tot["GM"] = "Total"
        gm = pd.concat([gm, pd.DataFrame([tot])], ignore_index=True)
    state.aia_gm_json = grid_payload_b64(gm, "GM", bar_cols=["HI", "ATP"], fixed=True)

    # UTM cohort
    rows2 = []
    _utm_src = coh["utm_source_cohort"].fillna("(Blank)")
    for src in sorted(_utm_src.unique()):
        c  = coh[_utm_src==src]
        l2 = c["record_id"].nunique()
        if l2 == 0: continue
        pd3 = c[c["payment_date"].notna()&(c["payment_date"]>=s)&(c["payment_date"]<=e)]
        # cActive PS >= 60: PS>=60, demo/HI stage, dc in same month as create
        aps = c[(c["prospect_score"]>=60)
                & (c["deal_stage"].isin(["Demo Conducted","High Intent"]))
                & (c["dc_date"].notna())
                & (c["create_date"].dt.year==c["dc_date"].dt.year)
                & (c["create_date"].dt.month==c["dc_date"].dt.month)]["record_id"].nunique()
        # MRR: line items of records paid in-range, unit_price / billing-frequency
        mrr_u = int(_AIA_LI[_AIA_LI["record_id"].isin(pd3["record_id"])]["mrr"].sum())
        rows2.append({
            "UTM Source": src,
            "Leads": l2,
            "DC":    c[c["dc_date"].notna()&(c["dc_date"]>=s)&(c["dc_date"]<=e)]["record_id"].nunique(),
            "HI":    c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
            "AIA Paid": pd3[pd3["module_type"]=="AIA Paid"]["record_id"].nunique(),
            "GST Paid": pd3[pd3["module_type"]=="GST Paid"]["record_id"].nunique(),
            "Active PS60": aps,
            "Tot Paid": pd3[pd3["module_type"].isin(["AIA Paid","GST Paid"])]["record_id"].nunique(),
            "Revenue":  int(pd3.groupby("record_id")["amount_paid"].max().sum()),
            "MRR":      mrr_u,
            "ATP":      _atp_amount(c, s, e),
        })
    utm = pd.DataFrame(rows2)
    if len(utm):
        tot2 = utm.select_dtypes("number").sum().to_dict(); tot2["UTM Source"] = "Total"
        utm = pd.concat([utm, pd.DataFrame([tot2])], ignore_index=True)
    state.aia_utm_json = grid_payload_b64(utm, "UTM Source", bar_cols=["HI", "ATP"], fixed=True)

    # Reason tables
    def _reason(date_col, label, rcol):
        sub = _rng(df, date_col, s, e)
        if rcol not in sub.columns: return pd.DataFrame(columns=["Reason", label])
        r = sub.groupby(rcol)["record_id"].nunique().reset_index()
        r.columns = ["Reason", label]
        return r.sort_values(label, ascending=False).reset_index(drop=True)
    state.aia_discard_df = _reason("discard_date",    "Discards",    "aia_discard_reason")
    state.aia_lost_df    = _reason("closed_lost_date", "Closed Lost", "aia_lost_reason")
    state.aia_parked_df  = _reason("parked_date",      "Parked",      "aia_parked_reason")

    # ── Incentive Tracker ────────────────────────────────────────────
    _INC_COLS = ["GM","Gap (Prev Month)","AIA+VA Revenue","Combined MRR",
                 "Base Target","Adjusted Target","Achievement %","Incentive Tier","Incentive Payout"]
    if len(_INCENTIVE_TARGETS) == 0:
        state.aia_incentive_json = grid_payload_b64(pd.DataFrame())
    else:
        m_start  = pd.Timestamp(state.aia_start_date).replace(day=1)
        m_end    = (m_start + relativedelta(months=1)) - pd.Timedelta(days=1)
        pm_start = m_start - relativedelta(months=1)
        pm_end   = m_start - pd.Timedelta(days=1)
        curr_t   = _INCENTIVE_TARGETS[_INCENTIVE_TARGETS["month"] == m_start]
        prev_t   = _INCENTIVE_TARGETS[_INCENTIVE_TARGETS["month"] == pm_start]
        if len(curr_t) == 0:
            state.aia_incentive_json = grid_payload_b64(pd.DataFrame())
        else:
            aia_c = _rng(_AIA, "payment_date", m_start, m_end)
            va_c  = _rng(_VA,  "payment_date", m_start, m_end)
            aia_p = _rng(_AIA, "payment_date", pm_start, pm_end)
            va_p  = _rng(_VA,  "payment_date", pm_start, pm_end)
            inc_rows = []
            for _, tr in curr_t.iterrows():
                gm        = tr["gm_combined"]
                base_tgt  = int(tr["monthly_mrr_target"])
                carry_fwd = bool(tr["is_gap_carry_forwarded"])
                prev_tr   = prev_t[prev_t["gm_combined"] == gm]
                prev_tgt  = int(prev_tr["monthly_mrr_target"].iloc[0]) if len(prev_tr) else 0
                aia_pg    = aia_p[aia_p["deal_owner"] == gm]
                va_pg     = va_p[va_p["deal_owner"] == gm]
                prev_rev  = (aia_pg.groupby("record_id")["amount_paid"].max().sum()
                             + va_pg["amount_paid"].sum()
                             + (va_pg["ot_amount_paid"].sum() if "ot_amount_paid" in va_pg.columns else 0))
                gap = (max(0, prev_tgt * 0.70 - prev_rev)
                       if carry_fwd and prev_tgt > 0 and prev_rev < prev_tgt * 0.70 else 0)
                adj_tgt   = base_tgt + gap
                aia_cg    = aia_c[aia_c["deal_owner"] == gm]
                va_cg     = va_c[va_c["deal_owner"] == gm]
                aia_rev   = aia_cg.groupby("record_id")["amount_paid"].max().sum() if len(aia_cg) else 0
                va_rev    = (va_cg["amount_paid"].sum()
                             + (va_cg["ot_amount_paid"].sum() if "ot_amount_paid" in va_cg.columns else 0))
                total_rev = aia_rev + va_rev
                aia_ids   = aia_cg[aia_cg["module_type"] == "AIA Paid"]["record_id"].unique()
                gst_ids   = aia_cg[aia_cg["module_type"] == "GST Paid"]["record_id"].unique()
                va_ids    = va_cg["record_id"].unique()
                aia_li_n  = _AIA_LI[_AIA_LI["record_id"].isin(aia_ids) & (_AIA_LI["recurring_type"] == "New")] if "recurring_type" in _AIA_LI.columns else _AIA_LI.iloc[0:0]
                gst_li_n  = _AIA_LI[_AIA_LI["record_id"].isin(gst_ids) & (_AIA_LI["recurring_type"] == "New")] if "recurring_type" in _AIA_LI.columns else _AIA_LI.iloc[0:0]
                va_li_n   = _VA_LI[_VA_LI["record_id"].isin(va_ids) & (_VA_LI["recurring_type"] == "New")] if "recurring_type" in _VA_LI.columns else _VA_LI.iloc[0:0]
                comb_mrr  = (aia_li_n["mrr"].sum() + gst_li_n["mrr"].sum()
                             + ((va_li_n["unit_price"] / va_li_n["term"].replace(0,1).fillna(1)).sum() if len(va_li_n) else 0))
                ach = total_rev / adj_tgt if adj_tgt > 0 else 0
                if base_tgt == 0:    tier = "No Target Set"
                elif total_rev == 0: tier = "No Revenue"
                elif ach < 0.70:     tier = "Under (<70%)"
                elif ach <= 1.30:    tier = "Base (70-130%)"
                else:                tier = "Accelerated (>130%)"
                mult   = 1.3 if ach > 1.30 else (1.0 if ach >= 0.70 else 0)
                rate_r = 0.39 if ach > 1.30 else (0.30 if ach >= 0.70 else 0)
                aia_inc = (sum(r["mrr"] * mult if r["billing_frequency"] == "annually" else r["mrr"] * rate_r
                               for _, r in aia_li_n.iterrows()) if mult > 0 and len(aia_li_n) else 0)
                gst_inc = gst_li_n["mrr"].sum() * mult if len(gst_li_n) else 0
                va_inc  = (sum((r["unit_price"] / max(float(r["term"] or 1), 1)) *
                               (mult if float(r["term"] or 1) == 12 else rate_r)
                               for _, r in va_li_n.iterrows()) if mult > 0 and len(va_li_n) else 0)
                inc_rows.append({
                    "GM":               gm,
                    "Gap (Prev Month)": int(gap),
                    "AIA+VA Revenue":   int(total_rev),
                    "Combined MRR":     int(comb_mrr),
                    "Base Target":      base_tgt,
                    "Adjusted Target":  int(adj_tgt),
                    "Achievement %":    f"{ach*100:.1f}%",
                    "Incentive Tier":   tier,
                    "Incentive Payout": int(round(aia_inc + gst_inc + va_inc)),
                })
            if inc_rows:
                inc_df = pd.DataFrame(inc_rows).sort_values("Incentive Payout", ascending=False).reset_index(drop=True)
                tot_row = {"GM":"Total","Gap (Prev Month)":inc_df["Gap (Prev Month)"].sum(),
                           "AIA+VA Revenue":inc_df["AIA+VA Revenue"].sum(),
                           "Combined MRR":inc_df["Combined MRR"].sum(),
                           "Base Target":inc_df["Base Target"].sum(),
                           "Adjusted Target":inc_df["Adjusted Target"].sum(),
                           "Achievement %":"","Incentive Tier":"",
                           "Incentive Payout":inc_df["Incentive Payout"].sum()}
                inc_df = pd.concat([inc_df, pd.DataFrame([tot_row])], ignore_index=True)
                state.aia_incentive_json = grid_payload_b64(
                    inc_df, "GM", sort_default_col="Incentive Payout",
                    center_cols=["Achievement %", "Incentive Tier"],
                    bar_cols=["Gap (Prev Month)", "Incentive Payout"],
                    bar_color={"Gap (Prev Month)": "#f1a0a0", "Incentive Payout": "#c5e07a"},
                    heat_cols={"AIA+VA Revenue": "green"}, autosize=True)
            else:
                state.aia_incentive_json = grid_payload_b64(pd.DataFrame())

# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — CS & FINANCE
# ═══════════════════════════════════════════════════════════════════

def _apply_usage_filter(state):
    """Filter the Customer Usage & Health grid by the Deal Name / CSM dropdowns."""
    d = state.cs_usage_all
    if d is None or len(d) == 0:
        state.cs_usage_json = grid_payload_b64(pd.DataFrame())
        return
    _d = _sel(state.cs_usage_deal)
    if _d:
        d = d[d["Deal Name"].isin(_d)]
    _m = _sel(state.cs_usage_csm)
    if _m:
        d = d[d["CSM"].isin(_m)]
    _st = _sel(state.cs_usage_stage)
    if _st:
        d = d[d["Stage"].isin(_st)]
    state.cs_usage_json = grid_payload_b64(
        d, sort_default_col="Usage Active Days (28d)",
        streak_cols=["Usage Streak Last 28D (desc)"], status_cols=["Status"],
        center_cols=["Paid On", "Int Date", "Due On", "Cadence", "Status"],
        heat_cols={"Usage Active Days (28d)": "green"},
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})

def _cs_refresh(state):
    s = pd.Timestamp(state.cs_start_date)
    e = pd.Timestamp(state.cs_end_date)
    df = _AIA.copy()
    _co = _sel(state.cs_selected_owner)
    if _co: df = df[df["cs_owner"].isin(_co)]
    _cd = _sel(state.cs_selected_deal)
    if _cd:
        # Deal Name list comes from line items; map back to the deals' records so
        # the whole page (incl. the line-item Revenue/Retention matrices) filters.
        _cd_rids = set(_AIA_LI[_AIA_LI["deal_name"].isin(_cd)]["record_id"].dropna())
        df = df[df["record_id"].isin(_cd_rids)]
    today = pd.Timestamp(date.today())
    paid_all = df[df["payment_date"].notna()]

    state.cs_kpi_paid_all = paid_all["record_id"].nunique()
    state.cs_kpi_aia_paid = paid_all[paid_all["module_type"]=="AIA Paid"]["record_id"].nunique()
    state.cs_kpi_refunds  = df[df["asked_refund"] == "Yes"]["record_id"].nunique() if "asked_refund" in df.columns else 0

    def _next_renewal(row):
        base = row.get("renewed_date") if pd.notna(row.get("renewed_date")) else row.get("payment_date")
        if pd.isna(base): return pd.NaT
        m = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}.get(row.get("billing_cycle",""))
        return base + relativedelta(months=m) if m else pd.NaT

    excl = ["Churned","CS Parked","Blocked","Integration Failed"]
    paid_active = paid_all[~paid_all["deal_stage"].isin(excl)].copy()
    paid_active["next_renewal"] = paid_active.apply(_next_renewal, axis=1)
    state.cs_kpi_overdue = paid_active[paid_active["next_renewal"]<today]["record_id"].nunique()
    state.cs_kpi_due_7d  = paid_active[
        (paid_active["next_renewal"]>=today-pd.Timedelta(days=7))
        &(paid_active["next_renewal"]<=today+pd.Timedelta(days=7))]["record_id"].nunique()

    # #Integration Due (DAX): AIA Paid, paid in range, not activated/adopted,
    # and not in a terminal/done stage. (No integration_done_date requirement.)
    _excl_id = ["Churned","CS Parked","Blocked","Integration Failed","Integration Done"]
    intd = _rng(df, "payment_date", s, e)
    intd = intd[(intd["module_type"]=="AIA Paid")
                & (intd["activation_date"].isna())
                & (intd["adopted_date"].isna())
                & (~intd["deal_stage"].isin(_excl_id))]
    state.cs_kpi_int_due = intd["record_id"].nunique()

    # Customer-usage table still needs the integration-done base set
    # health_base: all module types with non-blank email + intd, matches _idrfr (module_type.notna())
    # and PBI's Customer_Status_measure which has no module_type filter.
    health_base = df[(df["integration_done_date"].notna())
                     & (df["login_email_id"].notna())
                     & (df["login_email_id"].astype(str).str.strip() != "")
                     & (df["module_type"].notna())]
    # int_done: AIA Paid only — used for usage table and cs_kpi_active
    int_done = df[(df["integration_done_date"].notna())
                  & (df["login_email_id"].notna())
                  & (df["login_email_id"].astype(str).str.strip() != "")
                  & (df["module_type"]=="AIA Paid")]

    renewed_sub          = _rng(df, "renewed_date", s, e)
    state.cs_kpi_renewed = renewed_sub[renewed_sub["module_type"]=="AIA Paid"]["record_id"].nunique()
    state.cs_kpi_blocked = paid_all[paid_all["deal_stage"]=="Blocked"]["record_id"].nunique()
    state.cs_kpi_rfr     = paid_all[paid_all["deal_stage"]=="Ready for Renewal"]["record_id"].nunique()

    if "due_on" in _AIA_LI.columns:
        active_li = _AIA_LI[_AIA_LI["due_on"]>=today]
    else:
        active_li = _AIA_LI
    state.cs_kpi_mrr    = _fmt(int(active_li["unit_price"].sum()))

    int_customers = int_done[~int_done["deal_stage"].isin(["Churned","CS Parked"])].copy()
    statuses = int_customers.apply(lambda r: _customer_status(r, _UPL, _SYN), axis=1)
    state.cs_kpi_active = int((statuses=="Active").sum())

    # Revenue + Retention matrices — refunds-adjusted billing-to-MRR breakdown
    # (DAX total_monthly_collection / #Active Paid Users) with Fresh Renewals
    # and Total rows. YYYY-MM labels, blank zeros, chronological order.
    _refund_map = None
    if "asked_refund" in _AIA.columns:
        _refund_map = (_AIA.dropna(subset=["record_id"]).drop_duplicates("record_id")
                           .set_index("record_id")["asked_refund"])
    # Matrices come from line items; restrict them to the filtered deals/owners
    # (the CS Owner / Deal Name dropdowns) via record_id so the filter reaches here.
    _li_cs = _AIA_LI
    if _co or _cd:
        _li_cs = _AIA_LI[_AIA_LI["record_id"].isin(df["record_id"])]
    _rev_m = _mrr_matrix(_li_cs, _refund_map, "revenue")
    _ret_m = _mrr_matrix(_li_cs, _refund_map, "retention")
    _rev_heat = {c: "green" for c in _rev_m.columns if c != "Cohort"} if len(_rev_m) else {}
    _ret_heat = {c: "green" for c in _ret_m.columns if c != "Cohort"} if len(_ret_m) else {}
    state.cs_revenue_matrix_json = (grid_payload_b64(_rev_m, total_id_col="Cohort",
                                    blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                    autosize=True, heat_cols=_rev_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                    heat_by_row=True)
                                    if len(_rev_m) else grid_payload_b64(pd.DataFrame()))
    state.cs_retention_matrix_json = (grid_payload_b64(_ret_m, total_id_col="Cohort",
                                      blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                      autosize=True, heat_cols=_ret_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                      heat_by_row=True)
                                      if len(_ret_m) else grid_payload_b64(pd.DataFrame()))

    # ── Three stacked CSM Performance tables ────────────────────────────────
    def _idrfr(sub):
        bc = sub["billing_cycle"] if "billing_cycle" in sub.columns else pd.Series("", index=sub.index)
        mod = sub["module_type"].notna()
        integ  = sub[mod & (sub["deal_stage"]=="Integration Done")]["record_id"].nunique()
        rfr    = sub[mod & (sub["deal_stage"]=="Ready for Renewal") & (bc=="Monthly")]["record_id"].nunique()
        allren = sub[mod & ((sub["deal_stage"]=="Renewal Done")
                            | ((sub["deal_stage"]=="Ready for Renewal") & (bc!="Monthly")))]["record_id"].nunique()
        return int(integ + rfr + allren)

    # per-customer health — one row per deal, matching PBI COUNTROWS (not deduplicated by email)
    # Uses health_base (all module types, not just AIA Paid) to match PBI Customer_Status_measure
    today_n = pd.Timestamp(date.today()).normalize()
    hrows = []
    for _, row in health_base.iterrows():
        em = _clean_email(row.get("login_email_id",""))
        if not em:
            continue
        ac = _acct_for(em)
        intd = row.get("integration_done_date")
        if pd.isna(intd):
            continue
        intd = pd.Timestamp(intd).normalize()
        dsince = (today_n - intd).days
        cad = _cadence_of(row)
        hrows.append({
            "cs_owner": row.get("cs_owner"),
            "stage": row.get("deal_stage"),
            # dedup key for the engagement "Active" measure (PBI DISTINCTCOUNT of
            # the AIA account — two deals sharing one account count once)
            "akey": ac if ac else ("em:" + em),
            "a7":  1 if _activity_between(ac, today_n-pd.Timedelta(days=6),  today_n) else 0,
            "a14": 1 if _activity_between(ac, today_n-pd.Timedelta(days=13), today_n) else 0,
            "a21": 1 if _activity_between(ac, today_n-pd.Timedelta(days=20), today_n) else 0,
            "a28": 1 if _activity_between(ac, today_n-pd.Timedelta(days=27), today_n) else 0,
            "status": _customer_status_m(ac, intd, cad, dsince, today_n),
            "fy":  1 if _flagged_yesterday(ac, intd, cad, dsince, today_n) else 0,
            "tf":  _total_flags_30d(ac, intd, cad, today_n),
        })
    hdf = pd.DataFrame(hrows)
    # Health metrics consider only these stages
    _HEALTH_STAGES = ["Integration Done", "Ready for Renewal", "Renewal Done"]
    hdf_health = hdf[hdf["stage"].isin(_HEALTH_STAGES)] if len(hdf) else hdf

    _excl_uc = ["Churned","CS Parked","Blocked","Integration Failed","Integration Done"]
    t1_rows, t2_rows, t3_rows = [], [], []
    for csm in sorted(df["cs_owner"].dropna().unique()):
        c   = df[df["cs_owner"]==csm]
        cp  = c[c["payment_date"].notna() & (c["module_type"]=="AIA Paid")]
        mod = c[c["module_type"].notna()]
        int_due    = c[(c["module_type"]=="AIA Paid") & c["payment_date"].notna()
                       & c["activation_date"].isna() & c["adopted_date"].isna()
                       & ~c["deal_stage"].isin(_excl_uc)]["record_id"].nunique()
        int_failed = mod[mod["deal_stage"]=="Integration Failed"]["record_id"].nunique()
        integrated = mod[mod["deal_stage"]=="Integration Done"]["record_id"].nunique()
        t1_rows.append({
            "CSM":       csm,
            "AIA Paid":  cp["record_id"].nunique(),
            "Under CS":  int(int_due + int_failed + integrated),   # DAX: ID + IF + Integrated
            "Int Due":   int(int_due),
            "Int Failed":int(int_failed),
            "Integrated":int(integrated),
            "Blocked":   cp[cp["deal_stage"]=="Blocked"]["record_id"].nunique(),
            "Ready for Renewal": c[
                (c["deal_stage"]=="Ready for Renewal") &
                (c["billing_cycle"]=="Monthly" if "billing_cycle" in c.columns else False) &
                c["module_type"].notna()
            ]["record_id"].nunique(),
            "Paid/Renewed": c[
                (c["module_type"]=="AIA Paid") &
                ((c["deal_stage"]=="Renewal Done") |
                 ((c["deal_stage"]=="Ready for Renewal") &
                  (c["billing_cycle"]!="Monthly" if "billing_cycle" in c.columns else True)))
            ]["record_id"].nunique(),
            "CS Parked": cp[cp["deal_stage"]=="CS Parked"]["record_id"].nunique(),
            "Churned":   c[c["deal_stage"]=="Churned"]["record_id"].nunique(),
        })
        idr = _idrfr(c)
        h  = hdf[hdf["cs_owner"]==csm] if len(hdf) else hdf
        hh = hdf_health[hdf_health["cs_owner"]==csm] if len(hdf_health) else hdf_health
        # Active counts: DISTINCTCOUNT of account (PBI) — dedupe deals sharing one account
        def _act_n(col):
            return int(hh[hh[col] == 1]["akey"].nunique()) if len(hh) else 0
        t2_rows.append({
            "CSM": csm, "ID + RFR + Renewed": idr,
            "Active Last 7d":  _act_n("a7"),
            "Active Last 14d": _act_n("a14"),
            "Active Last 21d": _act_n("a21"),
            "Active Last 28d": _act_n("a28"),
        })
        t3_rows.append({
            "CSM": csm, "ID + RFR + Renewed": idr,
            "Red Flags Yesterday":  int(hh["fy"].sum()) if len(hh) else 0,
            "Last 30d Total Flags": int(hh["tf"].sum()) if len(hh) else 0,
            "Active Customers":        int((hh["status"]=="Active").sum()) if len(hh) else 0,
            "Risk of Churn Customers": int((hh["status"]=="Risk of Churn").sum()) if len(hh) else 0,
            "Inactive Customers":      int((hh["status"]=="Inactive").sum()) if len(hh) else 0,
        })

    def _with_total(rows, idcol):
        d = pd.DataFrame(rows)
        if len(d):
            tot = d.select_dtypes("number").sum().to_dict(); tot[idcol] = "Total"
            d = pd.concat([d, pd.DataFrame([tot])], ignore_index=True)
        return d

    state.cs_csm_aia_json = grid_payload_b64(
        _with_total(t1_rows, "CSM"), total_id_col="CSM", sort_default_col="AIA Paid",
        blank_zeros=True, bar_cols=["Int Due"], bar_color="#f4a98c", autosize=True)
    state.cs_csm_eng_json = grid_payload_b64(
        _with_total(t2_rows, "CSM"), total_id_col="CSM", sort_default_col="ID + RFR + Renewed",
        blank_zeros=True, autosize=True)
    state.cs_csm_health_json = grid_payload_b64(
        _with_total(t3_rows, "CSM"), total_id_col="CSM", sort_default_col="ID + RFR + Renewed",
        blank_zeros=True, bar_cols=["Red Flags Yesterday"], bar_color="#f1a0a0", autosize=True)

    # Customer Usage Cohort (last 10 integration weeks) — counts + % tables.
    # Both use fixed column widths so they line up as a comparison; % values
    # (strings) are centre-aligned.
    cnt_df, pct_df = _usage_cohort()
    _coh_heat = {f"W{o}": "green" for o in range(10)}
    state.cs_cohort_count_json = (grid_payload_b64(cnt_df, total_id_col="Integration Week",
                                  blank_zeros=True, no_sort=True, fixed=True,
                                  sortable=False, center_all=True, heat_cols=_coh_heat)
                                  if len(cnt_df) else grid_payload_b64(pd.DataFrame()))
    state.cs_cohort_pct_json   = (grid_payload_b64(pct_df, total_id_col="Integration Week",
                                  no_sort=True, fixed=True, sortable=False, center_all=True,
                                  heat_cols=_coh_heat)
                                  if len(pct_df) else grid_payload_b64(pd.DataFrame()))

    # Usage & Health table — every record with a non-blank payment_date (PBI rule:
    # no integration / module-type / email filter). Paid-but-not-yet-integrated and
    # GST-Paid records show too, with blank Int Date and 0 usage.
    usage_base = df[df["payment_date"].notna()]
    usage_rows = []
    for _, row in usage_base.iterrows():
        email  = _clean_email(row.get("login_email_id",""))
        active_days, streak = _usage_28(email)
        intd = row.get("integration_done_date")
        dsince = (today.normalize() - pd.Timestamp(intd).normalize()).days if pd.notna(intd) else 0
        cad = _cadence_of(row)
        _ddmy = lambda v: pd.Timestamp(v).strftime("%d-%b-%y") if pd.notna(v) else ""
        usage_rows.append({
            "Deal Name":       row.get("deal_name",""),
            "record_id":       row.get("record_id",""),
            "CSM":             row.get("cs_owner",""),
            "Stage":           row.get("deal_stage",""),
            "Paid On":         _ddmy(row.get("payment_date")),
            "Int Date":        _ddmy(row.get("integration_done_date")),
            "Due On":          _due_on(row.get("record_id")),
            "Cadence":         cad,
            "Usage Active Days (28d)": active_days,
            "Usage Streak Last 28D (desc)": streak,
            # Status is blank when not yet integrated (no days-since basis), matching PBI
            "Status": (_customer_status_m(_acct_for(email),
                        pd.Timestamp(intd).normalize(), cad, dsince, today.normalize()) or "")
                      if pd.notna(intd) else "",
        })
    usage_all = pd.DataFrame(usage_rows)
    state.cs_usage_all = usage_all
    state.cs_usage_deal_list = (sorted(usage_all["Deal Name"].dropna().unique().tolist())
                                if len(usage_all) else [])
    state.cs_usage_csm_list  = (sorted(usage_all["CSM"].dropna().unique().tolist())
                                if len(usage_all) else [])
    state.cs_usage_stage_list = (sorted(usage_all["Stage"].dropna().unique().tolist())
                                 if len(usage_all) else [])
    _apply_usage_filter(state)

    # Renewal window ±14d — only Ready for Renewal / Renewal Done; Due On =
    # billing end date, shown first in dd-MMM-yy.
    rw = df[df["deal_stage"].isin(["Ready for Renewal", "Renewal Done"])].copy()
    rw["_due"] = pd.to_datetime(rw["record_id"].map(_BILLING_END), errors="coerce")
    rw = rw[(rw["_due"] >= today - pd.Timedelta(days=14))
            & (rw["_due"] <= today + pd.Timedelta(days=14))].sort_values("_due")
    rwd = pd.DataFrame({
        "Due On":    rw["_due"].dt.strftime("%d-%b-%y"),
        "Deal Name": rw.get("deal_name", ""),
        "record_id": rw["record_id"].values,
        "CSM":       rw.get("cs_owner", ""),
        "POC":       rw.get("poc_number", ""),
        "Email":     rw.get("poc_email", ""),
        "Stage":     rw.get("deal_stage", ""),
        "Amount":    rw.get("amount_paid", 0),
    })
    state.cs_renewal_window_json = (grid_payload_b64(
        rwd, no_sort=True, center_cols=["Due On", "Amount"], autosize=True,
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})
        if len(rwd) else grid_payload_b64(pd.DataFrame()))

# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — MARKETING
# ═══════════════════════════════════════════════════════════════════

def _mkt_refresh(state):
    s = pd.Timestamp(state.mkt_start_date)
    e = pd.Timestamp(state.mkt_end_date)
    mkt_all = _MKT[(_MKT["day"]>=s)&(_MKT["day"]<=e)] if "day" in _MKT.columns else _MKT

    # channel cross-filter (set by clicking a pie). Spend filters _MKT.channel,
    # leads/conversions filter _AIA.deal_source_group with the same label.
    cf = state.mkt_channel_filter
    state.mkt_filter_label = (f"Channel: {cf}  (click pie again or Show All to clear)"
                              if cf != "All" else "")
    mkt = mkt_all
    aia_base = _AIA
    if cf != "All":
        if "channel" in mkt.columns:
            mkt = mkt[mkt["channel"] == cf]
        if "deal_source_group" in _AIA.columns:
            aia_base = _AIA[_AIA["deal_source_group"] == cf]

    total_spend  = int(mkt["cost"].sum()) if "cost" in mkt.columns else 0
    state.mkt_kpi_spend = _fmt(total_spend)

    aia_sub      = _rng(aia_base,"create_date",s,e)
    total_leads  = aia_sub["record_id"].nunique()
    state.mkt_kpi_leads = _fmtn(total_leads)

    paid_sub = _rng(aia_base,"payment_date",s,e)
    if "asked_refund" in paid_sub.columns:
        paid_ch = paid_sub[paid_sub["asked_refund"] != "Yes"]["record_id"].nunique()
    else:
        paid_ch = paid_sub["record_id"].nunique()

    state.mkt_kpi_cpl = _fmt(total_spend//total_leads) if total_leads else "₹0"
    state.mkt_kpi_cac = _fmt(total_spend//paid_ch)     if paid_ch    else "₹0"

    li_paid   = _AIA_LI[_AIA_LI["record_id"].isin(paid_sub["record_id"])
                         &(_AIA_LI["date_paid"]>=s)&(_AIA_LI["date_paid"]<=e)]
    if "recurring_type" in li_paid.columns:
        new_li = li_paid[li_paid["recurring_type"]=="New"]
        if len(new_li) == 0: new_li = li_paid
    else:
        new_li = li_paid
    total_mrr = int(new_li["mrr"].sum()) if len(new_li) else 0
    state.mkt_kpi_arpu    = _fmt(total_mrr//paid_ch) if paid_ch else "₹0"
    cac_v  = total_spend//paid_ch if paid_ch else 0
    arpu_v = total_mrr//paid_ch   if paid_ch else 0
    state.mkt_kpi_payback = f"{round(cac_v/arpu_v)} mo" if arpu_v else "—"

    _mkt_full = _MKT[_MKT["channel"]==cf] if (cf!="All" and "channel" in _MKT.columns) else _MKT
    li_full = _AIA_LI
    if cf != "All" and "deal_source_group" in _AIA.columns:
        li_full = _AIA_LI[_AIA_LI["record_id"].isin(aia_base["record_id"])]
    _heat_mkt = {"MRR": "green", "ARPU": "green", "CAC": "red"}

    # Monthly Performance — all months, expanding, with Total
    mdf = _mkt_breakdown(_mkt_full, aia_base, li_full, "M", "Month",
                         lambda p: p.strftime("%b %y"), drop_zero_spend=True)
    state.mkt_monthly_json = (grid_payload_b64(mdf, total_id_col="Month", no_sort=True,
                              sortable=False, center_all=True, bar_cols=["Spend (₹)"],
                              bar_color="#7fb3e0", heat_cols=_heat_mkt, autosize=True)
                              if len(mdf) else grid_payload_b64(pd.DataFrame()))

    # Weekly Breakdown — same structure, by week
    wdf = _mkt_breakdown(_mkt_full, aia_base, li_full, "W", "Week",
                         lambda p: p.start_time.strftime("%d %b %y"), last_n=8)
    state.mkt_weekly_json = (grid_payload_b64(wdf, total_id_col="Week", no_sort=True,
                             sortable=False, center_all=True, bar_cols=["Spend (₹)"],
                             bar_color="#7fb3e0", heat_cols=_heat_mkt, autosize=True)
                             if len(wdf) else grid_payload_b64(pd.DataFrame()))

    # charts (trend) — derive from the monthly breakdown
    if len(mdf):
        chart = mdf[mdf["Month"] != "Total"].rename(columns={"Spend (₹)": "Spend"})
        state.mkt_spend_df = chart[["Month","Spend","Leads"]].rename(columns={"Month":"YearMonth"})
        state.mkt_cpl_df   = chart[["Month","CPL","CAC"]].rename(columns={"Month":"YearMonth"})
    else:
        state.mkt_spend_df = pd.DataFrame(); state.mkt_cpl_df = pd.DataFrame()

    # Channel pies — always show ALL channels (from the channel-unfiltered data)
    # so a different slice can be clicked.
    if "channel" in mkt_all.columns and len(mkt_all):
        cs = mkt_all.groupby("channel")["cost"].sum().reset_index(); cs.columns=["Channel","Spend"]
        cs = cs.sort_values("Spend", ascending=False, ignore_index=True)
        state.mkt_channel_spend_json = pie_payload_b64(cs, "Channel", "Spend")
    else:
        state.mkt_channel_spend_json = pie_payload_b64(pd.DataFrame())

    cl = _rng(_AIA,"create_date",s,e).groupby("deal_source_group")["record_id"].nunique().reset_index()
    cl.columns = ["Channel","Leads"]
    cl = cl.sort_values("Leads", ascending=False, ignore_index=True)
    state.mkt_channel_leads_json = pie_payload_b64(cl, "Channel", "Leads")

# ═══════════════════════════════════════════════════════════════════
# PAGE 4 — VA OPS
# ═══════════════════════════════════════════════════════════════════

def _va_ops_refresh(state):
    s = pd.Timestamp(state.va_start_date)
    e = pd.Timestamp(state.va_end_date)
    df = _VA.copy()
    _o = _sel(state.va_selected_owner)
    if _o:    df = df[df["deal_owner"].isin(_o)]
    _c = _sel(state.va_selected_campaign)
    if _c:    df = df[df["utm_campaign"].isin(_c)]
    df_allchan = df  # before channel cross-filter — the pie always shows every channel
    if state.va_channel_filter != "All" and "deal_source_group" in df.columns:
        df = df[df["deal_source_group"]==state.va_channel_filter]
    state.va_filter_label = (f"Channel: {state.va_channel_filter}  (click pie again or Show All to clear)"
                             if state.va_channel_filter != "All" else "")

    state.va_kpi_leads       = _rng(df,"create_date",s,e)["record_id"].nunique()
    state.va_kpi_ds          = _rng(df,"ds_date",s,e)["record_id"].nunique()
    state.va_kpi_dc          = _rng(df,"dc_date",s,e)["record_id"].nunique()
    hi = _rng(df,"eta_pay_date",s,e)
    state.va_kpi_hi          = hi[hi["deal_stage"]=="High Intent"]["record_id"].nunique()
    pd_                      = _rng(df,"payment_date",s,e)
    state.va_kpi_paid        = pd_["record_id"].nunique()
    state.va_kpi_discards    = _rng(df,"discard_date",s,e)["record_id"].nunique()
    state.va_kpi_parked      = _rng(df,"parked_date",s,e)["record_id"].nunique()
    state.va_kpi_closed_lost = _rng(df,"closed_lost_date",s,e)["record_id"].nunique()
    rev = int(pd_["amount_paid"].sum()) + int(pd_["ot_amount_paid"].sum())
    state.va_kpi_revenue     = _fmt(rev)
    cycle_map = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}
    mrr_df = pd_.copy(); mrr_df["_m"] = mrr_df["billing_cycle"].map(cycle_map)
    state.va_kpi_mrr = _fmt(int((mrr_df["amount_paid"]/mrr_df["_m"].fillna(1)).sum()))
    today = pd.Timestamp(date.today())
    eom = df[(df["eta_pay_date"].notna())
             &(df["eta_pay_date"]>=today.replace(day=1))
             &(df["eta_pay_date"]<=today+pd.offsets.MonthEnd(0))
             &(df["payment_date"].isna())]
    state.va_kpi_eom = str(state.va_kpi_paid + len(eom))

    coh   = _rng(df,"create_date",s,e)
    leads = coh["record_id"].nunique()
    ds2  = coh[coh["ds_date"].notna()&(coh["ds_date"]>=s)&(coh["ds_date"]<=e)]["record_id"].nunique()
    dc2  = coh[coh["dc_date"].notna()&(coh["dc_date"]>=s)&(coh["dc_date"]<=e)]["record_id"].nunique()
    hi2  = coh[coh["eta_pay_date"].notna()&(coh["eta_pay_date"]>=s)&(coh["eta_pay_date"]<=e)&(coh["deal_stage"]=="High Intent")]["record_id"].nunique()
    paid2= coh[coh["payment_date"].notna()&(coh["payment_date"]>=s)&(coh["payment_date"]<=e)]["record_id"].nunique()
    p = lambda n: f"{n/leads*100:.0f}%" if leads else "0%"
    _vlabels = [f"<b>{leads}</b>", f"<b>{ds2} ({p(ds2)})</b>", f"<b>{dc2} ({p(dc2)})</b>",
                f"<b>{hi2} ({p(hi2)})</b>", f"<b>{paid2} ({p(paid2)})</b>"]
    state.va_funnel_fig = _make_funnel(
        ["Leads", "DS", "DC", "Agreed", "Paid"],
        [leads, ds2, dc2, hi2, paid2], _vlabels)

    e_cap = min(e, pd.Timestamp(date.today()))     # cap trend at today, like AIA Ops
    dc_sub = _rng(df,"dc_date",s,e_cap).copy(); dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    trend = pd.DataFrame({"date":pd.date_range(s,e_cap,freq="D")}).merge(daily_dc,on="date",how="left").fillna(0)
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    state.va_trend_df = trend.astype({"DC":int})

    ch = _rng(df_allchan,"create_date",s,e).groupby("deal_source_group")["record_id"].nunique().reset_index()
    ch.columns = ["Channel","Count"]
    ch = ch.sort_values("Count", ascending=False, ignore_index=True)
    state.va_channel_pie_json = pie_payload_b64(ch, "Channel", "Count")

    rows = []
    for owner in sorted(df["deal_owner"].dropna().unique()):
        o = df[df["deal_owner"]==owner]
        l = _rng(o,"create_date",s,e)["record_id"].nunique()
        if l==0: continue
        pd2 = _rng(o,"payment_date",s,e)
        rows.append({"GM":owner,"Leads":l,
            "DC":_rng(o,"dc_date",s,e)["record_id"].nunique(),
            "HI":_rng(o,"eta_pay_date",s,e).query("deal_stage=='High Intent'")["record_id"].nunique(),
            "Paid":pd2["record_id"].nunique(),
            "Revenue":int(pd2["amount_paid"].sum()+pd2["ot_amount_paid"].sum()),
            "MRR":_va_mrr(pd2["record_id"]),
            "ATP":_atp_amount_va(o, s, e)})
    va_gm = pd.DataFrame(rows)
    if len(va_gm):
        tot = va_gm.select_dtypes("number").sum().to_dict(); tot["GM"]="Total"
        va_gm = pd.concat([va_gm, pd.DataFrame([tot])], ignore_index=True)
    state.va_gm_json = grid_payload_b64(va_gm, "GM", bar_cols=["HI", "ATP"],
                                        fixed=True, autosize=True, first_col_w=250)

    rows2 = []
    _utm_src = coh["utm_source_cohort"].fillna("(Blank)")
    for src in sorted(_utm_src.unique()):
        c = coh[_utm_src==src]; l2 = c["record_id"].nunique()
        if l2==0: continue
        # DAX cohPaid: BOTH create_date AND payment_date must be in [s, e]
        coh_paid = c[c["payment_date"].notna()&(c["payment_date"]>=s)&(c["payment_date"]<=e)]
        rows2.append({"UTM":src,"Leads":l2,
            "DC":c[c["dc_date"].notna()&(c["dc_date"]>=s)&(c["dc_date"]<=e)]["record_id"].nunique(),
            "HI":c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
            "Paid":coh_paid["record_id"].nunique(),
            "Revenue":int(coh_paid["amount_paid"].sum()+coh_paid.get("ot_amount_paid", pd.Series(0, index=coh_paid.index)).sum()),
            "MRR":_va_mrr(coh_paid["record_id"]),
            "ATP":_atp_amount_va(c, s, e)})
    va_utm = pd.DataFrame(rows2)
    if len(va_utm):
        tot2 = va_utm.select_dtypes("number").sum().to_dict(); tot2["UTM"]="Total"
        va_utm = pd.concat([va_utm, pd.DataFrame([tot2])], ignore_index=True)
    state.va_utm_json = grid_payload_b64(va_utm, "UTM", bar_cols=["HI", "ATP"],
                                         fixed=True, autosize=True, first_col_w=250)

    def _rv(col,label,rcol):
        sub = _rng(df,col,s,e)
        if rcol not in sub.columns: return pd.DataFrame(columns=["Reason",label])
        r = sub.groupby(rcol)["record_id"].nunique().reset_index(); r.columns=["Reason",label]
        return r.sort_values(label,ascending=False).reset_index(drop=True)
    state.va_discard_df = _rv("discard_date","Discards","va_discard_reason")
    state.va_lost_df    = _rv("closed_lost_date","Lost","va_lost_reason")
    state.va_parked_df  = _rv("parked_date","Parked","va_parked_reason")

# ═══════════════════════════════════════════════════════════════════
# PAGE 5 — VA FINANCE
# ═══════════════════════════════════════════════════════════════════

def _vaf_refresh(state):
    today = pd.Timestamp(date.today())
    df = _VA.copy()
    li = _VA_LI.copy()
    # Deal Name + Line Item Name filters
    _vd = _sel(state.vaf_selected_deal)
    if _vd:
        # Deal Name list comes from the line-item table (the matrix source)
        li = li[li["deal_name"].isin(_vd)]
        df = df[df["record_id"].isin(li["record_id"])]
    _vli = _sel(state.vaf_selected_line_item)
    if _vli and "line_item_name" in li.columns:
        li = li[li["line_item_name"].isin(_vli)]
        df = df[df["record_id"].isin(li["record_id"])]

    paid = df[df["payment_date"].notna()]
    state.vaf_kpi_active  = paid[~paid["deal_stage"].isin(["Churned"])]["record_id"].nunique()
    state.vaf_kpi_revenue = _fmt(int(paid["amount_paid"].sum()+paid["ot_amount_paid"].sum()))
    cycle_map = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}
    mrr_df = paid.copy(); mrr_df["_m"] = mrr_df["billing_cycle"].map(cycle_map)
    state.vaf_kpi_mrr = _fmt(int((mrr_df["amount_paid"]/mrr_df["_m"].fillna(1)).sum()))

    # Build due_on map from line items — max due_on per record_id (matches PBI)
    va_due_map = {}
    if "due_on" in li.columns and "record_id" in li.columns:
        va_due_map = (li.dropna(subset=["record_id","due_on"])
                        .groupby("record_id")["due_on"].max().to_dict())

    def _next_va(row):
        rid = row.get("record_id")
        if rid in va_due_map:
            return va_due_map[rid]
        base = row.get("renewed_date") if pd.notna(row.get("renewed_date")) else row.get("payment_date")
        if pd.isna(base): return pd.NaT
        m = cycle_map.get(row.get("billing_cycle",""))
        return base + relativedelta(months=m) if m else pd.NaT
    paid2 = paid.copy(); paid2["next_renewal"] = paid2.apply(_next_va, axis=1)
    state.vaf_kpi_due_14d = paid2[
        (paid2["next_renewal"]>=today-pd.Timedelta(days=14))
        &(paid2["next_renewal"]<=today+pd.Timedelta(days=14))]["record_id"].nunique()

    _vrev = _mrr_matrix(li, None, "revenue", add_onetime=True)   # VA: + One-time row
    _vret = _mrr_matrix(li, None, "retention", add_onetime=True)
    _vrev_heat = {c: "green" for c in _vrev.columns if c != "Cohort"} if len(_vrev) else {}
    _vret_heat = {c: "green" for c in _vret.columns if c != "Cohort"} if len(_vret) else {}
    state.vaf_revenue_matrix_json   = (grid_payload_b64(_vrev, total_id_col="Cohort",
                                       blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                       autosize=True, heat_cols=_vrev_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                       heat_by_row=True)
                                       if len(_vrev) else grid_payload_b64(pd.DataFrame()))
    state.vaf_retention_matrix_json = (grid_payload_b64(_vret, total_id_col="Cohort",
                                       blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                       autosize=True, heat_cols=_vret_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                       heat_by_row=True)
                                       if len(_vret) else grid_payload_b64(pd.DataFrame()))

    if len(li) > 0:
        li3 = li.dropna(subset=["date_paid"]).copy()
        li3["BillingMonth"] = li3["date_paid"].dt.to_period("M").astype(str)
        t = li3.groupby("BillingMonth")["unit_price"].sum().reset_index(); t.columns=["BillingMonth","Revenue"]
        state.vaf_revenue_trend_df = t.sort_values("BillingMonth").reset_index(drop=True)
    else:
        state.vaf_revenue_trend_df = pd.DataFrame()

    rw = paid2[(paid2["next_renewal"]>=today-pd.Timedelta(days=14))
               &(paid2["next_renewal"]<=today+pd.Timedelta(days=14))].sort_values("next_renewal")
    svc_map = {}
    if "line_item_name" in li.columns:
        svc_map = (li.dropna(subset=["line_item_name"])
                     .groupby("record_id")["line_item_name"]
                     .apply(lambda x: ", ".join(sorted(set(x.astype(str))))).to_dict())
    rwd = pd.DataFrame({
        "Due On":    rw["next_renewal"].dt.strftime("%d-%b-%y"),
        "Deal Name": rw.get("deal_name", ""),
        "record_id": rw["record_id"].values,
        "VA Service Name": rw["record_id"].map(svc_map).fillna(""),
        "POC Number": rw["poc_number"] if "poc_number" in rw.columns else pd.Series("", index=rw.index),
        "POC Email": rw.get("poc_email", ""),
        "Stage":     rw.get("deal_stage", ""),
        "Amount":    rw.get("amount_paid", 0),
    })
    state.vaf_renewal_json = (grid_payload_b64(rwd, no_sort=True,
                              center_cols=["Due On", "Amount"], autosize=True,
                              link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})
                              if len(rwd) else grid_payload_b64(pd.DataFrame()))

# ═══════════════════════════════════════════════════════════════════
# STATE VARIABLES
# ═══════════════════════════════════════════════════════════════════

import calendar as _calendar
_today       = date.today()
_month_start = date(_today.year, _today.month, 1)
_month_end   = date(_today.year, _today.month,
                    _calendar.monthrange(_today.year, _today.month)[1])
# earliest lead (create_date) across AIA + VA — lower bound for the date filters
_lead_dates  = pd.concat([_AIA.get("create_date", pd.Series(dtype="datetime64[ns]")),
                          _VA.get("create_date", pd.Series(dtype="datetime64[ns]"))]).dropna()
_lead_min    = _lead_dates.min().date() if len(_lead_dates) else date(2024, 12, 1)

# Page 1
aia_start_date = _month_start;  aia_end_date = _month_end
aia_owner_list    = sorted(_AIA["deal_owner"].dropna().unique().tolist())
aia_campaign_list = sorted(_AIA["utm_campaign"].dropna().unique().tolist())
aia_selected_owner = [];  aia_selected_campaign = []
aia_kpi_leads=0; aia_kpi_ds=0; aia_kpi_dc=0; aia_kpi_hi=0
aia_kpi_aia_paid=0; aia_kpi_gst_paid=0; aia_kpi_paid=0; aia_kpi_refunds=0
aia_kpi_parked=0; aia_kpi_discards=0; aia_kpi_closed_lost=0
aia_kpi_collected="₹0"; aia_kpi_mrr="₹0"
aia_funnel_fig = go.Figure()
aia_trend_fig = go.Figure()
aia_channel_pie_json = ""
aia_channel_filter = "All"; aia_channel_order = []; aia_filter_label = ""
aia_channel_click = ""; aia_channel_click_last = ""
aia_gm_json=""; aia_utm_json=""; aia_incentive_json=""
aia_discard_df=pd.DataFrame(); aia_lost_df=pd.DataFrame(); aia_parked_df=pd.DataFrame()

# Page 2
cs_start_date = date(2020,1,1);  cs_end_date = _today   # no date filter on CS page (all-time)
cs_owner_list = sorted(_AIA["cs_owner"].dropna().unique().tolist())
cs_deal_list  = sorted(_AIA_LI["deal_name"].dropna().unique().tolist())  # from line items (matrix source)
cs_selected_owner=[]; cs_selected_deal=[]
cs_kpi_paid_all=0; cs_kpi_overdue=0; cs_kpi_due_7d=0; cs_kpi_int_due=0
cs_kpi_renewed=0; cs_kpi_refunds=0; cs_kpi_blocked=0; cs_kpi_rfr=0
cs_kpi_aia_paid=0; cs_kpi_mrr="₹0"; cs_kpi_active=0
cs_revenue_matrix_json=""; cs_retention_matrix_json=""; cs_csm_aia_json=""
cs_csm_eng_json=""; cs_csm_health_json=""
cs_cohort_count_json=""; cs_cohort_pct_json=""; cs_usage_json=""
cs_usage_all=pd.DataFrame(); cs_usage_deal=[]; cs_usage_csm=[]; cs_usage_stage=[]
cs_usage_deal_list=[]; cs_usage_csm_list=[]; cs_usage_stage_list=[]
cs_renewal_window_json=""

# Page 3
mkt_start_date = date(2020,1,1); mkt_end_date = _today   # no date filter on Marketing page (all-time)
mkt_deal_list = ["All"] + sorted(_AIA["deal_name"].dropna().unique().tolist()[:100])
mkt_line_item_list = (["All"] + sorted(_AIA_LI["line_item_name"].dropna().unique().tolist()[:100])
                      if "line_item_name" in _AIA_LI.columns else ["All"])
mkt_selected_deal="All"; mkt_selected_line_item="All"
mkt_kpi_spend="₹0"; mkt_kpi_leads="0"; mkt_kpi_cpl="₹0"; mkt_kpi_cac="₹0"
mkt_kpi_arpu="₹0"; mkt_kpi_payback="—"
mkt_monthly_json=""; mkt_weekly_json=""; mkt_spend_df=pd.DataFrame(); mkt_cpl_df=pd.DataFrame()
mkt_channel_spend_json=""; mkt_channel_leads_json=""
mkt_channel_filter="All"; mkt_filter_label=""
mkt_channel_click=""; mkt_channel_click_last=""; mkt_leads_click=""; mkt_leads_click_last=""

# Page 4
va_start_date = _month_start;  va_end_date = _month_end
va_owner_list    = sorted(_VA["deal_owner"].dropna().unique().tolist())
va_campaign_list = sorted(_VA["utm_campaign"].dropna().unique().tolist())
va_selected_owner=[]; va_selected_campaign=[]
va_kpi_leads=0; va_kpi_ds=0; va_kpi_dc=0; va_kpi_hi=0; va_kpi_paid=0
va_kpi_discards=0; va_kpi_parked=0; va_kpi_closed_lost=0
va_kpi_revenue="₹0"; va_kpi_mrr="₹0"; va_kpi_eom="0"
va_funnel_fig=go.Figure(); va_trend_df=pd.DataFrame(); va_channel_pie_json=""
va_channel_filter="All"; va_filter_label=""; va_channel_click=""; va_channel_click_last=""
va_gm_json=""; va_utm_json=""
va_discard_df=pd.DataFrame(); va_lost_df=pd.DataFrame(); va_parked_df=pd.DataFrame()

# Page 5
vaf_start_date = date(2020,1,1); vaf_end_date = _today   # no date filter (all-time)
vaf_deal_list = sorted(_VA_LI["deal_name"].dropna().unique().tolist())  # from line items (matrix source)
vaf_line_item_list = (sorted(_VA_LI["line_item_name"].dropna().unique().tolist())
                      if "line_item_name" in _VA_LI.columns else [])
vaf_selected_deal=[]; vaf_selected_line_item=[]
vaf_kpi_active=0; vaf_kpi_revenue="₹0"; vaf_kpi_mrr="₹0"; vaf_kpi_due_14d=0
vaf_revenue_matrix_json=""; vaf_retention_matrix_json=""
vaf_revenue_trend_df=pd.DataFrame(); vaf_renewal_json=""

# Custom multi-select dropdowns — one shared JS→Python bridge + a JSON holder
# ({lov, sel, label}) per filter that the JS checkbox dropdown renders from.
ms_bridge = ""; ms_bridge_last = ""
aia_owner_ms      = _ms_json(aia_owner_list,    [])
aia_campaign_ms   = _ms_json(aia_campaign_list, [])
va_owner_ms       = _ms_json(va_owner_list,     [])
va_campaign_ms    = _ms_json(va_campaign_list,  [])
cs_owner_ms       = _ms_json(cs_owner_list,     [])
cs_deal_ms        = _ms_json(cs_deal_list,      [])
cs_usage_deal_ms  = _ms_json([], [])
cs_usage_csm_ms   = _ms_json([], [])
cs_usage_stage_ms = _ms_json([], [])
vaf_deal_ms       = _ms_json(vaf_deal_list,      [])
vaf_line_item_ms  = _ms_json(vaf_line_item_list, [])

# ── Chart configs ──────────────────────────────────────────────────
chart_config = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d","select2d","autoScale2d",
                               "zoom2d","pan2d","zoomIn2d","zoomOut2d","resetScale2d"],
}

_bg = "rgba(0,0,0,0)"
_font = {"family":"Inter,sans-serif","size":12}

aia_funnel_layout = {
    "funnelmode": "stack",
    "margin": {"l": 95, "r": 95, "t": 20, "b": 20},
    "height": 340,
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Inter,sans-serif", "size": 13},
    "showlegend": False,
    "yaxis": {"side": "left", "automargin": True, "title": "",
              "tickfont": {"size": 13, "color": "#1a3a6b", "family": "Inter,sans-serif"}},
}
aia_trend_layout  = {"barmode":"group","margin":{"l":40,"r":20,"t":10,"b":70},
                     "height":320,"legend":{"orientation":"h","y":-0.28,"x":0},
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font,
                     "xaxis":{"tickangle":-45}}
aia_pie_layout    = {"margin":{"l":20,"r":20,"t":30,"b":60},"height":340,
                     "paper_bgcolor":_bg,"showlegend":True,
                     "legend":{"orientation":"h","y":-0.2,"x":0.05},"font":_font}


va_funnel_layout  = aia_funnel_layout
va_trend_layout   = {"margin":{"l":40,"r":20,"t":10,"b":60},"height":280,
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font,
                     "xaxis":{"title":""}}
va_pie_layout     = aia_pie_layout

mkt_trend_layout  = {"barmode":"group","margin":{"l":40,"r":20,"t":10,"b":60},
                     "height":300,"legend":{"orientation":"h","y":-0.3},
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font}
mkt_cpl_layout    = {"margin":{"l":40,"r":20,"t":10,"b":60},"height":300,
                     "legend":{"orientation":"h","y":-0.3},
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font}
mkt_pie_layout    = aia_pie_layout

vaf_trend_layout  = {"margin":{"l":40,"r":20,"t":10,"b":60},"height":300,
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font}

# ═══════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════


def on_aia_filter_change(state):
    # AIA Ops & VA Ops share Start Date, End Date and Deal Owner (Campaign stays independent).
    # Owner falls back to "All" if the selected owner isn't a VA Ops deal owner.
    state.va_start_date     = state.aia_start_date
    state.va_end_date       = state.aia_end_date
    state.va_selected_owner = [o for o in _sel(state.aia_selected_owner) if o in state.va_owner_list]
    _aia_ops_refresh(state)
    _va_ops_refresh(state)
def on_cs_filter_change(state):  _cs_refresh(state); _sync_ms(state)
def on_cs_usage_filter(state):   _apply_usage_filter(state); _sync_ms(state)
def on_mkt_filter_change(state): _mkt_refresh(state)
def on_va_filter_change(state):
    state.aia_start_date     = state.va_start_date
    state.aia_end_date       = state.va_end_date
    state.aia_selected_owner = [o for o in _sel(state.va_selected_owner) if o in state.aia_owner_list]
    _aia_ops_refresh(state)
    _va_ops_refresh(state)
def on_vaf_filter_change(state): _vaf_refresh(state); _sync_ms(state)

# ── Custom multi-select bridge ──────────────────────────────────────────────
# key -> (state var holding the chosen list, scope deciding which refresh runs)
_MS_DISPATCH = {
    "aia_owner":      ("aia_selected_owner",     "aia"),
    "aia_campaign":   ("aia_selected_campaign",  "aia"),
    "va_owner":       ("va_selected_owner",      "va"),
    "va_campaign":    ("va_selected_campaign",   "va"),
    "cs_owner":       ("cs_selected_owner",      "cs"),
    "cs_deal":        ("cs_selected_deal",       "cs"),
    "cs_usage_deal":  ("cs_usage_deal",          "usage"),
    "cs_usage_csm":   ("cs_usage_csm",           "usage"),
    "cs_usage_stage": ("cs_usage_stage",         "usage"),
    "vaf_deal":       ("vaf_selected_deal",      "vaf"),
    "vaf_line_item":  ("vaf_selected_line_item", "vaf"),
}

def _sync_ms(state):
    """Push each filter's {lov, sel, label} JSON to its hidden holder so the JS
    checkbox dropdowns reflect the current (possibly server-changed) selection."""
    state.aia_owner_ms      = _ms_json(aia_owner_list,    state.aia_selected_owner)
    state.aia_campaign_ms   = _ms_json(aia_campaign_list, state.aia_selected_campaign)
    state.va_owner_ms       = _ms_json(va_owner_list,     state.va_selected_owner)
    state.va_campaign_ms    = _ms_json(va_campaign_list,  state.va_selected_campaign)
    state.cs_owner_ms       = _ms_json(cs_owner_list,     state.cs_selected_owner)
    state.cs_deal_ms        = _ms_json(cs_deal_list,      state.cs_selected_deal)
    state.cs_usage_deal_ms  = _ms_json(state.cs_usage_deal_list,  state.cs_usage_deal)
    state.cs_usage_csm_ms   = _ms_json(state.cs_usage_csm_list,   state.cs_usage_csm)
    state.cs_usage_stage_ms = _ms_json(state.cs_usage_stage_list, state.cs_usage_stage)
    state.vaf_deal_ms       = _ms_json(vaf_deal_list,      state.vaf_selected_deal)
    state.vaf_line_item_ms  = _ms_json(vaf_line_item_list, state.vaf_selected_line_item)

def on_ms_change(state):
    """One shared handler for every custom multi-select. The JS writes
    '<key>|<json-list>||<counter>' into the hidden ms_bridge input."""
    raw = state.ms_bridge
    if not raw or raw == state.ms_bridge_last:
        return
    state.ms_bridge_last = raw
    try:
        payload, _ctr = raw.rsplit("||", 1)
        key, js = payload.split("|", 1)
        sel = json.loads(js)
        if not isinstance(sel, list):
            return
    except Exception:
        return
    if key not in _MS_DISPATCH:
        return
    var, scope = _MS_DISPATCH[key]
    setattr(state, var, sel)
    if scope == "aia":     on_aia_filter_change(state)
    elif scope == "va":    on_va_filter_change(state)
    elif scope == "cs":    _cs_refresh(state)
    elif scope == "usage": _apply_usage_filter(state)
    elif scope == "vaf":   _vaf_refresh(state)
    _sync_ms(state)


def _bridge_channel(raw):
    """The pie iframe writes 'Channel||<counter>' into a hidden input; strip the
    counter (which only exists so the value always changes and on_change fires)."""
    if not isinstance(raw, str) or not raw:
        return None
    return raw.split("||")[0].strip()

def on_aia_channel_click(state):
    raw = state.aia_channel_click
    if not raw or raw == state.aia_channel_click_last:
        return  # dedupe duplicate events fired for the same click
    state.aia_channel_click_last = raw
    ch = _bridge_channel(raw)
    if not ch:
        return
    state.aia_channel_filter = "All" if ch == state.aia_channel_filter else ch
    _aia_ops_refresh(state)

def on_aia_channel_reset(state):
    state.aia_channel_filter = "All"
    _aia_ops_refresh(state)

def on_va_channel_click(state):
    raw = state.va_channel_click
    if not raw or raw == state.va_channel_click_last:
        return
    state.va_channel_click_last = raw
    ch = _bridge_channel(raw)
    if not ch:
        return
    state.va_channel_filter = "All" if ch == state.va_channel_filter else ch
    _va_ops_refresh(state)

def on_va_channel_reset(state):
    state.va_channel_filter = "All"
    _va_ops_refresh(state)

def on_mkt_channel_click(state):
    raw = state.mkt_channel_click
    if not raw or raw == state.mkt_channel_click_last:
        return
    state.mkt_channel_click_last = raw
    ch = _bridge_channel(raw)
    if not ch:
        return
    state.mkt_channel_filter = "All" if ch == state.mkt_channel_filter else ch
    _mkt_refresh(state)

def on_mkt_leads_click(state):
    raw = state.mkt_leads_click
    if not raw or raw == state.mkt_leads_click_last:
        return
    state.mkt_leads_click_last = raw
    ch = _bridge_channel(raw)
    if not ch:
        return
    state.mkt_channel_filter = "All" if ch == state.mkt_channel_filter else ch
    _mkt_refresh(state)

def on_mkt_channel_reset(state):
    state.mkt_channel_filter = "All"
    _mkt_refresh(state)


def on_reset_filters(state, *_):
    """Alt+Shift+R — reset all page filters to month defaults."""
    today = date.today()
    ms = today.replace(day=1)
    me = (ms + relativedelta(months=1)) - timedelta(days=1)
    # AIA Ops
    state.aia_start_date     = ms;  state.aia_end_date     = me
    state.aia_selected_owner = []; state.aia_selected_campaign = []
    state.aia_channel_filter = "All"; state.aia_filter_label = ""
    # VA Ops
    state.va_start_date      = ms;  state.va_end_date      = me
    state.va_selected_owner  = []; state.va_selected_campaign = []
    state.va_channel_filter  = "All"; state.va_filter_label  = ""
    # CS Finance
    state.cs_selected_owner  = []; state.cs_selected_deal = []
    state.cs_usage_deal = []; state.cs_usage_csm = []; state.cs_usage_stage = []
    # Marketing
    state.mkt_channel_filter = "All"; state.mkt_filter_label = ""
    # VA Finance
    state.vaf_selected_deal  = []; state.vaf_selected_line_item = []
    _refresh_all(state)

def on_manual_refresh(state, *_):
    """Ctrl+Shift+5 — re-pull all data from the databases on demand and push the
    fresh data to every connected session (same effect as the scheduled
    auto-refresh, but immediate and not limited to 08:00–19:00)."""
    try:
        _reload_data()
        print(f"[manual-refresh] data reloaded at {datetime.now(_IST):%Y-%m-%d %H:%M:%S IST}")
    except Exception as ex:
        print(f"[manual-refresh] error: {ex}")
    _refresh_all(state)                                  # update the triggering session now
    try:
        gui.broadcast_callback(_broadcast_refresh)       # update all other open sessions
    except Exception:
        pass

def on_navigate(state, page_name, params):
    if page_name == "/":
        navigate(state, "aia")
    return page_name

def _refresh_all(state):
    state.last_synced = _fmt_sync()
    _aia_ops_refresh(state)
    _cs_refresh(state)
    _mkt_refresh(state)
    _va_ops_refresh(state)
    _vaf_refresh(state)
    _sync_ms(state)

def on_init(state):
    navigate(state, "aia")
    _refresh_all(state)

def _broadcast_refresh(state):
    """Re-run every page's compute for an already-connected client (no navigation)."""
    try:
        _refresh_all(state)
    except Exception:
        pass

def _auto_refresh_loop(gui):
    """Re-pull data and push it to all connected sessions on every :00 / :30 clock
    mark (IST), from 08:00 (first) through 19:00 (last) inclusive. These are absolute
    clock times and do not drift with the container start time; the startup data load
    is a separate, immediate refresh on top of this schedule."""
    while True:
        now = datetime.now(_IST)
        # sleep until the next half-hour clock boundary (:00 or :30)
        if now.minute < 30:
            nxt = now.replace(minute=30, second=0, microsecond=0)
        else:
            nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        _time.sleep(max(1, (nxt - now).total_seconds()))

        t = datetime.now(_IST)
        # window: 08:00 first .. 19:00 last (19:30+ and overnight are skipped)
        within = (8 <= t.hour <= 18) or (t.hour == 19 and t.minute == 0)
        if within:
            try:
                _reload_data()
                gui.broadcast_callback(_broadcast_refresh)
                print(f"[auto-refresh] data reloaded at {t:%Y-%m-%d %H:%M IST}")
            except Exception as ex:
                print(f"[auto-refresh] error: {ex}")

# ═══════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════

from pages.aia_ops    import AIA_OPS_PAGE
from pages.cs_finance import CS_FINANCE_PAGE
from pages.marketing  import MARKETING_PAGE
from pages.va_ops     import VA_OPS_PAGE
from pages.va_finance import VA_FINANCE_PAGE

ROOT_PAGE = """
<|↺|button|id=reset-filters-btn|on_action=on_reset_filters|class_name=hidden-reset|>
<|⟳|button|id=manual-refresh-btn|on_action=on_manual_refresh|class_name=hidden-reset|>
<|part|class_name=piebridge msbridge|
<|{ms_bridge}|input|on_change=on_ms_change|change_delay=0|>
|>
<|content|>
"""

nav_links = [
    ("/aia",        "AIA Ops"),
    ("/cs",         "CS & Finance"),
    ("/marketing",  "Marketing"),
    ("/va-ops",     "VA Ops"),
    ("/va-finance", "VA Finance"),
]

pages = {
    "/":          ROOT_PAGE,
    "aia":        AIA_OPS_PAGE,
    "cs":         CS_FINANCE_PAGE,
    "marketing":  MARKETING_PAGE,
    "va-ops":     VA_OPS_PAGE,
    "va-finance": VA_FINANCE_PAGE,
}

# ═══════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    gui = Gui(pages=pages, css_file="main.css", flask=flask_app)
    # background auto-refresh: every 30 min, 08:00–19:00 IST
    threading.Thread(target=_auto_refresh_loop, args=(gui,), daemon=True).start()
    gui.run(
        title="AiA + VA Dashboard",
        dark_mode=False,
        port=8080,
        host="0.0.0.0",
        on_init=on_init,
        on_navigate=on_navigate,
        use_reloader=False,
    )