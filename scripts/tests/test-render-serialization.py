#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲前串行化判据的双向回归（2026-10-08 立，任务 #43）。

被测对象是 `scripts/render-hook-batch.sh` 里那五个函数**本身**——不是它们的注释、也不是另抄的
一份副本。做法是 `HF_SOURCE_ONLY=1 source` 那份脚本再逐条调用，所以跑的就是生产链上的代码。

为什么这一套必须常驻：连着两次真事故都不是「渲出来不合格」，而是「渲的时候机器不对」：
 ⑴ 13:13《昆虫记》六次 attempt 全 `ENOTEMPTY …/npm-cache/_npx/<hash>/node_modules/get-uri`——
    两个会话同时 npx，抢同一棵 _npx 安装树，谁都不报错，只是永远渲不出文件，而这条链会把六次
    机会全烧在同一个竞态上；
 ⑵ 2026-10-05《李尔王》：另一个会话正在改写共享真源时开渲，成片是「一半旧一半新」的像素，
    十道门禁照绿。
两类都不产生「不合格产物」：它们产生**没有产物的渲染**和**无法对账的渲染**。所以断言只能钉在
「什么时候允许开渲」这个判据上，而判据里有进程表、mtime、锁目录三种量——三种都能被写坏，且写坏
之后一切照绿，因此每条都带「写坏了会怎样」的对照。
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = "scripts/render-hook-batch.sh"
FIXDIR = os.path.join(ROOT, "tmp", "render-serialization-test")
QUIET_AGE_SECONDS = 3600       # 夹具默认「一小时前就没人动过」——见 stamp_quiet()

SPAWNED = []
FAILED = []
PASSED = 0


def check(name, ok, detail=""):
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED.append(name)
        print(f"  ❌ {name}" + (f"\n     {detail}" if detail else ""))
    return ok


def sh(expr, env=None, timeout=180):
    """把生产脚本 source 进来再执行 expr。"""
    e = dict(os.environ)
    e["HF_SOURCE_ONLY"] = "1"
    e.pop("HF_SETTLE_BYPASS", None)       # 逐条测试自己决定，别继承上一次运行的环境
    e.update(env or {})
    # errors="replace" 不是装饰：bash 3.2 把全角首字节读进变量名时，`set -u` 的报错里带的就是
    # 那半个非法字节（2026-10-09 实测 `$owner）` 那行炸出的 stderr 第 45 字节是 0xef）。默认严格
    # 解码会让这条断言变成 Python 自己的 traceback，读起来像测试坏了，实际是被测脚本炸了。
    p = subprocess.run(["bash", "-c", f'set -u; source {SCRIPT}; {expr}'],
                       cwd=ROOT, env=e, capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def spawn(args):
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    SPAWNED.append(p)
    time.sleep(0.6)
    return p


def kill(p):
    try:
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=5)
    except Exception:
        pass


def pgrep_pids(pattern):
    out = subprocess.run(["pgrep", "-fl", pattern], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s", line)
        if m:
            pids.add(int(m.group(1)))
    return pids


# ---------------------------------------------------------------------------
# 夹具：一个「像仓库根」的目录，只放判据真正要读的八个真源。copy2 保留 mtime，所以健康夹具的
# 「最后改动距今」天然很大——正例必须一秒内放行，这本身就是第 3 组第一条断言。
# ---------------------------------------------------------------------------
REQUIRED_SOURCES = [
    "scripts/create-episode-preview.mjs",
    "scripts/render_scene_map_template_fallback.py",
    "scripts/lib/book-cover.json",
    "scripts/lib/book_cover.py",
    "scripts/check-book-cover-overlay.py",
    "scripts/check-water-ripple.py",
    "scripts/lib/water_ripple.py",
    "scripts/composition-digest.py",
]


def fixture(name, mutate=None):
    root = os.path.join(FIXDIR, name)
    if os.path.isdir(root):
        shutil.rmtree(root)
    for rel in REQUIRED_SOURCES:
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), dst)
    if mutate:
        mutate(root)
    stamp_quiet(root)
    return root


def stamp_quiet(root, age=QUIET_AGE_SECONDS):
    """把夹具里所有文件的 mtime 统一压到 age 秒之前。
    静默判据的输入之一就是 mtime，而 copy2 保留的是**仓库此刻**的 mtime——2026-10-09 11:31 实测：
    另一个会话正在改门 14（`check-book-cover-overlay.py` 落盘于 24 秒前），于是「机器静 → 立刻放行」
    这条正例被真实世界顶红。正例的**前提**是「这台机器已经静了一小时」，前提就该由夹具写定，
    而不是碰运气；否则这条判据会因为别人干活而红、因为别人没干活而绿，最后和所有看运气的断言
    一样被人删掉。改动（edit/_none_motion/_half_written）都发生在 stamp 之前，所以被改过的那份
    也照样是「一小时前改完就没人碰了」。第 3 组里「有人正在连续改」那条用真进程去 utime，不靠这里。"""
    t = time.time() - age
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            os.utime(os.path.join(dirpath, f), (t, t))


def edit(rel_path, drop_token):
    """夹具改动器：把某个 API 引用**真的**删掉，而不是造一份「长得像」的假文件。"""
    def _m(root):
        p = os.path.join(root, rel_path)
        src = open(p, encoding="utf-8").read()
        assert drop_token in src, f"{rel_path} 里本来就没有 {drop_token}，夹具改动器无效"
        open(p, "w", encoding="utf-8").write(src.replace(drop_token, "#已删"))
    return _m


def _none_motion(root):
    p = os.path.join(root, "scripts/lib/book-cover.json")
    cfg = json.load(open(p, encoding="utf-8"))
    cfg["motion"]["type"] = "none"
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def _half_written(root):
    p = os.path.join(root, "scripts/lib/book-cover.json")
    src = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(src[: len(src) // 2])


# ---------------------------------------------------------------------------
# 第 3 组的「机器上有没有人在渲」必须读一份**受控**的进程表，不能读这台机器此刻的表。
# 理由不是「测试要稳」这句空话：本机同时开着好几个会话——2026-10-09 11:20 写这条回归时，
# 另一个会话的批正跑着兜底渲染器（pgrep 里那行 33276 就是它）。判据读 pgrep，真表里只要
# 有别人的渲染，「机器静 → 立刻放行」这条正例就必然红；而一条只在机器碰巧空着时才绿的
# 回归不会被跑第二遍，判据于是从常驻变成摆设（这条仓库已经付过一次这个利息）。
# 做法：把一个假 pgrep 放进 PATH 前面，只让它打印夹具里那份表。生产的 awk 自排除、comm
# 白名单、mtime 静默窗、封层三方一致全部照跑真代码，被替换的只有「表里有谁」这一项输入。
# 非空转由 busy 那条承担：同一份判据、同一份夹具，表里多一个活着的 pid 就必须从「放行」
# 翻成「等」——外加断言那份表里的标志串真的被打出来了，证明走的是垫片不是真机器表。
# ---------------------------------------------------------------------------
SHIM_BIN = os.path.join(FIXDIR, "shim-bin")
SHIM_TABLE = os.path.join(FIXDIR, "pgrep-table.txt")


def install_pgrep_shim():
    os.makedirs(SHIM_BIN, exist_ok=True)
    p = os.path.join(SHIM_BIN, "pgrep")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('#!/bin/bash\ncat "${ZQ_PGREP_TABLE:-/dev/null}" 2>/dev/null || true\n')
    os.chmod(p, 0o755)


def settle_arm(root, table="", env_extra=None, book=None, quiet=True, timeout=180):
    with open(SHIM_TABLE, "w", encoding="utf-8") as fh:
        fh.write(table)
    if quiet:
        # 每一趟都自己把夹具压回「一小时前就没人动过」。上一趟的 toucher 是用 os.utime(path, None)
        # 弄脏的——它把 mtime 写成**杀死它那一刻**，于是后面所有正例都读成「30 秒前刚改过」。
        # 静默窗的输入必须由夹具写定，不然同一份判据会因为前一条断言做过什么而红。
        stamp_quiet(root)
    e = dict(SETTLE_FAST)
    e["ZQ_PGREP_TABLE"] = SHIM_TABLE
    e["PATH"] = SHIM_BIN + os.pathsep + os.environ["PATH"]
    e.update(env_extra or {})
    return sh(f'settle_before_render "{book or FIXTURE_BOOK}" "{root}"', env=e, timeout=timeout)


SETTLE_FAST = {
    "HF_SETTLE_SECONDS": "60",
    "HF_SETTLE_POLL_SECONDS": "1",
    "HF_SETTLE_RECHECK_SECONDS": "1",
    "HF_SETTLE_MAX_WAIT": "8",
}
FIXTURE_BOOK = "串行化判据测试期"

print("渲前串行化判据回归（scripts/render-hook-batch.sh）")
install_pgrep_shim()

# =========================================================================
# 1) 忙判据：钉在可执行体上
# =========================================================================
print("\n[1] others_rendering：谁算忙")

fake = spawn([sys.executable, "-c", "import time; time.sleep(60)", "hyperframes-zqtest"])
rc, out = sh(f'others_rendering "{FIXTURE_BOOK}"')
check("假的 python/hyperframes 渲染进程被判成忙（它必须出现）", str(fake.pid) in out,
      f"rc={rc} out={out[:300]}——判据如果把解释器放过去，两个会话就会一起去抢同一棵 _npx 树")

own = spawn([sys.executable, "-c", "import time; time.sleep(60)",
             "hyperframes-zqtest", "荒凉山庄"])
rc, out = sh('others_rendering "荒凉山庄"')
check("同书名的本期渲染被自排除（否则永远等不到机器静）", str(own.pid) not in out, out[:300])
rc, out = sh(f'others_rendering "{FIXTURE_BOOK}"')
check("换个书名它就必须在（自排除不是把判据关掉）", str(own.pid) in out, out[:300])

psargs = subprocess.run(["ps", "-o", "args=", "-p", str(own.pid)],
                        capture_output=True, text=True).stdout
envC = dict(os.environ)
envC["LC_ALL"] = "C"
envC["LANG"] = "C"
psargs_c = subprocess.run(["ps", "-o", "args=", "-p", str(own.pid)],
                          capture_output=True, text=True, env=envC).stdout
same_book_out = sh(f'others_rendering "荒凉山庄"')[1]
pg_c = subprocess.run(["pgrep", "-fl", r"hyperframes|render_scene_map_template_fallback\.py"],
                      capture_output=True, text=True, env=envC).stdout
check("书名自排除走的是 pgrep 原文：同一个 pid 在 `ps -o args=` 里随环境变（UTF-8 出原字、"
      "LC_ALL=C 出 M-xx），拿它当判据是一枚硬币",
      "荒凉山庄" in psargs and "荒凉山庄" not in psargs_c and "M-" in psargs_c,
      f"utf8={psargs[-40:]!r} / C={psargs_c[-40:]!r}")
check("pgrep 那份不受 locale 影响（LC_ALL=C 下仍然是中文），所以 awk index() 的自排除是稳的",
      str(own.pid) in pg_c and "荒凉山庄" in pg_c and str(own.pid) not in same_book_out,
      f"pgrep_C 有={str(own.pid) in pg_c} 同书调用输出里有它={str(own.pid) in same_book_out}")

watcher = spawn(["/bin/zsh", "-c",
                 "for i in 1 2 3 4; do grep -c hyperframes "
                 "scripts/render_scene_map_template_fallback.py; sleep 20; done"])
hits = pgrep_pids(r"hyperframes|render_scene_map_template_fallback\.py")
rc, out = sh(f'others_rendering "{FIXTURE_BOOK}"')
check("对照：pgrep 本身看得见那个轮询监控进程（所以下一条的排除来自 comm 判据，不是漏检）",
      watcher.pid in hits, f"pgrep={sorted(hits)}")
check("但盯着同一批真源做 grep 的 shell 不被判成忙（2026-10-08 13:47 那次队列原地空等就是这个）",
      str(watcher.pid) not in out, out[:400])

for p in (fake, own, watcher):
    kill(p)

# =========================================================================
# 2) 本书封层的三方一致
# =========================================================================
print("\n[2] cover_layer_ready：lib / 渲染器 / 门 14 / 水波门共读同一条 motion")

rc, out = sh(f'cover_layer_ready "{ROOT}"')
check("今天的仓库根必须绿（lib 声明 zoom_in，三个读者都引用动效 API）",
      rc == 0 and "三方一致" in out, f"rc={rc} out={out[:300]}")

r = fixture("no-renderer-seq", edit("scripts/render_scene_map_template_fallback.py", "cover_frame_sequence"))
rc, out = sh(f'cover_layer_ready "{r}"')
check("渲染器退回静态叠层 → 红且点名渲染器",
      rc == 1 and "render_scene_map_template_fallback.py" in out, f"rc={rc} out={out[:300]}")

r = fixture("no-water-mask", edit("scripts/check-water-ripple.py", "per_frame_cover_masks"))
rc, out = sh(f'cover_layer_ready "{r}"')
check("水波门丢了逐帧足迹掩码 → 红且点名 check-water-ripple.py",
      rc == 1 and "check-water-ripple.py" in out, f"rc={rc} out={out[:300]}")

r = fixture("no-gate14-zoom", edit("scripts/check-book-cover-overlay.py", "zoom_at"))
rc, out = sh(f'cover_layer_ready "{r}"')
check("门 14 退回静态比对 → 红且点名门 14",
      rc == 1 and "check-book-cover-overlay.py" in out, f"rc={rc} out={out[:300]}")

r = fixture("motion-none", _none_motion)
rc, out = sh(f'cover_layer_ready "{r}"')
check("lib 没声明动效 → 绿（没有这层的期数不该被三方判据卡住）",
      rc == 0 and "未声明" in out, f"rc={rc} out={out[:300]}")

r = fixture("half-written", _half_written)
rc, out = sh(f'cover_layer_ready "{r}"')
check("lib 的 json 正在被写（半截）→ 判「等」而不是判「过」",
      rc == 1 and "正在写" in out, f"rc={rc} out={out[:300]}")

r = fixture("missing-lib")
os.remove(os.path.join(r, "scripts/lib/book_cover.py"))
rc, out = sh(f'cover_layer_ready "{r}"')
check("真源被删掉的那一秒 → 红（异常不能被吞、不能静默放行）", rc == 1, f"rc={rc} out={out[:300]}")

# =========================================================================
# 3) 静默判据
# =========================================================================
print("\n[3] settle_before_render：什么时候才允许开渲")

healthy = fixture("healthy")
t0 = time.time()
rc, out = settle_arm(healthy)
el = time.time() - t0
check("机器静、无人渲、三方一致 → 放行", rc == 0 and "✅" in out, f"rc={rc} out={out[-400:]}")
check("且放行是**立刻**的（静默窗按「最后一次改动距今」算，不是「我已经看了多久」）",
      el < 10 and "已静默" in out,
      f"用了 {el:.1f}s 而 SETTLE_SECONDS=60：判据一付息，十期一批就每期白等 240 秒，最后被人删掉")

toucher = spawn([sys.executable, "-c",
                 "import time,os,sys\n"
                 "p = sys.argv[1]\n"
                 "for _ in range(200):\n"
                 "    os.utime(p, None)\n"
                 "    time.sleep(0.4)\n",
                 os.path.join(healthy, "scripts/composition-digest.py")])
rc, out = settle_arm(healthy)
check("有人正在连续改共享真源 → 等不到就红（宁可多判不可漏判）",
      rc == 1 and "最后改动距今" in out, f"rc={rc} out={out[-400:]}")
kill(toucher)

r = fixture("missing-required")
os.remove(os.path.join(r, "scripts/render_scene_map_template_fallback.py"))
rc, out = settle_arm(r)
check("必需真源被删走（有人在重构）→ 判「缺席、等」，不是把这一项跳过",
      rc == 1 and "共享真源缺席" in out, f"rc={rc} out={out[-400:]}")

r = fixture("cover-diverged", edit("scripts/check-water-ripple.py", "per_frame_cover_masks"))
rc, out = settle_arm(r)
check("封层三方分叉 → 渲前就拦下（而不是渲完让门去红）",
      rc == 1 and "封层真源还没落齐" in out, f"rc={rc} out={out[-400:]}")

busy = spawn([sys.executable, "-c", "import time; time.sleep(90)"])
MARK = "zqshim-标记-token"
table = f"{busy.pid} /opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/" \
        f"Versions/3.14/Resources/Python.app/Contents/MacOS/Python -c import time; " \
        f"time.sleep(90) hyperframes {MARK}\n"
rc, out = settle_arm(healthy, table=table)
check("有他期渲染在跑 → 等，等到超时就红", rc == 1 and "有他期渲染在跑" in out,
      f"rc={rc} out={out[-400:]}")
check("这条走的是受控进程表，不是本机此刻的表（并发会话不会把正例顶红，判据也就不会被删）",
      MARK in out, out[-400:])
# 同一份判据、同一份夹具，只把表里那一行换成「这本期自己的渲染」——自排除必须让正例回到放行
rc, out = settle_arm(healthy, table=table.replace(MARK, f"{MARK} {FIXTURE_BOOK}"))
check("表里只有本期自己的渲染 → 自排除生效，仍然放行（否则一批十期会自己把自己卡死）",
      rc == 0 and "✅" in out, f"rc={rc} out={out[-400:]}")

rc, out = settle_arm(healthy, table=table, env_extra={"HF_SETTLE_BYPASS": "1"})
check("HF_SETTLE_BYPASS=1 不是理由：既不放行，也不让判据「不看表」——照旧看见他期渲染并等到超时",
      rc == 1 and "不足" in out and "有他期渲染在跑" in out, f"rc={rc} out={out[-400:]}")
rc, out = settle_arm(healthy, env_extra={"HF_SETTLE_BYPASS": "1"})
check("理由不合格也不是硬拒：机器真的静着时，它退回正常判据后仍然放行（否则空转的旁路会把静机器也卡死）",
      rc == 0 and "不足" in out and "✅" in out, f"rc={rc} out={out[-400:]}")
rc, out = settle_arm(healthy, table=table, env_extra={
    "HF_SETTLE_BYPASS": "单期重渲，已人工确认这台机器上没有任何渲染在跑"})
check("写了真理由才放行，且理由被打印（例外落在盘上，不落在对话里）",
      rc == 0 and "已跳过渲前串行化判据" in out and "单期重渲" in out, f"rc={rc} out={out[-300:]}")
kill(busy)

# =========================================================================
# 4) npx 缓存互斥锁
# =========================================================================
print("\n[4] render_lock_acquire / release：这段时间的 npx 缓存归谁")

trace = os.path.join(FIXDIR, "critical.trace")

# 证人取**区间重叠**，不取共享计数器。上一版用「在区里读到几个」，非空转那条赌的是两个进程
# 恰好同时起来——2026-10-09 11:36 实测赌输过一次：两条都读到 1，peak 也是 1，看着和互斥一模一样。
# 现在先在 trace 里报到、等两个都到齐再一起冲进锁，于是「同一把锁」必须判不出重叠、
# 「两把锁」必须判得出重叠，两边都不靠运气。时间戳用 python 取小数，因为 macOS 的
# `date +%s.%N` 不认 %N（它会打印出字面的 `.N`），而整秒会把 2 秒的区间错开成「刚好相接」。
TS_FN = 'ts() { python3 -c \'import time; print("%.3f" % time.time())\'; }\n'


def contender(lock, tag):
    return (
        "set -u\n"
        f'export HF_SOURCE_ONLY=1 HF_RENDER_LOCK_DIR="{lock}" HF_LOCK_POLL_SECONDS=1 HF_LOCK_MAX_WAIT=40\n'
        f"source {SCRIPT}\n"
        + TS_FN +
        f'echo "READY {tag} $$" >> "{trace}"\n'
        f'for _ in $(seq 1 80); do [ "$(grep -c "^READY" "{trace}")" -ge 2 ] && break; sleep 0.1; done\n'
        f'render_lock_acquire "测试临界区 {tag}" || {{ echo ACQ_FAIL; exit 1; }}\n'
        f'echo "BEGIN {tag} $(ts) $$" >> "{trace}"\n'
        "sleep 2\n"
        f'echo "END {tag} $(ts) $$" >> "{trace}"\n'
        "render_lock_release\n"
    )


def run_contenders(jobs):
    """jobs = [(lock, tag)]，同一份 trace，并发跑。返回 ([(rc, stdout)], 重叠对数, 标签)。"""
    with open(trace, "w", encoding="utf-8") as fh:
        fh.write("")
    procs = [subprocess.Popen(["bash", "-c", contender(lock, tag)], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace")
             for lock, tag in jobs]
    res = [(pr.wait(), pr.stdout.read()) for pr in procs]
    ev = {}
    for ln in open(trace, encoding="utf-8"):
        parts = ln.split()
        if len(parts) >= 3 and parts[0] in ("BEGIN", "END"):
            ev.setdefault(parts[1], {})[parts[0]] = float(parts[2])
    tags = sorted(t for t in ev if "BEGIN" in ev[t] and "END" in ev[t])
    pairs = sum(1 for i in range(len(tags)) for j in range(i + 1, len(tags))
                if ev[tags[i]]["BEGIN"] < ev[tags[j]]["END"]
                and ev[tags[j]]["BEGIN"] < ev[tags[i]]["END"])
    return res, pairs, tags


s_lock = os.path.join(FIXDIR, "shared.lock")
shutil.rmtree(s_lock, ignore_errors=True)
res, pairs, tags = run_contenders([(s_lock, "A"), (s_lock, "B")])
check("两个批同时抢同一把锁：两条临界区**不重叠**（区间证人，不是计数猜）",
      all(rc == 0 for rc, _ in res) and len(tags) == 2 and pairs == 0,
      f"rc={[r[0] for r in res]} 标签={tags} 重叠={pairs}")
check("而且是真的抢过：后到的那条打印过「已等 …s，锁被占」（否则不重叠可能只是启动顺序的运气）",
      any("锁被占" in txt for _, txt in res), f"输出={[(r[0], r[1][:80]) for r in res]}")

l1 = os.path.join(FIXDIR, "sep1.lock")
l2 = os.path.join(FIXDIR, "sep2.lock")
shutil.rmtree(l1, ignore_errors=True)
shutil.rmtree(l2, ignore_errors=True)
res, pairs, tags = run_contenders([(l1, "A"), (l2, "B")])
check("对照（非空转）：同一份证人、同样的两道杠，不共享锁时必须看到重叠——这一条红就说明上一条是白判",
      len(tags) == 2 and pairs == 1,
      f"标签={tags} 重叠={pairs} rc={[r[0] for r in res]}")

dead = spawn([sys.executable, "-c", "import time; time.sleep(30)"])
dead_pid = dead.pid
kill(dead)
time.sleep(0.5)
stale = os.path.join(FIXDIR, "stale.lock")
shutil.rmtree(stale, ignore_errors=True)
os.makedirs(stale)
with open(os.path.join(stale, "owner"), "w", encoding="utf-8") as fh:
    fh.write(f"{dead_pid}\t上一代留下的\n2026-01-01 00:00:00\n")
t0 = time.time()
rc, out = sh('render_lock_acquire "接管测试" && render_lock_release',
             env={"HF_RENDER_LOCK_DIR": stale, "HF_LOCK_POLL_SECONDS": "1",
                  "HF_LOCK_MAX_WAIT": "20"})
check("持有者进程已不在的陈旧锁被接管（一次崩溃不该堵死后面所有期）",
      rc == 0 and "接管陈旧锁" in out and time.time() - t0 < 10, f"rc={rc} out={out[:300]}")

live = spawn([sys.executable, "-c", "import time; time.sleep(90)"])
held = os.path.join(FIXDIR, "held.lock")
shutil.rmtree(held, ignore_errors=True)
os.makedirs(held)
with open(os.path.join(held, "owner"), "w", encoding="utf-8") as fh:
    fh.write(f"{live.pid}\t别期的批\t2026-10-09 00:00:00\n")
t0 = time.time()
rc, out = sh('render_lock_acquire "拿不到测试"',
             env={"HF_RENDER_LOCK_DIR": held, "HF_LOCK_POLL_SECONDS": "1",
                  "HF_LOCK_MAX_WAIT": "4"})
check("锁被活人占着 → 到 HF_LOCK_MAX_WAIT 就返回红，不无限等",
      rc == 1 and "没拿到渲染锁" in out and time.time() - t0 < 20, f"rc={rc} out={out[:300]}")
check("红话里要说出现是谁占着（否则下一个会话只能瞎猜）", str(live.pid) in out, out[:300])

cycle = os.path.join(FIXDIR, "cycle.lock")
shutil.rmtree(cycle, ignore_errors=True)
rc, out = sh('render_lock_acquire "第一段" && cat "$RENDER_LOCK_DIR/owner" && render_lock_release '
             '&& render_lock_acquire "第二段" && render_lock_release',
             env={"HF_RENDER_LOCK_DIR": cycle})
check("acquire→release→再 acquire 走得通，且 owner 里写着 pid＋标签＋时间（人读得懂谁在占）",
      rc == 0 and "第一段" in out and re.search(r"^\d+\t", out, re.M) is not None,
      f"rc={rc} out={out[:300]}")
check("release 之后锁目录真的没了（不是只删 owner 文件）",
      not os.path.isdir(cycle), "")

kill(live)
shutil.rmtree(held, ignore_errors=True)

# =========================================================================
# 5) 接线：判据得真的在生产路径上被调用，而不是躺在文件里
# =========================================================================
print("\n[5] 接线与形状")

src = open(os.path.join(ROOT, SCRIPT), encoding="utf-8").read()

i_settle = src.find('settle_before_render "$EPISODE"')
i_preview = src.find("node scripts/create-episode-preview.mjs")
check("settle 在每期循环里被**调用**，且排在生成预览目录之前",
      0 < i_settle < i_preview, f"settle={i_settle} preview={i_preview}")

i_acq = src.find('render_lock_acquire "$ep/$seg')
i_npm = src.find('npm run render 2>&1')
i_rel = src.find("render_lock_release", i_npm)
check("npm run render 被 acquire/release 夹住（锁的覆盖面就是 npx 那棵 _npx 树）",
      0 < i_acq < i_npm < i_rel, f"acq={i_acq} npm={i_npm} rel={i_rel}")

# 「被记过的形状不许回来」这一类只能扫**代码行**：本文件的注释必须点名被撤回的那个路径
# （/private/tmp/npm-cache、`ps -o args=`），否则读代码的人不知道为什么要绕开它。用整文件
# includes() 去扫会把说明本身判红——这条仓库在闪屏守卫上已经写过同一条教训。
code = "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))

cache_default = re.search(r'NPM_CACHE_DIR="\$\{HF_NPM_CACHE_DIR:(-[^}]+)\}"', src)
check("npm 缓存的默认值钉在仓库内（全局 /private/tmp 那份就是 2026-10-08 ENOTEMPTY 的现场），"
      "且真的被 export 给渲染用的 npm",
      bool(cache_default) and cache_default.group(1).lstrip("-").startswith("$ROOT/tmp/")
      and 'export npm_config_cache="$NPM_CACHE_DIR"' in code,
      cache_default.group(0) if cache_default else "找不到 NPM_CACHE_DIR=\"${HF_NPM_CACHE_DIR:-…}\"")
check("被记过的形状不许回来：代码行里不再出现 /private/tmp/npm-cache（注释里点名它可以）",
      "/private/tmp/npm-cache" not in code, "")

for token in ("check-water-ripple.py", "lib/book-cover.json", "lib/book_cover.py",
              "check-book-cover-overlay.py", "render_scene_map_template_fallback.py",
              "create-episode-preview.mjs"):
    check(f"WATCH_FILES 里还列着 {token}", token in src, "")

check("忙判据用 comm（可执行体）", 'ps -o comm=' in code, "")
check("被记过的形状不许回来：代码行里不用 ps -o args= 去匹配书名", "ps -o args=" not in code, "")
check("HF_SOURCE_ONLY 只是测试缝合，不是渲染旁路（批循环仍在同一个文件里）",
      'if [ -n "${HF_SOURCE_ONLY:-}" ]' in src and 'for EPISODE in "$@"' in src, "")

withdrawn = src.replace('NPM_CACHE_DIR="${HF_NPM_CACHE_DIR:-$ROOT/tmp/npm-cache-hf}"',
                        'NPM_CACHE_DIR="${HF_NPM_CACHE_DIR:-/private/tmp/npm-cache}"')
m2 = re.search(r'NPM_CACHE_DIR="\$\{HF_NPM_CACHE_DIR:(-[^}]+)\}"', withdrawn)
check("缓存那条断言不是空转：把 /private/tmp 注回默认值，同一个谓词必须判红",
      bool(m2) and not m2.group(1).lstrip("-").startswith("$ROOT/tmp/"), "")
withdrawn2 = src.replace('cm="$(ps -o comm= -p "$pid" 2>/dev/null)"',
                         'cm="$(ps -o args= -p "$pid" 2>/dev/null)"')
code2 = "\n".join(ln for ln in withdrawn2.split("\n") if not ln.strip().startswith("#"))
check("comm 那条断言不是空转：注回 ps -o args= 必须被自己的规则抓到",
      "ps -o args=" in code2 and "ps -o args=" not in code, "")
# 这里**不再**加一条「全角标点前的裸 $变量」扫描：check.mjs 已经有一份通用的（扫所有 scripts/*.sh
# 并自带灌回自检），在测试里再抄一遍就是「四个地方用不同措辞问同一个问题」那类分叉。
# 2026-10-09 那条 `$owner）` 炸的是**运行时**，而上面「陈旧锁被接管」那条正例跑的就是那一行——
# 它红即说明形状回来了。


# =========================================================================
# 6) render_lock_run：`npm run check` 的那三个 npx 也走同一把锁
# =========================================================================
# 渲染不是唯一碰 _npx 树的调用点：check.mjs 跑 `npx --yes hyperframes@… lint/validate/inspect`，
# 用的是**同一棵**树。只在渲染侧排队等于修了一半——一边排队、一边直冲，13:13《昆虫记》那六次
# ENOTEMPTY 照旧。协议仍然只有一份实现（这个函数），Node 侧只 source 再调用。
print("\n[6] render_lock_run：check 侧的 npx 也在锁里")

LR_DIR = os.path.join(FIXDIR, "locked-run")
shutil.rmtree(LR_DIR, ignore_errors=True)
os.makedirs(LR_DIR)

rc, out = sh(f'render_lock_run "探针/健康" "{LR_DIR}" pwd',
             env={"HF_RENDER_LOCK_DIR": os.path.join(FIXDIR, "lr.lock")})
check("跑通了，而且 cd 到调用方给的目录（source 里那句 cd ROOT 不许漏给被包命令）",
      rc == 0 and out.strip() == LR_DIR, f"rc={rc} out={out!r} want={LR_DIR!r}")
check("跑完锁目录真的没了",
      not os.path.isdir(os.path.join(FIXDIR, "lr.lock")), "残留 tmp/locked-run 的锁目录")

rc, out = sh(f'render_lock_run "探针/退码" "{LR_DIR}" sh -c "exit 3"',
             env={"HF_RENDER_LOCK_DIR": os.path.join(FIXDIR, "lr2.lock")})
check("被包命令的退出码原样透传（3 而不是 0 也不是 75）", rc == 3, f"rc={rc} out={out[-200:]}")

rc, out = sh(f'render_lock_run "探针/缓存" "{LR_DIR}" sh -c \'printf %s "$npm_config_cache"\'',
             env={"HF_RENDER_LOCK_DIR": os.path.join(FIXDIR, "lr3.lock")})
check("锁里跑的命令继承**仓库内** npm 缓存（source 带出来的 export，这就是排队生效的机制）",
      rc == 0 and out.strip().endswith("/tmp/npm-cache-hf"), f"rc={rc} out={out[-200:]!r}")

busy_lr = os.path.join(FIXDIR, "lr-busy.lock")
shutil.rmtree(busy_lr, ignore_errors=True)
os.makedirs(busy_lr)
marker = os.path.join(LR_DIR, "ran.marker")
if os.path.exists(marker):
    os.remove(marker)
holder = spawn([sys.executable, "-c", "import time; time.sleep(60)"])
with open(os.path.join(busy_lr, "owner"), "w", encoding="utf-8") as fh:
    fh.write(f"{holder.pid}\t探针占位\t2026-10-09 00:00:00\t{ROOT}\n")
rc, out = sh(f'render_lock_run "探针/抢不到" "{LR_DIR}" sh -c "touch {marker}"',
             env={"HF_RENDER_LOCK_DIR": busy_lr, "HF_LOCK_POLL_SECONDS": "1",
                  "HF_LOCK_MAX_WAIT": "3"})
check("活人占着锁 → 75（调用方要把它和「命令自己失败」分开报，否则别人在渲就伪装成模板有问题）",
      rc == 75 and "没拿到渲染锁" in out, f"rc={rc} out={out[-260:]}")
check("75 那条**没有**偷跑被包命令（锁没拿到就不能碰那棵树）",
      not os.path.exists(marker), "marker 存在 = 没拿到锁也跑了")
kill(holder)
shutil.rmtree(busy_lr, ignore_errors=True)

# 接线：check.mjs 必须走这把锁，而不是自己写一份 mkdir 协议，也不许留着裸 run("npx")。
mjs = open(os.path.join(ROOT, "scripts/check.mjs"), encoding="utf-8").read()
mjs_code = "\n".join(ln for ln in mjs.split("\n") if not ln.strip().startswith("//"))
check("check.mjs 复用 render_lock_run（协议只有驱动里那一份实现，Node 侧不重抄）",
      "render_lock_run" in mjs_code and "runLocked" in mjs_code, "")
check("被记过的形状不许回来：check.mjs 的代码行里没有裸 run(\"npx\") 直冲那棵树（注释里点名它可以）",
      'run("npx"' not in mjs_code, "")
deject = mjs_code.replace('runLocked(`npm run check: hyperframes ${command}`, "npx", args, templateDir,',
                          'run("npx", args, templateDir,')
check("上面那条不是空转：把裸 npx 注回同一个谓词，它必须抓到", 'run("npx"' in deject, "")

# 顺序陷阱（2026-10-09 探针实测到的一次真错）：驱动在**顶层**就把 HF_LOCK_MAX_WAIT 读进
# LOCK_MAX_WAIT，所以 source 之后再 export 它是一句死代码——等待上限会退回默认 1800s，
# 一次「别人正在渲」把整条 npm run check 挂半小时，而没人知道自己为什么在等。
rl_body = re.search(r"function runLocked\([\s\S]*?\n\}", mjs)
rl = rl_body.group(0) if rl_body else ""
i_export, i_source = rl.find("export HF_LOCK_MAX_WAIT"), rl.find("source scripts/render-hook-batch.sh")
check("HF_LOCK_MAX_WAIT 在 source **之前**导出（之后导出等于没导出）",
      0 <= i_export < i_source, f"export={i_export} source={i_source}")
swapped_lines = rl.split("\n")
ie = next((i for i, l in enumerate(swapped_lines) if "export HF_LOCK_MAX_WAIT" in l), None)
isrc = next((i for i, l in enumerate(swapped_lines) if "source scripts/render-hook-batch.sh" in l), None)
# 按**整行**对调，不按子串 replace：这两句在 JS 里各自是带引号和 `\n` 转义的一段字符串，
# 拿「bash 里的样子」去 replace 匹配不到任何东西，decoy 会静默变成原文——那正是
# 「灌回被撤回的形状仍然判绿」这种空转自检的诞生方式（2026-10-09 第一次写就踩中了）。
if ie is not None and isrc is not None and ie < isrc:
    swapped_lines[ie], swapped_lines[isrc] = swapped_lines[isrc], swapped_lines[ie]
swapped = "\n".join(swapped_lines)
s_exp, s_src = swapped.find("export HF_LOCK_MAX_WAIT"), swapped.find("source scripts/render-hook-batch.sh")
check("顺序那条断言不是空转：把 export 那一行整行挪到 source 之后，同一个谓词必须判红",
      ie is not None and isrc is not None and not (0 <= s_exp < s_src),
      f"ie={ie} isrc={isrc} decoy export={s_exp} source={s_src}")


# =========================================================================
print("\n—— 清理 ——")
for p in SPAWNED:
    kill(p)
shutil.rmtree(FIXDIR, ignore_errors=True)

print(f"\n通过 {PASSED} 条，失败 {len(FAILED)} 条")
if FAILED:
    print("失败清单：\n  - " + "\n  - ".join(FAILED))
    sys.exit(1)
print("✅ 渲前串行化判据全部断言通过")
