# Data Schema, Formulations & Validation Methodology

## 1. Star-Schema Tables

- `dim_site_contract(site_id PK, customer_segment, contractual_uptime_sla, commissioned_month, decommissioned_or_eof_month)`
- `dim_energy_asset(asset_id PK, site_id FK, customer_segment, asset_type, estimated_peak_kw, total_metered_snapshots, net_verified_kwh, quarantined_reset_count)`
- `dim_cec_inverter_spec(inverter_name PK, vac_volts, paco_watts, pdco_watts, vdco_volts, pnt_watts, mppt_low_volts, mppt_high_volts, nominal_efficiency, scale_tier, cec_type)`
- `fct_monthly_asset_sla((asset_id, year_month) PK, site_id FK, lifecycle_stage, expected_commissioned_hours, valid_metered_hours, uptime_availability_ratio, contractual_sla_target, sla_compliant_flag, verified_energy_kwh, capacity_utilization_ratio, telemetry_gap_incidents, meter_reset_anomalies)`

## 2. Analytical Formulations

1. **Cumulative Meter Telescoping & Reset Quarantine:**
   $$\Delta E_{i,t} = M_{i,t} - M_{i,t-1}, \quad E_{i,t}^{\text{verified}} = \max(0, \Delta E_{i,t}) \cdot \mathbb{I}(\Delta E_{i,t} \ge -10^{-6})$$
   Exact telescoping conservation enforces $M_{i,T} - M_{i,t_0} = \sum_t \Delta E_{i,t}^+ + \sum_t \Delta E_{i,t}^-$.
2. **Contractual Live Telemetry Uptime Availability:**
   $$U_{i,m} = \frac{\sum_{t \in m} \mathbb{I}(\text{meter}_{i,t} \text{ present} \land i \notin \text{interpolated}_t)}{H_{i,m}^{\text{commissioned}}}, \quad \text{SLA\_Compliant}_{i,m} = \mathbb{I}(U_{i,m} \ge \tau_{\text{segment}})$$
3. **Battery Energy Storage Round-Trip Efficiency (`RTE`):**
   $$\eta_{\text{RTE}} = \frac{\sum_t E_{\text{discharge},t}^{\text{verified}}}{\sum_t E_{\text{charge},t}^{\text{verified}}}$$
4. **Statistical Hypothesis Testing:** Two-proportion pooled $Z$-test across commercial/industrial vs. residential uptime and SLA compliance, plus Welch's unequal-variance $t$-test across seasonal solar capacity utilization.
