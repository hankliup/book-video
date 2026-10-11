#!/usr/bin/env python3
"""Pexels 鉴权头的传法回归：密钥走 stdin 的 curl 配置，不进 argv（scripts/lib/pexels_curl.py）。

为什么钉这一条：这台机器常年并行跑七八个会话和一堆后台渲染，而 curl 的 argv 是**全局可读**的
——同机任何进程 `ps -eww -o command` 都能读出「curl -sSL -H Authorization: <key> …」整条命令行。
密钥进 argv 一次，就等于把共享 key 交给整台机器所有用户态进程；再叠上第二条：`check=True` 的
`CalledProcessError` 把 `cmd` 列表带进异常，任何打印整条命令的日志/会话记录又带走一次，而这条链路
上 curl 抖动（exit 16 HTTP2 framing / 18 partial file）是常态。2026-10-06 之前六个取图/锁定脚本
全是这个形状（`fetch-pexels-by-id.py`、`fetch-pexels-hd.py`、`fetch-pexels-curl.py`、
`fetch-pexels-previews.py`、`lock-pexels-selection.py`、`_fetch_bpq_pexels.py`，Node 侧
`fetch-pexels-images-curl.mjs` 一份），其中 `lock-pexels-selection.py` 的 docstring 还写着
「密钥绝不进命令行参数」——注释与实现相反，正是「绿灯是旧的，缺陷是新的」那一类。

三件事都要判，缺一件就是空转：
  ① lib 现场拼出的 argv 里没有密钥、也没有 `Authorization` 字样（**并且**真发一次请求，
     让本地 HTTP 服务亲眼收到那个头——只测 argv 不测送达，一个「把 header 弄丢了」的
     「修法」也能跑绿）；
  ② 反向对照：把回滚前的六个形状逐条塞进同一个度量，必须被认成泄漏，否则①是在测空气；
  ③ 密钥含引号/换行时直接拒绝——curl 配置按行、按双引号解析，那种 key 会把一行配置打断。

真网络不在这里跑（check 要能离线跑）；静态扫全仓 `scripts/` 的那道在 check.mjs 里，形状表只放
一份，避免「两边各抄一份正则」这种自己会漂的守卫。
"""
import http.server
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))

import pexels_curl  # noqa: E402

FAKE_KEY = "fAke0Key-ForLocalTestsOnly"
FAILS = []


def check(name, ok, detail=""):
    print(f"{'✅' if ok else '❌'} {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------------------
# ① 正向：argv 干净 + 头真的送达（本地服务当证人）
# ---------------------------------------------------------------------------
received = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        received["authorization"] = self.headers.get("Authorization")
        received["path"] = self.path
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音，别把测试输出搅浑
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}/v1/photos/1"

argv = pexels_curl.build(["-sS", "--max-time", "10"], url)
check("argv 里没有 Authorization 字样",
      not any("Authorization" in str(a) for a in argv), " ".join(argv))
check("argv 里没有密钥", not any(FAKE_KEY in str(a) for a in argv))
check("argv 用 -K - 从 stdin 读配置", argv[:3] == ["curl", "-K", "-"])

res = pexels_curl.run(["-sS", "--max-time", "10"], url, FAKE_KEY)
check("curl 退出码 0（配置行格式被本机 curl 接受）", res.returncode == 0,
      res.stderr.decode("utf-8", "replace")[:120])
check("服务端真的收到了那个头，值等于密钥",
      received.get("authorization") == FAKE_KEY, f"实测 {received.get('authorization')!r}")
check("URL 仍走 argv（它不是凭据）", received.get("path") == "/v1/photos/1")

data = pexels_curl.get_json(["-sS", "--max-time", "10"], url, FAKE_KEY)
check("get_json 走同一条链", isinstance(data, dict) and data.get("ok") is True)

# ---------------------------------------------------------------------------
# ② 反向对照：改之前那六个真实形状，逐个必须被同一条度量认成泄漏。
#    没这一步，① 只是在测空气——一个「干脆不发 Authorization」的实现也能全绿。
# ---------------------------------------------------------------------------
leaky_shapes = {
    "fetch-pexels-by-id.py 旧形状": ["curl", "-sSL", "--max-time", "180",
                                      f"-H", f"Authorization: {FAKE_KEY}", url],
    "fetch-pexels-hd.py 旧形状": ["curl", "-sSL", "--max-time", "120",
                                  "-H", f"Authorization: {FAKE_KEY}", url],
    "fetch-pexels-curl.py 旧形状": ["curl", "-sS", "--max-time", "30",
                                    "-H", f"Authorization: {FAKE_KEY}", url],
    "fetch-pexels-previews.py 旧形状": ["curl", "-sSL", "--max-time", "60",
                                        "-H", f"Authorization: {FAKE_KEY}", url],
    "lock-pexels-selection.py 旧形状": ["curl", "-sS", "--max-time", "60",
                                        "-H", f"Authorization: {FAKE_KEY}", url],
    "_fetch_bpq_pexels.py 旧形状": ["curl", "-s", "-m", "30", "-A", "Mozilla/5.0",
                                    "-H", f"Authorization: {FAKE_KEY}", url],
    "fetch-pexels-images-curl.mjs 旧形状": ["curl", "-sS", "--fail", "-m", "30",
                                            "-H", f"Authorization: {FAKE_KEY}", url],
}


def leaks(argv_list):
    """和 ① 用的同一条度量：密钥出现在 argv 的任一项里就是泄漏。"""
    return any(FAKE_KEY in str(a) for a in argv_list)


for name, shape in leaky_shapes.items():
    check(f"反向对照：{name} 判为泄漏", leaks(shape))
check("反向对照度量对干净 argv 判为不泄漏", not leaks(argv))

# ---------------------------------------------------------------------------
# ③ 配置行的解析安全：含引号/换行的 key 必须当场拒绝，而不是拼出一条被截断的配置
# ---------------------------------------------------------------------------
for bad in ['has"quote', "has\nnewline", "back\\slash"]:
    try:
        pexels_curl.auth_config(bad)
        check(f"拒绝危险字符的 key（{bad!r}）", False, "没抛异常")
    except pexels_curl.PexelsCurlError:
        check(f"拒绝危险字符的 key（{bad!r}）", True)

try:
    pexels_curl.auth_config("")
    check("空 key 直接拒绝", False, "没抛异常")
except pexels_curl.PexelsCurlError:
    check("空 key 直接拒绝", True)

# 报错消息里不许有密钥：拿一个必然失败的 URL 验（端口 1 拒绝连接）
try:
    pexels_curl.get_json(["-sS", "--max-time", "2"], "http://127.0.0.1:1/x", FAKE_KEY)
    check("失败时抛 PexelsCurlError", False, "竟然成功了")
except pexels_curl.PexelsCurlError as exc:
    check("失败消息里没有密钥", FAKE_KEY not in str(exc), str(exc)[:100])

server.shutdown()

print("-" * 60)
if FAILS:
    print(f"❌ 未通过 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("✅ Pexels 鉴权头传法回归：全部通过（密钥只在 stdin，argv 干净，头确实送达）")
