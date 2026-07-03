"""CS & Finance page layout."""

CS_FINANCE_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1|gap=12px|
<|part|
<div class="msc" data-key="cs_owner"><div class="msc-cap">CS Owner</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_owner_ms}|text|mode=raw|class_name=msc-data msc-data-cs_owner|>
|>
<|part|
<div class="msc wide" data-key="cs_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_deal_ms}|text|mode=raw|class_name=msc-data msc-data-cs_deal|>
|>
<|part|
<div class="msc" data-key="cs_rectype"><div class="msc-cap">Recurring Type</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_rectype_ms}|text|mode=raw|class_name=msc-data msc-data-cs_rectype|>
|>
|>
|>
|>

<|part|class_name=page-header|
# CS & Finance

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1 1 1 1|gap=8px|
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
Product Blocked

<|{cs_kpi_blocked}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
AiA Renewed

<|{cs_kpi_renewed}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{cs_kpi_mrr}|text|class_name=kpi-value|hover_text={cs_kpi_mrr_exact}|>
|>
|>

<|layout|columns=1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-red|
Refunds

<|{cs_kpi_refunds}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Due ±7 Days

<|{cs_kpi_due_7d}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-orange|
Overdue Renewals

<|{cs_kpi_overdue}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Integration Due

<|{cs_kpi_int_due}|text|class_name=kpi-value|>
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
**Customer Activity Cohort**

<|layout|columns=1 1 1 1|gap=12px|
<|part|
<div class="msc" data-key="cs_activity_event"><div class="msc-cap">Event Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_activity_event_ms}|text|mode=raw|class_name=msc-data msc-data-cs_activity_event|>
|>
<|part|
<div class="msc wide" data-key="cs_activity_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_activity_deal_ms}|text|mode=raw|class_name=msc-data msc-data-cs_activity_deal|>
|>
<|part|
<div class="msc" data-key="cs_activity_stage"><div class="msc-cap">Deal Stage</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_activity_stage_ms}|text|mode=raw|class_name=msc-data msc-data-cs_activity_stage|>
|>
<|part|
<div class="msc" data-key="cs_activity_csm"><div class="msc-cap">CSM</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_activity_csm_ms}|text|mode=raw|class_name=msc-data msc-data-cs_activity_csm|>
|>
|>

<|part|class_name=gridholder gridholder-cs_activity_count|
<|{cs_activity_count_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_activity_count" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Activity Cohort %**

<|part|class_name=gridholder gridholder-cs_activity_pct|
<|{cs_activity_pct_json}|text|mode=raw|>
|>
<iframe src="/grid/cs_activity_pct" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Usage Cohort (Accounting Sync & Uploads only)**

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

<|layout|columns=1 1 1 1|gap=12px|
<|part|
<div class="msc wide" data-key="cs_usage_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_usage_deal_ms}|text|mode=raw|class_name=msc-data msc-data-cs_usage_deal|>
|>
<|part|
<div class="msc" data-key="cs_usage_csm"><div class="msc-cap">CSM</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_usage_csm_ms}|text|mode=raw|class_name=msc-data msc-data-cs_usage_csm|>
|>
<|part|
<div class="msc" data-key="cs_usage_stage"><div class="msc-cap">Deal Stage</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_usage_stage_ms}|text|mode=raw|class_name=msc-data msc-data-cs_usage_stage|>
|>
<|part|
<div class="msc" data-key="cs_usage_owner"><div class="msc-cap">Deal Owner</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{cs_usage_owner_ms}|text|mode=raw|class_name=msc-data msc-data-cs_usage_owner|>
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
