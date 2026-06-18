# AIA Ops Dashboard (Taipy)

Power BI → Taipy migration. Connects to Neon + Supabase, auto-refreshes every 30 min.

## Quick Start (local)

```bash
cp .env.example .env
# Edit .env with your Neon and Supabase credentials

pip install -r requirements.txt
taipy run main.py --port 8080
```

Open http://localhost:8080

## Deploy to VPS (Docker)

```bash
cp .env.example .env
# Edit .env with credentials

docker compose up -d --build
```

Dashboard available at http://your-vps-ip:8080

## Project Structure

```
main.py                 # Taipy app entry point + page layout
data/
  fetch.py              # SQL queries (Neon + Supabase)
  computed.py           # DAX calculated columns → pandas
  measures.py           # DAX measures → Python functions
```

## Pages

1. **AIA Ops Dashboard** (built) — KPIs, funnel, DC trend, GM table, UTM table, channel pie, reason tables
2. CS & Finance — TODO
3. AIA Marketing Tracker — TODO
4. VA Ops — TODO
5. VA Finance — TODO

## DAX → Python Mapping

- `USERELATIONSHIP(date_col, DateTable)` → filter `date_col` against slicer range
- `TREATAS(VALUES(...))` → pandas merge/join
- `DISTINCTCOUNT(record_id)` → `df["record_id"].nunique()`
- Cohort measures (cDS, cDC) → filter both create_date AND stage_date in range
- Revenue/Retention matrices → pivot tables with pro-rata allocation
