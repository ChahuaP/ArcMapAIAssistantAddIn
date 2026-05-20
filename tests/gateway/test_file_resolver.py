import pathlib
import tempfile
import unittest

from gateway_py3.file_resolver import FileResolver


class FileResolverTests(unittest.TestCase):
    def test_full_path_resolves_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "nanjing.shp"
            path.write_text("", encoding="utf-8")
            resolver = FileResolver()
            result = resolver.resolve_command("帮我打开 %s" % path)

        self.assertEqual(result.status, "resolved")
        self.assertTrue(result.path.endswith("nanjing.shp"))
        self.assertEqual(result.workflow()["steps"][0]["operation"], "layer.add_layer")

    def test_drive_root_is_too_broad_and_asks_for_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Data").mkdir()
            (root / "Projects").mkdir()
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("帮我打开 D 盘下的 nanjing.shp")

        self.assertEqual(result.status, "clarify")
        self.assertIn("范围太大", result.summary)
        self.assertIn("Data", result.summary)

    def test_polite_prefix_before_drive_is_not_directory_fragment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Data").mkdir()
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("请你打开d盘下的nanjing.shp")

        self.assertEqual(result.status, "clarify")
        self.assertIn("范围太大", result.summary)
        self.assertNotIn("你", result.summary)
        self.assertNotIn("没有找到目录", result.summary)

    def test_specific_directory_searches_limited_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            shp_dir = root / "Data" / "shapefile"
            shp_dir.mkdir(parents=True)
            target = shp_dir / "nanjing.shp"
            target.write_text("", encoding="utf-8")
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("帮我打开 D 盘 Data 文件夹 shapefile 文件夹下的 nanjing.shp")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(pathlib.Path(result.path), target)

    def test_later_output_drive_does_not_override_input_drive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            shp_dir = root / "Data"
            shp_dir.mkdir()
            target = shp_dir / "nanjing.shp"
            target.write_text("", encoding="utf-8")
            resolver = FileResolver(drive_roots={"D": root, "E": root / "Output"})
            result = resolver.resolve_command("请你打开D盘Data文件夹下的nanjing.shp，输出到E盘")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(pathlib.Path(result.path), target)

    def test_clarification_answer_is_used_as_directory_fragment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            data_dir = root / "Data"
            data_dir.mkdir()
            target = data_dir / "nanjing.shp"
            target.write_text("", encoding="utf-8")
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("帮我打开d盘的nanjing.shp\n用户补充：Data文件夹下")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(pathlib.Path(result.path), target)

    def test_clarification_marker_does_not_become_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Data").mkdir()
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("帮我打开d盘的nanjing.shp\n用户补充：Data文件夹下")

        self.assertEqual(result.status, "clarify")
        self.assertNotIn("用户补充", result.summary)

    def test_missing_file_lists_next_level_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            data_dir = root / "Data"
            (data_dir / "shapefile").mkdir(parents=True)
            (data_dir / "boundary").mkdir()
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("帮我打开d盘的nanjing.shp\n用户补充：Data文件夹下")

        self.assertEqual(result.status, "clarify")
        self.assertIn("下一层目录有", result.summary)
        self.assertIn("shapefile", result.summary)

    def test_folder_path_adds_all_shapefiles(self):
        with tempfile.TemporaryDirectory() as directory:
            shp_dir = pathlib.Path(directory) / "shapefile"
            shp_dir.mkdir()
            (shp_dir / "nanjing.shp").write_text("", encoding="utf-8")
            (shp_dir / "roads.shp").write_text("", encoding="utf-8")
            (shp_dir / "notes.txt").write_text("", encoding="utf-8")
            resolver = FileResolver()
            result = resolver.resolve_command("帮我添加 %s 文件夹下的两个shapefile" % shp_dir)

        workflow = result.workflow()
        self.assertEqual(result.status, "resolved")
        self.assertEqual(len(workflow["steps"]), 2)
        self.assertEqual(workflow["steps"][0]["operation"], "layer.add_layer")
        self.assertTrue(workflow["steps"][0]["arguments"]["path"].endswith("nanjing.shp"))
        self.assertTrue(workflow["steps"][1]["arguments"]["path"].endswith("roads.shp"))

    def test_parse_command_returns_clean_text_without_resolved_folder_path(self):
        with tempfile.TemporaryDirectory() as directory:
            shp_dir = pathlib.Path(directory) / "叠加分析" / "相交"
            shp_dir.mkdir(parents=True)
            (shp_dir / "p1.shp").write_text("", encoding="utf-8")
            (shp_dir / "p2.shp").write_text("", encoding="utf-8")
            resolver = FileResolver()
            parsed = resolver.parse_command("打开%s下所有shp，然后执行p1和p2的相交" % shp_dir)

        self.assertEqual(parsed.file_resolution.status, "resolved")
        self.assertNotIn(str(shp_dir), parsed.clean_text)
        self.assertIn("执行p1和p2的相交", parsed.clean_text)

    def test_drive_fragment_adds_all_shapefiles_in_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            shp_dir = root / "Data"
            shp_dir.mkdir()
            (shp_dir / "nanjing.shp").write_text("", encoding="utf-8")
            (shp_dir / "roads.shp").write_text("", encoding="utf-8")
            resolver = FileResolver(drive_roots={"D": root})
            result = resolver.resolve_command("帮我添加 D 盘 Data 文件夹下的两个shp")

        workflow = result.workflow()
        self.assertEqual(result.status, "resolved")
        self.assertEqual(len(workflow["steps"]), 2)

    def test_folder_with_many_shapefiles_asks_for_narrower_input(self):
        with tempfile.TemporaryDirectory() as directory:
            shp_dir = pathlib.Path(directory) / "shapefile"
            shp_dir.mkdir()
            for index in range(13):
                (shp_dir / ("layer_%02d.shp" % index)).write_text("", encoding="utf-8")
            resolver = FileResolver()
            result = resolver.resolve_command("帮我添加 %s 文件夹下的shp" % shp_dir)

        self.assertEqual(result.status, "clarify")
        self.assertIn("数量太多", result.summary)

    def test_missing_folder_path_does_not_fall_back_to_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "roads.shp").write_text("", encoding="utf-8")
            missing_dir = root / "missing"
            resolver = FileResolver()
            result = resolver.resolve_command("帮我添加 %s 文件夹下的两个shp" % missing_dir)

        self.assertEqual(result.status, "clarify")
        self.assertIn("没有找到目录", result.summary)
        self.assertIn("missing", result.summary)


if __name__ == "__main__":
    unittest.main()
