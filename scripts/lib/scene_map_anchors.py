#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene-map 的行锚点：唯一的「图层行号还对着本期文案吗」判据（2026-10-14）。

为什么需要（2026-10-14《源泉》）：正文文案把第 78 行「而是：」和第 79 行「在复杂的现实世界里，」
并成了一行，95 行变 94 行。`check-scene-map-before-render.py` 的四条前置断言（越界 / 未覆盖 /
相邻同图 / 图层没有对应文件）里，只有第一条和第二条能碰到这种改动——把最后一层的 order_end
从 95 改成 94，两道就都绿了，而 **中间每一个切点都往回错了一行**：atmosphere-9 本该从
「也许不是像洛克一样生活」开始，实际从「而是：在复杂的现实世界里」开始。行号是一串整数，
它对着的行文变了，它不报错。

这一族缺陷的形状是「改结构不改判据」，所以判据不能只看数：必须把**作者当时看到的文字**
钉在图层上。于是每层写两个锚点（anchor_start = order_start 那一行的全文，anchor_end =
order_end 那一行的全文），顶层写 body_rows = 盖章时的行数。判的时候逐层比对：

  锚点等于当前行           → 过
  锚点在当前某行整行命中   → 红：行号要改到那一行（合并/删行之后，锚点自己就是正确行号）
  锚点被并进了更长的行     → 红：点名被并进第几行
  锚点哪儿都找不到         → 红：这一行被删掉了
  行数没变、只是字面不同   → 红话改成「多半是改字，重盖章即可」——不让人为了过闸去重排行号

`body_rows` 与当前行数是否相等，决定了红话给哪一条修法。这是「红必须说出正确修法」的要求：
《源泉》那种合并要改行号，《面纱》那种去装饰符号只要重盖章，两者在这四条断言下都是绿的，
混成一条红话就会把人派去做一次不必要且危险的正文重排（重排过的图层—行文配对，已交付的正文会改样）。

没有锚点的期数**不判**（判不了＝没测，不是绿）：锚点是这一层被写下时的文字，事后补不出来——
2026-10-14 全库 124 份 scene-map 里没有一份留过任何形式的行文指纹（顶层键只有
layers/note/episode/motion/source/fps/derived_from/caption_mode/selection，图层键只有
name/order_start/order_end/theme/index/static/pexels_photo_id/approx_seconds/
layer_start/layer_end/theme_note/reuse_of），把「今天的行文」当成「当初的意图」写回去，
是给一个没测过的东西造假证人。所以补盖章必须显式声明它只锁住「从现在起」（写 anchor_note），
而判据永远只比锚点，不比盖章时间。

命名口径：anchor_* 存的是**整行文字**而不是前缀。前缀匹配会被合并吃掉——「而是：」并进行后
仍是第 78 行的开头，`startswith` 照样命中，而这正是本模块要抓的那一种改动。
"""
from __future__ import annotations

import csv
import os

ANCHOR_FIELDS = (("anchor_start", "order_start"), ("anchor_end", "order_end"))
BODY_ROWS_KEY = "body_rows"


def read_body_rows(ep_dir):
    """script-body.csv → {order: 去空白后的整行文字}。行号重复时保留先出现的那一份。

    **不筛 version**，与 `check-scene-map-before-render.py` 原来的 `orders` 完全同口径
    （围城 A/B、女儿红 A/short 两期的 max_order 都由这里给出）；越界与覆盖两条断言用它，
    锚点判定不用它——见 `anchor_rows`。
    """
    path = os.path.join(ep_dir, "script-body.csv")
    if not os.path.exists(path):
        return {}
    rows = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            o = (r.get("order") or "").strip()
            if not o:
                continue
            try:
                idx = int(o)
            except ValueError:
                continue
            rows.setdefault(idx, (r.get("text") or "").strip())
    return rows


def anchor_rows(ep_dir):
    """锚点用的那份行文：只取本期实际渲染的那个 version 的行。

    为什么锚点必须按版本取，而越界不必：多版本 CSV 里第 7 行可能同时是 A 版的一句话和 B 版的
    另一句话，锚点跟「先出现的那一份」比会把《围城》这种正常状态判成文案错位。版本用
    `scripts/lib/script_version.resolve_segment_version` 解析（Node 侧建轴链用的是同一条规则的
    孪生实现），这里不重述第四条口径。CSV 没有 version 列或解析出的版本在本期没有行时退回全表，
    并在第三项里报告退让——「锚点跟哪一版比」不能是一个读者看不见的选择。
    """
    from script_version import resolve_segment_version  # noqa: PLC0415 - lib 同级，延迟导入避免环
    all_rows = read_body_rows(ep_dir)
    path = os.path.join(ep_dir, "script-body.csv")
    if not all_rows or not os.path.exists(path):
        return all_rows, "-", not all_rows
    version, _changed, _episode = resolve_segment_version(ep_dir, path)
    picked = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("version") or "").strip() != version:
                continue
            o = (r.get("order") or "").strip()
            try:
                idx = int(o)
            except ValueError:
                continue
            picked.setdefault(idx, (r.get("text") or "").strip())
    if not picked:
        return all_rows, f"{version}（本期没有这一版本行，退回全表）", True
    return picked, version, False


def has_anchors(scene):
    """这份 map 有没有任何可判的锚点（含顶层 body_rows）。"""
    if scene.get(BODY_ROWS_KEY) is not None:
        return True
    for L in scene.get("layers") or []:
        if any(L.get(k) for k, _ in ANCHOR_FIELDS):
            return True
    return False


def coverage(scene):
    """(已盖锚点槽位数, 应盖槽位数)。应盖 = 有合法行号的层 × 2。"""
    slots = judged = 0
    for L in scene.get("layers") or []:
        for key, row_key in ANCHOR_FIELDS:
            try:
                int(L.get(row_key))
            except (TypeError, ValueError):
                continue
            slots += 1
            if L.get(key):
                judged += 1
    return judged, slots


def stamp(scene, rows, note=None):
    """就地把 body_rows + 每层 anchor_start/anchor_end 写成当前行文，返回改动槽位数。

    只加键、不动任何既有键与图层顺序：锚点是渲染器不读的额外字段，盖章不许改变成片。
    """
    layers = scene.get("layers") or []
    changed = 0
    scene[BODY_ROWS_KEY] = len(rows)
    for L in layers:
        for key, row_key in ANCHOR_FIELDS:
            try:
                idx = int(L.get(row_key))
            except (TypeError, ValueError):
                continue
            text = rows.get(idx)
            if text is None:
                continue                      # 行号越界，交给越界那条断言，这里不写假锚点
            if L.get(key) != text:
                L[key] = text
                changed += 1
    if note is not None:
        scene["anchor_note"] = note
    return changed


def unstamp(scene):
    """撤掉盖章（回到判据缺席的形状）：供回归和误盖章回退用，不删任何原有键。"""
    scene.pop(BODY_ROWS_KEY, None)
    scene.pop("anchor_note", None)
    for L in scene.get("layers") or []:
        for key, _ in ANCHOR_FIELDS:
            L.pop(key, None)


def _find_anchor(anchor, rows):
    exact = [o for o, t in rows.items() if t == anchor]
    merged = [o for o, t in rows.items() if t != anchor and t.startswith(anchor)]
    return exact, merged


def judge(scene, rows):
    """返回 (problems, judged)。judged=0 表示这一期判不了（无锚点），不是绿。"""
    if not has_anchors(scene):
        return [], 0
    problems = []
    stamped = scene.get(BODY_ROWS_KEY)
    structural = stamped is not None and stamped != len(rows)
    layers = scene.get("layers") or []
    max_order = max(rows) if rows else 0
    judged = 0
    for L in layers:
        name = L.get("name")
        for key, row_key in ANCHOR_FIELDS:
            anchor = (L.get(key) or "").strip()
            if not anchor:
                continue
            try:
                idx = int(L.get(row_key))
            except (TypeError, ValueError):
                continue
            judged += 1
            if idx < 1 or idx > max_order:
                problems.append(
                    f"图层 {L.get('name')} 的 {row_key}={idx} 在本期所选文案版本里没有这一行"
                    f"（该版 {len(rows)} 行）：锚点「{anchor[:24]}」无从比对。"
                    f"要么这一层指向了别的版本，要么文案删过行"
                )
                continue
            cur = rows.get(idx, "")
            if cur == anchor:
                continue
            exact, merged = _find_anchor(anchor, rows)
            where = f"图层 {name} 的 {row_key}={idx}"
            if len(exact) == 1:
                problems.append(
                    f"{where} 指错了行：锚点「{anchor[:24]}」现在整行落在第 {exact[0]} 行，"
                    f"第 {idx} 行是「{cur[:24]}」。把 {row_key} 改成 {exact[0]}；"
                    f"改完重盖章 python3 scripts/stamp-scene-map-anchors.py"
                )
            elif len(exact) > 1:
                problems.append(
                    f"{where} 的锚点「{anchor[:24]}」在第 {exact[:6]} 行都整行命中，行号无法唯一还原："
                    f"这几行文案相同，需要人工确认这一层本来盖的是哪一段"
                )
            elif len(merged) == 1 and not structural:
                problems.append(
                    f"{where} 的锚点「{anchor[:24]}」被并进了第 {merged[0]} 行"
                    f"（「{rows[merged[0]][:30]}」）：这一行不再是独立的一行，"
                    f"它后面所有切点都可能错一行。按锚点重排行号后重盖章"
                )
            elif structural:
                problems.append(
                    f"{where} 对不上：锚点记的是「{anchor[:24]}」，第 {idx} 行现在是「{cur[:24]}」。"
                    f"盖章时正文 {stamped} 行、现在 {len(rows)} 行——文案合并/删过行，"
                    f"这份图层表的行号整体错位，逐层按锚点改回行号（锚点命中的那一行就是正确行号）"
                )
            else:
                problems.append(
                    f"{where} 对不上，但行数没变（{len(rows)} 行）：第 {idx} 行现在是「{cur[:24]}」，"
                    f"锚点记的是「{anchor[:24]}」。多半只是改了字没改行结构，"
                    f"确认没有整行增删之后直接重盖章即可，不要去重排行号"
                )
    return problems, judged
