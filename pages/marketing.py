"""AIA Marketing Tracker page layout."""

MARKETING_PAGE = """

<|part|class_name=page-header|
# AIA Marketing Tracker

<|part|class_name=filter-bar|
<|layout|columns=1 1 1 1|gap=12px|
<|{mkt_start_date}|date|label=Start Date|on_change=on_mkt_filter_change|>
<|{mkt_end_date}|date|label=End Date|on_change=on_mkt_filter_change|>
<|{mkt_selected_deal}|selector|lov={mkt_deal_list}|dropdown|filter|label=Deal Name|on_change=on_mkt_filter_change|>
<|{mkt_selected_line_item}|selector|lov={mkt_line_item_list}|dropdown|filter|label=Line Item Name|on_change=on_mkt_filter_change|>
|>
|>

<|part|class_name=kpi-section|
<|layout|columns=1 1 1 1 1 1|gap=8px|
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>Total Spend|>
<|part|class_name=kpi-value|><|{mkt_kpi_spend}|text|>|>
|>
<|part|class_name=kpi-card kpi-blue|
<|part|class_name=kpi-label|>Total Leads|>
<|part|class_name=kpi-value|><|{mkt_kpi_leads}|text|>|>
|>
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>Avg CPL|>
<|part|class_name=kpi-value|><|{mkt_kpi_cpl}|text|>|>
|>
<|part|class_name=kpi-card kpi-grey|
<|part|class_name=kpi-label|>Avg CAC|>
<|part|class_name=kpi-value|><|{mkt_kpi_cac}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>₹ ARPU|>
<|part|class_name=kpi-value|><|{mkt_kpi_arpu}|text|>|>
|>
<|part|class_name=kpi-card kpi-green|
<|part|class_name=kpi-label|>Payback Period|>
<|part|class_name=kpi-value|><|{mkt_kpi_payback}|text|> mo|>
|>
|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Monthly Performance|>
<|{mkt_monthly_df}|table|page_size=20|class_name=data-table|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Spend vs Leads Trend|>
<|{mkt_spend_df}|chart|type=bar|x=YearMonth|y[1]=Spend|y[2]=Leads|layout={mkt_trend_layout}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>CPL vs CAC Trend|>
<|{mkt_cpl_df}|chart|type=line|x=YearMonth|y[1]=CPL|y[2]=CAC|layout={mkt_cpl_layout}|plot_config={chart_config}|>
|>
|>

<|part|class_name=chart-card|
<|part|class_name=chart-title|>Weekly Breakdown (Current Month)|>
<|{mkt_weekly_df}|table|page_size=10|class_name=data-table|>
|>

<|layout|columns=1 1|gap=16px|
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Channel Distribution — Spend|>
<|{mkt_channel_spend_df}|chart|type=pie|values=Spend|labels=Channel|layout={mkt_pie_layout}|plot_config={chart_config}|>
|>
<|part|class_name=chart-card|
<|part|class_name=chart-title|>Channel Distribution — Leads|>
<|{mkt_channel_leads_df}|chart|type=pie|values=Leads|labels=Channel|layout={mkt_pie_layout}|plot_config={chart_config}|>
|>
|>

|>
"""
