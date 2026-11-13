import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ht32_runtime import cli
from ht32_runtime.serial import (
    SerialError,
    SerialTap,
    analyze_intervals,
    capture_bytes,
    list_port_infos,
    parse_port_list,
    parse_send_payload,
    render_hex,
    render_lines,
    render_stamped_lines,
    run_log,
    run_ports,
    run_send,
    write_capture_bundle,
    write_capture_log,
)

ROOT = Path(__file__).resolve().parents[1]


class FakePort:
    """按脚本返回数据的假串口（记录写入字节）。"""

    def __init__(self, reads=None, *, raise_on_open=False, reset_ok=True):
        self.reads = list(reads or [])
        self.written = b""
        self.closed = False
        self.raise_on_open = raise_on_open
        self.reset_ok = reset_ok
        self.reset_calls = 0

    def read(self, size):
        return self.reads.pop(0) if self.reads else b""

    def write(self, data):
        self.written += data
        return len(data)

    def pulse_reset(self, **_kwargs):
        self.reset_calls += 1
        return self.reset_ok

    def close(self):
        self.closed = True


class SerialModuleTest(unittest.TestCase):
    def test_normalize_port_accepts_common_forms(self):
        from ht32_runtime.serial import _normalize_port
        self.assertEqual(_normalize_port("com13"), "COM13")
        self.assertEqual(_normalize_port(r"\.\COM13"), "COM13")
        self.assertEqual(_normalize_port(" COM7 "), "COM7")
        for bad in ("NOTAPORT", "COM", "COM123456", "LPT1"):
            with self.subTest(port=bad), self.assertRaises(SerialError):
                _normalize_port(bad)

    def test_list_port_infos_maps_environment_details(self):
        details = [{"port": "COM13", "name": "USB 串行设备 (COM13)"}, {"port": "COM7", "name": None}]
        with patch("ht32_runtime.serial.enumerate_serial_port_details", return_value=details):
            ports = list_port_infos()
        self.assertEqual([(item.port, item.name) for item in ports], [("COM13", "USB 串行设备 (COM13)"), ("COM7", "COM7")])

    def test_capture_bytes_respects_deadline_and_concatenates(self):
        port = FakePort([b"AB", b"CD"])
        ticks = iter([0, 1, 2])
        data = capture_bytes(port, 2, monotonic=lambda: next(ticks), sleep=lambda _: None)
        self.assertEqual(data, b"ABCD")

    def test_capture_bytes_returns_empty_when_silent(self):
        port = FakePort([])
        ticks = iter([0, 1, 2, 3])
        self.assertEqual(capture_bytes(port, 2, monotonic=lambda: next(ticks), sleep=lambda _: None), b"")

    def test_render_lines_handles_crlf_cr_and_trailing_newline(self):
        self.assertEqual(render_lines(b"a\r\nb\rc\nd\n"), ["a", "b", "c", "d"])
        self.assertEqual(render_lines(b""), [])
        self.assertEqual(render_lines("LED1 = ON".encode("utf-8")), ["LED1 = ON"])

    def test_render_lines_replaces_undecodable_bytes_instead_of_crashing(self):
        lines = render_lines(b"\xff\xfe")
        self.assertEqual(len(lines), 1)
        self.assertNotIn("\xff", lines[0])

    def test_render_hex_layout(self):
        rows = render_hex(b"LED1 = OFF  (count 44139)\r\n")
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("00000000  4c 45 44 31 20 3d 20 4f 46 46 20 20 28 63 6f 75"))
        self.assertIn("|LED1 = OFF  (cou|", rows[0])
        self.assertTrue(rows[1].startswith("00000010"))
        self.assertEqual(render_hex(b""), [])

    def test_parse_send_payload_text_hex_and_invalid(self):
        self.assertEqual(parse_send_payload("PING", hex_mode=False), b"PING")
        self.assertEqual(parse_send_payload("41 42 43", hex_mode=True), b"ABC")
        self.assertEqual(parse_send_payload("41AB", hex_mode=True), b"\x41\xab")
        with self.assertRaises(SerialError):
            parse_send_payload("411", hex_mode=True)  # 奇数个 hex 数字
        with self.assertRaises(SerialError):
            parse_send_payload("zz", hex_mode=True)
        with self.assertRaises(SerialError):
            parse_send_payload("", hex_mode=False)

    def test_run_ports_ok_warn_and_count(self):
        empty = run_ports("serial", lister=lambda: [])
        self.assertEqual((empty.status, empty.exit_code), ("warn", 0))
        self.assertIn("未发现串口", empty.warnings[0])
        self.assertEqual(empty.observations["ports"], [])
        from ht32_runtime.serial import PortInfo
        ok = run_ports("serial", lister=lambda: [PortInfo("COM13", "USB 串行设备 (COM13)")])
        self.assertEqual(ok.status, "ok")
        self.assertEqual(ok.observations["ports"], [{"port": "COM13", "name": "USB 串行设备 (COM13)"}])

    def test_run_log_reports_lines_and_writes_global_log_only(self):
        port = FakePort([b"LED1 = ON\r\n", b"LED1 = OFF\r\n"])
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
            logs_dir = Path(home) / ".ht32-skills" / "logs" / "serial"
            before = sorted(item.name for item in Path(cwd).iterdir())
            result = run_log("serial", port="COM13", baud=115200, seconds=1, logs_dir=logs_dir,
                             opener=lambda *_args: port)
            after = sorted(item.name for item in Path(cwd).iterdir())
            self.assertEqual(before, after)  # 工程目录零写入
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.observations["lines"], ["LED1 = ON", "LED1 = OFF"])
            self.assertEqual(result.observations["bytes_received"], 23)
            log_path = Path(result.artifacts["serial_log"])
            self.assertTrue(log_path.is_file())
            self.assertIn(".ht32-skills", str(log_path))
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("LED1 = ON", text)
            self.assertRegex(text, r"\[\S+ \+[\d.]+ms\] LED1 = ON")  # 默认 both：墙钟 + 相对
            self.assertEqual(Path(result.artifacts["serial_bin"]).read_bytes(),
                             b"LED1 = ON\r\nLED1 = OFF\r\n")
            jsonl = [json.loads(item) for item in
                     Path(result.artifacts["serial_jsonl"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["offset"] for item in jsonl], [0, 11])
            self.assertTrue(all("t_abs" in item and "hex" in item for item in jsonl))
            self.assertTrue(port.closed)

    def test_unwritable_logs_dir_degrades_with_warning(self):
        """宿主沙箱拦全局目录：采集结果照常返回，只是不落日志、降级为 warn。"""
        port = FakePort([b"hello\r\n"])
        with tempfile.TemporaryDirectory() as root:
            blocker = Path(root) / "not-a-dir"
            blocker.write_text("x", encoding="utf-8")
            result = run_log("serial", port="COM13", baud=115200, seconds=1,
                             logs_dir=blocker / "logs", opener=lambda *_args: port)
        self.assertEqual((result.status, result.exit_code), ("warn", 0))
        self.assertNotIn("serial_log", result.artifacts)
        self.assertEqual(result.observations["lines"], ["hello"])
        self.assertTrue(any("HT32_SKILLS_HOME" in item for item in result.warnings))

    def test_run_log_hex_mode_and_no_data_warning(self):
        port = FakePort([b"AB"])
        with tempfile.TemporaryDirectory() as home:
            logs_dir = Path(home) / "logs" / "serial"
            result = run_log("serial", port="COM13", baud=9600, seconds=1, hex_mode=True,
                             logs_dir=logs_dir, opener=lambda *_args: port)
            self.assertEqual(result.observations["hex"], render_hex(b"AB"))

        silent = FakePort([])
        with tempfile.TemporaryDirectory() as home:
            logs_dir = Path(home) / "logs" / "serial"
            ticks = iter([0, 1, 2])
            result = run_log("serial", port="COM13", baud=115200, seconds=1, logs_dir=logs_dir,
                             opener=lambda *_args: silent, monotonic=lambda: next(ticks), sleep=lambda _: None)
            self.assertEqual((result.status, result.exit_code), ("warn", 0))
            self.assertIn("未从 COM13 收到任何数据", result.warnings[0])

    def test_capture_log_marks_flash_boundary(self):
        with tempfile.TemporaryDirectory() as home:
            logs_dir = Path(home) / "logs" / "serial"
            path = write_capture_log(logs_dir, "COM13", 115200, 15, b"OLD\r\nNEW\r\n", marker_at=5,
                                     marker_source="usb_reenumerate")
            raw = path.read_bytes()
        self.assertIn(b"[post_flash_offset] 5\n", raw)
        self.assertIn(b"[post_flash_offset_source] usb_reenumerate\n", raw)
        self.assertLess(raw.index(b"OLD"), raw.index(b"[ht32-skills] ---- target reset detected"))
        self.assertGreater(raw.index(b"NEW"), raw.index(b"[ht32-skills] ---- target reset detected"))
        self.assertNotIn(b"post-reset output ----", raw.split(b"[data]")[0])

    def test_capture_log_wording_distinguishes_approximate_boundary(self):
        """没有重新枚举时不许把「烧录命令返回」说成 post-reset。"""
        with tempfile.TemporaryDirectory() as home:
            logs_dir = Path(home) / "logs" / "serial"
            path = write_capture_log(logs_dir, "COM13", 115200, 15, b"OLD\r\nNEW\r\n", marker_at=5,
                                     marker_source="flash_command_return")
            text = path.read_text(encoding="utf-8")
        self.assertIn("flash command returned", text)
        self.assertIn("boundary approximate", text)
        self.assertNotIn("below is post-reset output", text)

    def test_bundle_writes_log_jsonl_and_bin(self):
        chunks = [{"offset": 0, "ms": 107.0, "at": "2026-10-28T22:00:00.107+08:00", "bytes": 3},
                  {"offset": 3, "ms": 214.0, "at": "2026-10-28T22:00:00.214+08:00", "bytes": 3}]
        with tempfile.TemporaryDirectory() as home:
            bundle = write_capture_bundle(Path(home), "COM13", 115200, 1, b"A\xff\nCD\n", chunks,
                                          stamp="both")
            log_text = bundle["log"].read_text(encoding="utf-8")
            records = [json.loads(item) for item in
                       bundle["jsonl"].read_text(encoding="utf-8").splitlines()]
            raw = bundle["bin"].read_bytes()
        self.assertIn("[22:00:00.107 +107.000ms] A", log_text)
        self.assertIn("[22:00:00.214 +214.000ms] CD", log_text)
        self.assertEqual(raw, b"A\xff\nCD\n")          # .bin 保留原始字节（.log 会把 0xff 洗成替换符）
        self.assertEqual(records[0]["hex"], "41ff0a")
        self.assertEqual(records[1]["t_rel_ms"], 214.0)

    def test_stamp_none_keeps_raw_capture_in_log(self):
        with tempfile.TemporaryDirectory() as home:
            path = write_capture_log(Path(home), "COM13", 115200, 1, b"OLD\r\nNEW\r\n", marker_at=5)
            raw = path.read_bytes()
        self.assertIn(b"OLD\r\n", raw)
        self.assertIn(b"NEW\r\n", raw)

    def test_stamped_lines_support_rel_abs_and_marker(self):
        chunks = [{"offset": 0, "ms": 12.5, "at": "2026-10-28T22:00:01.012+08:00", "bytes": 4},
                  {"offset": 4, "ms": 25.0, "at": "2026-10-28T22:00:01.025+08:00", "bytes": 4}]
        data = b"OLD\nNEW\n"
        self.assertEqual(render_stamped_lines(data, chunks, "rel"),
                         ["[+12.500ms] OLD", "[+25.000ms] NEW"])
        self.assertEqual(render_stamped_lines(data, chunks, "abs"),
                         ["[22:00:01.012] OLD", "[22:00:01.025] NEW"])
        self.assertEqual(render_stamped_lines(data, chunks, "both", marker_at=4,
                                              marker_source="usb_reenumerate"),
                         ["[22:00:01.012 +12.500ms] OLD",
                          "[ht32-skills] ---- target reset detected (USB CDC re-enumerated); "
                          "below is post-reset output; offset=4 ----",
                          "[22:00:01.025 +25.000ms] NEW"])

    def test_analyze_intervals_reports_gap_statistics(self):
        chunks = [{"offset": 0, "ms": 100.0, "bytes": 4},
                  {"offset": 4, "ms": 300.0, "bytes": 4},
                  {"offset": 8, "ms": 500.0, "bytes": 4}]
        stats = analyze_intervals(b"##\n\n##\n\n##\n\n", chunks)
        self.assertEqual((stats["matched_lines"], stats["intervals"]), (3, 2))
        self.assertEqual((stats["mean_ms"], stats["min_ms"], stats["max_ms"]), (200.0, 200.0, 200.0))
        self.assertEqual(stats["jitter_ms"], 0.0)
        empty = analyze_intervals(b"nothing\n", chunks)
        self.assertEqual(empty["intervals"], 0)

    def test_parse_port_list_accepts_multi_and_rejects_duplicates(self):
        self.assertEqual(parse_port_list("com13, COM14"), ["COM13", "COM14"])
        with self.assertRaises(SerialError):
            parse_port_list("COM13,COM13")
        with self.assertRaises(SerialError):
            parse_port_list(" , ")

    def test_run_log_sends_while_capturing(self):
        port = FakePort([b"REQ\r\n", b"RESP\r\n"])
        with tempfile.TemporaryDirectory() as home:
            result = run_log("serial", port="COM13", baud=115200, seconds=1,
                             logs_dir=Path(home), send_plan=[(0.0, b"PING\r\n")],
                             opener=lambda *_args: port)
        self.assertEqual(port.written, b"PING\r\n")
        self.assertEqual(result.observations["sends"], [{"at_seconds": 0.0, "bytes": 6}])
        self.assertEqual(result.observations["lines"], ["REQ", "RESP"])

    def test_run_log_multi_port_shares_one_window(self):
        first, second = FakePort([b"from-COM13\r\n"]), FakePort([b"from-COM14\r\n"])
        ports = {"COM13": first, "COM14": second}
        with tempfile.TemporaryDirectory() as home:
            result = run_log("serial", port="COM13,COM14", baud=115200, seconds=1,
                             logs_dir=Path(home), opener=lambda name, _baud: ports[name])
            self.assertTrue(Path(result.artifacts["serial_log_COM14"]).is_file())
            self.assertTrue(Path(result.artifacts["serial_bin"]).is_file())
        self.assertEqual(result.observations["ports"], ["COM13", "COM14"])
        self.assertEqual(result.observations["port"], "COM13,COM14")
        self.assertEqual(result.observations["per_port"]["COM14"]["lines"], ["from-COM14"])
        self.assertIn("serial_log_COM14", result.artifacts)

    def test_run_log_reset_pulses_before_capture(self):
        port = FakePort([b"boot\r\n"])
        with tempfile.TemporaryDirectory() as home:
            result = run_log("serial", port="COM13", baud=115200, seconds=1, logs_dir=Path(home),
                             reset=True, opener=lambda *_args: port)
        self.assertEqual(port.reset_calls, 1)
        self.assertEqual(result.observations["reset"], {"method": "dtr-rts-pulse", "applied": True})
        self.assertEqual(result.status, "ok")

    def test_run_log_analyze_intervals_and_marker_warning(self):
        port = FakePort([b"#\r\n", b"#\r\n"])
        with tempfile.TemporaryDirectory() as home:
            result = run_log("serial", port="COM13", baud=115200, seconds=1, logs_dir=Path(home),
                             analyze="intervals", opener=lambda *_args: port)
        self.assertEqual(result.observations["analyze"]["intervals"]["matched_lines"], 2)
        self.assertEqual(result.observations["analyze"]["intervals"]["intervals"], 1)

        silent = FakePort([b"no marker here\r\n"])
        with tempfile.TemporaryDirectory() as home:
            result = run_log("serial", port="COM13", baud=115200, seconds=1, logs_dir=Path(home),
                             analyze="intervals", opener=lambda *_args: silent)
        self.assertTrue(any("analyze intervals" in item for item in result.warnings), result.warnings)

    def test_win32_access_denied_message_points_to_comassistant(self):
        """错误 5 的提示要能让 agent 立刻想到释放 MCP 占用的串口。"""
        from ht32_runtime.serial import WindowsSerialPort
        with patch("ht32_runtime.serial._CreateFileW", return_value=None), \
                patch("ht32_runtime.serial.ctypes.get_last_error", return_value=5):
            with self.assertRaises(SerialError) as caught:
                WindowsSerialPort("COM13", 115200).open()
        self.assertIn("ComAssistant", str(caught.exception))

    def test_run_log_open_failure_propagates_serial_error(self):
        def opener(*_args):
            raise SerialError("串口 COM99 不存在：请先用 `serial ports` 确认可用端口")
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(SerialError):
                run_log("serial", port="COM99", baud=115200, seconds=1,
                        logs_dir=Path(home), opener=opener)

    def test_run_send_text_hex_and_newline(self):
        port = FakePort()
        result = run_send("serial", port="COM13", baud=115200, data="PING", newline=True,
                          opener=lambda *_args: port)
        self.assertEqual((result.status, port.written), ("ok", b"PING\r\n"))
        hex_port = FakePort()
        run_send("serial", port="COM13", baud=115200, data="41 42", hex_mode=True,
                 opener=lambda *_args: hex_port)
        self.assertEqual(hex_port.written, b"AB")


class TapPort:
    """贯穿式观测用的假串口：按脚本给数据，可在第 N 次读取时抛错（模拟端口消失）。"""

    def __init__(self, chunks=(), *, fail_at=None, fail_always=False):
        self.chunks, self.fail_at, self.fail_always = list(chunks), fail_at, fail_always
        self.reads, self.closed = 0, False

    def read(self, size):
        self.reads += 1
        if self.fail_always or (self.fail_at is not None and self.reads == self.fail_at):
            raise SerialError("端口消失（复位重新枚举）")
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


class FakeClock:
    """确定性时钟：sleep 即推进虚拟时间，避免测试依赖真实等待。"""

    def __init__(self, start=0.0):
        self.now = start

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 1e-9)


class SerialTapTest(unittest.TestCase):
    @staticmethod
    def wait_for(predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError("等待采集条件超时")

    def tap(self, opener, clock, **kwargs):
        return SerialTap("COM13", 115200, opener=opener, sleep=clock.sleep,
                         monotonic=clock.monotonic, poll_interval=0.01, **kwargs)

    def test_tap_keeps_bytes_printed_at_start_and_stops_on_silence(self):
        port = TapPort([b"HT32F5xxxx boot\r\n", b"init done\r\n"])
        clock = FakeClock()
        handle = self.tap(lambda *_args: port, clock)
        handle.start()
        self.wait_for(lambda: b"".join(handle._chunks).endswith(b"init done\r\n"))
        before = clock.now
        report = handle.stop(tail_seconds=30.0, silence_seconds=0.2)
        self.assertEqual(report.data, b"HT32F5xxxx boot\r\ninit done\r\n")
        self.assertEqual((report.reopens, report.errors), (0, []))
        self.assertEqual(report.stopped_by, "idle")
        self.assertLess(clock.now - before, 30.0)  # 静默早停：没耗满窗口
        self.assertTrue(port.closed)

    def test_tap_reopens_after_port_disappears(self):
        ports = iter([TapPort([b"before\r\n"], fail_at=2), TapPort([b"after reset\r\n"])])
        clock = FakeClock()
        handle = self.tap(lambda *_args: next(ports), clock, max_reopens=2)
        handle.start()
        self.wait_for(lambda: b"".join(handle._chunks) == b"before\r\nafter reset\r\n")
        report = handle.stop(tail_seconds=5.0, silence_seconds=0.1)
        self.assertEqual(report.reopens, 1)
        self.assertEqual(report.data, b"before\r\nafter reset\r\n")

    def test_tap_reports_error_when_reopen_exhausted(self):
        clock = FakeClock()
        handle = self.tap(lambda *_args: TapPort(fail_always=True), clock, max_reopens=1)
        handle.start()
        report = handle.stop(tail_seconds=0.05, silence_seconds=0.01)
        self.assertEqual(report.data, b"")
        self.assertEqual(report.reopens, 1)
        self.assertTrue(any("重开已达上限" in message for message in report.errors))

    def test_tap_start_propagates_open_failure(self):
        def opener(*_args):
            raise SerialError("串口 COM99 不存在：请先用 `serial ports` 确认可用端口")
        with self.assertRaises(SerialError):
            SerialTap("COM99", 115200, opener=opener).start()

    def test_tap_stop_without_start_is_empty(self):
        report = self.tap(lambda *_args: TapPort(), FakeClock()).stop(tail_seconds=1.0)
        self.assertEqual((report.data, report.reopens), (b"", 0))

    def test_tap_silence_threshold_adapts_to_slow_print_cadence(self):
        """固件每 2s 打印一次时，2s 固定阈值会误判静默；阈值应放大到「典型间隔 × 1.5」。"""
        handle = self.tap(lambda *_args: TapPort(), FakeClock())
        handle._arrivals = [0.0, 2.0, 4.0]
        handle._last_data_at = 4.0
        handle._chunks.append(b"tick\r\n")
        self.assertEqual(handle._typical_interval(), 2.0)
        report = handle.stop(tail_seconds=30.0, silence_seconds=2.0)
        self.assertEqual(report.silence_used, 3.0)
        self.assertEqual(report.stopped_by, "timeout")  # 未启动采集线程，只有阈值生效

    def test_tap_typical_interval_ignores_burst_gaps(self):
        handle = self.tap(lambda *_args: TapPort(), FakeClock())
        handle._arrivals = [0.0, 0.01, 0.02, 2.0]
        self.assertEqual(handle._typical_interval(), 1.98)
        handle._arrivals = []
        self.assertIsNone(handle._typical_interval())


class SerialCliTest(unittest.TestCase):
    def run_cli(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return code, json.loads(buffer.getvalue())

    def test_serial_ports_command_returns_single_json(self):
        with patch("ht32_runtime.cli.serial_run_ports") as run:
            run.return_value = run_ports("serial", lister=lambda: [])
            code, payload = self.run_cli(["serial", "ports"])
        self.assertEqual((code, payload["status"]), (0, "warn"))
        self.assertEqual(payload["operation"], "serial-ports")
        self.assertEqual(payload["backend"], "serial")

    def test_serial_log_nonexistent_port_is_blocked_with_clear_message(self):
        code, payload = self.run_cli(["serial", "log", "--port", "COM99", "--seconds", "1"])
        self.assertEqual((code, payload["status"]), (2, "blocked"))
        self.assertIn("COM99", payload["error"]["message"])

    def test_serial_log_invalid_port_name_is_blocked(self):
        code, payload = self.run_cli(["serial", "log", "--port", "NOTAPORT", "--seconds", "1"])
        self.assertEqual((code, payload["status"]), (2, "blocked"))
        self.assertIn("不合法", payload["error"]["message"])

    def test_serial_send_invalid_hex_is_blocked(self):
        code, payload = self.run_cli(["serial", "send", "--port", "COM13", "--data", "zz", "--hex"])
        self.assertEqual((code, payload["status"]), (2, "blocked"))
        self.assertIn("hex", payload["error"]["message"])

    def test_runtime_never_references_host_private_state_dir(self):
        """串口自足化红线：runtime 不得出现宿主 skill 私有状态目录名。

        目录名以拼接写法构造——本仓库（含测试自身）对该字符串必须零命中，
        全仓 grep 才是干净的验收证据。
        """
        needle = "." + "embedded" + "skills"
        for source in (ROOT / "runtime").rglob("*.py"):
            with self.subTest(source=source.name):
                self.assertNotIn(needle, source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
