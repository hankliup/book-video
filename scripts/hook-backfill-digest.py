#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为一段结构（无 script-hook.csv）的历史剧集生成「写钩子用的摘要」。

写钩子需要三样东西同时在场：正文开头已经说了什么（钩子不得撞车）、正文里最可能被
当作核心金句的那一句（钩子要前置它，且封面不得印它）、这一期设计出来要打中谁
（brief.json 的 emotion / audience）。逐期用 Read 把 90~150 行 script-body.csv 读一遍，
113 期读下来既慢又会在中途丢掉前面几期的记忆，所以把摘要压缩成固定字段先落盘。

正文首行（order 1）是书名，口播从 order 2 起，所以 opening 取的是 order 2 起。

用法：
  python3 scripts/hook-backfill-digest.py "<书名>" [更多书名...]
  python3 scripts/hook-backfill-digest.py --all
输出：JSON（单期对象，或多期数组）到 stdout。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes")


def read_rows(path):
    """script-body.csv → [(order, text)]，跳过表头，容忍引号与空行。"""
    import csv
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            txt = (rec.get("text") or "").strip()
            if not txt:
                continue
            try:
                order = int(rec.get("order") or 0)
            except ValueError:
                continue
            out.append((order, txt))
    out.sort(key=lambda x: x[0])
    return out


def golden_candidates(rows):
    """挑出正文里像「金句」的行：短、独立成句、含判断语气、不靠上下文。

    金句的形态在这套剧集里很稳定——它是一句可以被单独抄下来的判断，通常在正文开头
    附近或结尾回扣处。这里只做机械筛选给出候选，最终选哪句由写钩子的人判断。
    """
    body = [r for r in rows if r[0] >= 2]
    scored = []
    for order, txt in body:
        n = len(re.sub(r"[，。？！、；：…\s]", "", txt))
        if n < 6 or n > 24:
            continue
        score = 0.0
        # 判断句特征
        if re.search(r"(是|从来|永远|从来都|才算|才配|才是|不过|其实|本来|终究|到底)", txt):
            score += 1.2
        if txt.endswith("。"):
            score += 0.6
        # 纯叙事行（带「那天」「第二年」「他/她+动作」）降权
        if re.search(r"(那天|第二年|后来|于是|然后|那天晚上)", txt):
            score -= 1.0
        if n <= 16:
            score += 0.6
        # 开头与结尾附近加分（house style 是金句前置 + 收尾回扣）
        if order <= 12:
            score += 1.0
        if order >= body[-1][0] - 12:
            score += 1.0
        scored.append((score, order, txt))
    scored.sort(key=lambda x: (-x[0], x[1]))
    seen, out = set(), []
    for score, order, txt in scored:
        if txt in seen:
            continue
        seen.add(txt)
        out.append({"order": order, "text": txt, "score": round(score, 2)})
        if len(out) >= 6:
            break
    return out


def digest(name):
    d = os.path.join(EP, name)
    sc = os.path.join(d, "script-body.csv")
    if not os.path.exists(sc):
        raise SystemExit(f"{name}: 没有 script-body.csv（可能是新命名 script-body.csv，本期不在回补范围）")
    rows = read_rows(sc)
    brief = {}
    bp = os.path.join(d, "brief.json")
    if os.path.exists(bp):
        brief = json.load(open(bp, encoding="utf-8"))
    body = [r for r in rows if r[0] >= 2]
    return {
        "book": name,
        "display_title": brief.get("display_title") or name,
        "author": brief.get("author") or "",
        "script_version": brief.get("scriptVersion") or "A",
        "emotion": brief.get("emotion") or "",
        "audience": brief.get("audience") or "",
        "visual_direction": brief.get("visual_direction") or "",
        "body_row_count": len(body),
        "opening": [{"order": o, "text": t} for o, t in body[:8]],
        "ending": [{"order": o, "text": t} for o, t in body[-5:]],
        "golden_candidates": golden_candidates(rows),
        "has_hook": os.path.exists(os.path.join(d, "script-hook.csv")),
    }


def all_hookless():
    out = []
    for name in sorted(os.listdir(EP)):
        d = os.path.join(EP, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        if os.path.exists(os.path.join(d, "script-hook.csv")):
            continue
        if os.path.exists(os.path.join(d, "script-body.csv")):
            out.append(name)
    return out


def main():
    args = sys.argv[1:]
    if args == ["--all"]:
        args = all_hookless()
    elif not args:
        raise SystemExit(__doc__)
    items = [digest(a) for a in args]
    print(json.dumps(items if len(items) > 1 else items[0], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
