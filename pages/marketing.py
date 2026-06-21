"""AIA Marketing Tracker page layout."""

MARKETING_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
|>

<|part|class_name=page-header|
# AIA Marketing Tracker

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Total Spend

<|{mkt_kpi_spend}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Total Leads

<|{mkt_kpi_leads}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Avg CPL

<|{mkt_kpi_cpl}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Avg CAC

<|{mkt_kpi_cac}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
ARPU

<|{mkt_kpi_arpu}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Payback Period

<|{mkt_kpi_payback}|text|class_name=kpi-value|>
|>
|>

<|part|class_name=chart-card|
**Monthly Performance**

<|part|class_name=gridholder gridholder-mkt_monthly|
<|{mkt_monthly_json}|text|mode=raw|>
|>
<iframe src="/grid/mkt_monthly" class="grid-frame" style="width:100%;height:760px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Weekly Breakdown**

<|part|class_name=gridholder gridholder-mkt_weekly|
<|{mkt_weekly_json}|text|mode=raw|>
|>
<iframe src="/grid/mkt_weekly" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Spend vs Leads Trend**

<|{mkt_spend_df}|chart|type=bar|x=YearMonth|y[1]=Spend|y[2]=Leads|layout={mkt_trend_layout}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
**CPL vs CAC Trend**

<|{mkt_cpl_df}|chart|type=line|x=YearMonth|y[1]=CPL|y[2]=CAC|layout={mkt_cpl_layout}|plot_config={chart_config}|>
|>
|>

<|part|class_name=piebridge piebridge-mkt_channel|
<|{mkt_channel_click}|input|on_change=on_mkt_channel_click|change_delay=0|>
|>
<|part|class_name=piebridge piebridge-mkt_leads|
<|{mkt_leads_click}|input|on_change=on_mkt_leads_click|change_delay=0|>
|>

<|part|class_name=active-filter|render={mkt_filter_label != ""}|
<|{mkt_filter_label}|text|> <|Show All|button|on_action=on_mkt_channel_reset|class_name=reset-btn|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Channel Distribution — Spend**  (click a slice to filter)

<|part|class_name=gridholder pieholder-mkt_channel|
<|{mkt_channel_spend_json}|text|mode=raw|>
|>
<iframe src="/pie/mkt_channel" class="grid-frame" style="width:100%;height:430px;border:none;"></iframe>
|>
<|part|class_name=chart-card|
**Channel Distribution — Leads**  (click a slice to filter)

<|part|class_name=gridholder pieholder-mkt_leads|
<|{mkt_channel_leads_json}|text|mode=raw|>
|>
<iframe src="/pie/mkt_leads" class="grid-frame" style="width:100%;height:430px;border:none;"></iframe>
|>
|>
"""
