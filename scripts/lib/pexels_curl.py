"""Pexels 鉴权头的唯一传法：curl 的配置走 stdin（`-K -`），密钥绝不进 argv。

为什么 `-H "Authorization: <key>"` 不行，两条独立的理由：

1. **argv 是全局可读的。** macOS 上 `ps -eww -o command` 能把任何进程当前整条命令行读出来，
   而这台机器常年并行跑七八个会话和一堆后台渲染——一次取图就把共享 key 暴露给同机所有
   用户态进程（以及它们写的 crash log、审计、监控）。头信息不属于「URL 里那点查询词」这种
   可以接受的暴露面：URL 泄露的是检索意图，argv 泄露的是凭据本身。
2. **异常会把整条命令打出来。** `subprocess.run(..., check=True)` 抛的 `CalledProcessError`
   带着 `cmd` 列表，`curl` 抖动（exit 16 HTTP2 framing / 18 partial file 在这条链路上很常见）
   一次，就把密钥写进终端、写进会话记录、写进日志。仓库里已经有人为此补了 `_redact()` 和
   `.replace(key, "***")`——那是在泄漏之后擦，而这个模块让泄漏无从发生：密钥只走一次管道。

`-K -` 让 curl 从 stdin 读配置文件，内容形如 `header = "Authorization: xxx"`，不进 argv 也不落盘。
密钥本身按 Pexels 的字符集校验（拒绝引号/换行，否则配置文件里的引号会被打断）。

用法：

    from lib import pexels_curl
    res = pexels_curl.run(["-sSL", "--max-time", "180"], url, key)   # CompletedProcess
    data = pexels_curl.get_json(["-sS", "--max-time", "30"], url, key)

需要自带 stderr 语义的调用方（比如 fetch-pexels-by-id.py 的 `_redact`）用 `run()` 自己判返回码；
只想要 JSON 的用 `get_json()`，它在失败时抛 `PexelsCurlError`，消息里只有 URL 与 stderr，没有密钥。
"""

from __future__ import annotations

import json
import re
import subprocess

# curl 配置文件里值是用双引号包的，且按行解析：允许这两类字符就等于允许一行配置被拆成两条。
_BAD_KEY_CHARS = re.compile(r"""["'\\\n\r]""")


class PexelsCurlError(RuntimeError):
    """curl 失败。消息已确保不含密钥（只拼 URL 去查询串后的部分 + stderr）。"""


def auth_config(key: str) -> bytes:
    """curl 配置文件的字节内容：一行 header。"""
    if not key:
        raise PexelsCurlError("缺少 Pexels API Key（应来自仓库 .env，不作为命令行参数传入）")
    if _BAD_KEY_CHARS.search(key):
        raise PexelsCurlError("Pexels API Key 含引号/反斜杠/换行，无法安全写进 curl 配置")
    return f'header = "Authorization: {key}"\n'.encode("utf-8")


def build(opts, url: str) -> list[str]:
    """拼出**不含密钥**的 argv：`-K -` 让 curl 从 stdin 取 header。"""
    return ["curl", "-K", "-"] + [str(a) for a in opts] + [url]


def run(opts, url: str, key: str) -> subprocess.CompletedProcess:
    """跑一次带鉴权的 curl，返回原始 CompletedProcess（bytes stdout/stderr）。

    刻意不用 check=True：见模块 docstring 第 2 条。调用方自己判 returncode，
    并且报错时只带 `url.split("?")[0]` 和 stderr。
    """
    return subprocess.run(build(opts, url), input=auth_config(key), capture_output=True)


def _clean_stderr(raw: bytes) -> str:
    return (raw or b"").decode("utf-8", "replace").strip()[:200]


def get_text(opts, url: str, key: str) -> str:
    res = run(opts, url, key)
    if res.returncode != 0:
        raise PexelsCurlError(
            f"curl {res.returncode} 失败: {url.split('?')[0]}: {_clean_stderr(res.stderr)}")
    return res.stdout.decode("utf-8", "replace")


def get_json(opts, url: str, key: str) -> dict:
    res = run(opts, url, key)
    if res.returncode != 0:
        raise PexelsCurlError(
            f"curl {res.returncode} 失败: {url.split('?')[0]}: {_clean_stderr(res.stderr)}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise PexelsCurlError(
            f"curl {res.returncode} 返回的不是 JSON: {url.split('?')[0]}: {exc}") from exc
