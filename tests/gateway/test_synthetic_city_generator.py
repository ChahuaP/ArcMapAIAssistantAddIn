import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments" / "synthetic_city" / "generate_dataset.py"
SPEC = importlib.util.spec_from_file_location("synthetic_city_generator", MODULE_PATH)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class SyntheticCityGeneratorTests(unittest.TestCase):
    def test_flood_service_truth_uses_closed_metric_distance_not_buffer_tessellation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dataset"
            generator.generate(output, seed=20260905)
            flood_zones = gpd.read_file(output / "source" / "flood_zones.shp")
            communities = gpd.read_file(output / "source" / "communities.shp")
            shelters = gpd.read_file(output / "source" / "shelters.shp")
            expected_ids = json.loads(
                (output / "truth" / "expected_ids.json").read_text(encoding="utf-8")
            )

        high_flood = unary_union(
            flood_zones.loc[flood_zones["RISK_LVL"] >= 4].geometry
        )
        affected = communities.loc[
            communities.geometry.within(high_flood)
            & (communities["POP"] >= 800)
            & (communities["VULN_LVL"] == "HIGH")
        ]
        affected_union = unary_union(affected.geometry)
        exact = shelters.loc[
            (shelters.geometry.distance(affected_union) <= 2000)
            & (shelters["STATUS"] == "OPEN"),
            "SHLT_ID",
        ].tolist()

        self.assertIn("S05", exact)
        self.assertEqual(expected_ids["flood_available_shelters"], exact)

    def test_priority_roads_are_derived_only_from_risk_schools(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dataset"
            generator.generate(output)
            risk_schools = gpd.read_file(output / "truth" / "road_risk_schools.shp")
            priority_roads = gpd.read_file(output / "truth" / "road_priority_roads.shp")

        self.assertFalse(risk_schools.empty)
        self.assertFalse(priority_roads.empty)
        self.assertTrue(
            generator.intersects_closed_metric_buffer(
                priority_roads, risk_schools, 500,
            ).all()
        )

    def test_seed_is_public_reproducible_dataset_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            different = root / "different"
            generator.generate(first, seed=101)
            generator.generate(second, seed=101)
            generator.generate(different, seed=102)

            first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
            second_manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
            different_manifest = json.loads((different / "manifest.json").read_text(encoding="utf-8"))
            first_truth = json.loads((first / "truth" / "expected_ids.json").read_text(encoding="utf-8"))
            second_truth = json.loads((second / "truth" / "expected_ids.json").read_text(encoding="utf-8"))
            different_truth = json.loads((different / "truth" / "expected_ids.json").read_text(encoding="utf-8"))

        self.assertEqual(first_manifest["seed"], 101)
        self.assertEqual(second_manifest["seed"], 101)
        self.assertEqual(different_manifest["seed"], 102)
        self.assertEqual(first_truth, second_truth)
        self.assertNotEqual(first_truth, different_truth)

    def test_scale_and_city_bounds_are_public_dataset_inputs(self):
        shifted_bounds = (770000.0, 3638000.0, 790000.0, 3658000.0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            standard = root / "standard"
            scaled = root / "scaled"
            generator.generate(standard, seed=201, scale=1.0)
            generator.generate(
                scaled,
                seed=201,
                scale=1.5,
                city_bounds=shifted_bounds,
            )
            standard_manifest = json.loads(
                (standard / "manifest.json").read_text(encoding="utf-8")
            )
            scaled_manifest = json.loads(
                (scaled / "manifest.json").read_text(encoding="utf-8")
            )
            districts = gpd.read_file(scaled / "source" / "districts.shp")

        self.assertEqual(scaled_manifest["data_scale"], 1.5)
        self.assertEqual(scaled_manifest["city_bounds"], list(shifted_bounds))
        self.assertGreater(
            scaled_manifest["source_feature_total"],
            standard_manifest["source_feature_total"],
        )
        self.assertEqual(tuple(districts.total_bounds), shifted_bounds)

    def test_invalid_generation_inputs_leave_no_partial_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "invalid"
            with self.assertRaises(ValueError):
                generator.generate(output, scale=0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
