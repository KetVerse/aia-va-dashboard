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
<|{va_selected_owner}|selector|lov={va_owner_list}|dropdown|filter|label=Deal Owner|on_change=on_va_filter_change|>
|>
<|part|
<|{va_selected_campaign}|selector|lov={va_campaign_list}|dropdown|filter|label=UTM Campaign|on_change=on_va_filter_change|class_name=wide-filter|>
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
Agreed

<|{va_kpi_hi}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Paid

<|{va_kpi_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Total Revenue

<|{va_kpi_revenue}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{va_kpi_mrr}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
EOM Estimate

<|{va_kpi_eom}|text|class_name=kpi-value|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Marketing Funnel (Cohort)**

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
