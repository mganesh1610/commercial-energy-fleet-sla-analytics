-- Governed SQL Joins & CTEs Views for Solar, Storage & EV Charging Contractual SLA Reporting

WITH asset_monthly_ledger AS (
    SELECT
        m.year_month,
        s.customer_segment,
        m.site_id,
        m.asset_id,
        a.asset_type,
        m.lifecycle_stage,
        m.expected_commissioned_hours,
        m.valid_metered_hours,
        m.uptime_availability_ratio,
        m.contractual_sla_target,
        m.sla_compliant_flag,
        m.verified_energy_kwh,
        m.telemetry_gap_incidents,
        AVG(m.uptime_availability_ratio) OVER (
            PARTITION BY m.asset_id
            ORDER BY m.year_month
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) AS rolling_3m_uptime_ratio
    FROM fct_monthly_asset_sla m
    INNER JOIN dim_energy_asset a ON m.asset_id = a.asset_id
    INNER JOIN dim_site_contract s ON m.site_id = s.site_id
),
sla_breach_watchlist AS (
    SELECT
        year_month,
        customer_segment,
        site_id,
        asset_id,
        asset_type,
        lifecycle_stage,
        ROUND(uptime_availability_ratio * 100.0, 2) AS actual_uptime_pct,
        ROUND(contractual_sla_target * 100.0, 2) AS target_sla_pct,
        ROUND((uptime_availability_ratio - contractual_sla_target) * 100.0, 2) AS sla_margin_pp,
        telemetry_gap_incidents,
        ROUND(rolling_3m_uptime_ratio * 100.0, 2) AS rolling_3m_uptime_pct
    FROM asset_monthly_ledger
    WHERE sla_compliant_flag = 0
)
SELECT *
FROM sla_breach_watchlist
ORDER BY sla_margin_pp ASC, telemetry_gap_incidents DESC;
