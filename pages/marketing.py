"""AIA Marketing Tracker page layout."""

MARKETING_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1|gap=12px|
<|part|
<div class="msc" data-key="mkt_channel"><div class="msc-cap">Channel</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{mkt_channel_ms}|text|mode=raw|class_name=msc-data msc-data-mkt_channel|>
|>
<|part|
<div class="msc wide" data-key="mkt_campaign"><div class="msc-cap">UTM Campaign</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{mkt_campaign_ms}|text|mode=raw|class_name=msc-data msc-data-mkt_campaign|>
|>
<|part|
<div class="msc wide" data-key="mkt_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{mkt_deal_ms}|text|mode=raw|class_name=msc-data msc-data-mkt_deal|>
|>
|>
|>
|>

<|part|class_name=page-header|
# AIA Marketing Tracker

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|part|class_name=dsig-holder|
<|part|class_name=dsig-channelbar|
<|{mkt_sig_channel}|selector|lov={mkt_sig_channels}|dropdown|on_change=on_mkt_sig_channel|>
|>
<|part|class_name=dsig-datebar|
<|{mkt_sig_date}|date|on_change=on_mkt_sig_date|format=dd/MM/yyyy|min={mkt_sig_min}|max={mkt_sig_max}|editable=True|>
|>
<|{mkt_signals_html}|text|mode=raw|>
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
**Weekly Funnel (8W)**

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
