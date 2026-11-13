import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ht32_runtime.config import (
    ConfigError,
    config_view,
    init_project_config,
    load_machine_config,
    load_project_config,
    resolve_debugger,
)
from dataclasses import FrozenInstanceError

from ht32_runtime.models import DebuggerConfig, DebuggerSettings


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "demo.uvprojx").write_text("<Project />", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_project(self, data):
        path = self.root / ".ht32-skills" / "config.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_machine(self, debugger):
        path = self.root / "machine.json"
        path.write_text(json.dumps({"schema_version": 1, "defaults": {"debugger": debugger}}), encoding="utf-8")
        return load_machine_config(path).debugger

    def valid_project(self):
        return {
            "schema_version": 1,
            "project": {"chip": "HT32F52352", "uvprojx": "demo.uvprojx", "target": "Demo"},
            "debugger": {"backend": "jlink", "probe_serial": "123", "interface": "swd", "speed_khz": 500},
        }

    def test_loads_complete_v1_project(self):
        self.write_project(self.valid_project())
        config = load_project_config(self.root)
        self.assertEqual(config.chip, "HT32F52352")
        self.assertEqual(config.uvprojx, (self.root / "demo.uvprojx").resolve())
        self.assertEqual(config.debugger.backend, "jlink")

    def test_project_rejects_boolean_schema_version(self):
        data = self.valid_project()
        data["schema_version"] = True
        self.write_project(data)

        with self.assertRaises(ConfigError):
            load_project_config(self.root)

    def test_machine_rejects_boolean_schema_version(self):
        path = self.root / "machine.json"
        path.write_text(json.dumps({"schema_version": True}), encoding="utf-8")

        with self.assertRaises(ConfigError):
            load_machine_config(path)

    def test_sparse_debugger_settings_are_frozen_and_use_field_access(self):
        data = self.valid_project()
        data["debugger"] = {"backend": "jlink"}
        self.write_project(data)

        project_debugger = load_project_config(self.root).debugger
        machine_debugger = self.write_machine({"interface": "jtag"})

        self.assertIsInstance(project_debugger, DebuggerSettings)
        self.assertEqual(project_debugger.backend, "jlink")
        self.assertIsNone(project_debugger.interface)
        self.assertEqual(machine_debugger.interface, "jtag")
        with self.assertRaises(FrozenInstanceError):
            project_debugger.backend = "keil"

    def test_project_without_debugger_inherits_machine_fields(self):
        data = self.valid_project()
        del data["debugger"]
        self.write_project(data)
        resolved = resolve_debugger(
            machine=self.write_machine({"backend": "jlink", "interface": "jtag", "speed_khz": 4000}),
            project=load_project_config(self.root).debugger,
            overrides={},
        )
        self.assertEqual(resolved, DebuggerConfig("jlink", None, "jtag", 4000))

    def test_project_partial_debugger_inherits_other_machine_fields(self):
        data = self.valid_project()
        data["debugger"] = {"backend": "keil"}
        self.write_project(data)
        resolved = resolve_debugger(
            machine=self.write_machine(
                {"backend": "jlink", "probe_serial": "123", "interface": "jtag", "speed_khz": 4000}
            ),
            project=load_project_config(self.root).debugger,
            overrides={},
        )
        self.assertEqual(resolved, DebuggerConfig("keil", "123", "jtag", 4000))

    def test_rejects_escape_unknown_fields_and_invalid_speed(self):
        cases = []
        escaped = self.valid_project(); escaped["project"]["uvprojx"] = "../outside.uvprojx"; cases.append(escaped)
        unknown = self.valid_project(); unknown["extra"] = True; cases.append(unknown)
        invalid = self.valid_project(); invalid["debugger"]["speed_khz"] = 0; cases.append(invalid)
        for data in cases:
            with self.subTest(data=data):
                self.write_project(data)
                with self.assertRaises(ConfigError):
                    load_project_config(self.root)

    def test_rejects_split_flash_or_debug_debugger(self):
        for section in ("flash", "debug"):
            data = self.valid_project(); data[section] = {"debugger": {"backend": "jlink"}}
            self.write_project(data)
            with self.assertRaisesRegex(ConfigError, "flash/debug 不得持久化独立 debugger"):
                load_project_config(self.root)

    def test_debugger_priority_and_overrides_do_not_persist(self):
        project_file = self.write_project(self.valid_project())
        resolved = resolve_debugger(
            machine=DebuggerConfig(backend="keil", speed_khz=1000),
            project=load_project_config(self.root).debugger,
            overrides={"speed_khz": 2000, "backend": None},
        )
        self.assertEqual(resolved, DebuggerConfig("jlink", "123", "swd", 2000))
        self.assertEqual(json.loads(project_file.read_text(encoding="utf-8"))["debugger"]["speed_khz"], 500)

    def test_machine_default_missing_and_config_view_priority(self):
        self.write_project(self.valid_project())
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"USERPROFILE": home}, clear=False):
                machine = load_machine_config()
                view = config_view(self.root, {"interface": "jtag"})
        self.assertEqual(machine.debugger, DebuggerSettings())
        self.assertEqual(view["debugger"]["backend"], "jlink")
        self.assertEqual(view["debugger"]["interface"], "jtag")

    def test_init_writes_only_project_config_and_requires_force(self):
        path = init_project_config(self.root, "HT32F52352", "demo.uvprojx", None, DebuggerConfig())
        self.assertEqual(path, self.root.resolve() / ".ht32-skills" / "config.json")
        self.assertEqual(set(path.parent.iterdir()), {path})
        with self.assertRaises(ConfigError):
            init_project_config(self.root, "HT32F52352", "demo.uvprojx", None, DebuggerConfig())

    def test_init_rejects_invalid_values_without_writing_file(self):
        config_path = self.root / ".ht32-skills" / "config.json"
        invalid_cases = (
            ("", "demo.uvprojx", None, DebuggerConfig()),
            (123, "demo.uvprojx", None, DebuggerConfig()),
            ("HT32F52352", "demo.uvprojx", 123, DebuggerConfig()),
            ("HT32F52352", None, None, DebuggerConfig()),
            ("HT32F52352", "../demo.uvprojx", None, DebuggerConfig()),
            ("HT32F52352", "demo.uvprojx", None, {"speed_khz": 0}),
        )
        for chip, uvprojx, target, debugger in invalid_cases:
            with self.subTest(chip=chip, uvprojx=uvprojx, target=target, debugger=debugger):
                with self.assertRaises(ConfigError):
                    init_project_config(self.root, chip, uvprojx, target, debugger)
                self.assertFalse(config_path.exists())


if __name__ == "__main__":
    unittest.main()
