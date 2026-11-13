import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from ht32_runtime.artifacts import ArtifactError, collect_build_artifacts, list_uvprojx, list_uvprojx_targets


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixture = Path(__file__).parent / "fixtures" / "project" / "project" / "MDK_ARMv5" / "demo.uvprojx"
        self.project_dir = self.root / "project" / "MDK_ARMv5"
        self.project_dir.mkdir(parents=True)
        self.uvprojx = self.project_dir / "demo.uvprojx"
        self.uvprojx.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_lists_uvprojx_and_targets(self):
        self.assertEqual(list_uvprojx(self.root), [self.uvprojx.resolve()])
        self.assertEqual(list_uvprojx_targets(self.uvprojx), ["Demo", "Other"])

    def test_list_targets_returns_empty_only_for_valid_xml_without_targets(self):
        empty = self.root / "empty.uvprojx"
        empty.write_text("<Project />", encoding="utf-8")
        self.assertEqual(list_uvprojx_targets(empty), [])

    def test_list_targets_reports_missing_unreadable_and_malformed_files(self):
        with self.assertRaisesRegex(ArtifactError, "不存在"):
            list_uvprojx_targets(self.root / "missing.uvprojx")

        malformed = self.root / "malformed.uvprojx"
        malformed.write_text("<Project>", encoding="utf-8")
        with self.assertRaisesRegex(ArtifactError, "XML"):
            list_uvprojx_targets(malformed)

        with patch("xml.etree.ElementTree.parse", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(ArtifactError, "无法读取"):
                list_uvprojx_targets(self.uvprojx)

    def test_collects_only_existing_target_artifacts_as_absolute_paths(self):
        output = self.project_dir / "Objects"
        output.mkdir()
        for name in ("demo.axf", "demo.hex", "demo.bin"):
            (output / name).write_text(name, encoding="utf-8")

        artifacts = collect_build_artifacts(self.uvprojx, "Demo")

        self.assertEqual(set(artifacts), {"axf", "elf", "hex", "bin"})
        self.assertEqual(artifacts["elf"], [])
        self.assertEqual(artifacts["axf"], [str((output / "demo.axf").resolve())])
        self.assertTrue(all(Path(path).is_absolute() for paths in artifacts.values() for path in paths))

    def test_missing_artifacts_and_unknown_target_are_not_guessed(self):
        expected = {"axf": [], "elf": [], "hex": [], "bin": []}
        self.assertEqual(collect_build_artifacts(self.uvprojx, "Other"), expected)
        self.assertEqual(collect_build_artifacts(self.uvprojx, "Missing"), expected)


if __name__ == "__main__":
    unittest.main()
