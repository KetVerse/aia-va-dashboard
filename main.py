"""
AIA + VA Operations Dashboard — 5 Pages
Run: python main.py
"""
import os
import re
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
  function lbl(sel){ return sel.length===0 ? "All" : (sel.length===1 ? sel[0] : "Multiple Selections ("+sel.length+")"); }
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
      var willOpen = !open;
      msc.classList.toggle("open", willOpen);
      if(willOpen){
        // focus the search box immediately so typing works without an extra
        // click into it — deferred a tick so the panel is visible first
        // (focusing a still-hidden element is a silent no-op in some browsers).
        var s = msc.querySelector(".msc-search");
        if(s) setTimeout(function(){ s.focus(); }, 0);
      }
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
  function closeAllMsc(){
    document.querySelectorAll(".msc.open").forEach(function(o){ o.classList.remove("open"); });
  }
  document.addEventListener("click", closeAllMsc);
  // Clicks land inside the grid/pie IFRAMES (separate documents), so they never
  // bubble to this parent click handler. Focus leaving the parent window (which
  // happens the moment an iframe is clicked) fires 'blur' — use it to close any
  // open dropdown when the user clicks a chart or table.
  window.addEventListener("blur", closeAllMsc);
  var pending = false;
  function schedule(){ if(pending) return; pending = true; setTimeout(function(){ pending = false; scan(); }, 40); }
  // characterData:true is essential — the *_ms option holders start EMPTY and are
  // filled a moment later by on_init/_sync_ms (server push). Taipy often applies
  // that as an in-place text (characterData) update, which a childList-only
  // observer misses, leaving the dropdown stuck on "All" with no values.
  try{ new MutationObserver(schedule).observe(document.body, {childList:true, subtree:true, characterData:true}); }catch(e){}
  if(document.readyState !== "loading") scan();
  else document.addEventListener("DOMContentLoaded", scan);
  // Safety net against the load-time race: re-read the holders every second and
  // render as soon as they populate. scan() is a no-op unless a holder's data
  // actually changed and it skips any panel the user has open, so this is cheap
  // and non-disruptive. This is what makes the filters list reliably without a
  // rebuild + hard-refresh dance.
  setInterval(scan, 1000);
})();
</script>
"""

# ── Snapshot mode ───────────────────────────────────────────────────────────
# Loading any page with ?snapshot=1 flags the window so every grid iframe renders
# at full content height (no internal scroll) — used by the daily PDF renderer to
# capture all rows of every table. The grid iframes read window.parent.__SNAPSHOT__
# (same origin) and force their autosize path; see grid_server.py.
_SNAPSHOT_SCRIPT = """
<script id="snapshot-mode">
(function () {
  if (!/[?&]snapshot=1/.test(location.search)) return;
  window.__SNAPSHOT__ = true;
  document.documentElement.setAttribute('data-snapshot', '1');
})();
</script>
"""

# Adds a small "⧉" copy button to the top-right of every grid chart-card (the
# white title area, above the table — so it never overlaps the sticky header).
# The button appears only on card hover. On click it reads the grid iframe
# (same-origin) — header + all shown rows + the Total row — and copies it as
# tab-separated text so it pastes cleanly into Excel/Sheets. One script covers
# every grid; pies (src=/pie/) are skipped.
_COPYBTN_SCRIPT = """
<script id="grid-copy-btns">
(function(){
  // Read the rendered grid straight from the iframe DOM (same-origin). The
  // thead/tbody/tfoot only contain VISIBLE columns, so header/rows/total stay
  // aligned; we strip the sort-arrow (.arr) and priority (.pri) markers from
  // header cells. This avoids depending on the iframe's JS internals.
  function cells(tr, sel){
    return Array.prototype.map.call(tr.querySelectorAll(sel), function(c){
      var t=c.cloneNode(true);
      var junk=t.querySelectorAll(".arr, .pri");
      for(var i=0;i<junk.length;i++) junk[i].remove();
      return (t.textContent||"").replace(/\\s+/g," ").trim();
    });
  }
  function build(f){
    var doc;
    try{ doc=f.contentDocument; }catch(e){ return null; }
    if(!doc) return null;
    var head=doc.querySelector("#h tr");
    var brows=doc.querySelectorAll("#b tr");
    if(!head || !brows.length) return null;
    var lines=[cells(head,"th").join("\\t")];
    for(var i=0;i<brows.length;i++) lines.push(cells(brows[i],"td").join("\\t"));
    var ft=doc.querySelector("#f tr");
    if(ft) lines.push(cells(ft,"td").join("\\t"));
    return lines.join("\\n");
  }
  function fb(text,done){
    var ta=document.createElement("textarea"); ta.value=text;
    ta.style.position="fixed"; ta.style.left="-9999px"; document.body.appendChild(ta);
    ta.focus(); ta.select();
    try{ document.execCommand("copy"); }catch(e){}
    ta.remove(); if(done) done();
  }
  function copyText(text,btn){
    var done=function(){ if(btn){ btn.classList.add("copied");
      setTimeout(function(){ btn.classList.remove("copied"); },1200); } };
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(done).catch(function(){ fb(text,done); });
    } else { fb(text,done); }
  }
  function attach(){
    var frames=document.querySelectorAll("iframe.grid-frame");
    for(var i=0;i<frames.length;i++){
      var f=frames[i];
      if(f.__copyBtn) continue;
      if((f.getAttribute("src")||"").indexOf("/grid/")<0) continue;  // grids only, not pies
      var card=f.closest ? f.closest(".chart-card") : null;
      if(!card) continue;
      if(getComputedStyle(card).position==="static") card.style.position="relative";
      var b=document.createElement("button");
      b.textContent="\\u29C9";  // U+29C9 ⧉
      b.title="Copy table (header, all rows & Total) — paste into Excel/Sheets";
      b.className="grid-copy-btn";
      (function(frame,btn){
        btn.addEventListener("click", function(){ var t=build(frame); if(t) copyText(t,btn); });
      })(f,b);
      card.appendChild(b);
      f.__copyBtn=b;
    }
  }
  try{ new MutationObserver(function(){ attach(); }).observe(document.documentElement,{childList:true,subtree:true}); }catch(e){}
  if(document.readyState!=="loading") attach();
  else document.addEventListener("DOMContentLoaded", attach);
  setInterval(attach, 1500);
})();
</script>
"""

_DATERANGE_SCRIPT = """
<script id="daterange-autoend">
// Taipy's date_range is two independent single pickers (MUI's real range picker
// is a paid Pro component), so picking the start doesn't hand off to the end.
// This bridges that: after a day is chosen in the START calendar, auto-open the
// END calendar — giving the "click start → pick end" flow with the native control.
(function(){
  var advance = false;
  document.addEventListener("click", function(e){
    if(!e.target.closest) return;
    if(e.target.closest(".taipy-date-range-picker-start button")){ advance = true;  return; }
    if(e.target.closest(".taipy-date-range-picker-end button"))  { advance = false; return; }
    if(advance && e.target.closest(".MuiPickersDay-root")){
      advance = false;
      // The start-pick fires a server refresh that re-renders the picker; opening
      // the End calendar mid-refresh gets closed, and the MUI icon button TOGGLES
      // (so rapid re-clicks just flip it shut). Instead: wait for the DOM to go
      // quiet for 400ms (refresh settled), then click the End button exactly ONCE.
      var settle = null, cap = null;
      var obs = new MutationObserver(function(){ clearTimeout(settle); settle = setTimeout(fire, 400); });
      function fire(){
        clearTimeout(settle); clearTimeout(cap); obs.disconnect();
        var eb = document.querySelector(".taipy-date-range-picker-end button");
        if(eb) eb.click();
      }
      obs.observe(document.body, {childList: true, subtree: true});
      settle = setTimeout(fire, 700);   // in case no mutations fire at all
      cap    = setTimeout(fire, 4000);  // hard cap
    }
  }, true);
})();
</script>
"""

_CHART_KEYS_SCRIPT = """
<script id="trend-chart-keys">
// Trend charts: while hovering one, press 'z' = Zoom, 'v' = Pan, 'r' = Reset axes.
// Clicks the plotly modebar button for that plot (no global Plotly needed). Scoped
// to the trend plots (those with a DS bar trace) so other charts/inputs are safe.
(function(){
  var hovered = null;
  function isTrend(gd){
    try { return (gd.data||[]).some(function(t){ return t.name==='DS'; }); }
    catch(e){ return false; }
  }
  document.addEventListener('mouseover', function(e){
    var gd = e.target.closest ? e.target.closest('.js-plotly-plot') : null;
    if(gd && gd.data && isTrend(gd)) hovered = gd;
  });
  var MAP = {z:'Zoom', v:'Pan', r:'Reset axes'};
  document.addEventListener('keydown', function(e){
    if(!hovered) return;
    var tag = e.target && e.target.tagName;
    if(tag && /^(INPUT|TEXTAREA|SELECT)$/.test(tag)) return;   // don't hijack typing
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    var title = MAP[(e.key||'').toLowerCase()];
    if(!title) return;
    var btn = hovered.querySelector('.modebar a[data-title="'+title+'"]');
    if(btn){ btn.click(); e.preventDefault(); }
  });
})();
</script>
"""

_DC_LEGEND_SCRIPT = """
<script id="trend-anno-sync">
// The DC numbers and the Qualified value boxes are plotly ANNOTATIONS (layout-level), so
// unlike bar/line text they do NOT disappear when their series is unchecked in the legend.
// This keeps them in sync with the legend: hide the DC numbers when DC is off, hide the
// Qualified boxes when Qualified is off. DS labels are bar text, so they hide on their own.
// Also: DC numbers are WHITE so they read on the blue DS bar; when DS is off they sit on a
// white background, so flip them to dark. Annotations are classified by their text colour
// (navy = Qualified box; white/dark-orange = DC number). Scoped to trend plots (DS bar).
(function(){
  var DARK = '#9c4a0f', NAVY = 'rgb(31,78,121)';
  function isTrend(gd){ try { return (gd.data||[]).some(function(t){return t.name==='DS';}); } catch(e){ return false; } }
  function vis(gd, name){
    var t = (gd.data||[]).find(function(x){ return x.name===name; });
    if(!t) return 'absent';
    return (t.visible==='legendonly') ? 'off' : 'on';
  }
  function fillOf(t){
    var f = (t.style && t.style.fill) || t.getAttribute('fill') || '';
    return f.replace(/\\s+/g, '').toLowerCase();
  }
  function sync(gd){
    var dsOff = vis(gd,'DS')==='off', dcOff = vis(gd,'DC')==='off', qOff = vis(gd,'Qualified')==='off';
    gd.querySelectorAll('.infolayer text.annotation-text').forEach(function(t){
      var grp = (t.closest && t.closest('.annotation')) || t.parentNode;
      var f = fillOf(t);
      if(f === NAVY){                               // Qualified value box
        grp.style.display = qOff ? 'none' : '';
        return;
      }
      // otherwise a DC number (white, or dark-orange on DC>=DS days)
      if(dcOff){ grp.style.display = 'none'; return; }
      grp.style.display = '';
      if(dsOff && (f === 'rgb(255,255,255)' || f === '#ffffff')){   // on white bg -> dark
        t.style.fill = DARK;
        t.querySelectorAll('tspan').forEach(function(s){ s.style.fill = DARK; });
      }
    });
  }
  function bind(){
    document.querySelectorAll('.js-plotly-plot').forEach(function(gd){
      if(gd._trendSync || !isTrend(gd) || typeof gd.on !== 'function') return;
      gd._trendSync = true;
      gd.on('plotly_afterplot', function(){ sync(gd); });
      sync(gd);
    });
  }
  setInterval(bind, 1500); bind();
})();
</script>
"""

_DSIG_SCRIPT = """
<script id="dsig-render">
// The Marketing "Daily signals" panel is emitted as an HTML string through a Taipy
// text|mode=raw holder, which renders it HTML-ESCAPED (as visible text). This reads
// that decoded text (element.textContent unescapes the entities) and injects it as
// real innerHTML into a sibling render div. CSS hides the raw source (.taipy-text-raw)
// so it never flashes; a MutationObserver injects the instant Taipy drops the content
// in (and re-syncs after each data refresh), with a slow interval as a safety net.
(function(){
  function sync(){
    document.querySelectorAll('.dsig-holder').forEach(function(h){
      var raw = h.querySelector('.taipy-text-raw');
      if(!raw) return;
      var html = raw.textContent || '';
      var tgt = h.querySelector('.dsig-render-target');
      if(!tgt){
        tgt = document.createElement('div');
        tgt.className = 'dsig-render-target';
        raw.parentNode.insertBefore(tgt, raw);
      }
      if(tgt._lastHtml !== html){
        tgt.innerHTML = html;
        tgt._lastHtml = html;
      }
      raw.style.display = 'none';
    });
  }
  var pending = false;
  function schedule(){                        // coalesce mutation bursts into one sync
    if(pending) return;
    pending = true;
    (window.requestAnimationFrame || function(f){ setTimeout(f, 0); })(function(){
      pending = false; sync();
    });
  }
  function start(){
    if(document.body){
      new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
    }
    sync();
  }
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
  setInterval(sync, 1000);                    // safety net
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
                    _ZOOM_LOCK_SCRIPT + _PAGE_NAV_SCRIPT + _MULTISELECT_SCRIPT
                    + _SNAPSHOT_SCRIPT + _COPYBTN_SCRIPT + _DATERANGE_SCRIPT
                    + _CHART_KEYS_SCRIPT + _DC_LEGEND_SCRIPT + _DSIG_SCRIPT + "</body>"))
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


def _make_trend(labels, ds, dc, qual=None):
    """Overlay column + optional line (Power BI style): DS as blue bars in the BACK
    and DC as orange bars in FRONT, both on the 0 baseline (so DC reads as a portion
    of DS, not added to it); Qualified — when given — as a navy spline with boxed
    values. DS labels sit above each bar; DC numbers sit just above the orange bar,
    adaptively lifted so they never overlap the Qualified boxes. Bold labels + ticks."""
    xb = [f"<b>{l}</b>" for l in labels]   # slightly bold date ticks
    ds_c, dc_c, line_c = "#1a7fc4", "#ed7d31", "#1f4e79"   # DS blue, DC orange, line navy
    fig = go.Figure()
    # DS behind (full-height bar) — the only bars that carry value labels
    fig.add_bar(x=xb, y=ds, name="DS", marker_color=ds_c, marker_line_width=0,
                text=[f"<b>{v}</b>" if v else "" for v in ds], textposition="outside",
                textfont={"size": 10, "color": "#1a3a6b", "family": "Inter,sans-serif"},
                cliponaxis=False)
    # DC in front (drawn after DS -> on top), orange. Its value is a white number just
    # ABOVE the orange bar, adaptively lifted so it never overlaps the Qualified box
    # below it (Conducted >= Qualified); dark text on the rare DC >= DS day (sits on
    # white). White reads on the blue DS bar.
    fig.add_bar(x=xb, y=dc, name="DC", marker_color=dc_c, marker_line_width=0,
                cliponaxis=False)

    # Stack bottom -> top: Qualified box, DC number, DS label — never overlapping. Use
    # an estimate of px-per-data-unit to lift the DC number clear of the Qualified box.
    _qs = list(qual) if qual is not None else [0] * len(dc)
    _ymax = max([v for v in list(ds) + list(dc) + _qs if v] + [1])
    _ppu = 240.0 / (_ymax * 1.12)          # ~plot-area px per unit (h360 - t30 - b90)
    anns = []
    for i, (x, d) in enumerate(zip(xb, dc)):
        if not d:
            continue
        q   = _qs[i] if i < len(_qs) else 0
        dsv = ds[i]  if i < len(ds)  else 0
        dc_ys = 11
        if q:                               # lift so the DC number clears the Qual box
            _need = 32 - (d - q) * _ppu
            if _need > dc_ys:
                dc_ys = _need
        _col = "#ffffff" if d < dsv else "#9c4a0f"
        anns.append(dict(x=x, y=d, text=f"<b>{d}</b>", showarrow=False, yshift=round(dc_ys),
                         font=dict(size=10, color=_col, family="Inter,sans-serif")))
    if qual is not None:
        fig.add_scatter(x=xb, y=qual, name="Qualified", mode="lines+markers",
                        line={"color": line_c, "width": 3, "shape": "spline"},
                        marker={"size": 7, "color": line_c})
        # soft rounded label boxes for the LINE points only
        for x, q in zip(xb, qual):
            if q:
                anns.append(dict(x=x, y=q, text=f"<b>{q}</b>", showarrow=False, yshift=13,
                                 bgcolor="#e6edf6", bordercolor="#9fb6d4", borderpad=3,
                                 font=dict(size=10, color=line_c, family="Inter,sans-serif")))
    fig.update_layout(
        barmode="overlay", height=360, annotations=anns, dragmode="pan",
        margin={"l": 40, "r": 20, "t": 30, "b": 90},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter,sans-serif", "size": 12},
        legend={"orientation": "h", "y": -0.34, "x": 0},
        xaxis={"title": "", "tickangle": -45,
               "tickfont": {"size": 11, "family": "Inter,sans-serif", "color": "#1a3a6b"}},
        # fixedrange locks the y-axis so drag/pan only moves left-right (no up-down
        # or diagonal); the x-axis stays pannable.
        yaxis={"title": "", "showgrid": True, "gridcolor": "#eef2f7", "fixedrange": True},
    )
    return fig


# Event-table events that count as "work" (turn a streak dot green + active), the
# accounting-sync signal (purple), and the engagement-only events (light blue,
# NOT active). Uploads and Accounting Syncs themselves come from the unbounded
# _UPL/_SYN summaries; everything else here comes from _ACT_EVENTS.
_STREAK_EVENT_BUCKET = {
    "Transaction Ledger Updated": "txns", "Transaction Type Updated": "txns",  # "Transactions updated"
    "Transaction Status": "txnstatus",                                         # shown separately
    "Invoice Created": "invoices", "Invoice Bulk Edited": "invoices",
    "Entity Created": "entities", "Recon Processed": "recon",
    "Vendor Mismatch Resolved": "vmr", "Mapping Completed": "mapping",
    "Delete": "deletes",                       # work
    "Login": "logins", "Dashboard Viewed": "views",   # engagement only (not active)
}

# Activity Score weighting: per event per day -> weight * min(count, cap_per_day),
# summed over the last 28 days and all events. Max 315 pts/day. Uses the raw
# event-table counts for all 14 events (from _ACT_EVENTS).
_ACTIVITY_SCORE = {
    "Login": (1, 1), "Dashboard Viewed": (1, 4), "Upload": (5, 10), "Delete": (1, 5),
    "Transaction Status": (1, 20), "Transaction Ledger Updated": (2, 15),
    "Transaction Type Updated": (2, 15), "Entity Created": (1, 20),
    "Invoice Created": (3, 10), "Invoice Bulk Edited": (3, 5),
    "Vendor Mismatch Resolved": (4, 10), "Recon Processed": (4, 5),
    "Accounting Sync": (8, 5), "Mapping Completed": (10, 1),
}

def _activity_scores():
    """Per-account Activity Score over the last 28 days: sum over days & events of
    weight * min(daily_count, cap_per_day). One vectorized pass over the in-memory
    _ACT_EVENTS (no DB). Returns {account_id: score}."""
    today = pd.Timestamp(date.today()).normalize()
    start = today - pd.Timedelta(days=27)
    ev = _ACT_EVENTS
    if ev is None or len(ev) == 0:
        return {}
    e = ev[(ev["event_time"] >= start) & (ev["event_time"] < today + pd.Timedelta(days=1))
           & (ev["event_name"].isin(_ACTIVITY_SCORE))].dropna(subset=["account_id"]).copy()
    if len(e) == 0:
        return {}
    e["_d"] = e["event_time"].dt.normalize()
    daily = e.groupby(["account_id", "_d", "event_name"]).size().reset_index(name="n")
    daily["w"]   = daily["event_name"].map(lambda x: _ACTIVITY_SCORE[x][0])
    daily["cap"] = daily["event_name"].map(lambda x: _ACTIVITY_SCORE[x][1])
    daily["pts"] = daily["w"] * daily[["n", "cap"]].min(axis=1)
    return daily.groupby("account_id")["pts"].sum().astype(int).to_dict()

def _recent_event_lookup():
    """Per (account_id, day) counts of the streak's event-table events for the last
    28 days, computed ONCE from the in-memory _ACT_EVENTS so _usage_28 is a cheap
    dict lookup per customer (no DB, no 124k-row rescan per account)."""
    today = pd.Timestamp(date.today()).normalize()
    start = today - pd.Timedelta(days=27)
    ev = _ACT_EVENTS
    if ev is None or len(ev) == 0:
        return {}
    e = ev[(ev["event_time"] >= start) & (ev["event_time"] < today + pd.Timedelta(days=1))].copy()
    e["_b"] = e["event_name"].map(_STREAK_EVENT_BUCKET)
    e = e.dropna(subset=["_b", "account_id"])
    if len(e) == 0:
        return {}
    e["_d"] = e["event_time"].dt.normalize()
    lu = {}
    for (ac, d, b), n in e.groupby(["account_id", "_d", "_b"]).size().items():
        lu.setdefault(ac, {}).setdefault(d, {})[b] = int(n)
    return lu

def _usage_28(email, ev_lu):
    """Usage in the last 28 days for a customer's account. Returns
    (active_days_count, streak). `streak` encodes 28 days as ';'-joined 13-field
    tokens (index 0 = today .. 27 = today-27d):
      on,uploads,syncs,items,views,txns,entities,recon,vmr,mapping,invoices,deletes,logins,txnstatus
    on=1 (ACTIVE) when there was ANY event that day — an upload, an accounting
    sync, any work event (transactions / entities / invoices / recon /
    vendor-mismatch / mapping / delete), OR a presence event (login /
    dashboard-viewed). The grid colours the dot: green=accounting sync,
    yellow=any other event, grey=nothing."""
    ac = _EMAIL_ACCT.get(_clean_email(email))
    today = pd.Timestamp(date.today()).normalize()
    blank = ";".join([",".join(["0"] * 14)] * 28)
    if ac is None:
        return 0, blank
    start = today - pd.Timedelta(days=27)
    uploads = {}; syncs = {}; items = {}
    if "date" in _UPL.columns:
        u = _UPL[(_UPL["account_id"] == ac) & (_UPL["date"] >= start) & (_UPL["date"] <= today)].copy()
        if len(u) and "total_uploads" in u.columns:
            u["_d"] = u["date"].dt.normalize()
            uploads = u.groupby("_d")["total_uploads"].sum().to_dict()
    if "event_date" in _SYN.columns:
        sy = _SYN[(_SYN["account_id"] == ac) & (_SYN["event_date"] >= start) & (_SYN["event_date"] <= today)].copy()
        if len(sy):
            sy["_d"] = sy["event_date"].dt.normalize()
            if "items_count" in sy.columns:
                items = sy.groupby("_d")["items_count"].sum().to_dict()
            syncs = sy.groupby("_d").size().to_dict()
    acc_ev = ev_lu.get(ac, {})
    active = 0
    toks = []
    for i in range(28):
        d = today - pd.Timedelta(days=i)
        up = int(uploads.get(d, 0) or 0); sc = int(syncs.get(d, 0) or 0); it = int(items.get(d, 0) or 0)
        ce = acc_ev.get(d, {})
        txn = ce.get("txns", 0); ent = ce.get("entities", 0); rec = ce.get("recon", 0)
        vmr = ce.get("vmr", 0); mp = ce.get("mapping", 0); inv = ce.get("invoices", 0)
        dele = ce.get("deletes", 0); log = ce.get("logins", 0); vw = ce.get("views", 0)
        ts = ce.get("txnstatus", 0)   # Transaction Status (shown separately from "Transactions updated")
        # active = ANY event that day, presence (login / dashboard-viewed) included
        on = 1 if (up or sc or txn or ts or ent or rec or vmr or mp or inv or dele or log or vw) else 0
        active += on
        toks.append("%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d" % (
            on, up, sc, it, vw, txn, ent, rec, vmr, mp, inv, dele, log, ts))
    return active, ";".join(toks)


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


def _va_incentive(s, e):
    """Per-AM incentive view for the selected date range [s, e]:
      • One-time Collected = One-time line items paid in the range
      • MRR Collected      = the AM's MRR bucket for the month(s) in range — each
        RENEWAL line's monthly rate (unit_price/term) is SPREAD across its `term`
        months from billing_start; we sum the slices whose month falls inside the
        range (VA is operational, so MRR is earned in the months it covers, not
        up-front). New-business lines are NOT tracked here.
      • Total MRR = One-time Collected + MRR Collected
    AM comes from va_live.am_owner; independent of the page's owner/campaign/
    channel filters (only the date range applies)."""
    li = _VA_LI
    if li is None or len(li) == 0 or "recurring_type" not in li.columns:
        return pd.DataFrame()
    am_map = ({} if "am_owner" not in _VA.columns else
              _VA.dropna(subset=["record_id"]).drop_duplicates("record_id")
                 .set_index("record_id")["am_owner"].to_dict())
    def _am(rid):
        v = am_map.get(rid)
        return v.strip() if (isinstance(v, str) and v.strip()) else "—"
    sp, ep = s.to_period("M"), e.to_period("M")
    onetime, mrr = {}, {}
    ot = li[(li["recurring_type"] == "One-time") & li["date_paid"].notna()]
    ot = ot[(ot["date_paid"] >= s) & (ot["date_paid"] <= e)]
    for r in ot.itertuples():
        onetime[_am(r.record_id)] = onetime.get(_am(r.record_id), 0) + float(r.unit_price or 0)
    rec = li[li["recurring_type"] == "Renewal"].copy()
    rec["bstart"] = rec["billing_start_date"].where(rec["billing_start_date"].notna(), rec["date_paid"])
    rec = rec.dropna(subset=["bstart"])
    for r in rec.itertuples():
        term = int(r.term) if (pd.notna(r.term) and r.term and r.term > 0) else 1
        rate = float(r.unit_price or 0) / term
        bp = pd.Period(r.bstart, "M")
        am = _am(r.record_id)
        for k in range(term):
            if sp <= (bp + k) <= ep:
                mrr[am] = mrr.get(am, 0) + rate
    ams = sorted(set(onetime) | set(mrr))
    rows = [{"AM": am, "One-time Collected": round(onetime.get(am, 0)),
             "MRR Collected": round(mrr.get(am, 0)),
             "Total MRR": round(onetime.get(am, 0) + mrr.get(am, 0))} for am in ams]
    t = pd.DataFrame(rows)
    if len(t):
        t = t.sort_values("Total MRR", ascending=False).reset_index(drop=True)
        tot = t[["One-time Collected", "MRR Collected", "Total MRR"]].sum().to_dict()
        tot["AM"] = "Total"
        t = pd.concat([t, pd.DataFrame([tot])], ignore_index=True)
    return t


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


def _usage_cohort(event_filter=None, deal_filter=None, stage_filter=None, csm_filter=None):
    """Customer Usage Cohort (last 12 integration weeks). Rows = integration-week
    Monday; columns = Integrated (cohort size) + W1..W12 (active accounts that had
    any upload/sync activity in that week-offset window; W1 = the integration week
    itself). The current in-progress calendar week is excluded. Returns (counts_df,
    pct_df), each with a pinned Total row. Replicates the DAX cohort measures.

    event_filter (Customer Activity Cohort mode — used by the new event-driven
    charts; leave as None for the original Usage Cohort behavior above).
    "Upload" and "Accounting Sync" are always sourced from the old, unbounded
    _UPL/_SYN tables (reliable multi-month history) rather than the 90-day-bounded
    new event tables — those two event names are effectively aliases for the
    original Usage Cohort signal. Every other event name comes from the new
    aia_*_events tables, which is genuinely new signal with no older equivalent.
        None       -> legacy path, membership checked against _ACTIVE_WEEKS
                      (Upload+Sync summary tables). Unchanged behavior.
        []          -> "All Events": _ACTIVE_WEEKS (old Upload+Sync, unbounded)
                      UNION the 12 other tracked events from the new tables
                      (90-day bounded) — a strict superset of the old Usage
                      Cohort, since nothing tracked there is dropped.
        [names...] -> "Upload"/"Accounting Sync" resolve to _ACTIVE_WEEKS_UPL /
                      _ACTIVE_WEEKS_SYN; any other names resolve to
                      _ACTIVE_WEEKS_EV filtered to those names. Unioned together.

    deal_filter / stage_filter / csm_filter: optional lists restricting the
    cohort base by Deal Name / Deal Stage / CSM (cs_owner) before computing
    W1..W12 — used by the Customer Activity Cohort's filter row. None/[] means
    no restriction (matches every prior call site, including the legacy one
    above)."""
    _OLD_SOURCED = {"Upload": _ACTIVE_WEEKS_UPL, "Accounting Sync": _ACTIVE_WEEKS_SYN}
    if event_filter is None:
        weeks_set = _ACTIVE_WEEKS
    elif event_filter:
        _evs = set(event_filter)
        weeks_set = set()
        for _name, _old_set in _OLD_SOURCED.items():
            if _name in _evs:
                weeks_set |= _old_set
        _new_evs = _evs - set(_OLD_SOURCED)
        if _new_evs:
            weeks_set |= {(a, w) for (a, w, ev) in _ACTIVE_WEEKS_EV if ev in _new_evs}
    else:
        weeks_set = _ACTIVE_WEEKS | {(a, w) for (a, w, ev) in _ACTIVE_WEEKS_EV
                                     if ev not in _OLD_SOURCED}

    base = _AIA[(_AIA["integration_done_date"].notna())
                & (_AIA["login_email_id"].notna())
                & (_AIA["login_email_id"].astype(str).str.strip() != "")
                & (_AIA["module_type"] == "AIA Paid")].copy()
    if deal_filter:
        base = base[base["deal_name"].isin(deal_filter)]
    if stage_filter:
        base = base[base["deal_stage"].isin(stage_filter)]
    if csm_filter:
        base = base[base["cs_owner"].isin(csm_filter)]
    if len(base) == 0:
        return pd.DataFrame(), pd.DataFrame()
    iw = base["integration_done_date"].dt.normalize()
    base["iw"] = iw - pd.to_timedelta(iw.dt.weekday, unit="D")     # Monday
    today = pd.Timestamp(date.today()).normalize()
    # last COMPLETE calendar week (its Sunday has already passed). The current
    # in-progress week is dropped from both the integration-week rows AND the
    # offset columns so every shown number reflects a full week of data.
    last_complete_mon = (today - pd.Timedelta(days=today.weekday())) - pd.Timedelta(days=7)
    weeks = sorted([w for w in base["iw"].dropna().unique() if w <= last_complete_mon])[-12:]
    if not weeks:
        return pd.DataFrame(), pd.DataFrame()
    OFFS = list(range(12))

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
        # One resolved account per DEAL (record_id), so the active numerator is
        # counted in the same unit as `size` (distinct deals). Two deals sharing a
        # login email map to one account_id; counting the de-duped account set
        # would count that once in the numerator but twice in Integrated (e.g. the
        # 08 Jun week capping at 90% when every deal actually logged in). Unresolved
        # emails -> None, which never matches weeks_set (stays inactive).
        deal_accts = {}
        for rid, em in sub[["record_id", "login_email_id"]].itertuples(index=False):
            if rid not in deal_accts:
                deal_accts[rid] = _EMAIL_ACCT.get(_clean_email(em))
        deal_accts = list(deal_accts.values())   # one per deal (account_id may repeat)
        label = wk.strftime("%d %b")
        crow = {"Integration Week": label, "Integrated": size}
        prow = {"Integration Week": label, "Integrated": size}
        for o in OFFS:
            cws = wk + pd.Timedelta(days=o * 7)
            col = f"W{o+1}"                 # W1 = the integration week itself (1-indexed)
            if cws > last_complete_mon:     # current in-progress / future week -> blank
                crow[col] = ""; prow[col] = ""
                continue
            active = sum(1 for a in deal_accts if a and (a, cws) in weeks_set)
            crow[col] = active
            pct = round(active / size * 100) if size else 0
            prow[col] = (f"{pct}%" if pct else "")   # blank when 0%
            tot_act[o] += active; tot_size[o] += size; tot_valid[o] = True
        cnt_rows.append(crow); pct_rows.append(prow)

    cnt_tot = {"Integration Week": "Total", "Integrated": tot_int}
    pct_tot = {"Integration Week": "Total", "Integrated": tot_int}
    for o in OFFS:
        col = f"W{o+1}"
        cnt_tot[col] = tot_act[o] if tot_valid[o] else ""
        _tp = round(tot_act[o] / tot_size[o] * 100) if (tot_valid[o] and tot_size[o]) else 0
        pct_tot[col] = (f"{_tp}%" if _tp else "")   # blank when 0%
    cnt_rows.append(cnt_tot); pct_rows.append(pct_tot)
    return pd.DataFrame(cnt_rows), pd.DataFrame(pct_rows)


def _mrr_matrix(li, refund_map, mode, add_onetime=False, as_of=None, add_new=False):
    """Refunds-adjusted billing-to-MRR cohort matrix (replicates the DAX
    total_monthly_collection / #Active Paid Users). Each non-refunded line item
    is recognised across its active term (billing_start_date month .. +term,
    exclusive; falls back to date_paid when billing_start_date is missing),
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
    # Recognition is anchored to the month the payment is FOR (billing_start_date),
    # not when the cash arrived (date_paid): a late / back-dated payment must still
    # be attributed to its intended billing month. Fall back to date_paid when
    # billing_start_date is missing. (The Fresh Renewals / One-time rows below stay
    # keyed on date_paid — the cash-received view — by design.)
    if "billing_start_date" in li.columns:
        _bstart = li["billing_start_date"].where(li["billing_start_date"].notna(), li["date_paid"])
    else:
        _bstart = li["date_paid"]
    li["start_p"]  = _bstart.dt.to_period("M")
    li["cohort_p"] = li["cohort_month"].dt.to_period("M")
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
            recs.append((str(r.cohort_p), r.start_p + k, r.record_id, r.monthly))
    sp = pd.DataFrame(recs, columns=["Cohort", "bp", "rid", "amt"])
    if len(sp) == 0:
        return pd.DataFrame()

    lo = min(li["cohort_p"].min(), li["start_p"].min())
    hi = li["start_p"].max()
    # Columns always END at the current month: extend to it when the latest
    # payment in view is earlier (so a multi-month subscription still shows its
    # split up to today), and never run PAST it — a line item billed for a future
    # month (e.g. paid 16-Jul, billing_start in Aug) must not open a future
    # column and expose every other cohort's not-yet-earned future spread.
    if as_of is not None:
        _asof = pd.Period(pd.Timestamp(as_of), freq="M")
        hi = _asof if _asof >= lo else lo
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
    # New Collection = full cash collected from 'New' line items in the month paid
    # (cash view, like Fresh Renewals / One-time — NOT the normalised cohort split).
    if add_new:
        ncb = _by_month(li[li["recurring_type"] == "New"])
        extra.append({"Cohort": "New Collection", **{c: int(ncb[p]) for c, p in zip(cols, full)}})
    frb = _by_month(li[li["recurring_type"] == "Renewal"])
    extra.append({"Cohort": "Fresh Renewals", **{c: int(frb[p]) for c, p in zip(cols, full)}})
    if add_onetime:
        otb = _by_month(li[li["recurring_type"] == "One-time"])
        extra.append({"Cohort": "One-time", **{c: int(otb[p]) for c, p in zip(cols, full)}})
    # Total row stays = column sums of the cohort (New-acquisition, normalised-MRR)
    # rows only. The cash rows above (New Collection / Fresh Renewals / One-time)
    # are informational and are deliberately NOT rolled into this Total.
    extra.append({"Cohort": "Total", **{c: int(piv[c].sum()) for c in cols}})
    return pd.concat([out, pd.DataFrame(extra)], ignore_index=True)

def _matrix_current_mrr(rev_m, today, exclude_onetime=False):
    """Current-month MRR = the revenue matrix's Total row under the current-month
    column (normalised monthly recurring per _mrr_matrix; refunds excluded for the
    feeds that pass a refund_map). With exclude_onetime, the One-time row's
    current-month value is subtracted — one-time payments are never recurring.
    Returns 0 when that column/row is absent."""
    if rev_m is None or not len(rev_m):
        return 0
    col = today.strftime("%b %y")
    if col not in rev_m.columns:
        return 0
    tot = rev_m.loc[rev_m["Cohort"] == "Total", col]
    val = int(tot.iloc[0]) if len(tot) else 0
    if exclude_onetime:
        ot = rev_m.loc[rev_m["Cohort"] == "One-time", col]
        if len(ot):
            o = str(ot.iloc[0]).strip()
            if o not in ("", "0"):
                val -= int(float(o))
    return val

def _distinct_payers_by_month(li, refund_map, cols):
    """Distinct paying customers per month across New/Renewal/One-time line items
    (refund-filtered), keyed to the matrix's '%b %y' column labels. A customer who
    paid two types in the same month counts ONCE — unlike summing the type rows,
    which would count them twice. Used for the retention 'Total Payments' row."""
    d = li.dropna(subset=["date_paid"])
    d = d[d["recurring_type"].isin(["New", "Renewal", "One-time"])]
    if refund_map is not None:
        ref = d["record_id"].map(refund_map).astype("string").str.strip().str.lower()
        d = d[(ref != "yes").fillna(True)]
    by = d.groupby(d["date_paid"].dt.to_period("M"))["record_id"].nunique()
    out = {}
    for c in cols:
        p = pd.Period(pd.to_datetime(c, format="%b %y"), freq="M")
        out[c] = int(by.get(p, 0))
    return out

NEON_URL     = os.getenv("NEON_DATABASE_URL", "")
SUPABASE_URL = os.getenv("SUPABASE_DATABASE_URL", "")

# ═══════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════

def _q(url, sql, _tries=5, statement_timeout_ms=None):
    """Run a query, return a DataFrame. The container's NAT path to Neon is slow
    and occasionally drops a transfer ("SSL connection closed unexpectedly"), so:
    enable TCP keepalives (keeps the NAT conntrack entry alive during long pulls),
    retry a few times on transient drops, and ALWAYS close the connection
    (psycopg2's `with` only ends the transaction — it never closes the socket,
    which otherwise leaks a connection per query).
    statement_timeout_ms: optional per-query server-side timeout (unset by
    default — no behavior change for existing callers). Used for the new
    activity-event queries so a missing index / unexpectedly large scan fails
    fast instead of loading the DB, given this Supabase project's prior
    Disk IO Budget incident."""
    last = None
    for i in range(_tries):
        conn = None
        try:
            kwargs = dict(connect_timeout=20, keepalives=1, keepalives_idle=15,
                          keepalives_interval=5, keepalives_count=8)
            if statement_timeout_ms:
                kwargs["options"] = f"-c statement_timeout={int(statement_timeout_ms)}"
            conn = psycopg2.connect(url, **kwargs)
            return pd.read_sql_query(sql, conn)
        except Exception as ex:
            last = ex
            print(f"[retry {i+1}/{_tries}] DB query failed: {ex}")
            _time.sleep(3)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    raise last

# ── Customer Activity Cohort — 5 event tables (Supabase project qmaphtslnvvifkzmbrvh) ──
# Each entry: table name -> columns to select. Column lists are deliberately
# minimal and MUST match the covering index's INCLUDE list exactly (event_time,
# account_id, event_name[, items_count]) so Postgres can answer via an
# index-only scan — no `email`, no `raw` jsonb, no SELECT *. This project had a
# Disk IO Budget outage from an unbounded/malformed query; every query here is
# bounded to a rolling 100-day window (event_time >= now() - interval '100 days')
# and account_id IS NOT NULL. See OPS notes for the required indexes:
#   CREATE INDEX idx_session_events_cohort         ON aia_session_events        (event_time) INCLUDE (account_id, event_name);
#   CREATE INDEX idx_upload_events_cohort          ON aia_upload_events         (event_time) INCLUDE (account_id, event_name);
#   CREATE INDEX idx_sync_events_cohort            ON aia_sync_events           (event_time) INCLUDE (account_id, event_name, items_count);
#   CREATE INDEX idx_transaction_events_cohort     ON aia_transaction_events    (event_time) INCLUDE (account_id, event_name, items_count);
#   CREATE INDEX idx_vendor_invoice_events_cohort  ON aia_vendor_invoice_events (event_time) INCLUDE (account_id, event_name);
_EVENT_TABLES = {
    # session events include Login rows whose account_id is NULL in the source
    # (pre-auth) — we also pull `email` here so the account_id can be backfilled
    # from aia_accounts; the tiny heap fetch is worth recovering ~all logins.
    "session":        ("aia_session_events",        ["account_id", "event_name", "event_time", "email"]),
    "upload":         ("aia_upload_events",          ["account_id", "event_name", "event_time"]),
    "sync":           ("aia_sync_events",            ["account_id", "event_name", "event_time", "items_count"]),
    "transaction":    ("aia_transaction_events",     ["account_id", "event_name", "event_time", "items_count"]),
    "vendor_invoice": ("aia_vendor_invoice_events",  ["account_id", "event_name", "event_time"]),
}

def _load_activity_events():
    """Bounded 90-day pulls from the 5 Customer Activity Cohort event tables.
    Each table is queried and error-handled INDEPENDENTLY (own try/except) so a
    problem with any one of them (e.g. a missing items_count column) can't blank
    out the rest of the dashboard — these are additive, not on the critical load
    path that _load_all()'s outer try/except guards. statement_timeout_ms bails
    fast if a query runs long (e.g. an index is missing)."""
    out = {}
    for key, (table, cols) in _EVENT_TABLES.items():
        try:
            collist = ", ".join(cols)
            # session Login events carry a NULL account_id but a valid email;
            # keep them so _prep_activity_events can backfill the account_id.
            acct_where = ("(account_id IS NOT NULL OR email IS NOT NULL)"
                          if key == "session" else "account_id IS NOT NULL")
            out[key] = _q(SUPABASE_URL,
                f"SELECT {collist} FROM public.{table} "
                f"WHERE event_time >= now() - interval '100 days' AND {acct_where}",
                statement_timeout_ms=10000)
        except Exception as ex:
            print(f"[WARN] activity event table {table} failed: {ex} -- using empty frame")
            out[key] = pd.DataFrame(columns=cols)
    return out

def _empty_activity_events():
    return {key: pd.DataFrame(columns=cols) for key, (_t, cols) in _EVENT_TABLES.items()}


def _load_acct_by_email():
    """email -> account_id from the authoritative aia_accounts table (which links
    every account to its hubspot_login_email and its app account_email). Two jobs:
      1. backfill the account_id on session Login events that arrive NULL, and
      2. resolve base-row login_email -> account for accounts that never uploaded
         / synced (so _UPL/_SYN never linked them).
    hubspot_login_email wins over account_email (it matches _AIA.login_email_id)."""
    m = {}
    try:
        df = _q(SUPABASE_URL,
            "SELECT account_id, hubspot_login_email, account_email FROM public.aia_accounts",
            statement_timeout_ms=15000)
    except Exception as ex:
        print(f"[WARN] aia_accounts email map failed: {ex}")
        return m
    if df is None or not len(df):
        return m
    # hubspot_login_email first (first-wins), then account_email as fallback
    for col in ("hubspot_login_email", "account_email"):
        if col not in df.columns:
            continue
        for ac, em in df[["account_id", col]].dropna().itertuples(index=False):
            em = _clean_email(em)
            if em and em not in m:
                m[em] = ac
    return m

def _load_signals():
    """Marketing 'Daily signals' inputs from Supabase: GA sessions (ga_daily) and
    the AI SDR WhatsApp log ("AI SDR - Conversations"). Both are bounded to a rolling
    45-day window (this project had a prior Disk-IO incident, so every pull is bounded
    + time-limited) and each is guarded independently so a failure just yields an empty
    frame rather than blanking the Marketing page. aia_live lives on Neon, so the joins
    to these happen later in pandas."""
    ga = pd.DataFrame(columns=["date", "hostname", "landing_page", "sessions"])
    conv = pd.DataFrame(columns=["lead_phone", "deal_id", "direction",
                                 "template_name", "delivery_status", "timestamp"])
    try:
        ga = _q(SUPABASE_URL,
            "SELECT date, hostname, landing_page, sessions FROM public.ga_daily "
            "WHERE date >= current_date - interval '45 days'",
            statement_timeout_ms=15000)
    except Exception as ex:
        print(f"[WARN] ga_daily load failed: {ex} -- using empty frame")
    try:
        conv = _q(SUPABASE_URL,
            'SELECT lead_phone, deal_id, direction, template_name, delivery_status, '
            '"timestamp" FROM "AI SDR - Conversations" '
            "WHERE \"timestamp\" >= now() - interval '45 days'",
            statement_timeout_ms=20000)
    except Exception as ex:
        print(f"[WARN] AI SDR conversations load failed: {ex} -- using empty frame")
    return ga, conv

def _load_all():
    try:
        aia = _q(NEON_URL, "SELECT * FROM public.aia_live WHERE is_deleted IS NULL")
        va  = _q(NEON_URL, "SELECT * FROM public.va_live WHERE is_deleted IS NULL")
        li  = _q(NEON_URL, "SELECT * FROM public.line_items WHERE deleted IS NULL")
        inc = _q(NEON_URL, "SELECT gm_combined, month, monthly_mrr_target, is_gap_carry_forwarded FROM public.incentive_targets ORDER BY month, gm_combined")
        mkt = _q(SUPABASE_URL, "SELECT * FROM public.marketing_spends ORDER BY day ASC")
        upl = _q(SUPABASE_URL, "SELECT * FROM public.user_daily_upload_summary ORDER BY date ASC")
        syn = _q(SUPABASE_URL, "SELECT * FROM public.accounting_sync_mixpanel")
        act = _load_activity_events()
        print(f"[OK] AIA:{len(aia)} VA:{len(va)} LI:{len(li)} INC:{len(inc)} MKT:{len(mkt)} UPL:{len(upl)} SYN:{len(syn)} "
              f"ACT:{sum(len(d) for d in act.values())}")
        return aia, va, li, inc, mkt, upl, syn, act
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
                pd.DataFrame(columns=cols_syn), _empty_activity_events())

print("Loading data...")
_RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN, _RAW_ACT = _load_all()

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

def _phone10(v):
    """Normalise any phone string to its last 10 digits (drops +91 / spaces / dashes)
    so aia_live.poc_number and Conversations.lead_phone join on the same key."""
    d = re.sub(r"\D", "", str(v or ""))
    return d[-10:] if len(d) >= 10 else ""

def _prep_signals(ga, conv):
    """Normalise the Daily-signals inputs. GA: date -> midnight, sessions numeric.
    Conversations: timestamptz -> naive IST, a msg_date day column, a last-10-digit
    phone key, and lower-cased direction/template/status for clean matching."""
    ga = ga.copy() if ga is not None else pd.DataFrame()
    if len(ga):
        if "date" in ga.columns:
            ga["date"] = pd.to_datetime(ga["date"], errors="coerce").dt.normalize()
        ga["sessions"] = pd.to_numeric(ga.get("sessions"), errors="coerce").fillna(0)
    conv = conv.copy() if conv is not None else pd.DataFrame()
    if len(conv):
        ts = pd.to_datetime(conv["timestamp"], errors="coerce", utc=True)
        ts_ist = ts.dt.tz_convert(_IST).dt.tz_localize(None)
        conv["timestamp"] = ts_ist
        conv["msg_date"] = ts_ist.dt.normalize()
        conv["p10"] = conv["lead_phone"].apply(_phone10)
        for cc in ("direction", "template_name", "delivery_status"):
            if cc in conv.columns:
                conv[cc] = conv[cc].astype(str).str.strip().str.lower()
    return ga, conv

_RAW_GA, _RAW_CONV = _load_signals()
_GA, _CONV = _prep_signals(_RAW_GA, _RAW_CONV)

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

def _prep_activity_events(act_dict):
    """Combine the 5 event-table pulls into one long frame: [account_id,
    event_name, event_time, items_count]. event_time normalised to tz-naive (UTC).
    Session Login rows arrive with a NULL account_id but a valid email — backfill
    their account_id from aia_accounts (_ACCT_BY_EMAIL) so those logins are
    attributable in the Customer Activity Cohort; any row still unresolved after
    backfill is dropped."""
    frames = []
    for df in act_dict.values():
        if df is None or len(df) == 0:
            continue
        d = df.copy()
        if not {"account_id", "event_name", "event_time"}.issubset(d.columns):
            continue
        d["event_time"] = pd.to_datetime(d["event_time"], errors="coerce", utc=True).dt.tz_convert(None)
        if "items_count" not in d.columns:
            d["items_count"] = np.nan
        # backfill NULL account_id from the row's email (session Login events)
        if "email" in d.columns:
            need = d["account_id"].isna()
            if need.any():
                d.loc[need, "account_id"] = d.loc[need, "email"].map(
                    lambda e: _ACCT_BY_EMAIL.get(_clean_email(e)))
        frames.append(d[["account_id", "event_name", "event_time", "items_count"]])
    if not frames:
        return pd.DataFrame(columns=["account_id", "event_name", "event_time", "items_count"])
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["account_id"])   # drop rows we still couldn't resolve
    out["items_count"] = pd.to_numeric(out["items_count"], errors="coerce")
    return out

def _clean_email(v):
    """Normalise an email for lookup: lower-case, trim, and strip any Unicode
    'other/control' characters (e.g. a stray U+2060 word-joiner that some CRM
    exports prepend) which otherwise break email→account matching."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = "".join(c for c in str(v) if unicodedata.category(c)[0] != "C")
    return s.lower().strip()

# Authoritative email -> account_id (aia_accounts), built BEFORE the event prep so
# session Login rows that arrive with a NULL account_id can be backfilled by email.
_ACCT_BY_EMAIL = _load_acct_by_email()
_ACT_EVENTS = _prep_activity_events(_RAW_ACT)
# Pre-filtered once so _usage_28 (called once per paid customer) doesn't re-scan
# every event_name on every call — only account_id/date filtering happens per call.
_DVIEW_EVENTS = _ACT_EVENTS[_ACT_EVENTS["event_name"] == "Dashboard Viewed"]

# ── Usage-cohort lookups (precomputed once) ─────────────────────────────────
# email -> account_id, and the set of (account_id, week-Monday) that had any
# upload OR sync activity. Used to build the Customer Usage Cohort table fast.
def _build_activity_lookups():
    email_acct = {}

    def _weekset(src, dcol):
        """(account_id, week-Monday) pairs with activity in `src`, plus
        email->account_id enrichment (first-wins) into the shared email_acct."""
        s = set()
        if "account_id" not in src.columns or dcol not in src.columns:
            return s
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
                s.add((ac, mm))
        return s

    # Kept separate (not just combined) so the Customer Activity Cohort can source
    # "Upload" and "Accounting Sync" specifically from these unbounded, long-running
    # tables instead of the 90-day-bounded new event tables — those two old tables
    # already have reliable multi-month history; the new tables add signal only for
    # event types that were never tracked before.
    active_weeks_upl = _weekset(_UPL, "date")
    active_weeks_syn = _weekset(_SYN, "event_date")
    active_weeks = active_weeks_upl | active_weeks_syn   # legacy combined set (Customer Usage Cohort, unchanged)

    # Gap-fill the email->account map for accounts that only ever logged in /
    # viewed the dashboard (no _UPL/_SYN row above), so their event activity is
    # attributable in the Customer Activity Cohort. First-wins keeps the
    # authoritative _UPL/_SYN mapping wherever both exist. This does NOT change
    # the legacy Usage Cohort: its membership still requires an (acct, week) in
    # active_weeks (upload/sync only), which these accounts don't have.
    for em, ac in _ACCT_BY_EMAIL.items():
        if em not in email_acct:
            email_acct[em] = ac

    # Event-aware weekly activity for the Customer Activity Cohort charts, built
    # from the 5 aia_*_events tables (independent of the Upload/Sync summary
    # tables above). Keeps event_name so the Event Name dropdown can filter.
    active_weeks_ev = set()   # {(account_id, week_monday, event_name)}
    if len(_ACT_EVENTS):
        d = _ACT_EVENTS.dropna(subset=["account_id", "event_time", "event_name"])
        dn = d["event_time"].dt.normalize()
        mon = dn - pd.to_timedelta(dn.dt.weekday, unit="D")
        for ac, mm, ev in zip(d["account_id"], mon, d["event_name"]):
            if pd.notna(mm):
                active_weeks_ev.add((ac, mm, ev))

    return email_acct, active_weeks, active_weeks_upl, active_weeks_syn, active_weeks_ev

_EMAIL_ACCT, _ACTIVE_WEEKS, _ACTIVE_WEEKS_UPL, _ACTIVE_WEEKS_SYN, _ACTIVE_WEEKS_EV = _build_activity_lookups()

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
    global _RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN, _RAW_ACT
    global _AIA, _VA, _AIA_LI, _VA_LI, _INCENTIVE_TARGETS, _MKT, _UPL, _SYN, _ACT_EVENTS, _DVIEW_EVENTS
    global _EMAIL_ACCT, _ACTIVE_WEEKS, _ACTIVE_WEEKS_UPL, _ACTIVE_WEEKS_SYN, _ACTIVE_WEEKS_EV, _ACCT_DATES, _BILLING_END, _LAST_SYNC, _ACCT_BY_EMAIL
    global _RAW_GA, _RAW_CONV, _GA, _CONV
    _RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN, _RAW_ACT = _load_all()
    _RAW_GA, _RAW_CONV = _load_signals()
    _GA, _CONV = _prep_signals(_RAW_GA, _RAW_CONV)
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
    _ACCT_BY_EMAIL = _load_acct_by_email()   # rebuild before prep (backfill dep)
    _ACT_EVENTS = _prep_activity_events(_RAW_ACT)
    _DVIEW_EVENTS = _ACT_EVENTS[_ACT_EVENTS["event_name"] == "Dashboard Viewed"]
    _EMAIL_ACCT, _ACTIVE_WEEKS, _ACTIVE_WEEKS_UPL, _ACTIVE_WEEKS_SYN, _ACTIVE_WEEKS_EV = _build_activity_lookups()
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
    return "All" if n == 0 else (sel[0] if n == 1 else f"Multiple Selections ({n})")

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

def _fmt2(v):
    """1-decimal KPI display, dropping the decimal for exact multiples
    (₹80K and ₹3L when exact; ₹1.6L / ₹12.5K otherwise)."""
    v = int(v)
    if v >= 1_00_000:
        return f"₹{v//1_00_000}L" if v % 1_00_000 == 0 else f"₹{v/1_00_000:.1f}L"
    if v >= 1000:
        return f"₹{v//1000}K" if v % 1000 == 0 else f"₹{v/1000:.1f}K"
    return f"₹{v}"

def _inr(v):
    """Exact value, Indian-grouped: 303676 -> ₹3,03,676."""
    v = int(round(v)); neg = v < 0; s = str(abs(v))
    if len(s) <= 3:
        body = s
    else:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head: groups.insert(0, head)
        body = ",".join(groups) + "," + tail
    return f"₹{'-' if neg else ''}{body}"


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
    _aia_rev = int(pd_.groupby("record_id")["amount_paid"].max().sum())
    state.aia_kpi_collected = _fmt2(_aia_rev)
    state.aia_kpi_collected_exact = f"{_inr(_aia_rev)} · Acquired amount (includes Refunds)"
    # MRR (aia_kpi_mrr) is set below from the GM Performance Total row (acquired MRR).

    # Funnel
    coh   = _rng(df,"create_date",s,e)
    leads = coh["record_id"].nunique()
    ds_n  = coh[coh["ds_date"].notna()&(coh["ds_date"]>=s)&(coh["ds_date"]<=e)]["record_id"].nunique()
    dc_n  = coh[coh["dc_date"].notna()&(coh["dc_date"]>=s)&(coh["dc_date"]<=e)]["record_id"].nunique()
    # Funnel HI: any cohort lead with an eta_pay_date in range counts (regardless
    # of current stage / paid / parked). This affects ONLY the funnel — the HI KPI
    # card and the GM/UTM tables keep their own definitions.
    hi2_mask = (coh["eta_pay_date"].notna()&(coh["eta_pay_date"]>=s)&(coh["eta_pay_date"]<=e))
    hi2   = coh[hi2_mask]["record_id"].nunique()
    paid2 = coh[coh["payment_date"].notna()&(coh["payment_date"]>=s)&(coh["payment_date"]<=e)]["record_id"].nunique()
    p = lambda n: f"{n/leads*100:.0f}%" if leads else "0%"
    _labels = [f"<b>{leads}</b>", f"<b>{ds_n} ({p(ds_n)})</b>", f"<b>{dc_n} ({p(dc_n)})</b>",
               f"<b>{hi2} ({p(hi2)})</b>", f"<b>{paid2} ({p(paid2)})</b>"]
    state.aia_funnel_fig = _make_funnel(
        ["Leads", "DS", "DC", "HI", "Paid"],
        [leads, ds_n, dc_n, hi2, paid2], _labels)

    # Scheduled/Conducted/Qualified trend — DS (blue) behind DC (orange) overlay
    # bars + Qualified line, capped at today. DS by ds_date, DC/Qualified by dc_date.
    e_cap  = min(e, pd.Timestamp(date.today()))
    dc_sub = _rng(df,"dc_date",s,e_cap).copy()
    dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    daily_q  = dc_sub[dc_sub["prospect_score"]>=60].groupby("date")["record_id"].nunique().reset_index(name="Qualified")
    ds_sub = _rng(df,"ds_date",s,e_cap).copy()
    ds_sub["date"] = ds_sub["ds_date"].dt.normalize()
    daily_ds = ds_sub.groupby("date")["record_id"].nunique().reset_index(name="DS")
    trend = pd.DataFrame({"date": pd.date_range(s, e_cap, freq="D")})
    trend = (trend.merge(daily_ds,on="date",how="left").merge(daily_dc,on="date",how="left")
                  .merge(daily_q,on="date",how="left").fillna(0))
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    trend = trend.astype({"DS":int,"DC":int,"Qualified":int})
    state.aia_trend_fig = _make_trend(trend["date_label"].tolist(), trend["DS"].tolist(),
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
            "HI (ATP)":   _rng(o,"eta_pay_date",s,e).query("deal_stage=='High Intent'")["record_id"].nunique(),
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
    # MRR KPI = Acquired MRR from the GM Performance Total row (includes refunds).
    _gm_mrr = int(gm.iloc[-1]["MRR"]) if len(gm) else 0
    state.aia_kpi_mrr = _fmt2(_gm_mrr)
    state.aia_kpi_mrr_exact = f"{_inr(_gm_mrr)} · Acquired MRR (includes refunds)"
    state.aia_gm_json = grid_payload_b64(gm, "GM", bar_cols=["HI (ATP)", "ATP"], fixed=True,
        header_tips={"HI (ATP)": "Active HI deals with payment ETA in the selected period"})

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
            "HI (ATP)": c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
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
    state.aia_utm_json = grid_payload_b64(utm, "UTM Source", bar_cols=["HI (ATP)", "ATP"], fixed=True,
        header_tips={"HI (ATP)": "Active HI deals with payment ETA in the selected cohort"})

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
            # Per-GM hover breakdown, one line per DEAL:
            #   "₹price[, OT ₹ot] (date, term) – Deal Name"
            # rec_frame carries the main price in `val_col`; ot_frame supplies the
            # one-time price (summed per deal). Deals with only a one-time line still
            # appear (price omitted).
            def _tip_lines(rec_frame, ot_frame, val_col):
                ot_map = {}
                if ot_frame is not None and len(ot_frame):
                    for r in ot_frame.itertuples():
                        dn = str(getattr(r, "deal_name", "") or "").strip() or "—"
                        try: ot_map[dn] = ot_map.get(dn, 0) + float(getattr(r, "unit_price", 0) or 0)
                        except (TypeError, ValueError): pass
                def _row(dn, price, tm, dps, ot):
                    parts = ([f"₹{int(round(price)):,}"] if price is not None else [])
                    if ot: parts.append(f"OT ₹{int(round(ot)):,}")
                    paren = ", ".join(([dps] if dps else []) + ([f"{tm}m"] if tm else []))
                    return ", ".join(parts) + (f" ({paren})" if paren else "") + f"  {dn}"
                out, seen = [], set()
                if rec_frame is not None and len(rec_frame):
                    for r in rec_frame.sort_values("date_paid").itertuples():   # chronological
                        dn = str(getattr(r, "deal_name", "") or "").strip() or "—"
                        try: v = float(getattr(r, val_col, 0) or 0)
                        except (TypeError, ValueError): v = 0
                        tm = getattr(r, "term", None); tm = int(tm) if (pd.notna(tm) and tm) else None
                        dp = getattr(r, "date_paid", None); dps = pd.Timestamp(dp).strftime("%d-%b") if pd.notna(dp) else ""
                        ot = ot_map.get(dn) if dn not in seen else None
                        seen.add(dn)
                        out.append(_row(dn, v, tm, dps, ot))
                for dn, ot in ot_map.items():          # deals with only a one-time line
                    if dn in seen: continue
                    seen.add(dn); out.append(_row(dn, None, None, "", ot))
                return out
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
                # Hover breakdowns (one line per deal). Revenue = recurring lines'
                # unit_price; MRR = New lines' per-month rate (aia/gst 'mrr'; va
                # unit_price/term). Both attach the deal's one-time price as OT.
                _aia_all = _AIA_LI[_AIA_LI["record_id"].isin(list(aia_ids) + list(gst_ids))]
                _va_all  = _VA_LI[_VA_LI["record_id"].isin(list(va_ids))]
                _has_rt  = "recurring_type" in _aia_all.columns
                _ot = (pd.concat([_aia_all[_aia_all["recurring_type"] == "One-time"],
                                  _va_all[_va_all["recurring_type"] == "One-time"]])
                       if _has_rt else _aia_all.iloc[0:0])
                _rev_rec = (pd.concat([_aia_all[_aia_all["recurring_type"] != "One-time"],
                                       _va_all[_va_all["recurring_type"] != "One-time"]])
                            if _has_rt else pd.concat([_aia_all, _va_all]))
                if len(_rev_rec):
                    _rev_rec = _rev_rec.assign(tipval=_rev_rec["unit_price"])
                _mrr_rec = pd.concat([
                    aia_li_n.assign(tipval=aia_li_n["mrr"]) if len(aia_li_n) else aia_li_n,
                    gst_li_n.assign(tipval=gst_li_n["mrr"]) if len(gst_li_n) else gst_li_n,
                    (va_li_n.assign(tipval=va_li_n["unit_price"] / va_li_n["term"].replace(0,1).fillna(1))
                     if len(va_li_n) else va_li_n),
                ])
                rev_tip = "\n".join(_tip_lines(_rev_rec, _ot, "tipval"))
                mrr_tip = "\n".join(_tip_lines(_mrr_rec, _ot, "tipval"))
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
                    "AIA+VA Revenue tip": rev_tip,
                    "Combined MRR tip":   mrr_tip,
                })
            if inc_rows:
                inc_df = pd.DataFrame(inc_rows).sort_values("Incentive Payout", ascending=False).reset_index(drop=True)
                tot_row = {"GM":"Total","Gap (Prev Month)":inc_df["Gap (Prev Month)"].sum(),
                           "AIA+VA Revenue":inc_df["AIA+VA Revenue"].sum(),
                           "Combined MRR":inc_df["Combined MRR"].sum(),
                           "Base Target":inc_df["Base Target"].sum(),
                           "Adjusted Target":inc_df["Adjusted Target"].sum(),
                           "Achievement %":"","Incentive Tier":"",
                           "Incentive Payout":inc_df["Incentive Payout"].sum(),
                           "AIA+VA Revenue tip":"","Combined MRR tip":""}
                inc_df = pd.concat([inc_df, pd.DataFrame([tot_row])], ignore_index=True)
                state.aia_incentive_json = grid_payload_b64(
                    inc_df, "GM", sort_default_col="AIA+VA Revenue",
                    center_cols=["Achievement %", "Incentive Tier"],
                    bar_cols=["Gap (Prev Month)", "Incentive Payout"],
                    bar_color={"Gap (Prev Month)": "#f1a0a0", "Incentive Payout": "#c5e07a"},
                    heat_cols={"AIA+VA Revenue": "green"}, autosize=True,
                    tip_cols={"AIA+VA Revenue": "AIA+VA Revenue tip",
                              "Combined MRR": "Combined MRR tip"})
            else:
                state.aia_incentive_json = grid_payload_b64(pd.DataFrame())

# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — CS & FINANCE
# ═══════════════════════════════════════════════════════════════════

def _apply_usage_filter(state):
    """Filter the Customer Usage & Health grid by Deal Name / CSM / Stage / Deal Owner / Cadence / Status."""
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
    _ow = _sel(state.cs_usage_owner)
    if _ow:
        d = d[d["Deal Owner"].isin(_ow)]
    _cad = _sel(state.cs_usage_cadence)
    if _cad:
        d = d[d["Cadence"].isin(_cad)]
    # Status includes "" (empty box) as a real option — use the raw list, since
    # _sel() strips "" (its "no filter" sentinel).
    _sta = state.cs_usage_status if isinstance(state.cs_usage_status, list) else []
    if _sta:
        d = d[d["Status"].isin(_sta)]
    d = d.drop(columns=["Deal Owner"], errors="ignore")   # filter-only, never shown
    # Sl no is a running serial over the CURRENT view: the grid re-numbers it 1..N
    # in display order (see rownum_col), so it stays pinned top-to-bottom through
    # any re-sort, and the last row = how many rows survived the filters.
    d = d.reset_index(drop=True)
    d.insert(0, "Sl no", range(1, len(d) + 1))
    state.cs_usage_json = grid_payload_b64(
        d, sort_default_col="Usage Active Days (28d)", rownum_col="Sl no",
        col_w={"Deal Name": 300},   # keep the slack off the widest text column
        streak_cols=["Usage Streak Last 28D (desc)"], status_cols=["Status"],
        center_cols=["Paid On", "Int Date", "Due On", "Cadence", "Status"],
        date_cols=["Paid On", "Int Date", "Due On"],
        heat_cols={"Usage Active Days (28d)": "green", "Activity Score": "blue"},
        class_cols={"Int Date": "__intcls"},
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})

def _merge_cohort_pct_count(cnt_df, pct_df, mode="all"):
    """Combine the count + % cohort frames into ONE table. `mode` picks what each
    week-offset cell prints:
      "all"   -> '16 (94%)'   (count with the % in brackets — the default)
      "pct"   -> '94%'        (retention only, no brackets)
      "count" -> '16'         (customer count only)
    'Integration Week' / 'Integrated' pass through unchanged; blank (future) and
    zero-activity cells stay blank.
    Also emits a hidden numeric '__pct_<W>' column per week holding the raw
    retention %, returned as a {display_col: source_col} map. The grid shades
    from THOSE on a fixed 0-100 scale (heat_from + heat_max), so the colour always
    means retention — never the headcount, and never rescaled per column."""
    if not len(cnt_df) or not len(pct_df):
        return cnt_df, {}
    wcols = [c for c in cnt_df.columns if c not in ("Integration Week", "Integrated")]
    def _pct_num(pv):
        try:
            return float(str(pv).replace("%", "").strip())
        except (TypeError, ValueError):
            return None
    def _cell(cnt, pctv):
        if cnt is None or cnt == "":
            return "", None                 # offset past today
        try:
            c = int(cnt)
        except (TypeError, ValueError):
            return "", None
        if c == 0:
            return "", None                 # no activity that week
        p = pctv if (isinstance(pctv, str) and pctv) else "0%"
        txt = f"{c}" if mode == "count" else (p if mode == "pct" else f"{c} ({p})")
        return txt, _pct_num(p)
    m = cnt_df.copy()
    heat_from = {}
    for c in wcols:
        pairs = [_cell(cv, pv) for cv, pv in zip(cnt_df[c], pct_df[c])]
        m[c] = [t for t, _ in pairs]
        src = f"__pct_{c}"
        m[src] = [n for _, n in pairs]
        heat_from[c] = src
    return m, heat_from


def _build_cohort_tables(state):
    """Rebuild ONLY the two cohort tables — Customer Usage Cohort and Customer
    Activity Cohort. Each merges its count + % into one '% (count)' table. They
    share the Event Name / Deal Name / Deal Stage / CSM filter row and are
    independent of the rest of the CS Finance page, so filter changes route here
    instead of the full (slow) _cs_refresh."""
    _act_deal  = _sel(state.cs_activity_deal)
    _act_stage = _sel(state.cs_activity_stage)
    _act_csm   = _sel(state.cs_activity_csm)
    _coh_heat = {f"W{o+1}": "green" for o in range(12)}
    # View: "Cohort %" -> % only, "Customers" -> counts only, else both (default).
    _cv = _sel(state.cs_cohort_view)
    _mode = ("pct" if _cv == ["Cohort %"] else "count" if _cv == ["Customers"] else "all")

    def _merged_json(cnt_df, pct_df):
        m, _hfrom = _merge_cohort_pct_count(cnt_df, pct_df, _mode)
        # Shade by the retention % on a FIXED 0-100 scale so the same % is the
        # same green in every cell — independent of cohort size, of which column
        # it sits in, and of whether the cell is currently printing the % or not.
        return (grid_payload_b64(m, total_id_col="Integration Week",
                                 no_sort=True, fixed=True, sortable=False,
                                 center_all=True, heat_cols=_coh_heat, autosize=True,
                                 heat_from=_hfrom, heat_max=100)
                if len(m) else grid_payload_b64(pd.DataFrame()))

    # Customer Usage Cohort (Accounting Sync only) — merged % (count). Passing
    # event_filter=["Accounting Sync"] resolves to _ACTIVE_WEEKS_SYN (sync weeks
    # from the unbounded _SYN table), so uploads no longer count here.
    cnt_df, pct_df = _usage_cohort(event_filter=["Accounting Sync"],
                                   deal_filter=_act_deal, stage_filter=_act_stage, csm_filter=_act_csm)
    state.cs_cohort_count_json = _merged_json(cnt_df, pct_df)

    # Customer Activity Cohort — same shape, sourced from the aia_*_events tables
    # via the Event Name filter. Merged % (count).
    _act_ev = _sel(state.cs_activity_event)
    act_cnt_df, act_pct_df = _usage_cohort(event_filter=_act_ev, deal_filter=_act_deal,
                                           stage_filter=_act_stage, csm_filter=_act_csm)
    state.cs_activity_count_json = _merged_json(act_cnt_df, act_pct_df)


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

    excl = ["Churned","CS Parked","Product Blocked","Integration Failed"]
    paid_active = paid_all[~paid_all["deal_stage"].isin(excl)].copy()
    if len(paid_active):
        # .apply(axis=1) on an empty frame yields a float64 column, which breaks
        # the < today comparison below — so only compute when there are rows.
        paid_active["next_renewal"] = paid_active.apply(_next_renewal, axis=1)
        state.cs_kpi_overdue = paid_active[paid_active["next_renewal"]<today]["record_id"].nunique()
        state.cs_kpi_due_7d  = paid_active[
            (paid_active["next_renewal"]>=today-pd.Timedelta(days=7))
            &(paid_active["next_renewal"]<=today+pd.Timedelta(days=7))]["record_id"].nunique()
    else:
        state.cs_kpi_overdue = 0
        state.cs_kpi_due_7d  = 0

    # #Integration Due (DAX): AIA Paid, paid in range, not activated/adopted,
    # and not in a terminal/done stage. (No integration_done_date requirement.)
    _excl_id = ["Churned","CS Parked","Product Blocked","Integration Failed","Integration Done"]
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
    state.cs_kpi_blocked = paid_all[paid_all["deal_stage"]=="Product Blocked"]["record_id"].nunique()
    state.cs_kpi_rfr     = paid_all[paid_all["deal_stage"]=="Ready for Renewal"]["record_id"].nunique()

    # MRR is set further down from the Revenue Matrix's current-month Total
    # (normalised ÷term, refunds excluded) so the card and the matrix agree.

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
    _crt = _sel(state.cs_selected_rectype)   # Recurring Type filter (line-item level)
    if _crt and "recurring_type" in _li_cs.columns:
        _li_cs = _li_cs[_li_cs["recurring_type"].isin(_crt)]
    _rev_m = _mrr_matrix(_li_cs, _refund_map, "revenue", as_of=today)
    _ret_m = _mrr_matrix(_li_cs, _refund_map, "retention", as_of=today)
    _cs_mrr = _matrix_current_mrr(_rev_m, today)
    state.cs_kpi_mrr = _fmt2(_cs_mrr)
    state.cs_kpi_mrr_exact = f"{_inr(_cs_mrr)} · (Refunds Excluded)"
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

    _excl_uc = ["Churned","CS Parked","Product Blocked","Integration Failed","Integration Done"]
    t1_rows, t2_rows, t3_rows = [], [], []
    _today_ts = pd.Timestamp(date.today()).normalize()
    _month_start_ts = _today_ts.replace(day=1)   # 1st of the current month (for Renewals Collected MTD)
    for csm in sorted(df["cs_owner"].dropna().unique()):
        c   = df[df["cs_owner"]==csm]
        cp  = c[c["payment_date"].notna() & (c["module_type"]=="AIA Paid")]
        mod = c[c["module_type"].notna()]
        int_due    = c[(c["module_type"]=="AIA Paid") & c["payment_date"].notna()
                       & c["activation_date"].isna() & c["adopted_date"].isna()
                       & ~c["deal_stage"].isin(_excl_uc)]["record_id"].nunique()
        int_failed = mod[mod["deal_stage"]=="Integration Failed"]["record_id"].nunique()
        integrated = mod[mod["deal_stage"]=="Integration Done"]["record_id"].nunique()
        # Renewals Collected (MTD): ₹ collected from every Renewal line item of this
        # CSM's deals with date_paid in the current month. NOT de-duped by deal — a
        # deal that pays twice this month (e.g. clearing last month's overdue AND the
        # current cycle) contributes both payments.
        # Amount per line item = unit_price, but for MONTHLY billing the unit_price
        # is the per-month price so it's unit_price × term (# months paid) — same
        # rule as the contract-value calc in export_renewed.py.
        _c_rids = set(c["record_id"].dropna())
        _ren = _AIA_LI[_AIA_LI["record_id"].isin(_c_rids)
                       & (_AIA_LI["recurring_type"] == "Renewal")
                       & _AIA_LI["date_paid"].notna()
                       & (_AIA_LI["date_paid"] >= _month_start_ts)
                       & (_AIA_LI["date_paid"] <= _today_ts)].copy()
        _up   = _ren["unit_price"].fillna(0)
        _term = _ren["term"].where(_ren["term"] > 0, 1).fillna(1)
        _mon  = _ren["billing_frequency"].astype(str).str.lower().str.strip() == "monthly"
        _ren["_amt"] = _up.where(~_mon, _up * _term)
        _ren_total = float(_ren["_amt"].sum())
        # tooltip: one line per payment "₹<amt> (dd Mon, <term>m) <deal>" so a double
        # payment shows twice, and the lines add up to the column total.
        _ren_s = _ren.sort_values("date_paid")
        _ren_deals = []
        for dn, am, dp, tm in _ren_s[["deal_name", "_amt", "date_paid", "term"]].itertuples(index=False):
            _deal = str(dn) if pd.notna(dn) else "(unnamed)"
            _dps  = pd.Timestamp(dp).strftime("%d %b") if pd.notna(dp) else ""
            _tmi  = int(tm) if (pd.notna(tm) and tm and tm > 0) else 1
            _ren_deals.append(f'₹{int(round(am)):,} ({_dps}, {_tmi}m) {_deal}')
        t1_rows.append({
            "CSM":       csm,
            "AIA Paid":  cp["record_id"].nunique(),
            "Int Due":   int(int_due),
            "Int Failed":int(int_failed),
            "Integrated":int(integrated),
            "Product Blocked":   cp[cp["deal_stage"]=="Product Blocked"]["record_id"].nunique(),
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
            "Renewals Collected ₹ (MTD)": round(_ren_total),
            "Renewals Collected Deals": "\n".join(_ren_deals),   # hidden — tooltip source
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
        blank_zeros=True, bar_cols=["Int Due"], bar_color="#f4a98c", autosize=True,
        tip_cols={"Renewals Collected ₹ (MTD)": "Renewals Collected Deals"})
    state.cs_csm_eng_json = grid_payload_b64(
        _with_total(t2_rows, "CSM"), total_id_col="CSM", sort_default_col="ID + RFR + Renewed",
        blank_zeros=True, autosize=True)
    state.cs_csm_health_json = grid_payload_b64(
        _with_total(t3_rows, "CSM"), total_id_col="CSM", sort_default_col="ID + RFR + Renewed",
        blank_zeros=True, bar_cols=["Red Flags Yesterday"], bar_color="#f1a0a0", autosize=True)

    # Cohort tables (Usage + Activity) share the Event/Deal/Stage/CSM filter row
    # and are independent of the rest of this page — build them on their own.
    _build_cohort_tables(state)

    # Usage & Health table — every record with a non-blank payment_date (PBI rule:
    # no integration / module-type / email filter). Paid-but-not-yet-integrated and
    # GST-Paid records show too, with blank Int Date and 0 usage.
    # Built from the FULL _AIA (NOT the top-filtered `df`): this section is
    # self-contained with its own Deal Name / CSM / Deal Stage / Deal Owner
    # filters, so the top-nav CS Owner / Deal Name filters must not scope it (or
    # a stale top selection leaves the usage dropdowns limited to that deal).
    usage_base = _AIA[_AIA["payment_date"].notna()]
    _ev_lu = _recent_event_lookup()   # one in-memory pre-group; no DB, no per-customer rescan
    _scores = _activity_scores()      # {account_id: 28d weighted Activity Score}
    usage_rows = []
    for _, row in usage_base.iterrows():
        email  = _clean_email(row.get("login_email_id",""))
        active_days, streak = _usage_28(email, _ev_lu)
        _acct = _EMAIL_ACCT.get(email)
        activity_score = int(_scores.get(_acct, 0)) if _acct else 0
        intd = row.get("integration_done_date")
        dsince = (today.normalize() - pd.Timestamp(intd).normalize()).days if pd.notna(intd) else 0
        cad = _cadence_of(row)
        _ddmy = lambda v: pd.Timestamp(v).strftime("%d-%b-%y") if pd.notna(v) else ""
        usage_rows.append({
            "Deal Name":       row.get("deal_name",""),
            "record_id":       row.get("record_id",""),
            "Deal Owner":      row.get("deal_owner",""),   # filter only (dropped before render)
            "CSM":             row.get("cs_owner",""),
            "Stage":           row.get("deal_stage",""),
            "Paid On":         _ddmy(row.get("payment_date")),
            "Int Date":        _ddmy(row.get("integration_done_date")),
            # Orange Int Date while the customer is still inside their initial
            # milestone window (days_since <= the cadence's past-initial day);
            # black once they're in steady state. Hidden — drives the cell class.
            "__intcls":        ("cell-orange" if (pd.notna(intd)
                                and dsince <= _CAD_PASTINIT.get(cad, 29)) else ""),
            "Due On":          _due_on(row.get("record_id")),
            "Cadence":         cad,
            "Usage Active Days (28d)": active_days,
            "Activity Score": activity_score,
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
    state.cs_usage_owner_list = (sorted(usage_all["Deal Owner"].dropna().unique().tolist())
                                 if len(usage_all) else [])
    state.cs_usage_cadence_list = (sorted(usage_all["Cadence"].dropna().unique().tolist())
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
        date_cols=["Due On"],
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})
        if len(rwd) else grid_payload_b64(pd.DataFrame()))

# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — MARKETING
# ═══════════════════════════════════════════════════════════════════

# ── Marketing "Daily signals" panel ──────────────────────────────────────────
_FT_TEMPLATES = {"initial_verification", "demo_details", "updated_demo_details"}
_DS_TEMPLATES = {"demo_details", "updated_demo_details"}
_DELIVERED_STATUS = {"delivered", "read"}

def _grp(n):
    """Indian-grouped integer string, NO currency symbol: 1234567 -> '12,34,567'.
    (Distinct from _inr, which prefixes ₹ — keep them separate.)"""
    n = int(round(float(n)))
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(?<=\d)(?=(?:\d\d)+$)", ",", head)
        s = head + "," + tail
    return ("-" if neg else "") + s

def _mad_band(vals):
    """Median and the MAD band: median ± 1.4826·MAD (robust ~1σ). MAD is used
    instead of stddev because ad spend / lead counts are spiky and one outlier day
    would inflate σ enough to swallow a genuine anomaly."""
    a = np.asarray([float(x) for x in vals], dtype=float)
    if a.size == 0:
        return 0.0, 0.0, 0.0
    med = float(np.median(a))
    mad = float(np.median(np.abs(a - med)))
    span = 1.4826 * mad
    return med, med - span, med + span

def _rate_color(v, good, ok):
    return "green" if v >= good else ("amber" if v >= ok else "red")

def _sig_rate_card(title, value_txt, unit, sub, pct, color, date_txt=""):
    pct = max(0.0, min(100.0, float(pct)))
    date_html = f'<span class="dsig-date">{date_txt}</span>' if date_txt else ""
    return (
        f'<div class="dsig-card dsig-{color}">'
        f'<div class="dsig-title"><span class="dsig-name">{title}</span>{date_html}</div>'
        f'<div class="dsig-val dsig-val-{color}">{value_txt}<span class="dsig-unit">{unit}</span></div>'
        f'<div class="dsig-sub">{sub}</div>'
        f'<div class="dsig-bar"><div class="dsig-bar-fill dsig-fill-{color}" style="width:{pct:.2f}%"></div></div>'
        f'</div>')

def _sig_band_card(title, value_txt, date_txt, lo, med, hi, value, is_money, higher_good=True):
    # Colour is direction-aware, not just "in/out of band": red means a BAD surprise, not
    # merely an unusual one. In band -> green (normal). Out of band -> good if it moved the
    # helpful way (leads up / spend down) = green, else red. So a lead spike reads green,
    # a lead drought or a spend blow-out reads red.
    lo = max(0.0, float(lo)); hi = float(hi); med = float(med); value = float(value)
    in_band = (lo <= value <= hi)
    if in_band:
        status = "green"
    else:
        good = (value > hi) == bool(higher_good)   # above & higher-good, or below & lower-good
        status = "green" if good else "red"
    width = (hi - lo) if hi > lo else 1.0
    clamp = lambda x: max(0.0, min(100.0, x))
    pos     = clamp((value - lo) / width * 100.0)
    mid_pos = clamp((med   - lo) / width * 100.0)
    fmt = (lambda x: "₹" + _grp(x)) if is_money else (lambda x: _grp(x))
    date_html = f'<span class="dsig-date">{date_txt}</span>' if date_txt else ""
    return (
        f'<div class="dsig-card dsig-band dsig-{status}">'
        f'<div class="dsig-title"><span class="dsig-name">{title}</span>{date_html}</div>'
        f'<div class="dsig-val">{value_txt}</div>'
        '<div class="dsig-slider">'
        '<div class="dsig-track"></div>'
        f'<div class="dsig-mid" style="left:{mid_pos:.2f}%"></div>'
        f'<div class="dsig-dot dsig-dot-{status}" style="left:{pos:.2f}%"></div>'
        '</div>'
        f'<div class="dsig-scale"><span>{fmt(lo)}</span><span>{fmt(med)}</span><span>{fmt(hi)}</span></div>'
        '</div>')

def _daily_signals_html():
    """Build the 6 'Daily signals' cards. Funnel cards (LP traffic-to-lead, First-touch,
    DS follow-up, WhatsApp delivered) are for TODAY (IST); the two Google band cards
    (spend, leads) are for YESTERDAY with a trailing-28-day median ± MAD band. aia_live
    (Neon) joins to Conversations (Supabase) on the last-10-digit phone; POC history for
    the first-touch template gate is bounded to the 45-day Conversations pull."""
    now_ist = datetime.now(_IST)
    today = pd.Timestamp(now_ist.date())
    yday  = today - pd.Timedelta(days=1)
    aia, conv, ga, mkt = _AIA, _CONV, _GA, _MKT

    def _live(df):
        return df[df["is_deleted"] != "Yes"] if "is_deleted" in df.columns else df

    # outbound messages grouped by last-10 phone (for the three messaging cards)
    by_phone = {}
    if len(conv):
        cout = conv[conv["direction"] == "outbound"]
        by_phone = {p: g for p, g in cout.groupby("p10") if p}

    # Card 2 — First-touch sent (today's created deals)
    dt = _live(_rng(aia, "create_date", today, today)).copy()
    ft_den = int(dt["record_id"].nunique()) if len(dt) else 0
    ft_num = 0
    deliver_flags = []           # one per first-touch deal -> feeds the delivered card
    if len(dt):
        dt["p10"] = dt["poc_number"].apply(_phone10) if "poc_number" in dt.columns else ""
        for _, r in dt.iterrows():
            p = r["p10"]; cdate = r["create_date"]
            pf = by_phone.get(p)
            if pf is None or pd.isna(cdate):
                continue
            outs  = pf[pf["msg_date"] >= cdate]     # outbound after the deal was created
            prior = pf[pf["msg_date"] <  cdate]     # any earlier thread => repeat POC
            is_repeat = len(prior) > 0
            cand = outs if is_repeat else outs[outs["template_name"].isin(_FT_TEMPLATES)]
            if len(cand):
                ft_num += 1
                first = cand.sort_values("timestamp").iloc[0]
                deliver_flags.append(str(first["delivery_status"]) in _DELIVERED_STATUS)
    ft_rate = (100.0 * ft_num / ft_den) if ft_den else 0.0

    # Card 4 — WhatsApp delivered (of the first-touch messages that went out)
    del_den = len(deliver_flags)
    del_num = int(sum(deliver_flags))
    del_rate = (100.0 * del_num / del_den) if del_den else 0.0

    # Card 3 — DS follow-up sent (today's demos booked)
    ds = _live(_rng(aia, "ds_date", today, today)).copy()
    ds_den = int(ds["record_id"].nunique()) if len(ds) else 0
    ds_num = 0
    if len(ds):
        ds["p10"] = ds["poc_number"].apply(_phone10) if "poc_number" in ds.columns else ""
        for _, r in ds.iterrows():
            p = r["p10"]; bdate = r["ds_date"]
            pf = by_phone.get(p)
            if pf is None or pd.isna(bdate):
                continue
            cand = pf[(pf["msg_date"] >= bdate) & (pf["template_name"].isin(_DS_TEMPLATES))]
            if len(cand):
                ds_num += 1
    ds_rate = (100.0 * ds_num / ds_den) if ds_den else 0.0

    # gads leads (deal_source contains GAds/Google), non-deleted, dated
    def _gads_live(df):
        d = _live(df)
        if "deal_source" in d.columns:
            d = d[d["deal_source"].astype(str).str.contains("gads|google", case=False, na=False)]
        return d
    gl = _gads_live(aia).copy()
    gl["_d"] = pd.to_datetime(gl["create_date"], errors="coerce").dt.normalize()

    ydtxt = yday.strftime("%d %b")

    # Card 1 — LP traffic-to-lead (YESTERDAY): gads leads / paid gads sessions.
    # Yesterday (not today) because GA sessions land ~a day late.
    lp_den = 0
    if len(ga):
        g = ga.copy()
        g["_d"] = pd.to_datetime(g["date"], errors="coerce").dt.normalize()
        sel = g[(g["_d"] == yday)
                & (g["hostname"].astype(str) == "www.aiaccountant.com")
                & (g["landing_page"].astype(str).str.contains("gads", case=False, na=False))]
        lp_den = int(pd.to_numeric(sel["sessions"], errors="coerce").fillna(0).sum())
    lp_num = int(gl[gl["_d"] == yday]["record_id"].nunique())
    lp_rate = (100.0 * lp_num / lp_den) if lp_den else 0.0

    # Band = the last 7 days PREVIOUS TO yesterday (yesterday itself excluded), so
    # yesterday is judged against the past week of clean history — a genuine spike/drop
    # yesterday isn't diluted by being counted in its own band. So if yesterday's leads
    # jump to 54 while the prior 7 days top out at 49, it reads OUT of band (dot right, red).
    _BAND_DAYS = 7
    band_start = yday - pd.Timedelta(days=_BAND_DAYS)  # yday-7 .. yday-1  (7 days)
    band_end   = yday - pd.Timedelta(days=1)
    band_idx   = pd.date_range(band_start, band_end, freq="D")

    # Card 6 — Google leads: yesterday's value vs the prior-28-day band
    leads_val = int(gl[gl["_d"] == yday]["record_id"].nunique())
    ld = gl[(gl["_d"] >= band_start) & (gl["_d"] <= band_end)]
    ld_daily = ld.groupby("_d")["record_id"].nunique().reindex(band_idx, fill_value=0)
    l_med, l_lo, l_hi = _mad_band(ld_daily.values)

    # Card 5 — Google spend: yesterday's value vs the prior-28-day band
    gs = mkt.copy()
    if "channel" in gs.columns:
        gs = gs[gs["channel"] == "Google Ads"]
    spend_val = 0.0; s_med = s_lo = s_hi = 0.0
    if {"day", "cost"}.issubset(gs.columns) and len(gs):
        gs["_d"] = pd.to_datetime(gs["day"], errors="coerce").dt.normalize()
        spend_val = float(gs.loc[gs["_d"] == yday, "cost"].sum())
        spb = gs[(gs["_d"] >= band_start) & (gs["_d"] <= band_end)]
        sp_daily = spb.groupby("_d")["cost"].sum().reindex(band_idx, fill_value=0.0)
        s_med, s_lo, s_hi = _mad_band(sp_daily.values)
    spend_date = ydtxt

    cards = [
        _sig_rate_card("LP Traffic-to-Deal (Google)", f"{lp_rate:.2f}", "%",
                       f"{lp_num} of {_grp(lp_den)} sessions", lp_rate,
                       _rate_color(lp_rate, 0.8, 0.4), ydtxt),
        _sig_rate_card("First-touch sent", f"{ft_rate:.1f}", "%",
                       f"{ft_num} of {ft_den} deals", ft_rate,
                       _rate_color(ft_rate, 90, 75)),
        _sig_rate_card("DS follow-up sent", f"{ds_rate:.1f}", "%",
                       f"{ds_num} of {ds_den} demos", ds_rate,
                       _rate_color(ds_rate, 90, 75)),
        _sig_rate_card("WhatsApp delivered", f"{del_rate:.1f}", "%",
                       f"{del_num} of {del_den} sent", del_rate,
                       _rate_color(del_rate, 90, 75)),
        _sig_band_card("Google spend", "₹" + _grp(spend_val), spend_date,
                       s_lo, s_med, s_hi, spend_val, True, higher_good=False),
        _sig_band_card("Google leads", str(leads_val), ydtxt,
                       l_lo, l_med, l_hi, leads_val, False, higher_good=True),
    ]
    head = (f'<div class="dsig-head">Daily signals '
            f'<span>{today.strftime("%d %b %Y")}</span></div>')
    return ('<div class="dsig-panel">' + head
            + '<div class="dsig-grid">' + "".join(cards) + '</div></div>')


def _mkt_refresh(state):
    try:
        state.mkt_signals_html = _daily_signals_html()
    except Exception as ex:
        print(f"[WARN] daily signals failed: {ex}")
        state.mkt_signals_html = ""
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
    state.va_kpi_revenue     = _fmt2(rev)
    state.va_kpi_revenue_exact = f"{_inr(rev)} · Acquired amount (includes Refunds)"
    # MRR (va_kpi_mrr) is set below from the GM Performance Total row (acquired MRR).
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
    # Funnel HI: any cohort lead with an eta_pay_date in range (same rule as AIA);
    # funnel-only — the "Agreed" KPI card keeps its own High-Intent-stage definition.
    hi2  = coh[coh["eta_pay_date"].notna()&(coh["eta_pay_date"]>=s)&(coh["eta_pay_date"]<=e)]["record_id"].nunique()
    paid2= coh[coh["payment_date"].notna()&(coh["payment_date"]>=s)&(coh["payment_date"]<=e)]["record_id"].nunique()
    p = lambda n: f"{n/leads*100:.0f}%" if leads else "0%"
    _vlabels = [f"<b>{leads}</b>", f"<b>{ds2} ({p(ds2)})</b>", f"<b>{dc2} ({p(dc2)})</b>",
                f"<b>{hi2} ({p(hi2)})</b>", f"<b>{paid2} ({p(paid2)})</b>"]
    state.va_funnel_fig = _make_funnel(
        ["Leads", "DS", "DC", "HI", "Paid"],
        [leads, ds2, dc2, hi2, paid2], _vlabels)

    # Scheduled/Conducted trend — same DS (blue) behind DC (orange) overlay as AIA
    # Ops, minus the Qualified line (VA has no qualified metric). Capped at today.
    e_cap = min(e, pd.Timestamp(date.today()))
    dc_sub = _rng(df,"dc_date",s,e_cap).copy(); dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    ds_sub = _rng(df,"ds_date",s,e_cap).copy(); ds_sub["date"] = ds_sub["ds_date"].dt.normalize()
    daily_ds = ds_sub.groupby("date")["record_id"].nunique().reset_index(name="DS")
    trend = (pd.DataFrame({"date":pd.date_range(s,e_cap,freq="D")})
             .merge(daily_ds,on="date",how="left").merge(daily_dc,on="date",how="left").fillna(0))
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    trend = trend.astype({"DS":int,"DC":int})
    state.va_trend_fig = _make_trend(trend["date_label"].tolist(),
                                     trend["DS"].tolist(), trend["DC"].tolist())

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
            "HI (ATP)":_rng(o,"eta_pay_date",s,e).query("deal_stage=='High Intent'")["record_id"].nunique(),
            "Paid":pd2["record_id"].nunique(),
            "Revenue":int(pd2["amount_paid"].sum()+pd2["ot_amount_paid"].sum()),
            "MRR":_va_mrr(pd2["record_id"]),
            "ATP":_atp_amount_va(o, s, e)})
    va_gm = pd.DataFrame(rows)
    if len(va_gm):
        tot = va_gm.select_dtypes("number").sum().to_dict(); tot["GM"]="Total"
        va_gm = pd.concat([va_gm, pd.DataFrame([tot])], ignore_index=True)
    # MRR KPI = acquired MRR from the GM Performance Total row (excludes one-time, incl refunds).
    _vgm_mrr = int(va_gm.iloc[-1]["MRR"]) if len(va_gm) else 0
    state.va_kpi_mrr = _fmt2(_vgm_mrr)
    state.va_kpi_mrr_exact = f"{_inr(_vgm_mrr)} · Acquired MRR (includes Refunds but excludes One-time amounts)"
    state.va_gm_json = grid_payload_b64(va_gm, "GM", bar_cols=["HI (ATP)", "ATP"],
                                        fixed=True, autosize=True, first_col_w=250,
                                        header_tips={"HI (ATP)": "Active HI deals with payment ETA in the selected period"})

    rows2 = []
    _utm_src = coh["utm_source_cohort"].fillna("(Blank)")
    for src in sorted(_utm_src.unique()):
        c = coh[_utm_src==src]; l2 = c["record_id"].nunique()
        if l2==0: continue
        # DAX cohPaid: BOTH create_date AND payment_date must be in [s, e]
        coh_paid = c[c["payment_date"].notna()&(c["payment_date"]>=s)&(c["payment_date"]<=e)]
        rows2.append({"UTM":src,"Leads":l2,
            "DC":c[c["dc_date"].notna()&(c["dc_date"]>=s)&(c["dc_date"]<=e)]["record_id"].nunique(),
            "HI (ATP)":c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
            "Paid":coh_paid["record_id"].nunique(),
            "Revenue":int(coh_paid["amount_paid"].sum()+coh_paid.get("ot_amount_paid", pd.Series(0, index=coh_paid.index)).sum()),
            "MRR":_va_mrr(coh_paid["record_id"]),
            "ATP":_atp_amount_va(c, s, e)})
    va_utm = pd.DataFrame(rows2)
    if len(va_utm):
        tot2 = va_utm.select_dtypes("number").sum().to_dict(); tot2["UTM"]="Total"
        va_utm = pd.concat([va_utm, pd.DataFrame([tot2])], ignore_index=True)
    state.va_utm_json = grid_payload_b64(va_utm, "UTM", bar_cols=["HI (ATP)", "ATP"],
                                         fixed=True, first_col_w=250,
                                         header_tips={"HI (ATP)": "Active HI deals with payment ETA in the selected cohort"})

    _inc = _va_incentive(s, e)
    state.va_incentive_json = (grid_payload_b64(_inc, total_id_col="AM",
                               sort_default_col="Total MRR",
                               autosize=True, first_col_w=220,
                               center_cols=["One-time Collected", "MRR Collected", "Total MRR"],
                               heat_cols={"Total MRR": "green"},
                               header_tips={"MRR Collected": "Renewal MRR spread across the month(s) in the date filter",
                                            "Total MRR": "One-time Collected + MRR Collected for the selected month(s)"})
                               if len(_inc) else grid_payload_b64(pd.DataFrame()))

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

# ── Accounts Receivable Tracker ─────────────────────────────────────
# One row per VA deal that has recurring (New/Renewal) line items. Each paid
# line item covers `span` months from its billing_start_date; a deal is judged
# purely on whether it's currently up to date with its most-recent coverage.
_AR_FREQ_SPAN = {"bi_monthly": 2, "quarterly": 3, "per_six_months": 6, "annually": 12}

def _ar_span(freq, term):
    """Coverage span (months) of one line item: monthly → its `term`
    (so a 2-month monthly line covers 2), every other cadence a fixed block."""
    if freq == "monthly":
        t = int(term) if (pd.notna(term) and term) else 1
        return max(1, t)
    return _AR_FREQ_SPAN.get(freq, max(1, int(term) if (pd.notna(term) and term) else 1))

def _ar_build_base():
    """Full AR table across every VA deal with recurring line items.
    Status logic (non-churned):
      • overdue period = a due period (start ≤ today) with no covering line item
      • Pending  → the ONLY uncovered-due period is the current/most-recent one
                   (clean record that just lapsed) — orange
      • Overdue  → any older gap, or 2+ uncovered periods (accumulated dues) — red
      • Upcoming → fully paid up, next coverage ends within 14 days
      • Collected→ fully paid up, next coverage ends > 14 days out
    Dues/Outstanding: Overdue/Pending = uncovered periods × (span, unit_price);
    Upcoming = the one upcoming period; Collected/Churned = blank."""
    today = pd.Timestamp(date.today())
    li, va = _VA_LI, _VA
    if li is None or len(li) == 0 or "recurring_type" not in li.columns:
        return pd.DataFrame()
    rec = li[li["recurring_type"].isin(["New", "Renewal"])].copy()
    rec = rec.dropna(subset=["record_id", "billing_start_date"])
    if len(rec) == 0:
        return pd.DataFrame()
    vacols = ["record_id", "deal_name", "am_owner", "deal_owner", "deal_stage", "payment_date"]
    vshow = (va[[c for c in vacols if c in va.columns]]
             .drop_duplicates("record_id").set_index("record_id"))
    rows = []
    for rid, g in rec.groupby("record_id"):
        g = g.sort_values("billing_start_date")
        starts = list(g["billing_start_date"])
        spans  = [_ar_span(r.billing_frequency, r.term) for r in g.itertuples()]
        prices = list(g["unit_price"])
        covered, cov_end = set(), None
        for st, sp in zip(starts, spans):
            for k in range(sp):
                covered.add((st + relativedelta(months=k)).to_period("M"))
            end = st + relativedelta(months=sp)
            cov_end = end if (cov_end is None or end > cov_end) else cov_end
        first_billing = starts[0]
        latest_span   = spans[-1]
        latest_price  = prices[-1] if prices else 0

        # blank owner/AM/stage -> "—" (a real, selectable value) instead of "",
        # which _sel() treats as "no filter" so the blank option can't filter.
        def _v(col):
            if rid in vshow.index and col in vshow.columns:
                x = vshow.loc[rid, col]
                if isinstance(x, str):
                    x = x.strip()
                    return x if x else "—"
                if pd.notna(x):
                    return x
            return "—"
        deal_name = _v("deal_name")
        if deal_name == "—" and "deal_name" in g.columns and pd.notna(g["deal_name"].iloc[0]):
            deal_name = str(g["deal_name"].iloc[0])
        stage = _v("deal_stage")
        pay1  = vshow.loc[rid, "payment_date"] if (rid in vshow.index and "payment_date" in vshow.columns) else pd.NaT
        churned = isinstance(stage, str) and "churn" in stage.lower()

        # walk the deal's expected periods (cadence = latest line's span)
        due_periods, overdue = [], []
        k = 0
        while k <= 600:
            pstart = first_billing + relativedelta(months=latest_span * k)
            if pstart > today:
                break
            due_periods.append(pstart)
            if pstart.to_period("M") not in covered:
                overdue.append(pstart)
            k += 1

        if churned:
            status, dues, outstanding, next_due = "Churned", np.nan, np.nan, pd.NaT
        elif overdue:
            n = len(overdue)
            dues, outstanding, next_due = n * latest_span, n * latest_price, cov_end
            last_due = due_periods[-1] if due_periods else None
            status = "Pending" if (n == 1 and overdue[0] == last_due) else "Overdue"
        elif cov_end is not None and cov_end <= today + pd.Timedelta(days=14):
            status, dues, outstanding, next_due = "Upcoming", latest_span, latest_price, cov_end
        else:
            status, dues, outstanding, next_due = "Collected", np.nan, np.nan, cov_end

        rows.append({
            "record_id": rid, "Deal Name": deal_name, "AM": _v("am_owner"),
            "Deal Owner": _v("deal_owner"), "Deal Stage": stage,
            "1st Payment Date": pay1, "Pending Dues (Months)": dues,
            "Outstanding Amount": outstanding, "Due Status": status,
            "Next Due Date": next_due,
        })
    return pd.DataFrame(rows)

_AR_STATUS_RANK = {"Overdue": 0, "Pending": 1, "Upcoming": 2, "Collected": 3, "Churned": 4}

def _ar_refresh(state):
    base = _ar_build_base()
    state.vaf_ar_all = base
    d = base
    if len(d):
        for col, sv in (("Deal Name", state.vaf_ar_deal), ("Deal Stage", state.vaf_ar_stage),
                        ("AM", state.vaf_ar_am), ("Deal Owner", state.vaf_ar_owner),
                        ("Due Status", state.vaf_ar_status)):
            s = _sel(sv)
            if s and col in d.columns:
                d = d[d[col].isin(s)]
    if len(d):
        d = d.assign(_r=d["Due Status"].map(_AR_STATUS_RANK).fillna(9),
                     _o=d["Outstanding Amount"].fillna(-1))
        d = d.sort_values(["_r", "_o"], ascending=[True, False]).drop(columns=["_r", "_o"])
        _dt = lambda x: x.strftime("%d-%b-%y") if pd.notna(x) else ""
        disp = pd.DataFrame({
            "Deal Name": d["Deal Name"].values,
            "record_id": d["record_id"].values,
            "AM": d["AM"].values,
            "Deal Owner": d["Deal Owner"].values,
            "Deal Stage": d["Deal Stage"].values,
            "1st Payment Date": d["1st Payment Date"].apply(_dt).values,
            "Pending Dues (Months)": d["Pending Dues (Months)"].values,
            "Outstanding Amount": d["Outstanding Amount"].values,
            "Due Status": d["Due Status"].values,
            "Next Due Date": d["Next Due Date"].apply(_dt).values,
        })
        state.vaf_ar_json = grid_payload_b64(
            disp, no_sort=True, autosize=True, max_height=560,
            center_cols=["1st Payment Date", "Pending Dues (Months)", "Outstanding Amount",
                         "Due Status", "Next Due Date"],
            status_cols=["Due Status"], date_cols=["1st Payment Date", "Next Due Date"],
            link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})
    else:
        state.vaf_ar_json = grid_payload_b64(pd.DataFrame())

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
    _vrt = _sel(state.vaf_selected_rectype)   # Recurring Type filter (line-item level)
    if _vrt and "recurring_type" in li.columns:
        li = li[li["recurring_type"].isin(_vrt)]

    paid = df[df["payment_date"].notna()]
    # Total Customers — every paid customer, churned included.
    state.vaf_kpi_active  = paid["record_id"].nunique()
    # Refunds — deals asked_refund=Yes; filter-aware (respects Deal Name / Line
    # Item filters via df), same as the CS Finance Refunds card.
    state.vaf_kpi_refunds = (df[df["asked_refund"] == "Yes"]["record_id"].nunique()
                             if "asked_refund" in df.columns else 0)
    # Total Revenue = sum of every line item's unit_price (the full billed value
    # across recurring + one-time), respecting the Deal / Line Item filters.
    _va_rev = int(li["unit_price"].sum())
    state.vaf_kpi_revenue = _fmt2(_va_rev)
    state.vaf_kpi_revenue_exact = f"{_inr(_va_rev)} · Total Contract value & not MRR"
    cycle_map = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}
    # MRR is set further down from the Revenue Matrix's current-month Total
    # (normalised ÷term) so the card and the matrix agree.

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

    # Refunds-adjusted (same as CS): drop every line item whose deal is
    # asked_refund=Yes, so its revenue leaves every cell and it isn't retained.
    _v_refund_map = None
    if "asked_refund" in _VA.columns:
        _v_refund_map = (_VA.dropna(subset=["record_id"]).drop_duplicates("record_id")
                            .set_index("record_id")["asked_refund"])
    _vrev = _mrr_matrix(li, _v_refund_map, "revenue", add_onetime=True, as_of=today, add_new=True)   # VA: + New Collection / One-time rows
    _vret = _mrr_matrix(li, _v_refund_map, "retention", add_onetime=True, as_of=today, add_new=True)
    # MRR KPI reads the raw "Total" row (recurring MRR) — compute it BEFORE the
    # matrix is renamed/re-laid-out below.
    _va_mrr = _matrix_current_mrr(_vrev, today, exclude_onetime=True)
    state.vaf_kpi_mrr = _fmt2(_va_mrr)
    state.vaf_kpi_mrr_exact = f"{_inr(_va_mrr)} · Excludes One-time amount & Refunds"
    # Re-lay-out for display: cohorts, then the recurring Total (renamed), then the
    # cash memo rows, then a Collected total that sums ONLY the three cash rows.
    #   revenue  : "Total MRR"       + "Total Collected"    (₹)
    #   retention: "Total Recurring" + "Total Transactions" (counts)
    def _finalize_va_matrix(m, total_label, collected_label, collected_values=None):
        if m is None or not len(m):
            return m
        _memo = ["New Collection", "Fresh Renewals", "One-time"]
        _cols = [c for c in m.columns if c != "Cohort"]
        cohorts = m[~m["Cohort"].isin(_memo + ["Total"])].copy()
        totrow  = m[m["Cohort"] == "Total"].copy()
        totrow["Cohort"] = total_label                       # rename recurring Total
        memo    = m[m["Cohort"].isin(_memo)].copy()
        coll = {"Cohort": collected_label}
        for c in _cols:
            # revenue → sum the cash rows (₹). retention → distinct payers, so a
            # customer paying two types in a month counts once (collected_values).
            coll[c] = (int(collected_values.get(c, 0)) if collected_values is not None
                       else int(pd.to_numeric(memo[c], errors="coerce").fillna(0).sum()))
        return pd.concat([cohorts, totrow, memo, pd.DataFrame([coll])], ignore_index=True)
    _vrev = _finalize_va_matrix(_vrev, "Total MRR", "Total Collected")
    _ret_cols = [c for c in _vret.columns if c != "Cohort"] if len(_vret) else []
    _vret_distinct = _distinct_payers_by_month(li, _v_refund_map, _ret_cols)
    _vret = _finalize_va_matrix(_vret, "Total Recurring", "Total Payments",
                                collected_values=_vret_distinct)
    _vrev_heat = {c: "green" for c in _vrev.columns if c != "Cohort"} if len(_vrev) else {}
    _vret_heat = {c: "green" for c in _vret.columns if c != "Cohort"} if len(_vret) else {}
    state.vaf_revenue_matrix_json   = (grid_payload_b64(_vrev, total_id_col="Cohort",
                                       blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                       autosize=True, heat_cols=_vrev_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                       heat_by_row=True, total_inline=True)
                                       if len(_vrev) else grid_payload_b64(pd.DataFrame()))
    state.vaf_retention_matrix_json = (grid_payload_b64(_vret, total_id_col="Cohort",
                                       blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                       autosize=True, heat_cols=_vret_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                       heat_by_row=True, total_inline=True)
                                       if len(_vret) else grid_payload_b64(pd.DataFrame()))

    # Parked / Churned reason breakdowns — paid customers only (payment_date
    # known), independent of the page filters (these are deal-stage roll-ups of
    # va_live, not line-item views). Reason × AM, counted, most-common first.
    _paid_va = _VA[_VA["payment_date"].notna()]
    def _reason_tbl(stage, reason_col, count_name):
        s = _paid_va[_paid_va["deal_stage"].astype(str).str.lower() == stage]
        if len(s) == 0 or reason_col not in s.columns:
            return pd.DataFrame()
        t = pd.DataFrame({
            "ReasonFull": s[reason_col].astype(str).str.strip().replace(
                {"": "—", "None": "—", "nan": "—"}),
            "AM": (s["am_owner"].astype(str).str.strip().replace(
                {"": "—", "None": "—", "nan": "—"}) if "am_owner" in s.columns else "—"),
        })
        g = t.groupby(["ReasonFull", "AM"]).size().reset_index(name=count_name)
        g = g.sort_values(count_name, ascending=False).reset_index(drop=True)
        # truncate the reason in-cell; full text stays available as a hover tooltip
        g.insert(0, "Reason", g["ReasonFull"].map(
            lambda x: x if len(x) <= 40 else x[:39].rstrip() + "…"))
        return g[["Reason", "ReasonFull", "AM", count_name]]
    _parked  = _reason_tbl("parked",  "va_parked_reason", "Parked")
    _churned = _reason_tbl("churned", "churned_reason",   "Churned")
    state.vaf_parked_json  = (grid_payload_b64(_parked, no_sort=True,
                              center_cols=["AM", "Parked"], tip_cols={"Reason": "ReasonFull"})
                              if len(_parked) else grid_payload_b64(pd.DataFrame()))
    state.vaf_churned_json = (grid_payload_b64(_churned, no_sort=True,
                              center_cols=["AM", "Churned"], tip_cols={"Reason": "ReasonFull"})
                              if len(_churned) else grid_payload_b64(pd.DataFrame()))

    if len(li) > 0:
        li3 = li.dropna(subset=["date_paid"]).copy()
        li3["BillingMonth"] = li3["date_paid"].dt.to_period("M").astype(str)
        t = li3.groupby("BillingMonth")["unit_price"].sum().reset_index(); t.columns=["BillingMonth","Revenue"]
        state.vaf_revenue_trend_df = t.sort_values("BillingMonth").reset_index(drop=True)
    else:
        state.vaf_revenue_trend_df = pd.DataFrame()

    rw = paid2[(paid2["next_renewal"]>=today-pd.Timedelta(days=14))
               &(paid2["next_renewal"]<=today+pd.Timedelta(days=14))]
    if "deal_stage" in rw.columns:                     # drop Churned deals from the window
        rw = rw[rw["deal_stage"] != "Churned"]
    rw = rw.sort_values("next_renewal")
    rwd = pd.DataFrame({
        "Due On":    rw["next_renewal"].dt.strftime("%d-%b-%y"),
        "Deal Name": rw.get("deal_name", ""),
        "record_id": rw["record_id"].values,
        "GM":        rw.get("deal_owner", ""),          # GM = deal owner
        "POC Number": rw["poc_number"] if "poc_number" in rw.columns else pd.Series("", index=rw.index),
        "POC Email": rw.get("poc_email", ""),
        "Stage":     rw.get("deal_stage", ""),
        "Amount":    rw.get("amount_paid", 0),
    })
    state.vaf_renewal_json = (grid_payload_b64(rwd, no_sort=True,
                              center_cols=["Due On", "Amount"], autosize=True,
                              date_cols=["Due On"],
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

# Page 1
aia_start_date = _month_start;  aia_end_date = _month_end
aia_date_range = [_month_start, _month_end]   # single-box range picker <-> start/end
aia_owner_list    = sorted(_AIA["deal_owner"].dropna().unique().tolist())
aia_campaign_list = sorted(_AIA["utm_campaign"].dropna().unique().tolist())
aia_selected_owner = [];  aia_selected_campaign = []
aia_kpi_leads=0; aia_kpi_ds=0; aia_kpi_dc=0; aia_kpi_hi=0
aia_kpi_aia_paid=0; aia_kpi_gst_paid=0; aia_kpi_paid=0; aia_kpi_refunds=0
aia_kpi_parked=0; aia_kpi_discards=0; aia_kpi_closed_lost=0
aia_kpi_collected="₹0"; aia_kpi_collected_exact="₹0"; aia_kpi_mrr="₹0"; aia_kpi_mrr_exact="₹0"
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
cs_rectype_list = sorted(_AIA_LI["recurring_type"].dropna().unique().tolist()) if "recurring_type" in _AIA_LI.columns else []
cs_selected_owner=[]; cs_selected_deal=[]; cs_selected_rectype=[]
cs_kpi_paid_all=0; cs_kpi_overdue=0; cs_kpi_due_7d=0; cs_kpi_int_due=0
cs_kpi_renewed=0; cs_kpi_refunds=0; cs_kpi_blocked=0; cs_kpi_rfr=0
cs_kpi_aia_paid=0; cs_kpi_mrr="₹0"; cs_kpi_mrr_exact="₹0"; cs_kpi_active=0
cs_revenue_matrix_json=""; cs_retention_matrix_json=""; cs_csm_aia_json=""
cs_csm_eng_json=""; cs_csm_health_json=""
cs_cohort_count_json=""; cs_usage_json=""
cs_usage_all=pd.DataFrame(); cs_usage_deal=[]; cs_usage_csm=[]; cs_usage_stage=[]; cs_usage_owner=[]; cs_usage_status=[]; cs_usage_cadence=[]
cs_usage_deal_list=[]; cs_usage_csm_list=[]; cs_usage_stage_list=[]; cs_usage_owner_list=[]; cs_usage_cadence_list=[]
cs_renewal_window_json=""
# Customer Activity Cohort (14 tracked events across the 5 aia_*_events tables)
cs_activity_event_list = [
    "Login", "Dashboard Viewed", "Upload", "Delete", "Accounting Sync",
    "Transaction Status", "Transaction Ledger Updated", "Transaction Type Updated",
    "Entity Created", "Invoice Created", "Invoice Bulk Edited",
    "Vendor Mismatch Resolved", "Recon Processed", "Mapping Completed",
]
cs_activity_event = []   # [] = All Events
cs_activity_count_json = ""
# View mode for BOTH cohort tables: All (count + %) / Cohort % / Customers
cs_cohort_view_list = ["Cohort %", "Customers"]
cs_cohort_view = []
# Deal Name / Deal Stage / CSM filters, scoped to the cohort's own base population
# (integrated AIA Paid records) so every option can actually match a row.
_act_base_mask = (_AIA["integration_done_date"].notna()) & (_AIA["module_type"] == "AIA Paid")
cs_activity_deal_list  = sorted(_AIA[_act_base_mask]["deal_name"].dropna().unique().tolist())
cs_activity_stage_list = sorted(_AIA[_act_base_mask]["deal_stage"].dropna().unique().tolist())
cs_activity_csm_list   = sorted(_AIA[_act_base_mask]["cs_owner"].dropna().unique().tolist())
cs_activity_deal = []; cs_activity_stage = []; cs_activity_csm = []

# ── Matrix explainer tooltips (ⓘ next to each matrix heading) ────────────────
# Multi-line bullet text lives in vars (the inline control syntax can't hold line
# breaks); .MuiTooltip-tooltip has white-space: pre-line so the \n render as lines.
cs_rev_tip = ("Revenue Matrix (₹)\n"
              "• Cohort Spread: Based on MRR\n"
              "• Fresh Renewals: Monthly cash collected\n"
              "• Total: Sum of cohort MRR")
cs_ret_tip = ("Customer Retention Matrix\n"
              "• Cohort Spread: Based on recurring customers by term\n"
              "• Fresh Renewals: Customers who paid that month\n"
              "• Total: Sum of recurring customers")
cs_usage_tip = ("Usage Streak — last 28 days\n"
                "• Green = Accounting Sync that day\n"
                "• Yellow = any other event (uploads, transactions, invoices, recon, logins…)\n"
                "• Grey = not active (no event that day)\n"
                "Usage Active Days (28d) = number of active days (green + yellow); grey days are not active.\n"
                "Hover a dot for that day's event counts.")
vaf_rev_tip = ("Revenue Matrix (₹)\n"
               "• Cohort Spread: Based on MRR + one-time revenue\n"
               "• Fresh Renewals: Monthly cash collected\n"
               "• Total MRR: Sum of MRR + one-time revenue (excludes Fresh Renewals)\n"
               "• Total Collected: New + Fresh Renewals + One-time (all cash collected)")
vaf_ret_tip = ("Customer Retention Matrix\n"
               "• Cohort Spread: Based on recurring + one-time customers\n"
               "• Fresh Renewals: Customers who paid that month (includes one-time)\n"
               "• Total Recurring: Sum of recurring + one-time customers\n"
               "• Total Payments: distinct customers who paid that month")

# Page 3
mkt_start_date = date(2020,1,1); mkt_end_date = _today   # no date filter on Marketing page (all-time)
mkt_kpi_spend="₹0"; mkt_kpi_leads="0"; mkt_kpi_cpl="₹0"; mkt_kpi_cac="₹0"
mkt_kpi_arpu="₹0"; mkt_kpi_payback="—"
mkt_signals_html=""
mkt_monthly_json=""; mkt_weekly_json=""; mkt_spend_df=pd.DataFrame(); mkt_cpl_df=pd.DataFrame()
mkt_channel_spend_json=""; mkt_channel_leads_json=""
mkt_channel_filter="All"; mkt_filter_label=""
mkt_channel_click=""; mkt_channel_click_last=""; mkt_leads_click=""; mkt_leads_click_last=""

# Page 4
va_start_date = _month_start;  va_end_date = _month_end
va_date_range = [_month_start, _month_end]    # single-box range picker <-> start/end
va_owner_list    = sorted(_VA["deal_owner"].dropna().unique().tolist())
va_campaign_list = sorted(_VA["utm_campaign"].dropna().unique().tolist())
va_selected_owner=[]; va_selected_campaign=[]
va_kpi_leads=0; va_kpi_ds=0; va_kpi_dc=0; va_kpi_hi=0; va_kpi_paid=0
va_kpi_discards=0; va_kpi_parked=0; va_kpi_closed_lost=0
va_kpi_revenue="₹0"; va_kpi_revenue_exact="₹0"; va_kpi_mrr="₹0"; va_kpi_mrr_exact="₹0"; va_kpi_eom="0"
va_funnel_fig=go.Figure(); va_trend_fig=go.Figure(); va_channel_pie_json=""
va_channel_filter="All"; va_filter_label=""; va_channel_click=""; va_channel_click_last=""
va_gm_json=""; va_utm_json=""; va_incentive_json=""
va_discard_df=pd.DataFrame(); va_lost_df=pd.DataFrame(); va_parked_df=pd.DataFrame()

# Page 5
vaf_deal_list = sorted(_VA_LI["deal_name"].dropna().unique().tolist())  # from line items (matrix source)
vaf_rectype_list = sorted(_VA_LI["recurring_type"].dropna().unique().tolist()) if "recurring_type" in _VA_LI.columns else []
vaf_line_item_list = (sorted(_VA_LI["line_item_name"].dropna().unique().tolist())
                      if "line_item_name" in _VA_LI.columns else [])
vaf_selected_deal=[]; vaf_selected_line_item=[]; vaf_selected_rectype=[]
vaf_kpi_active=0; vaf_kpi_refunds=0; vaf_kpi_revenue="₹0"; vaf_kpi_revenue_exact="₹0"; vaf_kpi_mrr="₹0"; vaf_kpi_mrr_exact="₹0"; vaf_kpi_due_14d=0
vaf_revenue_matrix_json=""; vaf_retention_matrix_json=""
vaf_revenue_trend_df=pd.DataFrame(); vaf_renewal_json=""
vaf_parked_json=""; vaf_churned_json=""
# Accounts Receivable Tracker — its own 5 cross-filtering dropdowns, independent
# of the page's top filter bar (mirrors the CS Usage & Health table pattern).
vaf_ar_all=pd.DataFrame(); vaf_ar_json=""
vaf_ar_deal=[]; vaf_ar_stage=[]; vaf_ar_am=[]; vaf_ar_owner=[]; vaf_ar_status=[]

# Custom multi-select dropdowns — one shared JS→Python bridge + a JSON holder
# ({lov, sel, label}) per filter that the JS checkbox dropdown renders from.
ms_bridge = ""; ms_bridge_last = ""
aia_owner_ms      = _ms_json(aia_owner_list,    [])
aia_campaign_ms   = _ms_json(aia_campaign_list, [])
va_owner_ms       = _ms_json(va_owner_list,     [])
va_campaign_ms    = _ms_json(va_campaign_list,  [])
cs_owner_ms       = _ms_json(cs_owner_list,     [])
cs_deal_ms        = _ms_json(cs_deal_list,      [])
cs_rectype_ms     = _ms_json(cs_rectype_list,   [])
cs_usage_deal_ms  = _ms_json([], [])
cs_usage_csm_ms   = _ms_json([], [])
cs_usage_stage_ms = _ms_json([], [])
cs_usage_owner_ms = _ms_json([], [])
cs_usage_cadence_ms = _ms_json([], [])
cs_usage_status_ms = _ms_json([], [])
cs_activity_event_ms = _ms_json(cs_activity_event_list, [])
cs_activity_deal_ms  = _ms_json(cs_activity_deal_list,  [])
cs_activity_stage_ms = _ms_json(cs_activity_stage_list, [])
cs_activity_csm_ms   = _ms_json(cs_activity_csm_list,   [])
cs_cohort_view_ms    = _ms_json(cs_cohort_view_list,    [])
vaf_deal_ms       = _ms_json(vaf_deal_list,      [])
vaf_rectype_ms    = _ms_json(vaf_rectype_list,   [])
vaf_line_item_ms  = _ms_json(vaf_line_item_list, [])
vaf_ar_deal_ms    = _ms_json([], [])
vaf_ar_stage_ms   = _ms_json([], [])
vaf_ar_am_ms      = _ms_json([], [])
vaf_ar_owner_ms   = _ms_json([], [])
vaf_ar_status_ms  = _ms_json([], [])

# ── Chart configs ──────────────────────────────────────────────────
chart_config = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d","select2d","autoScale2d",
                               "zoom2d","pan2d","zoomIn2d","zoomOut2d","resetScale2d"],
}
# Trend charts (AIA/VA Ops): keep the pan / zoom / reset toolbar buttons so a dense
# date range can be dragged left-right (dragmode="pan" on their layouts) and zoomed.
trend_config = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
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
    # keep every value label the same size (no per-bar auto-shrink, so HI matches the rest)
    "uniformtext": {"minsize": 16, "mode": "show"},
    "yaxis": {"side": "left", "automargin": True, "title": "",
              "tickfont": {"size": 13, "color": "#1a3a6b", "family": "Inter,sans-serif"}},
}
mkt_trend_layout  = {"barmode":"group","margin":{"l":40,"r":20,"t":10,"b":60},
                     "height":300,"legend":{"orientation":"h","y":-0.3},
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font}
mkt_cpl_layout    = {"margin":{"l":40,"r":20,"t":10,"b":60},"height":300,
                     "legend":{"orientation":"h","y":-0.3},
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

# Single-box date-range pickers (AIA/VA Ops): split the [start, end] list back into
# the existing start/end vars, mirror to the linked page's picker, then reuse the
# existing filter-change flow so nothing downstream needs to know about the range.
def on_aia_date(state):
    dr = state.aia_date_range
    if isinstance(dr, (list, tuple)) and len(dr) == 2 and dr[0] and dr[1]:
        state.aia_start_date = dr[0]; state.aia_end_date = dr[1]
        state.va_date_range = [dr[0], dr[1]]
        on_aia_filter_change(state)
def on_va_date(state):
    dr = state.va_date_range
    if isinstance(dr, (list, tuple)) and len(dr) == 2 and dr[0] and dr[1]:
        state.va_start_date = dr[0]; state.va_end_date = dr[1]
        state.aia_date_range = [dr[0], dr[1]]
        on_va_filter_change(state)

# ── Custom multi-select bridge ──────────────────────────────────────────────
# key -> (state var holding the chosen list, scope deciding which refresh runs)
_MS_DISPATCH = {
    "aia_owner":      ("aia_selected_owner",     "aia"),
    "aia_campaign":   ("aia_selected_campaign",  "aia"),
    "va_owner":       ("va_selected_owner",      "va"),
    "va_campaign":    ("va_selected_campaign",   "va"),
    "cs_owner":       ("cs_selected_owner",      "cs"),
    "cs_deal":        ("cs_selected_deal",       "cs"),
    "cs_rectype":     ("cs_selected_rectype",    "cs"),
    "cs_usage_deal":  ("cs_usage_deal",          "usage"),
    "cs_usage_csm":   ("cs_usage_csm",           "usage"),
    "cs_usage_stage": ("cs_usage_stage",         "usage"),
    "cs_usage_owner": ("cs_usage_owner",         "usage"),
    "cs_usage_cadence": ("cs_usage_cadence",     "usage"),
    "cs_usage_status": ("cs_usage_status",       "usage"),
    "cs_activity_event": ("cs_activity_event",   "activity"),
    "cs_activity_deal":  ("cs_activity_deal",    "activity"),
    "cs_activity_stage": ("cs_activity_stage",   "activity"),
    "cs_activity_csm":   ("cs_activity_csm",     "activity"),
    "cs_cohort_view":    ("cs_cohort_view",      "activity"),
    "vaf_deal":       ("vaf_selected_deal",      "vaf"),
    "vaf_line_item":  ("vaf_selected_line_item", "vaf"),
    "vaf_rectype":    ("vaf_selected_rectype",   "vaf"),
    "vaf_ar_deal":    ("vaf_ar_deal",            "ar"),
    "vaf_ar_stage":   ("vaf_ar_stage",           "ar"),
    "vaf_ar_am":      ("vaf_ar_am",              "ar"),
    "vaf_ar_owner":   ("vaf_ar_owner",           "ar"),
    "vaf_ar_status":  ("vaf_ar_status",          "ar"),
}

def _sync_ms(state):
    """Push each filter's {lov, sel, label} JSON to its hidden holder so the JS
    checkbox dropdowns reflect the current selection. Some option lists are
    DEPENDENT (cascading): they narrow based on a related filter's selection —
      CS Deal Name  <- CS Owner
      Usage Deal/CSM/Stage/Owner  <- each other (cross-filter)
      VA Deal Name  <- Recurring Type"""
    state.aia_owner_ms      = _ms_json(aia_owner_list,    state.aia_selected_owner)
    state.aia_campaign_ms   = _ms_json(aia_campaign_list, state.aia_selected_campaign)
    state.va_owner_ms       = _ms_json(va_owner_list,     state.va_selected_owner)
    state.va_campaign_ms    = _ms_json(va_campaign_list,  state.va_selected_campaign)
    state.cs_owner_ms       = _ms_json(cs_owner_list,     state.cs_selected_owner)
    state.cs_rectype_ms     = _ms_json(cs_rectype_list,   state.cs_selected_rectype)
    # Customer Activity Cohort: Deal Name / Deal Stage / CSM cross-filter each
    # other (Event Name is independent of deals, so it keeps the full list).
    # Recompute the base mask against the CURRENT _AIA — the module-level
    # _act_base_mask is tied to the original _AIA's index, but _reload_data()
    # (the 30-min auto-refresh) reassigns _AIA to a new DataFrame without
    # rebuilding that mask. The stale mask is then unalignable -> _sync_ms
    # crashed here, so every filter set after this point (the whole Customer
    # Usage & Health row) silently stopped populating until the next full rebuild.
    _ab = _AIA[(_AIA["integration_done_date"].notna()) & (_AIA["module_type"] == "AIA Paid")]
    def _alov(target):
        d = _ab
        for col, sv in (("deal_name", state.cs_activity_deal),
                        ("deal_stage", state.cs_activity_stage),
                        ("cs_owner", state.cs_activity_csm)):
            if col == target:
                continue
            s = _sel(sv)
            if s:
                d = d[d[col].isin(s)]
        return sorted(d[target].dropna().unique().tolist())
    state.cs_activity_event_ms = _ms_json(cs_activity_event_list, state.cs_activity_event)
    state.cs_cohort_view_ms    = _ms_json(cs_cohort_view_list, state.cs_cohort_view)
    state.cs_activity_deal_ms  = _ms_json(_alov("deal_name"),  state.cs_activity_deal)
    state.cs_activity_stage_ms = _ms_json(_alov("deal_stage"), state.cs_activity_stage)
    state.cs_activity_csm_ms   = _ms_json(_alov("cs_owner"),   state.cs_activity_csm)

    # CS Deal Name options depend on the selected CS Owner(s)
    _co = _sel(state.cs_selected_owner)
    if _co:
        _rids = _AIA[_AIA["cs_owner"].isin(_co)]["record_id"]
        cs_deal_lov = sorted(_AIA_LI[_AIA_LI["record_id"].isin(_rids)]["deal_name"].dropna().unique().tolist())
    else:
        cs_deal_lov = cs_deal_list
    state.cs_deal_ms        = _ms_json(cs_deal_lov, state.cs_selected_deal)

    # Customer Usage & Health: Deal Name / CSM / Stage / Deal Owner / Status cross-filter
    _ua = state.cs_usage_all
    def _ulov(target):
        d = _ua
        if d is None or len(d) == 0:
            return []
        for col, sv in (("Deal Name", state.cs_usage_deal), ("CSM", state.cs_usage_csm),
                        ("Stage", state.cs_usage_stage), ("Deal Owner", state.cs_usage_owner),
                        ("Cadence", state.cs_usage_cadence),
                        ("Status", state.cs_usage_status)):
            if col == target:
                continue
            # Status keeps its raw list so the "" (empty-box) option isn't stripped
            # by _sel; the other columns normalise through _sel as usual.
            s = (sv if isinstance(sv, list) else []) if col == "Status" else _sel(sv)
            if s:
                d = d[d[col].isin(s)]
        return sorted(d[target].dropna().unique().tolist()) if target in d.columns else []
    state.cs_usage_deal_ms   = _ms_json(_ulov("Deal Name"),  state.cs_usage_deal)
    state.cs_usage_csm_ms    = _ms_json(_ulov("CSM"),        state.cs_usage_csm)
    state.cs_usage_stage_ms  = _ms_json(_ulov("Stage"),      state.cs_usage_stage)
    state.cs_usage_owner_ms  = _ms_json(_ulov("Deal Owner"), state.cs_usage_owner)
    state.cs_usage_cadence_ms = _ms_json(_ulov("Cadence"),   state.cs_usage_cadence)
    # empty Status "" is included as a real, selectable option (an empty box)
    state.cs_usage_status_ms = _ms_json(_ulov("Status"), state.cs_usage_status)

    # VA Deal Name options depend on the selected Recurring Type(s)
    _vrt = _sel(state.vaf_selected_rectype)
    if _vrt and "recurring_type" in _VA_LI.columns:
        va_deal_lov = sorted(_VA_LI[_VA_LI["recurring_type"].isin(_vrt)]["deal_name"].dropna().unique().tolist())
    else:
        va_deal_lov = vaf_deal_list
    state.vaf_deal_ms       = _ms_json(va_deal_lov, state.vaf_selected_deal)
    state.vaf_rectype_ms    = _ms_json(vaf_rectype_list,   state.vaf_selected_rectype)
    state.vaf_line_item_ms  = _ms_json(vaf_line_item_list, state.vaf_selected_line_item)

    # Accounts Receivable Tracker: Deal Name / Deal Stage / AM / Deal Owner /
    # Due Status cross-filter each other (options narrow to the current selection).
    _ar = state.vaf_ar_all
    def _arlov(target):
        d = _ar
        if d is None or len(d) == 0:
            return []
        for col, sv in (("Deal Name", state.vaf_ar_deal), ("Deal Stage", state.vaf_ar_stage),
                        ("AM", state.vaf_ar_am), ("Deal Owner", state.vaf_ar_owner),
                        ("Due Status", state.vaf_ar_status)):
            if col == target:
                continue
            s = _sel(sv)
            if s:
                d = d[d[col].isin(s)]
        return sorted(d[target].dropna().unique().tolist()) if target in d.columns else []
    state.vaf_ar_deal_ms   = _ms_json(_arlov("Deal Name"),  state.vaf_ar_deal)
    state.vaf_ar_stage_ms  = _ms_json(_arlov("Deal Stage"), state.vaf_ar_stage)
    state.vaf_ar_am_ms     = _ms_json(_arlov("AM"),         state.vaf_ar_am)
    state.vaf_ar_owner_ms  = _ms_json(_arlov("Deal Owner"), state.vaf_ar_owner)
    state.vaf_ar_status_ms = _ms_json(_arlov("Due Status"), state.vaf_ar_status)

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
    elif scope == "activity": _build_cohort_tables(state)
    elif scope == "vaf":   _vaf_refresh(state)
    elif scope == "ar":    _ar_refresh(state)
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
    state.cs_selected_owner  = []; state.cs_selected_deal = []; state.cs_selected_rectype = []
    state.cs_usage_deal = []; state.cs_usage_csm = []; state.cs_usage_stage = []; state.cs_usage_owner = []; state.cs_usage_cadence = []; state.cs_usage_status = []
    state.cs_activity_event = []; state.cs_activity_deal = []; state.cs_activity_stage = []; state.cs_activity_csm = []
    # Marketing
    state.mkt_channel_filter = "All"; state.mkt_filter_label = ""
    # VA Finance
    state.vaf_selected_deal  = []; state.vaf_selected_line_item = []; state.vaf_selected_rectype = []
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
    _ar_refresh(state)
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