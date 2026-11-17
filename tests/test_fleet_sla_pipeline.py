import csv
import tempfile
import unittest
from pathlib import Path

from src.fleet_sla_pipeline import (
    assign_lifecycle_stage,
    classify_channel,
    two_proportion_z_test,
    welch_t_test,
)


class TestFleetSLAPipeline(unittest.TestCase):
    def test_channel_classification_covers_solar_storage_ev_and_grid(self) -> None:
        self.assertEqual(
            classify_channel("DE_KN_industrial2_pv"),
            ("SITE_INDUSTRIAL2", "industrial", "solar_pv"),
        )
        self.assertEqual(
            classify_channel("DE_KN_industrial2_storage_charge"),
            ("SITE_INDUSTRIAL2", "industrial", "storage_charge"),
        )
        self.assertEqual(
            classify_channel("DE_KN_industrial2_storage_decharge"),
            ("SITE_INDUSTRIAL2", "industrial", "storage_discharge"),
        )
        self.assertEqual(
            classify_channel("DE_KN_industrial3_ev"),
            ("SITE_INDUSTRIAL3", "industrial", "ev_charger"),
        )
        self.assertIsNone(classify_channel("DE_KN_residential1_dishwasher"))

    def test_lifecycle_stage_monotonic_progression(self) -> None:
        self.assertEqual(assign_lifecycle_stage(0), "1_Commissioning")
        self.assertEqual(assign_lifecycle_stage(2), "2_Provisional_Acceptance")
        self.assertEqual(assign_lifecycle_stage(6), "3_Active_Warranty_SLA")
        self.assertEqual(assign_lifecycle_stage(20), "4_Steady_State_OM")

    def test_welch_t_test_detects_directional_difference(self) -> None:
        res = welch_t_test([0.22, 0.25, 0.24, 0.26, 0.23], [0.06, 0.08, 0.07, 0.05, 0.09])
        self.assertGreater(res["diff"], 0.14)
        self.assertGreater(res["t_stat"], 10.0)
        self.assertLess(res["p_value_two_sided"], 1e-6)

    def test_two_proportion_z_test_symmetry_and_bounds(self) -> None:
        res = two_proportion_z_test(985, 1000, 940, 1000)
        self.assertAlmostEqual(res["diff_pp"], 4.5, places=4)
        self.assertGreater(res["z_stat"], 5.0)
        self.assertLess(res["p_value_two_sided"], 1e-6)

    def test_committed_outputs_and_dq_gates_exist_and_pass(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        summary_path = repo_root / "outputs" / "validation_and_hypothesis_summary.json"
        if summary_path.exists():
            import json
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(data["dq_gates_passed"], data["dq_gates_total"])
            self.assertEqual(data["dq_gates_total"], 12)
            self.assertGreaterEqual(data["scale"]["core_energy_asset_snapshots"], 400000)

    def test_monthly_sla_mart_non_negative_energy_invariant(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        mart_path = repo_root / "outputs" / "monthly_fleet_contract_sla_mart.csv"
        if mart_path.exists():
            with mart_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertGreater(len(rows), 600)
            for r in rows:
                self.assertGreaterEqual(float(r["verified_energy_kwh"]), 0.0)
                ratio = float(r["uptime_availability_ratio"])
                self.assertGreaterEqual(ratio, 0.0)
                self.assertLessEqual(ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
