import pathlib
import tempfile
import unittest

from gateway_py3.file_resolver import FileResolver


class FileResolverTests(unittest.TestCase):
    def test_exact_file_path_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "nanjing.shp"
            path.write_text("", encoding="utf-8")
            result = FileResolver().resolve({"path": str(path)})

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.files[0]["layer_name"], "nanjing")

    def test_exact_folder_path_lists_requested_shapefiles(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "shapefile"
            folder.mkdir()
            (folder / "p1.shp").write_text("", encoding="utf-8")
            (folder / "p2.shp").write_text("", encoding="utf-8")
            (folder / "notes.txt").write_text("", encoding="utf-8")
            result = FileResolver().resolve({"path": str(folder), "extensions": ["shp"]})

        self.assertEqual(result.status, "resolved")
        self.assertEqual([item["layer_name"] for item in result.files], ["p1", "p2"])

    def test_drive_root_file_search_is_too_broad(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Data").mkdir()
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve({"drive": "D", "file_name": "nanjing.shp"})

        self.assertEqual(result.status, "clarify")
        self.assertIn("范围太大", result.question)
        self.assertIn("Data", result.question)

    def test_drive_directory_file_search_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            folder = root / "Data" / "shapefile"
            folder.mkdir(parents=True)
            target = folder / "nanjing.shp"
            target.write_text("", encoding="utf-8")
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve({
                "drive": "D",
                "directory_parts": ["Data", "shapefile"],
                "file_name": "nanjing.shp"
            })

        self.assertEqual(result.status, "resolved")
        self.assertEqual(pathlib.Path(result.path), target)

    def test_drive_directory_folder_listing_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            folder = root / "Data" / "overlay"
            folder.mkdir(parents=True)
            (folder / "p1.shp").write_text("", encoding="utf-8")
            (folder / "p2.shp").write_text("", encoding="utf-8")
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve({
                "drive": "D",
                "directory": "Data\\overlay",
                "extensions": ["shp"]
            })

        self.assertEqual(result.status, "resolved")
        self.assertEqual([item["layer_name"] for item in result.files], ["p1", "p2"])

    def test_missing_file_lists_next_level_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            data_dir = root / "Data"
            (data_dir / "shapefile").mkdir(parents=True)
            (data_dir / "boundary").mkdir()
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve({
                "drive": "D",
                "directory": "Data",
                "file_name": "nanjing.shp"
            })

        self.assertEqual(result.status, "clarify")
        self.assertIn("下一层目录有", result.question)
        self.assertIn("shapefile", result.question)

    def test_folder_with_many_files_asks_for_narrower_input(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "shapefile"
            folder.mkdir()
            for index in range(13):
                (folder / ("layer_%02d.shp" % index)).write_text("", encoding="utf-8")
            result = FileResolver().resolve({"folder_path": str(folder), "extensions": ["shp"]})

        self.assertEqual(result.status, "clarify")
        self.assertIn("数量太多", result.question)

    def test_missing_folder_path_does_not_fall_back_to_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "roads.shp").write_text("", encoding="utf-8")
            missing_dir = root / "missing"
            result = FileResolver().resolve({"folder_path": str(missing_dir), "extensions": ["shp"]})

        self.assertEqual(result.status, "clarify")
        self.assertIn("没有找到目录", result.question)
        self.assertIn("missing", result.question)


if __name__ == "__main__":
    unittest.main()
