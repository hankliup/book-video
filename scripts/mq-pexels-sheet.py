#!/usr/bin/env python3
"""只读检索 Pexels 并把候选拼成带编号的 contact sheet，供人工挑图。

为什么不用 fetch-pexels-hd.py 的首条结果：同一关键词每次返回顺序不同，且首条经常
带水印/文字/人脸抢焦点/顶部过亮（白字书名会被吃掉）。本脚本只下载缩略图到 tmp/，
不写 episodes/，挑定后再用 scripts/fetch-pexels-by-id.py 按 ID 取原图。

用法:
  python3 scripts/mq-pexels-sheet.py [--cache <dir>] <out.png> "<query1>" "<query2>" ...

  --cache <dir>：按关键词存一份检索结果 JSON，命中就复用、不再打 API。一台机器上
  常有两条会话同时选题（共用同一个 Pexels key，额度是共享的，撞限流时整张表要重跑
  几十分钟），有了缓存就能续跑：失败的表重跑时只做没查过的那几个词。缓存文件名带
  关键词哈希，不同会话查同一个词能互相复用。
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont

SEARCH = os.path.expanduser("~/.workbuddy/skills/nook-pexels/scripts/pexels_search.py")
PER_PAGE = 8
CELL_W, THUMB_H, LABEL_H = 200, 267, 46  # 3:4 竖构图缩略

argv = sys.argv[1:]
cache_dir = None
if argv[:1] == ["--cache"]:
    if len(argv) < 2:
        sys.exit("--cache 需要一个目录参数")
    cache_dir, argv = argv[1], argv[2:]
    os.makedirs(cache_dir, exist_ok=True)

if not argv:
    sys.exit("用法: mq-pexels-sheet.py [--cache <dir>] <out.png> \"<query1>\" ...")
out_path = argv[0]
queries = argv[1:]
if not queries:
    sys.exit("need at least one query")

rows = []
# /tmp/mq_px_<n>.json 是本脚本的中间文件。同一个仓库常有多条会话并发检索（另一条会话
# 也在跑本脚本时，写死文件名会让两边互相读到对方的结果，选出来的图与关键词对不上），
# 所以中间文件按进程号隔离。
RUN_TAG = str(os.getpid())
for qi, q in enumerate(queries):
    tmp_json = f"/tmp/mq_px_{RUN_TAG}_{qi}.json"
    cached = None
    if cache_dir:
        cached = os.path.join(
            cache_dir, hashlib.sha1(q.encode("utf-8")).hexdigest()[:16] + ".json")
    if cached and os.path.exists(cached):
        print(f"[{qi}] {q} -> 复用缓存")
        shutil.copy(cached, tmp_json)
    elif os.path.exists(tmp_json):
        print(f"[{qi}] {q} -> 复用本次中间结果")
    else:
        # Pexels 免费 key 是限流额度（且常有另一条会话在同一台机器上并发检索）。
        # 429 时旧写法 check=True 直接抛 CalledProcessError，整张表白跑，还看不到原因
        # （capture_output 把「Too Many Requests」吞进了 stderr）。这里改成退避重试。
        for attempt in range(6):
            proc = subprocess.run(
                ["python3", SEARCH, "--type", "photo", "--query", q,
                 "--per-page", str(PER_PAGE), "--orientation", "portrait", "--out", tmp_json],
                capture_output=True, text=True,
            )
            if proc.returncode == 0 and os.path.isfile(tmp_json) and os.path.getsize(tmp_json) > 0:
                break
            blob = (proc.stdout or "") + (proc.stderr or "")
            if "429" not in blob and "Too Many Requests" not in blob:
                sys.exit(f"检索失败 [{qi}] {q}\n{blob.strip()[-500:]}")
            wait = 30 * (attempt + 1)
            print(f"[{qi}] 限流，{wait}s 后重试（第 {attempt + 1} 次）", flush=True)
            time.sleep(wait)
        else:
            sys.exit(f"[{qi}] {q} 连续限流，稍后带 --cache 重跑本表可跳过已查的词")
        if cached:
            shutil.copy(tmp_json, cached)   # 只缓存查成功的，失败的下次仍会重试
        time.sleep(2)  # 逐条检索之间留口，别把额度一口气打满
    data = json.load(open(tmp_json, encoding="utf-8"))
    for p in data.get("photos", []):
        src = p.get("src", {})
        rows.append({
            "query": q,
            "id": p["id"],
            "w": p.get("width"), "h": p.get("height"),
            "thumb": src.get("large2x") or src.get("medium") or src.get("original"),
            # 把锁号/取原图要用的元数据一起落到 sidecar：否则 lock-pexels-selection.py
            # 只能回落到 /tmp 中间文件或按 id 重查 API，而共享 key 一限流就是几十次调用
            # 全在退避重试（本期实测）。sidecar 自足之后选型阶段完全离线。
            "photographer": p.get("photographer", ""),
            "url": p.get("url", ""),
            "avg_color": p.get("avg_color", ""),
            "alt": p.get("alt", ""),
            "original": src.get("original", ""),
        })
    print(f"[{qi}] {q} -> {len(data.get('photos', []))}")

print(f"total candidates: {len(rows)}")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def center_crop(im, tw, th):
    scale = max(tw / im.width, th / im.height)
    im = im.resize((int(im.width * scale + 0.5), int(im.height * scale + 0.5)), Image.LANCZOS)
    x = (im.width - tw) // 2
    y = (im.height - th) // 2
    return im.crop((x, y, x + tw, y + th))


cols = 6
nrows = (len(rows) + cols - 1) // cols
sheet = Image.new("RGB", (cols * CELL_W, nrows * (THUMB_H + LABEL_H)), (18, 18, 18))
dr = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
except OSError:
    font = ImageFont.load_default()

for i, item in enumerate(rows):
    try:
        thumb = center_crop(fetch(item["thumb"]), CELL_W, THUMB_H)
    except Exception as exc:  # noqa: BLE001 - 缩略图失败只留占位，不阻断整表
        thumb = Image.new("RGB", (CELL_W, THUMB_H), (60, 20, 20))
        print(f"  ! #{i} {item['id']} {exc}")
    cx, cy = (i % cols) * CELL_W, (i // cols) * (THUMB_H + LABEL_H)
    sheet.paste(thumb, (cx, cy))
    dr.rectangle([cx, cy + THUMB_H, cx + CELL_W, cy + THUMB_H + LABEL_H], fill=(10, 10, 10))
    dr.text((cx + 4, cy + THUMB_H + 4), f"#{i}  id={item['id']}", fill=(240, 240, 240), font=font)
    dr.text((cx + 4, cy + THUMB_H + 22), item["query"][:26], fill=(150, 210, 150), font=font)

sheet.save(out_path)
json.dump(rows, open(os.path.splitext(out_path)[0] + ".json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"sheet -> {out_path}  ({sheet.width}x{sheet.height})")
