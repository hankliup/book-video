#!/usr/bin/env python3
"""图片**段归属**的路由回归：一份整期清单绝不能被环境变量搬到另一个段。

为什么钉这一条（2026-10-06《猎人笔记》）：`data/<书名>_pexels_ids.json` 记的是整期选型
（这期是钩子 2 张 + 正文 16 张），而钩子段出片前那一步准备脚本写的是
`IMAGES_SUBDIR=images-hook FORCE=1 python3 scripts/fetch-pexels-by-id.py episodes/<书名> data/<书名>_pexels_ids.json`。
当时的路由链是：正文条目没有 `dir` → `dirs_for()` 落到 `body_images_dir()` → 那个函数**认
IMAGES_SUBDIR** → 于是 16 张正文图被逐张按 FORCE 重下，原始亮度落进 `images-hook/`，
把真正的两张钩子图盖掉了；而后面那条 `IMAGES_SUBDIR=images-hook grade` 只调钩子目录，
正文集还顺带丢了调色。两道门禁都看不见：目录里确实有文件、尺寸对、溯源行也在，
只是那两张钩子拍已经不是钩子拍了。

修法分三层，这里三层都要判：
  ① `lib/episode_paths.segment_subdir()` —— 段归属只由条目自己决定，`IMAGES_SUBDIR`
     不许把任何一条正文图路由进钩子目录（对照：不带 env 时历史期仍回 `images`，
     以免这条断言只在「目录恰好叫 images-body」时成立）。
  ② `lock-pexels-candidates.py` —— 锁的时候**无条件**写 `dir` + `segment`，
     归属不许留给下游反推。跑一次真脚本验它。
  ③ `fetch-pexels-by-id.py` 只按条目路由、并有 `--segment` 这道「本轮不动另一段」的闸；
     `prepare-hook-segment.sh` 必须带上它。
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib import episode_paths  # noqa: E402

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: 期望 {want!r}，实得 {got!r}")
        print(f"  ❌ {label}: 期望 {want!r}，实得 {got!r}")
    else:
        print(f"  ✅ {label}: {got!r}")


def subdir(item, ep, env=None):
    """在指定 IMAGES_SUBDIR 下问一次路由；env=None 表示这个开关根本没设。"""
    old = os.environ.get("IMAGES_SUBDIR")
    if env is None:
        os.environ.pop("IMAGES_SUBDIR", None)
    else:
        os.environ["IMAGES_SUBDIR"] = env
    try:
        return episode_paths.segment_subdir(ep, item)
    finally:
        os.environ.pop("IMAGES_SUBDIR", None)
        if old is not None:
            os.environ["IMAGES_SUBDIR"] = old


print("① segment_subdir：钩子段的开关不改写正文条目的归属")
NEW = os.path.join(HERE, "_routing_probe_new")
LEGACY = os.path.join(HERE, "_routing_probe_legacy")
os.makedirs(os.path.join(NEW, "images-body"), exist_ok=True)
os.makedirs(os.path.join(LEGACY, "images"), exist_ok=True)
try:
    BODY_DIR = {"name": "atmosphere-2", "dir": "images-body", "segment": "body"}
    BODY_SEG = {"name": "atmosphere-3", "segment": "body"}
    BODY_BARE = {"name": "atmosphere-4"}                      # 旧清单：段归属没写
    HOOK_SEG = {"name": "atmosphere-1", "segment": "hook"}
    HOOK_DIR = {"name": "atmosphere-2", "dir": "images-hook"}
    # 这次事故的正文侧：IMAGES_SUBDIR=images-hook 时，三种正文写法都必须留在 images-body。
    for label, item in (("写了 dir 的正文", BODY_DIR),
                        ("写了 segment=body 的正文", BODY_SEG),
                        ("什么都没写的正文（旧清单）", BODY_BARE)):
        check(f"IMAGES_SUBDIR=images-hook 时 {label}",
              subdir(item, NEW, "images-hook"), "images-body")
    # 钩子侧照常进钩子目录：这条是正对照，缺了它上面三条「都留正文目录」可能只是函数坏了。
    for label, item in (("segment=hook", HOOK_SEG), ("dir=images-hook", HOOK_DIR)):
        check(f"IMAGES_SUBDIR=images-hook 时 {label}",
              subdir(item, NEW, "images-hook"), "images-hook")
    # 没设开关时正文照常、历史期回 images：证明上面的「留在 images-body」来自探测而不是硬编码。
    check("不设 IMAGES_SUBDIR 的新期正文", subdir(BODY_BARE, NEW, None), "images-body")
    check("不设 IMAGES_SUBDIR 的历史期正文", subdir(BODY_BARE, LEGACY, None), "images")
    # 历史期（只有 images/）才是这轮钩子回补的主体：同一把开关也必须搬不动它们的正文图。
    check("IMAGES_SUBDIR=images-hook 时的历史期正文", subdir(BODY_BARE, LEGACY, "images-hook"), "images")
    check("IMAGES_SUBDIR=images-hook 时的历史期钩子", subdir(HOOK_SEG, LEGACY, "images-hook"), "images-hook")
    check("IMAGES_SUBDIR=images（正文段自己）", subdir(BODY_BARE, NEW, "images"), "images")
    # 反面对照：事故当时代码走的正是「fetch → body_images_dir → body_images_subdir」这条链，
    # 那个解析器认 IMAGES_SUBDIR（这对 grade / fix-bright-regions「这一段一次调完」是对的），
    # 所以把它当**条目路由**用时，钩子段的开关就整份改道了。量出旧链会落 images-hook，
    # 上面那三行「留在 images-body」才是 segment_subdir 在做功，而不是目录探测恰好没撞上。
    old = os.environ.get("IMAGES_SUBDIR")
    os.environ["IMAGES_SUBDIR"] = "images-hook"
    try:
        check("旧路由链（body_images_subdir）在 images-hook 开关下的落点",
              episode_paths.body_images_subdir(NEW), "images-hook")
    finally:
        os.environ.pop("IMAGES_SUBDIR", None)
        if old is not None:
            os.environ["IMAGES_SUBDIR"] = old
finally:
    for d in (NEW, LEGACY):
        shutil.rmtree(d, ignore_errors=True)

print("② lock-pexels-candidates.py：锁这一步把归属写死在清单里")
WORK = os.path.join(ROOT, "tmp", "segment-routing-test")
shutil.rmtree(WORK, ignore_errors=True)
try:
    os.makedirs(os.path.join(WORK, "b1"), exist_ok=True)
    os.makedirs(os.path.join(WORK, "h1"), exist_ok=True)
    # 两个候选目录，一张正文一张钩子；id 用一个从未上屏的假 id，跨期那道网自然放行。
    for d, pid in (("b1", 999999901), ("h1", 999999902)):
        json.dump([{"id": pid, "photographer": "tester", "url": f"https://pexels.com/photo/{pid}",
                    "image_url": f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg",
                    "pixels": "1000x1333", "avg_color": "#777777", "alt": "routing fixture"}],
                  open(os.path.join(WORK, d, "cand.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
    picks = [
        {"name": "atmosphere-1", "beat": os.path.join(WORK, "b1"), "cell": 0,
         "note": "正文段：故意不写 dir/segment，验锁这一步会不会补上"},
        {"name": "atmosphere-1", "beat": os.path.join(WORK, "h1"), "cell": 0, "dir": "images-hook",
         "note": "钩子段"},
    ]
    picks_path = os.path.join(WORK, "picks.json")
    manifest_path = os.path.join(WORK, "manifest.json")
    json.dump(picks, open(picks_path, "w", encoding="utf-8"), ensure_ascii=False)
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "lock-pexels-candidates.py"),
                           "_routing_test_", picks_path, "--apply", "--out", manifest_path],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        FAILS.append("lock 脚本没跑成（exit %s）：%s" % (proc.returncode, proc.stderr[-400:]))
        print(f"  ❌ lock 脚本 exit {proc.returncode}\n{proc.stdout[-600:]}{proc.stderr[-600:]}")
    else:
        locked = json.load(open(manifest_path, encoding="utf-8"))
        by_seg = {(x.get("name"), x.get("dir")): x for x in locked}
        check("锁完的条目数", len(locked), 2)
        body = next((x for x in locked if x.get("dir") == "images-body"), None)
        hook = next((x for x in locked if x.get("dir") == "images-hook"), None)
        check("picks 没写 dir 的正文条目也拿到了 dir", body is not None, True)
        check("picks 没写 dir 的正文条目 segment", (body or {}).get("segment"), "body")
        check("钩子条目 segment", (hook or {}).get("segment"), "hook")
        check("钩子条目仍落在 images-hook", by_seg.get(("atmosphere-1", "images-hook")) is not None, True)
        # 负对照：把这条清单交给「按条目路由」的 fetch，IMAGES_SUBDIR=images-hook 也一条不落钩子目录。
        if body:
            probe = os.path.join(WORK, "episodes")
            os.makedirs(os.path.join(probe, "images-body"), exist_ok=True)
            os.environ["IMAGES_SUBDIR"] = "images-hook"
            try:
                check("重放：正文条目在 images-hook 开关下的落点",
                      episode_paths.segment_subdir(probe, body), "images-body")
                check("重放：钩子条目在 images-hook 开关下的落点",
                      episode_paths.segment_subdir(probe, hook), "images-hook")
            finally:
                os.environ.pop("IMAGES_SUBDIR", None)
finally:
    shutil.rmtree(WORK, ignore_errors=True)
    stray = os.path.join(ROOT, "data", "_routing_test__pexels_ids.json")
    if os.path.exists(stray):
        os.remove(stray)

print("③ 静态：fetch 按条目路由 + 钩子准备脚本只取钩子段")
GUARDS = [
    ("scripts/fetch-pexels-by-id.py", ["from episode_paths import", "segment_subdir",
                                       "--segment=", "ONLY_SEGMENT"]),
    ("scripts/lock-pexels-candidates.py", ['entry["dir"] = sub', 'entry["segment"] =']),
    ("scripts/prepare-hook-segment.sh", ["--segment=hook"]),
]
for rel, tokens in GUARDS:
    src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    for tok in tokens:
        if tok not in src:
            FAILS.append(f"{rel} 不再包含 {tok}")
            print(f"  ❌ {rel} 不再包含 {tok}")
        else:
            print(f"  ✅ {rel} 引用 {tok}")

# 「无条件写」必须是真无条件：`if sub:` 那一档回到 lock 脚本，正文条目就又可能没有 dir，
# 于是「这次处理钩子段」的环境开关重新获得改写正文路由的能力——①②两条断言也就跟着失效。
lock_src = open(os.path.join(ROOT, "scripts", "lock-pexels-candidates.py"), encoding="utf-8").read()
guarded = [i for i, line in enumerate(lock_src.splitlines(), 1)
           if line.strip().startswith("if sub:") and 'entry["dir"]' in
           "\n".join(lock_src.splitlines()[i:i + 3])]
if guarded:
    FAILS.append(f"lock-pexels-candidates.py 第 {guarded} 行又用 `if sub:` 兜住了段归属")
    print(f"  ❌ lock 脚本第 {guarded} 行：`if sub:` 回来了（正文条目可能又不写 dir）")
else:
    print("  ✅ lock 脚本无条件写 dir/segment（`if sub:` 没有回来）")

# fetch 的循环体不能再退回「整期共用一个 images_dir」：dirs_for 必须仍在按条目算。
fetch_src = open(os.path.join(ROOT, "scripts", "fetch-pexels-by-id.py"), encoding="utf-8").read()
if "images_dir, raw_dir, original_dir = dirs_for(item)" not in fetch_src:
    FAILS.append("fetch-pexels-by-id.py 不再按条目调用 dirs_for(item)")
    print("  ❌ fetch 循环没有按条目 dirs_for(item)")
else:
    print("  ✅ fetch 循环按条目 dirs_for(item)")

if FAILS:
    print(f"\n❌ {len(FAILS)} 项未通过")
    for f in FAILS:
        print(f"   - {f}")
    sys.exit(1)
print("\n✅ 段归属路由：三层全部按设计判红/判绿")
