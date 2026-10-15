"""Daily Funnel sheet -- full rebuild every run (not incremental): every cohort
day (keyed by aia_live.create_date, from EARLIEST_COHORT forward) is
recomputed fresh, since Activated/Paid for an older cohort can still change
as those leads mature. A new row for today gets added on top each day; the
sheet only has columns A-L, nothing else is carried forward between runs.

FT Scheduled = aia_live.ft_scheduled_date is set (the real HubSpot "date
entered Free Trial Scheduled" field, synced via the HS Neon Integrations
workflow -- no longer a stage-based proxy).
"""
import sys, os, io, contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    import main as m
for _line in _buf.getvalue().splitlines():
    print(f"[main] {_line}", file=sys.stderr)

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.formatting.rule import ColorScaleRule

OUT_PATH = os.path.join(_HERE, "exports", "Daily Funnel.xlsx")
SHEET = "Funnel"
TODAY = pd.Timestamp(pd.Timestamp.now().date())
EARLIEST_COHORT = pd.Timestamp("2026-09-14")   # sheet starts here; keeps growing forward only
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DAILY_COLS = ["Cohort Date", "Day", "MQLs", "FT Scheduled", "FT Started", "Activated", "Paid",
              "MQL → FT Scheduled", "FT Scheduled → FT Started", "FT Started → Activated",
              "FT Started → Paid", "MQL → FT Started"]
N_DAILY_COLS = len(DAILY_COLS)   # 12 (A..L)
DATA_HEADER_ROW = 7
DATA_START_ROW = 8

HEAD_FILL = PatternFill("solid", fgColor="FF0F2F52")
HEAD_FONT = Font(bold=True, color="FFFFFFFF")

def resolve(login_email, poc_email):
    acct = m._acct_for(login_email) if login_email else None
    email_used = login_email
    if acct is None and poc_email:
        acct = m._acct_for(poc_email)
        email_used = poc_email
    return acct, email_used

def main():
    ev_lu = m._recent_event_lookup()
    aia = m._AIA.dropna(subset=["create_date"]).copy()
    aia["_cohort"] = aia["create_date"].dt.normalize()

    all_dates = pd.date_range(EARLIEST_COHORT, TODAY, freq="D")
    print(f"Rebuilding {len(all_dates)} cohort days ({EARLIEST_COHORT.date()} .. {TODAY.date()})")

    # ── per-cohort-day computation. 0 is written as None (a truly empty cell,
    # not the number 0 and not a dash placeholder) so the sheet reads clean. ──
    rows = []   # newest first
    for cd in sorted(all_dates, reverse=True):
        day_deals = aia[aia["_cohort"] == cd]
        mql = day_deals["record_id"].nunique()
        if mql == 0:
            rows.append((cd, None, None, None, None, None))
            continue
        ft_started = int(day_deals["ft_start_date"].notna().sum())
        paid_mask = day_deals["payment_date"].notna()
        if "asked_refund" in day_deals.columns:
            paid_mask = paid_mask & (day_deals["asked_refund"] != "Yes")
        paid = int(paid_mask.sum())
        ft_sched = int(day_deals["ft_scheduled_date"].notna().sum())
        activated = 0
        for _, r in day_deals[day_deals["ft_start_date"].notna()].iterrows():
            acct, email_used = resolve(r.get("login_email_id", ""), r.get("poc_email", ""))
            if acct is None:
                continue
            active_days = m._usage_28(email_used, ev_lu, acct=acct)[0]
            if active_days >= 2:
                activated += 1
        rows.append((cd, mql, ft_sched or None, ft_started or None,
                     activated or None, paid or None))

    print(f"Computed rows: {len(rows)} | most recent: {rows[0][0].date()} MQLs={rows[0][1]}")

    # ── write workbook ──
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET

    ws["A1"] = "SUMMARY"
    ws["A2"] = "Period"; ws["B2"] = "Day"
    for i, h in enumerate(DAILY_COLS[2:], start=3):
        ws.cell(row=2, column=i, value=h)

    n = len(rows)
    last_row = DATA_START_ROW + n - 1
    def band(period_row, label, start_offset, end_offset):
        # offsets are 0-based from DATA_START_ROW (0 = today's row)
        r0 = min(DATA_START_ROW + start_offset, last_row)
        r1 = min(DATA_START_ROW + end_offset, last_row)
        ws.cell(row=period_row, column=1, value=label)
        ws.cell(row=period_row, column=2,
                 value=f'=TEXT(A{r1},"dd-mmm")&" to "&TEXT(A{r0},"dd-mmm")')
        for col_letter in "CDEFG":
            ws[f"{col_letter}{period_row}"] = f"=SUM({col_letter}{r0}:{col_letter}{r1})"
        ws[f"H{period_row}"] = f'=IF(C{period_row}=0,"",D{period_row}/C{period_row})'
        ws[f"I{period_row}"] = f'=IF(D{period_row}=0,"",E{period_row}/D{period_row})'
        ws[f"J{period_row}"] = f'=IF(E{period_row}=0,"",F{period_row}/E{period_row})'
        ws[f"K{period_row}"] = f'=IF(E{period_row}=0,"",G{period_row}/E{period_row})'
        ws[f"L{period_row}"] = f'=IF(C{period_row}=0,"",E{period_row}/C{period_row})'
    band(3, "Last 7 days", 0, 6)
    band(4, "Prior 7 days", 7, 13)
    band(5, "Last 30 days", 0, 29)

    ws["A6"] = "DAILY COHORTS (newest on top)"
    for i, h in enumerate(DAILY_COLS, start=1):
        ws.cell(row=DATA_HEADER_ROW, column=i, value=h)

    for i, (cd, mql, ft_sched, ft_started, activated, paid) in enumerate(rows):
        r = DATA_START_ROW + i
        ws.cell(row=r, column=1, value=cd.to_pydatetime())
        ws.cell(row=r, column=1).number_format = "dd-mmm-yy"
        ws.cell(row=r, column=2, value=DOW[cd.weekday()])
        ws.cell(row=r, column=3, value=mql)
        ws.cell(row=r, column=4, value=ft_sched)
        ws.cell(row=r, column=5, value=ft_started)
        ws.cell(row=r, column=6, value=activated)
        ws.cell(row=r, column=7, value=paid)
        ws.cell(row=r, column=8, value=f'=IF(C{r}=0,"",D{r}/C{r})')
        ws.cell(row=r, column=9, value=f'=IF(D{r}=0,"",E{r}/D{r})')
        ws.cell(row=r, column=10, value=f'=IF(E{r}=0,"",F{r}/E{r})')
        ws.cell(row=r, column=11, value=f'=IF(E{r}=0,"",G{r}/E{r})')
        ws.cell(row=r, column=12, value=f'=IF(C{r}=0,"",E{r}/C{r})')

    pct_cols = ["H", "I", "J", "K", "L"]
    for r in [3, 4, 5] + list(range(DATA_START_ROW, last_row + 1)):
        for c in pct_cols:
            ws[f"{c}{r}"].number_format = "0.0%"

    widths = {"A": 13, "B": 6, "C": 8, "D": 12, "E": 11, "F": 10, "G": 8,
              "H": 13, "I": 15, "J": 15, "K": 13, "L": 13}
    for c, w in widths.items():
        ws.column_dimensions[c].width = w

    # Header styling (navy fill, white bold) -- SUMMARY/DAILY COHORTS section
    # titles and both header rows.
    ws["A1"].font = Font(bold=True)
    ws["A6"].font = Font(bold=True)
    for r in (2, DATA_HEADER_ROW):
        for c in range(1, N_DAILY_COLS + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = HEAD_FILL
            cell.font = HEAD_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Heat-color only the last column (MQL -> FT Started), red-yellow-green,
    # across both the summary rows and every daily row -- matches the
    # original manually-built sheet's styling. A live conditional-format
    # rule, not static per-cell fills, so it stays correct if values change.
    def heat_rule():
        return ColorScaleRule(
            start_type="min", start_color="FFF8696B",
            mid_type="percentile", mid_value=50, mid_color="FFFFEB84",
            end_type="max", end_color="FF63BE7B")
    ws.conditional_formatting.add("L3:L5", heat_rule())
    ws.conditional_formatting.add(f"L{DATA_START_ROW}:L{last_row}", heat_rule())

    ws.freeze_panes = "A8"

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    wb.save(OUT_PATH)
    print(f"Saved: {OUT_PATH}")

if __name__ == "__main__":
    main()
