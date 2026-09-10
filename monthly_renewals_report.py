"""Monthly AIA renewals report -- last month's renewals (due + payments) plus
this month's upcoming dues.

Standalone script (imports main.py so the line-item / renewal math is the exact
same code the dashboard and the ad-hoc renewals builder already use -- one
source of truth). Meant to be run by an n8n Schedule Trigger + SSH "Execute a
command" node on the 1st of each month: `python3 monthly_renewals_report.py`.

Sent on the 1st of month M, the workbook has three sheets:
  - "{M-1 Mon YY} Renewals Due"      -- last month's due list (Renewed? Yes/No)
  - "{M-1 Mon YY} Renewal Payments"  -- last month's actual renewal payments
  - "{M Mon YY} Renewals Due"        -- this month's upcoming dues
and prints exactly one line to stdout:
  REPORT_JSON={"file_path": "...", "host_file_path": "...", "filename": "...",
               "subject": "...", "overall_html": "..."}
so the downstream n8n Code node can extract it with /REPORT_JSON=(.+)/, the same
sentinel convention monthly_cs_report.py uses. main.py's own load prints are
redirected to stderr so they never land on this stdout line.
"""
import sys, os, io, json, contextlib
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)                 # so main.py's load_dotenv()/relative imports resolve
sys.path.insert(0, _HERE)

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    import main as m
for _line in _buf.getvalue().splitlines():
    print(f"[main] {_line}", file=sys.stderr)

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HS = "https://app-na2.hubspot.com/contacts/39668252/record/0-3/"
CYC = {"monthly": "Monthly", "quarterly": "Quarterly",
       "per_six_months": "Half-yearly", "annually": "Annual"}
RUPEE = "₹"

NAVY = PatternFill("solid", fgColor="1A3A6B"); HF = Font(bold=True, color="FFFFFF", size=10)
NORED = PatternFill("solid", fgColor="FCE4D6")
_thin = Side(style="thin", color="D9D9D9"); BD = Border(_thin, _thin, _thin, _thin)
LINK = Font(color="1155CC", underline="single"); CTR = Alignment(horizontal="center")


def cyc(f):
    f = str(f or "").strip().lower()
    return CYC.get(f, f.title() if f else "-")


def ordn(n):
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def amt(r):
    tp = r.get("total_price")
    if pd.notna(tp) and float(tp) != 0:
        return float(tp)
    up = r.get("unit_price")
    if pd.isna(up):
        return 0.0
    tm = r.get("term")
    tm = 1 if (pd.isna(tm) or tm <= 0) else int(tm)
    f = str(r.get("billing_frequency", "")).strip().lower()
    return float(up) * tm if f == "monthly" else float(up)


def fdate(v):
    return v.strftime("%d-%b-%y") if pd.notna(v) else ""


def _li_frame():
    li = m._AIA_LI.copy()
    li = li[li["deleted"].isna()] if "deleted" in li.columns else li
    li["_bs"] = pd.to_datetime(li["billing_start_date"], errors="coerce")
    li["_do"] = pd.to_datetime(li["due_on"], errors="coerce")
    li["_dp"] = pd.to_datetime(li["date_paid"], errors="coerce")
    li["_rt"] = li["recurring_type"].astype(str).str.strip().str.lower()
    return li


def _deals():
    A = m._AIA.dropna(subset=["record_id"]).drop_duplicates("record_id").set_index("record_id")
    return A


def build_due(li, A, m0, m1):
    """Renewals whose due_on falls in [m0, m1]. Renewed? = the positional-next
    line item is a renewal (early/late payment tolerant)."""
    def a(rid, col):
        return A[col].get(rid, "") if rid in A.index else ""
    rows = []
    for rid, g in li.groupby("record_id"):
        g = g.sort_values("_bs")
        ren = g[g["_rt"] == "renewal"]
        ren_rank = {bs: i + 1 for i, bs in enumerate(ren["_bs"].tolist())}
        seq = [r for _, r in g.iterrows()]
        for i, r in enumerate(seq):
            do = r["_do"]
            if pd.isna(do) or not (m0 <= do <= m1):
                continue
            nth_due = int((ren["_bs"] <= r["_bs"]).sum()) + 1
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            renewed = nxt is not None and nxt["_rt"] == "renewal"
            rows.append({
                "Deal Name": r.get("deal_name", ""), "_rid": rid,
                "Period Start": r["_bs"], "Days Extended": int(r.get("days_extended") or 0),
                "Due On": do, "Renewal Due": f"{ordn(nth_due)} renewal due",
                "Expiring Cycle": cyc(r.get("billing_frequency")), "Expiring Amount": amt(r),
                "Renewed?": "Yes" if renewed else "No",
                "Renewal Paid On": (nxt["_dp"] if renewed else pd.NaT),
                "Renewal Paid": (f"{ordn(ren_rank.get(nxt['_bs'], 0))} renewal paid" if renewed else ""),
                "New Cycle": (cyc(nxt.get("billing_frequency")) if renewed else ""),
                "Renewal Amount": (amt(nxt) if renewed else None),
                "Term Change": ("Yes" if (renewed and cyc(nxt.get("billing_frequency")) != cyc(r.get("billing_frequency"))) else ""),
                "CSM": a(rid, "cs_owner"), "GM": a(rid, "deal_owner"), "Deal Stage": a(rid, "deal_stage"),
            })
    df = pd.DataFrame(rows)
    return df.sort_values("Due On") if len(df) else df


def build_pay(li, A, m0, m1):
    """Renewal payments whose date_paid falls in [m0, m1]."""
    def a(rid, col):
        return A[col].get(rid, "") if rid in A.index else ""
    rows = []
    for rid, g in li.groupby("record_id"):
        ren = g[g["_rt"] == "renewal"].sort_values("_bs")
        rank = {bs: i + 1 for i, bs in enumerate(ren["_bs"].tolist())}
        for _, r in ren.iterrows():
            dp = r["_dp"]
            if pd.isna(dp) or not (m0 <= dp <= m1):
                continue
            rows.append({
                "Payment Date": dp, "Renewal": f"{ordn(rank.get(r['_bs'], 0))} renewal paid",
                "Billing Cycle": cyc(r.get("billing_frequency")),
                "Deal Name": r.get("deal_name", ""), "_rid": rid, "Line Item": r.get("line_item_name", ""),
                "Term": (int(r["term"]) if pd.notna(r.get("term")) else ""), "Amount": amt(r),
                "CSM": a(rid, "cs_owner"), "Deal Stage": a(rid, "deal_stage"), "GM": a(rid, "deal_owner"),
            })
    df = pd.DataFrame(rows)
    return df.sort_values("Payment Date") if len(df) else df


def _hdr(ws, cols):
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        x = ws.cell(1, c); x.fill = NAVY; x.font = HF; x.border = BD
        x.alignment = Alignment(horizontal="center", vertical="center")


S1 = ["Deal Name", "Period Start", "Days Extended", "Due On", "Renewal Due", "Expiring Cycle",
      f"Expiring Amount ({RUPEE})", "Renewed?", "Renewal Paid On", "Renewal Paid", "New Cycle",
      f"Renewal Amount ({RUPEE})", "Term Change", "CSM", "Deal Owner (GM)", "Deal Stage"]
S1W = [40, 13, 9, 11, 15, 13, 16, 10, 14, 15, 11, 16, 12, 18, 18, 16]
S2 = ["Payment Date", "Renewal", "Billing Cycle", "Deal Name", "Line Item", "Term",
      f"Amount ({RUPEE})", "CSM", "Deal Stage", "Deal Owner (GM)"]
S2W = [13, 16, 13, 40, 46, 7, 13, 18, 16, 18]


def write_due_sheet(wb, title, due):
    ws = wb.create_sheet(title[:31])
    _hdr(ws, S1)
    for _, r in due.iterrows():
        ws.append([r["Deal Name"], fdate(r["Period Start"]), r["Days Extended"], fdate(r["Due On"]),
                   r["Renewal Due"], r["Expiring Cycle"], r["Expiring Amount"], r["Renewed?"],
                   fdate(r["Renewal Paid On"]), r["Renewal Paid"], r["New Cycle"],
                   (r["Renewal Amount"] if pd.notna(r["Renewal Amount"]) else ""), r["Term Change"],
                   r["CSM"], r["GM"], r["Deal Stage"]])
        row = ws.max_row
        dc = ws.cell(row, 1); dc.hyperlink = HS + str(r["_rid"]); dc.font = LINK
        for c in range(1, len(S1) + 1):
            cl = ws.cell(row, c); cl.border = BD
            if c in (2, 3, 4, 5, 6, 8, 9, 10, 11, 13):
                cl.alignment = CTR
        ws.cell(row, 7).number_format = '#,##0'
        ws.cell(row, 12).number_format = '#,##0'
        if r["Renewed?"] == "No":
            for c in range(1, len(S1) + 1):
                ws.cell(row, c).fill = NORED
    n_due = len(due); n_ren = int((due["Renewed?"] == "Yes").sum()) if n_due else 0
    tr = ws.max_row + 1
    pct = round(n_ren / n_due * 100) if n_due else 0
    ws.cell(tr, 1, f"Total - {n_due} due, {n_ren} renewed ({pct}%)").font = Font(bold=True)
    for c in range(1, len(S1) + 1):
        ws.cell(tr, c).border = BD
    for i, w in enumerate(S1W, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(S1))}{max(ws.max_row - 1, 1)}"
    return n_due, n_ren


def write_pay_sheet(wb, title, pay):
    ws = wb.create_sheet(title[:31])
    _hdr(ws, S2)
    for _, r in pay.iterrows():
        ws.append([fdate(r["Payment Date"]), r["Renewal"], r["Billing Cycle"], r["Deal Name"],
                   r["Line Item"], r["Term"], r["Amount"], r["CSM"], r["Deal Stage"], r["GM"]])
        row = ws.max_row
        dc = ws.cell(row, 4); dc.hyperlink = HS + str(r["_rid"]); dc.font = LINK
        for c in range(1, len(S2) + 1):
            cl = ws.cell(row, c); cl.border = BD
            if c in (1, 2, 3, 6, 7):
                cl.alignment = CTR
        ws.cell(row, 7).number_format = '#,##0'
    total = float(pay["Amount"].sum()) if len(pay) else 0.0
    tr = ws.max_row + 1
    ws.cell(tr, 1, "TOTAL").font = Font(bold=True)
    tc = ws.cell(tr, 7, total); tc.font = Font(bold=True); tc.number_format = '#,##0'
    for c in range(1, len(S2) + 1):
        ws.cell(tr, c).border = BD
    for i, w in enumerate(S2W, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(S2))}{max(ws.max_row - 1, 1)}"
    return len(pay), total


def _summary_html(prev_label, this_label, p_due, p_ren, p_pay_n, p_pay_tot, p_due_val, t_due, t_due_val):
    pct = round(p_ren / p_due * 100) if p_due else 0
    def money(v):
        return f"{RUPEE}{v:,.0f}"
    rows = [
        ("Renewals due", f"{p_due}"),
        ("Renewed", f"{p_ren} ({pct}%)"),
        ("Not renewed", f"{p_due - p_ren}"),
        ("Expiring value", money(p_due_val)),
        ("Renewal payments received", f"{p_pay_n}"),
        ("Payment collected", money(p_pay_tot)),
    ]
    body = ""
    for k, v in rows:
        body += (f'<tr><td style="padding:6px 10px;border:1px solid #e0e0e0">{k}</td>'
                 f'<td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:right;'
                 f'font-weight:600">{v}</td></tr>')
    prev_tbl = (f'<p style="font-weight:700;margin:18px 0 4px;font-size:14px;'
                f'font-family:Segoe UI,Arial,sans-serif">{prev_label} &mdash; closed</p>'
                f'<table style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;'
                f'font-size:13px;width:100%;max-width:420px">'
                f'<tr><th style="padding:6px 10px;background:#1A3A6B;color:#fff;text-align:left">Metric</th>'
                f'<th style="padding:6px 10px;background:#1A3A6B;color:#fff;text-align:right">Value</th></tr>'
                f'{body}</table>')
    this_tbl = (f'<p style="font-weight:700;margin:18px 0 4px;font-size:14px;'
                f'font-family:Segoe UI,Arial,sans-serif">{this_label} &mdash; upcoming</p>'
                f'<table style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;'
                f'font-size:13px;width:100%;max-width:420px">'
                f'<tr><th style="padding:6px 10px;background:#1A3A6B;color:#fff;text-align:left">Metric</th>'
                f'<th style="padding:6px 10px;background:#1A3A6B;color:#fff;text-align:right">Value</th></tr>'
                f'<tr><td style="padding:6px 10px;border:1px solid #e0e0e0">Renewals due this month</td>'
                f'<td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:right;font-weight:600">{t_due}</td></tr>'
                f'<tr><td style="padding:6px 10px;border:1px solid #e0e0e0">Value at stake</td>'
                f'<td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:right;font-weight:600">{RUPEE}{t_due_val:,.0f}</td></tr>'
                f'</table>')
    return prev_tbl + this_tbl


def main_run():
    today = pd.Timestamp(date.today()).normalize()
    this_month = today.replace(day=1)
    prev_month = this_month - pd.DateOffset(months=1)
    this_end = this_month + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59)
    prev_end = prev_month + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59)
    prev_label = prev_month.strftime("%b %Y")
    this_label = this_month.strftime("%b %Y")

    li = _li_frame()
    A = _deals()

    prev_due = build_due(li, A, prev_month, prev_end)
    prev_pay = build_pay(li, A, prev_month, prev_end)
    this_due = build_due(li, A, this_month, this_end)

    wb = Workbook(); wb.remove(wb.active)
    p_due, p_ren = write_due_sheet(wb, f"{prev_month.strftime('%b %y')} Renewals Due", prev_due)
    p_pay_n, p_pay_tot = write_pay_sheet(wb, f"{prev_month.strftime('%b %y')} Renewal Payments", prev_pay)
    t_due, _t_ren = write_due_sheet(wb, f"{this_month.strftime('%b %y')} Renewals Due", this_due)

    p_due_val = float(prev_due["Expiring Amount"].sum()) if len(prev_due) else 0.0
    t_due_val = float(this_due["Expiring Amount"].sum()) if len(this_due) else 0.0

    out_dir = os.path.join(_HERE, "exports")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"AIA_Renewals_{prev_month.strftime('%b%Y')}_and_{this_month.strftime('%b%Y')}.xlsx"
    file_path = os.path.join(out_dir, filename)
    wb.save(file_path)

    host_dir = os.environ.get("HOST_EXPORT_DIR", "/opt/taipy-dashboard/exports")
    result = {
        "file_path": file_path,
        "host_file_path": f"{host_dir.rstrip('/')}/{filename}",
        "filename": filename,
        "subject": f"AIA Renewals — {prev_label} closed, {this_label} upcoming",
        "overall_html": _summary_html(prev_label, this_label, p_due, p_ren, p_pay_n,
                                      p_pay_tot, p_due_val, t_due, t_due_val),
    }
    print("REPORT_JSON=" + json.dumps(result))


if __name__ == "__main__":
    main_run()
