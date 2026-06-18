"""
AIA + VA Operations Dashboard — 5 Pages
Run: python main.py
"""
import os
import pandas as pd
import numpy as np
from datetime import date
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import psycopg2
import plotly.graph_objects as go
from taipy.gui import Gui

load_dotenv()

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
        print(f"✓ AIA:{len(aia)} VA:{len(va)} LI:{len(li)} INC:{len(inc)} MKT:{len(mkt)} UPL:{len(upl)} SYN:{len(syn)}")
        return aia, va, li, inc, mkt, upl, syn
    except Exception as ex:
        print(f"⚠ DB error: {ex} — using empty frames")
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
                    "va_discard_reason","va_parked_reason","va_lost_reason","services_bought","poc_email"]
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

print("Loading data…")
_RAW_AIA, _RAW_VA, _RAW_LI, _RAW_INC, _RAW_MKT, _RAW_UPL, _RAW_SYN = _load_all()

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
    email = str(row["login_email_id"]).lower().strip()
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
    if state.aia_selected_owner != "All":    df = df[df["deal_owner"]==state.aia_selected_owner]
    if state.aia_selected_campaign != "All": df = df[df["utm_campaign"]==state.aia_selected_campaign]

    state.aia_kpi_leads       = _rng(df,"create_date",s,e)["record_id"].nunique()
    state.aia_kpi_ds          = _rng(df,"ds_date",s,e)["record_id"].nunique()
    state.aia_kpi_dc          = _rng(df,"dc_date",s,e)["record_id"].nunique()
    hi = _rng(df,"eta_pay_date",s,e)
    state.aia_kpi_hi          = hi[hi["deal_stage"]=="High Intent"]["record_id"].nunique()
    pd_                       = _rng(df,"payment_date",s,e)
    state.aia_kpi_aia_paid    = pd_[pd_["module_type"]=="AIA Paid"]["record_id"].nunique()
    state.aia_kpi_gst_paid    = pd_[pd_["module_type"]=="GST Paid"]["record_id"].nunique()
    if "asked_refund" in pd_.columns:
        state.aia_kpi_paid    = pd_[pd_["asked_refund"].isna()]["record_id"].nunique()
        state.aia_kpi_refunds = _rng(df,"churned_date",s,e)[df["asked_refund"].notna()]["record_id"].nunique()
    else:
        state.aia_kpi_paid    = pd_["record_id"].nunique()
        state.aia_kpi_refunds = 0
    state.aia_kpi_parked      = _rng(df,"parked_date",s,e)["record_id"].nunique()
    state.aia_kpi_discards    = _rng(df,"discard_date",s,e)["record_id"].nunique()
    state.aia_kpi_closed_lost = _rng(df,"closed_lost_date",s,e)["record_id"].nunique()
    state.aia_kpi_collected   = _fmt(int(pd_.groupby("record_id")["amount_paid"].max().sum()))
    cycle_map = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}
    if "asked_refund" in pd_.columns:
        mrr_df = pd_[pd_["asked_refund"].isna()].copy()
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
    _labels = [str(leads), f"{ds_n} ({p(ds_n)})", f"{dc_n} ({p(dc_n)})",
               f"{hi2} ({p(hi2)})", f"{paid2} ({p(paid2)})"]
    _fig = go.Figure(go.Funnel(
        y=["Leads", "DS", "DC", "High Intent", "Paid"],
        x=[leads, ds_n, dc_n, hi2, paid2],
        text=_labels,
        textinfo="text",
        textposition="auto",
        marker={"color": ["#90CAF9","#42A5F5","#1E88E5","#1976D2","#1565C0"]},
    ))
    _fig.update_layout(**aia_funnel_layout)
    state.aia_funnel_fig = _fig

    # DC trend
    dc_sub = _rng(df,"dc_date",s,e).copy()
    dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    daily_q  = dc_sub[dc_sub["prospect_score"]>=60].groupby("date")["record_id"].nunique().reset_index(name="Qualified")
    trend = pd.DataFrame({"date": pd.date_range(s, e, freq="D")})
    trend = trend.merge(daily_dc,on="date",how="left").merge(daily_q,on="date",how="left").fillna(0)
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    state.aia_trend_df = trend.astype({"DC":int,"Qualified":int})

    # Channel pie
    ch = _rng(df,"create_date",s,e).groupby("deal_source_group")["record_id"].nunique().reset_index()
    ch.columns = ["Channel","Count"]
    state.aia_channel_df = ch

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
        paid_no_refund = pd2[pd2["asked_refund"].isna()] if "asked_refund" in pd2.columns else pd2
        rows.append({
            "GM":         owner,
            "Leads":      l,
            "DC":         _rng(o,"dc_date",s,e)["record_id"].nunique(),
            "HI":         _rng(o,"eta_pay_date",s,e).query("deal_stage=='High Intent'")["record_id"].nunique(),
            "AIA Paid":   pd2[pd2["module_type"]=="AIA Paid"]["record_id"].nunique(),
            "GST Paid":   pd2[pd2["module_type"]=="GST Paid"]["record_id"].nunique(),
            "Active PS60":_rng(o,"dc_date",s,e).query("prospect_score>=60 and deal_stage in ['Demo Conducted','High Intent']")["record_id"].nunique(),
            "Paid":       paid_no_refund["record_id"].nunique(),
            "Revenue":    int(pd2.groupby("record_id")["amount_paid"].max().sum()),
            "MRR":        int(new_li["mrr"].sum()) if len(new_li) else 0,
        })
    gm = pd.DataFrame(rows)
    if len(gm):
        tot = gm.select_dtypes("number").sum().to_dict(); tot["GM"] = "Total"
        gm = pd.concat([gm, pd.DataFrame([tot])], ignore_index=True)
    state.aia_gm_df = gm

    # UTM cohort
    rows2 = []
    for src in sorted(coh["utm_source_cohort"].dropna().unique()):
        c  = coh[coh["utm_source_cohort"]==src]
        l2 = c["record_id"].nunique()
        if l2 == 0: continue
        pd3 = c[c["payment_date"].notna()&(c["payment_date"]>=s)&(c["payment_date"]<=e)]
        rows2.append({
            "UTM Source": src,
            "Leads": l2,
            "DC":    c[c["dc_date"].notna()&(c["dc_date"]>=s)&(c["dc_date"]<=e)]["record_id"].nunique(),
            "HI":    c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
            "AIA Paid": pd3[pd3["module_type"]=="AIA Paid"]["record_id"].nunique(),
            "GST Paid": pd3[pd3["module_type"]=="GST Paid"]["record_id"].nunique(),
            "Paid":     pd3["record_id"].nunique(),
            "Revenue":  int(pd3.groupby("record_id")["amount_paid"].max().sum()),
        })
    utm = pd.DataFrame(rows2)
    if len(utm):
        tot2 = utm.select_dtypes("number").sum().to_dict(); tot2["UTM Source"] = "Total"
        utm = pd.concat([utm, pd.DataFrame([tot2])], ignore_index=True)
    state.aia_utm_df = utm

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
        state.aia_incentive_df = pd.DataFrame(columns=_INC_COLS)
    else:
        m_start  = pd.Timestamp(state.aia_start_date).replace(day=1)
        m_end    = (m_start + relativedelta(months=1)) - pd.Timedelta(days=1)
        pm_start = m_start - relativedelta(months=1)
        pm_end   = m_start - pd.Timedelta(days=1)
        curr_t   = _INCENTIVE_TARGETS[_INCENTIVE_TARGETS["month"] == m_start]
        prev_t   = _INCENTIVE_TARGETS[_INCENTIVE_TARGETS["month"] == pm_start]
        if len(curr_t) == 0:
            state.aia_incentive_df = pd.DataFrame(columns=_INC_COLS)
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
                state.aia_incentive_df = pd.concat([inc_df, pd.DataFrame([tot_row])], ignore_index=True)
            else:
                state.aia_incentive_df = pd.DataFrame(columns=_INC_COLS)

# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — CS & FINANCE
# ═══════════════════════════════════════════════════════════════════

def _cs_refresh(state):
    s = pd.Timestamp(state.cs_start_date)
    e = pd.Timestamp(state.cs_end_date)
    df = _AIA.copy()
    if state.cs_selected_owner != "All": df = df[df["cs_owner"]==state.cs_selected_owner]
    today = pd.Timestamp(date.today())
    paid_all = df[df["payment_date"].notna()]

    state.cs_kpi_paid_all = paid_all["record_id"].nunique()
    state.cs_kpi_aia_paid = paid_all[paid_all["module_type"]=="AIA Paid"]["record_id"].nunique()
    state.cs_kpi_refunds  = df[df["asked_refund"].notna()]["record_id"].nunique() if "asked_refund" in df.columns else 0

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

    int_done_mask = (df["integration_done_date"].notna()
                     & df["login_email_id"].notna()
                     & (df["module_type"]=="AIA Paid"))
    int_done = df[int_done_mask]
    not_activated = (int_done["activation_date"].isna()
                     & int_done["adopted_date"].isna()
                     & ~int_done["deal_stage"].isin(["Churned","CS Parked","Blocked",
                                                     "Integration Failed","Integration Done"]))
    state.cs_kpi_int_due = int_done[not_activated]["record_id"].nunique()

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

    # Revenue matrix
    li_sub = _AIA_LI.copy()
    if len(li_sub) > 0 and "cohort_month" in li_sub.columns and "date_paid" in li_sub.columns:
        li_sub["billing_month"] = li_sub["date_paid"].dt.to_period("M").astype(str)
        li_sub["cohort_label"]  = li_sub["cohort_month"].dt.strftime("%b %y")
        rev_piv = li_sub.pivot_table(index="cohort_label", columns="billing_month",
                                      values="unit_price", aggfunc="sum", fill_value=0)
        state.cs_revenue_matrix = rev_piv.reset_index().rename(columns={"cohort_label":"Cohort"})
        ret_piv = li_sub.pivot_table(index="cohort_label", columns="billing_month",
                                      values="record_id", aggfunc=pd.Series.nunique, fill_value=0)
        state.cs_retention_matrix = ret_piv.reset_index().rename(columns={"cohort_label":"Cohort"})
    else:
        state.cs_revenue_matrix   = pd.DataFrame()
        state.cs_retention_matrix = pd.DataFrame()

    # CSM table
    csm_rows = []
    for csm in sorted(df["cs_owner"].dropna().unique()):
        c  = df[df["cs_owner"]==csm]
        cp = c[c["payment_date"].notna() & (c["module_type"]=="AIA Paid")]
        csm_rows.append({
            "CSM":       csm,
            "AIA Paid":  cp["record_id"].nunique(),
            "Under CS":  cp[cp["deal_stage"].isin(["Payment Done","Integration Due"])]["record_id"].nunique(),
            "Int Due":   cp[cp["activation_date"].isna()
                            &~cp["deal_stage"].isin(["Churned","CS Parked","Blocked",
                                                     "Integration Failed","Integration Done"])]["record_id"].nunique(),
            "Int Failed":cp[cp["deal_stage"]=="Integration Failed"]["record_id"].nunique(),
            "Integrated":cp[cp["deal_stage"]=="Integration Done"]["record_id"].nunique(),
            "RFR":       cp[cp["deal_stage"]=="Ready for Renewal"]["record_id"].nunique(),
            "Renewed":   _rng(c,"renewed_date",s,e)["record_id"].nunique(),
            "Parked":    cp[cp["deal_stage"]=="CS Parked"]["record_id"].nunique(),
            "Blocked":   cp[cp["deal_stage"]=="Blocked"]["record_id"].nunique(),
            "Churned":   c[c["deal_stage"]=="Churned"]["record_id"].nunique(),
        })
    state.cs_csm_aia_df = pd.DataFrame(csm_rows)

    # Usage table (top 50)
    usage_rows = []
    for _, row in int_done.head(50).iterrows():
        email  = str(row.get("login_email_id","")).lower().strip()
        acct_u = _UPL[_UPL["email"]==email]["account_id"].dropna()
        acct_s = _SYN[_SYN["email"]==email]["account_id"].dropna()
        acct   = acct_u.iloc[0] if len(acct_u) else (acct_s.iloc[0] if len(acct_s) else None)
        usage_rows.append({
            "Deal Name":       row.get("deal_name",""),
            "CSM":             row.get("cs_owner",""),
            "Stage":           row.get("deal_stage",""),
            "Paid On":         str(row.get("payment_date",""))[:10],
            "Int Date":        str(row.get("integration_done_date",""))[:10],
            "Cadence":         row.get("cadence",""),
            "Usage (28d)":     _streak(email, acct, _UPL, _SYN, 28),
            "Status":          _customer_status(row, _UPL, _SYN) or "",
        })
    state.cs_usage_df = pd.DataFrame(usage_rows)

    # Renewal window ±14d
    rw = paid_active.copy()
    rw["next_renewal_date"] = rw.apply(_next_renewal, axis=1)
    w14 = rw[(rw["next_renewal_date"]>=today-pd.Timedelta(days=14))
             &(rw["next_renewal_date"]<=today+pd.Timedelta(days=14))]
    cols_needed = [c for c in ["deal_name","cs_owner","poc_number","poc_email",
                                "deal_stage","next_renewal_date","amount_paid"] if c in w14.columns]
    state.cs_renewal_window_df = (w14[cols_needed]
        .rename(columns={"deal_name":"Deal Name","cs_owner":"CSM","poc_number":"POC",
                         "poc_email":"Email","deal_stage":"Stage",
                         "next_renewal_date":"Due On","amount_paid":"Amount"})
        .sort_values("Due On").reset_index(drop=True))

# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — MARKETING
# ═══════════════════════════════════════════════════════════════════

def _mkt_refresh(state):
    s = pd.Timestamp(state.mkt_start_date)
    e = pd.Timestamp(state.mkt_end_date)
    mkt = _MKT[(_MKT["day"]>=s)&(_MKT["day"]<=e)] if "day" in _MKT.columns else _MKT

    total_spend  = int(mkt["cost"].sum()) if "cost" in mkt.columns else 0
    state.mkt_kpi_spend = _fmt(total_spend)

    aia_sub      = _rng(_AIA,"create_date",s,e)
    total_leads  = aia_sub["record_id"].nunique()
    state.mkt_kpi_leads = _fmtn(total_leads)

    paid_sub = _rng(_AIA,"payment_date",s,e)
    if "asked_refund" in paid_sub.columns:
        paid_ch = paid_sub[paid_sub["asked_refund"].isna()]["record_id"].nunique()
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

    # Monthly table
    if "day" in _MKT.columns:
        tmp = _MKT.copy()
        tmp["YearMonth"] = tmp["day"].dt.to_period("M").astype(str)
        ms = tmp.groupby("YearMonth")["cost"].sum().reset_index(); ms.columns=["YM","Spend"]

        aa = _AIA.copy(); aa["YM"] = aa["create_date"].dt.to_period("M").astype(str)
        ml = aa.groupby("YM")["record_id"].nunique().reset_index(); ml.columns=["YM","Leads"]

        dc_t = aa[aa["dc_date"].notna()].copy(); dc_t["YM_dc"]=dc_t["dc_date"].dt.to_period("M").astype(str)
        mdc  = dc_t.groupby("YM_dc")["record_id"].nunique().reset_index(); mdc.columns=["YM","DC"]

        pp_t = aa[aa["payment_date"].notna()].copy(); pp_t["YM_p"]=pp_t["payment_date"].dt.to_period("M").astype(str)
        mpp  = pp_t.groupby("YM_p")["record_id"].nunique().reset_index(); mpp.columns=["YM","Paid"]

        monthly = (ms.merge(ml,on="YM",how="outer")
                     .merge(mdc,on="YM",how="outer")
                     .merge(mpp,on="YM",how="outer").fillna(0))
        monthly["CPL"] = (monthly["Spend"]/monthly["Leads"].replace(0,np.nan)).fillna(0).astype(int)
        monthly["CAC"] = (monthly["Spend"]/monthly["Paid"].replace(0,np.nan)).fillna(0).astype(int)
        monthly = monthly.sort_values("YM").reset_index(drop=True)
        monthly.rename(columns={"YM":"Month","Spend":"Spend (₹)"},inplace=True)
        state.mkt_monthly_df  = monthly
        state.mkt_spend_df    = monthly
        state.mkt_cpl_df      = monthly
    else:
        state.mkt_monthly_df  = pd.DataFrame()
        state.mkt_spend_df    = pd.DataFrame()
        state.mkt_cpl_df      = pd.DataFrame()

    # Weekly
    cur_start = date.today().replace(day=1)
    mwk = _MKT[_MKT["day"]>=pd.Timestamp(cur_start)] if "day" in _MKT.columns else pd.DataFrame()
    if len(mwk):
        mwk = mwk.copy(); mwk["Week"] = mwk["day"].dt.to_period("W").astype(str)
        state.mkt_weekly_df = mwk.groupby("Week").agg(Spend=("cost","sum"),Impressions=("impressions","sum")).reset_index()
    else:
        state.mkt_weekly_df = pd.DataFrame()

    # Channel pies
    if "channel" in _MKT.columns and len(mkt):
        cs = mkt.groupby("channel")["cost"].sum().reset_index(); cs.columns=["Channel","Spend"]
        state.mkt_channel_spend_df = cs
    else:
        state.mkt_channel_spend_df = pd.DataFrame()

    cl = _rng(_AIA,"create_date",s,e).groupby("deal_source_group")["record_id"].nunique().reset_index()
    cl.columns = ["Channel","Leads"]
    state.mkt_channel_leads_df = cl

# ═══════════════════════════════════════════════════════════════════
# PAGE 4 — VA OPS
# ═══════════════════════════════════════════════════════════════════

def _va_ops_refresh(state):
    s = pd.Timestamp(state.va_start_date)
    e = pd.Timestamp(state.va_end_date)
    df = _VA.copy()
    if state.va_selected_owner != "All":    df = df[df["deal_owner"]==state.va_selected_owner]
    if state.va_selected_campaign != "All": df = df[df["utm_campaign"]==state.va_selected_campaign]

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
    state.va_funnel_df = pd.DataFrame({
        "Stage":["Leads","DS","DC","Agreed","Paid"],
        "Count":[leads,ds2,dc2,hi2,paid2],
        "Label":[str(leads),f"{ds2} ({p(ds2)})",f"{dc2} ({p(dc2)})",f"{hi2} ({p(hi2)})",f"{paid2} ({p(paid2)})"]
    })

    dc_sub = _rng(df,"dc_date",s,e).copy(); dc_sub["date"] = dc_sub["dc_date"].dt.normalize()
    daily_dc = dc_sub.groupby("date")["record_id"].nunique().reset_index(name="DC")
    trend = pd.DataFrame({"date":pd.date_range(s,e,freq="D")}).merge(daily_dc,on="date",how="left").fillna(0)
    trend["date_label"] = trend["date"].dt.strftime("%b %d")
    state.va_trend_df = trend.astype({"DC":int})

    ch = _rng(df,"create_date",s,e).groupby("deal_source_group")["record_id"].nunique().reset_index()
    ch.columns = ["Channel","Count"]
    state.va_channel_df = ch

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
            "Revenue":int(pd2["amount_paid"].sum()+pd2["ot_amount_paid"].sum())})
    va_gm = pd.DataFrame(rows)
    if len(va_gm):
        tot = va_gm.select_dtypes("number").sum().to_dict(); tot["GM"]="Total"
        va_gm = pd.concat([va_gm,pd.DataFrame([tot])],ignore_index=True)
    state.va_gm_df = va_gm

    rows2 = []
    for src in sorted(coh["utm_source_cohort"].dropna().unique()):
        c = coh[coh["utm_source_cohort"]==src]; l2 = c["record_id"].nunique()
        if l2==0: continue
        pd3 = c[c["payment_date"].notna()&(c["payment_date"]>=s)&(c["payment_date"]<=e)]
        rows2.append({"UTM":src,"Leads":l2,
            "DC":c[c["dc_date"].notna()&(c["dc_date"]>=s)&(c["dc_date"]<=e)]["record_id"].nunique(),
            "HI":c[c["eta_pay_date"].notna()&(c["eta_pay_date"]>=s)&(c["eta_pay_date"]<=e)&(c["deal_stage"]=="High Intent")]["record_id"].nunique(),
            "Paid":pd3["record_id"].nunique(),"Revenue":int(pd3["amount_paid"].sum())})
    va_utm = pd.DataFrame(rows2)
    if len(va_utm):
        tot2 = va_utm.select_dtypes("number").sum().to_dict(); tot2["UTM"]="Total"
        va_utm = pd.concat([va_utm,pd.DataFrame([tot2])],ignore_index=True)
    state.va_utm_df = va_utm

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
    s = pd.Timestamp(state.vaf_start_date)
    e = pd.Timestamp(state.vaf_end_date)
    today = pd.Timestamp(date.today())
    df = _VA.copy()
    paid = df[df["payment_date"].notna()]

    state.vaf_kpi_active  = paid[~paid["deal_stage"].isin(["Churned"])]["record_id"].nunique()
    state.vaf_kpi_revenue = _fmt(int(paid["amount_paid"].sum()+paid["ot_amount_paid"].sum()))
    cycle_map = {"Annual":12,"Half-yearly":6,"Quarterly":3,"Bi-monthly":2,"Monthly":1}
    mrr_df = paid.copy(); mrr_df["_m"] = mrr_df["billing_cycle"].map(cycle_map)
    state.vaf_kpi_mrr = _fmt(int((mrr_df["amount_paid"]/mrr_df["_m"].fillna(1)).sum()))

    def _next_va(row):
        base = row.get("renewed_date") if pd.notna(row.get("renewed_date")) else row.get("payment_date")
        if pd.isna(base): return pd.NaT
        m = cycle_map.get(row.get("billing_cycle",""))
        return base + relativedelta(months=m) if m else pd.NaT
    paid2 = paid.copy(); paid2["next_renewal"] = paid2.apply(_next_va, axis=1)
    state.vaf_kpi_due_14d = paid2[
        (paid2["next_renewal"]>=today-pd.Timedelta(days=14))
        &(paid2["next_renewal"]<=today+pd.Timedelta(days=14))]["record_id"].nunique()

    if len(_VA_LI) > 0 and "cohort_month" in _VA_LI.columns:
        li2 = _VA_LI.copy()
        li2["billing_month"] = li2["date_paid"].dt.to_period("M").astype(str)
        li2["cohort_label"]  = li2["cohort_month"].dt.strftime("%b %y")
        rev_piv = li2.pivot_table(index="cohort_label",columns="billing_month",values="unit_price",aggfunc="sum",fill_value=0)
        state.vaf_revenue_matrix    = rev_piv.reset_index().rename(columns={"cohort_label":"Cohort"})
        ret_piv = li2.pivot_table(index="cohort_label",columns="billing_month",values="record_id",aggfunc=pd.Series.nunique,fill_value=0)
        state.vaf_retention_matrix  = ret_piv.reset_index().rename(columns={"cohort_label":"Cohort"})
    else:
        state.vaf_revenue_matrix    = pd.DataFrame()
        state.vaf_retention_matrix  = pd.DataFrame()

    if len(_VA_LI) > 0:
        li3 = _VA_LI.copy(); li3["BillingMonth"] = li3["date_paid"].dt.to_period("M").astype(str)
        t = li3.groupby("BillingMonth")["unit_price"].sum().reset_index(); t.columns=["BillingMonth","Revenue"]
        state.vaf_revenue_trend_df = t.sort_values("BillingMonth").reset_index(drop=True)
    else:
        state.vaf_revenue_trend_df = pd.DataFrame()

    rw = paid2[(paid2["next_renewal"]>=today-pd.Timedelta(days=14))&(paid2["next_renewal"]<=today+pd.Timedelta(days=14))]
    cols_v = [c for c in ["deal_name","poc_email","deal_stage","next_renewal","amount_paid"] if c in rw.columns]
    state.vaf_renewal_df = (rw[cols_v]
        .rename(columns={"deal_name":"Deal","poc_email":"Email","deal_stage":"Stage",
                         "next_renewal":"Due On","amount_paid":"Amount"})
        .sort_values("Due On").reset_index(drop=True))

# ═══════════════════════════════════════════════════════════════════
# STATE VARIABLES
# ═══════════════════════════════════════════════════════════════════

_today       = date.today()
_month_start = date(_today.year, _today.month, 1)

# Page 1
aia_start_date = _month_start;  aia_end_date = _today
aia_owner_list    = ["All"] + sorted(_AIA["deal_owner"].dropna().unique().tolist())
aia_campaign_list = ["All"] + sorted(_AIA["utm_campaign"].dropna().unique().tolist())
aia_selected_owner = "All";  aia_selected_campaign = "All"
aia_kpi_leads=0; aia_kpi_ds=0; aia_kpi_dc=0; aia_kpi_hi=0
aia_kpi_aia_paid=0; aia_kpi_gst_paid=0; aia_kpi_paid=0; aia_kpi_refunds=0
aia_kpi_parked=0; aia_kpi_discards=0; aia_kpi_closed_lost=0
aia_kpi_collected="₹0"; aia_kpi_mrr="₹0"
aia_funnel_fig = go.Figure()
aia_trend_df  = pd.DataFrame({"date_label":[],"DC":[],"Qualified":[]})
aia_channel_df= pd.DataFrame({"Channel":[],"Count":[]})
aia_gm_df=pd.DataFrame(); aia_utm_df=pd.DataFrame()
aia_discard_df=pd.DataFrame(); aia_lost_df=pd.DataFrame(); aia_parked_df=pd.DataFrame()
aia_incentive_df=pd.DataFrame()

# Page 2
cs_start_date = _month_start;  cs_end_date = _today
cs_owner_list = ["All"] + sorted(_AIA["cs_owner"].dropna().unique().tolist())
cs_deal_list  = ["All"] + sorted(_AIA["deal_name"].dropna().unique().tolist()[:200])
cs_selected_owner="All"; cs_selected_deal="All"
cs_kpi_paid_all=0; cs_kpi_overdue=0; cs_kpi_due_7d=0; cs_kpi_int_due=0
cs_kpi_renewed=0; cs_kpi_refunds=0; cs_kpi_blocked=0; cs_kpi_rfr=0
cs_kpi_aia_paid=0; cs_kpi_mrr="₹0"; cs_kpi_active=0
cs_revenue_matrix=pd.DataFrame(); cs_retention_matrix=pd.DataFrame()
cs_csm_aia_df=pd.DataFrame(); cs_usage_df=pd.DataFrame(); cs_renewal_window_df=pd.DataFrame()

# Page 3
mkt_start_date = date(2024,12,1); mkt_end_date = _today
mkt_deal_list = ["All"] + sorted(_AIA["deal_name"].dropna().unique().tolist()[:100])
mkt_line_item_list = (["All"] + sorted(_AIA_LI["line_item_name"].dropna().unique().tolist()[:100])
                      if "line_item_name" in _AIA_LI.columns else ["All"])
mkt_selected_deal="All"; mkt_selected_line_item="All"
mkt_kpi_spend="₹0"; mkt_kpi_leads="0"; mkt_kpi_cpl="₹0"; mkt_kpi_cac="₹0"
mkt_kpi_arpu="₹0"; mkt_kpi_payback="—"
mkt_monthly_df=pd.DataFrame(); mkt_spend_df=pd.DataFrame(); mkt_cpl_df=pd.DataFrame()
mkt_weekly_df=pd.DataFrame(); mkt_channel_spend_df=pd.DataFrame(); mkt_channel_leads_df=pd.DataFrame()

# Page 4
va_start_date = _month_start;  va_end_date = _today
va_owner_list    = ["All"] + sorted(_VA["deal_owner"].dropna().unique().tolist())
va_campaign_list = ["All"] + sorted(_VA["utm_campaign"].dropna().unique().tolist())
va_selected_owner="All"; va_selected_campaign="All"
va_kpi_leads=0; va_kpi_ds=0; va_kpi_dc=0; va_kpi_hi=0; va_kpi_paid=0
va_kpi_discards=0; va_kpi_parked=0; va_kpi_closed_lost=0
va_kpi_revenue="₹0"; va_kpi_mrr="₹0"; va_kpi_eom="0"
va_funnel_df=pd.DataFrame(); va_trend_df=pd.DataFrame(); va_channel_df=pd.DataFrame()
va_gm_df=pd.DataFrame(); va_utm_df=pd.DataFrame()
va_discard_df=pd.DataFrame(); va_lost_df=pd.DataFrame(); va_parked_df=pd.DataFrame()

# Page 5
vaf_start_date = date(2024,12,1); vaf_end_date = _today
vaf_deal_list = ["All"] + sorted(_VA["deal_name"].dropna().unique().tolist()[:200])
vaf_selected_deal="All"
vaf_kpi_active=0; vaf_kpi_revenue="₹0"; vaf_kpi_mrr="₹0"; vaf_kpi_due_14d=0
vaf_revenue_matrix=pd.DataFrame(); vaf_retention_matrix=pd.DataFrame()
vaf_revenue_trend_df=pd.DataFrame(); vaf_renewal_df=pd.DataFrame()

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
    "margin": {"l": 20, "r": 110, "t": 20, "b": 20},
    "height": 320,
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Inter,sans-serif", "size": 12},
    "showlegend": False,
    "yaxis": {"side": "right", "automargin": True, "title": ""},
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
                     "paper_bgcolor":_bg,"plot_bgcolor":_bg,"font":_font}
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

def on_aia_filter_change(state): _aia_ops_refresh(state)
def on_cs_filter_change(state):  _cs_refresh(state)
def on_mkt_filter_change(state): _mkt_refresh(state)
def on_va_filter_change(state):  _va_ops_refresh(state)
def on_vaf_filter_change(state): _vaf_refresh(state)

def total_row_style(state, index, row):
    vals = list(row.values()) if row else []
    return "total-row" if vals and str(vals[0]) == "Total" else ""

def _sort_pinned(df, col, asc, id_col):
    if len(df) == 0 or col not in df.columns: return df
    mask = df[id_col] == "Total"
    data = df[~mask].sort_values(col, ascending=asc, ignore_index=True)
    return pd.concat([data, df[mask]], ignore_index=True)

def on_sort_gm(state, action, payload):
    state.aia_gm_df = _sort_pinned(state.aia_gm_df, payload["col"], payload["order"]=="asc", "GM")

def on_sort_utm(state, action, payload):
    state.aia_utm_df = _sort_pinned(state.aia_utm_df, payload["col"], payload["order"]=="asc", "UTM Source")

def on_sort_incentive(state, action, payload):
    state.aia_incentive_df = _sort_pinned(state.aia_incentive_df, payload["col"], payload["order"]=="asc", "GM")

def on_sort_va_gm(state, action, payload):
    state.va_gm_df = _sort_pinned(state.va_gm_df, payload["col"], payload["order"]=="asc", "GM")

def on_sort_va_utm(state, action, payload):
    state.va_utm_df = _sort_pinned(state.va_utm_df, payload["col"], payload["order"]=="asc", "UTM")

def on_init(state):
    _aia_ops_refresh(state)
    _cs_refresh(state)
    _mkt_refresh(state)
    _va_ops_refresh(state)
    _vaf_refresh(state)

# ═══════════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════════

from pages.aia_ops    import AIA_OPS_PAGE
from pages.cs_finance import CS_FINANCE_PAGE
from pages.marketing  import MARKETING_PAGE
from pages.va_ops     import VA_OPS_PAGE
from pages.va_finance import VA_FINANCE_PAGE

pages = {
    "/":          AIA_OPS_PAGE,
    "cs":         CS_FINANCE_PAGE,
    "marketing":  MARKETING_PAGE,
    "va-ops":     VA_OPS_PAGE,
    "va-finance": VA_FINANCE_PAGE,
}

# ═══════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    gui = Gui(pages=pages, css_file="main.css")
    gui.run(
        title="AiA + VA Dashboard",
        dark_mode=False,
        port=8080,
        host="0.0.0.0",
        on_init=on_init,
        use_reloader=False,
    )