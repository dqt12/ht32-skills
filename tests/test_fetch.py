import hashlib
import io
import ssl
import tempfile
import unittest
import zipfile
from pathlib import Path

from ht32_runtime.fetch import FetchFailed, FetchRefused, _HttpsOnlyRedirect, extract_bundle, run_fetch

PAYLOAD = b"PK\x03\x04 ht32 bundle payload"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class FakeResponse:
    def __init__(self, payload): self.payload, self.offset = payload, 0
    def read(self, size=-1):
        chunk = self.payload[self.offset:] if size < 0 else self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def opener_for(payload=PAYLOAD, *, fail_cert=False, error=None):
    calls = []

    def opener(url, context, timeout):
        calls.append(context)
        if fail_cert and context.verify_mode != ssl.CERT_NONE:
            raise ssl.SSLError("CERTIFICATE_VERIFY_FAILED")
        if error is not None:
            raise error
        return FakeResponse(payload)

    opener.calls = calls
    return opener


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name) / "bundle.zip"

    def tearDown(self):
        self.temp.cleanup()

    def test_https_download_with_sha256_and_size(self):
        opener = opener_for()
        result = run_fetch("https://mcp.example/bundle.zip", self.out, sha256=DIGEST,
                           size=len(PAYLOAD), opener=opener)

        self.assertEqual((result.status, result.exit_code), ("ok", 0))
        self.assertEqual(self.out.read_bytes(), PAYLOAD)
        self.assertEqual(result.artifacts["file"], str(self.out))
        self.assertEqual(result.observations["sha256"], DIGEST)
        self.assertEqual(result.observations["bytes"], len(PAYLOAD))
        self.assertEqual(result.observations["tls"], "verified")
        self.assertFalse(self.out.with_name(self.out.name + ".part").exists())
        self.assertEqual(len(opener.calls), 1)

    def test_missing_sha256_is_ok_but_warns_about_integrity(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out, opener=opener_for())
        self.assertEqual(result.status, "warn")
        self.assertIn("SHA256", result.warnings[0])
        self.assertTrue(self.out.is_file())

    def test_sha256_mismatch_deletes_partial_file(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out, sha256="0" * 64, opener=opener_for())
        self.assertEqual((result.status, result.exit_code), ("error", 3))
        self.assertIn("SHA256 不一致", result.error["message"])
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_name(self.out.name + ".part").exists())

    def test_size_mismatch_is_error(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out, size=len(PAYLOAD) + 1,
                           sha256=DIGEST, opener=opener_for())
        self.assertEqual(result.status, "error")
        self.assertIn("大小不一致", result.error["message"])
        self.assertFalse(self.out.exists())

    def test_http_url_is_refused_without_contacting_server(self):
        opener = opener_for()
        result = run_fetch("http://mcp.example/bundle.zip", self.out, opener=opener)
        self.assertEqual((result.status, result.exit_code), ("blocked", 2))
        self.assertIn("HTTPS", result.error["message"])
        self.assertEqual(opener.calls, [])

    def test_invalid_sha256_argument_is_blocked(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out, sha256="not-a-hash", opener=opener_for())
        self.assertEqual(result.status, "blocked")
        self.assertIn("64 位十六进制", result.error["message"])

    def test_cert_failure_without_hash_is_blocked_with_guidance(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out, opener=opener_for(fail_cert=True))
        self.assertEqual((result.status, result.exit_code), ("blocked", 2))
        self.assertIn("--sha256", result.error["message"])
        self.assertFalse(self.out.exists())

    def test_cert_failure_with_hash_falls_back_to_unverified(self):
        opener = opener_for(fail_cert=True)
        result = run_fetch("https://mcp.example/bundle.zip", self.out, sha256=DIGEST, opener=opener)

        self.assertEqual((result.status, result.exit_code), ("warn", 0))
        self.assertEqual(result.observations["tls"], "unverified")
        self.assertEqual(self.out.read_bytes(), PAYLOAD)
        self.assertEqual(opener.calls[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(opener.calls[1].verify_mode, ssl.CERT_NONE)

    def test_allow_unverified_permits_hashless_fallback(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out,
                           allow_unverified=True, opener=opener_for(fail_cert=True))
        self.assertEqual((result.status, result.exit_code), ("warn", 0))
        self.assertEqual(result.observations["tls"], "unverified")
        self.assertTrue(any("SHA256" in item for item in result.warnings))

    def test_network_failure_is_error(self):
        result = run_fetch("https://mcp.example/bundle.zip", self.out,
                           opener=opener_for(error=OSError("network down")))
        self.assertEqual((result.status, result.exit_code), ("error", 3))
        self.assertIn("network down", result.error["message"])

    def test_redirect_to_http_is_refused(self):
        handler = _HttpsOnlyRedirect()
        with self.assertRaises(FetchRefused):
            handler.redirect_request(None, None, 302, "Found", {}, "http://mcp.example/bundle.zip")


def zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fetch_extract_writes_project_tree_after_hash_check(self):
        payload = zip_bytes({"HT32F50241/demo.uvprojx": "<Project/>",
                             "HT32F50241/README.md": "readme"})
        digest = hashlib.sha256(payload).hexdigest()
        out = self.root / "bundle.zip"
        target = self.root / "unpacked"
        result = run_fetch("https://mcp.example/bundle.zip", out, sha256=digest,
                           opener=opener_for(payload), extract_to=target)
        self.assertEqual((result.status, result.exit_code), ("ok", 0))
        self.assertEqual(result.artifacts["extracted"], str(target))
        self.assertTrue((target / "HT32F50241" / "demo.uvprojx").is_file())

    def test_extract_refuses_non_empty_target(self):
        payload = zip_bytes({"a.txt": "a"})
        out = self.root / "bundle.zip"
        out.write_bytes(payload)
        target = self.root / "busy"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(FetchFailed):
            extract_bundle(out, target)
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_extract_refuses_path_traversal_and_absolute_entries(self):
        for member in ("../escape.txt", "/etc/passwd", "C:/Windows/evil.txt"):
            with self.subTest(member=member):
                archive = self.root / "evil.zip"
                archive.write_bytes(zip_bytes({member: "x"}))
                with self.assertRaises(FetchRefused):
                    extract_bundle(archive, self.root / "out")
                self.assertFalse((self.root / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
