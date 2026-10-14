#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 scene-map.json 盖行锚点：把「这一层当初盖的是哪一句话」钉在图层表上。

判据本体在 `scripts/lib/scene_map_anchors.py`（`check-scene-map-before-render.py` 与
`build-scene-map.py` 共用同一份），这里只负责写盘。为什么要有这个工具：锚点是**作者当时看到的
文字**，只能在写图层表或核对完图层表的那一刻取；《源泉》2026-10-14 那次合并（第 78 行「而是：」
与第 79 行并成一行，95 行变 94 行）之后，越界/覆盖/同图/缺文件四条断言全绿，中间每个切点却都
错了一行——把锚点补上，下一次改文案才判得出来。

三种动作：
  <书名…> / --all      盖或重盖锚点（写入 body_rows + 每层 anchor_start/anchor_end）
  --check              只报现状：几期有锚点、几期对不上，不写盘
  --unstamp <书名…>    撤掉锚点，回到「判不了」的形状（供回退与回归用）

盖章会**拒绝**覆盖一份已经对不上的图层表：那种情况下当前行文不是意图，把它写进锚点等于把错位
钉成真值，得先按红话里的行号把 order_start/order_end 改回去。

盖章锁的是未来，不证明过去。`anchor_verified` 按 scene-map 与 script-body.csv 的先后如实记录：
图层表不比文案旧，说明这份表就是在这段行文上写的；文案比图层表新，则只声明「从现在起这两者绑定」，
不假装当初对齐过（AGENTS.md：文件的存在/时间戳证明不了内容，所以这里只把它当成盖章时的自我声明，
判据永远只比锚点与行文）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import scene_map_anchors as SA  # noqa: E402

STAMP_DATE = "2026-10-14"


def episodes_with_map(root=ROOT, books=None):
    if books:
        return list(books)
    ep_root = os.path.join(root, "episodes")
    if not os.path.isdir(ep_root):
        return []
    return sorted(d for d in os.listdir(ep_root)
                  if os.path.exists(os.path.join(ep_root, d, "scene-map.json")))


def load_map(ep_dir):
    path = os.path.join(ep_dir, "scene-map.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def atomic_dump(path, obj):
    tmp = path + ".stamp.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)          # 并行会话的渲染正在读这份文件：半截 JSON 会炸在那一刻


def verified_claim(ep_dir):
    """盖章时的自我声明：图层表是不是写在这份文案上的。"""
    sm = os.path.join(ep_dir, "scene-map.json")
    csv_p = os.path.join(ep_dir, "script-body.csv")
    if not os.path.exists(csv_p):
        return False, "没有 script-body.csv"
    if os.path.getmtime(sm) >= os.path.getmtime(csv_p):
        return True, "图层表不比文案旧"
    return False, "文案比图层表新：只锁未来，不证明当初对齐"


def stamp_one(name, root=ROOT, dry=False, unstamp=False):
    ep = os.path.join(root, "episodes", name)
    path = os.path.join(ep, "scene-map.json")
    if not os.path.exists(path):
        return "skip", f"{name}：没有 scene-map.json，无从盖章"
    scene = load_map(ep)
    rows, version, _blind = SA.anchor_rows(ep)
    if unstamp:
        if not SA.has_anchors(scene):
            return "skip", f"{name}：本来就没有锚点"
        SA.unstamp(scene)
        if not dry:
            atomic_dump(path, scene)
        return "ok", f"{name}：锚点已撤销（回到判不了）"
    problems, judged = SA.judge(scene, rows)
    if problems:
        return "refuse", (f"{name}：锚点与当前行文对不上 {len(problems)} 处（已判 {judged} 个槽位）。"
                          f"先把行号按锚点改回去再盖章，别把错位钉成真值：\n    · "
                          + "\n    · ".join(problems[:6]))
    ok_claim, why = verified_claim(ep)
    note = (f"anchors {STAMP_DATE} 按 script-body.csv 版本 {version} 补记；"
            f"{why}。判据只比 anchor_start/anchor_end 与该号行的全文"
            "（scripts/lib/scene_map_anchors.py）")
    before = json.dumps(scene, ensure_ascii=False, sort_keys=True)
    changed = SA.stamp(scene, rows, note=note)
    scene["anchor_verified"] = ok_claim
    if json.dumps(scene, ensure_ascii=False, sort_keys=True) == before:
        return "same", f"{name}：锚点已是当前行文（版本 {version}，{len(rows)} 行）"
    if not dry:
        atomic_dump(path, scene)
    return "ok", (f"{name}：盖章 {'(dry-run) ' if dry else ''}版本 {version}、{len(rows)} 行、"
                  f"写/改 {changed} 个锚点槽位；{why}")


def check_all(root=ROOT, books=None):
    eps = episodes_with_map(root, books)
    stamped = blind = drifted = 0
    drift_list = []
    for name in eps:
        ep = os.path.join(root, "episodes", name)
        scene = load_map(ep)
        rows, _v, _b = SA.anchor_rows(ep)
        problems, judged = SA.judge(scene, rows)
        if problems:
            drifted += 1
            drift_list.append((name, problems[0]))
        if SA.has_anchors(scene):
            stamped += 1
        else:
            blind += 1
    print(f"[盖章对账] {len(eps)} 期有 scene-map：{stamped} 期已盖（判得到），{blind} 期无锚点（判不了＝没测）")
    for name, first in drift_list:
        print(f"  ❌ {name}：{first}")
    if drift_list:
        return 1
    print("[盖章对账] ✅ 已盖锚点的图层表全部与当前行文一致")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("books", nargs="*", help="书名；不填且没有 --all 时打印用法")
    ap.add_argument("--all", action="store_true", help="全库每一期有 scene-map 的")
    ap.add_argument("--check", action="store_true", help="只报现状，不写盘")
    ap.add_argument("--dry-run", action="store_true", help="演算并打印，不写盘")
    ap.add_argument("--unstamp", action="store_true", help="撤销锚点")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    books = a.books or None
    if a.check:
        return check_all(a.root, books)
    if not books and not a.all:
        print(__doc__)
        return 2
    names = episodes_with_map(a.root, books)
    bad = 0
    tally = {"ok": 0, "same": 0, "skip": 0, "refuse": 0}
    for name in names:
        kind, line = stamp_one(name, root=a.root, dry=a.dry_run, unstamp=a.unstamp)
        tally[kind] = tally.get(kind, 0) + 1
        if kind in ("refuse", "skip") and books:
            # 指名道姓的一期没盖成，不能混在绿线里当一个「跳过」——那是下一份「扫到 0 期还报绿」。
            print(f"  ❌ {line}" if kind == "refuse" else f"  ⚠️ {line}")
            bad += kind == "refuse"
            continue
        if kind == "skip":
            print(f"  ⚠️ {line}")
            continue
        print(f"  {'✅' if kind in ('ok', 'same') else '❌'} {line}")
        bad += kind == "refuse"
    print(f"[盖章] {len(names)} 期：写/改 {tally['ok']}，已一致 {tally['same']}，"
          f"跳过 {tally['skip']}，拒盖 {tally['refuse']}"
          + ("  ← 有期数拒盖，行号得先按锚点改回去" if tally["refuse"] else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
