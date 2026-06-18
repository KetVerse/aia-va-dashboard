"""VA Ops Dashboard page layout."""

VA_OPS_PAGE = """

<|part|class_name=page-header|
# VA Ops Dashboard

<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|{va_start_date}|date|label=Start Date|on_change=on_va_filter_change|>
<|{va_end_date}|date|label=End Date|on_change=on_va_filter_change|>
<|{va_selected_owner}|selector|lov={va_owner_list}|dropdown|filter|label=Deal Owner|on_change=on_va_filter_change|>
<|{va_selected_campaign}|selector|lov={va_campaign_list}|dropdown|filter|label=UTM Campaign|on_change=on_va_filter_change|>
|>
|>

<|part|class_name=kpi-section|
<|layout|columns=1 1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#Leads|>
<|part|class_name=kpi-value|><|{va_kpi_leads}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#DS|>
<|part|class_name=kpi-value|><|{va_kpi_ds}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#DC|>
<|part|class_name=kpi-value|><|{va_kpi_dc}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#Agreed|>
<|part|class_name=kpi-value|><|{va_kpi_hi}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#Paid|>
<|part|class_name=kpi-value|><|{va_kpi_paid}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>₹ Total Revenue|>
<|part|class_name=kpi-value|><|{va_kpi_revenue}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>₹ MRR|>
<|part|class_name=kpi-value|><|{va_kpi_mrr}|text|>|>
|>
|>

<|layout|columns=1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>#Discard|>
<|part|class_name=kpi-value-sm|><|{va_kpi_discards}|text|>|>
|>
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>#Parked|>
<|part|class_name=kpi-value-sm|><|{va_kpi_parked}|text|>|>
|>
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>#Closed Lost|>
<|part|class_name=kpi-value-sm|><|{va_kpi_closed_lost}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>EOM Estimate|>
<|part|class_name=kpi-value-sm|><|{va_kpi_eom}|text|>|>
|>
|>
|>

<|layout|columns=1 2|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Marketing Funnel (Cohort)|>
<|{va_funnel_df}|chart|type=bar|x=Count|y=Stage|orientation=h|text=Label|layout={va_funnel_layout}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Demo Conducted Trend|>
<|{va_trend_df}|chart|type=bar|x=date_label|y=DC|layout={va_trend_layout}|plot_config={chart_config}|>
|>
|>

<|layout|columns=2 1|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>GM Performance|>
<|{va_gm_df}|table|page_size=50|class_name=data-table|on_sort=on_sort_va_gm|style=total_row_style|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Channel Distribution|>
<|{va_channel_df}|chart|type=pie|values=Count|labels=Channel|layout={va_pie_layout}|plot_config={chart_config}|>
|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>UTM Source Cohort|>
<|{va_utm_df}|table|page_size=50|class_name=data-table|on_sort=on_sort_va_utm|style=total_row_style|>
|>

<|layout|columns=1 1 1|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Discard Reasons|>
<|{va_discard_df}|table|page_size=10|class_name=data-table|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Lost Reasons|>
<|{va_lost_df}|table|page_size=10|class_name=data-table|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Parked Reasons|>
<|{va_parked_df}|table|page_size=10|class_name=data-table|>
|>
|>

|>
"""
