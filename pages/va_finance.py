"""VA Finance Dashboard page layout."""

VA_FINANCE_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1|gap=12px|
<|part|
<div class="msc wide" data-key="vaf_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_deal_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_deal|>
|>
<|part|
<div class="msc wide" data-key="vaf_line_item"><div class="msc-cap">Line Item Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_line_item_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_line_item|>
|>
<|part|
<div class="msc" data-key="vaf_rectype"><div class="msc-cap">Recurring Type</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_rectype_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_rectype|>
|>
|>
|>
|>

<|part|class_name=page-header|
# VA Finance Dashboard

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Total Customers

<|{vaf_kpi_active}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-red|
Refunds

<|{vaf_kpi_refunds}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Total Revenue

<|{vaf_kpi_revenue}|text|class_name=kpi-value|hover_text={vaf_kpi_revenue_exact}|>
|>
<|part|class_name=kpi-card kpi-green|
MRR

<|{vaf_kpi_mrr}|text|class_name=kpi-value|hover_text={vaf_kpi_mrr_exact}|>
|>
<|part|class_name=kpi-card kpi-orange|
Due ±14 Days

<|{vaf_kpi_due_14d}|text|class_name=kpi-value|>
|>
|>

<|part|class_name=chart-card|
**Revenue Matrix (₹)** <|ⓘ|text|hover_text={vaf_rev_tip}|class_name=info-ico|>

<|part|class_name=gridholder gridholder-vaf_revenue|
<|{vaf_revenue_matrix_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_revenue" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Customer Retention Matrix** <|ⓘ|text|hover_text={vaf_ret_tip}|class_name=info-ico|>

<|part|class_name=gridholder gridholder-vaf_retention|
<|{vaf_retention_matrix_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_retention" class="grid-frame" style="width:100%;height:480px;border:none;"></iframe>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Parked Customers — Reason × AM**

<|part|class_name=gridholder gridholder-vaf_parked|
<|{vaf_parked_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_parked" class="grid-frame" style="width:100%;height:360px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Churned Customers — Reason × AM**

<|part|class_name=gridholder gridholder-vaf_churned|
<|{vaf_churned_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_churned" class="grid-frame" style="width:100%;height:360px;border:none;"></iframe>
|>
|>

<|part|class_name=chart-card|
**Accounts Receivable Tracker**

<|layout|columns=1 1 1 1 1|gap=12px|
<|part|
<div class="msc wide" data-key="vaf_ar_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_ar_deal_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_ar_deal|>
|>
<|part|
<div class="msc" data-key="vaf_ar_stage"><div class="msc-cap">Deal Stage</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_ar_stage_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_ar_stage|>
|>
<|part|
<div class="msc" data-key="vaf_ar_am"><div class="msc-cap">AM</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_ar_am_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_ar_am|>
|>
<|part|
<div class="msc" data-key="vaf_ar_owner"><div class="msc-cap">Deal Owner</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_ar_owner_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_ar_owner|>
|>
<|part|
<div class="msc" data-key="vaf_ar_status"><div class="msc-cap">Status</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{vaf_ar_status_ms}|text|mode=raw|class_name=msc-data msc-data-vaf_ar_status|>
|>
|>

<|part|class_name=gridholder gridholder-vaf_ar|
<|{vaf_ar_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_ar" class="grid-frame" style="width:100%;height:560px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Renewal Window (Today ± 14 Days)**

<|part|class_name=gridholder gridholder-vaf_renewal|
<|{vaf_renewal_json}|text|mode=raw|>
|>
<iframe src="/grid/vaf_renewal" class="grid-frame" style="width:100%;height:420px;border:none;"></iframe>
|>
"""
