"""VA Finance Dashboard page layout."""

VA_FINANCE_PAGE = """
<|part|class_name=page-header|
# VA Finance Dashboard
|>

<|part|class_name=filter-bar|
<|layout|columns=1 1|gap=12px|
<|part|
<|{vaf_selected_deal}|selector|lov={vaf_deal_list}|dropdown|filter|label=Deal Name|on_change=on_vaf_filter_change|>
|>
<|part|
<|{vaf_selected_line_item}|selector|lov={vaf_line_item_list}|dropdown|filter|label=Line Item Name|on_change=on_vaf_filter_change|>
|>
|>
|>

<|layout|columns=1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Active Customers

<|{vaf_kpi_active}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Total Revenue

<|{vaf_kpi_revenue}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{vaf_kpi_mrr}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-orange|
Due ±14 Days

<|{vaf_kpi_due_14d}|text|class_name=kpi-value|>
|>
|>

<|part|class_name=chart-card|
**Revenue Matrix (₹)**

<|part|class_name=gridholder gridholder-vaf_revenue|
<|{vaf_revenue_matrix_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_revenue" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Retention Matrix**

<|part|class_name=gridholder gridholder-vaf_retention|
<|{vaf_retention_matrix_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_retention" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Renewal Window (Today ± 14 Days)**

<|part|class_name=gridholder gridholder-vaf_renewal|
<|{vaf_renewal_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_renewal" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>
"""
