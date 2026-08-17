"""AIA Bot usage tracker page layout."""

AIA_BOT_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|part|
<|{aia_date_range}|date_range|label_start=Start Date|label_end=End Date|format=dd/MM/yyyy|on_change=on_aia_date|>
|>
<|part|
<div class="msc" data-key="aiabot_segment"><div class="msc-cap">Segment</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_segment_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_segment|>
|>
<|part|
<div class="msc" data-key="aiabot_stage"><div class="msc-cap">Deal Stage</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_stage_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_stage|>
|>
<|part|
<div class="msc wide" data-key="aiabot_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_deal_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_deal|>
|>
|>
|>
|>

<|part|class_name=page-header|
# AIA Bot Tracker

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Total Users

<|{aiabot_kpi_users}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Paid Users

<|{aiabot_kpi_paid_users}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-amber|
FT Users

<|{aiabot_kpi_ft_users}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Messages

<|{aiabot_kpi_messages}|text|class_name=kpi-value|hover_text={aiabot_kpi_messages_tip}|>
|>
<|part|class_name=kpi-card kpi-grey|
Upload vs Query

<|{aiabot_kpi_split}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Active This Week

<|{aiabot_kpi_wau}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Success Rate

<|{aiabot_kpi_success}|text|class_name=kpi-value|hover_text={aiabot_kpi_success_tip}|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Adoption — Paid vs Free Trials vs Unknown**  (companies & messages)

<|chart|figure={aiabot_adopt_fig}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
**What They Use It For**  (intent · upload vs query)

<|chart|figure={aiabot_intent_fig}|plot_config={chart_config}|>
|>
|>

<|part|class_name=chart-card|
**Weekly Usage & Stickiness**  (messages + active companies per week)

<|chart|figure={aiabot_trend_fig}|plot_config={chart_config}|>
|>

<|part|class_name=chart-card|
**Where the Bot Falls Short**  (non-success messages — what users asked & the bot's reply)

<|part|
<div class="msc wide" data-key="aiabot_fail_intent"><div class="msc-cap">Intent</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_fail_intent_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_fail_intent|>
|>

<|part|class_name=gridholder gridholder-aiabot_fail|
<|{aiabot_fail_json}|text|mode=raw|>
|>
<iframe src="/grid/aiabot_fail" class="grid-frame" style="width:100%;height:540px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**AIA Bot Activity Cohort**

<|layout|columns=1 1 1|gap=12px|
<|part|
<div class="msc wide" data-key="aiabot_cohort_company"><div class="msc-cap">Company</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_cohort_company_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_cohort_company|>
|>
<|part|
<div class="msc wide" data-key="aiabot_cohort_intent"><div class="msc-cap">Intent</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_cohort_intent_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_cohort_intent|>
|>
<|part|
<div class="msc" data-key="aiabot_cohort_view"><div class="msc-cap">View</div><div class="msc-box"><span class="msc-text">Both</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{aiabot_cohort_view_ms}|text|mode=raw|class_name=msc-data msc-data-aiabot_cohort_view|>
|>
|>

<|part|class_name=gridholder gridholder-aiabot_cohort|
<|{aiabot_cohort_json}|text|mode=raw|>
|>
<iframe src="/grid/aiabot_cohort" class="grid-frame" style="width:100%;height:450px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Per-Company Detail**

<|part|class_name=gridholder gridholder-aiabot_table|
<|{aiabot_table_json}|text|mode=raw|>
|>
<iframe src="/grid/aiabot_table" class="grid-frame" style="width:100%;height:620px;border:none;"></iframe>
|>
"""
