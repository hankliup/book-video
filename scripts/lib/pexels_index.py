# -*- coding: utf-8 -*-
"""全仓库 Pexels photo id → (期数, 段, 文件名) 索引。

一句话：查「这张照片在别的期是不是已经上过屏」。

为什么要跨期索引：单期门禁看得见「钩子段复用了本期正文那一帧」——图片、清单、prompts.csv 都在
同一个 episode 目录里，`check-image-provenance.py` 一直查得到。它看不见的是「钩子段复用了
**另一期**正文早就放过的那一帧」，因为另一期的记录压根不在这一次的入参里。AGENTS.md 正是因此把
跨期重复划在单期门禁之外，并要求批量挑图脚本在锁之前先把 id 跟 `data/*_pexels_ids.json` 和所有
`prompts.csv` 对一遍。

问题是那个 diff 以前只活在挑图脚本里（`tmp/hook-b4/write-picks.py` 靠它拒掉过 #3842212、
#35592096、#17032600 三张），交付侧没有同一份口径。结果 2026-10-03 回补 17 期钩子段那一批里，
有 9 张钩子图其实是另一期正文早就上屏的照片，八道门禁全绿——把 111 份 prompts.csv 连起来索引才
第一次看见。所以口径写在这里，挑图脚本、锁脚本、门禁共用同一份，别再各抄一遍。

段怎么判：钩子段的行在 prompts.csv 里带 `images-hook/` 前缀（AGENTS.md 要求 filename 写真实目录
前缀，就是为了让同名 atmosphere-N 分得清属于哪段）；历史剧集的行没有前缀，而它们本来就没有钩子段，
所以无前缀一律算正文。`data/*_pexels_ids.json` 里的 `segment` / `dir` 是权威写法，优先采信。

用法：
  from lib import pexels_index
  index = pexels_index.build()
  hits = pexels_index.foreign_uses(index, photo_id, "魔戒3·王者归来")

  python3 scripts/lib/pexels_index.py            # 全仓库钩子撞图报告
  python3 scripts/lib/pexels_index.py --json
"""
import csv
import glob
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# 与 check-image-provenance.py / write-prompts-csv.py 同一个 id 写法（"Pexels #12345678"）
PEXELS_ID = re.compile(r"Pexels\s*(?:photo)?\s*#?\s*(\d{4,})", re.I)
HOOK_PREFIX = "images-hook"

_INDEX = None


def _segment_of(filename, explicit=None):
    """行的所属段：显式 segment/dir 优先，其次看 filename 目录前缀，无前缀算正文。"""
    if explicit:
        return "hook" if explicit == "hook" or HOOK_PREFIX in str(explicit) else "body"
    fn = str(filename or "")
    return "hook" if HOOK_PREFIX in fn else "body"


def _from_prompts_csv(path):
    ep = os.path.basename(os.path.dirname(path))
    out = []
    try:
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return ep, f"读不了 {path}: {exc}"
    for row in rows:
        fn = row.get("filename") or row.get("file") or ""
        blob = " ".join(str(v) for v in row.values() if v)
        found = PEXELS_ID.search(str(row.get("source") or "")) or PEXELS_ID.search(blob)
        if not found:
            continue
        out.append((ep, _segment_of(fn, row.get("segment")), os.path.basename(fn), int(found.group(1))))
    return ep, out


def _from_manifest(path):
    # data/<书名>_pexels_ids.json：期名在文件名里，不在目录名里
    ep = os.path.basename(path)[:-len("_pexels_ids.json")]
    try:
        man = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ep, f"读不了 {path}: {exc}"
    if isinstance(man, dict):
        man = man.get("images") or man.get("entries") or []
    out = []
    for item in man:
        pid = item.get("photo_id") or item.get("id")
        if pid is None:
            continue
        seg = _segment_of(item.get("dir", ""), item.get("segment"))
        out.append((ep, seg, item.get("name", "?") + ".png", int(pid)))
    return ep, out


def build(root=ROOT, cache=True):
    """photo_id → [(episode, segment, filename), ...]，全仓库每张有溯源的上屏图。

    cache=True 时同一进程内只扫一遍：`check-image-provenance.py --all` 会为一百多期各调一次，
    每次都重扫 111 份 CSV 的话门禁就从秒级变成分钟级，然后就没人跑了——门禁跑不动等于没有门禁。
    """
    global _INDEX
    if cache and _INDEX is not None:
        return _INDEX
    index = {}
    for path in sorted(glob.glob(os.path.join(root, "episodes", "*", "prompts.csv"))):
        _, rows = _from_prompts_csv(path)
        if isinstance(rows, str):
            print(f"⚠️ {rows}")
            continue
        for ep, seg, name, pid in rows:
            index.setdefault(pid, []).append((ep, seg, name))
    for path in sorted(glob.glob(os.path.join(root, "data", "*_pexels_ids.json"))):
        _, rows = _from_manifest(path)
        if isinstance(rows, str):
            print(f"⚠️ {rows}")
            continue
        for ep, seg, name, pid in rows:
            index.setdefault(pid, []).append((ep, seg, name))
    # 清单和 CSV 记的是同一张图的两行，(期, 段, 文件名) 去重后才是「上过几次屏」
    for pid in index:
        index[pid] = sorted({tuple(x) for x in index[pid]})
    if cache:
        _INDEX = index
    return index


def foreign_uses(index, photo_id, episode):
    """这张照片在**别的期**上屏过的记录。空列表 = 本期以外没人用过这张图。"""
    return [x for x in index.get(int(photo_id), []) if x[0] != episode]


def describe(hits):
    return "、".join(f"{ep}·{seg}/{name}" for ep, seg, name in hits)


def hook_collisions(index=None, root=ROOT):
    """所有「被当钩子图用、又在别的期已经出现过」的照片。

    返回 [(pid, hook_episode, hook_name, [foreign hits]), ...]，按钩子期名排序。
    """
    index = index if index is not None else build(root)
    out = []
    for pid, rows in sorted(index.items()):
        hooks = [r for r in rows if r[1] == "hook"]
        if not hooks:
            continue
        for ep, _seg, name in hooks:
            hits = foreign_uses(index, pid, ep)
            if hits:
                out.append((pid, ep, name, hits))
    return out


def main():
    import sys
    as_json = "--json" in sys.argv
    index = build(cache=False)
    cols = hook_collisions(index)
    if as_json:
        print(json.dumps([{"photo_id": p, "episode": e, "file": n,
                           "also_used_in": [{"episode": a, "segment": b, "file": c} for a, b, c in h]}
                          for p, e, n, h in cols], ensure_ascii=False, indent=2))
    else:
        n_hook = sum(1 for rows in index.values() if any(s == "hook" for _, s, _ in rows))
        print(f"钩子图跨期撞图检查 ｜ 仓库 {len(index)} 个 photo id ｜ 钩子段用过 {n_hook} 张")
        for p, e, n, h in cols:
            print(f"   ❌ 《{e}》钩子 {n} 的 Pexels #{p} 已经上过屏：{describe(h)}")
        if not cols:
            print("✅ 没有一张钩子图是别的期已经放过的照片")
    sys.exit(1 if cols else 0)


if __name__ == "__main__":
    main()
