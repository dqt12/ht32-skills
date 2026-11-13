"""工程 zip 下载器：固定「Python + SHA256 校验」的下载路径。

为什么要有它：`export_project_bundle` 云端模式返回 token 下载 URL，此前由 Agent 自己
选下载手段——PowerShell / curl 走 schannel，在受限宿主里会报
`SEC_E_NO_CREDENTIALS`；改成 Python 后，又常被写成「跳过证书校验」。两条路都不该由
Agent 即兴决定，所以固化成一个命令：

* 传输只走 HTTPS（拒绝 http；重定向到 http 也拒绝）
* 默认用 Python(OpenSSL) 的证书校验（不经 schannel，不会拿到 schannel 的凭据错误）
* 证书链校验失败时，只要给了 MCP 报告里的 SHA256（或显式 ``--allow-unverified``），
  退化为「按哈希校验」下载——内容完整性由 SHA256 钉死，不再依赖 CA
* 大小 / SHA256 必须与 export 报告一致；不一致删除半成品并报错
"""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .models import OperationResult

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1 << 20
_CERT_MARKERS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "self signed",
    "self-signed",
    "unable to get local issuer",
    "unknown ca",
    "no credentials",
)

Opener = Callable[[str, ssl.SSLContext, int], object]


class FetchRefused(RuntimeError):
    """策略/参数上的拒绝（非 HTTPS、证书不可信又无哈希）→ blocked。"""


class FetchFailed(RuntimeError):
    """下载或校验执行失败 → error。"""


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(str(newurl)).scheme.lower() != "https":
            raise FetchRefused("下载地址被重定向到非 HTTPS 地址，已拒绝")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _verified_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _unverified_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _default_open(url: str, context: ssl.SSLContext, timeout: int):
    opener = urllib.request.build_opener(
        _HttpsOnlyRedirect(), urllib.request.HTTPSHandler(context=context)
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ht32-skills/runtime"})
    return opener.open(request, timeout=timeout)


def _is_cert_failure(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLError):
        return True
    text = f"{exc} {reason or ''}".lower()
    return any(marker in text for marker in _CERT_MARKERS)


def _friendly(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"下载失败：HTTP {exc.code}（URL 可能已过期，重新 export 一次再试）"
    reason = getattr(exc, "reason", None)
    return f"下载失败：{reason or exc}"


def _stream(url: str, target: Path, context: ssl.SSLContext, opener: Opener, timeout: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with opener(url, context, timeout) as response, target.open("wb") as handle:
        while True:
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            total += len(chunk)
    return total, digest.hexdigest()


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _safe_members(archive: Path) -> list[str]:
    """校验 zip 成员路径：拒绝绝对路径、盘符与 `..` 逃逸（防解压写出目标目录）。"""
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            raise FetchRefused(f"压缩包内含绝对路径条目，已拒绝解压：{name}")
        parts = [part for part in normalized.split("/") if part]
        if any(part == ".." for part in parts):
            raise FetchRefused(f"压缩包内含目录逃逸条目，已拒绝解压：{name}")
    return names


def extract_bundle(archive: Path, target: Path) -> Path:
    """把下载好的工程 zip 解到 target（安全校验后整包解压）；target 必须为空或不存在。"""
    archive, target = Path(archive), Path(target)
    if not archive.is_file():
        raise FetchFailed(f"压缩包不存在：{archive}")
    names = _safe_members(archive)
    if target.exists():
        if not target.is_dir():
            raise FetchFailed(f"解压目标已存在且不是目录：{target}")
        if any(target.iterdir()):
            raise FetchFailed(f"解压目标目录非空（不覆盖已有内容）：{target}")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    if not names:
        raise FetchFailed(f"压缩包为空：{archive}")
    return target


def run_fetch(
    url: str,
    out: Path,
    *,
    sha256: str | None = None,
    size: int | None = None,
    allow_unverified: bool = False,
    timeout: int = 120,
    opener: Opener | None = None,
    extract_to: Path | None = None,
) -> OperationResult:
    """下载 URL 到 out，按需校验大小与 SHA256；`extract_to` 给出时再安全解压；返回统一 OperationResult。"""
    result = OperationResult.start("fetch", None, "global")
    opener = opener or _default_open

    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme.lower() != "https":
        result.error = {"message": "只允许 HTTPS 下载：请使用 export 返回的 https 下载地址"}
        return result.finish("blocked")

    expected = (sha256 or "").strip().lower().replace(" ", "")
    if expected and not _SHA256.match(expected):
        result.error = {"message": "SHA256 必须是 64 位十六进制字符串（取 export 报告里的值）"}
        return result.finish("blocked")

    out = Path(out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.error = {"message": f"输出目录不可写：{exc}"}
        return result.finish("blocked")

    part = out.with_name(out.name + ".part")
    tls = "verified"
    try:
        try:
            total, digest = _stream(str(url), part, _verified_context(), opener, timeout)
        except FetchRefused:
            raise
        except Exception as exc:
            if not _is_cert_failure(exc):
                raise FetchFailed(_friendly(exc)) from exc
            if not (expected or allow_unverified):
                raise FetchRefused(
                    "TLS 证书校验失败（" + _friendly(exc) + "）：如已拿到 export 报告里的 SHA256，"
                    "带上 --sha256 按哈希校验下载（内容完整性由哈希钉死）；否则检查系统证书存储 / 网络代理"
                ) from exc
            tls = "unverified"
            result.warnings.append("TLS 证书链校验失败，已改用哈希校验下载（内容完整性由 SHA256 保证）")
            try:
                total, digest = _stream(str(url), part, _unverified_context(), opener, timeout)
            except Exception as retry_exc:
                raise FetchFailed(_friendly(retry_exc)) from retry_exc

        if size is not None and total != int(size):
            raise FetchFailed(f"大小不一致：export 报告 {size} 字节，实际下载 {total} 字节")
        if expected and digest != expected:
            raise FetchFailed(f"SHA256 不一致：export 报告 {expected}，实际 {digest}")
        os.replace(part, out)
    except FetchRefused as exc:
        _cleanup(part)
        result.error = {"message": str(exc)}
        return result.finish("blocked")
    except FetchFailed as exc:
        _cleanup(part)
        result.error = {"message": str(exc)}
        return result.finish("error")
    except Exception as exc:
        _cleanup(part)
        result.error = {"message": _friendly(exc)}
        return result.finish("error")

    if tls == "unverified" and not expected:
        result.warnings.append("本次既未校验 CA 也未提供 SHA256：完整性无保证，请自行确认来源")
    elif tls == "verified" and not expected:
        result.warnings.append("未提供 SHA256：完整性仅依赖 TLS，建议带上 export 报告的 SHA256 再下载")
    result.artifacts["file"] = str(out)
    result.observations = {
        "url": str(url),
        "bytes": total,
        "sha256": digest,
        "tls": tls,
        "size_expected": size,
        "sha256_expected": expected or None,
    }
    if extract_to is not None:
        try:
            extracted = extract_bundle(out, Path(extract_to))
        except FetchRefused as exc:
            result.error = {"message": str(exc)}
            return result.finish("blocked")
        except (FetchFailed, zipfile.BadZipFile, OSError) as exc:
            result.error = {"message": f"下载已完成（zip 保留在 {out}），但解压失败：{exc}"}
            return result.finish("error")
        result.artifacts["extracted"] = str(extracted)
        result.observations["extracted"] = str(extracted)
    return result.finish("warn" if result.warnings else "ok")
