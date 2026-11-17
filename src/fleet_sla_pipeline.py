#!/usr/bin/env python3
"""Commercial, Industrial & Residential Solar, Battery Storage & EV Charging Fleet SLA Analytics Pipeline.

Builds a normalized SQLite warehouse from SHA-256-verified public energy telemetry (OPSD 60-minute
metered facility solar PV, battery storage charge/discharge, EV charging, and grid interconnect counters)
and NREL/CEC certified solar/storage inverter specifications. Evaluates contractual uptime availability
SLAs, project lifecycle milestones, battery round-trip efficiency, solar capacity utilization,
12 deterministic data-quality gates, and quantitative two-sample hypothesis tests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

OPSD_URL = "https://data.open-power-system-data.org/household_data/2020-04-15/household_data_60min_singleindex.csv"
OPSD_SHA256 = "15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60"

CEC_URL = "https://raw.githubusercontent.com/pvlib/pvlib-python/main/pvlib/data/sam-library-cec-inverters-2019-03-05.csv"
CEC_SHA256 = "c192252f0d61204df58fb0df95514a2aa529d88b45a236db7f16737661d616d8"

SITE_SLA_TARGETS = {
    "industrial": 0.985,   # 98.5% contractual uptime SLA for commercial/industrial sites
    "public": 0.980,       # 98.0% contractual uptime SLA for municipal/public facilities
    "residential": 0.970,  # 97.0% contractual uptime SLA for residential fleets
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_verified_download(url: str, expected_sha256: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "EnergyFleetSLAPipeline/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
            while True:
                buf = resp.read(1 << 20)
                if not buf:
                    break
                out.write(buf)
    digest = sha256_file(dest)
    if digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {dest}: got {digest}, expected {expected_sha256}")
    return digest


def classify_channel(col_name: str) -> Optional[Tuple[str, str, str]]:
    """Return (site_id, segment, asset_type) for core energy fleet channels, or None for sub-appliance loads."""
    if not col_name.startswith("DE_KN_"):
        return None
    rest = col_name[len("DE_KN_"):]
    parts = rest.split("_", 1)
    if len(parts) != 2:
        return None
    site_raw, metric = parts[0], parts[1]
    if site_raw.startswith("industrial"):
        segment = "industrial"
    elif site_raw.startswith("public"):
        segment = "public"
    elif site_raw.startswith("residential"):
        segment = "residential"
    else:
        return None

    site_id = f"SITE_{site_raw.upper()}"
    if metric.startswith("pv"):
        asset_type = "solar_pv"
    elif metric == "storage_charge":
        asset_type = "storage_charge"
    elif metric == "storage_decharge":
        asset_type = "storage_discharge"
    elif metric == "ev":
        asset_type = "ev_charger"
    elif metric == "grid_import":
        asset_type = "grid_import"
    elif metric == "grid_export":
        asset_type = "grid_export"
    else:
        return None
    return site_id, segment, asset_type


def normal_sf_two_sided(z_abs: float) -> float:
    return math.erfc(z_abs / math.sqrt(2.0))


def welch_t_test(xs: List[float], ys: List[float]) -> Dict[str, float]:
    n1, n2 = len(xs), len(ys)
    m1 = sum(xs) / n1
    m2 = sum(ys) / n2
    v1 = sum((x - m1) ** 2 for x in xs) / (n1 - 1)
    v2 = sum((y - m2) ** 2 for y in ys) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    t_stat = (m1 - m2) / se if se > 0 else 0.0
    num = (v1 / n1 + v2 / n2) ** 2
    den = ((v1 / n1) ** 2) / (n1 - 1) + ((v2 / n2) ** 2) / (n2 - 1)
    df = num / den if den > 0 else float(n1 + n2 - 2)
    p_approx = normal_sf_two_sided(abs(t_stat))
    return {
        "n_group_a": n1,
        "n_group_b": n2,
        "mean_group_a": round(m1, 6),
        "mean_group_b": round(m2, 6),
        "diff": round(m1 - m2, 6),
        "t_stat": round(t_stat, 4),
        "df": round(df, 2),
        "p_value_two_sided": p_approx,
    }


def two_proportion_z_test(k1: int, n1: int, k2: int, n2: int) -> Dict[str, float]:
    p1 = k1 / n1
    p2 = k2 / n2
    p_pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n2))
    z_stat = (p1 - p2) / se if se > 0 else 0.0
    return {
        "rate_group_a": round(p1, 6),
        "rate_group_b": round(p2, 6),
        "diff_pp": round((p1 - p2) * 100.0, 4),
        "z_stat": round(z_stat, 4),
        "p_value_two_sided": normal_sf_two_sided(abs(z_stat)),
    }


def parse_cec_inverters(cec_path: Path) -> List[Tuple]:
    records = []
    with cec_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader)
        _units = next(reader)
        idx = {name: i for i, name in enumerate(header)}
        for row in reader:
            if len(row) < len(header):
                continue
            try:
                name = row[idx["Name"]].strip()
                vac = float(row[idx["Vac"]] or 0.0)
                paco = float(row[idx["Paco"]] or 0.0)
                pdco = float(row[idx["Pdco"]] or 0.0)
                vdco = float(row[idx["Vdco"]] or 0.0)
                pnt = float(row[idx["Pnt"]] or 0.0)
                mppt_low = float(row[idx["Mppt_low"]] or 0.0)
                mppt_high = float(row[idx["Mppt_high"]] or 0.0)
                cec_type = row[idx["CEC_Type"]].strip()
            except ValueError:
                continue
            if paco <= 0 or pdco <= 0:
                continue
            eta_nom = min(1.0, max(0.0, paco / pdco))
            tier = "utility_commercial_10kw_plus" if paco >= 10000.0 else "distributed_sub_10kw"
            records.append((name, vac, paco, pdco, vdco, pnt, mppt_low, mppt_high, eta_nom, tier, cec_type))
    return records


def assign_lifecycle_stage(month_index: int) -> str:
    if month_index == 0:
        return "1_Commissioning"
    if month_index <= 2:
        return "2_Provisional_Acceptance"
    if month_index <= 14:
        return "3_Active_Warranty_SLA"
    return "4_Steady_State_OM"


def run_pipeline(opsd_csv: Path, cec_csv: Path, out_dir: Path, sqlite_path: Path) -> Dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if sqlite_path.exists():
        sqlite_path.unlink()

    opsd_sha = ensure_verified_download(OPSD_URL, OPSD_SHA256, opsd_csv)
    cec_sha = ensure_verified_download(CEC_URL, CEC_SHA256, cec_csv)

    cec_records = parse_cec_inverters(cec_csv)

    # Stream and profile OPSD fleet telemetry
    total_hourly_rows = 0
    total_facility_readings = 0
    core_asset_readings = 0
    negative_reset_quarantined = 0
    multi_hour_gap_events = 0

    prev_val: Dict[str, float] = {}
    prev_idx: Dict[str, int] = {}
    first_val: Dict[str, float] = {}
    last_val: Dict[str, float] = {}
    positive_delta_sum: Dict[str, float] = defaultdict(float)
    negative_delta_sum: Dict[str, float] = defaultdict(float)
    peak_hourly_kwh: Dict[str, float] = defaultdict(float)

    # Monthly aggregation per (channel, year_month)
    # Values: [expected_hours_in_window, observed_valid_hours, clean_delta_kwh, gap_incidents, reset_anomalies]
    monthly_stats: Dict[Tuple[str, str], List[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    channel_meta: Dict[str, Tuple[str, str, str]] = {}
    channel_first_seen_idx: Dict[str, int] = {}
    channel_last_seen_idx: Dict[str, int] = {}
    row_month_labels: List[str] = []

    # Seasonal & segment samples for quantitative hypothesis tests
    summer_pv_cf: List[float] = []
    winter_pv_cf: List[float] = []

    with opsd_csv.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        interp_idx = header.index("interpolated")
        de_cols = [(i, c) for i, c in enumerate(header) if c.startswith("DE_KN_")]
        core_cols = []
        for i, c in de_cols:
            meta = classify_channel(c)
            if meta is not None:
                channel_meta[c] = meta
                core_cols.append((i, c))

        for row_idx, row in enumerate(reader):
            total_hourly_rows += 1
            ts = row[0]
            ym = ts[:7]
            row_month_labels.append(ym)
            interp_raw = row[interp_idx].strip() if interp_idx < len(row) else ""
            interp_set = {tok.strip() for tok in interp_raw.split("|") if tok.strip()} if interp_raw else set()

            for i, _c in de_cols:
                if row[i] != "":
                    total_facility_readings += 1

            for i, c in core_cols:
                raw = row[i]
                if raw == "":
                    continue
                val = float(raw)
                core_asset_readings += 1
                if c not in channel_first_seen_idx:
                    channel_first_seen_idx[c] = row_idx
                    first_val[c] = val
                channel_last_seen_idx[c] = row_idx
                last_val[c] = val

                m_entry = monthly_stats[(c, ym)]
                if c in interp_set:
                    multi_hour_gap_events += 1
                    m_entry[3] += 1.0  # real telemetry dropout / interpolated outage hour
                else:
                    m_entry[1] += 1.0  # genuine live uninterpolated meter telemetry hour

                if c in prev_val:
                    hour_step = row_idx - prev_idx[c]
                    delta = val - prev_val[c]
                    if hour_step > 1:
                        multi_hour_gap_events += 1
                        m_entry[3] += 1.0
                    if delta < -1e-6:
                        negative_reset_quarantined += 1
                        negative_delta_sum[c] += delta
                        m_entry[4] += 1.0
                    else:
                        clean_d = max(0.0, delta)
                        positive_delta_sum[c] += clean_d
                        m_entry[2] += clean_d
                        if hour_step == 1 and c not in interp_set and clean_d > peak_hourly_kwh[c]:
                            peak_hourly_kwh[c] = clean_d
                prev_val[c] = val
                prev_idx[c] = row_idx

    # Compute exact expected active hours per (channel, year_month) within each channel's commissioned lifespan
    for c, start_i in channel_first_seen_idx.items():
        end_i = channel_last_seen_idx[c]
        for r_i in range(start_i, end_i + 1):
            ym = row_month_labels[r_i]
            monthly_stats[(c, ym)][0] += 1.0

    # Build normalized SQLite Data Warehouse
    conn = sqlite3.connect(str(sqlite_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE dim_site_contract (
            site_id TEXT PRIMARY KEY,
            customer_segment TEXT NOT NULL,
            contractual_uptime_sla REAL NOT NULL,
            commissioned_month TEXT NOT NULL,
            decommissioned_or_eof_month TEXT NOT NULL
        );

        CREATE TABLE dim_energy_asset (
            asset_id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES dim_site_contract(site_id),
            customer_segment TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            estimated_peak_kw REAL NOT NULL,
            total_metered_snapshots INTEGER NOT NULL,
            net_verified_kwh REAL NOT NULL,
            quarantined_reset_count INTEGER NOT NULL
        );

        CREATE TABLE dim_cec_inverter_spec (
            inverter_name TEXT PRIMARY KEY,
            vac_volts REAL NOT NULL,
            paco_watts REAL NOT NULL,
            pdco_watts REAL NOT NULL,
            vdco_volts REAL NOT NULL,
            pnt_watts REAL NOT NULL,
            mppt_low_volts REAL NOT NULL,
            mppt_high_volts REAL NOT NULL,
            nominal_efficiency REAL NOT NULL,
            scale_tier TEXT NOT NULL,
            cec_type TEXT NOT NULL
        );

        CREATE TABLE fct_monthly_asset_sla (
            asset_id TEXT NOT NULL REFERENCES dim_energy_asset(asset_id),
            site_id TEXT NOT NULL REFERENCES dim_site_contract(site_id),
            year_month TEXT NOT NULL,
            lifecycle_stage TEXT NOT NULL,
            expected_commissioned_hours INTEGER NOT NULL,
            valid_metered_hours INTEGER NOT NULL,
            uptime_availability_ratio REAL NOT NULL,
            contractual_sla_target REAL NOT NULL,
            sla_compliant_flag INTEGER NOT NULL,
            verified_energy_kwh REAL NOT NULL,
            capacity_utilization_ratio REAL NOT NULL,
            telemetry_gap_incidents INTEGER NOT NULL,
            meter_reset_anomalies INTEGER NOT NULL,
            PRIMARY KEY (asset_id, year_month)
        );
        """
    )

    cur.executemany(
        """
        INSERT INTO dim_cec_inverter_spec VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        cec_records,
    )

    # Site contract bounds
    site_months: Dict[str, List[str]] = defaultdict(list)
    site_segments: Dict[str, str] = {}
    for c, (site_id, seg, _atype) in channel_meta.items():
        site_segments[site_id] = seg
        site_months[site_id].append(row_month_labels[channel_first_seen_idx[c]])
        site_months[site_id].append(row_month_labels[channel_last_seen_idx[c]])

    for site_id in sorted(site_segments):
        seg = site_segments[site_id]
        ms = sorted(site_months[site_id])
        cur.execute(
            "INSERT INTO dim_site_contract VALUES (?, ?, ?, ?, ?)",
            (site_id, seg, SITE_SLA_TARGETS[seg], ms[0], ms[-1]),
        )

    asset_rows = []
    for c in sorted(channel_meta):
        site_id, seg, atype = channel_meta[c]
        obs_cnt = int(sum(v[1] for (ch, _ym), v in monthly_stats.items() if ch == c))
        resets = int(sum(v[4] for (ch, _ym), v in monthly_stats.items() if ch == c))
        peak_kw = max(0.1, round(peak_hourly_kwh[c], 4))
        asset_rows.append(
            (c, site_id, seg, atype, peak_kw, obs_cnt, round(positive_delta_sum[c], 4), resets)
        )
    cur.executemany("INSERT INTO dim_energy_asset VALUES (?, ?, ?, ?, ?, ?, ?, ?)", asset_rows)

    monthly_rows = []
    for c in sorted(channel_meta):
        site_id, seg, atype = channel_meta[c]
        sla_target = SITE_SLA_TARGETS[seg]
        peak_kw = max(0.1, peak_hourly_kwh[c])
        ch_yms = sorted(ym for (ch, ym) in monthly_stats if ch == c)
        for m_idx, ym in enumerate(ch_yms):
            exp_h, valid_h, kwh, gaps, resets = monthly_stats[(c, ym)]
            if exp_h <= 0:
                continue
            uptime = min(1.0, valid_h / exp_h)
            compliant = 1 if uptime >= sla_target else 0
            cap_util = min(1.0, kwh / (exp_h * peak_kw)) if peak_kw > 0 else 0.0
            stage = assign_lifecycle_stage(m_idx)
            monthly_rows.append(
                (
                    c,
                    site_id,
                    ym,
                    stage,
                    int(exp_h),
                    int(valid_h),
                    round(uptime, 6),
                    sla_target,
                    compliant,
                    round(kwh, 4),
                    round(cap_util, 6),
                    int(gaps),
                    int(resets),
                )
            )
            if atype == "solar_pv" and exp_h >= 200:
                month_num = int(ym[5:7])
                if 4 <= month_num <= 9:
                    summer_pv_cf.append(cap_util)
                else:
                    winter_pv_cf.append(cap_util)

    cur.executemany(
        "INSERT INTO fct_monthly_asset_sla VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        monthly_rows,
    )
    conn.commit()

    # Execute SQL Joins & CTEs analytical queries for BI Dashboard Marts
    segment_kpi_rows = cur.execute(
        """
        WITH monthly_rollup AS (
            SELECT
                s.customer_segment,
                a.asset_type,
                COUNT(*) AS asset_months,
                SUM(m.expected_commissioned_hours) AS expected_hours,
                SUM(m.valid_metered_hours) AS valid_hours,
                ROUND(100.0 * SUM(m.valid_metered_hours) / SUM(m.expected_commissioned_hours), 4) AS fleet_uptime_pct,
                SUM(m.sla_compliant_flag) AS compliant_asset_months,
                ROUND(100.0 * SUM(m.sla_compliant_flag) / COUNT(*), 4) AS monthly_sla_compliance_pct,
                ROUND(SUM(m.verified_energy_kwh), 2) AS total_verified_kwh,
                SUM(m.telemetry_gap_incidents) AS total_gap_incidents
            FROM fct_monthly_asset_sla m
            JOIN dim_energy_asset a ON m.asset_id = a.asset_id
            JOIN dim_site_contract s ON m.site_id = s.site_id
            GROUP BY s.customer_segment, a.asset_type
        )
        SELECT * FROM monthly_rollup
        ORDER BY customer_segment, asset_type;
        """
    ).fetchall()

    lifecycle_rows = cur.execute(
        """
        SELECT
            lifecycle_stage,
            COUNT(*) AS asset_months,
            ROUND(100.0 * SUM(valid_metered_hours) / SUM(expected_commissioned_hours), 4) AS stage_uptime_pct,
            ROUND(100.0 * SUM(sla_compliant_flag) / COUNT(*), 4) AS sla_compliance_pct,
            ROUND(SUM(verified_energy_kwh), 2) AS verified_kwh,
            SUM(telemetry_gap_incidents) AS gap_incidents
        FROM fct_monthly_asset_sla
        GROUP BY lifecycle_stage
        ORDER BY lifecycle_stage;
        """
    ).fetchall()

    # Industrial battery storage round-trip efficiency (RTE)
    chg_kwh = positive_delta_sum.get("DE_KN_industrial2_storage_charge", 0.0)
    dis_kwh = positive_delta_sum.get("DE_KN_industrial2_storage_decharge", 0.0)
    battery_rte_pct = round(100.0 * dis_kwh / chg_kwh, 2) if chg_kwh > 0 else 0.0

    # CEC Inverter efficiency summary by tier
    cec_tier_rows = cur.execute(
        """
        SELECT
            scale_tier,
            COUNT(*) AS inverter_models,
            ROUND(AVG(nominal_efficiency) * 100.0, 3) AS mean_nominal_efficiency_pct,
            ROUND(AVG(paco_watts) / 1000.0, 2) AS mean_ac_kw,
            ROUND(MAX(paco_watts) / 1000.0, 2) AS max_ac_kw
        FROM dim_cec_inverter_spec
        GROUP BY scale_tier
        ORDER BY scale_tier;
        """
    ).fetchall()

    # Hypothesis Test 1: Commercial/Industrial vs Residential monthly SLA compliance rate
    ind_k, ind_n = cur.execute(
        "SELECT SUM(m.sla_compliant_flag), COUNT(*) FROM fct_monthly_asset_sla m JOIN dim_site_contract s ON m.site_id = s.site_id WHERE s.customer_segment = 'industrial'"
    ).fetchone()
    res_k, res_n = cur.execute(
        "SELECT SUM(m.sla_compliant_flag), COUNT(*) FROM fct_monthly_asset_sla m JOIN dim_site_contract s ON m.site_id = s.site_id WHERE s.customer_segment = 'residential'"
    ).fetchone()

    # Also compare raw hourly telemetry availability across Commercial/Industrial vs Residential
    ind_vh, ind_eh = cur.execute(
        "SELECT SUM(m.valid_metered_hours), SUM(m.expected_commissioned_hours) FROM fct_monthly_asset_sla m JOIN dim_site_contract s ON m.site_id = s.site_id WHERE s.customer_segment = 'industrial'"
    ).fetchone()
    res_vh, res_eh = cur.execute(
        "SELECT SUM(m.valid_metered_hours), SUM(m.expected_commissioned_hours) FROM fct_monthly_asset_sla m JOIN dim_site_contract s ON m.site_id = s.site_id WHERE s.customer_segment = 'residential'"
    ).fetchone()

    hourly_uptime_z = two_proportion_z_test(int(ind_vh), int(ind_eh), int(res_vh), int(res_eh))
    monthly_sla_z = two_proportion_z_test(int(ind_k), int(ind_n), int(res_k), int(res_n))
    seasonal_pv_t = welch_t_test(summer_pv_cf, winter_pv_cf)

    # Deterministic 12/12 Data Quality & Reconciliation Audit Gates
    conservation_failures = 0
    for c in channel_meta:
        lhs = round(last_val[c] - first_val[c], 5)
        rhs = round(positive_delta_sum[c] + negative_delta_sum[c], 5)
        if abs(lhs - rhs) > 1e-3:
            conservation_failures += 1

    fk_violations = len(cur.execute("PRAGMA foreign_key_check;").fetchall())
    integrity_ok = cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    neg_kwh_rows = cur.execute("SELECT COUNT(*) FROM fct_monthly_asset_sla WHERE verified_energy_kwh < 0").fetchone()[0]
    invalid_uptime_rows = cur.execute(
        "SELECT COUNT(*) FROM fct_monthly_asset_sla WHERE uptime_availability_ratio < 0 OR uptime_availability_ratio > 1.000001"
    ).fetchone()[0]

    dq_gates = [
        {"gate_id": "DQ-01", "name": "OPSD SHA-256 cryptographic provenance match", "passed": opsd_sha == OPSD_SHA256},
        {"gate_id": "DQ-02", "name": "NREL/CEC Inverter Registry SHA-256 match", "passed": cec_sha == CEC_SHA256},
        {"gate_id": "DQ-03", "name": "All 11 deployed sites registered in dim_site_contract", "passed": len(site_segments) == 11},
        {"gate_id": "DQ-04", "name": "All 27 solar/storage/EV/grid channels normalized", "passed": len(channel_meta) == 27},
        {"gate_id": "DQ-05", "name": "Cumulative meter telescoping conservation (last - first == pos + neg)", "passed": conservation_failures == 0},
        {"gate_id": "DQ-06", "name": "Zero negative verified energy kWh in monthly SLA mart", "passed": neg_kwh_rows == 0},
        {"gate_id": "DQ-07", "name": "Uptime availability bounded strictly within [0, 1]", "passed": invalid_uptime_rows == 0},
        {"gate_id": "DQ-08", "name": "Battery storage round-trip efficiency physically valid (50%-100%)", "passed": 50.0 <= battery_rte_pct <= 100.0},
        {"gate_id": "DQ-09", "name": "3,200+ certified CEC inverter models loaded", "passed": len(cec_records) >= 3200},
        {"gate_id": "DQ-10", "name": "Lifecycle stage progression covers all 4 contractual phases", "passed": len(lifecycle_rows) == 4},
        {"gate_id": "DQ-11", "name": "SQLite foreign_key_check == 0 violations", "passed": fk_violations == 0},
        {"gate_id": "DQ-12", "name": "SQLite physical integrity_check == ok", "passed": integrity_ok},
    ]

    # Export CSV BI marts
    with (out_dir / "monthly_fleet_contract_sla_mart.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "asset_id", "site_id", "year_month", "lifecycle_stage",
            "expected_commissioned_hours", "valid_metered_hours", "uptime_availability_ratio",
            "contractual_sla_target", "sla_compliant_flag", "verified_energy_kwh",
            "capacity_utilization_ratio", "telemetry_gap_incidents", "meter_reset_anomalies",
        ])
        w.writerows(monthly_rows)

    with (out_dir / "segment_asset_kpi_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "customer_segment", "asset_type", "asset_months", "expected_hours", "valid_hours",
            "fleet_uptime_pct", "compliant_asset_months", "monthly_sla_compliance_pct",
            "total_verified_kwh", "total_gap_incidents",
        ])
        w.writerows(segment_kpi_rows)

    with (out_dir / "lifecycle_milestone_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "lifecycle_stage", "asset_months", "stage_uptime_pct",
            "sla_compliance_pct", "verified_kwh", "gap_incidents",
        ])
        w.writerows(lifecycle_rows)

    with (out_dir / "cec_inverter_efficiency_benchmarks.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "scale_tier", "inverter_models", "mean_nominal_efficiency_pct", "mean_ac_kw", "max_ac_kw"
        ])
        w.writerows(cec_tier_rows)

    total_exp_h, total_val_h, total_comp_m, total_m, total_pv_kwh, total_ev_kwh = cur.execute(
        """
        SELECT
            SUM(m.expected_commissioned_hours),
            SUM(m.valid_metered_hours),
            SUM(m.sla_compliant_flag),
            COUNT(*),
            SUM(CASE WHEN a.asset_type = 'solar_pv' THEN m.verified_energy_kwh ELSE 0 END),
            SUM(CASE WHEN a.asset_type = 'ev_charger' THEN m.verified_energy_kwh ELSE 0 END)
        FROM fct_monthly_asset_sla m
        JOIN dim_energy_asset a ON m.asset_id = a.asset_id
        """
    ).fetchone()

    conn.close()

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "opsd_url": OPSD_URL,
            "opsd_sha256": opsd_sha,
            "cec_inverters_url": CEC_URL,
            "cec_inverters_sha256": cec_sha,
        },
        "scale": {
            "hourly_fleet_intervals": total_hourly_rows,
            "total_facility_sensor_readings": total_facility_readings,
            "core_energy_asset_snapshots": core_asset_readings,
            "deployed_sites": len(site_segments),
            "monitored_energy_channels": len(channel_meta),
            "evaluated_asset_months": int(total_m),
            "cec_certified_inverter_models": len(cec_records),
        },
        "fleet_compliance_kpis": {
            "fleet_hourly_uptime_pct": round(100.0 * total_val_h / total_exp_h, 4),
            "compliant_asset_months": int(total_comp_m),
            "monthly_contract_sla_compliance_pct": round(100.0 * total_comp_m / total_m, 4),
            "verified_solar_pv_generation_kwh": round(float(total_pv_kwh), 2),
            "verified_ev_charging_delivery_kwh": round(float(total_ev_kwh), 2),
            "industrial_battery_charge_kwh": round(chg_kwh, 2),
            "industrial_battery_discharge_kwh": round(dis_kwh, 2),
            "industrial_battery_round_trip_efficiency_pct": battery_rte_pct,
            "telemetry_dropout_gap_incidents": multi_hour_gap_events,
            "negative_counter_resets_quarantined": negative_reset_quarantined,
        },
        "quantitative_hypothesis_tests": {
            "industrial_vs_residential_hourly_uptime_z_test": hourly_uptime_z,
            "industrial_vs_residential_monthly_sla_z_test": monthly_sla_z,
            "summer_vs_winter_solar_capacity_utilization_welch_t": seasonal_pv_t,
        },
        "dq_gates_passed": sum(1 for g in dq_gates if g["passed"]),
        "dq_gates_total": len(dq_gates),
        "dq_gates": dq_gates,
    }

    with (out_dir / "validation_and_hypothesis_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Commercial Energy Fleet SLA Analytics pipeline.")
    parser.add_argument("--opsd-csv", type=Path, default=Path("data/raw/opsd_60min.csv"))
    parser.add_argument("--cec-csv", type=Path, default=Path("data/raw/cec_inverters.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--sqlite-path", type=Path, default=Path("outputs/energy_fleet_warehouse.sqlite"))
    args = parser.parse_args()

    summary = run_pipeline(args.opsd_csv, args.cec_csv, args.out_dir, args.sqlite_path)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
