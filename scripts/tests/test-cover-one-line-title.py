#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面书名「一律单行 + 装不下就缩小字号」的双向回归。

三条裁决叠在这个文件上，缺任何一条都读不懂下面在断言什么：
  · 2026-10-01「封面图片的字体模糊不清晰，需要重新调整优化，字体放大」→ 96px 以下算糊；
  · 2026-10-07「书名不要有换行的现象」→ 单行优先；
  · 2026-10-08「修改视频封面图中的书名排版，**不要有换行**，并沉淀这个规则」→ 一律单行，
    折行那一支退役；
  · 2026-10-09「根据合适排版进行缩小字号」→ 装不下的出路是**缩字号**，不是折行、也不是改名。

10-08 那版留了一条「单行掉到清晰下限 96px 以下就报错，让用户写 cover_title」的口子，
并把它做成了全库红灯（13 期）。10-09 用户否掉的正是那个口子给出的两个选项：不写短名、
不折行，而是照出、把字号缩到排版合适为止。于是 96px 从**拒绝线**变成**必须留痕的告警线**，
拒绝线挪到 TITLE_ABSOLUTE_MIN 60px（＝钩子行最小字号，书名再小就比钩子还小，层级倒了）。
现目录 152 期里 13 期落在 96px 以下、0 期落在 60px 以下，所以 0 期需要 cover_title。

五个方向都必须被测到，否则这些断言会自己烂掉：
  ① 正向：本期书名 1 行、≥ 绝对下限、不出框，且阶梯交回的下限就是绝对下限本身。
  ② 非空转（三种回退形状各自要能让对应的红翻起来）：
     a) 把**老阶梯**（一到 TITLE_MIN 就折）回放进 check_title，「书名折了行」必须报红；
     b) 阶梯把下限写回清晰下限（等于撤回缩字号）必须报红；
     c) 单行低于清晰下限却**不写**那条 note（等于把代价抹安静）必须报红。
     不测这三头，「一律单行 + 缩字号」可能只是在测一个永远不成立的夹具。
  ③ 反向：真的装不进绝对下限的书名（合成 16 字）必须报错并指名 cover_title 与「不折行」，
     既不许折行也不许悄悄出比钩子还小的字。
  ④ 全库账：单行 100%、老阶梯确实会折行（这条裁决真的改了东西）、判红 0、
     低于清晰下限的期数 == 带那条留痕警告的期数、写了 cover_title 的期确实连绝对下限都装不进。
  ⑤ 静态守卫：判据只有一个真源，回归仍在调它，且两条线（96 留痕 / 60 拒绝）各自的
     字样还在各自的文件里。
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def load_wrapping_checker():
    """按路径加载 check-cover-wrapping.py（文件名带连字符，不能 import）。"""
    path = os.path.join(ROOT, "scripts", "check-cover-wrapping.py")
    spec = importlib.util.spec_from_file_location("check_cover_wrapping", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    checker = load_wrapping_checker()
    ns = checker.load_helpers()
    legible_min = ns["TITLE_LEGIBLE_MIN"]
    abs_min = ns["TITLE_ABSOLUTE_MIN"]
    safe_w = ns["SAFE_W"]
    fails = []

    def ladder(text):
        return ns["wrap_title_lines"](f"《{text}》")

    def run_ladder(title):
        """跑本期封面真正会画的那条路，报错就原样交出去。"""
        return ladder(title)

    # ──  ① 正向：本期书名单行、在绝对下限之上、不出框 ────────────────────────
    this_ep = "一颗简单的心"
    lines, size, floor, notes = run_ladder(this_ep)
    if lines != [f"《{this_ep}》"]:
        fails.append(f"① {this_ep} 仍被折行：{lines}")
    if size < abs_min:
        fails.append(f"① 单行字号 {size}px 低于绝对下限 {abs_min}px")
    if floor != abs_min:
        fails.append(f"① 阶梯交回的下限是 {floor}，不是绝对下限 {abs_min}：缩字号那一支没打开")
    if ns["_w"](lines[0], size) > safe_w:
        fails.append(f"① 单行出框：{int(ns['_w'](lines[0], size))}px > {safe_w}px")
    # 这一期本来就在清晰下限之上，不该带小字警告——警告不许变成无条件播报
    if size >= legible_min and any("清晰下限" in n for n in notes):
        fails.append(f"① {this_ep} 单行 {size}px ≥ 清晰下限 {legible_min}px 却带了留痕警告")

    # ──  ② 非空转：三种回退形状各自必须能让对应的红翻起来 ────────────────────
    # (a) 老阶梯在同一个书名上确实交出两行，并且被「书名折了行」抓到
    old = checker.old_ladder(ns, f"《{this_ep}》")
    if len(old) < 2:
        fails.append(f"②(a) 对照失效：老阶梯在 {this_ep} 上没有折行（{old}），"
                     "这条裁决没改变任何东西，①是空断言")
    saved = ns["wrap_title_lines"]
    ns["wrap_title_lines"] = lambda text: (
        checker.old_ladder(ns, text),
        min(ns["_fit_size"](ln, ns["TITLE_MAX"], ns["TITLE_MIN"])
            for ln in checker.old_ladder(ns, text)),
        ns["TITLE_MIN"], [])
    try:
        reds, _warns, shape = checker.check_title(ns, this_ep)
    finally:
        ns["wrap_title_lines"] = saved
    if not any("书名折了行" in r for r in reds):
        fails.append(f"②(a) 老阶梯（折成 {old}）没有被第 4 条判据抓到：{reds} —— 判据是空断言")
    if shape != "wrapped":
        fails.append(f"②(a) 回放形态应为 wrapped，实得 {shape}")
    reds_new, _w2, shape_new = checker.check_title(ns, this_ep)
    if any("书名折了行" in r for r in reds_new):
        fails.append(f"②(a) 当前阶梯被自己的第 4 条判红了：{reds_new}")
    if shape_new != "single":
        fails.append(f"②(a) 当前阶梯形态应为 single，实得 {shape_new}")

    # (b) 阶梯把返回的下限写回清晰下限＝撤回 10-09 缩字号，必须报红
    ns["wrap_title_lines"] = lambda text: ([text], 60, legible_min,
                                           ["书名低于清晰下限：占位 note"])
    try:
        reds_floor, _w, _s = checker.check_title(ns, this_ep)
    finally:
        ns["wrap_title_lines"] = saved
    if not any("阶梯交回的下限" in r for r in reds_floor):
        fails.append(f"②(b) floor 被写回清晰下限 {legible_min} 没有报红：{reds_floor} —— "
                     "那条下限返回值没被当成判据在守")

    # (c) 低于清晰下限却不写 note＝把代价抹安静，必须报红
    #     用全库真实超长书名（60~92px 那批）做夹具：它们就是这条警告存在的理由
    below_ep = "一个无政府主义者的意外死亡"
    _l, below_size, _f, below_notes = run_ladder(below_ep)
    if below_size >= legible_min:
        fails.append(f"②(c) 对照失效：《{below_ep}》单行 {below_size}px 已不在清晰下限之下，"
                     "留痕判据没有真夹具了（全库超长书名变短了？先重数 ④ 那条清单）")
    ns["wrap_title_lines"] = lambda text: ([text], below_size, abs_min, [])
    try:
        reds_silent, _w, _s = checker.check_title(ns, below_ep)
    finally:
        ns["wrap_title_lines"] = saved
    if not any("低于清晰下限却没有留痕警告" in r for r in reds_silent):
        fails.append(f"②(c) {below_size}px 低于清晰下限却没有 note，没报红：{reds_silent} —— "
                     "删警告等于悄悄撤回 2026-10-01 那条裁决，而守卫拦不住")
    reds_kept, _w, _s = checker.check_title(ns, below_ep)
    if any("低于清晰下限却没有留痕警告" in r for r in reds_kept):
        fails.append(f"②(c) 当前阶梯（{below_size}px + note）被留痕判据判红了：{reds_kept}")
    # (d) 比钩子还小＝层级倒了，绝对下限那条红也要能翻起来
    ns["wrap_title_lines"] = lambda text: ([text], abs_min - 4, abs_min,
                                           ["书名低于清晰下限：占位 note"])
    try:
        reds_abs, _w, _s = checker.check_title(ns, this_ep)
    finally:
        ns["wrap_title_lines"] = saved
    if not any("字号掉到绝对下限以下" in r for r in reds_abs):
        fails.append(f"②(d) {abs_min - 4}px 没有报「掉到绝对下限以下」：{reds_abs}")

    # ──  ③ 反向：连绝对下限都装不进的正式书名必须报错指名 cover_title ─────────
    synth = "一个无政府主义者的意外死亡啊"          # 16 个字符（含书名号）×60px > 950px
    if ns["_fit"](f"《{synth}》", abs_min):
        fails.append(f"③ 对照失效：《{synth}》在绝对下限 {abs_min}px 下其实装得进单行，"
                     "③ 是空断言——换一个更长的合成书名，别改绝对下限")
    try:
        got, got_size, _f, _n = ladder(synth)
        fails.append(f"③ 16 字书名没有报错，交出 {got} @ {got_size}px："
                     "要么折了行，要么把字出到了比钩子还小")
    except SystemExit as exc:
        msg = str(exc)
        if "cover_title" not in msg:
            fails.append(f"③ 报错没有指名出路 cover_title：{msg}")
        if "不折行" not in msg:
            fails.append(f"③ 报错没有说明「不折行」这条裁决：{msg}")
        if "缩小字号" not in msg:
            fails.append(f"③ 报错没有说明「装不下先缩小字号」（2026-10-09 裁决）：{msg}")
    # 那条报错必须是**唯一**的拒绝形状：现目录里没有任何一期走到它
    # （13 期落在 96 以下、0 期落在 60 以下），所以本期目录不需要任何 cover_title。
    got_title = ns["resolve_cover_title"](
        {"display_title": synth, "cover_title": "意外死亡"})
    if got_title != "意外死亡":
        fails.append(f"④ resolve_cover_title 没优先取 cover_title：{got_title}")
    if ns["resolve_cover_title"]({"display_title": this_ep}) != this_ep:
        fails.append("④ 没有 cover_title 时 resolve_cover_title 应退回 display_title")

    # ──  ④ 全库账：单行 100%、老阶梯确实会折行、留痕一 period 不落 ─────────────
    n_single = n_wrapped = n_changed = n_cover_title = 0
    n_reds = 0
    below_legible = []            # 正式书名 < 清晰下限（10-09 裁决要的代价）
    below_legible_traced = 0      # 其中带那条留痕 note 的
    needs_short = []              # 正式书名连绝对下限都装不进（唯一该写 cover_title 的档）
    renamed = set()               # 写了 cover_title 的那些期的正式书名
    for _name, title, has_brief, display_title in checker.episodes(ns):
        reds, _warns, shape = checker.check_title(ns, title,
                                                  display_title if has_brief else None)
        n_reds += len(reds)
        if shape == "single":
            n_single += 1
            if len(checker.old_ladder(ns, f"《{title}》")) > 1:
                n_changed += 1
        else:
            n_wrapped += 1
        try:
            dt_lines, dt_size, _f, dt_notes = run_ladder(display_title)
        except SystemExit:
            needs_short.append(display_title)
            dt_lines, dt_size, dt_notes = None, None, []
        if has_brief and dt_size is not None and dt_size < legible_min:
            below_legible.append(display_title)
            if any("清晰下限" in n for n in dt_notes):
                below_legible_traced += 1
        if title != display_title:
            n_cover_title += 1
            renamed.add(display_title)

    if n_wrapped:
        fails.append(f"④ 全库有 {n_wrapped} 期书名不是单行：一律单行被绕过了")
    if n_single == 0:
        fails.append("④ 全库 0 期单行：单行那一支被删了，①②都在测空夹具")
    if n_changed == 0:
        fails.append("④ 老阶梯和新阶梯在全库上结果完全相同：一律单行这条裁决没有落地")
    if n_reds:
        fails.append(f"④ 全库判红 {n_reds} 条：check-cover-wrapping.py 的红判据在真实目录上不干净")
    if below_legible_traced != len(below_legible):
        fails.append(f"④ {len(below_legible)} 期低于清晰下限，只有 {below_legible_traced} 期留痕："
                     "代价被部分抹安静了")
    if not below_legible:
        fails.append(f"④ 全库没有任何一期低于清晰下限 {legible_min}px：留痕判据没有真夹具，"
                     "这条警告会因为「从没响过」而被下一个人当成噪音删掉")
    unbacked = [dt for dt in needs_short if dt not in renamed]
    if unbacked:
        fails.append(f"④ {len(unbacked)} 期正式书名连绝对下限 {abs_min}px 都装不进"
                     f"却没写 cover_title：{unbacked[:5]}")
    if n_cover_title and len(needs_short) != n_cover_title:
        fails.append(f"④ 写了 cover_title 的 {n_cover_title} 期 ≠ 该写的 {len(needs_short)} 期："
                     "短名用在了不需要的期上（10-09 裁决：那些期该缩字号，不该改名），或者漏了")

    # ──  ⑤ 静态守卫：判据只有一个真源，两条线的字样各在其位 ───────────────────
    cover_src = open(os.path.join(ROOT, "scripts", "make_episode_cover.py"),
                     encoding="utf-8").read()
    sweep_src = open(os.path.join(ROOT, "scripts", "check-cover-wrapping.py"),
                     encoding="utf-8").read()
    for token, where, src in [
            ("TITLE_LEGIBLE_MIN", "make_episode_cover.py", cover_src),
            ("TITLE_ABSOLUTE_MIN", "make_episode_cover.py", cover_src),
            ("def wrap_title_lines(", "make_episode_cover.py", cover_src),
            ("def resolve_cover_title(", "make_episode_cover.py（书名唯一解析处）", cover_src),
            ("_fit_size(text, TITLE_MAX, TITLE_ABSOLUTE_MIN)",
             "make_episode_cover.py（单行那一支：找到绝对下限为止）", cover_src),
            ("书名低于清晰下限", "make_episode_cover.py（留痕 note 的措辞）", cover_src),
            ("def old_ladder(", "check-cover-wrapping.py（老阶梯回放）", sweep_src),
            ("书名折了行", "check-cover-wrapping.py 第 4 条", sweep_src),
            ("低于清晰下限却没有留痕警告", "check-cover-wrapping.py 第 5 条", sweep_src),
            ("阶梯交回的下限", "check-cover-wrapping.py 第 5 条（下限返回值）", sweep_src),
            ("字号掉到绝对下限以下", "check-cover-wrapping.py 第 5 条（层级倒了）", sweep_src),
            ("改名不是出路", "check-cover-wrapping.py 第 6 条（cover_title 用错档）", sweep_src),
            ('ns["resolve_cover_title"](brief)',
             "check-cover-wrapping.py 读同一个解析处", sweep_src)]:
        if token not in src:
            fails.append(f"⑤ {where} 不再包含 {token}：回归就在测一份会漂移的复制品")
    # 退役形状不许回来：折行那一支、2026-10-07 那版的下限常量。
    head = cover_src.split("# ---------------------------------------------------------------- 1. 底图")[0]
    if "ONE_LINE_MIN" in head:
        fails.append("⑤ make_episode_cover.py 里又出现了 ONE_LINE_MIN：那是 2026-10-07 "
                     "「装不下就折行」那一版的常量，2026-10-08 裁决已把它退役")
    if "_wrap_title(text" in head.split("def wrap_title_lines(", 1)[1].split("\ndef ", 1)[0]:
        fails.append("⑤ wrap_title_lines() 内部又去调折行阶梯了：一律单行的第②支回魂")
    tail = cover_src.split("1. 底图", 1)[1]
    tail_code = [ln for ln in tail.splitlines() if not ln.lstrip().startswith("#")]
    if any("_wrap_title(" in ln for ln in tail_code):
        fails.append("⑤ 画图段直接调了 _wrap_title()：绕开 wrap_title_lines()，"
                     "一律单行在出图口失效，而回归还在测阶梯本体")
    if sum("wrap_title_lines(" in ln for ln in tail_code) != 1:
        fails.append(f"⑤ 画图段有 {sum('wrap_title_lines(' in ln for ln in tail_code)} 行"
                     "调用 wrap_title_lines()，应为 1 行：多出来的那份就是第二个真源")
    if sum("resolve_cover_title(" in ln for ln in head.splitlines()
           if not ln.lstrip().startswith("#") and "def resolve_cover_title" not in ln) != 1:
        fails.append("⑤ 书名解析处不止一处：cover_title 的读取必须只走 resolve_cover_title()")
    # 出图口的两条线必须各自在位：拒绝只留给绝对下限，清晰下限走警告。
    guard_block = tail.split("if title_size <")[1] if "if title_size <" in tail else ""
    if not guard_block.startswith(" TITLE_ABSOLUTE_MIN"):
        fails.append("⑤ 画图段第一个 title_size 判据不是绝对下限：出图口的拒绝线被挪走了，"
                     "低于 96px 又会变成报错（2026-10-09 裁决要的正是「照出 + 留痕」）")

    print(f"TITLE_LEGIBLE_MIN={legible_min}px（留痕线）  TITLE_ABSOLUTE_MIN={abs_min}px（拒绝线）  "
          f"SAFE_W={safe_w}px")
    print(f"本期《{this_ep}》：单行 {size}px {lines}  老阶梯 {old}")
    print(f"真超长书名《{below_ep}》：单行 {below_size}px + 留痕警告 ✓   "
          f"合成 16 字书名：报错指名 cover_title ✓")
    print(f"全库：单行 {n_single} / 非单行 {n_wrapped}，其中 {n_changed} 期由 10-08 裁决改变；"
          f"低于清晰下限 {len(below_legible)} 期（全部留痕 {below_legible_traced}）；"
          f"装不进绝对下限 {len(needs_short)} 期；cover_title {n_cover_title} 期；判红 {n_reds}")
    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print("✅ 封面书名一律单行 + 缩小字号双向通过：本期单行不劈名 / 老阶梯·下限·留痕三种回退"
          "形状各自能翻红（非空转）/ 连绝对下限都装不进才报错指名 cover_title / "
          "全库单行 100% 且低于清晰下限的期全部留痕 / 判据只有一个真源")


if __name__ == "__main__":
    main()
