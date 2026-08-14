"""WA Bot usage tracker page layout."""

WA_BOT_PAGE = """
<|part|class_name=topbar|
<|navbar|lov={nav_links}|class_name=main-nav|>
<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|part|
<|{aia_date_range}|date_range|label_start=Start Date|label_end=End Date|format=dd/MM/yyyy|on_change=on_aia_date|>
|>
<|part|
<div class="msc" data-key="wabot_segment"><div class="msc-cap">Segment</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{wabot_segment_ms}|text|mode=raw|class_name=msc-data msc-data-wabot_segment|>
|>
<|part|
<div class="msc" data-key="wabot_stage"><div class="msc-cap">Deal Stage</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{wabot_stage_ms}|text|mode=raw|class_name=msc-data msc-data-wabot_stage|>
|>
<|part|
<div class="msc wide" data-key="wabot_deal"><div class="msc-cap">Deal Name</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{wabot_deal_ms}|text|mode=raw|class_name=msc-data msc-data-wabot_deal|>
|>
|>
|>
|>

<|part|class_name=page-header|
# WA Bot Tracker

<|Refreshed at: {last_synced} IST|text|class_name=sync-stamp|>
|>

<|layout|columns=1 1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
Total Users

<|{wabot_kpi_users}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Paid Users

<|{wabot_kpi_paid_users}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
FT Users

<|{wabot_kpi_ft_users}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-blue|
Messages

<|{wabot_kpi_messages}|text|class_name=kpi-value|hover_text={wabot_kpi_messages_tip}|>
|>
<|part|class_name=kpi-card kpi-grey|
Upload vs Query

<|{wabot_kpi_split}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-green|
Active This Week

<|{wabot_kpi_wau}|text|class_name=kpi-value|>
|>
<|part|class_name=kpi-card kpi-grey|
Success Rate

<|{wabot_kpi_success}|text|class_name=kpi-value|hover_text={wabot_kpi_success_tip}|>
|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
**Adoption — Paid vs Free Trials vs Unknown**  (companies & messages)

<|chart|figure={wabot_adopt_fig}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
**What They Use It For**  (intent · upload vs query)

<|chart|figure={wabot_intent_fig}|plot_config={chart_config}|>
|>
|>

<|part|class_name=chart-card|
**Weekly Usage & Stickiness**  (messages + active companies per week)

<|chart|figure={wabot_trend_fig}|plot_config={chart_config}|>
|>

<|part|class_name=chart-card|
**Where the Bot Falls Short**  (non-success messages — what users asked & the bot's reply)

<|part|
<div class="msc wide" data-key="wabot_fail_intent"><div class="msc-cap">Intent</div><div class="msc-box"><span class="msc-text">All</span><span class="msc-arrow">▾</span></div><div class="msc-panel"></div></div>
<|{wabot_fail_intent_ms}|text|mode=raw|class_name=msc-data msc-data-wabot_fail_intent|>
|>

<|part|class_name=gridholder gridholder-wabot_fail|
<|{wabot_fail_json}|text|mode=raw|>
|>
<iframe src="/grid/wabot_fail" class="grid-frame" style="width:100%;height:540px;border:none;"></iframe>
|>

<|part|class_name=chart-card|
**Per-Company Detail**

<|part|class_name=gridholder gridholder-wabot_table|
<|{wabot_table_json}|text|mode=raw|>
|>
<iframe src="/grid/wabot_table" class="grid-frame" style="width:100%;height:620px;border:none;"></iframe>
|>
"""
