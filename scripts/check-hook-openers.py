#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钩子第二行「代价落点」的开头重复检查（回补管线的选材期门禁）。

为什么要有这一步：钩子两行是固定装置——第一行金句，第二行把金句折回观众自己身上。
第一行每期独立创作，30 期查下来开头全不撞；第二行却极易收敛到同一个动作上。
2026-10-03 盘点 30 期：「我合上书」10 期、「我读…」9 期，两种开头吃掉 19/30。
单期读都没问题，连着录、连着发就是同一句话说三十遍。这个重复**没有任何下游门禁
能看见**——文案已经落盘、字数合规、五问自检过了、封面钩子也对得上，八道门禁全绿，
而缺陷就在观众耳朵里。

两种模式：
  · 默认           —— 盘点全部 episodes/*/script-hook.csv 第二行，按族报数，
                      超过 --cap（默认语料的 25%）的族判红。只盘点，不改文案。
  · --plan A B C   —— 一批**还没录音**的草稿在写盘前先撞一遍：既撞已出货的语料，
                      也撞同批彼此。有撞车就非零退出，改写完再跑一次。
                      草稿从 data/hook-drafts/<书名>.json 的 hook_lines[1] 取。

族怎么分：「我+读书动作」这一簇是这次真正的病，机器自己看不出「合上书」和「读完那」
是一类，所以这一簇给了显式正则表（READING_ACT）。其余一律回落到「去掉主语后取前
四字」这个纯字面桶——桶不需要维护，新出现的集中会在盘点里自己冒出来。要加族就加进
READING_ACT，别往剧集里塞例外。

用法：
  python3 scripts/check-hook-openers.py
  python3 scripts/check-hook-openers.py --plan 女儿红 推销员之死 卡利古拉
  python3 scripts/check-hook-openers.py --json        # 给别的脚本读
"""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes")
DRAFTS = os.path.join(ROOT, "data", "hook-drafts")

# 「我/你 + 读书动作」这一簇。写法上留了余地：合上书 / 合上最后一页 / 读到最后一行 /
# 翻到封底 都算同一族，因为观众听到的是同一个装置，不是同一个词。
READING_ACT = re.compile(
    r"^(?:我|你|他|她)?\s*(?:合上(?!眼)|放下(?!来)|读完|读到|读第|翻到|掩上|闭上书)"
)

# 第二个装置：把某句别人说过的话立起来当引子（那句算了 / 那句等稳 / 那句都过去了 /
# 那句好听的话）。它和「读书动作」是两种不同的收敛，分开才看得出各自会不会长歪。
QUOTED_LINE = re.compile(r"^(?:那|这)句")

Pronoun = re.compile(r"^(?:我|你|他|她|那|这)")

# 阈值只有这一处。CAP 是「某族占已出货语料的比例上限」，超过就过载，新批次不许再往里加；
# BATCH_FRACTION 是同一批内部的占比上限（10 期一批 → 同族最多 2 期）。
CAP = 0.25
BATCH_FRACTION = 0.2


def second_line(lines):
    """钩子第二行 = 代价落点。只有一行时返回 None（单行钩子不参与族统计）。"""
    return lines[1] if len(lines) > 1 else None


def family(text):
    if not text:
        return None
    t = text.strip()
    if READING_ACT.match(t):
        return "读书动作（合上书/读完/读到…）"
    if QUOTED_LINE.match(t):
        return "转述某句话（那句…/这句话…）"
    # 回落桶：剥掉一个主语的字数了不计，取剥完后的前四个非空白字符。
    core = re.sub(r"\s", "", t)
    core = Pronoun.sub("", core, count=1) if Pronoun.match(core) else core
    return "「" + core[:4] + "」"


def shipped_corpus():
    out = []
    for ep in sorted(os.listdir(EP)):
        p = os.path.join(EP, ep, "script-hook.csv")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig", newline="") as fh:
            rows = [(int(r["order"]), (r["text"] or "").strip())
                    for r in csv.DictReader(fh) if (r.get("text") or "").strip()]
        rows.sort(key=lambda x: x[0])
        out.append((ep, second_line([t for _, t in rows])))
    return out


def draft_line2(book):
    p = os.path.join(DRAFTS, f"{book}.json")
    if not os.path.exists(p):
        raise SystemExit(f"没有草稿：{p}")
    d = json.load(open(p, encoding="utf-8"))
    return second_line([l.strip() for l in d.get("hook_lines") or [] if l.strip()])


def longest_shared(a, b, minimum=4):
    """两段文案之间最长的连续公共串（少于 minimum 返回空）。
    存在的理由：封面钩子和钩子第二行必须同主题（AGENTS.md 要求的），但**不能共享字面**——
    封面是看的、片头是听的，同一串字先读后听一遍，观众的反应是「刚才那句我读过了」，
    代价落点就掉回复述。《卡利古拉》2026-10-03 就是这么撞上的：
    封面「都过去了这句你说过几回」，第二行「那句都过去了，我对自己说过多少次」。
    4 个字是分界：3 字以内是同一主题的必然重叠（「点头」「那几年」），不构成复述。
    """
    best = ""
    a, b = (a or ""), (b or "")
    for i in range(len(a)):
        for j in range(len(a), i + minimum - 1, -1):
            if j - i <= len(best):
                break
            g = a[i:j]
            if g in b and len(g) > len(best):
                best = g
                break
    return best if len(best) >= minimum else ""


def census(items):
    fam, members = {}, {}
    for ep, text in items:
        f = family(text)
        if not f:
            continue
        fam[f] = fam.get(f, 0) + 1
        members.setdefault(f, []).append(ep)
    return fam, members


def report(fam, members, total, cap_frac):
    lines = []
    reds = []
    for f, n in sorted(fam.items(), key=lambda kv: (-kv[1], kv[0])):
        share = n / total if total else 0
        mark = "🔴" if share > cap_frac else "  "
        lines.append(f"{mark} {n:>2}/{total}  {share:>5.0%}  {f:<28} {'、'.join(members[f])}")
        if share > cap_frac:
            reds.append(f)
    return lines, reds


def cover_echo(book, cover_hook, line2):
    """封面钩子 ↔ 第二行的逐字复述。返回问题描述，干净返回 None。"""
    g = longest_shared(cover_hook, line2)
    if not g:
        return None
    return (f"《{book}》封面钩子「{cover_hook}」与第二行「{line2}」共享连续 {len(g)} 字"
            f"「{g}」：封面是看的，片头是听的，同一串字过两遍就变成复述。"
            f"同主题要保持（AGENTS.md 要求），换字面。")


def plan_problems(pairs, corpus, cap=CAP, covers=None):
    """一批候选第二行 vs 已出货语料 + vs 同批彼此。返回问题列表，空列表即干净。
    给 promote-hook-draft.py 直接 import——断言只在这一处定义，写盘路径不另立口径。

    过载族用占比而不是固定条数：语料从 30 长到 113，「超过 3 期」会越管越松，
    而「合上书」这种族恰恰是攒到十几期才听出毛病的。
    covers 给了就顺带查封面复述（promote 那边有 cover_hook，草稿期就能查）。"""
    corpus = [(e, t) for e, t in corpus if e not in {b for b, _ in pairs}]
    total = sum(1 for _, t in corpus if t) or 1
    fam_c, mem_c = census(corpus)
    fam_p, mem_p = census(pairs)
    overloaded = {f for f, n in fam_c.items() if n / total > cap}
    problems = []
    for b, t in pairs:
        f = family(t)
        if f in overloaded:
            problems.append(
                f"《{b}》第二行「{t}」属于已经过载的族 {f}"
                f"（出货 {fam_c[f]}/{total} 期：{'、'.join(mem_c[f][:4])}…）")
    for f in sorted(fam_p):
        if fam_p[f] > max(1, int(len(pairs) * BATCH_FRACTION)):
            problems.append(f"同批内 {f} 出现 {fam_p[f]} 次：{'、'.join(mem_p[f])}")
    for b, t in pairs:
        cov = (covers or {}).get(b)
        msg = cover_echo(b, cov, t) if cov else None
        if msg:
            problems.append(msg)
    return problems


def shipped_covers():
    out = {}
    for ep in sorted(os.listdir(EP)):
        bp = os.path.join(EP, ep, "brief.json")
        if os.path.exists(bp):
            out[ep] = (json.load(open(bp, encoding="utf-8")).get("cover_hook") or "").strip()
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    cap = next((float(a.split("=", 1)[1]) for a in sys.argv[1:]
                if a.startswith("--cap=")), CAP)

    if "--cover-echo" in sys.argv:
        # 已出货语料的盘点：这条查得出「封面读一遍、片头再听一遍」，
        # 但只有**没录音**的期能改——音频是用户本人的声音，不是可重渲的产物。
        covers = shipped_covers()
        hits = [(ep, m) for ep, t in shipped_corpus()
                if (m := cover_echo(ep, covers.get(ep, ""), t))]
        recorded = {ep for ep in os.listdir(EP)
                    if os.path.exists(os.path.join(EP, ep, "钩子.mp3"))}
        fixable = [(ep, m) for ep, m in hits if ep not in recorded]
        if as_json:
            print(json.dumps({"hits": [m for _, m in hits],
                              "fixable": [m for _, m in fixable]},
                             ensure_ascii=False, indent=2))
            sys.exit(1 if fixable else 0)
        for ep, m in hits:
            print(f"{'🔒' if ep in recorded else '🔴'} {m}"
                  + ("   （音频已录，冻结；只进下一批的规避清单）" if ep in recorded
                     else "   ← 未录，现在改最便宜"))
        print(f"封面↔第二行逐字复述：{len(hits)} 期，其中未录可改 {len(fixable)} 期"
              if hits else "✅ 没有封面与第二行逐字复述")
        # 已录音的那些是历史事实，不该让这条盘点永远红着——只有「未录可改」的才判红。
        sys.exit(1 if fixable else 0)

    if "--plan" in sys.argv:
        # 草稿期检查：这批还没录音，改文案是免费的；录完再改就要重跑轴、重出封面、重渲。
        # 草稿可能已经提升过一次（第三批就是：文案早落盘了，只是还没录音），
        # 所以 plan_problems 会先把这几期从语料里摘掉，别让它们自己算成「已出货」。
        pairs = [(b, draft_line2(b)) for b in args]
        problems = plan_problems(pairs, shipped_corpus(), cap, shipped_covers())
        if as_json:
            print(json.dumps({"problems": problems}, ensure_ascii=False, indent=2))
        elif problems:
            print("钩子第二行开头撞车（还没录音，现在改最便宜）：")
            for p in problems:
                print(f"   · {p}")
            sys.exit(1)
        else:
            print(f"✅ 钩子第二行开头：{len(pairs)} 期草稿无撞车（过载族阈值 {cap:.0%}）")
        sys.exit(0)

    corpus = shipped_corpus()
    total = sum(1 for _, t in corpus if t)
    fam, mem = census(corpus)
    out, reds = report(fam, mem, total, cap)
    if as_json:
        print(json.dumps({"total": total, "cap": cap,
                          "families": fam, "members": mem, "over_cap": reds},
                         ensure_ascii=False, indent=2))
        sys.exit(1 if reds else 0)
    print(f"钩子第二行开头盘点 ｜ {total} 期 ｜ 过载阈值 >{cap:.0%}")
    print("\n".join(out))
    if reds:
        print(f"\n🔴 {len(reds)} 个族超过阈值：{'；'.join(reds)}")
        print("   这些族不要再往新批次里加；下一批用 --plan 先撞一遍再落盘。")
        sys.exit(1)
    print("✅ 没有族超过阈值")


if __name__ == "__main__":
    main()
