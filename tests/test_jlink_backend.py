import tempfile
import unittest
from pathlib import Path

from ht32_runtime.backends import OperationContext
from ht32_runtime.backends.jlink import JLinkBackend
from ht32_runtime.models import DebuggerConfig
from ht32_runtime.process import ProcessResult, ToolExecutionError


class Runner:
    def __init__(self, stdout="", code=0): self.stdout, self.code, self.calls, self.scripts = stdout, code, [], []
    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if "-CommanderScript" in argv:
            self.scripts.append(Path(argv[argv.index("-CommanderScript") + 1]).read_text(encoding="utf-8"))
        return ProcessResult(tuple(argv), self.code, self.stdout, "", 1, False, kwargs["log_path"])


class Server:
    def __init__(self): self.stopped = False
    def stop(self): self.stopped = True


class Starter:
    def __init__(self): self.calls, self.servers = [], []
    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs)); server = Server(); self.servers.append(server); return server


class JLinkBackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.image = self.root/"fw.hex"; self.image.write_text(":00", encoding="ascii")
    def tearDown(self): self.temp.cleanup()
    def ctx(self, serial="123"):
        return OperationContext(self.root, "pid", "HT32F52352", self.root/"demo.uvprojx", "Demo",
            DebuggerConfig("jlink", serial, "swd", 500),
            {"jlink_exe":"JLink.exe", "jlink_gdb_server_exe":"JLinkGDBServerCL.exe", "jlink_rtt_client_exe":"JLinkRTTClient.exe", "gdb_exe":"gdb.exe"}, self.root/"cache", self.root/"logs")

    def test_probes_and_commander_operations(self):
        runner = Runner("S/N: 123\nSerial number: 456\n"); backend = JLinkBackend(runner)
        result = backend.list_probes(self.ctx())
        self.assertEqual(result.observations["probes"], ["123"])
        backend.connect(self.ctx()); self.assertIn("device HT32F52352\nsi SWD\nspeed 500\nconnect\nexit", runner.scripts[-1])
        backend.flash(self.ctx(), self.image); self.assertIn(f"loadfile {self.image.resolve()}\nverify\nr\ng\nexit", runner.scripts[-1])
        backend.reset(self.ctx()); self.assertIn("r\ngo\nexit", runner.scripts[-1])
        backend.read_memory(self.ctx(), "0x20000000", 4, 32); self.assertIn("mem32 0x20000000, 4", runner.scripts[-1])
        backend.read_registers(self.ctx()); self.assertIn("regs", runner.scripts[-1])

    def test_writes_require_confirmation_and_validate_untrusted_values(self):
        backend = JLinkBackend(Runner())
        self.assertEqual(backend.write_memory(self.ctx(), "0x20000000", [1], 32, False).status, "blocked")
        self.assertEqual(backend.write_register(self.ctx(), "r0", "1", False).status, "blocked")
        with self.assertRaises(ValueError): backend.read_memory(self.ctx(), "exit\nerase", 1, 32)
        with self.assertRaises(ValueError): backend.connect(OperationContext(self.root, "pid", "chip\nexit", self.root/"p", None, DebuggerConfig("jlink"), self.ctx().tools, self.root/"cache", self.root/"logs"))
        with self.assertRaises(ValueError): backend.flash(self.ctx(), Path(str(self.image)+"\nexit"))
        with self.assertRaises(ValueError): backend.write_register(self.ctx(), "r0\nexit", "1", True)
        with self.assertRaises(ValueError): backend.read_memory(self.ctx(), "0x0", 1, 64)

    def test_debug_server_readiness_uses_injected_host(self):
        attempts = []
        class Connection:
            def close(self): pass
        backend = JLinkBackend(
            Runner(), Starter(), server_host="localhost",
            socket_connector=lambda address, timeout: attempts.append(address) or Connection(),
        )

        backend.start_debug_server(self.ctx(), 2444)

        self.assertEqual(attempts, [("localhost", 2444)])

    def test_debug_and_rtt_wait_for_server_tcp_readiness(self):
        attempts = []
        class Connection:
            def close(self): pass
        def connector(address, timeout):
            attempts.append(address)
            if len(attempts) % 2:
                raise OSError("not ready")
            return Connection()
        waits = []
        runner = Runner(); starter = Starter()
        backend = JLinkBackend(runner, starter, socket_connector=connector, wait=waits.append, server_timeout=1)
        elf = self.root/"fw.elf"; elf.write_text("elf", encoding="ascii")

        backend.debug(self.ctx(), elf, ["backtrace"], 2331)
        backend.rtt(self.ctx(), elf, 1, 2331, 19021)

        self.assertEqual(attempts, [("127.0.0.1", 2331), ("127.0.0.1", 2331), ("127.0.0.1", 19021), ("127.0.0.1", 19021)])
        self.assertEqual(waits, [0.05, 0.05])

    def test_server_early_exit_and_timeout_raise_execution_error(self):
        class Process:
            def __init__(self, code): self.code = code
            def poll(self): return self.code
        starter = Starter()
        server = Server(); server.process = Process(9)
        starter.__call__ = lambda *args, **kwargs: server
        backend = JLinkBackend(Runner(), lambda *args, **kwargs: server,
                               socket_connector=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("closed")),
                               wait=lambda seconds: None, server_timeout=0)
        with self.assertRaises(ToolExecutionError):
            backend.start_debug_server(self.ctx(), 2331)

        running = Server(); running.process = Process(None)
        backend = JLinkBackend(Runner(), lambda *args, **kwargs: running,
                               socket_connector=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("closed")),
                               wait=lambda seconds: None, server_timeout=0)
        with self.assertRaises(ToolExecutionError):
            backend.start_debug_server(self.ctx(), 2331)

    def test_debug_server_debug_script_and_rtt(self):
        runner = Runner(); starter = Starter(); backend = JLinkBackend(runner, starter, socket_connector=lambda *args, **kwargs: type("Connection", (), {"close": lambda self: None})())
        backend.start_debug_server(self.ctx(), 2331)
        argv = starter.calls[-1][0]
        self.assertIn("USB=123", argv); self.assertIn("-singlerun", argv)
        elf = self.root/"fw.elf"; elf.write_text("elf", encoding="ascii")
        backend.debug_script(self.ctx(), elf, ["break main", "backtrace"])
        self.assertIn("--batch", runner.calls[-1][0])
        result = backend.debug(self.ctx(), elf, ["break main", "continue", "step", "next", "finish", "backtrace", "print r0"])
        self.assertEqual(result.status, "ok"); self.assertTrue(starter.servers[-1].stopped)
        result = backend.rtt(self.ctx(), elf, 1)
        self.assertIn(result.status, {"ok", "warn"}); self.assertIn("-RTTTelnetPort", starter.calls[-1][0]); self.assertTrue(starter.servers[-1].stopped)
        ctx = self.ctx(); ctx.tools["gdb_exe"] = None
        self.assertEqual(backend.debug_script(ctx, elf, []).status, "blocked")

    def test_nonzero_tool_result_raises_execution_error(self):
        with self.assertRaises(ToolExecutionError):
            JLinkBackend(Runner(code=7)).connect(self.ctx())

    def test_missing_tools_are_blocked(self):
        ctx = self.ctx(); ctx.tools["jlink_exe"] = None
        self.assertEqual(JLinkBackend(Runner()).connect(ctx).status, "blocked")


if __name__ == "__main__": unittest.main()
