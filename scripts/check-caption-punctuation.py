#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕上屏文本验收门禁：证明「画面上真的一个标点、一个空白间隙都没有」，而且没被清洗吃掉一个字。

为什么需要它：去标点发生在渲染最后一步（`caption_display()`），一旦有人把剥离点挪到
折行之前、或把 CSV 的标点提前删干净，成片上不会出现报错，只会静默退化出
「1942年2月巴西一座山里的小城一个」这种 24 字无断点长串（《昨日的世界》实测）。
所以规则要落地，必须有一道拿字幕轨当证据的门禁，而不是靠人眼抽帧。

真源只有一份：`scripts/lib/subtitle-punctuation.json` 的 `displayStrip` / `displayKeep` /
`displayBreak`。渲染器（Python 兜底路径）、`scripts/lib/subtitle-display.mjs`（HyperFrames
body 路径）和本脚本都读它，任何一处另立字符表都算违规。

节奏由换行承担，不由空格承担（2026-09-27 起）。旧口径把句中断点号换成一个全角空格，
屏上留下「漂亮 聪明 有钱」式的空洞；现在断点号就是换行点，所以上屏行内出现任何空白
都是违规，一条字幕的上屏行数超过 `displayMaxLines` 也是违规。

字幕轨是证据，不是 script-body.csv 的副本：渲染器在 `caption_lines()` 折行、闭合标点归位
之后，才按共享策略去标点，并把真正画到画面上的行以 `display` 字段（多行用 " / " 分隔）
写进 `caption-track.json`，同时落下 `captionStyle` 标记（形如 `line-break@2026-09-27`）。
本脚本核的就是这些字段。标记决定严格程度：按新口径渲的轨（标记版本 ≥ 真源版本）任何
间隙/超行都判红；没有标记的历史轨只进「待重渲清单」告警，否则规则落地的那一刻会把
28 期已交付成片一起红灯、把正在渲染的会话一起掐断。缺 `display` 字段仍然算违规——
那等于这期连上屏文本都没留证据。

用法：
  python3 scripts/check-caption-punctuation.py episodes/<书名>/caption-track.json \
      --csv episodes/<书名>/script-body.csv [--png-dir /tmp/<书名>_tpl]
  python3 scripts/check-caption-punctuation.py --all          # 全仓库审计 + 待重渲清单

退出码：0 通过；1 存在违规（供流水线/交付前检查拦截）。
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POLICY_PATH = os.path.join(HERE, "lib", "subtitle-punctuation.json")

# 汉字（含扩展 A 区）：逐字一致性只比汉字/字母/数字，空白与标点不参与。
CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9·]")
WS_RE = re.compile(r"\s")


def load_policy():
    """读唯一真源并自检（配置文件写坏了必须先炸，而不是让门禁瞎放行）。"""
    pol = json.load(open(POLICY_PATH, encoding="utf-8"))
    for retired in ("displayPause", "displayPauseChar"):
        if retired in pol:
            raise SystemExit(f"{POLICY_PATH}: 仍带着已废止的 {retired}——断点号只能换行，"
                             f"不能换成空白字符（口径见 displayBreak / displayBreakTo）")
    strip = set(pol["displayStrip"])
    keep = set(pol.get("displayKeep", []))
    brk = set(pol.get("displayBreak", []))
    to = pol.get("displayBreakTo", "")
    render = pol.get("displayBreakRender", "")
    max_lines = int(pol.get("displayMaxLines", 2))
    style = f"line-break@{pol.get('displayBreakVersion', '0')}"
    both = sorted(strip & keep)
    if both:
        raise SystemExit(f"{POLICY_PATH}: {''.join(both)} 同时出现在 displayStrip 与 displayKeep")
    for ch in keep:
        if ord(ch) < 128:
            raise SystemExit(f"{POLICY_PATH}: displayKeep 不允许放 ASCII 标点「{ch}」")
    if render != "line-break":
        raise SystemExit(f'{POLICY_PATH}: displayBreakRender 只允许 "line-break"，'
                         f"当前是 {render!r}——屏上出现空白间隙的口径已作废")
    if to != "\n":
        raise SystemExit(f"{POLICY_PATH}: displayBreakTo 必须是换行，当前 {to!r}"
                         f"（空白间隙不是节奏，节奏由换行承担）")
    if not brk:
        raise SystemExit(f"{POLICY_PATH}: displayBreak 为空，等于没有断句依据")
    if not brk <= strip:
        raise SystemExit(f"{POLICY_PATH}: displayBreak 里的 {''.join(sorted(brk - strip))} "
                         f"不在 displayStrip，上屏会留下真标点")
    if not 1 <= max_lines <= 3:
        raise SystemExit(f"{POLICY_PATH}: displayMaxLines={max_lines} 不在 1-3 之间")
    return strip, keep, brk, max_lines, style


def residual(text, strip):
    return sorted({ch for ch in str(text) if ch in strip})


def gaps(text):
    """一行上屏文本里的空白间隙字符（行分隔已经是数组元素，这里绝不该有空白）。"""
    return sorted({ch for ch in str(text) if WS_RE.search(ch)})


def core_chars(text):
    return "".join(CHAR_RE.findall(str(text)))


def read_csv_rows(path):
    """script-body.csv → {order: text}。CSV 是文本真源，也是「清洗有没有吃字」的对照物。"""
    rows = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            order = (r.get("order") or "").strip()
            if order.isdigit():
                rows[int(order)] = (r.get("text") or "").strip()
    return rows


def norm_cap_name(name):
    """cap 文件名里的 0 补齐宽度按各期渲染器习惯不同（cap-000-01 / cap-000-1），归一后再比。"""
    stem = re.sub(r"\.png$", "", str(name))
    parts = stem.split("-")
    if len(parts) == 3 and parts[0] == "cap" and all(p.isdigit() for p in parts[1:]):
        return f"cap-{int(parts[1])}-{int(parts[2])}.png"
    return stem + ".png"


def display_lines(item):
    """取一条字幕轨的上屏行；没有 display 字段说明这期是老渲染器出的，返回 None。"""
    if "display" not in item:
        return None
    return [ln for ln in str(item["display"]).split(" / ") if ln]


def track_is_new_style(track, expected_style):
    """字幕轨是否按「断点号→换行」的现行口径渲出来。

    判据是渲染器落的 `captionStyle` 标记，而不是文件 mtime——mtime 只能说明有人碰过文件，
    不能说明画面变了。标记形如 `line-break@2026-09-27`，版本用 ISO 日期直接比大小。
    """
    seen = [str(item.get("captionStyle", "")) for item in track if item.get("captionStyle")]
    if not seen:
        return False
    want_ver = expected_style.split("@", 1)[-1]

    def ver(style):
        return style.split("@", 1)[-1] if "@" in style else ""
    return all(style.startswith("line-break@") and ver(style) >= want_ver for style in seen)


def check_track(track_path, csv_path, png_dir, strip, keep, max_lines=2,
                strict=True):
    """返回 (违规列表, 告警列表)。告警不计入退出码，用于历史轨的待重渲清单。"""
    bad = []
    warn = []
    track = json.load(open(track_path, encoding="utf-8"))
    if not track:
        return [f"{track_path}: 字幕轨为空"], warn, {"entries": 0, "gap_rows": 0,
                                                     "over_rows": 0, "legacy": 0}

    # 1) 上屏零标点 + 零空白间隙 + 行数上限（前提是轨里真的有上屏文本）
    legacy = sum(1 for item in track if display_lines(item) is None)
    if legacy:
        # 没落 display 的轨按口径分档：交付验收（--strict）判红，历史轨全仓库审计只进待重渲清单。
        msg = (f"字幕轨 {legacy}/{len(track)} 条没有 display 字段"
               f"（老渲染器只落了带标点的 CSV 原文，上屏残留查不出来，这期需按新规则重渲）")
        (bad if strict else warn).append(msg)
    gap_rows = 0
    over_rows = 0
    for item in track:
        lines = display_lines(item)
        if lines is None:
            continue
        order = item.get("order")
        if len(lines) > max_lines:
            over_rows += 1
            msg = (f"order {order} 一条字幕上屏 {len(lines)} 行 > displayMaxLines={max_lines}："
                   f"{' / '.join(lines)}（应顺延成下一条字幕，不是缩字号）")
            (bad if strict else warn).append(msg)
        for ln in lines:
            res = residual(ln, strip)
            if res:
                bad.append(f"order {order} 上屏残留标点 {''.join(res)}：{ln}")
            # 断点号已经换成换行：行内出现任何空白（半角/全角空格、制表符）都是间隙残留。
            gp = gaps(ln)
            if gp:
                gap_rows += 1
                shown = " ".join(f"U+{ord(ch):04X}" for ch in gp)
                msg = (f"order {order} 上屏留了空白间隙（{shown}），节奏该由换行承担：{ln}")
                (bad if strict else warn).append(msg)
        if not lines:
            bad.append(f"order {order} 上屏文本为空（标点剥离把整行剥没了）")
    if gap_rows and not strict:
        warn.append(f"合计 {gap_rows} 条字幕有空隙残留：这是旧口径（断点号→全角空格）渲的轨，需重渲")

    # 2) displayKeep 必须活着：CSV 里有 ·，字幕轨就得有 ·
    if csv_path and os.path.exists(csv_path):
        rows = read_csv_rows(csv_path)
        by_order = {}
        for item in track:
            by_order.setdefault(int(item["order"]), []).append(item)
        missing_orders = sorted(set(rows) - set(by_order))
        if missing_orders:
            bad.append("字幕轨缺 order：" + ", ".join(map(str, missing_orders[:12]))
                       + ("" if len(missing_orders) <= 12 else " …"))
        disp = {o: items for o, items in by_order.items()
                if all(display_lines(i) is not None for i in items)}   # 老轨只报缺 display
        disp_by_order = {o: "".join("".join(display_lines(i)) for i in items)
                         for o, items in disp.items()}
        keep_in_csv = {ch for t in rows.values() for ch in keep if ch in t}
        keep_in_track = {ch for txt in disp_by_order.values() for ch in keep if ch in txt}
        for ch in sorted(keep_in_csv - keep_in_track):
            if disp_by_order:
                bad.append(f"displayKeep 的「{ch}」在 CSV 里有、上屏全无"
                           f"（剥离误吃了专有名词分隔符）")
        # 3) 逐字一致：CSV 去标点去空格 == 该 order 各条上屏文本按序拼接
        for order in sorted(disp_by_order):
            src = core_chars("".join(str(rows.get(order, "")).split()))
            got = core_chars(disp_by_order[order])
            if src != got:
                bad.append(f"order {order} 上屏文本与 CSV 逐字不一致：\n"
                           f"       CSV : {src}\n       上屏: {got}")

    # 4) cap-*.png 与字幕轨一一对应（陈旧 PNG 会让 qc-caption-alignment 报假错配）
    if png_dir and os.path.isdir(png_dir):
        pngs = sorted(glob.glob(os.path.join(png_dir, "cap-*.png")))
        track_here = os.path.dirname(os.path.abspath(track_path)) == os.path.abspath(png_dir)
        if not pngs and not track_here:
            pass      # 持久轨在 episodes/，中间层已被清理：没 PNG 可核，不算违规
        elif len(pngs) != len(track):
            bad.append(f"{png_dir} 有 {len(pngs)} 张 cap-*.png，字幕轨 {len(track)} 条"
                       f"（数量不符：陈旧 PNG 会污染 IoU 候选池）")
        else:
            # 期专用兜底渲染器最容易漏掉 index/display：漏了这里会直接 KeyError，
            # 看起来像门禁自己的 bug，于是被跳过——必须报成一条明确违规。
            missing = [i for i in track if "index" not in i]
            if missing:
                bad.append(f"字幕轨有 {len(missing)} 条缺 index 字段（例 order "
                           f"{', '.join(str(i['order']) for i in missing[:5])}）："
                           "渲染器必须按 cap-<index>-<order>.png 的对应关系落轨，否则无法核对上屏 PNG")
            else:
                want = {norm_cap_name(f"cap-{int(i['index']):03d}-{int(i['order'])}.png") for i in track}
                have = {norm_cap_name(os.path.basename(p)) for p in pngs}
                if want != have:
                    diff = sorted(want ^ have)[:6]
                    bad.append("cap-*.png 命名与字幕轨 index/order 对不上，例如 " + ", ".join(diff))
    stats = {"entries": len(track), "gap_rows": gap_rows, "over_rows": over_rows,
             "legacy": legacy}
    return bad, warn, stats


def _track_path_for(episode_dir, name):
    """字幕轨优先看 episodes/（持久证据），退到渲染中间目录 /tmp/<书名>_tpl/。"""
    return next((p for p in (os.path.join(episode_dir, "caption-track.json"),
                             os.path.join(f"/tmp/{name}_tpl", "caption-track.json"))
                 if os.path.exists(p)), None)


def audit_all(strip, keep, brk, max_lines, style):
    """全仓库审计：每期一条状态，并把旧口径轨的空隙/超行数量汇成待重渲清单。"""
    print(f"[policy] 上屏删除 {len(strip)} 个标点 / 断点号 {''.join(sorted(brk))} → 换行"
          f" / 一条字幕最多 {max_lines} 行 / 标记 {style}")
    done = need = 0
    todo = []
    backlog = []
    for ep in sorted(glob.glob(os.path.join(ROOT, "episodes", "*"))):
        if not os.path.exists(os.path.join(ep, "script-body.csv")):
            continue
        name = os.path.basename(ep)
        tp = _track_path_for(ep, name)
        if tp is None:
            todo.append(name)
            continue
        track = json.load(open(tp, encoding="utf-8"))
        strict = track_is_new_style(track, style)
        # cap-*.png 只在渲染中间目录里，持久轨（episodes/）不带
        bad, warn, stats = check_track(tp, os.path.join(ep, "script-body.csv"),
                                       f"/tmp/{name}_tpl", strip, keep, max_lines, strict)
        final = sorted(glob.glob(os.path.join(ep, "renders", "*final*.mp4")))
        tag = "新口径" if strict else ("旧口径·无证据" if stats["legacy"] == stats["entries"]
                                       else "旧口径")
        print(f"  {'✅' if not bad else '❌'} {name:<24} {stats['entries']:>3} 条 [{tag}]"
              f"{' / 成片 ' + os.path.basename(final[0]) if final else ' / 无成片'}")
        for line in bad[:4]:
            print(f"      · {line}")
        if not strict and (stats["gap_rows"] or stats["over_rows"] or stats["legacy"]):
            backlog.append((name, stats["entries"], stats["gap_rows"], stats["over_rows"],
                            stats["legacy"], bool(final)))
        for line in warn[:2]:
            print(f"      ⚠️ {line}")
        done += not bad
        need += bool(bad)
    print(f"[audit] 通过 {done} / 违规 {need} / 无字幕轨 {len(todo)}")
    for name in todo:
        print(f"      ⚠️ {name}：没有 caption-track.json，成片按老规则渲染，需重渲")
    if backlog:
        backlog.sort(key=lambda row: -(row[2] + row[3]))
        print(f"[backlog] 旧口径待重渲：{len(backlog)} 期 / "
              f"{sum(r[2] for r in backlog)} 条空格间隙 / {sum(r[3] for r in backlog)} 条超行 / "
              f"{sum(r[4] for r in backlog)} 条连上屏证据（display）都没有")
        print(f"  {'期数':<26}{'字幕':>6}{'空格间隙':>10}{'超行':>8}{'无证据':>8}  已有成片")
        for name, entries, gap_n, over_n, legacy_n, has_final in backlog:
            print(f"  {name:<26}{entries:>8}{gap_n:>12}{over_n:>10}{legacy_n:>10}"
                  f"  {'有' if has_final else '无'}")
    sys.exit(1 if need else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tracks", nargs="*", help="caption-track.json 路径")
    ap.add_argument("--csv", default="", help="对应 script-body.csv，做逐字一致性对照")
    ap.add_argument("--png-dir", default="", help="渲染中间目录，核对 cap-*.png 是否与轨一致")
    ap.add_argument("--all", action="store_true",
                    help="审计 episodes/*/caption-track.json，报告每期状态与待重渲清单")
    ap.add_argument("--strict", action="store_true",
                    help="即使字幕轨没落 captionStyle 标记，也把空格间隙/超行判成违规（重渲验收用）")
    args = ap.parse_args()

    strip, keep, brk, max_lines, style = load_policy()
    global POLICY_BREAKS
    POLICY_BREAKS = brk

    if args.all:
        audit_all(strip, keep, brk, max_lines, style)

    if not args.tracks:
        raise SystemExit("给一个 caption-track.json，或用 --all 审计全仓库")
    total = 0
    for tp in args.tracks:
        track = json.load(open(tp, encoding="utf-8"))
        strict = args.strict or track_is_new_style(track, style)
        bad, warn, stats = check_track(tp, args.csv, args.png_dir, strip, keep,
                                       max_lines, strict)
        for line in warn:
            print(f"⚠️ {line}")
        n = stats["entries"]
        if bad:
            print(f"❌ {tp}：{n} 条字幕，{len(bad)} 处违规"
                  f"{'' if strict else '（旧口径轨：空格间隙只告警）'}")
            for line in bad:
                print(f"   · {line}")
        else:
            print(f"✅ {tp}：{n} 条字幕全部零标点、零空白间隙，单条不超 {max_lines} 行，"
                  f"且与 CSV 逐字一致{'' if strict else '（旧口径轨：间隙仅告警）'}")
        total += len(bad)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
