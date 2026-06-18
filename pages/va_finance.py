"""VA Finance Dashboard page layout."""

VA_FINANCE_PAGE = """

<|part|class_name=page-header|
# VA Finance Dashboard

<|part|class_name=filter-bar|
<|layout|columns=1 1 1|gap=12px|
<|{vaf_start_date}|date|label=Start Date|on_change=on_vaf_filter_change|>
<|{vaf_end_date}|date|label=End Date|on_change=on_vaf_filter_change|>
<|{vaf_selected_deal}|selector|lov={vaf_deal_list}|dropdown|filter|label=Deal Name|on_change=on_vaf_filter_change|>
|>
|>

<|part|class_name=kpi-section|
<|layout|columns=1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#Active Customers|>
<|part|class_name=kpi-value|><|{vaf_kpi_active}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>₹ Total Revenue|>
<|part|class_name=kpi-value|><|{vaf_kpi_revenue}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>₹ MRR|>
<|part|class_name=kpi-value|><|{vaf_kpi_mrr}|text|>|>
|>
<|part|class_name=kpi-card kpi-orange|
<|part|class_name=kpi-label|>Due ±14 Days|>
<|part|class_name=kpi-value|><|{vaf_kpi_due_14d}|text|>|>
|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Revenue Matrix (₹)|>
<|{vaf_revenue_matrix}|table|page_size=15|class_name=data-table matrix-table|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Customer Retention Matrix|>
<|{vaf_retention_matrix}|table|page_size=15|class_name=data-table matrix-table|>
|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Revenue Trend|>
<|{vaf_revenue_trend_df}|chart|type=bar|x=BillingMonth|y=Revenue|layout={vaf_trend_layout}|plot_config={chart_config}|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Renewal Window (Today ± 14 Days)|>
<|{vaf_renewal_df}|table|page_size=20|class_name=data-table|>
|>

|>
"""
