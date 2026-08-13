"""AIA Marketing Tracker page layout."""

MARKETING_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|part|class_name=nav-view-col|
<div class="msc-cap">View</div>
<|{mkt_view}|selector|lov={mkt_views}|dropdown|on_change=on_mkt_view|class_name=nav-view-sel|>
|>
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
<|part|class_name=dsig-datebar|
<|{mkt_sig_date}|date|on_change=on_mkt_sig_date|format=dd/MM/yyyy|min={mkt_sig_min}|max={mkt_sig_max}|editable=True|>
|>
<|{mkt_signals_html}|text|mode=raw|>
|>

<|part|class_name=chart-card|
**Monthly Performance**  *(view follows the nav-bar View dropdown)*

<|part|class_name=gridholder gridholder-mkt_monthly|
<|{mkt_monthly_json}|text|mode=raw|>
|>
<iframe src="/grid/mkt_monthly" class="grid-frame" style="width:100%;height:760px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Weekly Funnel (8W)**  *(view follows the nav-bar View dropdown)*

<|part|class_name=gridholder gridholder-mkt_weekly|
<|{mkt_weekly_json}|text|mode=raw|>
|>
<iframe src="/grid/mkt_weekly" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
<|part|class_name=mkt-tablehead|
**UTM Source Cohort**
<|{aia_date_range}|date_range|label_start=Start Date|label_end=End Date|format=dd/MM/yyyy|on_change=on_mkt_utm_date|class_name=mkt-utm-date|>
|>

<|part|class_name=gridholder gridholder-mkt_utm|
<|{mkt_utm_json}|text|mode=raw|>
|>
<iframe src="/grid/mkt_utm" class="grid-frame" style="width:100%;height:520px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**CPD vs Cost Per DC Trend**

<|chart|figure={mkt_cpl_fig}|plot_config={chart_config}|>
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
**Channel Distribution — Deals**  (click a slice to filter)

<|part|class_name=gridholder pieholder-mkt_leads|
<|{mkt_channel_leads_json}|text|mode=raw|>
|>
<iframe src="/pie/mkt_leads" class="grid-frame" style="width:100%;height:430px;border:none;"></iframe>
|>
|>
"""
