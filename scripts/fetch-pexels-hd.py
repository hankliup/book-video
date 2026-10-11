#!/usr/bin/env python3
"""从 Pexels 抓高清氛围图，Pillow 居中裁 3:4 → 1024x1365。

与 fetch-pexels-curl.py 的区别（关键）：
  旧脚本下载 photo.src.portrait / large2x，实测被限宽 —— 原图 4000px 只返回
  ~730-940px 宽，放大到 1024 会发虚（钢铁 08-31 首次遇到）。
  这里改用 photo.src.original + "?auto=compress&cs=tinysrgb&h=2000"（按高度
  2000px 请求，宽度自然满足），再本地裁切，清晰度有保证。

用法: python3 scripts/fetch-pexels-hd.py <episodeDir> <queries.json>
queries.json: [{"index":1,"label":"瓦尔海姆夏日","query":"wheat field summer ..."}, ...]

依赖: Pillow（本机装在 /Users/apple/.workbuddy/binaries/python/envs/default）
"""
import io
import json
import os
import re
import subprocess
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import pexels_curl  # noqa: E402

TARGET_W, TARGET_H = 1024, 1365  # 3:4，与 720x960 渲染帧同比例

ep_dir, queries_file = sys.argv[1], sys.argv[2]
repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
key = None
for line in open(os.path.join(repo, ".env"), encoding="utf-8"):
    m = re.match(r"^PEXELS_API_KEY=(.*)$", line.strip())
    if m:
        key = m.group(1).strip().strip('"').strip("'")
if not key:
    sys.exit("Missing PEXELS_API_KEY in .env")

images_dir = os.path.join(ep_dir, "images")
os.makedirs(images_dir, exist_ok=True)
queries = json.load(open(queries_file, encoding="utf-8"))


def curl(url, binary=False):
    # 密钥不进 curl 的 argv：鉴权头走 stdin 配置（scripts/lib/pexels_curl.py）。
    # 以前这里把 -H "Authorization: <key>" 拼进命令行，同机任何进程 ps 一眼看到，
    # 而 check=True 的异常、任何打印整条命令的日志又都会把它带走。
    r = pexels_curl.run(["-sSL", "--max-time", "120"], url, key)
    if r.returncode != 0:
        detail = r.stderr.decode("utf-8", "replace")
        raise SystemExit(f"curl {r.returncode} 失败: {url.split('?')[0]}: {detail.replace(key, '***')}")
    return r.stdout if binary else json.loads(r.stdout)


def crop_3x4(img):
    """居中裁成 3:4 再缩放到目标尺寸，避免拉伸变形。"""
    w, h = img.size
    th = int(w * TARGET_H / TARGET_W)  # 目标比例下应有的高度
    if h >= th:
        top = (h - th) // 2
        img = img.crop((0, top, w, top + th))
    else:  # 图太宽，按比例裁宽
        tw = int(h * TARGET_W / TARGET_H)
        left = (w - tw) // 2
        img = img.crop((left, 0, left + tw, h))
    return img.resize((TARGET_W, TARGET_H), Image.LANCZOS)


used, ok = set(), 0
for i, item in enumerate(queries, start=1):
    idx, label = item.get("index", i), item.get("label", "")
    name = item.get("name", f"atmosphere-{idx}")
    target = os.path.join(images_dir, f"{name}.png")
    if os.path.exists(target):
        print(f"  [{idx}] {label} — 已存在，跳过")
        ok += 1
        continue
    q = item["query"].replace(" ", "%20")
    data = curl(f"https://api.pexels.com/v1/search?query={q}&orientation=portrait&per_page=15")
    photo = next((p for p in data.get("photos", []) if p["id"] not in used), None)
    if not photo:
        print(f"  [{idx}] {label} 无可用结果")
        continue
    used.add(photo["id"])
    # 关键：用 original + 按高度 2000px 请求，绕开 portrait/large2x 的限宽
    src = photo["src"]["original"].split("?")[0] + "?auto=compress&cs=tinysrgb&h=2000"
    raw = curl(src, binary=True)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    crop_3x4(img).save(target)
    w0, h0 = Image.open(io.BytesIO(raw)).size
    print(f"  [{idx}] {label} ← {item['query']} (pexels #{photo['id']}, {photo['photographer']}) 源图 {w0}x{h0}")
    ok += 1

print(f"\n完成 {ok}/{len(queries)}")
sys.exit(0 if ok == len(queries) else 1)
