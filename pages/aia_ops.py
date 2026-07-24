"""AIA Ops Dashboard page layout."""

AIA_OPS_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1|gap=12px|
<|part|
<|{aia_date_range}|date_range|label_start=Start Date|label_end=End Date|format=dd/MM/yyyy|on_change=on_aia_date|>
|>
<|part|
<div class="msc" data-key="aia_owner"><div class="msc-cap">Deal Owner</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aia_owner_ms}|text|mode=raw|class_name=msc-data msc-data-aia_owner|>
|>
<|part|
<div class="msc wide" data-key="aia_campaign"><div class="msc-cap">UTM Campaign</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aia_campaign_ms}|text|mode=raw|class_name=msc-data msc-data-aia_campaign|>
|>
|>
|>
|>

<|part|class_name=page-header|
# AIA Ops Dashboard

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
AIA Paid

<|{aia_kpi_aia_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Revenue Collected

<|{aia_kpi_collected}|text|class_name=kpi-value|hover_text={aia_kpi_collected_exact}|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{aia_kpi_mrr}|text|class_name=kpi-value|hover_text={aia_kpi_mrr_exact}|>
|>
|>

<|layout|columns=1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Leads

<|{aia_kpi_leads}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
DS

<|{aia_kpi_ds}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
DC

<|{aia_kpi_dc}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
HI (ATP)

<|{aia_kpi_hi}|text|class_name=kpi-value|hover_text=Active HI deals with payment ETA in the selected period.|>
|>
<|part|class_name=kpi-card kpi-grey|
GST Paid

<|{aia_kpi_gst_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-red|
Refunds

<|{aia_kpi_refunds}|text|class_name=kpi-value|>
|>
|>

<|layout|columns=1 2|gap=16px|
<|part|class_name=chart-card|
**Marketing Funnel (Cohort)** <|ⓘ|text|hover_text=All leads that entered HI stage in the selected cohort, regardless of current stage.|class_name=info-ico|>

<|chart|figure={aia_funnel_fig}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
**Demos Scheduled vs Conducted vs Qualified Trend**

<|chart|figure={aia_trend_fig}|plot_config={trend_config}|>
|>
|>

<|part|class_name=chart-card|
**GM Performance**

<|part|class_name=gridholder gridholder-aia_gm|
<|{aia_gm_json}|text|mode=raw|>
|>
<iframe src="/grid/aia_gm" class="grid-frame" style="width:100%;height:560px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**UTM Source Cohort**

<|part|class_name=gridholder gridholder-aia_utm|
<|{aia_utm_json}|text|mode=raw|>
|>
<iframe src="/grid/aia_utm" class="grid-frame" style="width:100%;height:520px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**AIA + VA Incentive Tracker**

<|part|class_name=gridholder gridholder-aia_incentive|
<|{aia_incentive_json}|text|mode=raw|>
|>
<iframe src="/grid/aia_incentive" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Channel Distribution**  (click a slice to filter the page)

<|part|class_name=gridholder pieholder-aia_channel|
<|{aia_channel_pie_json}|text|mode=raw|>
|>
<|part|class_name=piebridge piebridge-aia_channel|
<|{aia_channel_click}|input|on_change=on_aia_channel_click|change_delay=0|>
|>
<iframe src="/pie/aia_channel" class="grid-frame" style="width:100%;height:430px;border:none;"></iframe>

<|part|class_name=active-filter|render={aia_filter_label != ""}|
<|{aia_filter_label}|text|> <|Show All|button|on_action=on_aia_channel_reset|class_name=reset-btn|>
|>
|>
<|part|class_name=chart-card|
**Discard Reasons**

<|{aia_discard_df}|table|page_size=10|class_name=data-table|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Parked Reasons**

<|{aia_parked_df}|table|page_size=10|class_name=data-table|>
|>
<|part|class_name=chart-card|
**Closed Lost Reasons**

<|{aia_lost_df}|table|page_size=10|class_name=data-table|>
|>
|>
"""
