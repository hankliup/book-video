#!/usr/bin/env python3
"""Pexels 候选预览抓取 + 联系表拼接（目检选片用）。

为什么需要它：fetch-pexels-images.mjs 用 node 内置 fetch 下 src.original，本机代理隧道
（198.18.0.x，tun 模式）会中途掐断（UND_ERR_SOCKET / HTTP2 framing），而原图 1-3MB，
一次断连整轮退出；原脚本下载分支没有 try，只能靠外层反复重跑。定稿回取走
fetch-pexels-by-id.py（已有 3 次重试 + --http1.1 + 解码校验 + w= 限宽），
所以本脚本只负责「看清每条检索词能拿到什么」这一件事：抓 w=480 的小预览（56KB 级，
隧道下稳定），拼成联系表供目检，并把候选 photo id 落盘供写 data/<书名>_pexels_ids.json。

下载重试口径与 fetch-pexels-by-id.py 的 curl_cdn 保持一致：第 2 轮起强制 --http1.1
绕开 HTTP2 帧错误，并且真正解码一遍——截断的 JPEG 能 open 但 load 会报错，
只 open 会把半张图当成成功。

用法:
  python3 scripts/fetch-pexels-previews.py <episode> <queries.json> [--w 480] [--only 3,7]
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import pexels_curl  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TARGET = (240, 320)          # 联系表里每格的尺寸（3:4）
COLS = 4
PADDING = 10
BG = (18, 18, 20)


def load_env():
    env = REPO / ".env"
    if not env.exists():
        sys.exit("缺少 .env")
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def curl_json(url, key, attempt=0):
    # 鉴权头走 stdin 配置，不进 curl 的 argv：同机 `ps -eww` 能读出任何进程的整条命令行，
    # 而这台机器常年并行跑多个会话（见 scripts/lib/pexels_curl.py）。
    opts = ["-sSL", "--max-time", "60"]
    if attempt > 0:
        opts.insert(0, "--http1.1")
    r = pexels_curl.run(opts, url, key)
    if r.returncode != 0:
        raise RuntimeError(f"curl {r.returncode}: {r.stderr.decode('utf-8', 'replace').strip()[:120]}")
    return json.loads(r.stdout)


def curl_cdn(url, width, attempt=0):
    src = url.split("?")[0] + f"?auto=compress&cs=tinysrgb&w={width}"
    cmd = ["curl", "-sSL", "--max-time", "120"]
    if attempt > 0:
        cmd.insert(1, "--http1.1")
    cmd += ["-o", "-", src]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl {r.returncode}: {r.stderr.decode('utf-8', 'replace').strip()[:120]}")
    try:
        probe = Image.open(io.BytesIO(r.stdout))
        probe.load()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"解码失败（{len(r.stdout)} bytes, {exc}）") from exc
    return r.stdout


def fetch_with_retry(url, width, name, attempts=4):
    last = "未知错误"
    for attempt in range(attempts):
        try:
            return curl_cdn(url, width, attempt)
        except RuntimeError as exc:
            last = str(exc)
            time.sleep(2 * (attempt + 1))
    print(f"    {name} 预览下载失败：{last}", file=sys.stderr)
    return None


def cover(img, size):
    w, h = size
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    img = img.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("queries")
    ap.add_argument("--w", type=int, default=480, help="预览图请求宽度（默认 480）")
    ap.add_argument("--only", default="", help="只抓这些 index，逗号分隔，如 3,7 或 3,result-bridge")
    ap.add_argument("--retries", type=int, default=4, help="同一条目的重试轮数（换不同照片重选）")
    args = ap.parse_args()

    load_env()
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        sys.exit("缺少 PEXELS_API_KEY")

    ep_dir = REPO / "episodes" / args.episode
    prev_dir = ep_dir / "preview"
    prev_dir.mkdir(parents=True, exist_ok=True)

    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    out_file = ep_dir / "pexels-candidates.json"
    picked = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else []
    by_name = {item["name"]: item for item in picked}
    used = {item["photo_id"] for item in picked}

    for item in queries:
        raw_index = item.get("index")
        explicit = raw_index if isinstance(raw_index, str) else None
        name = explicit or f"atmosphere-{int(raw_index)}"
        if only and name not in only and str(int(raw_index) if not explicit else "") not in only:
            continue
        if (prev_dir / f"{name}.jpg").exists() and name in by_name:
            print(f"  {name} 已存在，跳过")
            continue

        got = False
        for attempt in range(args.retries):
            url = ("https://api.pexels.com/v1/search?query="
                   + item["query"].replace(" ", "+")
                   + f"&orientation=portrait&per_page=8")
            try:
                photos = curl_json(url, key, attempt).get("photos", [])
            except Exception as exc:  # noqa: BLE001
                print(f"  {name} 检索重试 {attempt+1}: {exc}", file=sys.stderr)
                time.sleep(3)
                continue
            fresh = [p for p in photos if p["id"] not in used] or photos
            photo = max(fresh, key=lambda p: p["width"] * p["height"])
            raw = fetch_with_retry(photo["src"]["original"], args.w, name)
            if raw is None:
                continue
            (prev_dir / f"{name}.jpg").write_bytes(raw)
            used.add(photo["id"])
            by_name[name] = {
                "name": name,
                "label": item.get("label", ""),
                "query": item["query"],
                "photo_id": photo["id"],
                "photographer": photo["photographer"],
                "url": f"https://www.pexels.com/photo/{photo['id']}/",
                "image_url": photo["src"]["original"],
                "pixels": f"{photo['width']}x{photo['height']}",
                "alt": photo.get("alt", ""),
            }
            print(f"  {name} ← #{photo['id']} {photo['photographer']} {photo['width']}x{photo['height']}")
            got = True
            break
        if got:
            out_file.write_text(json.dumps(list(by_name.values()), ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

    # 拼联系表：每格上方标 name，下方标 id，方便目检后直接写 ids.json
    names = [n for n in by_name if (prev_dir / f"{n}.jpg").exists()]
    names.sort(key=lambda n: (0 if n == "result-bridge" else 1,
                              int(n.split("-")[1]) if n.split("-")[-1].isdigit() else 0))
    tw, th = TARGET
    cell_h = th + 34
    rows = (len(names) + COLS - 1) // COLS or 1
    sheet = Image.new("RGB", (COLS * tw + (COLS + 1) * PADDING,
                              rows * cell_h + (rows + 1) * PADDING), BG)
    for i, name in enumerate(names):
        r, c = divmod(i, COLS)
        x = PADDING + c * (tw + PADDING)
        y = PADDING + r * (cell_h + PADDING)
        try:
            tile = cover(Image.open(prev_dir / f"{name}.jpg").convert("RGB"), TARGET)
        except Exception as exc:  # noqa: BLE001
            print(f"  跳过 {name}: {exc}", file=sys.stderr)
            continue
        sheet.paste(tile, (x, y))
        try:
            from PIL import ImageDraw
            d = ImageDraw.Draw(sheet)
            d.text((x + 3, y + th + 4), f"{name}  #{by_name[name]['photo_id']}", fill=(235, 235, 235))
        except Exception:  # noqa: BLE001
            pass
    sheet_path = ep_dir / "pexels-contact-sheet.jpg"
    sheet.save(sheet_path, quality=88)
    print(f"\n候选 {len(names)} 张 → {sheet_path}")
    print(f"候选记录 → {out_file}")


if __name__ == "__main__":
    main()
