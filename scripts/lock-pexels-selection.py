#!/usr/bin/env python3
"""从 Pexels contact sheet 的候选清单按「格子编号」生成某期的已采纳图 id 锁定文件。

为什么按编号而不是手抄 photo id（2026-09-28 实测教训）：联系表上的 id 是一位一位
从图上读出来的，抄错一位仍然会取到另一张**真实存在**的 Pexels 照片，画幅/溯源/调色
三道门禁全绿——错的图就这样进了成片。选型只记录「第几张表的第几格」，id 由本脚本
从 mq-pexels-sheet.py 落盘的候选 JSON 里程序化取出，杜绝手抄。

用法：
  python3 scripts/lock-pexels-selection.py "<书名>" picks.json --apply
picks.json：
  [{"name": "result-bridge", "sheet": "tmp/xxx/sheet1.png", "cell": 1, "note": "..."}, ...]
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
KEY = None

import importlib.util as _ilu

# 跨期撞图的口径和交付门禁、挑图脚本共用同一份索引（见该文件开头为什么必须是同一份）。
_spec = _ilu.spec_from_file_location("pexels_index", os.path.join(ROOT, "scripts", "lib", "pexels_index.py"))
PIX = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(PIX)
_REPO_INDEX = None


def repo_index():
    global _REPO_INDEX
    if _REPO_INDEX is None:
        _REPO_INDEX = PIX.build(ROOT)
    return _REPO_INDEX


# 目检否决但从未投放的图另有名单：全仓库索引只认「已上过屏」，人工否决不落文件就只是
# 上一个会话的记忆，同一批废图每批都会重新端上来（写入见 scripts/reject-pexels-photo.py）。
_rspec = _ilu.spec_from_file_location("pexels_reject",
                                      os.path.join(ROOT, "scripts", "lib", "pexels_reject.py"))
REJECT = _ilu.module_from_spec(_rspec)
_rspec.loader.exec_module(REJECT)

# 鉴权头的传法只有一个真源：curl 的配置走 stdin，密钥不进 argv（同机 ps 读得到命令行，
# 而任何打印整条命令的报错还会把它带走）。
_cspec = _ilu.spec_from_file_location("pexels_curl",
                                      os.path.join(ROOT, "scripts", "lib", "pexels_curl.py"))
_pexels_curl = _ilu.module_from_spec(_cspec)
_cspec.loader.exec_module(_pexels_curl)
_REJECTED = None
_ALREADY_LOCKED = None


def reject_list():
    """缓存外壳，判据仍在 lib（名字避开 rejected_ids，理由见 lock-pexels-candidates.py）。"""
    global _REJECTED
    if _REJECTED is None:
        _REJECTED = REJECT.rejected_ids()
    return _REJECTED
def already_locked():
    """本期既有清单里的 photo id：重锁一段时原样搬回来的那些不拦。"""
    global _ALREADY_LOCKED
    if _ALREADY_LOCKED is None:
        _ALREADY_LOCKED = set()
        path = os.path.join(ROOT, "data", f"{args.episode}_pexels_ids.json")
        if os.path.isfile(path):
            try:
                for item in json.load(open(path, encoding="utf-8")):
                    pid = item.get("photo_id") or item.get("id")
                    if str(pid or "").isdigit():
                        _ALREADY_LOCKED.add(int(pid))
            except (OSError, ValueError, TypeError):
                pass
    return _ALREADY_LOCKED


def api_key():
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        m = re.match(r"^PEXELS_API_KEY=(.*)$", line.strip())
        if m:
            return m.group(1).strip().strip('"').strip("'")
    sys.exit("Missing PEXELS_API_KEY in .env")


def photo_by_pid(pid):
    """按 id 补元数据：/tmp/mq_px_<qi>.json 会被后一次检索覆盖，只有 id 是可靠的。

    多期并行时 Pexels 会回 429，所以这里带退避重试；密钥绝不进命令行参数——以前这条
    docstring 写着「绝不」而代码拼的是 `-H "Authorization: <key>"`，正好是注释与实现相反
    那一类：curl 的 argv 同机任何进程 `ps -eww` 都读得到。现在鉴权头走 stdin 配置
    （scripts/lib/pexels_curl.py）。
    """
    global KEY
    if KEY is None:
        KEY = api_key()
    for attempt in range(4):
        r = _pexels_curl.run(
            ["-sS", "--max-time", "60"],
            f"https://api.pexels.com/v1/photos/{pid}", KEY)
        if r.returncode == 0 and r.stdout.strip().startswith(b"{"):
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                data = {}
            return data if data.get("id") == pid else None
        time.sleep(3 * (attempt + 1))
    return None


ap = argparse.ArgumentParser()
ap.add_argument("episode")
ap.add_argument("picks")
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

picks = json.load(open(args.picks, encoding="utf-8"))
out_path = os.path.join(ROOT, "data", f"{args.episode}_pexels_ids.json")

manifest_cache = {}
photo_cache = {}


def load_manifest(sheet_png):
    key = os.path.splitext(os.path.abspath(sheet_png))[0]
    if key not in manifest_cache:
        manifest_cache[key] = json.load(open(key + ".json", encoding="utf-8"))
        # 同一张表按查询顺序拆成 /tmp/mq_px[_<pid>]_<qi>.json，里面才有摄影师与页面链接。
        # mq-pexels-sheet.py 为避免多期并行互相覆盖中间文件，文件名带了进程号，
        # 这里必须用 glob 一起收；只扫写死的 mq_px_<qi>.json 会一条都命中不了，
        # 于是每张图都退回按 id 重查 API —— 共享 key 限流时整批选型就卡死了。
        for raw in sorted(glob.glob("/tmp/mq_px_*.json")):
            try:
                photos = json.load(open(raw, encoding="utf-8")).get("photos", [])
            except (OSError, json.JSONDecodeError):
                continue
            for p in photos:
                photo_cache.setdefault(p["id"], p)
    return manifest_cache[key]


locked = []
for pick in picks:
    rows = load_manifest(pick["sheet"])
    cell = int(pick["cell"])
    if not 0 <= cell < len(rows):
        sys.exit(f"{pick['name']}: 格子 #{cell} 超出 {pick['sheet']} 的 {len(rows)} 个候选")
    row = rows[cell]
    # 持久否决名单（data/pexels-rejected.json）：检索那一步默认就会丢掉这些 id，但 picks 里的
    # cell 是人手抄的，可能来自 --ignore-rejected 那一轮，所以锁口再拦一次。
    # 同 lock-pexels-candidates.py：本期清单里本来就有的那张不拦，重锁不能因为名单后来收了一张
    # 已投放的图而整段打死——撤已投放的图走重选+重渲，不动名单。
    if row["id"] in reject_list() and row["id"] not in already_locked():
        sys.exit(f"{pick['name']}: {REJECT.describe(int(row['id']))}"
                 "——这张已被全局目检否决，换一张；确实要故意回收就改走 "
                 "lock-pexels-candidates.py --allow-rejected 并在 picks 里写 reason")
    # 钩子段先查跨期：这张照片要是别的期正文已经放过，锁下来就是一条「本期的钩子在放上一期的
    # 片尾」。AGENTS.md 把跨期重复划在单期门禁之外，让挑图脚本在锁之前 diff 全仓库清单——但那个
    # diff 以前只活在 tmp/hook-b4/write-picks.py 里，交付侧没有同一份口径，于是 2026-10-03 那批
    # 17 期钩子里有 10 期 11 张撞了别的期正文，八道门全绿。现在锁的这一步和门禁共用
    # scripts/lib/pexels_index.py，在这里拒绝比在成片上拒绝便宜一百倍（还没取图、没调色、没渲）。
    # 正文段的跨期重复不在本条规则里：全仓库正文本来就有几十张同图不同期，那是历史存量、不是缺陷。
    if (pick.get("segment") or "body") == "hook":
        hits = PIX.foreign_uses(repo_index(), row["id"], args.episode)
        if hits:
            sys.exit(f"{pick['name']}: Pexels #{row['id']} 已经上过别的期（{PIX.describe(hits)}）"
                     "——钩子段换一张本期与别期都没用过的照片")
    # mq-pexels-sheet.py 现在把摄影师/页面链接/原图地址一起写进了 sidecar，
    # 直接用它可以一次 API 都不打；只有历史期那份只存 id 的 sidecar 才回落补元数据。
    if row.get("photographer") and row.get("original"):
        photo = {"id": row["id"], "photographer": row["photographer"], "url": row.get("url", ""),
                 "width": row.get("w"), "height": row.get("h"), "avg_color": row.get("avg_color", ""),
                 "alt": row.get("alt", ""), "src": {"original": row["original"]}}
    else:
        photo = photo_cache.get(row["id"]) or photo_by_pid(row["id"])
    if not photo:
        sys.exit(f"{pick['name']}: #{cell} 的 photo id {row['id']} 取不到完整元数据（Pexels API 不通？）")
    photo_cache.setdefault(row["id"], photo)
    # 钩子段和正文段共用 atmosphere-N 命名，门禁只能靠 dir 分辨该去 images-hook/ 还是
    # images-body/ 找这张图。缺了它就得在 lock 之后手工补一遍 dir 再拆 body/hook 清单，
    # 换图重锁时漏补一次，钩子段整段就游离在溯源门禁之外。选型里已经写了 segment，这里直接带出来。
    segment = pick.get("segment") or "body"
    locked.append({
        "name": pick["name"],
        "photo_id": row["id"],
        "dir": "images-hook" if segment == "hook" else "images-body",
        "segment": segment,
        "photographer": photo.get("photographer", ""),
        "url": photo.get("url", ""),
        "page_url": photo.get("url", ""),
        "image_url": (photo.get("src") or {}).get("original", row.get("thumb", "")),
        "pixels": f"{photo.get('width')}x{photo.get('height')}",
        "avg_color": photo.get("avg_color", ""),
        "alt": photo.get("alt", ""),
        "query": row.get("query", ""),
        "note": pick.get("note", ""),
    })
    print(f"  {pick['name']:<16} #{cell:>2} → Pexels #{row['id']} · {photo.get('photographer')} "
          f"· {photo.get('width')}x{photo.get('height')} · {photo.get('avg_color')} · {(photo.get('alt') or '')[:34]}")

if len({item["photo_id"] for item in locked}) != len(locked):
    sys.exit("同一张 Pexels 照片被两个层引用了")

# 一份 manifest 记的是这一期**全部**已锁选型（钩子段 + 正文段，靠 segment/dir 分辨），
# 所以重锁一段只能替换那一段的条目。整份写掉的话，正文段的选型记录就只剩 prompts.csv
# 里的手抄件，下次 fetch/调色/溯源核对都没有可对的那一份了（2026-10-02 回补《秋园》钩子段：
# 锁 2 张钩子图把 23 行正文选型一次抹掉）。
old = []
if os.path.exists(out_path):
    old = json.load(open(out_path, encoding="utf-8"))
segs_now = {item["segment"] for item in locked}
kept = [item for item in old
        if (item.get("segment") or ("hook" if item.get("dir") == "images-hook" else "body")) not in segs_now]
merged = kept + locked

# 跨段撞图：钩子段和正文段是同一个观众在同一条时间轴上先后看到的两帧，
# 同一段内部的重复上面已经拦了，这里拦的是「本次锁的图正好是另一段用过的」。
# 门禁 check-image-provenance.py 也查这一条，但那要等到 prompts.csv 写完；
# 在锁的这一步就报出来，换图的成本最低。
by_pid = {}
for item in merged:
    by_pid.setdefault(item["photo_id"], []).append(item)
for pid, group in by_pid.items():
    if len({i["segment"] for i in group}) > 1:
        who = "、".join(f"{i['segment']}/{i['name']}" for i in group)
        sys.exit(f"Pexels #{pid} 被两段同时引用（{who}）：钩子段不要复用正文已经出现过的那帧")

if args.apply:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(merged, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {out_path}（本次锁 {len(locked)} 张 / {'、'.join(sorted(segs_now))}，"
          f"保留原有 {len(kept)} 张其他段选型）")
else:
    print(f"\n[dry-run] 未写盘，加 --apply（本次 {len(locked)} 张，"
          f"另有 {len(kept)} 张其他段选型会被保留）")
