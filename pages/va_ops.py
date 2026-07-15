"""VA Ops Dashboard page layout."""

VA_OPS_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|part|
<|{va_start_date}|date|label=Start Date|format=dd/MM/yyyy|on_change=on_va_filter_change|>
|>
<|part|
<|{va_end_date}|date|label=End Date|format=dd/MM/yyyy|on_change=on_va_filter_change|>
|>
<|part|
<div class="msc" data-key="va_owner"><div class="msc-cap">Deal Owner</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{va_owner_ms}|text|mode=raw|class_name=msc-data msc-data-va_owner|>
|>
<|part|
<div class="msc wide" data-key="va_campaign"><div class="msc-cap">UTM Campaign</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{va_campaign_ms}|text|mode=raw|class_name=msc-data msc-data-va_campaign|>
|>
|>
|>
|>

<|part|class_name=page-header|
# VA Ops Dashboard

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Leads

<|{va_kpi_leads}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
DS

<|{va_kpi_ds}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
DC

<|{va_kpi_dc}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
HI (ATP)

<|{va_kpi_hi}|text|class_name=kpi-value|hover_text=Active HI deals with payment ETA in the selected period.|>
|>
<|part|class_name=kpi-card kpi-blue|
Paid

<|{va_kpi_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Total Revenue

<|{va_kpi_revenue}|text|class_name=kpi-value|hover_text={va_kpi_revenue_exact}|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{va_kpi_mrr}|text|class_name=kpi-value|hover_text={va_kpi_mrr_exact}|>
|>
<|part|class_name=kpi-card kpi-blue|
EOM Estimate

<|{va_kpi_eom}|text|class_name=kpi-value|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Marketing Funnel (Cohort)** <|ⓘ|text|hover_text=All leads that entered HI stage in the selected cohort, regardless of current stage.|class_name=info-ico|>

<|chart|figure={va_funnel_fig}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
**Demo Conducted Trend**

<|{va_trend_df}|chart|type=bar|x=date_label|y=DC|layout={va_trend_layout}|plot_config={chart_config}|>
|>
|>

<|part|class_name=chart-card|
**GM Performance**

<|part|class_name=gridholder gridholder-va_gm|
<|{va_gm_json}|text|mode=raw|>
|>
<iframe src="/grid/va_gm" class="grid-frame" style="width:100%;height:520px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**UTM Source Cohort**

<|part|class_name=gridholder gridholder-va_utm|
<|{va_utm_json}|text|mode=raw|>
|>
<iframe src="/grid/va_utm" class="grid-frame" style="width:100%;height:520px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**AM Incentive Tracker**

<|part|class_name=gridholder gridholder-va_incentive|
<|{va_incentive_json}|text|mode=raw|>
|>
<iframe src="/grid/va_incentive" class="grid-frame" style="width:100%;height:360px;border:none;"></iframe>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Channel Distribution**  (click a slice to filter the page)

<|part|class_name=gridholder pieholder-va_channel|
<|{va_channel_pie_json}|text|mode=raw|>
|>
<|part|class_name=piebridge piebridge-va_channel|
<|{va_channel_click}|input|on_change=on_va_channel_click|change_delay=0|>
|>
<iframe src="/pie/va_channel" class="grid-frame" style="width:100%;height:430px;border:none;"></iframe>

<|part|class_name=active-filter|render={va_filter_label != ""}|
<|{va_filter_label}|text|> <|Show All|button|on_action=on_va_channel_reset|class_name=reset-btn|>
|>
|>
<|part|class_name=chart-card|
**Discard Reasons**

<|{va_discard_df}|table|page_size=10|class_name=data-table|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Closed Lost Reasons**

<|{va_lost_df}|table|page_size=10|class_name=data-table|>
|>
<|part|class_name=chart-card|
**Parked Reasons**

<|{va_parked_df}|table|page_size=10|class_name=data-table|>
|>
|>
"""
