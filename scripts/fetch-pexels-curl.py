#!/usr/bin/env python3
"""通过 curl（走系统代理）从 Pexels 拉竖版氛围图并裁成 720x960。

用法: python3 fetch-pexels-curl.py <episodeDir> <queries.json>
Node 的 undici 不读 HTTP_PROXY，本沙箱必须走 curl。
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import pexels_curl  # noqa: E402

ep_dir, queries_file = sys.argv[1], sys.argv[2]
repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
env_path = os.path.join(repo, ".env")
key = None
for line in open(env_path, encoding="utf-8"):
    m = re.match(r"^PEXELS_API_KEY=(.*)$", line.strip())
    if m:
        key = m.group(1).strip().strip('"').strip("'")
if not key:
    sys.exit("Missing PEXELS_API_KEY in .env")

images_dir = os.path.join(ep_dir, "images")
os.makedirs(images_dir, exist_ok=True)
queries = json.load(open(queries_file, encoding="utf-8"))


def curl_json(url):
    # 鉴权头走 stdin 配置，不进 curl 的 argv（见 scripts/lib/pexels_curl.py）。
    r = pexels_curl.run(["-sS", "--max-time", "30"], url, key)
    # 依旧不用 check=True：它的异常带着整条 cmd 列表，Key 就进了终端/会话记录。
    if r.returncode != 0:
        raise SystemExit(f"curl {r.returncode} 失败: {url.split('?')[0]}: "
                         f"{r.stderr.decode('utf-8', 'replace').strip().replace(key, '***')}")
    return json.loads(r.stdout)


def curl_file(url, dest):
    subprocess.run(["curl", "-sSL", "--max-time", "120", "-o", dest, url], check=True)


used = set()
ok = 0
for i, item in enumerate(queries, start=1):
    idx = item.get("index", i)
    label = item.get("label", "")
    target = os.path.join(images_dir, f"atmosphere-{idx}.png")
    if os.path.exists(target):
        print(f"  [{idx}] {label} — 已存在，跳过")
        ok += 1
        continue
    q = item["query"].replace(" ", "%20")
    data = curl_json(
        f"https://api.pexels.com/v1/search?query={q}&orientation=portrait&per_page=10"
    )
    photo = next((p for p in data.get("photos", []) if p["id"] not in used), None)
    if not photo:
        print(f"  [{idx}] {label} 无可用结果")
        continue
    used.add(photo["id"])
    src = photo["src"].get("portrait") or photo["src"].get("large") or photo["src"]["original"]
    raw = os.path.join(images_dir, f".raw-{idx}.jpg")
    curl_file(src, raw)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
        "-vf", "scale=720:960:force_original_aspect_ratio=increase,crop=720:960",
        "-frames:v", "1", target,
    ], check=True)
    os.remove(raw)
    print(f"  [{idx}] {label} ← {item['query']} (pexels #{photo['id']}, {photo['photographer']})")
    ok += 1

print(f"\n完成 {ok}/{len(queries)}")
sys.exit(0 if ok == len(queries) else 1)
