"""CS & Finance page layout."""

CS_FINANCE_PAGE = """

<|part|class_name=page-header|
# CS & Finance

<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|{cs_start_date}|date|label=Start Date|on_change=on_cs_filter_change|>
<|{cs_end_date}|date|label=End Date|on_change=on_cs_filter_change|>
<|{cs_selected_owner}|selector|lov={cs_owner_list}|dropdown|filter|label=CS Owner|on_change=on_cs_filter_change|>
<|{cs_selected_deal}|selector|lov={cs_deal_list}|dropdown|filter|label=Deal Name|on_change=on_cs_filter_change|>
|>
|>

<|part|class_name=kpi-section|
<|layout|columns=1 1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#Paid All|>
<|part|class_name=kpi-value|><|{cs_kpi_paid_all}|text|>|>
|>
<|part|class_name=kpi-card kpi-orange|
<|part|class_name=kpi-label|>Overdue Renewals|>
<|part|class_name=kpi-value|><|{cs_kpi_overdue}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>Due ±7 Days|>
<|part|class_name=kpi-value|><|{cs_kpi_due_7d}|text|>|>
|>
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>#Integration Due|>
<|part|class_name=kpi-value|><|{cs_kpi_int_due}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>#AiA Renewed|>
<|part|class_name=kpi-value|><|{cs_kpi_renewed}|text|>|>
|>
<|part|class_name=kpi-card kpi-red|
<|part|class_name=kpi-label|>#Refunds|>
<|part|class_name=kpi-value|><|{cs_kpi_refunds}|text|>|>
|>
<|part|class_name=kpi-card kpi-orange|
<|part|class_name=kpi-label|>Blocked|>
<|part|class_name=kpi-value|><|{cs_kpi_blocked}|text|>|>
|>
|>

<|layout|columns=1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>#Ready for Renewal|>
<|part|class_name=kpi-value-sm|><|{cs_kpi_rfr}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>#AIA Paid|>
<|part|class_name=kpi-value-sm|><|{cs_kpi_aia_paid}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>₹ MRR|>
<|part|class_name=kpi-value-sm|><|{cs_kpi_mrr}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>Active Customers|>
<|part|class_name=kpi-value-sm|><|{cs_kpi_active}|text|>|>
|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Revenue Matrix (₹)|>
<|{cs_revenue_matrix}|table|page_size=15|class_name=data-table matrix-table|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Customer Retention Matrix|>
<|{cs_retention_matrix}|table|page_size=15|class_name=data-table matrix-table|>
|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>CSM Performance|>
<|layout|columns=1 1|gap=16px|
<|part|
<|part|class_name=table-subtitle|>AIA Paid Customers|>
<|{cs_csm_aia_df}|table|page_size=10|class_name=data-table|>
|>
<|part|
<|part|class_name=table-subtitle|>ID + RFR + Renewed|>
<|{cs_csm_rfr_df}|table|page_size=10|class_name=data-table|>
|>
|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Customer Usage & Health|>
<|{cs_usage_df}|table|page_size=15|class_name=data-table|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Customer Usage Cohort (Last 10 Weeks)|>
<|{cs_cohort_df}|table|page_size=12|class_name=data-table matrix-table|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Renewal Window (Today ± 14 Days)|>
<|{cs_renewal_window_df}|table|page_size=20|class_name=data-table|>
|>

|>
"""
