#!/usr/bin/env python3
"""把候选照片按**接近成片的尺寸**落到本地，供全尺寸复核后再选型。

为什么要新增这一步：mq-pexels-sheet.py 的联系表每格只有 200x267，而穿帮物恰恰是在这个
尺寸下看不见的那一类东西——一块「STARBUCKS」围栏、一盏亮着的绿色 EXIT 灯、一条现代车牌、
一件卫衣和一双运动鞋。《达洛维夫人》2026-10-01 按联系表锁定的 18 张里有 13 张在全尺寸
复核时被判不合格，全部退回重选，而它们当初都是从表上「看着很对氛围」的格子里挑出来的。
所以选型不能只在缩略图上做：先按查询把候选原图缩到复核宽度（默认 760px，比联系表大 3.8 倍
面积，招牌和车标足够可读）落到本地，逐张看过之后再锁。

本脚本只写 tmp/，绝不碰 episodes/ 与 data/；挑定之后仍然走
lock-pexels-selection.py / fetch-pexels-by-id.py 那条按 id 取原图的路。

两层「不要再端上来」的名单都在这里生效，缺一层就会反复看见同一批废图：
  --exclude-used  → 全仓库已经**上过屏**的 id（scripts/lib/pexels_index.py）
  默认开启         → 目检**否决但从未投放**的 id（data/pexels-rejected.json，
                     唯一读入口 scripts/lib/pexels_reject.py，写入用
                     scripts/reject-pexels-photo.py）。后者是 2026-10-03 补的：
                     大理稻田亲子旅拍那一组 6 帧靠 Pexels 的乱标签
                     （"animals, Beautiful scenery, beautiful woman"）在第四批里被
                     召回 15 个候选批次，中式村委长廊 #39854273 被召回 18 个批次，
                     而 --exclude-used 永远拦不住它们——人工否决不落文件就只是会话记忆。
要故意把名单里的图捞回来（比如复核那条否决本身对不对）加 --ignore-rejected。

退出码：0 = 所有检索词都跑完；3 = 有检索词网络失败，候选池比设计上窄，
先带 --cache 重跑失败的那几个词再判「无可用」。

用法:
  python3 scripts/pexels_candidates.py --out tmp/<dir>/<beat> \
      [--cache tmp/<dir>/cache] [--per-query 3] [--width 760] "<query1>" "<query2>" ...

产出:
  <out>/cand-00.png …            居中裁 3:4 的复核图
  <out>/cand.json                [{file,id,photographer,url,image_url,pixels,avg_color,alt,query}]

cand.json 里的 id 就是 lock 阶段要用的 photo id，按 index 取，不要手抄。
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

from PIL import Image

SEARCH = os.path.expanduser("~/.workbuddy/skills/nook-pexels/scripts/pexels_search.py")
PER_PAGE = 8
AR_H_W = 1365 / 1024  # 3:4 竖幅的高宽比，与成片 720x960 同比例

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True, help="候选落地目录（只在 tmp/ 下）")
ap.add_argument("--cache", default=None, help="按关键词缓存检索结果的目录，与 mq-pexels-sheet.py 共用")
ap.add_argument("--per-query", type=int, default=3, help="每个查询取前几张候选")
ap.add_argument("--width", type=int, default=760, help="复核图宽度（px）")
ap.add_argument("--skip", default="", help="逗号分隔的 photo id，检索到也直接丢掉")
ap.add_argument("--exclude-used", action="store_true",
                help="把全仓库已经上过屏的 photo id 全部丢掉（钩子段挑图必带：跨期撞图只有"
                     "连着 111 份 prompts.csv 看才看得见，见 scripts/lib/pexels_index.py）")
ap.add_argument("--ignore-rejected", action="store_true",
                help="连持久否决名单里的 id 也放回来（只在你就是要看那张被否决的图时用）")
ap.add_argument("queries", nargs="+")
args = ap.parse_args()

skip = {int(x) for x in args.skip.split(",") if x.strip().isdigit()}
if not args.ignore_rejected:
    # 目检否决过的图必须先于检索丢掉：`--exclude-used` 只认「已投放」，而人工否决从来没投放过，
    # 所以同一批废图每一批都会重新端上来（大理稻田旅拍那组 2026-10-03 出现在 15 个候选批次、
    # 中式村委长廊出现在 18 个）。名单是唯一读入口 scripts/lib/pexels_reject.py。
    _rej = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "pexels_reject.py")
    _rspec = importlib.util.spec_from_file_location("pexels_reject", _rej)
    _rmod = importlib.util.module_from_spec(_rspec)
    _rspec.loader.exec_module(_rmod)
    rejected = _rmod.rejected_ids()
    if rejected:
        still_there = rejected & skip if skip else set()
        skip |= rejected
        print(f"持久否决：名单 {len(rejected)} 张，检索到一律丢掉"
              f"{'（其中 ' + str(len(still_there)) + ' 张你已用 --skip 显式给出）' if still_there else ''}")
if args.exclude_used:
    import importlib.util
    _root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    _spec = importlib.util.spec_from_file_location(
        "pexels_index", os.path.join(_root, "scripts", "lib", "pexels_index.py"))
    _pix = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_pix)
    used = set(_pix.build(_root))
    skip |= used
    print(f"跨期去重：全仓库已用 photo id {len(used)} 个，检索到一律丢掉")
os.makedirs(args.out, exist_ok=True)
if args.cache:
    os.makedirs(args.cache, exist_ok=True)

RUN_TAG = str(os.getpid())


def search(q):
    """与 mq-pexels-sheet.py 同一套缓存键（sha1 前 16 位），两条会话查同一个词可以互用。"""
    cached = os.path.join(args.cache, hashlib.sha1(q.encode("utf-8")).hexdigest()[:16] + ".json") \
        if args.cache else None
    if cached and os.path.exists(cached):
        data = json.load(open(cached, encoding="utf-8"))
        print(f"  [{q[:38]}] 复用缓存 {len(data.get('photos', []))}")
        return data
    tmp_json = f"/tmp/mq_px_{RUN_TAG}_{abs(hash(q)) % 100000}.json"
    net_fails = 0
    for attempt in range(6):
        proc = subprocess.run(
            ["python3", SEARCH, "--type", "photo", "--query", q,
             "--per-page", str(PER_PAGE), "--orientation", "portrait", "--out", tmp_json],
            capture_output=True, text=True)
        if proc.returncode == 0 and os.path.isfile(tmp_json) and os.path.getsize(tmp_json) > 0:
            break
        blob = (proc.stdout or "") + (proc.stderr or "")
        if "429" not in blob and "Too Many Requests" not in blob:
            # 代理抖动（Tunnel connection failed: 503）和限流不是一回事：限流值得退避重试，
            # 而一次网络错误不该把整条流的后面几个词一起打死。旧写法在这里 sys.exit，
            # 《达洛维夫人》重选时 a10 第二个词 503，整段候选只剩 3 张且 cand.json 没落盘，
            # 文件名与 photo id 的对应关系当场丢失，只能整段重跑。
            #
            # 但"不打死后面的词"不等于"这个词就放弃了"：503 是隧道抖动，隔几秒重来基本就好。
            # 第四批 10 期里有 3 个 beat 因为一次 503 只剩 3 张候选，目检之后判「无可用」，
            # 于是整期重检索——把一次网络抖动误读成「Pexels 没有这种图」，成本是一期人眼。
            net_fails += 1
            if net_fails <= 2:
                print(f"  [{q[:38]}] 网络错误（第 {net_fails} 次），退避重试：{blob.strip()[-120:]}")
                time.sleep(4 * net_fails)
                continue
            print(f"  [{q[:38]}] 重试 {net_fails} 次仍失败，跳过该词：{blob.strip()[-200:]}")
            return {"photos": [], "query_failed": True}
        wait = 30 * (attempt + 1)
        print(f"  [{q[:38]}] 限流，{wait}s 后重试", flush=True)
        time.sleep(wait)
    else:
        print(f"  [{q[:38]}] 连续限流，跳过该词（带 --cache 重跑可补）")
        return {"photos": [], "query_failed": True}
    data = json.load(open(tmp_json, encoding="utf-8"))
    if cached:
        json.dump(data, open(cached, "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(1.5)  # 逐词之间留口，别把共享额度一口气打满
    return data


def fetch(url, binary=True):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            if binary:
                Image.open(io.BytesIO(raw)).load()  # 半张 JPEG 能 open 不能 load
            return raw
        except Exception as exc:  # noqa: BLE001 - CDN 抖动只重试这一张
            last = exc
            time.sleep(2 * (attempt + 1))
    print(f"  ! 取回失败 {url[:70]}: {last}")
    return None


def crop_3x4(img):
    """与 fetch-pexels-by-id.py 同一口径：3:4 竖幅 = 高/宽 1.333，先判后裁再缩。

    判据必须写成 h/w 对 AR_H_W，写成 w/h 对 TARGET_RATIO 会把每一张竖图都判成横图，
    复核图上屏的就是被裁掉上下端的横条——穿帮物恰恰藏在被裁掉的那部分里。
    """
    w, h = img.size
    if h >= w * AR_H_W:
        th = int(w * AR_H_W)
        top = (h - th) // 2
        img = img.crop((0, top, w, top + th))
    else:
        tw = int(h / AR_H_W)
        left = (w - tw) // 2
        img = img.crop((left, 0, left + tw, h))
    return img.resize((args.width, int(args.width * AR_H_W)), Image.LANCZOS)


rows, seen = [], set()
failed_queries = []
for q in args.queries:
    got = search(q)
    photos = got.get("photos", [])
    if got.get("query_failed"):
        failed_queries.append(q)
    taken = 0
    for p in photos:
        if taken >= args.per_query:
            break
        pid = p["id"]
        if pid in seen or pid in skip:
            continue
        src = p.get("src", {})
        url = (src.get("original") or src.get("large2x") or "").split("?")[0]
        if not url:
            continue
        seen.add(pid)
        raw = fetch(f"{url}?auto=compress&cs=tinysrgb&w={args.width}")
        if not raw:
            continue
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img = crop_3x4(img)
        fname = f"cand-{len(rows):02d}.png"
        img.save(os.path.join(args.out, fname))
        rows.append({
            "file": fname, "index": len(rows), "id": pid,
            "photographer": p.get("photographer", ""),
            "url": p.get("url", ""), "image_url": url,
            "pixels": f"{p.get('width')}x{p.get('height')}",
            "avg_color": p.get("avg_color", ""), "alt": p.get("alt", ""),
            "query": q, "review_size": f"{img.width}x{img.height}",
        })
        taken += 1
    print(f"  [{q[:44]}] -> {taken} 张候选")

json.dump(rows, open(os.path.join(args.out, "cand.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"\n{len(rows)} 张候选 -> {args.out}/cand.json")
if failed_queries:
    # 候选池薄通常是**检索覆盖缺陷**，不是「这个主题没有这种图」。不喊出来的话，
    # 目检的人会把「无可用」写进结论并整期重检索，而真正该重跑的只是这一条 query。
    print(f"⚠️  这一轮有 {len(failed_queries)} 个检索词失败，候选池比设计上窄：")
    for q in failed_queries:
        print(f"     · {q}")
    print(f"   先带 --cache 重跑这些词（缓存只存成功结果，重跑只补失败的那几个），"
          f"再判「无可用」。退出码 3 = 候选池不完整。")
    sys.exit(3)
