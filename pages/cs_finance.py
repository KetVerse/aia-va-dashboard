"""CS & Finance page layout."""

CS_FINANCE_PAGE = """
<|part|class_name=page-header|
# CS & Finance
|>

<|part|class_name=filter-bar|
<|layout|columns=1 1|gap=12px|
<|part|
<|{cs_selected_owner}|selector|lov={cs_owner_list}|dropdown|filter|label=CS Owner|on_change=on_cs_filter_change|>
|>
<|part|
<|{cs_selected_deal}|selector|lov={cs_deal_list}|dropdown|filter|label=Deal Name|on_change=on_cs_filter_change|>
|>
|>
|>

<|layout|columns=1 1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Paid All

<|{cs_kpi_paid_all}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
AIA Paid

<|{cs_kpi_aia_paid}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Ready for Renewal

<|{cs_kpi_rfr}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-orange|
Blocked

<|{cs_kpi_blocked}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-red|
Refunds

<|{cs_kpi_refunds}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
AiA Renewed

<|{cs_kpi_renewed}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Integration Due

<|{cs_kpi_int_due}|text|class_name=kpi-value|>
|>
|>

<|layout|columns=1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Due ±7 Days

<|{cs_kpi_due_7d}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-orange|
Overdue Renewals

<|{cs_kpi_overdue}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{cs_kpi_mrr}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Active Customers

<|{cs_kpi_active}|text|class_name=kpi-value|>
|>
|>

<|part|class_name=chart-card|
**Revenue Matrix (₹)**

<|part|class_name=gridholder gridholder-cs_revenue|
<|{cs_revenue_matrix_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_revenue" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Retention Matrix**

<|part|class_name=gridholder gridholder-cs_retention|
<|{cs_retention_matrix_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_retention" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**CSM Performance — AIA Paid Customers**

<|part|class_name=gridholder gridholder-cs_csm_aia|
<|{cs_csm_aia_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_csm_aia" class="grid-frame" style="width:100%;height:330px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**CSM Performance — Engagement**

<|part|class_name=gridholder gridholder-cs_csm_eng|
<|{cs_csm_eng_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_csm_eng" class="grid-frame" style="width:100%;height:330px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**CSM Performance — Health**

<|part|class_name=gridholder gridholder-cs_csm_health|
<|{cs_csm_health_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_csm_health" class="grid-frame" style="width:100%;height:330px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Usage Cohort (Last 10 Weeks)**

<|part|class_name=gridholder gridholder-cs_cohort_count|
<|{cs_cohort_count_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_cohort_count" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Usage Cohort %**

<|part|class_name=gridholder gridholder-cs_cohort_pct|
<|{cs_cohort_pct_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_cohort_pct" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Usage & Health**

<|layout|columns=1 1 2|gap=12px|
<|part|
<|{cs_usage_deal}|selector|lov={cs_usage_deal_list}|dropdown|filter|label=Deal Name|on_change=on_cs_usage_filter|>
|>
<|part|
<|{cs_usage_csm}|selector|lov={cs_usage_csm_list}|dropdown|filter|label=CSM|on_change=on_cs_usage_filter|>
|>
<|part|

|>
|>

<|part|class_name=gridholder gridholder-cs_usage|
<|{cs_usage_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_usage" class="grid-frame" style="width:100%;height:560px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Renewal Window (Today ± 14 Days)**

<|part|class_name=gridholder gridholder-cs_renewal|
<|{cs_renewal_window_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_renewal" class="grid-frame" style="width:100%;height:520px;border:none;"></iframe>
|>
"""
