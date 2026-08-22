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
import html as _html
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
from flask import Flask, request
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
# Alt+1..6 -> jump straight to that page.
# (Ctrl+PgDn/PgUp can't be used: browsers reserve those for switching browser tabs.)
_PAGE_NAV_SCRIPT = """
<script id="page-nav">
(function () {
  var ORDER = ["/aia", "/cs", "/marketing", "/va-ops", "/va-finance", "/aia-bot"];
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
    else if (/^Digit[1-6]$/.test(e.code || "")) {
      var n = parseInt(e.code.slice(5), 10) - 1;  // Alt+1 -> page 0, ... Alt+6 -> page 5
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
      cap    = setTimeout(fire, 1000);  // hard cap
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
  function isTrend(gd){ try { return (gd.data||[]).some(function(t){return t.name==='DS'||t.name==='DB';}); } catch(e){ return false; } }
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
    var dsOff = vis(gd,'DS')==='off' || vis(gd,'DB')==='off', dcOff = vis(gd,'DC')==='off', qOff = vis(gd,'Qualified')==='off';
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

# Fast custom tooltip for the daily-signals sparkline dots. The dots carry data-tip;
# a single delegated listener shows a floating div on hover (instant, unlike the
# browser's native <title> which has a ~1s delay). Works for injected SVG too.
_SPARK_TIP_SCRIPT = """
<script id="dsig-spark-tip">
(function(){
  var tip = null;
  function box(){
    if(!tip){
      tip = document.createElement('div');
      tip.className = 'dsig-tip';
      tip.style.display = 'none';
      document.body.appendChild(tip);
    }
    return tip;
  }
  function place(el){
    var b = box(), r = el.getBoundingClientRect();
    b.textContent = el.getAttribute('data-tip') || '';
    b.style.display = 'block';
    b.style.left = (r.left + r.width/2) + 'px';
    b.style.top  = (r.top) + 'px';
  }
  document.addEventListener('mouseover', function(e){
    var t = e.target;
    if(t && t.getAttribute && t.getAttribute('data-tip')) place(t);
  }, true);
  document.addEventListener('mouseout', function(e){
    var t = e.target;
    if(tip && t && t.getAttribute && t.getAttribute('data-tip')) tip.style.display = 'none';
  }, true);
})();
</script>
"""

@flask_app.after_request
def _inject_zoom_lock(resp):
    try:
        # The /grid/ and /pie/ iframes are self-contained pages with their own JS and
        # CSS. Injecting the main-page scripts there is wrong — e.g. the sparkline
        # tooltip (.dsig-tip) is unstyled inside the iframe (main.css isn't loaded), so
        # it renders inline at the bottom as a stray caption when a grid cell tooltip
        # fires. Skip injection for those iframe responses.
        if request.path.startswith(("/grid/", "/pie/")):
            return resp
        if resp.headers.get("Content-Type", "").startswith("text/html"):
            html = resp.get_data(as_text=True)
            if "</body>" in html and 'id="zoom-lock"' not in html:
                resp.set_data(html.replace(
                    "</body>",
                    _ZOOM_LOCK_SCRIPT + _PAGE_NAV_SCRIPT + _MULTISELECT_SCRIPT
                    + _SNAPSHOT_SCRIPT + _COPYBTN_SCRIPT + _DATERANGE_SCRIPT
                    + _CHART_KEYS_SCRIPT + _DC_LEGEND_SCRIPT + _DSIG_SCRIPT
                    + _SPARK_TIP_SCRIPT + "</body>"))
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


def _make_trend(labels, ds, dc, qual=None, ds_name="DS"):
    """Overlay column + optional line (Power BI style): DS as blue bars in the BACK
    and DC as orange bars in FRONT, both on the 0 baseline (so DC reads as a portion
    of DS, not added to it); Qualified — when given — as a navy spline with boxed
    values. DS labels sit above each bar; DC numbers sit just above the orange bar,
    adaptively lifted so they never overlap the Qualified boxes. Bold labels + ticks."""
    xb = [f"<b>{l}</b>" for l in labels]   # slightly bold date ticks
    ds_c, dc_c, line_c = "#1a7fc4", "#ed7d31", "#1f4e79"   # DS blue, DC orange, line navy
    fig = go.Figure()
    # DS behind (full-height bar) — the only bars that carry value labels
    fig.add_bar(x=xb, y=ds, name=ds_name, marker_color=ds_c, marker_line_width=0,
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
    (active_days_count, streak). `streak` encodes 28 days as ';'-joined 18-field
    tokens (index 0 = today .. 27 = today-27d):
      on,uploads,syncs,items,views,txns,entities,recon,vmr,mapping,invoices,deletes,logins,txnstatus,
      lineitems,txnlines,reviewed,needsreview   (last 4 from user_daily_upload_summary)
    on=1 (ACTIVE) when there was ANY event that day — an upload, an accounting
    sync, any work event (transactions / entities / invoices / recon /
    vendor-mismatch / mapping / delete), OR a presence event (login /
    dashboard-viewed). The grid colours the dot: green=accounting sync,
    yellow=any other event, grey=nothing."""
    ac = _EMAIL_ACCT.get(_clean_email(email))
    today = pd.Timestamp(date.today()).normalize()
    blank = ";".join([",".join(["0"] * 18)] * 28)
    if ac is None:
        return 0, blank
    start = today - pd.Timedelta(days=27)
    uploads = {}; syncs = {}; items = {}; txnlines = {}   # from user_daily_upload_summary
    if "date" in _UPL.columns:
        u = _UPL[(_UPL["account_id"] == ac) & (_UPL["date"] >= start) & (_UPL["date"] <= today)].copy()
        if len(u):
            u["_d"] = u["date"].dt.normalize()
            g = u.groupby("_d")
            if "total_uploads" in u.columns:        uploads  = g["total_uploads"].sum().to_dict()
            if "stmt_txn_lines_total" in u.columns: txnlines = g["stmt_txn_lines_total"].sum().to_dict()
    # Line Items / Reviewed / Needs-Review now come from company_daily_bill_summary
    # (per company), pre-aggregated to (account, day) in _CBILL.
    cbill = _CBILL.get(ac, {})
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
        tl = int(txnlines.get(d, 0) or 0)
        li, rv, nr = cbill.get(d, (0, 0, 0))   # from company_daily_bill_summary
        # active = ANY event that day, presence (login / dashboard-viewed) included
        on = 1 if (up or sc or txn or ts or ent or rec or vmr or mp or inv or dele or log or vw) else 0
        active += on
        toks.append("%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d" % (
            on, up, sc, it, vw, txn, ent, rec, vmr, mp, inv, dele, log, ts, li, tl, rv, nr))
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
    """Cohort marketing performance by lead create-period, net of refunds: Spend,
    Leads, CPL, Net Paid, Net Revenue, MRR, CAC, ARPU, Payback — with a pinned
    Total row. Money is attributed to the month/week the LEAD was created (not
    when it paid), matching the Marketing Tracker 'Cohort Realized' sheet.
    Net Paid = leads created in the period that paid (amount_paid>0, not refunded);
    Net Revenue / MRR = those leads' line items' total_price / mrr (all line items,
    New + Renewal = the LTV/lifetime basis)."""
    spend_by = (mkt_df.dropna(subset=["day"]).groupby(mkt_df.dropna(subset=["day"])["day"].dt.to_period(freq))["cost"].sum()
                if "day" in mkt_df.columns and len(mkt_df) else pd.Series(dtype=float))
    aa = aia_df
    if "create_date" not in aa.columns:
        return pd.DataFrame()
    # Leads are GROSS (every lead acquired in the period, incl. later-refunded) —
    # CPL is spend per lead. Only Paid/Revenue/MRR go net of refunds below.
    aa_all = aa.dropna(subset=["create_date"]).copy()
    aa_all["cperiod"] = aa_all["create_date"].dt.to_period(freq)
    leads_by = aa_all.groupby("cperiod")["record_id"].nunique()
    # net-of-refunds subset, keyed to the lead's create-period
    aa_ok = aa_all[aa_all["asked_refund"] != "Yes"] if "asked_refund" in aa_all.columns else aa_all
    amt = aa_ok["amount_paid"] if "amount_paid" in aa_ok.columns else 0
    pmask = aa_ok["payment_date"].notna() & (amt > 0)
    netpaid_by = aa_ok[pmask].groupby("cperiod")["record_id"].nunique()
    # line items → the lead's create-period (revenue/MRR realised by cohort)
    cmap = aa_ok[["record_id", "cperiod"]].drop_duplicates("record_id")
    lim = li_df.dropna(subset=["date_paid"]) if "date_paid" in li_df.columns else li_df.iloc[0:0]
    if len(lim) and "record_id" in lim.columns:
        lim = lim.merge(cmap, on="record_id", how="inner")
        rev_by = lim.groupby("cperiod")["total_price"].sum() if "total_price" in lim.columns else pd.Series(dtype=float)
        mrr_by = lim.groupby("cperiod")["mrr"].sum() if "mrr" in lim.columns else pd.Series(dtype=float)
    else:
        rev_by = pd.Series(dtype=float); mrr_by = pd.Series(dtype=float)

    idxs = [s.index for s in [spend_by, leads_by, netpaid_by, rev_by, mrr_by] if len(s)]
    if not idxs:
        return pd.DataFrame()
    lo = min(i.min() for i in idxs); hi = max(i.max() for i in idxs)
    full = pd.period_range(lo, hi, freq=freq)
    if drop_zero_spend:
        # Trim only genuinely empty months (no spend AND no leads). A channel like
        # Organic has real leads with ₹0 spend — keep those rows; dropping on spend
        # alone would blank the whole table for any no-spend channel.
        sp_full = spend_by.reindex(full, fill_value=0)
        ld_full = leads_by.reindex(full, fill_value=0)
        full = full[[(float(sp_full[p]) > 0 or float(ld_full[p]) > 0) for p in full]]
    if last_n:
        full = full[-last_n:]
    if len(full) == 0:
        return pd.DataFrame()
    g = lambda s: s.reindex(full, fill_value=0)
    spend, leads, netpaid, rev, mrr = g(spend_by), g(leads_by), g(netpaid_by), g(rev_by), g(mrr_by)

    def _row(lbl, sp, ld, npd, rv, mr):
        cac  = round(sp / npd) if npd else 0
        arpu = round(mr / npd) if npd else 0
        return {label_name: lbl, "Spend (₹)": sp, "Leads": ld,
                "CPL": round(sp / ld) if ld else 0,
                "Net Paid": npd, "Net Revenue": rv, "MRR": mr,
                "CAC": cac, "ARPU": arpu,
                "Payback (mo)": round(cac / arpu) if arpu else 0}
    rows = [_row(label_fn(p), int(spend[p]), int(leads[p]), int(netpaid[p]),
                 int(rev[p]), int(mrr[p])) for p in full]
    rows.append(_row("Total", int(spend.sum()), int(leads.sum()), int(netpaid.sum()),
                     int(rev.sum()), int(mrr.sum())))
    return pd.DataFrame(rows)


def _mkt_funnel_8w(mkt_df, aia_df, last_n=8):
    """Weekly demo funnel on a LEAD-CREATE-WEEK COHORT basis — trailing `last_n`
    Mon–Sun weeks through the current week. Each row = leads CREATED that week, and
    DS / DC / DC(PS≥60) / No-Show count how many of *those* leads reached each stage
    (whenever it happened), so every rate is a true conversion ≤ 100%. Trade-off:
    recent weeks lag — their leads may not have booked/conducted their demos yet.
    Columns: Spend, Leads, CPL, DS, DS Rate, DC, Cost per DC, DC Rate, DC (PS≥60),
    Cost per PS≥60, Effective No-Show."""
    freq = "W"
    aa = aia_df
    if "create_date" not in aa.columns:
        return pd.DataFrame()
    aa = aa.dropna(subset=["create_date"]).copy()
    aa["cperiod"] = aa["create_date"].dt.to_period(freq)      # the lead's create week
    # Spend stays in-period — attributed to the week the money was actually spent.
    spend_by = (mkt_df.dropna(subset=["day"]).groupby(mkt_df.dropna(subset=["day"])["day"].dt.to_period(freq))["cost"].sum()
                if "day" in mkt_df.columns and len(mkt_df) else pd.Series(dtype=float))
    leads_by = aa.groupby("cperiod")["record_id"].nunique()
    def _coh(mask):
        # of each create-week's leads, how many satisfy `mask` (reached that stage)
        sub = aa[mask]
        return sub.groupby("cperiod")["record_id"].nunique() if len(sub) else pd.Series(dtype=float)
    ds_by   = _coh(aa["ds_date"].notna()) if "ds_date" in aa.columns else pd.Series(dtype=float)
    dc_by   = _coh(aa["dc_date"].notna()) if "dc_date" in aa.columns else pd.Series(dtype=float)
    dcps_by = (_coh(aa["dc_date"].notna() & (aa["prospect_score"] >= 60))
               if {"dc_date", "prospect_score"}.issubset(aa.columns) else pd.Series(dtype=float))
    # Effective No-Show: leads whose current stage is "Demo No-Show" (demo booked but
    # not attended), counted against their create week.
    noshow_by = _coh(aa["deal_stage"] == "Demo No-Show") if "deal_stage" in aa.columns else pd.Series(dtype=float)

    today_p = pd.Timestamp(date.today()).to_period(freq)
    idxs = [s.index for s in [spend_by, leads_by, ds_by, dc_by] if len(s)]
    if not idxs:
        return pd.DataFrame()
    lo = min(i.min() for i in idxs)
    hi = max(max(i.max() for i in idxs), today_p)
    full = pd.period_range(lo, hi, freq=freq)[-last_n:]
    if len(full) == 0:
        return pd.DataFrame()
    g = lambda s: s.reindex(full, fill_value=0)
    spend, leads, ds, dc, dcps, noshow = (g(spend_by), g(leads_by), g(ds_by),
                                          g(dc_by), g(dcps_by), g(noshow_by))
    _pct = lambda n, d: (f"{n/d*100:.1f}%" if d else "0.0%")

    def _row(lbl, sp, ld, d_s, d_c, d_p, ns_):
        return {"Week": lbl, "Spend (₹)": sp, "Leads": ld,
                "CPL": round(sp / ld) if ld else 0,
                "DS": d_s, "DS Rate": _pct(d_s, ld),
                "DC": d_c, "Cost per DC": round(sp / d_c) if d_c else 0,
                "DC Rate": _pct(d_c, d_s),
                "DC (PS≥60)": d_p, "Cost per PS≥60": round(sp / d_p) if d_p else 0,
                "Effective No-Show": ns_}
    rows = [_row(p.start_time.strftime("%d-%b"), int(spend[p]), int(leads[p]),
                 int(ds[p]), int(dc[p]), int(dcps[p]), int(noshow[p])) for p in full]
    rows.append(_row("Total", int(spend.sum()), int(leads.sum()), int(ds.sum()),
                     int(dc.sum()), int(dcps.sum()), int(noshow.sum())))
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════
# Redesigned Marketing funnel — one raw-number series feeding the Monthly (12M)
# and Weekly (8W) tables; render layer builds Total / Cost / Percentages views.
# Spine: Visits → Leads → MQL(Deals) → DS → DC → High PS → FT.
# ═══════════════════════════════════════════════════════════════════
def _sessions_by_period(ga_df, freq, sig_labels):
    """GA4 sessions per period + the SET of periods that have ANY GA data (so Visits
    can show '—' before GA history begins). sig_labels=None -> whole site; otherwise
    the union of each label's landing-page mask (paid segments are disjoint, so no
    double-count). Only www.aiaccountant.com sessions count."""
    if ga_df is None or not len(ga_df):
        return pd.Series(dtype=float), set()
    g = ga_df.copy()
    g["_d"] = pd.to_datetime(g["date"], errors="coerce").dt.normalize()
    g = g[g["hostname"].astype(str) == "www.aiaccountant.com"]
    if not len(g):
        return pd.Series(dtype=float), set()
    g["_p"] = g["_d"].dt.to_period(freq)
    ga_periods = set(g["_p"].dropna().unique())
    if sig_labels is None:
        gg = g
    else:
        mask = pd.Series(False, index=g.index); any_lp = False
        for lab in sig_labels:
            lm = _sig_lp_mask(g, lab)
            if lm is not None:
                mask = mask | lm; any_lp = True
        gg = g[mask] if any_lp else g.iloc[0:0]
    s = (pd.to_numeric(gg["sessions"], errors="coerce").fillna(0).groupby(gg["_p"]).sum()
         if len(gg) else pd.Series(dtype=float))
    return s, ga_periods

def _leads_by_period(cts, freq, sig_labels):
    """contacts_hs count per create-period, optionally channel-filtered by contact_source."""
    if cts is None or not len(cts) or "create_date" not in cts.columns:
        return pd.Series(dtype=float)
    c = cts.dropna(subset=["create_date"]).copy()
    if sig_labels is not None and "contact_source" in c.columns:
        c = c[c["contact_source"].map(_sig_contact_channel).isin(sig_labels)]
    if not len(c):
        return pd.Series(dtype=float)
    return c.groupby(c["create_date"].dt.to_period(freq)).size()

def _funnel_series(freq, last_n, aia_df, mkt_df, li_df, ga_df, cts_df, sig_labels):
    """Raw per-period series for the funnel spine + money, over the trailing `last_n`
    periods ending at the current one. aia_df / mkt_df are ALREADY channel-filtered by
    the nav (deal_source_group / _MKT.channel); ga_df / cts_df are filtered here by
    sig_labels. MQL/DS/DC/High PS/FT/Net Paid are cohort by lead create-period (matches
    the legacy Weekly Funnel); Net Revenue/MRR/CAC/ARPU reuse the _mkt_breakdown basis."""
    cur_p = pd.Timestamp(date.today()).to_period(freq)
    full  = pd.period_range(cur_p - (last_n - 1), cur_p, freq=freq)
    R = lambda s: s.reindex(full, fill_value=0)
    # spend — in-period (money spent that period)
    spend_by = (mkt_df.dropna(subset=["day"]).groupby(mkt_df.dropna(subset=["day"])["day"].dt.to_period(freq))["cost"].sum()
                if (mkt_df is not None and "day" in getattr(mkt_df, "columns", []) and len(mkt_df)) else pd.Series(dtype=float))
    # aia cohort — deals created in period + stage cohorts
    if aia_df is not None and "create_date" in aia_df.columns and len(aia_df):
        aa = aia_df.dropna(subset=["create_date"]).copy()
        aa["cperiod"] = aa["create_date"].dt.to_period(freq)
    else:
        aa = pd.DataFrame(columns=["record_id", "cperiod"])
    mql_by = aa.groupby("cperiod")["record_id"].nunique() if len(aa) else pd.Series(dtype=float)
    def _coh(mask):
        sub = aa[mask]
        return sub.groupby("cperiod")["record_id"].nunique() if len(sub) else pd.Series(dtype=float)
    ds_by  = _coh(aa["ds_date"].notna()) if ("ds_date" in aa.columns and len(aa)) else pd.Series(dtype=float)
    dc_by  = _coh(aa["dc_date"].notna()) if ("dc_date" in aa.columns and len(aa)) else pd.Series(dtype=float)
    hps_by = (_coh(aa["dc_date"].notna() & (aa["prospect_score"] >= 60))
              if ({"dc_date", "prospect_score"}.issubset(aa.columns) and len(aa)) else pd.Series(dtype=float))
    ft_by  = _coh(aa["ft_start_date"].notna()) if ("ft_start_date" in aa.columns and len(aa)) else pd.Series(dtype=float)
    # Effective No-Show: leads whose CURRENT stage is "Demo No-Show" — a demo was
    # booked but not attended. Same definition the legacy Weekly Funnel used, kept
    # as-is so the restored column reconciles with the old table.
    ns_by  = (_coh(aa["deal_stage"] == "Demo No-Show")
              if ("deal_stage" in aa.columns and len(aa)) else pd.Series(dtype=float))
    # net paid / revenue / MRR (net of refunds), cohort by create-period
    aa_ok = aa[aa["asked_refund"] != "Yes"] if ("asked_refund" in aa.columns and len(aa)) else aa
    netpaid_by = rev_by = mrr_by = pd.Series(dtype=float)
    if len(aa_ok):
        amt = aa_ok["amount_paid"] if "amount_paid" in aa_ok.columns else 0
        pmask = aa_ok["payment_date"].notna() & (amt > 0)
        netpaid_by = aa_ok[pmask].groupby("cperiod")["record_id"].nunique()
        cmap = aa_ok[["record_id", "cperiod"]].drop_duplicates("record_id")
        lim = li_df.dropna(subset=["date_paid"]) if (li_df is not None and "date_paid" in li_df.columns) else pd.DataFrame()
        if len(lim) and "record_id" in lim.columns:
            lim = lim.merge(cmap, on="record_id", how="inner")
            rev_by = lim.groupby("cperiod")["total_price"].sum() if "total_price" in lim.columns else pd.Series(dtype=float)
            mrr_by = lim.groupby("cperiod")["mrr"].sum() if "mrr" in lim.columns else pd.Series(dtype=float)
    # visits + leads (channel via sig_labels)
    visits_by, ga_periods = _sessions_by_period(ga_df, freq, sig_labels)
    leads_by = _leads_by_period(cts_df, freq, sig_labels)
    return {"full": full, "ga_periods": ga_periods,
            "spend": R(spend_by), "visits": R(visits_by), "leads": R(leads_by),
            "mql": R(mql_by), "ds": R(ds_by), "dc": R(dc_by), "highps": R(hps_by), "ft": R(ft_by),
            "noshow": R(ns_by),
            "netpaid": R(netpaid_by), "netrev": R(rev_by), "mrr": R(mrr_by)}

_MKT_VIEWS = ["Total", "Cost", "Percentages"]
_IMM_TIP   = "Cohort not mature yet"

def _mkt_render(fs, view, kind):
    """Base64 grid JSON for one Marketing-funnel table view.
      kind : 'monthly' (freq M; has MRR/CAC/ARPU/Payback) or 'weekly' (freq W; no money-KPIs)
      view : 'Total' | 'Cost' | 'Percentages'
    Cell rules: %-cell denominator <25 -> 'n<25' (grey italic + tip); any /0 -> '—';
    current (in-progress) period gets a 'Partial' badge; immature-cohort cells fade
    (opacity, tip). Total row: Total=sums, Cost=blended (Σspend/Σstage), %=pooled (ΣB/ΣA)."""
    freq = "M" if kind == "monthly" else "W"
    full = fs["full"]; ga = fs["ga_periods"]; N = len(full)
    cur  = pd.Timestamp(date.today()).to_period(freq)
    v    = lambda k, p: float(fs[k].get(p, 0) or 0)
    lab  = "Month" if kind == "monthly" else "Week"
    has_visits = kind == "weekly"   # Monthly drops Visits (GA history only ~2 months)
    has_noshow = kind == "weekly"   # Effective No-Show is a Weekly-Funnel column
    imm = {}   # immature-cohort fade removed per request — no greyed cells
    fd  = lambda stages, i: "cell-faded" if any(i in imm.get(s, ()) for s in stages) else ""
    cnt = lambda x: _grp(int(round(x)))
    def mg(*cs): return " ".join(c for c in cs if c)
    def pctc(num, den, cls=""):
        if den <= 0:   return ("—", cls, "")
        if den < 25:   return ("n<25", mg("cell-muted", cls), f"n = {int(den)} (<25)")
        return (f"{num / den * 100:.1f}%", cls, "")
    def costc(sp, c, cls=""):
        return ("—", cls, "") if c <= 0 else (cnt(sp / c), cls, "")

    def build(i, p, tot=False):
        # totals aggregate over all periods (visits only where GA exists)
        if tot:
            sp = sum(v("spend", q) for q in full); vi = sum(v("visits", q) for q in full if q in ga)
            le = sum(v("leads", q) for q in full); mq = sum(v("mql", q) for q in full)
            ds = sum(v("ds", q) for q in full);    dc = sum(v("dc", q) for q in full)
            hp = sum(v("highps", q) for q in full); ft = sum(v("ft", q) for q in full)
            npd = sum(v("netpaid", q) for q in full); nr = sum(v("netrev", q) for q in full)
            mr = sum(v("mrr", q) for q in full); ns = sum(v("noshow", q) for q in full)
            avail = True
        else:
            sp, vi, le, mq = v("spend", p), v("visits", p), v("leads", p), v("mql", p)
            ds, dc, hp, ft = v("ds", p), v("dc", p), v("highps", p), v("ft", p)
            npd, nr, mr = v("netpaid", p), v("netrev", p), v("mrr", p)
            ns = v("noshow", p)
            avail = p in ga
        r = {}
        if tot:
            r[lab] = ("Total", "", "")
        else:
            plab = p.strftime("%b %y") if freq == "M" else p.start_time.strftime("%d-%b")
            r[lab] = (plab, "cell-partial" if p == cur else "", "")
        vis = (cnt(vi), "", "") if avail else ("—", "", "")
        if view == "Total":
            r["Spend (₹)"] = (int(round(sp)), "", "")   # numeric so the grid draws the Spend bar
            if has_visits: r["Visits"] = vis
            r["Leads"] = (cnt(le), "", "")
            r["MQL (Deals)"] = (cnt(mq), "", "")
            r["DS"] = (cnt(ds), "", "")
            r["DC"] = (cnt(dc), "", "")
            r["High PS"] = (cnt(hp), fd(["highps"], i), "")
            r["FT"] = (cnt(ft), fd(["ft"], i), "")
            r["Net Paid"] = (cnt(npd), fd(["netpaid"], i), "")
            r["Net Revenue"] = (cnt(nr), fd(["netrev"], i), "")
            if has_noshow: r["Effective No-Show"] = (cnt(ns), "", "")
            if kind == "monthly":
                r["MRR"] = (cnt(mr), "", "")
                cac = round(sp / npd) if npd else None
                arpu = round(mr / npd) if npd else None
                pb  = round(cac / arpu) if (cac and arpu) else None
                fdn = fd(["netpaid"], i)
                r["CAC"] = (cnt(cac) if cac is not None else "—", fdn, "")
                r["ARPU"] = (cnt(arpu) if arpu is not None else "—", fdn, "")
                r["Payback (Mo)"] = (cnt(pb) if pb is not None else "—", fdn, "")
        elif view == "Cost":
            r["Spend (₹)"] = (int(round(sp)), "", "")   # numeric so the grid draws the Spend bar
            if has_visits:
                r["Cost/K Visits"] = ("—", "", "") if (not avail or vi <= 0) else (cnt(sp * 1000 / vi), "", "")
            r["CPL"] = costc(sp, le)
            r["Cost/MQL"] = costc(sp, mq)
            r["Cost/DS"] = costc(sp, ds)
            r["Cost/DC"] = costc(sp, dc)
            r["Cost/High PS"] = costc(sp, hp, fd(["highps"], i))
            r["Cost/FT"] = costc(sp, ft, fd(["ft"], i))
            if kind == "monthly":
                r["CAC"] = costc(sp, npd, fd(["netpaid"], i))
            # spend burnt on demos that were booked and then not attended
            if has_noshow: r["Cost/No-Show"] = costc(sp, ns)
        else:  # Percentages — post-MQL stages measured against the MQL (deals) base
            if has_visits:
                r["Visit→Lead"] = ("—", "", "") if not avail else pctc(le, vi)
            r["Lead→MQL"] = pctc(mq, le)
            r["MQL→DS"] = pctc(ds, mq)
            r["MQL→DC"] = pctc(dc, mq)
            r["MQL→High PS"] = pctc(hp, mq)
            r["MQL→FT"] = pctc(ft, mq)
            r["MQL→Paid"] = pctc(npd, mq)
            # measured against DS, not MQL: a no-show is only possible once a demo
            # was actually booked, so DS is the denominator that makes it a rate
            # (against MQL it would just be diluted by leads that never booked)
            if has_noshow: r["DS→No-Show"] = pctc(ns, ds)
        return r

    all_rows = [build(i, p) for i, p in enumerate(full)] + [build(None, None, tot=True)]
    cols = list(all_rows[0].keys())
    disp = {}
    class_cols = {}; tip_cols = {}
    for c in cols:
        disp[c] = [r[c][0] for r in all_rows]
        clv = [r[c][1] for r in all_rows]
        tpv = [(r[c][2] or (_IMM_TIP if "cell-faded" in r[c][1] else "")) for r in all_rows]
        if any(clv):
            disp[c + " ​cls"] = clv; class_cols[c] = c + " ​cls"
        if any(tpv):
            disp[c + " ​tip"] = tpv; tip_cols[c] = c + " ​tip"
    # Colour scales — green MRR/ARPU, red CAC — applied in EVERY view they appear in
    # (Total has all three; Cost has CAC). Cells are formatted strings, so shade from
    # hidden numeric source columns via heat_from.
    heat_cols = {}; heat_from = {}
    if kind == "monthly":
        mrr_h = []; cac_h = []; arpu_h = []
        for p in list(full) + [None]:            # None = the pinned Total row
            if p is None:
                sp = sum(v("spend", q) for q in full); npd = sum(v("netpaid", q) for q in full); mr = sum(v("mrr", q) for q in full)
            else:
                sp = v("spend", p); npd = v("netpaid", p); mr = v("mrr", p)
            mrr_h.append(int(round(mr)))
            cac_h.append(int(round(sp / npd)) if npd else 0)
            arpu_h.append(int(round(mr / npd)) if npd else 0)
        for colname, srccol, vals, color in (("MRR", "_mrr_h", mrr_h, "green"),
                                             ("ARPU", "_arpu_h", arpu_h, "green"),
                                             ("CAC", "_cac_h", cac_h, "red")):
            if colname in disp:                  # only where the column exists in this view
                disp[srccol] = vals; heat_cols[colname] = color; heat_from[colname] = srccol
    heat_cols = heat_cols or None; heat_from = heat_from or None
    df = pd.DataFrame(disp)
    return grid_payload_b64(df, lab, autosize=True, center_all=True, no_sort=True, sortable=False,
                            bar_cols=(["Spend (₹)"] if "Spend (₹)" in df.columns else None),
                            bar_color="#7fb3e0", heat_cols=heat_cols, heat_from=heat_from,
                            class_cols=class_cols or None, tip_cols=tip_cols or None)

def _mkt_utm_render(data, view):
    """UTM Source Cohort table in the shared Total / Cost / Percentages view. `data` =
    per-source dicts (src, deals, wa_bot, leads, ds, dc, hps, ft, tot_paid, revenue,
    mrr, spend). Cost = campaign-matched Spend ÷ stage; Percentages = stages vs the
    Deals cohort base. Total/Cost cells are NUMERIC so every column sorts correctly and
    the grid formats + heat/bar them; the % view stays string (n<25 / '—'). Total row:
    sums / blended / pooled. All columns sortable."""
    if not data:
        return grid_payload_b64(pd.DataFrame())
    # Default order: highest-revenue sources first (stable, so ties keep their prior
    # alphabetical order). no_sort=True below renders rows in this DataFrame order, so
    # this drives both the live page and the PDF snapshot; users can still click to re-sort.
    data = sorted(data, key=lambda d: d.get("revenue", 0), reverse=True)
    def pctc(num, den):
        if den <= 0:  return ("—", "", "")
        if den < 25:  return ("n<25", "cell-muted", f"n = {int(den)} (<25)")
        return (f"{num / den * 100:.1f}%", "", "")
    ncost = lambda sp, c: (int(round(sp / c)) if c > 0 else 0)   # numeric cost (0 when undefined)
    S = lambda k: sum(d[k] for d in data)
    def build(d, tot=False):
        g = (lambda k: S(k)) if tot else (lambda k: d[k])
        r = {"UTM Source": ("Total" if tot else d["src"], "", "")}
        if view == "Total":
            # order follows the funnel: Leads -> MQL (Deals) -> AIA Bot -> DS -> ...
            # "deals" is the same cohort count the Percentages view uses as its base.
            for k, col in (("leads", "Leads"), ("deals", "MQL (Deals)"),
                           ("wa_bot", "AIA Bot"), ("ds", "DS"), ("dc", "DC"),
                           ("hps", "High PS"), ("ft", "FT Started"), ("tot_paid", "Tot Paid"),
                           ("revenue", "Revenue"), ("mrr", "MRR")):
                r[col] = (int(g(k)), "", "")
        elif view == "Cost":
            sp = g("spend"); r["Spend (₹)"] = (int(round(sp)), "", "")
            r["CPL"] = (ncost(sp, g("leads")), "", ""); r["Cost/DS"] = (ncost(sp, g("ds")), "", "")
            r["Cost/DC"] = (ncost(sp, g("dc")), "", ""); r["Cost/High PS"] = (ncost(sp, g("hps")), "", "")
            r["Cost/FT"] = (ncost(sp, g("ft")), "", ""); r["CAC"] = (ncost(sp, g("tot_paid")), "", "")
        else:
            de = g("deals")
            r["Leads→Deals"] = pctc(de, g("leads")); r["Deals→DS"] = pctc(g("ds"), de); r["Deals→DC"] = pctc(g("dc"), de)
            r["Deals→High PS"] = pctc(g("hps"), de); r["Deals→FT"] = pctc(g("ft"), de); r["Deals→Paid"] = pctc(g("tot_paid"), de)
        return r
    all_rows = [build(d) for d in data] + [build(None, tot=True)]
    cols = list(all_rows[0].keys())
    disp = {}; class_cols = {}; tip_cols = {}
    for c in cols:
        disp[c] = [r[c][0] for r in all_rows]
        clv = [r[c][1] for r in all_rows]; tpv = [r[c][2] for r in all_rows]
        if any(clv): disp[c + "__cls"] = clv; class_cols[c] = c + "__cls"
        if any(tpv): disp[c + "__tip"] = tpv; tip_cols[c] = c + "__tip"
    heat_cols = {"MRR": "green"} if view == "Total" else ({"CAC": "red"} if view == "Cost" else None)
    df = pd.DataFrame(disp)
    return grid_payload_b64(df, "UTM Source", autosize=True, center_all=True, no_sort=True, sortable=True,
                            bar_cols=(["Spend (₹)"] if "Spend (₹)" in df.columns else None), bar_color="#7fb3e0",
                            heat_cols=heat_cols, class_cols=class_cols or None, tip_cols=tip_cols or None)


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
    contacts = pd.DataFrame(columns=["create_date", "contact_source", "utm_source", "utm_campaign"])
    try:
        # Wide enough (400d) to feed the Marketing Monthly (12M) / Weekly funnel Visits
        # column; Daily-signals only reads the last ~45d of this same frame.
        ga = _q(SUPABASE_URL,
            "SELECT date, hostname, landing_page, sessions FROM public.ga_daily "
            "WHERE date >= current_date - interval '400 days'",
            statement_timeout_ms=20000)
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
    try:
        # HubSpot contacts — the two columns Leads needs, bounded to ~13 months (400d)
        # so the Marketing Monthly (12M) / Weekly funnel Leads column has history;
        # Daily-signals reads only the last ~45d of this same frame. create_date is a
        # UTC timestamptz; take its UTC calendar date as-is (no IST shift) so the
        # day-count matches HubSpot's own "Create Date" column. Pinned to 'UTC' explicitly.
        # Exclude deleted contacts (is_deleted='Yes') the same way aia_live does.
        contacts = _q(SUPABASE_URL,
            "SELECT (create_date AT TIME ZONE 'UTC')::date AS create_date, "
            "contact_source, utm_source, utm_campaign FROM public.contacts_hs "
            "WHERE create_date >= now() - interval '400 days' "
            "AND is_deleted IS DISTINCT FROM 'Yes'",
            statement_timeout_ms=25000)
    except Exception as ex:
        print(f"[WARN] contacts_hs load failed: {ex} -- using empty frame")
    return ga, conv, contacts

def _load_gm_slots():
    """Marketing Daily-signals: GM demo-slot inventory (public.gm_slots_inventory)
    from Supabase, bounded to a rolling 45-day window and guarded so a failure just
    yields an empty frame. Columns: date, gm, slots_avl, created_at."""
    df = pd.DataFrame(columns=["date", "gm", "slots_avl", "created_at"])
    try:
        df = _q(SUPABASE_URL,
            "SELECT date, gm, slots_avl, created_at FROM public.gm_slots_inventory "
            "WHERE date >= current_date - interval '45 days'",
            statement_timeout_ms=20000)
    except Exception as ex:
        print(f"[WARN] gm_slots_inventory load failed: {ex} -- using empty frame")
    return df

def _prep_gm_slots(df):
    """date -> midnight; slots numeric; keep the LATEST snapshot per (date, gm) so a
    same-day re-write via created_at doesn't double-count."""
    df = df.copy() if df is not None else pd.DataFrame()
    if not len(df):
        return df
    df["_d"] = pd.to_datetime(df.get("date"), errors="coerce").dt.normalize()
    df["slots_avl"] = pd.to_numeric(df.get("slots_avl"), errors="coerce").fillna(0)
    df["gm"] = df.get("gm").astype(str).str.strip()
    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
        df = df.sort_values("created_at").drop_duplicates(subset=["_d", "gm"], keep="last")
    return df[df["_d"].notna()]

def _load_all():
    try:
        # aia_live / va_live date columns (create_date, ds_date, dc_date, payment_date, ...)
        # are stored as naive IST wall-clock (no tz) -- NOT UTC. Never apply a timezone
        # shift/AT TIME ZONE to them; read them as-is. (Confirmed against the source
        # pipeline; contrast with contacts_hs.create_date, which IS UTC timestamptz and
        # needs the AT TIME ZONE 'Asia/Kolkata' conversion done in _load_signals().)
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
                    "statement_frequency","bill_frequency","amount?","days_extended","poc_number","poc_email",
                    "aia_bot_date","ft_start_date"]
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
                     "parked_date","discard_date","closed_lost_date","churned_date",
                     "aia_bot_date","ft_start_date"])
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
    # total_price = full amount for the line item. Monthly deals with a multi-month
    # term bill term × unit_price; every other frequency already prices the full
    # commitment in unit_price. Mirrors the Marketing Tracker sheet's total_price.
    _is_monthly = df["billing_frequency"].astype(str).str.lower().eq("monthly")
    df["total_price"] = np.where(_is_monthly & (df["term"] != 1),
                                 df["term"] * df["unit_price"], df["unit_price"])
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

def _prep_signals(ga, conv, contacts=None):
    """Normalise the Daily-signals inputs. GA: date -> midnight, sessions numeric.
    Conversations: timestamptz -> naive IST, a msg_date day column, a last-10-digit
    phone key, and lower-cased direction/template/status for clean matching.
    Contacts: create_date (already a UTC calendar date) -> midnight for day matching."""
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
    contacts = contacts.copy() if contacts is not None else pd.DataFrame()
    if len(contacts) and "create_date" in contacts.columns:
        contacts["create_date"] = pd.to_datetime(contacts["create_date"], errors="coerce").dt.normalize()
    if len(contacts) and {"utm_campaign", "utm_source"}.issubset(contacts.columns):
        # bucket contacts by the SAME rule as deals: utm_campaign if present, else utm_source
        contacts["utm_source_cohort"] = contacts.apply(
            lambda r: r["utm_source"] if pd.isna(r["utm_campaign"]) or str(r["utm_campaign"]).strip() == ""
            else r["utm_campaign"], axis=1)
    return ga, conv, contacts

_RAW_GA, _RAW_CONV, _RAW_CONTACTS = _load_signals()
_GA, _CONV, _CONTACTS = _prep_signals(_RAW_GA, _RAW_CONV, _RAW_CONTACTS)
_GM_SLOTS = _prep_gm_slots(_load_gm_slots())

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

def _build_company_bill():
    """Per (account_id, day) bill-review counts from company_daily_bill_summary,
    which is keyed by company_id (no account_id) — linked to account via
    aia_companies. Returns {account_id: {normalised_date: (line_items, reviewed,
    needs_review)}} so _usage_28 is a cheap dict lookup. Bounded to ~40 days."""
    try:
        df = _q(SUPABASE_URL,
            "SELECT c.account_id::text AS account_id, b.date AS d, "
            "COALESCE(SUM(b.line_items_extracted_count),0) AS li, "
            "COALESCE(SUM(b.reviewed_count),0) AS rv, "
            "COALESCE(SUM(b.needs_review_count),0) AS nr "
            "FROM public.company_daily_bill_summary b "
            "JOIN public.aia_companies c ON c.company_id = b.company_id "
            "WHERE b.date >= (now() AT TIME ZONE 'Asia/Kolkata')::date - interval '40 days' "
            "GROUP BY c.account_id, b.date",
            statement_timeout_ms=20000)
    except Exception as ex:
        print(f"[WARN] company_daily_bill_summary load failed: {ex} -- bill counts unavailable")
        return {}
    if df is None or len(df) == 0:
        return {}
    df["_d"] = pd.to_datetime(df["d"], errors="coerce").dt.normalize()
    m = {}
    for ac, d, li, rv, nr in zip(df["account_id"], df["_d"], df["li"], df["rv"], df["nr"]):
        if pd.notna(ac) and pd.notna(d):
            m.setdefault(ac, {})[d] = (int(li or 0), int(rv or 0), int(nr or 0))
    return m

_CBILL = _build_company_bill()

def _build_db_bookings():
    """Demos-Booked (DB) booking events from hubspot_deal_logs (Supabase), which has
    one row per webhook fire. For each deal, walking its non-null ds_for rows ordered
    by created_at, a booking event is emitted whenever the ds_for DATE differs from the
    previous one (the first non-null is the original booking). Same-day reschedules
    (time-only changes) and duplicate webhook fires do NOT double-count. This keeps each
    date's count fixed once booked — a reschedule to a new date adds an event there
    instead of moving the original. Returns [record_id, ds_for_date], one row per event.
    Deals absent here fall back to aia_live.ds_for at the call site. Bounded to 3 months."""
    empty = pd.DataFrame(columns=["record_id", "ds_for_date"])
    try:
        d = _q(SUPABASE_URL,
            "SELECT record_id, ds_for, created_at FROM public.hubspot_deal_logs "
            "WHERE ds_for IS NOT NULL AND created_at >= NOW() - INTERVAL '3 months' "
            "ORDER BY record_id, created_at",
            statement_timeout_ms=30000)
    except Exception as ex:
        print(f"[WARN] hubspot_deal_logs load failed: {ex} -- DB trend falls back to aia_live.ds_for")
        return empty
    if d is None or len(d) == 0:
        return empty
    d["record_id"] = d["record_id"].astype(str)
    d["ds_for_date"] = pd.to_datetime(d["ds_for"], errors="coerce").dt.normalize()   # booking DATE
    d["created_at"] = pd.to_datetime(d["created_at"], errors="coerce", utc=True)
    d = d.dropna(subset=["record_id", "ds_for_date", "created_at"]).sort_values(["record_id", "created_at"])
    # emit an event whenever the ds_for DATE changes vs the deal's previous non-null
    # value — same-day time-only reschedules and duplicate fires don't double-count
    prev = d.groupby("record_id")["ds_for_date"].shift(1)
    changed = prev.isna() | (d["ds_for_date"] != prev)
    return d.loc[changed, ["record_id", "ds_for_date"]].reset_index(drop=True)

_DB_EVENTS = _build_db_bookings()

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
    global _EMAIL_ACCT, _ACTIVE_WEEKS, _ACTIVE_WEEKS_UPL, _ACTIVE_WEEKS_SYN, _ACTIVE_WEEKS_EV, _ACCT_DATES, _BILLING_END, _LAST_SYNC, _ACCT_BY_EMAIL, _CBILL, _DB_EVENTS
    global _RAW_GA, _RAW_CONV, _RAW_CONTACTS, _GA, _CONV, _CONTACTS, _FT_HEALTH_DF, _aiaBOT, _GM_SLOTS
    _FT_HEALTH_DF = None   # rebuilt lazily on next AIA Ops refresh
    _aiaBOT = None          # rebuilt lazily on next AIA Bot refresh
    _RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN, _RAW_ACT = _load_all()
    _RAW_GA, _RAW_CONV, _RAW_CONTACTS = _load_signals()
    _GA, _CONV, _CONTACTS = _prep_signals(_RAW_GA, _RAW_CONV, _RAW_CONTACTS)
    _GM_SLOTS = _prep_gm_slots(_load_gm_slots())
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
    _CBILL = _build_company_bill()
    _DB_EVENTS = _build_db_bookings()
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

_FT_HEALTH_DF = None

def _build_ft_health_df():
    """Free-Trial Customers Usage & Health data (AIA Ops): every AIA deal with a
    known ft_start_date, with the SAME 28-day usage streak + Activity Score +
    tooltip as the CS Usage & Health table. The heavy per-row usage scan is
    filter-independent, so the frame is cached and only rebuilt on data reload
    (see _reload_data); the Deal Name / GM / Stage dropdowns then slice it."""
    global _FT_HEALTH_DF
    if _FT_HEALTH_DF is not None:
        return _FT_HEALTH_DF
    # Free-trial customers only: FT started AND not yet paid (payment_date blank).
    # Once a payment lands they graduate to the CS Usage & Health table.
    base = (_AIA[_AIA["ft_start_date"].notna() & _AIA["payment_date"].isna()]
            if "ft_start_date" in _AIA.columns else _AIA.iloc[0:0])
    if len(base) == 0:
        _FT_HEALTH_DF = pd.DataFrame()
        return _FT_HEALTH_DF
    _ev_lu = _recent_event_lookup()
    _scores = _activity_scores()
    today = pd.Timestamp(date.today()).normalize()
    _ddmy = lambda v: pd.Timestamp(v).strftime("%d-%b-%y") if pd.notna(v) else ""
    rows = []
    for _, row in base.iterrows():
        email = _clean_email(row.get("login_email_id", ""))
        active_days, streak = _usage_28(email, _ev_lu)
        _acct = _EMAIL_ACCT.get(email)
        activity_score = int(_scores.get(_acct, 0)) if _acct else 0
        ftd = row.get("ft_start_date")
        dsince = (today - pd.Timestamp(ftd).normalize()).days if pd.notna(ftd) else 9999
        rows.append({
            "Deal Name": row.get("deal_name", ""),
            "record_id": row.get("record_id", ""),
            "GM": row.get("deal_owner", ""),
            "Stage": row.get("deal_stage", ""),
            "FT Start Date": _ddmy(ftd),
            # Orange while the trial is fresh (started within the last 14 days),
            # black once older. Hidden — drives the cell class.
            "__ftcls": ("cell-orange" if (pd.notna(ftd) and 0 <= dsince <= 14) else ""),
            "Usage Active Days (28d)": active_days,
            "Activity Score": activity_score,
            "Usage Streak Last 28D (desc)": streak,
        })
    _FT_HEALTH_DF = pd.DataFrame(rows)
    return _FT_HEALTH_DF


def _apply_ft_filter(state):
    """Filter the Free Trial Usage & Health grid by Deal Name / GM / Deal Stage."""
    d = state.aia_ft_all
    if d is None or len(d) == 0:
        state.aia_ft_json = grid_payload_b64(pd.DataFrame())
        return
    _dl = _sel(state.aia_ft_deal)
    if _dl:
        d = d[d["Deal Name"].isin(_dl)]
    _gm = _sel(state.aia_ft_gm)
    if _gm:
        d = d[d["GM"].isin(_gm)]
    _st = _sel(state.aia_ft_stage)
    if _st:
        d = d[d["Stage"].isin(_st)]
    # Sl no re-numbers 1..N over the current view (see rownum_col), so it stays
    # pinned top-to-bottom through any re-sort.
    d = d.reset_index(drop=True)
    d.insert(0, "Sl no", range(1, len(d) + 1))
    state.aia_ft_json = grid_payload_b64(
        d, sort_default_col="Usage Active Days (28d)", rownum_col="Sl no",
        col_w={"Deal Name": 300},
        streak_cols=["Usage Streak Last 28D (desc)"],
        center_cols=["FT Start Date"], date_cols=["FT Start Date"],
        heat_cols={"Usage Active Days (28d)": "green", "Activity Score": "blue"},
        class_cols={"FT Start Date": "__ftcls"},
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})


def _aia_ops_refresh(state):
    s = pd.Timestamp(state.aia_start_date)
    e = pd.Timestamp(state.aia_end_date)
    _ftdf = _build_ft_health_df()
    state.aia_ft_all = _ftdf
    state.aia_ft_deal_list  = sorted(_ftdf["Deal Name"].dropna().unique().tolist()) if len(_ftdf) else []
    state.aia_ft_gm_list    = sorted(_ftdf["GM"].dropna().unique().tolist()) if len(_ftdf) else []
    state.aia_ft_stage_list = sorted(_ftdf["Stage"].dropna().unique().tolist()) if len(_ftdf) else []
    _apply_ft_filter(state)
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

    # Booked/Conducted/Qualified trend — DS (blue) behind DC (orange) overlay
    # bars + Qualified line, capped at today. DS = demos BOOKED, counted by the day
    # they are scheduled FOR (ds_for), NOT the booking day (ds_date). ds_for carries
    # a timestamp + future appointments, so normalise to date before filtering so
    # today's later-in-the-day demos aren't dropped by the midnight cap.
    # DC/Qualified by dc_date.
    e_cap  = min(e, pd.Timestamp(date.today()))
    dc_sub = _rng(df,"dc_date",s,e_cap).copy()
    dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    daily_q  = dc_sub[dc_sub["prospect_score"]>=60].groupby("date")["record_id"].nunique().reset_index(name="Qualified")
    # DB (Demos Booked): booking EVENTS from hubspot_deal_logs — each reschedule keeps
    # the original date's count and adds one on the new date (vs aia_live.ds_for, which
    # overwrites). Restricted to this page's deals (respects filters + is_deleted).
    # Deals with no non-null ds_for in the logs fall back to their aia_live.ds_for.
    _ids    = set(df["record_id"].astype(str))
    _logged = set(_DB_EVENTS["record_id"]) if len(_DB_EVENTS) else set()
    _ev = (_DB_EVENTS[_DB_EVENTS["record_id"].isin(_ids)][["ds_for_date"]]
           .rename(columns={"ds_for_date": "date"}))
    _fb = df[~df["record_id"].astype(str).isin(_logged)]
    _fbd = pd.to_datetime(_fb["ds_for"], errors="coerce").dt.normalize() if "ds_for" in _fb.columns else pd.Series(pd.NaT, index=_fb.index)
    _fb_ev = pd.DataFrame({"date": _fbd[_fbd.notna()].values})
    _all_ev = pd.concat([_ev, _fb_ev], ignore_index=True)
    _all_ev = _all_ev[(_all_ev["date"] >= s) & (_all_ev["date"] <= e_cap)]
    daily_ds = _all_ev.groupby("date").size().reset_index(name="DS")
    trend = pd.DataFrame({"date": pd.date_range(s, e_cap, freq="D")})
    trend = (trend.merge(daily_ds,on="date",how="left").merge(daily_dc,on="date",how="left")
                  .merge(daily_q,on="date",how="left").fillna(0))
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    trend = trend.astype({"DS":int,"DC":int,"Qualified":int})
    state.aia_trend_fig = _make_trend(trend["date_label"].tolist(), trend["DS"].tolist(),
                                      trend["DC"].tolist(), trend["Qualified"].tolist(),
                                      ds_name="DB")   # Demos Booked (by ds_for)

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
            "AIA Bot":    _rng(o,"aia_bot_date",s,e)["record_id"].nunique(),
            "DS":         _rng(o,"ds_date",s,e)["record_id"].nunique(),
            "DC":         _rng(o,"dc_date",s,e)["record_id"].nunique(),
            "HI (ATP)":   _rng(o,"eta_pay_date",s,e).query("deal_stage=='High Intent'")["record_id"].nunique(),
            "FT Started": _rng(o,"ft_start_date",s,e)["record_id"].nunique(),
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
    # cohort count: members whose `col` date falls in [s, e] (safe if col missing)
    def _cin(frame, col):
        if col not in frame.columns:
            return 0
        return frame[frame[col].notna() & (frame[col] >= s) & (frame[col] <= e)]["record_id"].nunique()
    for src in sorted(_utm_src.unique()):
        c  = coh[_utm_src==src]
        l2 = c["record_id"].nunique()
        if l2 == 0: continue
        pd3 = c[c["payment_date"].notna()&(c["payment_date"]>=s)&(c["payment_date"]<=e)]
        # MRR: line items of records paid in-range, unit_price / billing-frequency
        mrr_u = int(_AIA_LI[_AIA_LI["record_id"].isin(pd3["record_id"])]["mrr"].sum())
        rows2.append({
            "UTM Source": src,
            "AIA Bot": _cin(c, "aia_bot_date"),
            "DS":     _cin(c, "ds_date"),
            "DC":     _cin(c, "dc_date"),
            "HI (ATP)": c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
            "FT Started": _cin(c, "ft_start_date"),
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
    # Identical column widths on BOTH matrices so the month columns line up vertically
    # for cohort-vs-cohort comparison (Cohort wide enough for the "Fresh Renewals" label;
    # every month column equal). Same col_w on both => identical rendered widths.
    _cs_mx_cw = {"Cohort": 185}
    for _c in list(_rev_m.columns) + list(_ret_m.columns):
        if _c != "Cohort":
            _cs_mx_cw.setdefault(_c, 120)
    state.cs_revenue_matrix_json = (grid_payload_b64(_rev_m, total_id_col="Cohort",
                                    blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                    autosize=True, heat_cols=_rev_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                    heat_by_row=True, col_w=_cs_mx_cw)
                                    if len(_rev_m) else grid_payload_b64(pd.DataFrame()))
    state.cs_retention_matrix_json = (grid_payload_b64(_ret_m, total_id_col="Cohort",
                                      blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                      autosize=True, heat_cols=_ret_heat, row_heat_cols=_MATRIX_ROW_HEAT,
                                      heat_by_row=True, col_w=_cs_mx_cw)
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
_FT_TEMPLATES = {"initial_verification", "demo_details", "updated_demo_details",
                 "initial_verification_free_trial", "updated_demo_details_free_trial_session"}
_DS_TEMPLATES = {"demo_details", "updated_demo_details", "updated_demo_details_free_trial_session"}
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

def _sig_rate_card(title, value_txt, unit, sub, pct, color, date_txt="", spark=""):
    pct = max(0.0, min(100.0, float(pct)))
    date_html = f'<span class="dsig-date">{date_txt}</span>' if date_txt else ""
    return (
        f'<div class="dsig-card dsig-{color}">'
        f'<div class="dsig-title"><span class="dsig-name">{title}</span>{date_html}</div>'
        f'<div class="dsig-val dsig-val-{color}">{value_txt}<span class="dsig-unit">{unit}</span></div>'
        f'<div class="dsig-sub">{sub}</div>'
        f'<div class="dsig-bar"><div class="dsig-bar-fill dsig-fill-{color}" style="width:{pct:.2f}%"></div></div>'
        f'{spark}'
        f'</div>')

def _band_status(lo, hi, value, higher_good=True):
    """green when in band, else green if the move was the helpful way (leads up /
    spend down) and red otherwise. Shared by the band card and its sparkline colour."""
    lo = max(0.0, float(lo)); hi = float(hi); value = float(value)
    if lo <= value <= hi:
        return "green"
    return "green" if ((value > hi) == bool(higher_good)) else "red"

def _sig_band_card(title, value_txt, date_txt, lo, med, hi, value, is_money, higher_good=True, spark=""):
    # Colour is direction-aware, not just "in/out of band": red means a BAD surprise, not
    # merely an unusual one. In band -> green (normal). Out of band -> good if it moved the
    # helpful way (leads up / spend down) = green, else red. So a lead spike reads green,
    # a lead drought or a spend blow-out reads red.
    lo = max(0.0, float(lo)); hi = float(hi); med = float(med); value = float(value)
    status = _band_status(lo, hi, value, higher_good)
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
        f'{spark}'
        '</div>')

_SPARK_HEX = {"green": "#16a34a", "amber": "#d97706", "red": "#dc2626"}
def _spark_hex(c):
    return _SPARK_HEX.get(c, "#94a3b8")

def _sparkline(pts, hexc):
    """Inline SVG 7-point sparkline. `pts` = list of {'v': float|None, 'size': float,
    'tip': str} (oldest→newest; None = no data that day). y auto-scales to the series'
    own min..max; each dot's radius scales with 'size' (volume/confidence) so a rate
    from a thin day reads as a small dot; the last point (the selected day) gets a
    ring. Native <title> tooltips — no JS. Returns '' if there's nothing to plot."""
    W, H, padx, pady = 132.0, 26.0, 0.0, 4.0   # padx 0 → first/last dot align to the progress bar's edges
    n = len(pts)
    if n == 0:
        return ""
    xs = [padx + (W - 2 * padx) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    vals = [p["v"] for p in pts if p["v"] is not None]
    if not vals:
        return ""
    vmin, vmax = min(vals), max(vals)
    rng = (vmax - vmin) or 1.0
    yof = lambda v: pady + (H - 2 * pady) * (1 - (v - vmin) / rng)
    szs = [p.get("size") or 0 for p in pts]
    smax = max(szs) if szs else 0
    rof = lambda s: (0.5 + 1 * (min(s, smax) / smax)) if smax > 0 else 1.6
    # polyline(s), broken across missing days
    segs, cur = [], []
    for i, p in enumerate(pts):
        if p["v"] is None:
            if len(cur) > 1: segs.append(cur)
            cur = []
        else:
            cur.append(f"{xs[i]:.1f},{yof(p['v']):.1f}")
    if len(cur) > 1: segs.append(cur)
    poly = "".join(
        f'<polyline points="{" ".join(s)}" fill="none" stroke="{hexc}" stroke-width="1.0" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>' for s in segs)
    last = max((i for i, p in enumerate(pts) if p["v"] is not None), default=-1)
    dots = []
    for i, p in enumerate(pts):
        if p["v"] is None:
            continue
        cx, cy, rr = xs[i], yof(p["v"]), rof(p.get("size") or 0)
        dc = p.get("color") or hexc          # per-day dot colour; the LINE stays hexc
        if i == last:
            dots.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rr + 1.0:.1f}" fill="none" '
                        f'stroke="{dc}" stroke-width="0.9" opacity="0.5"/>')
        dots.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rr:.1f}" fill="{dc}"/>')
        # invisible larger hit-target carries the tooltip text (JS reads data-tip)
        dots.append(f'<circle class="dsig-hit" cx="{cx:.1f}" cy="{cy:.1f}" r="7" '
                    f'fill="transparent" pointer-events="all" data-tip="{p.get("tip", "")}"/>')
    return (f'<svg class="dsig-spark" viewBox="0 0 {W:.0f} {H:.0f}" '
            f'preserveAspectRatio="none">{poly}{"".join(dots)}</svg>')

# ── Daily-signals CHANNEL filter — maps the panel's Channel dropdown to each
#    source. "All" = every source combined (no restriction). ──────────────────
_SIG_CH_GROUP = {"Google": "Google Ads", "Meta": "Meta Ads",
                 "LinkedIn": "LinkedIn Ads", "Organic": "Organic"}

# Paid channels each land on their own path prefix (/gads/..., /meta/...), keyed
# here by the FIRST path segment of ga_daily.landing_page. Everything else on the
# site -- the homepage '/', /blog/..., /resources/..., /case-studies/... -- is
# earned traffic and counts as Organic, defined as the complement so new pages
# are picked up automatically instead of needing to be enumerated here.
_SIG_LP_PAID = {"Google": "gads", "Meta": "meta"}
# GA's placeholder for sessions it couldn't attribute to a landing page. Kept OUT
# of Organic: it's unknown traffic, not earned traffic, and at ~7.7k sessions per
# 45 days it would dominate the denominator and understate the organic rate.
_SIG_LP_UNKNOWN = "(not set)"

def _sig_has_lp(channel):
    """True when the channel has landing-page traffic we can attribute at all.
    LinkedIn has no pages of its own, so its LP card shows a dash instead of a
    rate computed off the wrong denominator."""
    return channel == "All" or channel == "Organic" or channel in _SIG_LP_PAID

def _sig_lp_mask(g, channel):
    """Row mask selecting `channel`'s landing pages in a ga_daily frame, or None
    when the channel has none. Paid channels match their first path segment
    EXACTLY, not as a substring, so /blog/what-gads-costs is never miscounted as
    paid Google."""
    lp  = g["landing_page"].astype(str).str.strip()
    seg = lp.str.lstrip("/").str.split("/").str[0].str.lower()
    if channel in _SIG_LP_PAID:
        return seg == _SIG_LP_PAID[channel]
    if channel == "Organic":
        return (~seg.isin(list(_SIG_LP_PAID.values()))
                & (lp.str.lower() != _SIG_LP_UNKNOWN))
    return None

def _sig_contact_channel(src):
    """Bucket a contact_source into exactly ONE channel (first match wins, so
    'Tally Automation - meta' counts as Meta, not Google). 'Other' = untracked."""
    s = str(src).lower()
    if "meta" in s:                                                          return "Meta"
    if "linkedin" in s:                                                      return "LinkedIn"
    if any(k in s for k in ("tally automation", "pmax", "gads", "suvit")):   return "Google"
    if "homepage" in s:                                                      return "Organic"
    return "Other"

def _sig_deals(aia, channel):
    """Non-deleted AIA deals for the selected channel (All = every source)."""
    d = aia[aia["is_deleted"] != "Yes"] if "is_deleted" in aia.columns else aia
    grp = _SIG_CH_GROUP.get(channel)
    return d[d["deal_source_group"] == grp] if (grp and "deal_source_group" in d.columns) else d

def _sig_contacts(cts, channel):
    """contacts_hs rows for the selected channel (All = every contact). Each contact
    maps to a single channel via _sig_contact_channel, so channels never double-count
    an overlapping source."""
    if channel == "All" or "contact_source" not in cts.columns:
        return cts
    return cts[cts["contact_source"].map(_sig_contact_channel) == channel]

def _sig_sessions(ga, day, channel):
    """LP sessions for the channel on `day`. All = the whole site; paid channels =
    their own prefix; Organic = everything that isn't paid (see _sig_lp_mask).
    Channels with no landing pages return 0, which makes the card show a dash."""
    if not len(ga):
        return 0
    g = ga.copy(); g["_d"] = pd.to_datetime(g["date"], errors="coerce").dt.normalize()
    m = (g["_d"] == day) & (g["hostname"].astype(str) == "www.aiaccountant.com")
    if channel != "All":
        lp = _sig_lp_mask(g, channel)
        if lp is None:
            return 0
        m = m & lp
    return int(pd.to_numeric(g[m]["sessions"], errors="coerce").fillna(0).sum())

def _sig_lp_tip(ga, day, channel, limit=14):
    """Hover text listing the landing pages behind the LP-sessions denominator:
    one '/segment  sessions' line each, biggest first.

    Grouped at the FIRST path segment -- the same level the channel rule works at
    -- so the tooltip shows exactly why a page counts toward this channel, and
    stays readable for Organic, where a single day spans many blog URLs."""
    if not len(ga) or not _sig_has_lp(channel):
        return ""
    g = ga.copy()
    g["_d"] = pd.to_datetime(g["date"], errors="coerce").dt.normalize()
    m = (g["_d"] == day) & (g["hostname"].astype(str) == "www.aiaccountant.com")
    if channel != "All":
        lp = _sig_lp_mask(g, channel)
        if lp is None:
            return ""
        m = m & lp
    sub = g[m]
    if not len(sub):
        return ""
    raw = sub["landing_page"].astype(str).str.strip()
    seg = raw.str.lstrip("/").str.split("/").str[0].str.lower()
    label = pd.Series(np.where(raw.str.lower() == _SIG_LP_UNKNOWN, _SIG_LP_UNKNOWN,
                      np.where(seg == "", "/", "/" + seg)), index=sub.index)
    tot = (pd.to_numeric(sub["sessions"], errors="coerce").fillna(0)
             .groupby(label).sum().sort_values(ascending=False))
    lines = [f"{k}   {_grp(int(v))}" for k, v in tot.items()][:limit]
    if len(tot) > limit:
        lines.append(f"+{len(tot) - limit} more")
    return "Landing pages counted\n" + "\n".join(lines)

def _sig_spend(mkt, channel):
    """marketing_spends for the selected channel (All = every channel)."""
    ch = _SIG_CH_GROUP.get(channel)
    return mkt[mkt["channel"] == ch] if (ch and "channel" in mkt.columns) else mkt

def _daily_signals_html(day=None, channel="All"):
    """Build the 6 'Daily signals' cards, ALL for a single selected `day` (a
    date/Timestamp) and `channel` (All / Google / Meta / LinkedIn). Defaults to —
    and is capped at — yesterday (IST): today is never selectable because that
    day's data isn't fetched until the next day.
    The selected date is shown once, in the panel header; individual cards carry
    no date stamp. aia_live (Neon) joins to Conversations (Supabase) on the
    last-10-digit phone; POC history for the first-touch template gate is bounded
    to the 45-day Conversations pull, so days older than that under-report the
    messaging cards."""
    now_ist = datetime.now(_IST)
    today = pd.Timestamp(now_ist.date())
    yday  = today - pd.Timedelta(days=1)          # latest day with complete data
    lo_day = today - pd.Timedelta(days=45)        # oldest day the cards can cover
    day = (min(max(pd.Timestamp(day).normalize(), lo_day), yday)
           if day is not None else yday)
    aia, conv, ga, mkt, cts = _AIA, _CONV, _GA, _MKT, _CONTACTS
    channel = channel if channel in ("All", "Google", "Meta", "LinkedIn", "Organic") else "All"
    aia_ch = _sig_deals(aia, channel)          # channel-filtered, non-deleted deals
    cts_ch = _sig_contacts(cts, channel)       # channel-filtered contacts

    # outbound messages grouped by last-10 phone (for the three messaging cards)
    by_phone = {}
    if len(conv):
        cout = conv[conv["direction"] == "outbound"]
        by_phone = {p: g for p, g in cout.groupby("p10") if p}

    # Card — First-touch sent (selected day's created deals, this channel)
    dt = _rng(aia_ch, "create_date", day, day).copy()
    ft_den = int(dt["record_id"].nunique()) if len(dt) else 0
    ft_num = 0
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
    ft_rate = (100.0 * ft_num / ft_den) if ft_den else 0.0

    # channel deals created, keyed by day — feeds the MQL numerator + Leads card
    gl = aia_ch.copy()
    gl["_d"] = pd.to_datetime(gl["create_date"], errors="coerce").dt.normalize()

    # Card — LP Traffic-to-Leads: channel CONTACTS created / channel LP sessions.
    # (Meta/LinkedIn have no LP-session tracking -> lp_den 0 -> card shows a dash.)
    lp_den  = _sig_sessions(ga, day, channel)
    lp2_num = (int((cts_ch["create_date"] == day).sum())
               if len(cts_ch) and "create_date" in cts_ch.columns else 0)
    lp2_rate = (100.0 * lp2_num / lp_den) if lp_den else 0.0

    # Card — Leads to MQL: channel DEALS created / channel contacts (same-day).
    lp_num  = int(gl[gl["_d"] == day]["record_id"].nunique())
    mql_rate = (100.0 * lp_num / lp2_num) if lp2_num else 0.0

    # Band = the 7 days PREVIOUS TO the selected day (the day itself excluded), so
    # the day is judged against the past week of clean history.
    _BAND_DAYS = 7
    band_start = day - pd.Timedelta(days=_BAND_DAYS)  # day-7 .. day-1  (7 days)
    band_end   = day - pd.Timedelta(days=1)
    band_idx   = pd.date_range(band_start, band_end, freq="D")

    # Card — Leads (channel deals created): the day's value vs the prior 7-day band
    leads_val = lp_num
    ld = gl[(gl["_d"] >= band_start) & (gl["_d"] <= band_end)]
    ld_daily = ld.groupby("_d")["record_id"].nunique().reindex(band_idx, fill_value=0)
    l_med, l_lo, l_hi = _mad_band(ld_daily.values)

    # Card — Spend (channel): the day's value vs the prior 7-day band
    gs = _sig_spend(mkt, channel).copy()
    spend_val = 0.0; s_med = s_lo = s_hi = 0.0
    if {"day", "cost"}.issubset(gs.columns) and len(gs):
        gs["_d"] = pd.to_datetime(gs["day"], errors="coerce").dt.normalize()
        spend_val = float(gs.loc[gs["_d"] == day, "cost"].sum())
        spb = gs[(gs["_d"] >= band_start) & (gs["_d"] <= band_end)]
        sp_daily = spb.groupby("_d")["cost"].sum().reindex(band_idx, fill_value=0.0)
        s_med, s_lo, s_hi = _mad_band(sp_daily.values)

    # ── 7-day sparkline series (window ENDS at the selected day) ─────────────
    # Same numbers as each card's headline, one point per day; the selected day is
    # the last dot. Dots on rate cards are sized by that day's denominator so a
    # rate off a thin day reads as a small dot.
    spark_idx = pd.date_range(day - pd.Timedelta(days=6), day, freq="D")
    w0, w1 = spark_idx[0], spark_idx[-1]
    deals_daily = (gl[(gl["_d"] >= w0) & (gl["_d"] <= w1)]
                   .groupby("_d")["record_id"].nunique().reindex(spark_idx, fill_value=0))
    if len(cts_ch) and "create_date" in cts_ch.columns:
        _cd = pd.to_datetime(cts_ch["create_date"], errors="coerce").dt.normalize()
        contacts_daily = _cd[_cd.notna()].value_counts().reindex(spark_idx, fill_value=0)
    else:
        contacts_daily = pd.Series(0, index=spark_idx)
    # Same channel rule as _sig_sessions above, so the sparkline always plots the
    # series the headline is computed from. (It previously excluded only
    # Meta/LinkedIn, so Organic drew whole-site traffic under a dashed card.)
    if len(ga) and _sig_has_lp(channel):
        _g = ga.copy(); _g["_d"] = pd.to_datetime(_g["date"], errors="coerce").dt.normalize()
        _m = _g["_d"].between(w0, w1) & (_g["hostname"].astype(str) == "www.aiaccountant.com")
        if channel != "All":
            _m = _m & _sig_lp_mask(_g, channel)
        sessions_daily = (pd.to_numeric(_g.loc[_m, "sessions"], errors="coerce").fillna(0)
                          .groupby(_g.loc[_m, "_d"]).sum().reindex(spark_idx, fill_value=0))
    else:
        sessions_daily = pd.Series(0.0, index=spark_idx)
    if {"day", "cost"}.issubset(gs.columns) and len(gs):
        spend_daily = (gs[(gs["_d"] >= w0) & (gs["_d"] <= w1)]
                       .groupby("_d")["cost"].sum().reindex(spark_idx, fill_value=0.0))
    else:
        spend_daily = pd.Series(0.0, index=spark_idx)
    # First-touch series — one pass over the window's deals (cheap: ~7 days)
    ftN, ftD = {}, {}
    _dtw = _rng(aia_ch, "create_date", w0, w1).copy()
    if len(_dtw):
        _dtw["p10"] = _dtw["poc_number"].apply(_phone10) if "poc_number" in _dtw.columns else ""
        for _, r in _dtw.iterrows():
            cdate = r["create_date"]
            if pd.isna(cdate):
                continue
            k = cdate.normalize(); ftD[k] = ftD.get(k, 0) + 1
            pf = by_phone.get(r["p10"])
            if pf is None:
                continue
            outs = pf[pf["msg_date"] >= cdate]
            is_repeat = len(pf[pf["msg_date"] < cdate]) > 0
            cand = outs if is_repeat else outs[outs["template_name"].isin(_FT_TEMPLATES)]
            if len(cand):
                ftN[k] = ftN.get(k, 0) + 1

    def _rate_pts(numf, denf, lbl, good, ok):
        # each dot coloured by that day's rate vs the card's own thresholds
        out = []
        for d in spark_idx:
            den = float(denf(d)); num = float(numf(d))
            rate = (100.0 * num / den) if den else None
            tip = (f"{d.strftime('%d %b')} · {rate:.1f}% ({int(num)}/{int(den)})"
                   if rate is not None else f"{d.strftime('%d %b')} · no {lbl}")
            color = _spark_hex(_rate_color(rate, good, ok)) if rate is not None else None
            out.append({"v": rate, "size": den, "tip": tip, "color": color})
        return out
    def _val_pts(series, lo, hi, higher_good, money=False):
        # each dot coloured by that day's value vs the card's band
        out = []
        for d in spark_idx:
            v = float(series.get(d, 0))
            color = _spark_hex(_band_status(lo, hi, v, higher_good))
            out.append({"v": v, "size": 1, "color": color,
                        "tip": f"{d.strftime('%d %b')} · " + (("₹" + _grp(v)) if money else _grp(v))})
        return out

    lp_spark    = _sparkline(_rate_pts(lambda d: int(contacts_daily.get(d, 0)),
                             lambda d: float(sessions_daily.get(d, 0)), "LP traffic", 2.0, 1.0),
                             _spark_hex(_rate_color(lp2_rate, 2.0, 1.0)))
    mql_spark   = _sparkline(_rate_pts(lambda d: int(deals_daily.get(d, 0)),
                             lambda d: int(contacts_daily.get(d, 0)), "contacts", 70, 40),
                             _spark_hex(_rate_color(mql_rate, 70, 40)))
    ft_spark    = _sparkline(_rate_pts(lambda d: ftN.get(d, 0), lambda d: ftD.get(d, 0), "deals", 90, 75),
                             _spark_hex(_rate_color(ft_rate, 90, 75)))
    # band-card sparklines: the LINE takes the card's status colour; each dot is
    # coloured by that day's value vs the band (same rule as the card).
    spend_spark = _sparkline(_val_pts(spend_daily, s_lo, s_hi, False, money=True),
                             _spark_hex(_band_status(s_lo, s_hi, spend_val, higher_good=False)))
    leads_spark = _sparkline(_val_pts(deals_daily, l_lo, l_hi, True),
                             _spark_hex(_band_status(l_lo, l_hi, leads_val, higher_good=True)))

    # ── GM Slots Available — total open demo slots across GMs, per day ─────────
    # Not channel-scoped (GM availability is global). pct = day total / average of
    # the in-window days that HAVE data (incl. the selected day) — a fixed prior-7
    # average would divide by empty days while history is still filling in. Each
    # sparkline dot's hover lists every GM that day (incl. 0s), biggest first.
    gm_tot, gm_lines = {}, {}
    if len(_GM_SLOTS):
        _gsw = _GM_SLOTS[(_GM_SLOTS["_d"] >= w0) & (_GM_SLOTS["_d"] <= w1)]
        for _d, _g in _gsw.groupby("_d"):
            gm_tot[_d] = int(_g["slots_avl"].sum())
            gm_lines[_d] = sorted(((str(r["gm"]), int(r["slots_avl"]))
                                   for _, r in _g.iterrows()),
                                  key=lambda x: -x[1])
    _gm_avail = [gm_tot[d] for d in spark_idx if d in gm_tot]     # days that have data
    gm_avg = (sum(_gm_avail) / len(_gm_avail)) if _gm_avail else 0.0
    gm_day = gm_tot.get(day)                                       # None => no data that day
    gm_pct = (100.0 * gm_day / gm_avg) if (gm_day is not None and gm_avg) else 0.0
    def _gm_pts():
        out = []
        for d in spark_idx:
            t = gm_tot.get(d)
            if t is None:
                out.append({"v": None, "size": 0, "tip": f"{d.strftime('%d %b')} · no data"})
                continue
            pct = (100.0 * t / gm_avg) if gm_avg else 0.0
            _rows = gm_lines.get(d, [])
            _w = max((len(gm) for gm, _ in _rows), default=0)   # pad names to a column (tip is monospace)
            body = "\n".join(f"{gm.ljust(_w)}  {n:>2}" for gm, n in _rows)
            out.append({"v": float(t), "size": 1,
                        "color": _spark_hex(_rate_color(pct, 90, 75)),
                        "tip": f"{d.strftime('%d %b')} · {t} slots" + (f"\n{body}" if body else "")})
        return out
    gm_spark = _sparkline(_gm_pts(), _spark_hex(_rate_color(gm_pct, 90, 75)))
    if gm_day is not None:
        _gm_card = _sig_rate_card("GM Slots Available", str(gm_day), " slots",
                                  f"avg {gm_avg:.0f}/day · {len(gm_lines.get(day, []))} GMs",
                                  gm_pct, _rate_color(gm_pct, 90, 75), spark=gm_spark)
    else:
        _gm_card = _sig_rate_card("GM Slots Available", "—", "",
                                  "no inventory for this day", 0, "#94a3b8", spark=gm_spark)

    # LP Traffic-to-Leads — dash when the channel has no LP sessions (LinkedIn).
    # "N sessions" carries a hover listing the landing pages behind it, so the
    # denominator is inspectable instead of guesswork.
    if lp_den:
        _tip = _sig_lp_tip(ga, day, channel)
        _sess = (f'<span class="dsig-lp" title="{_html.escape(_tip, quote=True)}">'
                 f'{_grp(lp_den)} sessions</span>') if _tip else f"{_grp(lp_den)} sessions"
        _lp_card = _sig_rate_card("LP Traffic-to-Leads", f"{lp2_rate:.2f}", "%",
                                  f"{lp2_num} of {_sess}", lp2_rate,
                                  _rate_color(lp2_rate, 2.0, 1.0), spark=lp_spark)
    else:
        _lp_card = _sig_rate_card("LP Traffic-to-Leads", "—", "",
                                  f"{lp2_num} contacts · no LP traffic", 0, "#94a3b8", spark=lp_spark)

    # Every card is for the same selected day + channel, so no per-card date stamp.
    cards = [
        _lp_card,
        _sig_rate_card("Leads to MQL", f"{mql_rate:.1f}", "%",
                       f"{lp_num} Deals out of {lp2_num} contacts", mql_rate,
                       _rate_color(mql_rate, 70, 40), spark=mql_spark),
        _sig_rate_card("First-touch sent", f"{ft_rate:.1f}", "%",
                       f"{ft_num} of {ft_den} deals", ft_rate,
                       _rate_color(ft_rate, 90, 75), spark=ft_spark),
        _gm_card,
        _sig_band_card("Spend", "₹" + _grp(spend_val), "",
                       s_lo, s_med, s_hi, spend_val, True, higher_good=False, spark=spend_spark),
        _sig_band_card("Deals", str(leads_val), "",
                       l_lo, l_med, l_hi, leads_val, False, higher_good=True, spark=leads_spark),
    ]
    head = (f'<div class="dsig-head">Daily signals '
            f'<span>{day.strftime("%d %b %Y")}</span></div>')
    return ('<div class="dsig-panel">' + head
            + '<div class="dsig-grid">' + "".join(cards) + '</div></div>')


def _nav_sig_channel(state):
    """Map the nav-bar channel filter (multi-select deal_source_group) to a single
    Daily-signals label (All / Google / Meta / LinkedIn / Organic). One mapped pick →
    that channel; nothing selected or a mix → All."""
    _ch  = _sel(state.mkt_selected_channel)
    g2s  = {v: k for k, v in _SIG_CH_GROUP.items()}
    labs = [g2s[c] for c in _ch if c in g2s]
    return labs[0] if len(labs) == 1 else "All"

def _daily_signals_refresh(state):
    try:
        state.mkt_signals_html = _daily_signals_html(pd.Timestamp(state.mkt_sig_date),
                                                     _nav_sig_channel(state))
    except Exception as ex:
        print(f"[WARN] daily signals failed: {ex}")
        state.mkt_signals_html = ""

def on_mkt_sig_channel(state):
    """Daily-signals Channel dropdown changed — re-render the 7 cards for the day."""
    _daily_signals_refresh(state)

def on_mkt_view(state):
    """Shared Total / Cost / Percentages dropdown changed — rebuild BOTH funnel tables
    (Monthly 12M + Weekly 8W) in the selected view."""
    _mkt_refresh(state)

def on_mkt_utm_date(state):
    """UTM-table Start/End picker changed. It's bound to the SHARED ops range
    (aia_date_range), so mirror to AIA/VA Ops and refresh all three."""
    dr = state.aia_date_range
    if isinstance(dr, (list, tuple)) and len(dr) == 2 and dr[0] and dr[1]:
        state.aia_start_date = dr[0]; state.aia_end_date = dr[1]
        state.va_date_range = [dr[0], dr[1]]
        on_aia_filter_change(state)

def on_mkt_sig_date(state):
    """Daily-signals date picker changed — clamp to the valid window (45 days ago →
    yesterday) and re-render just the 6 cards for that day (the rest of the
    Marketing page is all-time)."""
    d = state.mkt_sig_date
    if isinstance(d, datetime):
        d = d.date()
    if d is not None:
        lo = _ist_today() - timedelta(days=45)
        hi = _ist_today() - timedelta(days=1)
        cd = min(max(d, lo), hi)
        if cd != d:                       # snap the picker back into range
            state.mkt_sig_date = cd
    _daily_signals_refresh(state)

def _make_cost_trend(x, spend, series):
    """Two-line monthly ₹-cost trend. `series` = [(name, denominators, word, colour)];
    each line is spend ÷ denominator for that month.

    Every denominator is a COHORT count — of the leads created that month, how many
    later reached the stage — so all lines lag, matching Monthly Performance rather
    than the period-in basis this chart used before. A month with no denominator
    plots as a gap, not ₹0, so an empty stage doesn't read as free.
    Each point carries a rounded-₹ label; hover shows ₹spend ÷ count = ₹value."""
    vals_all = [[round(s / d) if d else None for s, d in zip(spend, den)]
                for _n, den, _w, _c in series]
    # Labels normally sit above their dot. Where two series run close enough that
    # their labels would touch, the lower dot's label flips below — so the pair
    # stays legible and each label still reads against its own point. Threshold is
    # ~7% of the plotted range, roughly one label's height at this chart size
    # (y starts at 0, so the range is just the max value).
    _flat = [v for vs in vals_all for v in vs if v is not None]
    _near = (max(_flat) * 0.07) if _flat else 0
    positions = []
    for si, vs in enumerate(vals_all):
        pos = []
        for i, v in enumerate(vs):
            others = [vals_all[oi][i] for oi in range(len(vals_all)) if oi != si]
            others = [o for o in others if o is not None]
            crowded = v is not None and any(abs(v - o) <= _near for o in others)
            pos.append("bottom center" if (crowded and v < max(others)) else "top center")
        positions.append(pos)

    fig = go.Figure()
    for si, (name, den, word, colour) in enumerate(series):
        vals = vals_all[si]
        fig.add_scatter(
            x=x, y=vals, name=name, mode="lines+markers+text", cliponaxis=False,
            connectgaps=False,
            line={"color": colour, "width": 2}, marker={"size": 7, "color": colour},
            text=["" if v is None else _grp(v) for v in vals], textposition=positions[si],
            textfont={"size": 10, "color": "#000000", "family": "Inter,sans-serif"},
            customdata=[[f"₹{_grp(s)}", f"{_grp(d)} {word}",
                         "—" if v is None else f"₹{_grp(v)}"]
                        for s, d, v in zip(spend, den, vals)],
            hovertemplate="<b>%{x}</b><br>%{customdata[0]} ÷ %{customdata[1]} = %{customdata[2]}"
                          f"<extra>{name}</extra>",
        )
    fig.update_layout(
        margin={"l": 44, "r": 20, "t": 28, "b": 70}, height=300,
        legend={"orientation": "h", "y": -0.32, "x": 0},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter,sans-serif", "size": 12},
        # A transparent 10px tick pushes the month labels down and clear of any
        # data label that has flipped below its dot near the baseline (Jan-26).
        xaxis={"tickfont": {"size": 11, "family": "Inter,sans-serif", "color": "#1a3a6b"},
               "ticks": "outside", "ticklen": 10, "tickcolor": "rgba(0,0,0,0)"},
        yaxis={"showgrid": True, "gridcolor": "#eef2f7", "tickprefix": "₹", "rangemode": "tozero",
               "tickfont": {"size": 11, "family": "Inter,sans-serif"}},
    )
    return fig


def _mkt_refresh(state):
    _daily_signals_refresh(state)
    s = pd.Timestamp(state.mkt_start_date)
    e = pd.Timestamp(state.mkt_end_date)
    mkt_all = _MKT[(_MKT["day"]>=s)&(_MKT["day"]<=e)] if "day" in _MKT.columns else _MKT

    # Top-nav filters: Channel (also toggled by clicking a pie) + UTM Campaign.
    # Channel filters spend (_MKT.channel) and leads/conversions
    # (_AIA.deal_source_group); Campaign filters leads/conversions (_AIA.utm_campaign)
    # — spend carries no utm attribution, so a campaign filter leaves Spend/CAC at
    # the channel level. Both act on every table, KPI and chart below.
    _ch  = _sel(state.mkt_selected_channel)
    _cmp = _sel(state.mkt_selected_campaign)
    _dl  = _sel(state.mkt_selected_deal)
    _lbl = []
    if _ch:  _lbl.append("Channel: "  + ", ".join(_ch))
    if _cmp: _lbl.append("Campaign: " + ", ".join(_cmp))
    if _dl:  _lbl.append("Deal: " + (", ".join(_dl) if len(_dl) <= 2 else f"{len(_dl)} selected"))
    state.mkt_filter_label = "   ·   ".join(_lbl)
    mkt = mkt_all
    aia_base = _AIA
    if _ch and "channel" in mkt.columns:
        mkt = mkt[mkt["channel"].isin(_ch)]
    if _ch and "deal_source_group" in aia_base.columns:
        aia_base = aia_base[aia_base["deal_source_group"].isin(_ch)]
    if _cmp and "utm_campaign" in aia_base.columns:
        aia_base = aia_base[aia_base["utm_campaign"].isin(_cmp)]
    if _dl and "deal_name" in aia_base.columns:
        aia_base = aia_base[aia_base["deal_name"].isin(_dl)]

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

    _mkt_full = _MKT[_MKT["channel"].isin(_ch)] if (_ch and "channel" in _MKT.columns) else _MKT
    li_full = _AIA_LI
    if _ch or _cmp or _dl:
        li_full = _AIA_LI[_AIA_LI["record_id"].isin(aia_base["record_id"])]
    _heat_mkt = {"MRR": "green", "ARPU": "green", "CAC": "red"}

    # Monthly Performance — trailing 12 months (rolling window ending this month)
    mdf = _mkt_breakdown(_mkt_full, aia_base, li_full, "M", "Month",
                         lambda p: p.strftime("%b %y"), last_n=12)

    # Trend charts — all four lines are lagging/cohort: the denominators count the
    # leads CREATED that month which later reached the stage, so they line up with
    # Monthly Performance. (Cost/DC used to divide by DCs that HAPPENED that month,
    # which quietly disagreed with the table above it — e.g. Jul-26 read 5,664 on
    # the chart vs 6,456 in the table.) Built from mdf before the table renames
    # Leads/CPL; mdf already carries the cohort Leads and Net Paid we need.
    if len(mdf):
        chart = mdf[mdf["Month"] != "Total"].rename(columns={"Spend (₹)": "Spend"}).copy()
        _ac = aia_base.dropna(subset=["create_date"]).copy() if "create_date" in aia_base.columns else aia_base.iloc[0:0].copy()
        if len(_ac):
            _ac["_cp"] = _ac["create_date"].dt.to_period("M")
        _coh_n = lambda mask: (_ac[mask].groupby("_cp")["record_id"].nunique()
                               if len(_ac) else pd.Series(dtype=float))
        _dc_by = _coh_n(_ac["dc_date"].notna()) if len(_ac) else pd.Series(dtype=float)
        _hp_by = (_coh_n(_ac["dc_date"].notna() & (_ac["prospect_score"] >= 60))
                  if len(_ac) else pd.Series(dtype=float))
        _per = [pd.Period(pd.to_datetime(mth, format="%b %y"), freq="M") for mth in chart["Month"]]
        x     = chart["Month"].tolist()
        spend = [int(v) for v in chart["Spend"].tolist()]
        mqln  = [int(v) for v in chart["Leads"].tolist()]        # cohort: created that month
        npd   = [int(v) for v in chart["Net Paid"].tolist()]     # cohort: of those, paid
        dcn   = [int(_dc_by.get(p, 0)) for p in _per]
        hpn   = [int(_hp_by.get(p, 0)) for p in _per]
        state.mkt_cpl_fig = _make_cost_trend(x, spend, [
            ("Cost/MQL", mqln, "MQL", "#1a7fc4"),
            ("Cost/DC",  dcn,  "DC",  "#ed7d31")])
        # Green/purple rather than anything red: red is reserved for the tables'
        # CAC heat scale (magnitude), and red-vs-green is the classic colourblind
        # collision — this pair separates at ΔE 20.0 under deuteranopia.
        state.mkt_hps_cac_fig = _make_cost_trend(x, spend, [
            ("Cost/High PS", hpn, "High PS", "#2e9e6b"),
            ("CAC",          npd, "paid",    "#7e57c2")])
    else:
        state.mkt_cpl_fig = go.Figure()
        state.mkt_hps_cac_fig = go.Figure()

    # Redesigned funnel tables (Monthly 12M + Weekly 8W) sharing the Total/Cost/% view.
    # Channel: map the nav deal_source_group picks onto the sig session/contact buckets
    # (Google Ads->Google, …); unmapped picks (Referral/Others) contribute no sessions/
    # leads. Empty selection = All (whole-site Visits, all contacts).
    _G2S = {vv: kk for kk, vv in _SIG_CH_GROUP.items()}
    _sig_labels = None if not _ch else [_G2S[c] for c in _ch if c in _G2S]
    _view = state.mkt_view if getattr(state, "mkt_view", None) in _MKT_VIEWS else "Total"
    _fs_m = _funnel_series("M", 12, aia_base, _mkt_full, li_full, _GA, _CONTACTS, _sig_labels)
    _fs_w = _funnel_series("W", 8,  aia_base, _mkt_full, li_full, _GA, _CONTACTS, _sig_labels)
    state.mkt_monthly_json = _mkt_render(_fs_m, _view, "monthly")
    state.mkt_weekly_json  = _mkt_render(_fs_w, _view, "weekly")

    # UTM Source Cohort (moved from AIA Ops) — same period-in cohort logic, over the
    # SHARED ops date range (aia_start/end), sourced from the marketing-filtered
    # aia_base so it respects Channel / UTM Campaign / Deal Name. Leads (Contacts) =
    # contacts_hs bucketed by the same utm_source_cohort, channel + campaign filtered.
    _us = pd.Timestamp(state.aia_start_date); _ue = pd.Timestamp(state.aia_end_date)
    _cts = _CONTACTS
    if "create_date" in _cts.columns:
        _cts = _cts[(_cts["create_date"] >= _us) & (_cts["create_date"] <= _ue)]
    if _sig_labels is not None and "contact_source" in _cts.columns:
        _cts = _cts[_cts["contact_source"].map(_sig_contact_channel).isin(_sig_labels)]
    if _cmp and "utm_campaign" in _cts.columns:
        _cts = _cts[_cts["utm_campaign"].isin(_cmp)]
    _leads_src = (_cts.groupby(_cts["utm_source_cohort"].fillna("(Blank)")).size().to_dict()
                  if ("utm_source_cohort" in _cts.columns and len(_cts)) else {})
    _ucoh = _rng(aia_base, "create_date", _us, _ue)
    _usrc = _ucoh["utm_source_cohort"].fillna("(Blank)") if "utm_source_cohort" in _ucoh.columns else pd.Series(dtype=object)
    def _ucin(frame, col):
        if col not in frame.columns: return 0
        return frame[frame[col].notna() & (frame[col] >= _us) & (frame[col] <= _ue)]["record_id"].nunique()
    # per-source Spend via campaign match (channel-filtered spend, in the shared range)
    _msp = _mkt_full
    if "day" in getattr(_msp, "columns", []):
        _md = pd.to_datetime(_msp["day"], errors="coerce")
        _msp = _msp[(_md >= _us) & (_md <= _ue)]
    _spend_camp = (_msp.groupby("campaign")["cost"].sum().to_dict()
                   if ("campaign" in getattr(_msp, "columns", []) and len(_msp)) else {})
    _udata = []
    for _src in sorted(_usrc.unique()):
        c = _ucoh[_usrc == _src]
        _de = c["record_id"].nunique()
        if _de == 0: continue
        pd3 = c[c["payment_date"].notna() & (c["payment_date"] >= _us) & (c["payment_date"] <= _ue)]
        hps = (c[c["dc_date"].notna() & (c["prospect_score"] >= 60)
                 & (c["dc_date"] >= _us) & (c["dc_date"] <= _ue)]["record_id"].nunique()
               if {"dc_date", "prospect_score"}.issubset(c.columns) else 0)
        _udata.append({
            "src": _src, "deals": _de, "wa_bot": _ucin(c, "aia_bot_date"),
            "leads": int(_leads_src.get(_src, 0)), "ds": _ucin(c, "ds_date"), "dc": _ucin(c, "dc_date"),
            "hps": hps, "ft": _ucin(c, "ft_start_date"),
            "tot_paid": pd3[pd3["module_type"].isin(["AIA Paid", "GST Paid"])]["record_id"].nunique() if "module_type" in pd3.columns else 0,
            "revenue": int(pd3.groupby("record_id")["amount_paid"].max().sum()) if len(pd3) else 0,
            "mrr": int(_AIA_LI[_AIA_LI["record_id"].isin(pd3["record_id"])]["mrr"].sum()),
            "spend": float(_spend_camp.get(_src, 0)),
        })
    state.mkt_utm_json = _mkt_utm_render(_udata, _view)

    _daily_signals_refresh(state)   # Daily-signals cards follow the nav channel filter too

    # Channel pies — always show ALL channels (from the channel-unfiltered data)
    # so a different slice can be clicked. Scoped to the UTM Source Cohort table's
    # own Start/End range (_us/_ue, the shared ops range), so the pies and that
    # table always describe the same period; the page's own range is all-time,
    # which made the pies an all-time mix matching nothing else on the page.
    _mkt_pie = (mkt_all[(mkt_all["day"] >= _us) & (mkt_all["day"] <= _ue)]
                if ("day" in mkt_all.columns and len(mkt_all)) else mkt_all)
    if "channel" in _mkt_pie.columns and len(_mkt_pie):
        cs = _mkt_pie.groupby("channel")["cost"].sum().reset_index(); cs.columns=["Channel","Spend"]
        cs = cs.sort_values("Spend", ascending=False, ignore_index=True)
        state.mkt_channel_spend_json = pie_payload_b64(cs, "Channel", "Spend", money=True)
    else:
        state.mkt_channel_spend_json = pie_payload_b64(pd.DataFrame())

    # Deals by channel (distinct deal records by create_date) — this counts DEALS.
    cl = _rng(_AIA,"create_date",_us,_ue).groupby("deal_source_group")["record_id"].nunique().reset_index()
    cl.columns = ["Channel","Deals"]
    cl = cl.sort_values("Deals", ascending=False, ignore_index=True)
    state.mkt_channel_leads_json = pie_payload_b64(cl, "Channel", "Deals")

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
        # Sl no: running serial over the CURRENT (filtered) view — re-numbered 1..N in
        # display order by the grid (rownum_col), so it stays pinned top-to-bottom and
        # the last row = how many rows survived the filters.
        disp.insert(0, "Sl no", range(1, len(disp) + 1))
        state.vaf_ar_json = grid_payload_b64(
            disp, no_sort=True, autosize=True, max_height=560, rownum_col="Sl no",
            center_cols=["1st Payment Date", "Pending Dues (Months)", "Outstanding Amount",
                         "Due Status", "Next Due Date"],
            status_cols=["Due Status"], date_cols=["1st Payment Date", "Next Due Date"],
            col_w={"Sl no": 90, "Deal Name": 328},   # match the TAT Tracker's Sl no + Deal Name widths
            link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})
    else:
        state.vaf_ar_json = grid_payload_b64(pd.DataFrame())

_TAT_JOURNEY = {"Payment Done", "Ready for Renewal", "Renewal Done", "Parked", "AM Parked", "Churned"}
_TAT_RANK = {"Overdue": 0, "Due Soon": 1, "On Track": 2, "Renewed": 3, "Parked": 4, "Churned": 5}

def _tat_build():
    """VA TAT Tracker — one row per PAID VA deal in the renewal journey.
      T1 = Payment Done      -> Ready for Renewal  (rfr_date - payment_date)
      T2 = Ready for Renewal -> Renewal Done       (renewed_date - rfr_date)
    Benchmark = the MEDIAN TAT per transition (robust to outliers). Each active
    deal shows its stage's median as 'Expected TAT', a 'TAT Due In' countdown
    (median minus days in the current stage; negative once breached) and a status.
    Finished / terminal deals show Renewed / Parked / Churned. Returns
    (df, median_T1, median_T2)."""
    va = _VA
    if va is None or len(va) == 0 or "payment_date" not in va.columns:
        return pd.DataFrame(), 0, 0
    d = va[va["payment_date"].notna()].copy()
    for c in ("rfr_date", "renewed_date", "am_parked_date", "parked_date", "churned_date"):
        if c in d.columns:
            d[c] = pd.to_datetime(d[c], errors="coerce")
    today = pd.Timestamp(date.today()).normalize()
    _t1 = ((d["rfr_date"] - d["payment_date"]).dt.days
           if "rfr_date" in d.columns else pd.Series(dtype=float))
    _t2 = ((d["renewed_date"] - d["rfr_date"]).dt.days
           if {"renewed_date", "rfr_date"}.issubset(d.columns) else pd.Series(dtype=float))
    med1 = float(_t1[_t1 >= 0].median()) if _t1.notna().any() else 0.0
    med2 = float(_t2[_t2 >= 0].median()) if _t2.notna().any() else 0.0
    med1 = 0.0 if pd.isna(med1) else med1
    med2 = 0.0 if pd.isna(med2) else med2
    rows = []
    for _, r in d.iterrows():
        stg = str(r.get("deal_stage", "") or "")
        if stg not in _TAT_JOURNEY:
            continue
        expected, status, due_num, urgency = "—", "", None, 0.0
        if stg == "Churned":
            status = "Churned"
        elif stg in ("Parked", "AM Parked"):
            status = "Parked"
        elif stg == "Renewal Done":
            status = "Renewed"
            expected = f"{round(med2)} d" if med2 else "—"
        else:                                        # active transition (T1 or T2)
            med   = med1 if stg == "Payment Done" else med2
            start = r.get("payment_date") if stg == "Payment Done" else r.get("rfr_date")
            if med and pd.notna(start):
                elapsed = (today - pd.Timestamp(start).normalize()).days
                due_num = round(med) - elapsed        # days left; < 0 = overdue
                N = max(2, round(0.25 * med))         # amber window = 25% of the median
                status = "Overdue" if due_num < 0 else ("Due Soon" if due_num <= N else "On Track")
                expected = f"{round(med)} d"
                urgency = float(max(0, elapsed))
        rows.append({
            "Deal Name": r.get("deal_name", "") or "", "record_id": r.get("record_id", ""),
            "AM": (r.get("am_owner", "") or "—"),
            "Deal Owner": (r.get("deal_owner", "") or "—"),
            "Deal Stage": stg,
            "1st Payment Date": (pd.Timestamp(r.get("payment_date")).strftime("%d-%b-%y")
                                 if pd.notna(r.get("payment_date")) else ""),
            "RFR Date": (pd.Timestamp(r.get("rfr_date")).strftime("%d-%b-%y")
                         if pd.notna(r.get("rfr_date")) else ""),
            "Expected TAT (median)": expected, "TAT Status": status or "—",
            "TAT Due In": ("—" if due_num is None else f"{due_num} d"),
            "_due_sort": (due_num if due_num is not None else 9999),
            "_tat_urgency": urgency,
        })
    df = pd.DataFrame(rows)
    if len(df):
        df["_rank"] = df["TAT Status"].map(_TAT_RANK).fillna(9)
        df = (df.sort_values(["_rank", "_due_sort"])
                .drop(columns=["_rank", "_due_sort"]).reset_index(drop=True))
    return df, round(med1), round(med2)

def _tat_refresh(state):
    """Render the TAT Tracker from its base (state.vaf_tat_all), applying the panel's
    Deal Name / Deal Stage / AM / Deal Owner / TAT Status filters."""
    d = state.vaf_tat_all
    if d is None or len(d) == 0:
        state.vaf_tat_json = grid_payload_b64(pd.DataFrame())
        return
    for col, sv in (("Deal Name", state.vaf_tat_deal), ("Deal Stage", state.vaf_tat_stage),
                    ("AM", state.vaf_tat_am), ("Deal Owner", state.vaf_tat_owner),
                    ("TAT Status", state.vaf_tat_status)):
        s = _sel(sv)
        if s and col in d.columns:
            d = d[d[col].isin(s)]
    disp = d.copy()
    disp.insert(0, "Sl no", range(1, len(disp) + 1))
    state.vaf_tat_json = (grid_payload_b64(
        disp, no_sort=True, autosize=True, max_height=520, rownum_col="Sl no",
        center_cols=["Deal Stage", "1st Payment Date", "RFR Date", "Expected TAT (median)", "TAT Status", "TAT Due In"],
        status_cols=["TAT Status"], date_cols=["1st Payment Date", "RFR Date"], col_w={"Sl no": 90, "Deal Name": 328},
        heat_cols={"TAT Due In": "amber"}, heat_from={"TAT Due In": "_tat_urgency"},
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})
        if len(disp) else grid_payload_b64(pd.DataFrame()))

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
    # Pending Collections: renewal cash due but not yet collected. A subscription is
    # covered billing_start_date .. +term; the month coverage ENDS is when its renewal
    # is due. Take each deal's LATEST coverage-end (a renewal auto-extends it), keep
    # recurring only (New/Renewal, never One-time), and if that end month has arrived
    # (<= current month) it's pending. Cash = the cycle's full payment (total_price).
    _cur_per = pd.Period(today, freq="M")
    def _tm(r):                                   # term length in months for this cycle
        term = r.get("term"); term = 1 if (pd.isna(term) or term <= 0) else int(term)
        f = str(r.get("billing_frequency") or "").strip().lower()
        return {"monthly": term, "bi_monthly": 2, "quarterly": 3,
                "per_six_months": 6, "annually": 12}.get(f, term)
    def _cov_end(r):
        sd = r.get("billing_start_date")
        if pd.isna(sd):
            return pd.NaT
        de = r.get("days_extended"); de = 0 if pd.isna(de) else int(de)
        return sd + relativedelta(months=_tm(r)) + pd.Timedelta(days=de)
    # Churned / AM Parked deals are dropped from Pending ONLY (they still count in Total MRR / cohorts).
    _v_pend_excl = (set(_VA[_VA["deal_stage"].isin(["Churned", "AM Parked"])]["record_id"])
                    if "deal_stage" in _VA.columns else set())
    _pend = li.copy()
    if "recurring_type" in _pend.columns:
        _pend = _pend[_pend["recurring_type"].isin(["New", "Renewal"])]
    if _v_refund_map is not None:
        _pend = _pend[~_pend["record_id"].map(_v_refund_map).eq("Yes")]
    _pend = _pend[~_pend["record_id"].isin(_v_pend_excl)]
    _pend = _pend.dropna(subset=["record_id", "billing_start_date"]).copy()
    if len(_pend):
        _pend["_ce"] = _pend.apply(_cov_end, axis=1)
        _pend = _pend.dropna(subset=["_ce"])
        _pend = _pend.sort_values("_ce").groupby("record_id", as_index=False).last()  # latest cycle
        _pend["_termm"] = _pend.apply(_tm, axis=1)
        # Explode into one pending row per UNCOLLECTED billing period, from the latest
        # coverage-end up to the current month, stepping by the cycle length: monthly
        # accrues EVERY month (e.g. Jun, Jul, Aug); non-monthly only at each period end.
        _rows = []
        for _, _r in _pend.iterrows():
            _step = int(_r["_termm"]) if (pd.notna(_r["_termm"]) and _r["_termm"]) else 1
            _due = _r["_ce"]
            while pd.Period(_due, freq="M") <= _cur_per:
                _rows.append({"record_id": _r["record_id"],
                              "_m": pd.Period(_due, freq="M").strftime("%b %y"),
                              "_ce": _due, "_termm": _step,
                              "total_price": _r.get("total_price", 0),
                              "deal_name": _r.get("deal_name", "")})
                _due = _due + relativedelta(months=_step)
        _pend = pd.DataFrame(_rows, columns=["record_id", "_m", "_ce", "_termm", "total_price", "deal_name"])
    # per-month hover tip for the Pending row: "₹amt due for (dd-mmm, Nm) Deal"
    _pend_tips = {}
    if len(_pend):
        for _mk, _grp in _pend.sort_values("total_price", ascending=False).groupby("_m"):
            _pend_tips[_mk] = "\n".join(
                f"₹{int(round(_r['total_price'] or 0)):,} due for "
                f"{_r['_ce'].strftime('%d-%b')} ({int(_r['_termm'])}m) {_r.get('deal_name', '')}"
                for _, _r in _grp.iterrows())
    _mcols = [c for c in _vrev.columns if c != "Cohort"]
    def _pending_row(count=False):
        row = {"Cohort": "Pending Collections"}
        for c in _mcols:
            sub = _pend[_pend["_m"] == c] if len(_pend) else _pend
            row[c] = (int(sub["record_id"].nunique()) if count
                      else int(round(pd.to_numeric(sub.get("total_price"), errors="coerce").fillna(0).sum())))
        return row

    # Re-lay-out for display: cohorts, then the recurring Total (renamed), then the new
    # Pending Collections row. (New Collection / Fresh Renewals / One-time / Total
    # Collected / Total Payments removed per request.)
    def _finalize_va_matrix(m, total_label, pending_row):
        if m is None or not len(m):
            return m
        _drop = ["New Collection", "Fresh Renewals", "One-time"]
        cohorts = m[~m["Cohort"].isin(_drop + ["Total"])].copy()
        totrow  = m[m["Cohort"] == "Total"].copy()
        totrow["Cohort"] = total_label
        return pd.concat([cohorts, totrow, pd.DataFrame([pending_row])], ignore_index=True)
    _vrev = _finalize_va_matrix(_vrev, "Total MRR", _pending_row(count=False))
    _vret = _finalize_va_matrix(_vret, "Total Recurring", _pending_row(count=True))
    _vrev_heat = {c: "green" for c in _vrev.columns if c != "Cohort"} if len(_vrev) else {}
    _vret_heat = {c: "green" for c in _vret.columns if c != "Cohort"} if len(_vret) else {}
    # Per-month hidden tip columns — the Pending Collections row's cells hover-list the
    # deals due that month ("₹amt due for (dd-mmm, Nm) Deal"); every other row is blank.
    _tip_cols = {}
    for _c in _mcols:
        _tc = _c + " tip"
        _tip_cols[_c] = _tc
        for _mtx in (_vrev, _vret):
            if len(_mtx):
                _mtx[_tc] = [_pend_tips.get(_c, "") if v == "Pending Collections" else ""
                             for v in _mtx["Cohort"]]
    # Identical column widths on BOTH matrices so the month columns line up vertically
    # for cohort-vs-cohort comparison. Cohort wide enough for the "Pending Collections"
    # label; every month column equal. Auto-layout (table width:100%) distributes the
    # slack proportionally, so the same col_w on both tables => identical rendered widths.
    _mx_cw = {"Cohort": 185}
    for _c in _mcols:
        _mx_cw[_c] = 120
    state.vaf_revenue_matrix_json   = (grid_payload_b64(_vrev, total_id_col="Cohort",
                                       blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                       autosize=True, heat_cols=_vrev_heat, row_heat_cols={"Pending Collections": "amber"},
                                       heat_by_row=True, total_inline=True, tip_cols=_tip_cols, col_w=_mx_cw)
                                       if len(_vrev) else grid_payload_b64(pd.DataFrame()))
    state.vaf_retention_matrix_json = (grid_payload_b64(_vret, total_id_col="Cohort",
                                       blank_zeros=True, no_sort=True, sortable=False, center_all=True,
                                       autosize=True, heat_cols=_vret_heat, row_heat_cols={"Pending Collections": "amber"},
                                       heat_by_row=True, total_inline=True, tip_cols=_tip_cols, col_w=_mx_cw)
                                       if len(_vret) else grid_payload_b64(pd.DataFrame()))

    # TAT Tracker — turnaround through the renewal journey (below the retention matrix).
    # Build the base once, then render through _tat_refresh so its own filter row works.
    _tat_df, _tat_m1, _tat_m2 = _tat_build()
    state.vaf_tat_all = _tat_df
    _tat_refresh(state)
    state.vaf_tat_tip = (
        "TAT Tracker — turnaround time through the renewal journey\n"
        f"• T1: Payment Done → Ready for Renewal (median {_tat_m1} d)\n"
        f"• T2: Ready for Renewal → Renewal Done (median {_tat_m2} d)\n"
        "• Expected TAT (median) = the benchmark for the deal's current transition\n"
        "• TAT Due In = median − days already in the current stage (negative = overdue)\n"
        "• Status: On Track / Due Soon (last 25% of the median) / Overdue; "
        "Renewed = completed, Parked / Churned = terminal")

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
    _parked  = _reason_tbl("am parked", "am_parked_reason", "Parked")
    _churned = _reason_tbl("churned",   "churned_reason",   "Churned")
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
def _ist_today():
    """IST calendar date. Use for the Daily-signals date window so the picker's
    'yesterday' matches the IST sync stamp — the container clock may be UTC/behind,
    which would otherwise disable the real (IST) yesterday in the picker."""
    return datetime.now(_IST).date()
_month_start = date(_today.year, _today.month, 1)
_month_end   = date(_today.year, _today.month,
                    _calendar.monthrange(_today.year, _today.month)[1])

# Page 1
aia_start_date = _month_start;  aia_end_date = _month_end
aia_date_range = [_month_start, _month_end]   # single-box range picker <-> start/end
aia_owner_list    = sorted(_AIA["deal_owner"].dropna().unique().tolist())
aia_campaign_list = sorted(_AIA["utm_campaign"].dropna().unique().tolist())
aia_selected_owner = [];  aia_selected_campaign = []
# Free Trial Usage & Health filters (Deal Name / GM / Deal Stage)
aia_ft_all = None
aia_ft_deal = []; aia_ft_gm = []; aia_ft_stage = []
aia_ft_deal_list = []; aia_ft_gm_list = []; aia_ft_stage_list = []
aia_ft_deal_ms = _ms_json([], []); aia_ft_gm_ms = _ms_json([], []); aia_ft_stage_ms = _ms_json([], [])
# AIA Bot tracker
aiabot_all = None
aiabot_segment = []; aiabot_stage = []; aiabot_deal = []
aiabot_stage_list = []; aiabot_deal_list = []
aiabot_segment_ms = _ms_json(["Paid", "FT", "Unknown"], []); aiabot_stage_ms = _ms_json([], []); aiabot_deal_ms = _ms_json([], [])
aiabot_kpi_users = "0"; aiabot_kpi_paid_users = "0"; aiabot_kpi_ft_users = "0"
aiabot_kpi_messages = "0"; aiabot_kpi_messages_tip = ""; aiabot_kpi_split = "—"
aiabot_kpi_wau = "0"; aiabot_kpi_success = "—"; aiabot_kpi_success_tip = ""
aiabot_table_json = ""
aiabot_adopt_fig = go.Figure(); aiabot_intent_fig = go.Figure(); aiabot_trend_fig = go.Figure()
aiabot_fail_json = ""; aiabot_fail_intent = []; aiabot_fail_intent_list = []; aiabot_fail_intent_ms = _ms_json([], [])

# AIA Bot Activity Cohort
aiabot_cohort_json = ""
aiabot_cohort_intent = []
aiabot_cohort_intent_list = []
aiabot_cohort_intent_ms = _ms_json([], [])
aiabot_cohort_company = []
aiabot_cohort_company_list = []
aiabot_cohort_company_ms = _ms_json([], [])
aiabot_cohort_view = []
aiabot_cohort_view_list = ["Cohort %", "Users"]
aiabot_cohort_view_ms = _ms_json(aiabot_cohort_view_list, [])

aia_kpi_leads=0; aia_kpi_ds=0; aia_kpi_dc=0; aia_kpi_hi=0
aia_kpi_aia_paid=0; aia_kpi_gst_paid=0; aia_kpi_paid=0; aia_kpi_refunds=0
aia_kpi_parked=0; aia_kpi_discards=0; aia_kpi_closed_lost=0
aia_kpi_collected="₹0"; aia_kpi_collected_exact="₹0"; aia_kpi_mrr="₹0"; aia_kpi_mrr_exact="₹0"
aia_funnel_fig = go.Figure()
aia_trend_fig = go.Figure()
aia_channel_pie_json = ""
aia_channel_filter = "All"; aia_channel_order = []; aia_filter_label = ""
aia_channel_click = ""; aia_channel_click_last = ""
aia_gm_json=""; aia_utm_json=""; aia_incentive_json=""; aia_ft_json=""
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
cs_rev_tip = ("MRR Matrix (₹)\n"
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
aia_ft_tip = ("• Every AIA Unpaid deals with a known FT start date\n"
              "• FT Start Date is orange within the first 14 days\n"
              "• Active Days / Activity Score / streak = same 28-day measures as CS Usage & Health")
vaf_rev_tip = ("Revenue Matrix (₹)\n"
               "• Cohort Spread: Based on MRR + one-time revenue\n"
               "• Total MRR: Sum of MRR + one-time revenue\n"
               "• Pending Collections: Renewal cash due but not collected — accrues each uncollected billing month up to now (non-monthly show only at each period end); one-time, churned & AM-parked excluded")
vaf_ret_tip = ("Customer Retention Matrix\n"
               "• Cohort Spread: Based on recurring + one-time customers\n"
               "• Total Recurring: Sum of recurring + one-time customers\n"
               "• Pending Collections: Customers with a renewal due but not paid, counted each uncollected billing month up to now (one-time, churned & AM-parked excluded)")

# Page 3
mkt_start_date = date(2020,1,1); mkt_end_date = _today   # no date filter on Marketing page (all-time)
mkt_kpi_spend="₹0"; mkt_kpi_leads="0"; mkt_kpi_cpl="₹0"; mkt_kpi_cac="₹0"
mkt_kpi_arpu="₹0"; mkt_kpi_payback="—"
mkt_monthly_tip = ("Monthly Performance (12M)\n"
                   "• Shows each month's leads and their lagging indicators over time.\n"
                   "• Spend is counted for that month only.\n"
                   "• Recent months may look lower as they haven't matured yet.\n"
                   "• n<25 = too few leads for a reliable rate.\n"
                   "• — = no data/denominator.")
mkt_weekly_tip  = ("Weekly Funnel (8W)\n"
                   "• Shows leads from each week (Mon–Sun) and their lagging indicators over time.\n"
                   "• Includes the current partial week.\n"
                   "• Each conversion rate is based only on that week's leads.\n"
                   "• Newer weeks may look lower as they haven't matured yet.\n"
                   "• n<25 = too few leads for a reliable rate.\n"
                   "• — = no data/denominator.")
mkt_utm_tip     = ("UTM Source Cohort\n"
                   "• Groups leads by UTM Campaign, or UTM Source if the campaign is missing.\n"
                   "• Each metric is counted only if its date falls within the selected date range.\n"
                   "• Leads created in the period but with a later demo won't count under DS.\n"
                   "• Leads = contacts created during the selected period.\n"
                   "• Spend is matched to the campaign name.\n"
                   "• n<25 = too few leads to show a reliable rate.\n"
                   "• — = no data/denominator.")

mkt_signals_html=""
mkt_sig_channels = ["All", "Google", "Meta", "LinkedIn", "Organic"]   # Daily-signals channel filter
mkt_sig_channel  = "All"
mkt_view = "Total"; mkt_views = _MKT_VIEWS   # shared Total/Cost/Percentages view for both funnel tables
mkt_monthly_json=""; mkt_weekly_json=""; mkt_utm_json=""; mkt_spend_df=pd.DataFrame(); mkt_cpl_fig=go.Figure()
mkt_hps_cac_fig=go.Figure()
mkt_channel_spend_json=""; mkt_channel_leads_json=""
mkt_channel_filter="All"; mkt_filter_label=""
mkt_channel_click=""; mkt_channel_click_last=""; mkt_leads_click=""; mkt_leads_click_last=""
# Top-nav multi-select filters (Channel + UTM Campaign) — act on every table/KPI/chart.
# Channel options are the union of spend channels and lead source-groups (same labels).
mkt_channel_list  = sorted(set(_AIA["deal_source_group"].dropna().unique().tolist())
                           | (set(_MKT["channel"].dropna().unique().tolist()) if "channel" in _MKT.columns else set()))
mkt_campaign_list = sorted(_AIA["utm_campaign"].dropna().unique().tolist()) if "utm_campaign" in _AIA.columns else []
mkt_deal_list     = sorted(_AIA["deal_name"].dropna().unique().tolist()) if "deal_name" in _AIA.columns else []
mkt_selected_channel=[]; mkt_selected_campaign=[]; mkt_selected_deal=[]
# Daily-signals date picker: default (and max) = yesterday (today's data isn't in
# yet); min = 45 days back (older days lack the Conversations history the cards need).
_sig_today   = _ist_today()
mkt_sig_date = _sig_today - timedelta(days=1)
mkt_sig_max  = _sig_today - timedelta(days=1)
mkt_sig_min  = _sig_today - timedelta(days=45)

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
vaf_tat_json=""; vaf_tat_tip=""
# TAT Tracker — its own 5 cross-filtering dropdowns (Deal Name / Deal Stage / AM /
# Deal Owner / TAT Status), same pattern as the AR Tracker below.
vaf_tat_all=pd.DataFrame()
vaf_tat_deal=[]; vaf_tat_stage=[]; vaf_tat_am=[]; vaf_tat_owner=[]; vaf_tat_status=[]
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
mkt_channel_ms    = _ms_json(mkt_channel_list,  [])
mkt_campaign_ms   = _ms_json(mkt_campaign_list, [])
mkt_deal_ms       = _ms_json(mkt_deal_list,     [])
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
vaf_tat_deal_ms   = _ms_json([], [])
vaf_tat_stage_ms  = _ms_json([], [])
vaf_tat_am_ms     = _ms_json([], [])
vaf_tat_owner_ms  = _ms_json([], [])
vaf_tat_status_ms = _ms_json([], [])

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
    _mkt_refresh(state)     # UTM Source Cohort (on Marketing) shares the ops date range
    _aiabot_refresh(state)   # AIA Bot shares the ops date range too
def on_cs_filter_change(state):  _cs_refresh(state); _sync_ms(state)
def on_cs_usage_filter(state):   _apply_usage_filter(state); _sync_ms(state)
def on_mkt_filter_change(state): _mkt_refresh(state)
def on_va_filter_change(state):
    state.aia_start_date     = state.va_start_date
    state.aia_end_date       = state.va_end_date
    state.aia_selected_owner = [o for o in _sel(state.va_selected_owner) if o in state.aia_owner_list]
    _aia_ops_refresh(state)
    _va_ops_refresh(state)
    _mkt_refresh(state)     # UTM Source Cohort (on Marketing) shares the ops date range
    _aiabot_refresh(state)   # AIA Bot shares the ops date range too
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
    "aia_ft_deal":    ("aia_ft_deal",            "aiaft"),
    "aia_ft_gm":      ("aia_ft_gm",              "aiaft"),
    "aia_ft_stage":   ("aia_ft_stage",           "aiaft"),
    "aiabot_segment":  ("aiabot_segment",          "aiabot"),
    "aiabot_stage":    ("aiabot_stage",            "aiabot"),
    "aiabot_deal":     ("aiabot_deal",             "aiabot"),
    "aiabot_fail_intent": ("aiabot_fail_intent",   "aiabot"),
    "aiabot_cohort_intent":  ("aiabot_cohort_intent",  "aiabot"),
    "aiabot_cohort_company": ("aiabot_cohort_company", "aiabot"),
    "aiabot_cohort_view":    ("aiabot_cohort_view",    "aiabot"),
    "va_owner":       ("va_selected_owner",      "va"),
    "va_campaign":    ("va_selected_campaign",   "va"),
    "mkt_channel":    ("mkt_selected_channel",   "mkt"),
    "mkt_campaign":   ("mkt_selected_campaign",  "mkt"),
    "mkt_deal":       ("mkt_selected_deal",      "mkt"),
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
    "vaf_tat_deal":   ("vaf_tat_deal",           "tat"),
    "vaf_tat_stage":  ("vaf_tat_stage",          "tat"),
    "vaf_tat_am":     ("vaf_tat_am",             "tat"),
    "vaf_tat_owner":  ("vaf_tat_owner",          "tat"),
    "vaf_tat_status": ("vaf_tat_status",         "tat"),
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
    # Marketing filters cascade: Channel → Campaign → Deal Name (each option list
    # narrows to what's available given the higher-level selections).
    _mch  = _sel(state.mkt_selected_channel)
    _mcmp = _sel(state.mkt_selected_campaign)
    _md = _AIA
    if _mch and "deal_source_group" in _md.columns:
        _md = _md[_md["deal_source_group"].isin(_mch)]
    mkt_camp_lov = (sorted(_md["utm_campaign"].dropna().unique().tolist())
                    if "utm_campaign" in _md.columns else mkt_campaign_list)
    if _mcmp and "utm_campaign" in _md.columns:
        _md = _md[_md["utm_campaign"].isin(_mcmp)]
    mkt_deal_lov = (sorted(_md["deal_name"].dropna().unique().tolist())
                    if "deal_name" in _md.columns else mkt_deal_list)
    state.mkt_channel_ms    = _ms_json(mkt_channel_list,  state.mkt_selected_channel)
    state.mkt_campaign_ms   = _ms_json(mkt_camp_lov,      state.mkt_selected_campaign)
    state.mkt_deal_ms       = _ms_json(mkt_deal_lov,      state.mkt_selected_deal)
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

    # Free Trial Usage & Health: Deal Name / GM / Deal Stage cross-filter each other
    _fa = state.aia_ft_all
    def _flov(target):
        d = _fa
        if d is None or len(d) == 0:
            return []
        for col, sv in (("Deal Name", state.aia_ft_deal), ("GM", state.aia_ft_gm),
                        ("Stage", state.aia_ft_stage)):
            if col == target:
                continue
            s = _sel(sv)
            if s:
                d = d[d[col].isin(s)]
        return sorted(d[target].dropna().unique().tolist()) if target in d.columns else []
    state.aia_ft_deal_ms  = _ms_json(_flov("Deal Name"), state.aia_ft_deal)
    state.aia_ft_gm_ms    = _ms_json(_flov("GM"),        state.aia_ft_gm)
    state.aia_ft_stage_ms = _ms_json(_flov("Stage"),     state.aia_ft_stage)

    # AIA Bot: Segment (fixed lov) / Deal Stage / Deal Name cross-filter
    _wa = state.aiabot_all
    def _walov(target):
        d = _wa
        if d is None or len(d) == 0:
            return []
        for col, sv in (("segment", state.aiabot_segment), ("deal_stage", state.aiabot_stage),
                        ("deal_name", state.aiabot_deal)):
            if col == target:
                continue
            s = _sel(sv)
            if s:
                d = d[d[col].isin(s)]
        return sorted(d[target].dropna().unique().tolist()) if target in d.columns else []
    state.aiabot_segment_ms = _ms_json(["Paid", "FT", "Unknown"], state.aiabot_segment)
    state.aiabot_stage_ms   = _ms_json(_walov("deal_stage"), state.aiabot_stage)
    state.aiabot_deal_ms    = _ms_json(_walov("deal_name"),  state.aiabot_deal)
    state.aiabot_fail_intent_ms = _ms_json(state.aiabot_fail_intent_list, state.aiabot_fail_intent)
    state.aiabot_cohort_intent_ms  = _ms_json(state.aiabot_cohort_intent_list,  state.aiabot_cohort_intent)
    state.aiabot_cohort_company_ms = _ms_json(state.aiabot_cohort_company_list, state.aiabot_cohort_company)
    state.aiabot_cohort_view_ms    = _ms_json(aiabot_cohort_view_list, state.aiabot_cohort_view)

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

    # TAT Tracker: Deal Name / Deal Stage / AM / Deal Owner / TAT Status cross-filter.
    _tat = state.vaf_tat_all
    def _tatlov(target):
        d = _tat
        if d is None or len(d) == 0:
            return []
        for col, sv in (("Deal Name", state.vaf_tat_deal), ("Deal Stage", state.vaf_tat_stage),
                        ("AM", state.vaf_tat_am), ("Deal Owner", state.vaf_tat_owner),
                        ("TAT Status", state.vaf_tat_status)):
            if col == target:
                continue
            s = _sel(sv)
            if s:
                d = d[d[col].isin(s)]
        return sorted(d[target].dropna().unique().tolist()) if target in d.columns else []
    state.vaf_tat_deal_ms   = _ms_json(_tatlov("Deal Name"),  state.vaf_tat_deal)
    state.vaf_tat_stage_ms  = _ms_json(_tatlov("Deal Stage"), state.vaf_tat_stage)
    state.vaf_tat_am_ms     = _ms_json(_tatlov("AM"),         state.vaf_tat_am)
    state.vaf_tat_owner_ms  = _ms_json(_tatlov("Deal Owner"), state.vaf_tat_owner)
    state.vaf_tat_status_ms = _ms_json(_tatlov("TAT Status"), state.vaf_tat_status)

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
    # AIA Bot: the cohort Intent filter and the "falls short" Intent filter mirror
    # each other, so picking an intent in one place applies to both tables.
    if key == "aiabot_cohort_intent":
        state.aiabot_fail_intent = sel
    elif key == "aiabot_fail_intent":
        state.aiabot_cohort_intent = sel
    if scope == "aia":     on_aia_filter_change(state)
    elif scope == "aiaft": _apply_ft_filter(state)
    elif scope == "aiabot": _aiabot_refresh(state)
    elif scope == "va":    on_va_filter_change(state)
    elif scope == "mkt":   _mkt_refresh(state)
    elif scope == "cs":    _cs_refresh(state)
    elif scope == "usage": _apply_usage_filter(state)
    elif scope == "activity": _build_cohort_tables(state)
    elif scope == "vaf":   _vaf_refresh(state)
    elif scope == "ar":    _ar_refresh(state)
    elif scope == "tat":   _tat_refresh(state)
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

def _mkt_toggle_channel(state, ch):
    """Add/remove a channel in the nav Channel multi-select (the single source of
    truth) so pie clicks and the dropdown stay in sync."""
    cur = list(_sel(state.mkt_selected_channel))
    if ch in cur: cur.remove(ch)
    else:         cur.append(ch)
    state.mkt_selected_channel = cur
    _mkt_refresh(state); _sync_ms(state)

def on_mkt_channel_click(state):
    raw = state.mkt_channel_click
    if not raw or raw == state.mkt_channel_click_last:
        return
    state.mkt_channel_click_last = raw
    ch = _bridge_channel(raw)
    if ch: _mkt_toggle_channel(state, ch)

def on_mkt_leads_click(state):
    raw = state.mkt_leads_click
    if not raw or raw == state.mkt_leads_click_last:
        return
    state.mkt_leads_click_last = raw
    ch = _bridge_channel(raw)
    if ch: _mkt_toggle_channel(state, ch)

def on_mkt_channel_reset(state):
    state.mkt_selected_channel = []; state.mkt_selected_campaign = []; state.mkt_selected_deal = []
    _mkt_refresh(state); _sync_ms(state)


def on_reset_filters(state, *_):
    """Alt+Shift+R — reset all page filters to month defaults."""
    today = date.today()
    ms = today.replace(day=1)
    me = (ms + relativedelta(months=1)) - timedelta(days=1)
    # AIA Ops
    state.aia_start_date     = ms;  state.aia_end_date     = me
    state.aia_selected_owner = []; state.aia_selected_campaign = []
    state.aia_ft_deal = []; state.aia_ft_gm = []; state.aia_ft_stage = []
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
    state.mkt_selected_channel = []; state.mkt_selected_campaign = []; state.mkt_selected_deal = []
    state.mkt_channel_filter = "All"; state.mkt_filter_label = ""
    # VA Finance
    state.vaf_selected_deal  = []; state.vaf_selected_line_item = []; state.vaf_selected_rectype = []
    state.vaf_tat_deal = []; state.vaf_tat_stage = []; state.vaf_tat_am = []; state.vaf_tat_owner = []; state.vaf_tat_status = []
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
    _aiabot_refresh(state)
    _sync_ms(state)

def _refresh_signal_date_bounds(state):
    """Keep the Daily-signals date picker's min/max window current.

    Module-level defaults are frozen at server start, and on_init only recomputes
    them once, when a browser tab first opens its session. A tab left open across
    a midnight rollover never re-runs on_init, so its max ("yesterday") silently
    goes one day stale for every day it stays open, until the tab is reloaded.
    Called from on_init (new sessions) AND from every scheduled/manual broadcast
    refresh (already-open sessions) so both self-correct without a reload or a
    container rebuild. Only auto-advances the selected date when it was still
    sitting on the previous default (max), so a user who manually picked an older
    day to review isn't overridden.
    """
    _ist_now = _ist_today()
    new_max = _ist_now - timedelta(days=1)
    new_min = _ist_now - timedelta(days=45)
    d, m = state.mkt_sig_date, state.mkt_sig_max
    if isinstance(d, datetime): d = d.date()
    if isinstance(m, datetime): m = m.date()
    at_default = (d == m)
    state.mkt_sig_min = new_min
    state.mkt_sig_max = new_max
    if at_default:
        state.mkt_sig_date = new_max

def on_init(state):
    navigate(state, "aia")
    _refresh_signal_date_bounds(state)
    _refresh_all(state)

def _broadcast_refresh(state):
    """Re-run every page's compute for an already-connected client (no navigation)."""
    try:
        _refresh_signal_date_bounds(state)
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
# PAGE — AIA BOT (AIA bot usage tracker)
# ═══════════════════════════════════════════════════════════════════
_aiaBOT = None   # cached message-level frame (enriched); rebuilt on data reload
_WA_SEG_COLORS = {"Paid": "#2e8b57", "FT": "#e0952f", "Unknown": "#7b8794"}
# bold navy fonts for axes / legend (matches the AIA Ops chart styling)
_WA_AXIS = dict(size=13, color="#1a3a6b", family="Inter,sans-serif", weight="bold")
_WA_LEG  = dict(size=12, color="#1a3a6b", family="Inter,sans-serif", weight="bold")

def _build_aiabot():
    """One row per AIA bot message, enriched with the sender company's
    Segment (Paid / FT / Unknown) and mapped AIA deal.
      company_uuid -> aia_companies (account_id, company_name) -> AIA deal via
      login email. Segment: Paid = deal has payment_date; FT = ft_start_date
      known & no payment; else Unknown (incl. blank company_uuid = unknown number).
    Cached; rebuilt only on data reload (see _reload_data)."""
    global _aiaBOT
    if _aiaBOT is not None:
        return _aiaBOT
    try:
        m = _q(SUPABASE_URL,
            "SELECT message_id, sent_date, company_uuid::text AS cuid, "
            "message_type, interaction_type, intent, answer_status, user_id, "
            "user_request, reply_note "
            "FROM public.aia_bot_messages WHERE is_internal IS NOT TRUE")
    except Exception as ex:
        print(f"[WARN] aia_bot_messages load failed: {ex}")
        _aiaBOT = pd.DataFrame(); return _aiaBOT
    if m is None or len(m) == 0:
        _aiaBOT = pd.DataFrame(); return _aiaBOT
    m["sent_date"] = pd.to_datetime(m["sent_date"], errors="coerce")
    
    # Sort by date first so .last() reliably grabs their most recent company UUID
    uid_to_cuid = m.dropna(subset=["cuid"]).sort_values("sent_date").groupby("user_id")["cuid"].last()
    m["cuid"] = m["cuid"].fillna(m["user_id"].map(uid_to_cuid))
    
    try:
        comp = _q(SUPABASE_URL, "SELECT company_id::text cuid, account_id::text acct, "
                                "company_name, created_by_email email FROM public.aia_companies")
    except Exception:
        comp = pd.DataFrame(columns=["cuid", "acct", "company_name", "email"])
    cmap = comp.set_index("cuid") if len(comp) else comp
    # Two bridges from a company to its AIA deal: (1) account_id via product
    # activity (_acct_for), (2) direct email match (aia_companies.created_by_email
    # -> aia_live.login_email_id). Try account first, then email — maximises the
    # chance a free-trial company (no product activity yet) still links.
    a = _AIA.copy()
    a["_acct"] = a["login_email_id"].map(lambda e: _acct_for(e) if pd.notna(e) else None)
    a["_em"]   = a["login_email_id"].map(lambda e: _clean_email(e) if pd.notna(e) else None)
    by_acct = (a.dropna(subset=["_acct"]).sort_values("payment_date")
                 .drop_duplicates("_acct", keep="last").set_index("_acct"))
    by_em   = (a.dropna(subset=["_em"]).sort_values("payment_date")
                 .drop_duplicates("_em", keep="last").set_index("_em"))
    def _co_attr(cuid):
        row = cmap.loc[cuid] if (len(cmap) and cuid in cmap.index) else None
        cname = row["company_name"] if row is not None else None
        acct  = row["acct"] if row is not None else None
        em    = _clean_email(row["email"]) if (row is not None and pd.notna(row["email"])) else None
        src = None
        if acct is not None and acct in by_acct.index:  src = by_acct.loc[acct]
        elif em is not None and em in by_em.index:       src = by_em.loc[em]
        seg, dname, stage, rid = "Unknown", None, None, None
        if src is not None:
            stage = src["deal_stage"]; dname = src["deal_name"]; rid = src["record_id"]
            if   pd.notna(src["payment_date"]):   seg = "Paid"
            elif pd.notna(src["ft_start_date"]):  seg = "FT"
            else:                                 seg = "Unknown"
        return seg, cname, dname, stage, rid
    attr = {c: _co_attr(c) for c in m["cuid"].dropna().unique().tolist()}
    def _pick(i):
        return m["cuid"].map(lambda c: attr[c][i] if (pd.notna(c) and c in attr) else None)
    m["segment"]      = _pick(0)
    m["company_name"] = _pick(1)
    m["deal_name"]    = _pick(2)
    m["deal_stage"]   = _pick(3)
    m["record_id"]    = _pick(4)
    m["segment"] = m["segment"].fillna("Unknown")
    # blank company_uuid -> keyed per phone so each unknown number is its own "company"
    m["co_key"]  = m["cuid"].where(m["cuid"].notna(), "phone:" + m["user_id"].astype(str))
    def _coname(r):
        if pd.notna(r["company_name"]): return r["company_name"]
        if pd.notna(r["deal_name"]):    return r["deal_name"]
        return f"Unknown ({r['user_id']})"
    m["co_name"] = m.apply(_coname, axis=1)
    _aiaBOT = m
    return _aiaBOT

def _aiabot_cohort_matrix(messages_df, intent_filter=None, company_filter=None, mode="all"):
    """WoW retention heatmap for AIA Bot users (up to 12 weeks).

    SINGLE-COHORT rule: each user is locked to ONE row — the Monday-week of their
    FIRST message ("Users Started"). Any later message only lights up the Wn cell
    of that original cohort row; it never adds to another week's Users Started, so
    that column tracks brand-new users only (no double-count).

    Columns grow with the OLDEST displayed cohort's age (W1..Wn) — no empty future
    columns; it auto-extends by one each week.

    Built with the CS-cohort helper's 'Integration Week' / 'Integrated' headers so
    the count/% merge maths works, then renamed to 'Bot Start Week' / 'Users
    Started' for the UI. The current in-progress week is excluded throughout.
    """
    if messages_df is None or len(messages_df) == 0:
        return pd.DataFrame(), {}

    df = messages_df.dropna(subset=["user_id", "sent_date"]).copy()
    if company_filter:
        df = df[df["co_name"].isin(company_filter)]
    if len(df) == 0:
        return pd.DataFrame(), {}

    # each user's first-message week (Monday) = their single, fixed cohort
    first_dates = df.groupby("user_id")["sent_date"].min().dt.normalize()
    user_start_mon = first_dates - pd.to_timedelta(first_dates.dt.weekday, unit="D")
    user_start_map = user_start_mon.to_dict()
    cohort_of = {}
    for uid, sw in user_start_map.items():
        cohort_of.setdefault(sw, []).append(uid)

    # activity weeks (user, Monday) — the Intent filter narrows what counts as active
    act_df = df.copy()
    if intent_filter:
        act_df = act_df[act_df["intent"].isin(intent_filter)]
    act_norm = act_df["sent_date"].dt.normalize()
    act_df["msg_mon"] = act_norm - pd.to_timedelta(act_norm.dt.weekday, unit="D")
    active_weeks_set = set(zip(act_df["user_id"], act_df["msg_mon"]))

    # last fully-completed Mon–Sun week (the in-progress week is excluded)
    today = pd.Timestamp(date.today()).normalize()
    last_complete_mon = (today - pd.Timedelta(days=today.weekday())) - pd.Timedelta(days=7)

    unique_start_wks = sorted(w for w in user_start_mon.unique() if w <= last_complete_mon)[-12:]
    if not unique_start_wks:
        return pd.DataFrame(), {}

    # dynamic width: only as many W columns as the OLDEST cohort has aged (cap 12)
    oldest = pd.Timestamp(unique_start_wks[0])
    max_off = max(1, min(12, int((last_complete_mon - oldest).days // 7) + 1))
    OFFS = list(range(max_off))

    cnt_rows, pct_rows = [], []
    tot_users = 0
    tot_act = {o: 0 for o in OFFS}; tot_size = {o: 0 for o in OFFS}; tot_valid = {o: False for o in OFFS}

    for wk in unique_start_wks:
        wk = pd.Timestamp(wk)
        cohort_users = cohort_of.get(wk, [])
        size = len(cohort_users); tot_users += size
        label = wk.strftime("%d %b")
        crow = {"Integration Week": label, "Integrated": size}
        prow = {"Integration Week": label, "Integrated": size}
        for o in OFFS:
            cws = wk + pd.Timedelta(days=o * 7)
            col = f"W{o+1}"
            if cws > last_complete_mon:                # future for this cohort -> blank
                crow[col] = ""; prow[col] = ""; continue
            active = sum(1 for u in cohort_users if (u, cws) in active_weeks_set)
            crow[col] = active
            prow[col] = f"{round(active / size * 100) if size else 0}%"
            tot_act[o] += active; tot_size[o] += size; tot_valid[o] = True
        cnt_rows.append(crow); pct_rows.append(prow)

    # weighted-average Total row
    cnt_tot = {"Integration Week": "Total", "Integrated": tot_users}
    pct_tot = {"Integration Week": "Total", "Integrated": tot_users}
    for o in OFFS:
        col = f"W{o+1}"
        cnt_tot[col] = tot_act[o] if tot_valid[o] else ""
        tp = round(tot_act[o] / tot_size[o] * 100) if (tot_valid[o] and tot_size[o]) else 0
        pct_tot[col] = f"{tp}%" if tot_valid[o] else ""
    cnt_rows.append(cnt_tot); pct_rows.append(pct_tot)

    cnt_df, pct_df = pd.DataFrame(cnt_rows), pd.DataFrame(pct_rows)
    merged, heat_from = _merge_cohort_pct_count(cnt_df, pct_df, mode=mode)
    merged = merged.rename(columns={"Integration Week": "Bot Start Week", "Integrated": "Users Started"})
    return merged, heat_from

def _aiabot_refresh(state):
    m = _build_aiabot()
    state.aiabot_all = m
    if m is None or len(m) == 0:
        state.aiabot_kpi_users = "0"; state.aiabot_kpi_paid_users = "0"; state.aiabot_kpi_ft_users = "0"
        state.aiabot_kpi_messages = "0"; state.aiabot_kpi_messages_tip = ""
        state.aiabot_kpi_split = "—"; state.aiabot_kpi_wau = "0"
        state.aiabot_kpi_success = "—"; state.aiabot_kpi_success_tip = ""
        state.aiabot_stage_list = []; state.aiabot_deal_list = []
        state.aiabot_adopt_fig = go.Figure(); state.aiabot_intent_fig = go.Figure()
        state.aiabot_trend_fig = go.Figure()
        state.aiabot_fail_intent_list = []
        state.aiabot_fail_json = grid_payload_b64(pd.DataFrame())
        state.aiabot_table_json = grid_payload_b64(pd.DataFrame())
        return
    state.aiabot_stage_list = sorted(m["deal_stage"].dropna().unique().tolist())
    state.aiabot_deal_list  = sorted(m["deal_name"].dropna().unique().tolist())
    base = m.copy()
    _seg = _sel(state.aiabot_segment)
    if _seg: base = base[base["segment"].isin(_seg)]
    _st = _sel(state.aiabot_stage)
    if _st: base = base[base["deal_stage"].isin(_st)]
    _dl = _sel(state.aiabot_deal)
    if _dl: base = base[base["deal_name"].isin(_dl)]
    # Shared ops date range (aia_start/end — same picker as AIA/VA Ops & Marketing
    # UTM) applies to KPIs / adoption / intent / table; the weekly trend below stays
    # on `base` (all weeks) since it's a week-over-week view.
    _ds = pd.Timestamp(state.aia_start_date); _de = pd.Timestamp(state.aia_end_date)
    d = base[(base["sent_date"] >= _ds) & (base["sent_date"] <= _de)]

    # ── KPIs ──
    seg_co = d.groupby("segment")["co_key"].nunique().to_dict()    # companies per segment (Adoption chart)
    users  = d.groupby("segment")["user_id"].nunique().to_dict()   # distinct WA numbers per segment
    state.aiabot_kpi_users = _grp(d["user_id"].nunique())
    state.aiabot_kpi_paid_users = _grp(int(users.get('Paid', 0)))
    state.aiabot_kpi_ft_users = _grp(int(users.get('FT', 0)))
    state.aiabot_kpi_messages = _grp(len(d))
    _mseg = d.groupby("segment").size().to_dict()
    state.aiabot_kpi_messages_tip = (f"Paid: {int(_mseg.get('Paid',0))}\n"
                                    f"FT: {int(_mseg.get('FT',0))}\n"
                                    f"Unknown: {int(_mseg.get('Unknown',0))}")
    up = int((d["interaction_type"] == "upload").sum()); qy = int((d["interaction_type"] == "query").sum())
    state.aiabot_kpi_split = (f"{round(up/(up+qy)*100)}% upload" if (up + qy) else "—")
    if d["sent_date"].notna().any():
        mx = d["sent_date"].max().normalize()
        state.aiabot_kpi_wau = str(d[d["sent_date"] >= mx - pd.Timedelta(days=6)]["co_key"].nunique())
    else:
        state.aiabot_kpi_wau = "0"
    stt = d["answer_status"]; tot_status = int(stt.notna().sum())
    _succ = int((stt == "success").sum())
    if tot_status:
        state.aiabot_kpi_success = f"{round(_succ/tot_status*100)}%"
        _nonsucc = stt[stt.notna() & (stt != "success")]
        _topreason = (_nonsucc.value_counts().idxmax() if len(_nonsucc) else "—")
        state.aiabot_kpi_success_tip = (f"{_succ} of {tot_status} succeeded\n"
                                       f"{round((tot_status-_succ)/tot_status*100)}% failed · top reason: {_topreason}")
    else:
        state.aiabot_kpi_success = "—"; state.aiabot_kpi_success_tip = ""

    # ── Chart A: adoption ──
    segs = ["Paid", "FT", "Unknown"]
    seg_labels = ["Paid", "Free Trials", "Unknown"]   # x-axis display only; segment value stays "FT"
    co_counts  = [int(seg_co.get(s, 0)) for s in segs]
    msg_counts = [int((d["segment"] == s).sum()) for s in segs]
    figA = go.Figure()
    figA.add_bar(x=seg_labels, y=co_counts, name="Companies",
                 marker_color=[_WA_SEG_COLORS[s] for s in segs], text=co_counts,
                 textposition="outside", cliponaxis=False)
    figA.add_bar(x=seg_labels, y=msg_counts, name="Messages", marker_color="#3b82c4",
                 text=msg_counts, textposition="outside", cliponaxis=False)
    _amax = max([0] + co_counts + msg_counts)
    figA.update_layout(barmode="group", height=430, margin=dict(l=30, r=10, t=40, b=30),
                       paper_bgcolor=_bg, plot_bgcolor=_bg, font=_font,
                       legend=dict(orientation="h", y=-0.16, font=_WA_LEG),
                       xaxis=dict(tickfont=_WA_AXIS),
                       yaxis=dict(range=[0, _amax * 1.18], tickfont=_WA_AXIS))
    state.aiabot_adopt_fig = figA

    # ── Chart B: intent × interaction ──
    it = d[d["intent"].notna()]
    figB = go.Figure()
    if len(it):
        piv = it.groupby(["intent", "interaction_type"]).size().unstack(fill_value=0)
        piv["_tot"] = piv.sum(axis=1); piv = piv.sort_values("_tot")
        for itype, color in [("upload", "#3b82c4"), ("query", "#e0872f"), ("voice", "#7b8794")]:
            if itype in piv.columns:
                _vals = piv[itype].tolist()
                # value sits ON the segment (trace text) so it disappears when the
                # series is toggled off in the legend
                figB.add_bar(y=piv.index.tolist(), x=_vals, name=itype.title(),
                             orientation="h", marker_color=color,
                             text=[f"<b>{int(v)}</b>" if v else "" for v in _vals],
                             textposition="inside", insidetextanchor="middle",
                             textfont=dict(size=11, color="#ffffff", family="Inter,sans-serif"),
                             cliponaxis=False)
    figB.update_layout(barmode="stack", height=430, margin=dict(l=10, r=20, t=20, b=30),
                       paper_bgcolor=_bg, plot_bgcolor=_bg, font=_font, bargap=0.25,
                       uniformtext=dict(minsize=9, mode="hide"),
                       legend=dict(orientation="h", y=-0.13, font=_WA_LEG),
                       xaxis=dict(tickfont=_WA_AXIS),
                       yaxis=dict(ticklabelstandoff=5,   # 5px gap between the intent name and the bar
                                  tickfont=dict(size=14, color="#1a3a6b",
                                                family="Inter,sans-serif", weight="bold")))
    state.aiabot_intent_fig = figB

    # ── Chart C: weekly trend ── built from `base` (all weeks; ignores the date
    # range) but still respects the Segment / Deal Stage / Deal Name dropdowns.
    dd = base[base["sent_date"].notna()].copy()
    figC = go.Figure()
    xaxis_cfg = {}; _msgmax = 0; _comax = 0
    if len(dd):
        # weeks Monday → Sunday (W-SUN start_time is Monday)
        dd["_wkstart"] = dd["sent_date"].dt.to_period("W-SUN").dt.start_time
        wk = dd.groupby("_wkstart").agg(msgs=("message_id", "count"),
                                        cos=("co_key", "nunique")).reset_index()
        wk["_range"] = wk["_wkstart"].dt.strftime("%d %b") + " – " + (wk["_wkstart"] + pd.Timedelta(days=6)).dt.strftime("%d %b")
        _msgmax = float(wk["msgs"].max()); _comax = float(wk["cos"].max())
        # bold Messages value sits above each bar
        figC.add_bar(x=wk["_wkstart"], y=wk["msgs"], name="Messages", marker_color="#3b82c4", yaxis="y",
                     text=[f"<b>{int(v)}</b>" for v in wk["msgs"]], textposition="outside",
                     textfont=dict(size=11, color="#1a3a6b", family="Inter,sans-serif"), cliponaxis=False,
                     customdata=wk["_range"], hovertemplate="Week %{customdata}<br>Messages: %{y}<extra></extra>")
        figC.add_scatter(x=wk["_wkstart"], y=wk["cos"], name="Active companies", legendgroup="wa_co",
                         mode="lines+markers", line=dict(color="#1f4e79", width=3),
                         marker=dict(color="#1f4e79", size=7), yaxis="y2",
                         customdata=wk["_range"], hovertemplate="Week %{customdata}<br>Active companies: %{y}<extra></extra>")
        # Companies value in a boxed marker ABOVE each point, adaptively lifted so it
        # clears the Messages label on the bar. It's a legend-grouped TRACE (not an
        # annotation) → it hides together with the line when the series is toggled off.
        _H = 316.0                                   # ~plot-area height px (h400 - t36 - b48)
        _mr = (_msgmax * 1.22) or 1; _cr = (_comax * 1.5) or 1
        _ppu2 = _H / _cr                             # px per company-unit on the right axis
        _boxy = []
        for mv, cv in zip(wk["msgs"], wk["cos"]):
            line_px = cv / _cr * _H
            msg_label_px = mv / _mr * _H + 12        # message label ~12px above the bar top
            box_px = line_px + 16                    # default: 16px above the point
            if abs(box_px - msg_label_px) < 18:      # would collide with the message label
                box_px = msg_label_px + 22           # lift clearly above it
            _boxy.append(cv + (box_px - line_px) / _ppu2)   # px lift → y2 data units
        figC.add_scatter(x=wk["_wkstart"], y=_boxy, yaxis="y2", legendgroup="wa_co", showlegend=False,
                         mode="markers+text", cliponaxis=False, hoverinfo="skip",
                         marker=dict(symbol="square", size=22, color="#1f4e79",
                                     line=dict(color="#143a5c", width=1)),
                         text=[f"<b>{int(v)}</b>" for v in wk["cos"]], textposition="middle center",
                         textfont=dict(size=10, color="#ffffff", family="Inter,sans-serif"))
        # pin ticks to the Monday week-starts so labels match the bars (not Plotly's Sunday auto-ticks)
        xaxis_cfg = dict(tickmode="array", tickvals=wk["_wkstart"].tolist(),
                         ticktext=wk["_wkstart"].dt.strftime("%d %b").tolist())
    xaxis_cfg["tickfont"] = _WA_AXIS
    figC.update_layout(height=400, margin=dict(l=40, r=40, t=36, b=48),
                       paper_bgcolor=_bg, plot_bgcolor=_bg, font=_font,
                       legend=dict(orientation="h", y=-0.2, font=_WA_LEG), xaxis=xaxis_cfg,
                       yaxis=dict(title=dict(text="Messages", font=_WA_AXIS), tickfont=_WA_AXIS,
                                  range=([0, _msgmax * 1.22] if _msgmax else None)),
                       yaxis2=dict(title=dict(text="Companies", font=_WA_AXIS), tickfont=_WA_AXIS,
                                   overlaying="y", side="right", showgrid=False,
                                   range=([0, _comax * 1.5] if _comax else None)))
    state.aiabot_trend_fig = figC

    # ── Where the bot falls short — non-success messages (Intent synced with the
    # cohort Intent filter; Company filter also applies here) ──
    _c_company = _sel(state.aiabot_cohort_company)
    _c_intent  = _sel(state.aiabot_cohort_intent)   # synced with aiabot_fail_intent
    # Company ↔ Intent cross-filter each other's option lists: each list narrows by
    # the OTHER's selection, so picking an intent trims the Company dropdown & vice versa.
    _int_scoped  = base if not _c_company else base[base["co_name"].isin(_c_company)]
    _co_scoped   = base if not _c_intent  else base[base["intent"].isin(_c_intent)]
    _intent_lov  = sorted(_int_scoped["intent"].dropna().unique().tolist())
    _company_lov = sorted(_co_scoped["co_name"].dropna().unique().tolist())
    state.aiabot_fail_intent_list = _intent_lov
    fail = d[d["answer_status"].notna() & (d["answer_status"] != "success")].copy()
    _fi = _sel(state.aiabot_fail_intent)
    if _fi:        fail = fail[fail["intent"].isin(_fi)]
    if _c_company: fail = fail[fail["co_name"].isin(_c_company)]
    if len(fail):
        fail = fail.sort_values("sent_date", ascending=False)
        fdf = pd.DataFrame({
            "Date": fail["sent_date"].dt.strftime("%d-%b-%y"),
            "Intent": fail["intent"].fillna(""),
            "Status": fail["answer_status"].fillna(""),
            "User Request": fail["user_request"].fillna(""),
            "Bot Reply": fail["reply_note"].fillna(""),
            "Company": fail["co_name"],
        }).reset_index(drop=True)
        fdf.insert(0, "Sl no", range(1, len(fdf) + 1))
        state.aiabot_fail_json = grid_payload_b64(
            fdf, rownum_col="Sl no", sortable=True, no_sort=True, hdr_center=True,
            fixed_layout=True,   # columns ellipsize right at their own edge
            center_cols=["Date", "Status"],   # these cells centered; Intent/User Request/Bot Reply/Company left
            col_w={"Sl no": 55, "Date": 95, "Status": 145, "Intent": 160,
                   "User Request": 300, "Bot Reply": 520, "Company": 240},
            date_cols=["Date"])
    else:
        state.aiabot_fail_json = grid_payload_b64(pd.DataFrame())
    # ── AIA Bot Activity Cohort ──
    state.aiabot_cohort_intent_list = _intent_lov
    state.aiabot_cohort_company_list = _company_lov

    _c_view = _sel(state.aiabot_cohort_view)
    # View: "Cohort %" -> % only, "Users" -> counts only, else both (default).
    _c_mode = ("pct" if _c_view == ["Cohort %"] else "count" if _c_view == ["Users"] else "all")

    coh_df, coh_heat_from = _aiabot_cohort_matrix(base, intent_filter=_c_intent,
                                                  company_filter=_c_company, mode=_c_mode)
    _coh_heat = {c: "green" for c in coh_df.columns if str(c).startswith("W")}

    if len(coh_df):
        state.aiabot_cohort_json = grid_payload_b64(
            coh_df, total_id_col="Bot Start Week",
            no_sort=True, fixed=True, sortable=False,
            center_all=True, heat_cols=_coh_heat, autosize=True,
            heat_from=coh_heat_from, heat_max=100
        )
    else:
        state.aiabot_cohort_json = grid_payload_b64(pd.DataFrame())
    
    # ── Per-Company Detail ── also respects the cohort Company + Intent filters
    dpc = d
    if _c_company: dpc = dpc[dpc["co_name"].isin(_c_company)]
    if _c_intent:  dpc = dpc[dpc["intent"].isin(_c_intent)]
    rows = []
    for ck, grp in dpc.groupby("co_key"):
        st2 = grp["answer_status"]; tots = int(st2.notna().sum())
        intent_top = grp["intent"].dropna()
        # phone-shaped user_ids only — a few messages mis-log the company UUID in
        # this field, which isn't a real WA number.
        wa_nums = ", ".join(sorted(u for u in grp["user_id"].dropna().astype(str).unique() if u.isdigit()))
        rows.append({
            "Deal Name": grp["deal_name"].dropna().iloc[0] if grp["deal_name"].notna().any() else "",
            "record_id": str(grp["record_id"].dropna().iloc[0]) if grp["record_id"].notna().any() else "",
            "Deal Stage": grp["deal_stage"].dropna().iloc[0] if grp["deal_stage"].notna().any() else "",
            "Company": grp["co_name"].iloc[0],
            "Segment": grp["segment"].iloc[0],
            "WA Number": wa_nums,
            "Messages": len(grp),
            "Uploads": int((grp["interaction_type"] == "upload").sum()),
            "Queries": int((grp["interaction_type"] == "query").sum()),
            "Top Intent": (intent_top.value_counts().idxmax() if len(intent_top) else ""),
            "Active Days": int(grp["sent_date"].dt.normalize().nunique()),
            "Last Seen": (grp["sent_date"].max().strftime("%d-%b-%y") if grp["sent_date"].notna().any() else ""),
            "Success %": (round((st2 == "success").sum()/tots*100) if tots else 0),
        })
    t = pd.DataFrame(rows).sort_values("Messages", ascending=False).reset_index(drop=True)
    t.insert(0, "Sl no", range(1, len(t) + 1))
    state.aiabot_table_json = grid_payload_b64(
        t, sort_default_col="Messages", rownum_col="Sl no",
        col_w={"Company": 240, "Deal Name": 220, "WA Number": 150},
        center_cols=["Deal Stage", "Segment", "WA Number", "Messages", "Uploads", "Queries",
                     "Active Days", "Last Seen", "Success %"],
        date_cols=["Last Seen"],
        heat_cols={"Messages": "green", "Active Days": "blue"},
        link_cols={"Deal Name": ("record_id", "https://app-na2.hubspot.com/contacts/39668252/record/0-3/")})


# ═══════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════

from pages.aia_ops    import AIA_OPS_PAGE
from pages.cs_finance import CS_FINANCE_PAGE
from pages.marketing  import MARKETING_PAGE
from pages.va_ops     import VA_OPS_PAGE
from pages.va_finance import VA_FINANCE_PAGE
from pages.aia_bot     import AIA_BOT_PAGE

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
    ("/aia-bot",     "AIA Bot"),
]

pages = {
    "/":          ROOT_PAGE,
    "aia":        AIA_OPS_PAGE,
    "cs":         CS_FINANCE_PAGE,
    "marketing":  MARKETING_PAGE,
    "va-ops":     VA_OPS_PAGE,
    "va-finance": VA_FINANCE_PAGE,
    "aia-bot":     AIA_BOT_PAGE,
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