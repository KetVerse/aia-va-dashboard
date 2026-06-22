#!/usr/bin/env python3
"""
Daily dashboard PDF snapshot — all 5 pages, all tables fully expanded, charts embedded.
Runs at 7:15 PM IST daily via n8n (SSH trigger).

Setup on VPS (one-time):
    pip install weasyprint kaleido google-api-python-client google-auth
    # Place Google service-account JSON at /opt/taipy-dashboard/gdrive-key.json
    # Add GDRIVE_FOLDER_ID to /opt/taipy-dashboard/.env

Output: /tmp/dashboard_YYYYMMDD_HHMM_IST.pdf  (also uploaded to Google Drive)
Prints OUTPUT_PATH=<path> and GDRIVE_URL=<url> to stdout for n8n to parse.
"""

import os, sys, base64, io, traceback
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import psycopg2
import plotly.graph_objects as go
import plotly.io as pio
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv("/opt/taipy-dashboard/.env")

_IST      = timezone(timedelta(hours=5, minutes=30))
_NOW      = datetime.now(_IST)
_TODAY    = _NOW.date()
_OUT_DIR  = Path("/tmp")
_KEY_FILE = Path("/opt/taipy-dashboard/gdrive-key.json")

HUBSPOT_BASE = "https://app-na2.hubspot.com/contacts/39668252/record/0-3/"

# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def _neon_conn():
    return psycopg2.connect(os.environ["NEON_DATABASE_URL"])

def _supa_conn():
    return psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"])

def _query(conn, sql):
    return pd.read_sql(sql, conn)

def load_data():
    print("[report] Loading data from databases...")
    neon = _neon_conn()
    supa = _supa_conn()

    aia  = _query(neon, "SELECT * FROM aia_live")
    va   = _query(neon, "SELECT * FROM va_live")
    li   = _query(neon, "SELECT * FROM line_items")
    inc  = _query(neon, "SELECT * FROM incentive_targets") if _table_exists(neon, "incentive_targets") else pd.DataFrame()

    mkt  = _query(supa, "SELECT * FROM marketing_spend")  if _table_exists(supa, "marketing_spend")  else pd.DataFrame()
    upl  = _query(supa, "SELECT * FROM usage_logs")       if _table_exists(supa, "usage_logs")       else pd.DataFrame()
    syn  = _query(supa, "SELECT * FROM sync_logs")        if _table_exists(supa, "sync_logs")        else pd.DataFrame()

    neon.close(); supa.close()
    print(f"[report]   AIA={len(aia)} VA={len(va)} LI={len(li)} MKT={len(mkt)}")
    return aia, va, li, inc, mkt, upl, syn

def _table_exists(conn, name):
    cur = conn.cursor()
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None

# ═══════════════════════════════════════════════════════════════════════════════
# 2. HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _dt(df, col):
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df

def _prep_aia(raw):
    df = raw.copy()
    for c in ["create_date","ds_date","dc_date","payment_date","churned_date",
              "parked_date","discard_date","closed_lost_date","renewed_date",
              "integration_done_date","activation_date","adopted_date"]:
        _dt(df, c)
    return df

def _prep_va(raw):
    df = raw.copy()
    for c in ["create_date","ds_date","dc_date","payment_date","renewed_date"]:
        _dt(df, c)
    return df

def _prep_li(raw):
    df = raw.copy()
    for c in ["date_paid","billing_start_date"]:
        _dt(df, c)
    if "unit_price" in df.columns:
        df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0)
    if "term" in df.columns:
        df["term"] = pd.to_numeric(df["term"], errors="coerce").fillna(12)
    if "mrr" not in df.columns and "unit_price" in df.columns and "term" in df.columns:
        df["mrr"] = df["unit_price"] / df["term"].replace(0, 12)
    return df

def _rng(df, col, s, e):
    if col not in df.columns: return df.iloc[0:0]
    return df[(df[col] >= pd.Timestamp(s)) & (df[col] <= pd.Timestamp(e))]

def _fmt(n):
    n = int(n)
    if abs(n) >= 1_00_00_000: return f"₹{n/1_00_00_000:.1f}Cr"
    if abs(n) >= 1_00_000:    return f"₹{n/1_00_000:.1f}L"
    if abs(n) >= 1_000:       return f"₹{n/1_000:.0f}K"
    return f"₹{n:,}"

def _pct(n, d):
    return f"{n/d*100:.0f}%" if d else "—"

# ═══════════════════════════════════════════════════════════════════════════════
# 3. CHART HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_NAVY = "#1a3a6b"

def _fig_to_b64(fig, w=900, h=380):
    try:
        png = pio.to_image(fig, format="png", width=w, height=h, scale=2)
        return "data:image/png;base64," + base64.b64encode(png).decode()
    except Exception as e:
        print(f"[report] Chart render failed: {e}")
        return ""

def _funnel_fig(labels, values):
    fig = go.Figure(go.Funnel(
        y=labels, x=values,
        texttemplate="%{value:,} (%{percentInitial:.0%})",
        marker=dict(color=["#1a7fc4","#2196f3","#42a5f5","#64b5f6","#90caf9"]),
    ))
    fig.update_layout(margin=dict(l=80,r=20,t=20,b=20), paper_bgcolor="white",
                      font=dict(family="Inter,sans-serif", size=12))
    return fig

def _bar_line_fig(x, bar_y, line_y, bar_name="DC", line_name="Qualified"):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=x, y=bar_y, name=bar_name, marker_color="#42a5f5"))
    fig.add_trace(go.Scatter(x=x, y=line_y, name=line_name, mode="lines+markers",
                             line=dict(color=_NAVY, width=2)))
    fig.update_layout(margin=dict(l=40,r=20,t=20,b=60), paper_bgcolor="white",
                      font=dict(family="Inter,sans-serif", size=11),
                      legend=dict(orientation="h", y=-0.25))
    return fig

def _pie_fig(labels, values, colors):
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.45,
                           marker=dict(colors=colors),
                           texttemplate="<b>%{label}</b> %{percent}"))
    fig.update_layout(margin=dict(l=40,r=40,t=20,b=40), paper_bgcolor="white",
                      font=dict(family="Inter,sans-serif", size=11),
                      legend=dict(orientation="h", y=-0.1))
    return fig

# ═══════════════════════════════════════════════════════════════════════════════
# 4. HTML BUILDING BLOCKS
# ═══════════════════════════════════════════════════════════════════════════════

def _kpi_cards(items):
    """items = list of (label, value, color)  color: 'blue'|'green'|'red'|'gray'"""
    _bg = {"blue":"#1a7fc4","green":"#16a34a","red":"#dc2626","gray":"#475569"}
    cards = "".join(
        f'<div class="kpi" style="background:{_bg.get(c,"#1a7fc4")}">'
        f'<div class="kpi-label">{l}</div><div class="kpi-val">{v}</div></div>'
        for l, v, c in items
    )
    return f'<div class="kpi-row">{cards}</div>'

def _df_table(df, title="", link_col=None, id_col="record_id"):
    """Render a DataFrame as a full HTML table (all rows, no pagination)."""
    if df is None or len(df) == 0:
        return f'<div class="section-title">{title}</div><p class="empty">No data</p>'

    cols = [c for c in df.columns if c != id_col]
    has_link = link_col and link_col in cols and id_col in df.columns

    html = f'<div class="section-title">{title}</div>' if title else ""
    html += '<div class="tbl-wrap"><table>'
    html += "<thead><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr></thead>"
    html += "<tbody>"
    for _, row in df.iterrows():
        html += "<tr>"
        for c in cols:
            v = row[c]
            cell = "" if (v is None or (not isinstance(v, str) and pd.isna(v))) else str(v)
            if has_link and c == link_col:
                rid = row.get(id_col, "")
                if rid:
                    cell = f'<a href="{HUBSPOT_BASE}{rid}" style="color:#1a7fc4">{cell}</a>'
            html += f"<td>{cell}</td>"
        html += "</tr>"
    html += "</tbody></table></div>"
    return html

def _chart_block(b64, title="", w="100%"):
    if not b64:
        return ""
    return (f'<div class="section-title">{title}</div>' if title else "") + \
           f'<img src="{b64}" style="width:{w};max-width:100%;margin-bottom:12px">'

def _page_break():
    return '<div style="page-break-after:always"></div>'

# ═══════════════════════════════════════════════════════════════════════════════
# 5. PAGE BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _page_header(title, refreshed):
    return (f'<div class="page-header"><span class="page-title">{title}</span>'
            f'<span class="sync-stamp">Refreshed at: {refreshed}</span></div>')

def build_aia_page(aia_raw, li_raw, inc_raw):
    df  = _prep_aia(aia_raw)
    li  = _prep_li(li_raw)
    today = pd.Timestamp(_TODAY)
    s = today.replace(day=1)
    e = today

    sub   = _rng(df, "create_date", s, e)
    ds    = _rng(df, "ds_date", s, e)
    dc    = _rng(df, "dc_date", s, e)
    paid_ = _rng(df, "payment_date", s, e)
    if "asked_refund" in paid_.columns:
        paid_net = paid_[paid_["asked_refund"].isna()]
    else:
        paid_net = paid_

    hi_mask = df["deal_stage"] == "High Intent" if "deal_stage" in df.columns else pd.Series(False, index=df.index)
    hi = df[(df["dc_date"].notna()) & hi_mask] if "dc_date" in df.columns else df.iloc[0:0]

    leads  = sub["record_id"].nunique() if "record_id" in sub.columns else 0
    ds_n   = ds["record_id"].nunique()  if "record_id" in ds.columns  else 0
    dc_n   = dc["record_id"].nunique()  if "record_id" in dc.columns  else 0
    hi_n   = hi["record_id"].nunique()  if "record_id" in hi.columns  else 0
    paid_n = paid_net["record_id"].nunique() if "record_id" in paid_net.columns else 0

    aia_paid = paid_["record_id"].nunique() if "record_id" in paid_.columns else 0
    rev = int(paid_net.groupby("record_id")["amount_paid"].max().sum()) if "amount_paid" in paid_net.columns and "record_id" in paid_net.columns else 0
    active_li = li[li["due_on"] >= today] if "due_on" in li.columns else li
    mrr = int(active_li["unit_price"].sum()) if "unit_price" in active_li.columns else 0

    kpis = _kpi_cards([
        ("Leads",    leads,   "blue"),
        ("DS",       ds_n,    "blue"),
        ("DC",       dc_n,    "blue"),
        ("High Intent", hi_n, "blue"),
        ("AIA Paid", aia_paid,"blue"),
        ("Paid",     paid_n,  "blue"),
        ("Revenue",  _fmt(rev),"green"),
        ("MRR",      _fmt(mrr),"green"),
    ])

    # Funnel
    funnel_labels = ["Leads","DS","DC","High Intent","Paid"]
    funnel_vals   = [leads, ds_n, dc_n, hi_n, paid_n]
    funnel_img = _fig_to_b64(_funnel_fig(funnel_labels, funnel_vals), w=500, h=350)

    # Trend (DC vs Qualified last 30d)
    trend_img = ""
    if "dc_date" in df.columns and "deal_stage" in df.columns:
        last30 = pd.Timestamp(_TODAY) - pd.Timedelta(days=30)
        dc30   = df[df["dc_date"] >= last30].copy()
        if len(dc30):
            dc30["_d"] = dc30["dc_date"].dt.date
            by_day = dc30.groupby("_d")["record_id"].nunique()
            qual   = dc30[dc30["deal_stage"].isin(["High Intent","Paid","AIA Paid"])].groupby("_d")["record_id"].nunique()
            x = sorted(set(by_day.index) | set(qual.index))
            trend_img = _fig_to_b64(
                _bar_line_fig([str(d) for d in x],
                              [int(by_day.get(d, 0)) for d in x],
                              [int(qual.get(d, 0)) for d in x]),
                w=900, h=300)

    # GM Performance
    gm_html = ""
    if "deal_owner" in df.columns and "record_id" in df.columns:
        gm_rows = []
        for gm, g in df[df["payment_date"].notna()].groupby("deal_owner"):
            gsub   = _rng(g, "create_date", s, e)
            gdc    = _rng(g, "dc_date", s, e)
            gpaid  = _rng(g, "payment_date", s, e)
            grev   = int(gpaid.groupby("record_id")["amount_paid"].max().sum()) if "amount_paid" in gpaid.columns else 0
            gli    = li[li["record_id"].isin(gpaid["record_id"].values)] if "record_id" in li.columns else li.iloc[0:0]
            gmrr   = int(gli["mrr"].sum()) if "mrr" in gli.columns else 0
            gm_rows.append({"GM": gm,
                            "Leads": gsub["record_id"].nunique() if "record_id" in gsub.columns else 0,
                            "DC": gdc["record_id"].nunique() if "record_id" in gdc.columns else 0,
                            "Paid": gpaid["record_id"].nunique() if "record_id" in gpaid.columns else 0,
                            "Revenue": grev, "MRR": gmrr})
        if gm_rows:
            gm_df = pd.DataFrame(gm_rows).sort_values("Revenue", ascending=False)
            tot   = gm_df.select_dtypes("number").sum().to_dict(); tot["GM"] = "Total"
            gm_df = pd.concat([gm_df, pd.DataFrame([tot])], ignore_index=True)
            gm_html = _df_table(gm_df, "GM Performance")

    # Incentive Tracker
    inc_html = ""
    if len(inc_raw):
        _dt(inc_raw, "month")
        curr = inc_raw[inc_raw["month"] == pd.Timestamp(s)]
        if len(curr):
            inc_rows = []
            for _, tr in curr.iterrows():
                gm = tr.get("gm_combined", "")
                tgt = int(tr.get("monthly_mrr_target", 0))
                gp  = _rng(df[df["deal_owner"] == gm] if "deal_owner" in df.columns else df.iloc[0:0],
                            "payment_date", s, e)
                rev2 = int(gp.groupby("record_id")["amount_paid"].max().sum()) if "amount_paid" in gp.columns and len(gp) else 0
                ach  = rev2 / tgt if tgt else 0
                tier = ("Accelerated (>130%)" if ach > 1.30 else
                        ("Base (70-130%)" if ach >= 0.70 else
                         ("Under (<70%)" if rev2 > 0 else "No Revenue")))
                inc_rows.append({"GM": gm, "Revenue": rev2, "Target": tgt,
                                 "Achievement": f"{ach*100:.1f}%", "Tier": tier})
            if inc_rows:
                inc_html = _df_table(pd.DataFrame(inc_rows).sort_values("Revenue", ascending=False),
                                     "AIA + VA Incentive Tracker")

    return (
        _page_header("AIA Ops Dashboard", _NOW.strftime("%d %b %Y – %H:%M IST")) +
        kpis +
        '<div class="two-col">' +
        _chart_block(funnel_img, "Marketing Funnel (Cohort)") +
        _chart_block(trend_img, "Demo Conducted vs Qualified Trend") +
        '</div>' +
        gm_html + inc_html
    )


def build_cs_page(aia_raw, li_raw):
    df  = _prep_aia(aia_raw)
    li  = _prep_li(li_raw)
    today = pd.Timestamp(_TODAY)
    paid_all = df[df["payment_date"].notna()]

    paid_n   = paid_all["record_id"].nunique() if "record_id" in paid_all.columns else 0
    aia_paid = paid_all[paid_all["module_type"] == "AIA Paid"]["record_id"].nunique() if "module_type" in paid_all.columns else 0
    active_li = li[li["due_on"] >= today] if "due_on" in li.columns else li
    mrr = int(active_li["unit_price"].sum()) if "unit_price" in active_li.columns else 0

    kpis = _kpi_cards([
        ("Paid (All time)", paid_n, "blue"),
        ("AIA Paid",   aia_paid, "blue"),
        ("MRR",        _fmt(mrr), "green"),
    ])

    # Usage & Health (all integration-done AIA Paid customers)
    usage_html = ""
    int_done = df[(df["integration_done_date"].notna()) &
                  (df["login_email_id"].notna()) &
                  (df["module_type"] == "AIA Paid")] if "integration_done_date" in df.columns else df.iloc[0:0]
    if len(int_done):
        def _ddmy(v): return pd.Timestamp(v).strftime("%d-%b-%y") if pd.notna(v) else ""
        rows = []
        for _, row in int_done.iterrows():
            rows.append({
                "Deal Name":   row.get("deal_name", ""),
                "record_id":   row.get("record_id", ""),
                "CSM":         row.get("cs_owner", ""),
                "Stage":       row.get("deal_stage", ""),
                "Paid On":     _ddmy(row.get("payment_date")),
                "Int Date":    _ddmy(row.get("integration_done_date")),
            })
        usage_html = _df_table(pd.DataFrame(rows), "Customer Usage & Health",
                               link_col="Deal Name", id_col="record_id")

    # Renewal Window ±14d
    rw_html = ""
    if "deal_stage" in df.columns:
        rw = df[df["deal_stage"].isin(["Ready for Renewal", "Renewal Done"])].copy()
        billing_end = {}
        if "record_id" in li.columns and "billing_start_date" in li.columns and "term" in li.columns:
            li2 = li.copy()
            li2["_end"] = li2["billing_start_date"] + pd.to_timedelta(li2["term"].fillna(12) * 30, unit="D")
            billing_end = li2.groupby("record_id")["_end"].max().to_dict()
        rw["_due"] = pd.to_datetime(rw["record_id"].map(billing_end), errors="coerce") if "record_id" in rw.columns else pd.NaT
        rw = rw[(rw["_due"] >= today - pd.Timedelta(days=14)) & (rw["_due"] <= today + pd.Timedelta(days=14))]
        if len(rw):
            rwd = pd.DataFrame({
                "Due On":    rw["_due"].dt.strftime("%d-%b-%y"),
                "Deal Name": rw.get("deal_name", ""),
                "record_id": rw["record_id"].values,
                "CSM":       rw.get("cs_owner", ""),
                "Stage":     rw.get("deal_stage", ""),
                "Amount":    rw.get("amount_paid", 0),
            })
            rw_html = _df_table(rwd, "CS Renewal Window (±14 days)",
                                link_col="Deal Name", id_col="record_id")

    return (
        _page_header("CS & Finance Dashboard", _NOW.strftime("%d %b %Y – %H:%M IST")) +
        kpis + usage_html + rw_html
    )


def build_marketing_page(mkt_raw, aia_raw):
    df  = _prep_aia(aia_raw)
    today = pd.Timestamp(_TODAY)
    s = today.replace(day=1); e = today

    _dt(mkt_raw, "day")
    mkt = mkt_raw.copy()
    total_spend = int(mkt["cost"].sum()) if "cost" in mkt.columns else 0
    leads = _rng(df, "create_date", s, e)["record_id"].nunique() if "record_id" in df.columns else 0
    paid  = _rng(df, "payment_date", s, e)["record_id"].nunique() if "record_id" in df.columns else 0
    cac   = int(total_spend / paid) if paid else 0
    cpl   = int(total_spend / leads) if leads else 0

    kpis = _kpi_cards([
        ("Total Spend",   _fmt(total_spend), "blue"),
        ("Leads",         leads,             "blue"),
        ("Paid",          paid,              "blue"),
        ("CAC",           _fmt(cac),         "gray"),
        ("CPL",           _fmt(cpl),         "gray"),
    ])

    # Monthly table
    monthly_html = ""
    if "day" in mkt.columns and "channel" in mkt.columns:
        mkt2 = mkt.copy()
        mkt2["Month"] = mkt2["day"].dt.to_period("M").astype(str)
        monthly = mkt2.groupby(["Month","channel"])["cost"].sum().unstack(fill_value=0)
        monthly["Total"] = monthly.sum(axis=1)
        monthly = monthly.reset_index().sort_values("Month", ascending=False)
        monthly_html = _df_table(monthly, "Monthly Marketing Spend")

    # Channel pie
    pie_img = ""
    if "channel" in mkt.columns and "cost" in mkt.columns:
        by_ch = mkt.groupby("channel")["cost"].sum().reset_index()
        by_ch = by_ch[by_ch["cost"] > 0].sort_values("cost", ascending=False)
        _CH_COLORS = {"google ads":"#FBBC05","linkedin ads":"#42a5f5","meta ads":"#1877F2",
                      "meta":"#1877F2","linkedin":"#42a5f5","facebook":"#1877F2"}
        colors = [_CH_COLORS.get(str(l).lower(), "#8b5cf6") for l in by_ch["channel"]]
        if len(by_ch):
            pie_img = _fig_to_b64(_pie_fig(by_ch["channel"].tolist(),
                                           by_ch["cost"].tolist(), colors), w=600, h=350)

    return (
        _page_header("AIA Marketing Tracker", _NOW.strftime("%d %b %Y – %H:%M IST")) +
        kpis +
        _chart_block(pie_img, "Spend by Channel") +
        monthly_html
    )


def build_va_ops_page(va_raw):
    df  = _prep_va(va_raw)
    today = pd.Timestamp(_TODAY)
    s = today.replace(day=1); e = today

    sub  = _rng(df, "create_date", s, e)
    paid = _rng(df, "payment_date", s, e)

    leads = sub["record_id"].nunique() if "record_id" in sub.columns else 0
    dc_n  = _rng(df, "dc_date", s, e)["record_id"].nunique() if "record_id" in df.columns else 0
    paid_n = paid["record_id"].nunique() if "record_id" in paid.columns else 0
    rev    = int(paid["amount_paid"].sum()) if "amount_paid" in paid.columns else 0

    kpis = _kpi_cards([
        ("Leads",   leads,  "blue"),
        ("DC",      dc_n,   "blue"),
        ("Paid",    paid_n, "blue"),
        ("Revenue", _fmt(rev), "green"),
    ])

    # VA GM Performance
    gm_html = ""
    if "deal_owner" in df.columns:
        rows = []
        for gm, g in df.groupby("deal_owner"):
            gp = _rng(g, "payment_date", s, e)
            rows.append({"GM": gm,
                         "Leads": _rng(g, "create_date", s, e)["record_id"].nunique() if "record_id" in g.columns else 0,
                         "DC": _rng(g, "dc_date", s, e)["record_id"].nunique() if "record_id" in g.columns else 0,
                         "Paid": gp["record_id"].nunique() if "record_id" in gp.columns else 0,
                         "Revenue": int(gp["amount_paid"].sum()) if "amount_paid" in gp.columns else 0})
        if rows:
            gm_df = pd.DataFrame(rows).sort_values("Revenue", ascending=False)
            tot = gm_df.select_dtypes("number").sum().to_dict(); tot["GM"] = "Total"
            gm_df = pd.concat([gm_df, pd.DataFrame([tot])], ignore_index=True)
            gm_html = _df_table(gm_df, "VA GM Performance")

    return (
        _page_header("VA Ops Dashboard", _NOW.strftime("%d %b %Y – %H:%M IST")) +
        kpis + gm_html
    )


def build_va_finance_page(va_raw, li_raw):
    df  = _prep_va(va_raw)
    li  = _prep_li(li_raw.copy() if hasattr(li_raw, "copy") else li_raw)
    today = pd.Timestamp(_TODAY)

    # VA Renewal Window ±14d
    rw_html = ""
    if "payment_date" in df.columns and "record_id" in df.columns:
        paid2 = df[df["payment_date"].notna()].copy()
        def _next(row):
            base = row.get("payment_date")
            if pd.isna(base): return pd.NaT
            m = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Monthly":1}.get(
                str(row.get("billing_cycle","")), None)
            return base + relativedelta(months=m) if m else pd.NaT
        if "billing_cycle" in paid2.columns:
            paid2["next_renewal"] = paid2.apply(_next, axis=1)
            rw = paid2[(paid2["next_renewal"] >= today - pd.Timedelta(days=14)) &
                       (paid2["next_renewal"] <= today + pd.Timedelta(days=14))].sort_values("next_renewal")
            if len(rw):
                svc_map = {}
                if "line_item_name" in li.columns and "record_id" in li.columns:
                    svc_map = (li.dropna(subset=["line_item_name"])
                                 .groupby("record_id")["line_item_name"]
                                 .apply(lambda x: ", ".join(sorted(set(x.astype(str))))).to_dict())
                rwd = pd.DataFrame({
                    "Due On":          rw["next_renewal"].dt.strftime("%d-%b-%y"),
                    "Deal Name":       rw.get("deal_name", ""),
                    "record_id":       rw["record_id"].values,
                    "VA Service Name": rw["record_id"].map(svc_map).fillna(""),
                    "Stage":           rw.get("deal_stage", ""),
                    "Amount":          rw.get("amount_paid", 0),
                })
                rw_html = _df_table(rwd, "VA Renewal Window (±14 days)",
                                    link_col="Deal Name", id_col="record_id")

    # Revenue trend
    trend_img = ""
    va_li = li[li["record_id"].isin(df["record_id"].values)] if "record_id" in li.columns and "record_id" in df.columns else li.iloc[0:0]
    if "date_paid" in va_li.columns and "unit_price" in va_li.columns and len(va_li):
        va_li2 = va_li.copy()
        va_li2["Month"] = va_li2["date_paid"].dt.to_period("M").astype(str)
        t = va_li2.groupby("Month")["unit_price"].sum().reset_index().sort_values("Month")
        if len(t):
            fig = go.Figure(go.Bar(x=t["Month"], y=t["unit_price"], marker_color="#1a7fc4"))
            fig.update_layout(margin=dict(l=40,r=20,t=20,b=60), paper_bgcolor="white",
                              font=dict(family="Inter,sans-serif", size=11),
                              yaxis_title="Revenue (₹)")
            trend_img = _fig_to_b64(fig, w=900, h=300)

    return (
        _page_header("VA Finance Dashboard", _NOW.strftime("%d %b %Y – %H:%M IST")) +
        _chart_block(trend_img, "VA Monthly Revenue Trend") +
        rw_html
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ASSEMBLE HTML DOCUMENT
# ═══════════════════════════════════════════════════════════════════════════════

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Inter, Arial, sans-serif; font-size: 12px; color: #334155;
       background: white; padding: 0; }
.page-header { display: flex; align-items: baseline; gap: 16px;
               border-bottom: 3px solid #1a7fc4; padding-bottom: 8px;
               margin-bottom: 16px; margin-top: 8px; }
.page-title  { font-size: 20px; font-weight: 700; color: #1a3a6b; }
.sync-stamp  { font-size: 11px; font-weight: 700; color: #334155; }
.kpi-row     { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.kpi         { flex: 1; min-width: 110px; border-radius: 8px; padding: 10px 14px;
               color: white; }
.kpi-label   { font-size: 10px; font-weight: 600; opacity: .85;
               text-transform: uppercase; letter-spacing: .4px; }
.kpi-val     { font-size: 18px; font-weight: 700; margin-top: 4px; }
.two-col     { display: flex; gap: 16px; margin-bottom: 12px; }
.two-col > * { flex: 1; min-width: 0; }
.section-title { font-size: 13px; font-weight: 700; color: #1a3a6b;
                 margin: 16px 0 6px; text-transform: uppercase; letter-spacing: .4px; }
.tbl-wrap    { overflow: visible; margin-bottom: 16px; }
table        { width: 100%; border-collapse: collapse; font-size: 11px; }
thead th     { background: #1a3a6b; color: white; font-weight: 600; font-size: 10px;
               text-transform: uppercase; letter-spacing: .4px; padding: 7px 10px;
               text-align: left; white-space: nowrap; }
tbody td     { padding: 6px 10px; border-bottom: 1px solid #f1f5f9; white-space: nowrap; }
tbody tr:nth-child(even) td { background: #f8fafc; }
.empty       { color: #94a3b8; font-size: 12px; margin-bottom: 12px; }
"""

def assemble_html(pages_html):
    body = ""
    for i, (title, html) in enumerate(pages_html):
        body += f'<div class="dash-page">{html}</div>'
        if i < len(pages_html) - 1:
            body += '<div style="page-break-after:always"></div>'

    stamp = _NOW.strftime("%d %b %Y %H:%M IST")
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>AiA + VA Dashboard Snapshot — {stamp}</title>
<style>{_CSS}</style>
</head>
<body>
<div style="text-align:right;font-size:10px;color:#94a3b8;padding:4px 0 8px">
  Generated {stamp} • AiA + VA Operations Dashboard
</div>
{body}
</body></html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# 7. GOOGLE DRIVE UPLOAD
# ═══════════════════════════════════════════════════════════════════════════════

def upload_to_drive(file_path: Path) -> str:
    """Upload file to Google Drive and return the web view URL."""
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "")
    if not _KEY_FILE.exists():
        print("[report] gdrive-key.json not found — skipping Drive upload")
        return ""
    if not folder_id:
        print("[report] GDRIVE_FOLDER_ID not set — skipping Drive upload")
        return ""

    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = service_account.Credentials.from_service_account_file(
        str(_KEY_FILE),
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    meta = {"name": file_path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(file_path), mimetype="application/pdf")
    f = service.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
    link = f.get("webViewLink", "")
    print(f"[report] Uploaded to Drive: {link}")
    return link


# ═══════════════════════════════════════════════════════════════════════════════
# 8. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    stamp   = _NOW.strftime("%Y%m%d_%H%M")
    outfile = _OUT_DIR / f"dashboard_{stamp}_IST.pdf"

    print(f"[report] ── AiA + VA Dashboard PDF Snapshot ──")
    print(f"[report] Date: {_NOW.strftime('%d %b %Y %H:%M IST')}")
    print(f"[report] Output: {outfile}")

    try:
        aia, va, li, inc, mkt, upl, syn = load_data()
    except Exception:
        print("[report] FATAL: Could not load data")
        traceback.print_exc()
        return 1

    # Prep once
    aia_df = _prep_aia(aia)
    va_df  = _prep_va(va)
    li_df  = _prep_li(li)

    print("[report] Building pages...")
    pages = [
        ("AIA Ops",      build_aia_page(aia, li, inc)),
        ("CS & Finance", build_cs_page(aia, li)),
        ("Marketing",    build_marketing_page(mkt, aia)),
        ("VA Ops",       build_va_ops_page(va)),
        ("VA Finance",   build_va_finance_page(va, li)),
    ]

    print("[report] Assembling HTML...")
    html = assemble_html(pages)

    print("[report] Converting to PDF (weasyprint)...")
    try:
        from weasyprint import HTML as WP
        WP(string=html, base_url="/").write_pdf(str(outfile))
    except Exception:
        print("[report] weasyprint failed — saving HTML fallback")
        traceback.print_exc()
        html_out = _OUT_DIR / f"dashboard_{stamp}_IST.html"
        html_out.write_text(html, encoding="utf-8")
        print(f"OUTPUT_PATH={html_out}")
        return 1

    size_kb = outfile.stat().st_size // 1024
    print(f"[report] PDF saved: {outfile} ({size_kb} KB)")
    print(f"OUTPUT_PATH={outfile}")

    drive_url = upload_to_drive(outfile)
    if drive_url:
        print(f"GDRIVE_URL={drive_url}")

    print("[report] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
