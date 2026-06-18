"""AIA Ops Dashboard page layout."""

AIA_OPS_PAGE = """
<|part|class_name=page-header|
# AIA Ops Dashboard
|>

<|layout|columns=1 1 1 1|gap=16px|
<|part|
<|{aia_start_date}|date|label=Start Date|on_change=on_aia_filter_change|>
|>
<|part|
<|{aia_end_date}|date|label=End Date|on_change=on_aia_filter_change|>
|>
<|part|
<|{aia_selected_owner}|selector|lov={aia_owner_list}|dropdown|filter|label=Deal Owner|on_change=on_aia_filter_change|>
|>
<|part|
<|{aia_selected_campaign}|selector|lov={aia_campaign_list}|dropdown|filter|label=UTM Campaign|on_change=on_aia_filter_change|>
|>
|>

<|layout|columns=1 1 1 1 1 1 1|gap=8px|
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
High Intent

<|{aia_kpi_hi}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
AIA Paid

<|{aia_kpi_aia_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Paid

<|{aia_kpi_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Revenue Collected

<|{aia_kpi_collected}|text|class_name=kpi-value|>
|>
|>

<|layout|columns=1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-grey|
GST Paid

<|{aia_kpi_gst_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-red|
Refunds

<|{aia_kpi_refunds}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Parked

<|{aia_kpi_parked}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Discards

<|{aia_kpi_discards}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Closed Lost

<|{aia_kpi_closed_lost}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{aia_kpi_mrr}|text|class_name=kpi-value|>
|>
|>

<|layout|columns=1 2|gap=16px|
<|part|class_name=chart-card|
**Marketing Funnel (Cohort)**

<|chart|figure={aia_funnel_fig}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
**Demo Conducted vs Qualified Trend**

<|{aia_trend_df}|chart|type=bar|x=date_label|y[1]=DC|y[2]=Qualified|layout={aia_trend_layout}|plot_config={chart_config}|>
|>
|>

<|part|class_name=chart-card|
**GM Performance**

<|{aia_gm_df}|table|page_size=50|class_name=data-table|on_sort=on_sort_gm|style=total_row_style|>
|>

<|part|class_name=chart-card|
**UTM Source Cohort**

<|{aia_utm_df}|table|page_size=50|class_name=data-table|on_sort=on_sort_utm|style=total_row_style|>
|>

<|part|class_name=chart-card|
**Channel Distribution**

<|{aia_channel_df}|chart|type=pie|values=Count|labels=Channel|layout={aia_pie_layout}|plot_config={chart_config}|>
|>

<|layout|columns=1 1 1|gap=16px|
<|part|class_name=chart-card|
**Discard Reasons**

<|{aia_discard_df}|table|page_size=10|class_name=data-table|>
|>
<|part|class_name=chart-card|
**Lost Reasons**

<|{aia_lost_df}|table|page_size=10|class_name=data-table|>
|>
<|part|class_name=chart-card|
**Parked Reasons**

<|{aia_parked_df}|table|page_size=10|class_name=data-table|>
|>
|>

<|part|class_name=chart-card|
**AIA + VA Incentive Tracker**

<|{aia_incentive_df}|table|page_size=50|class_name=data-table|on_sort=on_sort_incentive|style=total_row_style|>
|>
"""