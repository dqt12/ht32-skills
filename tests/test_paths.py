import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ht32_runtime.models import OperationResult
from ht32_runtime.paths import (
    DATA_HOME_ENV,
    RuntimePaths,
    global_root_dir,
    normalize_project_path,
    project_id_for,
    writable_dir,
)


class PathContractTest(unittest.TestCase):
    def test_project_id_is_stable_for_windows_case_and_separator(self):
        self.assertEqual(project_id_for(r"D:\Work\Demo"), project_id_for("d:/work/demo"))
        self.assertEqual(len(project_id_for(r"D:\Work\Demo")), 16)

    def test_normalize_preserves_windows_drive_root(self):
        self.assertEqual(normalize_project_path("D:/"), "d:/")
        self.assertEqual(normalize_project_path("D:\\"), "d:/")
        # 不再断言 "D:"：它依赖进程的 per-drive cwd（D:\ 或别的目录），跨机器不确定。

    def test_normalize_preserves_unc_share_root(self):
        self.assertEqual(normalize_project_path("\\\\Server\\Share\\"), "//server/share/")
        self.assertEqual(normalize_project_path("//Server/Share/"), "//server/share/")

    def test_runtime_paths_keep_mutable_data_out_of_project(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            with patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
                paths = RuntimePaths.for_project(Path(project))
                paths.ensure_global_dirs()
            self.assertEqual(paths.project_config, Path(project).resolve() / ".ht32-skills" / "config.json")
            self.assertEqual(paths.global_root, Path(home) / ".ht32-skills")
            self.assertTrue(paths.state_file.is_relative_to(paths.global_root))
            self.assertTrue(paths.cache_dir.is_relative_to(paths.global_root))
            self.assertTrue(paths.logs_dir.is_relative_to(paths.global_root))
            self.assertEqual(set((Path(project) / ".ht32-skills").iterdir()), set())

    def test_global_data_home_can_be_redirected_for_sandboxed_hosts(self):
        """宿主工作区沙箱拦住 home 时，HT32_SKILLS_HOME 可把整份数据目录搬走。"""
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as alt:
            with patch.dict(os.environ, {"USERPROFILE": home, DATA_HOME_ENV: alt}, clear=False):
                paths = RuntimePaths.for_project(Path(home))
                paths.ensure_global_dirs()
                self.assertEqual(global_root_dir(), Path(alt))
                self.assertEqual(paths.global_root, Path(alt))
                self.assertTrue(paths.logs_dir.is_relative_to(Path(alt)))
                self.assertTrue(paths.machine_config.is_relative_to(Path(alt)))
                self.assertTrue(paths.logs_dir.is_dir())

    def test_writable_dir_reports_unusable_paths_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as root:
            blocker = Path(root) / "not-a-dir"
            blocker.write_text("x", encoding="utf-8")
            self.assertIsNone(writable_dir(blocker / "logs"))
            self.assertEqual(writable_dir(Path(root) / "logs"), Path(root) / "logs")
            self.assertEqual(list((Path(root) / "logs").iterdir()), [])   # 探测文件已清理

    def test_result_status_and_exit_code_contract(self):
        for status, exit_code in (("ok", 0), ("warn", 0), ("blocked", 2), ("error", 3)):
            result = OperationResult.start("flash", "jlink", "0123456789abcdef")
            self.assertEqual(result.finish(status).exit_code, exit_code)
            self.assertEqual(result.to_dict()["schema_version"], 1)
        with self.assertRaises(ValueError):
            OperationResult.start("flash", None, "id").finish("unknown")


if __name__ == "__main__":
    unittest.main()
