#!/usr/bin/env python3
"""Proxy-aware Pexels fetcher for 百万英镑 atmosphere images.

Shells out to curl (honors http_proxy/https_proxy) for the API + downloads,
then ffmpeg crops each to 720x960 portrait. Saves atmosphere-N.png in order.
"""
import json
import os
import subprocess
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import pexels_curl  # noqa: E402

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EPISODE = "百万英镑"
IMAGES_DIR = os.path.join(ROOT, "episodes", EPISODE, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

KEY = os.environ.get("PEXELS_API_KEY") or open(os.path.join(ROOT, ".env")).read().split("PEXELS_API_KEY=")[1].split("\n")[0].strip()

queries = json.load(open(os.path.join(ROOT, "episodes", EPISODE, "pexels-queries.json"), encoding="utf-8"))

def curl_json(url, headers=None):
    """带鉴权取 JSON。headers 只接受非密钥头（如 User-Agent）。

    Authorization 不再从 headers 传：curl 的 argv 同机任何进程 `ps -eww` 都读得到，
    而这里的报错又会把整条命令打出来。鉴权头走 stdin 配置，真源 scripts/lib/pexels_curl.py。
    """
    opts = ["-s", "-m", "30", "-H", "User-Agent: Mozilla/5.0"]
    for k, v in (headers or {}).items():
        if k.lower() == "authorization":
            raise ValueError("Authorization 不进 curl 的 argv，请使用 pexels_curl.run")
        opts += ["-H", f"{k}: {v}"]
    out = pexels_curl.run(opts, url, KEY)
    if out.returncode != 0:
        raise RuntimeError(f"curl failed {url}: {out.stderr.decode('utf-8', 'replace')}")
    return json.loads(out.stdout)

used_ids = set()
results = []
for idx, item in enumerate(queries, start=1):
    label = item["label"]
    query = item["query"]
    target = os.path.join(IMAGES_DIR, f"atmosphere-{idx}.png")
    if os.path.exists(target):
        print(f"  [{idx}] {label} — 已存在，跳过")
        results.append(idx); continue
    url = f"https://api.pexels.com/v1/search?query={quote(query)}&orientation=portrait&per_page=10"
    try:
        data = curl_json(url)
    except Exception as e:
        print(f"  [{idx}] {label} 搜索失败: {e}")
        continue
    photos = data.get("photos", [])
    photo = next((p for p in photos if p["id"] not in used_ids), None)
    if not photo:
        print(f"  [{idx}] {label} 无可用结果")
        continue
    used_ids.add(photo["id"])
    src = photo["src"].get("portrait") or photo["src"].get("large") or photo["src"]["original"]
    raw = os.path.join(IMAGES_DIR, f".raw-{idx}.jpg")
    dl = subprocess.run(["curl", "-s", "-m", "60", "-A", "Mozilla/5.0", "-o", raw, src], capture_output=True, text=True)
    if dl.returncode != 0 or not os.path.exists(raw) or os.path.getsize(raw) < 1000:
        print(f"  [{idx}] {label} 下载失败")
        continue
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
                    "-vf", "scale=720:960:force_original_aspect_ratio=increase,crop=720:960",
                    "-frames:v", "1", target], check=False)
    os.remove(raw)
    if os.path.exists(target):
        print(f"  [{idx}] {label} ← {query} (pexels #{photo['id']}, {photo['photographer']})")
        results.append(idx)
    else:
        print(f"  [{idx}] {label} ffmpeg 失败")

print(f"\n完成 {len(results)}/{len(queries)} 张")
