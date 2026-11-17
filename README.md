# Commercial, Industrial & Residential Solar, Battery Storage & EV Charging Fleet SLA Analytics

End-to-end **SQL, Python, and Power BI (`DAX`)** data warehouse and compliance analytics suite for monitoring deployed **solar PV, battery energy storage, EV charging, and grid interconnect fleets** across commercial/industrial, municipal, and residential customer segments.

## Architecture & Core Capabilities

1. **Cryptographically Verified Multi-Source Ingestion:** Validates SHA-256 checksums for the Open Power System Data (OPSD) 60-minute metered facility dataset (`38,454` hourly intervals; `1,132,970` facility sensor readings; `426,619` core Solar PV, Battery Storage, EV Charging, and Grid Interconnect meter snapshots across `11` deployed sites and `27` channels) and the NREL/CEC SAM Inverter Hardware Specifications Library (`3,262` certified solar/storage inverter models).
2. **Normalized SQL Star-Schema Warehouse:** Loads `dim_site_contract`, `dim_energy_asset`, `dim_cec_inverter_spec`, and `fct_monthly_asset_sla` in SQLite with strict foreign-key enforcement, cumulative meter telescoping conservation (`last - first == sum(pos_delta) + sum(neg_delta)`), and live vs. interpolated telemetry dropout separation.
3. **Contractual Obligations & Product Lifecycle Tracking:** Evaluates segment-specific contractual uptime SLAs (`98.5%` industrial, `98.0%` municipal, `97.0%` residential) across four project lifecycle milestones (`1_Commissioning`, `2_Provisional_Acceptance`, `3_Active_Warranty_SLA`, `4_Steady_State_OM`).
4. **Ad-Hoc Quantitative Hypothesis Testing:** Executes two-proportion $Z$-tests on segment uptime and SLA compliance plus Welch's $t$-tests on seasonal solar capacity utilization, exporting star-schema CSV marts and `DAX` measures for Power BI / Tableau dashboards.

## Verified Execution Results (`outputs/validation_and_hypothesis_summary.json`)

- **Fleet Telemetry Scale:** `426,619` Solar PV, Battery Storage, EV Charging & Grid Interconnect snapshots across `11` sites, `27` channels, and `620` evaluated asset-months (`144.80 MWh` verified solar generation; `3.18 MWh` EV charging delivery; `60.55%` industrial battery round-trip efficiency).
- **Live Uninterpolated Uptime & SLA Compliance:** `98.28%` hourly live telemetry uptime (`93.46%` monthly contractual SLA compliance) across commercial/industrial assets vs. `94.42%` hourly uptime (`81.11%` monthly SLA compliance) across residential assets (`+3.86 pp` uptime delta, $Z = 58.94$; `+12.35 pp` monthly SLA delta, $Z = 4.13$, $p = 3.66 \times 10^{-5}$), isolating `18,927` telemetry dropout intervals and `98` SLA-breach asset-months (`522/620` compliant).
- **Seasonal Solar Capacity Utilization:** Summer (`Apr-Sep`) vs. Winter (`Oct-Mar`) PV capacity factor shift of `+12.76 pp` (`18.76%` vs. `6.00%`, Welch $t = 17.78$, $p = 1.06 \times 10^{-70}$).
- **Deterministic Data Quality Gates:** Passed **12/12 (`100%`)** schema, foreign-key, cumulative meter conservation, and physical bound checks, plus **6/6** unit tests.

## Quickstart

```bash
python3 src/fleet_sla_pipeline.py
python3 -m unittest discover -s tests -v
sqlite3 outputs/energy_fleet_warehouse.sqlite < sql/fleet_contract_sla_views.sql
```

## Public Data Provenance

- **Primary Fleet Telemetry:** Open Power System Data (2020), *Data Package Household Data* (`household_data_60min_singleindex.csv`), Version `2020-04-15`, DOI `10.25832/household_data/2020-04-15` (`SHA-256: 15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60`).
- **Primary Hardware Specs:** NREL System Advisor Model (SAM) / California Energy Commission (CEC) Inverter Database (`sam-library-cec-inverters-2019-03-05.csv`, `SHA-256: c192252f0d61204df58fb0df95514a2aa529d88b45a236db7f16737661d616d8`).
- **Backup Public Dataset:** UCI Power Consumption & Solar Irradiance Distribution Zones Dataset (`52,416` 10-minute intervals, DOI `10.24432/C5B039`, CC BY 4.0).
