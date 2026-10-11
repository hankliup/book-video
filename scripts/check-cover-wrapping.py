#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面折行回归：把 make_episode_cover.py 的折行判据拿去跑全部剧集，不画图。

为什么单独一个脚本：折行是**排版规则**，不是某一期封面。它只在书名恰好是
《三体1·地球往事》《涅朵奇卡：一个女人的一生》这类带分隔号的长书名时才暴露，
所以「跑一遍本期看看」永远看不见回归——真出问题的那一刻是 113 期一起出错版。
折行阶梯从 make_episode_cover.wrap_title_lines() 取，本脚本不复制第二份判据
（复制了就会漂移，这是本仓库反复踩过的坑）。

判红：
  1. 硬禁则（GB/T 15834）：任何一行不得以点号、间隔号、后半括号领行；
  2. 出框：任何一行在所选字号下不得超过 SAFE_W，或阶梯本身报错（单行装不进绝对下限）；
  3. 标点锚点被放弃：书名里有 ·／：／，，且「折在标点之后」的两半都装得下，
     实际却折到了别处——2026-10-02 的《三体1·地球往事》就是这个形状
     （虚词偏好越过了硬锚点，把「地球往事」劈成「地球 / 往事」）。
  4. 书名折了行（2026-10-08 用户裁决「修改视频封面图中的书名排版，不要有换行，并沉淀这个
     规则」）：一律单行，`len(lines) > 1` 直接判红。这一条带两个**防空转**断言：
     ① 全库必须既有「老阶梯会折行、现在走单行」的期（`n_changed`），为 0 说明第 4 条
        只是在测一个永远不成立的夹具；② 全库必须真的有期走单行（`n_single`），为 0 说明
        单行那一支被删了或下限被调走了。
  5. 字号的两条线（2026-10-09 用户裁决「根据合适排版进行缩小字号」把缩字号合法化了，
     代价是 2026-10-01 否过的「字体模糊不清晰」必须由留痕而不是由报错来记账）：
     ① 单行字号 < TITLE_LEGIBLE_MIN 96px 而阶梯没写那条「书名低于清晰下限」的 note —— 红，
        删警告 = 悄悄撤回裁决；② 单行字号 < TITLE_ABSOLUTE_MIN 60px（＝钩子行的最小字号）
        也是红，书名比钩子还小就是层级倒了。低于 96px **本身不再是红**，它由脚本末尾的
        全库清单数出来（有几期、分别多少 px），嫌糊的出路是本期 brief.json 的 cover_title。
  6. cover_title 用错了地方：正式书名本来能在清晰下限之上单行，却改了封面用名 —— 红。
     2026-10-09 给超长书名的出路是缩小字号，改名不是出路；只有缩到绝对下限仍装不进才允许。

书名取自 `make_episode_cover.resolve_cover_title()`：回归读的和出图画的是同一个解析处，
否则封面画 cover_title、回归跑 display_title，就是在测另一个程序。

劈词（折点不在 jieba 词边界上）只报不判红：《追忆似 / 水年华》是已知、已接受的
残留，jieba 没有「似水」这个词条，任何折法在它眼里都算边界。

用法：
  python3 scripts/check-cover-wrapping.py
  python3 scripts/check-cover-wrapping.py --book 三体1·地球往事
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes")
COVER_SRC = os.path.join(ROOT, "scripts", "make_episode_cover.py")
# 只取 helper 段：从文件头到「1. 底图」之前，画图那段要读图片、会改盘。
HELPERS_CUT = "# ---------------------------------------------------------------- 1. 底图"
# helper 段之前还夹着一段「读本期 brief.json」的逐期 I/O：回归跑的是全部书名，
# 不该被某一期的目录是否存在绑住，所以按标记把整段摘掉。
BRIEF_IO = "\nbrief = json.load("
HELPERS_IO = "_probe = ImageDraw"
MARKS = ("：", ":", "·", "，", "、")


def load_helpers():
    src = open(COVER_SRC, encoding="utf-8").read()
    head = src.split(HELPERS_CUT)[0]
    body = head.split(BRIEF_IO)[0] + "\n" + HELPERS_IO + head.split(HELPERS_IO, 1)[1]
    ns = {"__file__": COVER_SRC, "os": os, "json": json, "sys": sys, "re": re}
    os.environ.setdefault("EPISODE", "__wrap_probe__")
    exec(compile(body, COVER_SRC, "exec"), ns)      # noqa: S102 - 本仓库自己的脚本
    return ns


def episodes(ns):
    """逐期产出 (目录名, 封面书名, 有没有 brief, 正式书名)。

    封面书名走 `ns["resolve_cover_title"]()`，和出图口同一份解析：缩到绝对下限仍装不进
    的那一档靠 `cover_title` 兜底（见 make_episode_cover.py 的注释），回归若只读
    display_title，就是在测一个封面根本不会画的字符串。
    """
    for name in sorted(os.listdir(EP)):
        d = os.path.join(EP, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        bp = os.path.join(d, "brief.json")
        title = None
        if os.path.exists(bp):
            try:
                brief = json.load(open(bp, encoding="utf-8"))
            except json.JSONDecodeError:
                brief = None
            if brief:
                title = (ns["resolve_cover_title"](brief), brief.get("display_title"))
        if title is None:
            title = (name, name)
        yield name, title[0], os.path.exists(bp), title[1]


def cuts_of(lines, lead, tail):
    """把折好的行还原成裸书名上的折点偏移。"""
    out, i = [], 0
    for ln in lines[:-1]:
        s = ln[len(lead):] if ln.startswith(lead) else ln
        i += len(s)
        out.append(i)
    return out


def old_ladder(ns, text):
    """2026-10-08 裁决**之前**那条阶梯的逐字回放：一到 TITLE_MIN 就找折行。

    留着它只为一个用途：证明第 4 条判据不是空断言。新阶梯交出单行、老阶梯交出多行的
    期数就是这条裁决真正改变的东西；那个数为 0，说明「一律单行」根本没在生效
    （下限被调走了，或者那个分支被删了），而所有 grep 型守卫照样全绿。
    """
    lines = ns["_wrap_title"](text, ns["TITLE_MIN"])
    if lines is None:
        lines = ns["_wrap_title"](text, ns["TITLE_MIN"], ban_words=False)
    if lines is None:
        lines = ns["_wrap_title"](text, ns["HARD_MIN_TITLE"])
    if lines is None:
        lines = ns["_wrap_title"](text, ns["HARD_MIN_TITLE"], ban_words=False)
    return lines or [text]


def check_title(ns, title, display_title=None):
    """返回 (reds, warns, 形态)。形态：'single' 单行 / 'wrapped' 折行。"""
    reds, warns = [], []
    text = f"《{title}》"
    body, lead, tail = ns["_strip_brackets"](text)
    try:
        lines, size, floor, notes = ns["wrap_title_lines"](text)
    except SystemExit as exc:
        # 一律单行 + 缩小字号（2026-10-09 用户裁决）之后，「报错」只剩一种形状：
        # 正式书名连绝对下限都装不进。出路是本期 brief.json 的 cover_title，
        # 不是折行，也不是把 TITLE_LEGIBLE_MIN / TITLE_ABSOLUTE_MIN / SAFE_W 往下调。
        return [f"书名既不折行也够不到绝对下限（该写 cover_title）：{exc}"], [], "wrapped"
    for note in notes:
        warns.append(note)
    # 阶梯返回的那条下限必须是绝对下限本身。它一旦被写回 TITLE_LEGIBLE_MIN，
    # 缩字号那一支就等于没打开——超长书名会在 96px 处报错而不是照出，
    # 而「报错」在这套判据里是红的，于是全库 13 期一起变红，看不出根因在哪。
    if floor != ns["TITLE_ABSOLUTE_MIN"]:
        reds.append(f"阶梯交回的下限是 {floor}，不是绝对下限 {ns['TITLE_ABSOLUTE_MIN']}："
                    "wrap_title_lines() 又用清晰下限去量单行了，那等于撤回缩小字号那条裁决")
    # 小字必须留痕：低于清晰下限却不写那条 note，就等于把 2026-10-09 裁决里
    # 「这是用户点名要的代价」那一半抹掉——日志安静了，封面还是糊的。
    if size < ns["TITLE_LEGIBLE_MIN"] and not any("清晰下限" in n for n in notes):
        reds.append(f"低于清晰下限却没有留痕警告：{text} 单行 {size}px < "
                    f"{ns['TITLE_LEGIBLE_MIN']}px，wrap_title_lines() 没写「书名低于清晰下限」"
                    "那条 note——删警告就是偷偷撤回这条裁决")
    if size < ns["TITLE_ABSOLUTE_MIN"]:
        reds.append(f"字号掉到绝对下限以下：{size}px < {ns['TITLE_ABSOLUTE_MIN']}px，"
                    "书名比钩子行还小，层级倒了")
    for idx, ln in enumerate(lines):
        if ln in (lead, tail) or not ln:
            reds.append(f"空行/只剩括号：{lines}")
            break
        if idx and ln[0] in ns["NO_LINE_START"]:
            reds.append(f"行首是禁则标点：{ln}")
        if ns["_w"](ln, size) > ns["SAFE_W"] + 1:
            reds.append(f"出框：{ln} 在 {size}px 宽 {int(ns['_w'](ln, size))} > {ns['SAFE_W']}")
    # 不在这里重复阶梯自己的那条降字号警告：它带着同一个数字、同一个理由，再说一遍
    # 只会把「警告 98 条」变成读不下去的噪音，而真正的记账口是上面那条「留痕」红
    # （低于清晰下限必须有 note）和末尾那份全库清单。
    cuts = cuts_of(lines, lead, tail)
    bounds = ns["_word_boundaries"](body)
    for c in cuts:
        if bounds is not None and c not in bounds:
            warns.append(f"劈词：{body[:c]} | {body[c:]}")
    # 标点锚点：只在两行折法上判——三行时中间段由同一条规则递归折出，窗口另算。
    # 一律单行之后正常走不到这里，它守的是「有人把折行那一支接回来」。
    if len(lines) == 2 and not any(body[c - 1] in MARKS for c in cuts):
        for c in range(1, len(body)):
            if body[c - 1] not in MARKS:
                continue
            a, b = lead + body[:c], body[c:] + tail
            if ns["_w"](a, size) <= ns["SAFE_W"] and ns["_w"](b, size) <= ns["SAFE_W"]:
                reds.append(f"标点断点被放弃：可折成「{a} / {b}」，实际「{lines[0]} / {lines[1]}」")
                break
    # 第 4 条：一律单行（2026-10-08 用户裁决「修改视频封面图中的书名排版，不要有换行」）。
    # 只要阶梯交出多行就是红，不看字数、不看字号——绝对式裁决要配绝对式判据。
    # 2026-10-09 补上另一半：装不下就缩小字号（「根据合适排版进行缩小字号」），
    # 所以「小」不是红，「折」才是红，而「小」必须由上面那条留痕警告记账。
    if len(lines) > 1:
        reds.append(f"书名折了行：{text} 交出 {lines}，按 2026-10-08/10-09 裁决封面书名一律单行"
                    f"（装不下就缩字号，缩到绝对下限还不行才写 cover_title，不折行）")
    # 兜底名的来路要留痕，而且它现在**只**该出现在「缩到绝对下限仍装不进」的那一档：
    # 2026-10-09 用户给超长书名的出路是缩小字号，不是改封面用名。
    if display_title and title != display_title:
        if _fits_absolutely(ns, display_title):
            reds.append(f"改了封面用名，可正式书名缩字号就能单行：《{display_title}》→《{title}》"
                        f"（在绝对下限 {ns['TITLE_ABSOLUTE_MIN']}px 下单行装得进）。"
                        "2026-10-09 裁决「根据合适排版进行缩小字号」：那些期的出路是缩字号，"
                        "改名不是出路")
        else:
            warns.append(f"封面用名 cover_title《{title}》≠ 正式书名《{display_title}》"
                         "（正式书名连绝对下限都装不进单行，这是唯一允许的出路）")
    return reds, warns, ("single" if len(lines) == 1 else "wrapped")


def main():
    args = sys.argv[1:]
    only = args[args.index("--book") + 1] if "--book" in args else None
    ns = load_helpers()
    reds_total = warn_total = checked = 0
    n_single = n_wrapped = n_changed = n_cover_title = 0
    below_legible = []
    for name, title, has_brief, display_title in episodes(ns):
        if only and name != only:
            continue
        checked += 1
        reds, warns, shape = check_title(ns, title, display_title if has_brief else None)
        if shape == "single":
            n_single += 1
            if len(old_ladder(ns, f"《{title}》")) > 1:
                n_changed += 1
        else:
            n_wrapped += 1
        # cover_title 用错档的那条红归 check_title 管（它才拿着阶梯的判决），
        # 这里只数账，别在两个地方各写一份措辞。
        if title != display_title:
            n_cover_title += 1
        dt_size = _ladder_size(ns, display_title)
        if has_brief and dt_size is not None and dt_size < ns["TITLE_LEGIBLE_MIN"]:
            below_legible.append((display_title, dt_size))
        if not has_brief:
            warns.append("无 brief.json，书名按目录名")
        if reds or warns:
            print(f"{'❌' if reds else '⚠️'} 《{title}》")
            for r in reds:
                print(f"   · {r}")
            for w in warns:
                print(f"   · {w}")
        reds_total += len(reds)
        warn_total += len(warns)
    # 两条防空转，方向相反，缺一条第 4 条判据就会悄悄烂掉：
    # ① 老阶梯（一到 TITLE_MIN 就折行）必须在这批书名上真的会折行——否则「一律单行」
    #    只是在测一个永远不成立的夹具，删掉整个分支也不会有任何守卫变红。
    # ② 单行那一支必须真的在生效（n_single>0），否则第 4 条判据同样空转。
    #    「低于清晰下限」不再是红：2026-10-09 用户裁决它就是这条裁决要的代价，
    #    代价由每期那条「书名低于清晰下限」的警告记账（check_title 里「留痕」那条红
    #    保证警告不许被删），这里只把它数出来贴在末尾，让全库规模每次可见。
    if only is None:
        if n_single == 0:
            reds_total += 1
            print("❌ 全库没有一期走单行：wrap_title_lines 的分支没在生效"
                  "（阶梯被改回折行或 TITLE_ABSOLUTE_MIN 被调走），第 4 条判据成了空断言")
        if n_changed == 0:
            reds_total += 1
            print("❌ 一律单行相对老阶梯 0 期生效：这条判据没有抓到任何东西")
        print(f"\n一律单行 + 缩小字号（2026-10-08/10-09 裁决）：单行 {n_single} 期 / "
              f"折行 {n_wrapped} 期，其中 {n_changed} 期是老阶梯会折、现在走单行的；"
              f"cover_title {n_cover_title} 期")
        if below_legible:
            print(f"⚠️ {len(below_legible)} 期正式书名在清晰下限 "
                  f"{ns['TITLE_LEGIBLE_MIN']}px 之下，按 2026-10-09 裁决「根据合适排版进行缩小字号」"
                  "缩字号照出。这是用户点名要的代价，每次回归都要看得见：")
            for dt, s in sorted(below_legible, key=lambda x: x[1]):
                print(f"   · 《{dt}》单行上限 {s}px（{len(dt) + 2} 个字符 ÷ {ns['SAFE_W']}px）")
            print("   想让它变清楚只有两条路：改 display_title 本身（那是全书的账，要用户点头），"
                  "或写 cover_title——而写 cover_title 会撞上「改名不是出路」那条红，"
                  "因为 2026-10-09 已经否过一次了。不许动 TITLE_LEGIBLE_MIN / "
                  "TITLE_ABSOLUTE_MIN / SAFE_W 这三个数来让这份清单变短。")
    print(f"\n跑 {checked} 期书名排版：判红 {reds_total}，警告 {warn_total}")
    sys.exit(1 if reds_total else 0)


def _ladder_size(ns, title):
    """这个书名单行能长到的最大字号（阶梯给的那个 size），拿不到就返回 None。"""
    try:
        _lines, size, _floor, _notes = ns["wrap_title_lines"](f"《{title}》")
    except SystemExit:
        return None
    return size


def _fits_absolutely(ns, title):
    """这个正式书名能不能缩到绝对下限以内、走单行装进安全宽度。

    2026-10-09 之前判的是清晰下限（装不进 96px 就拒绝出图）；现在判的是绝对下限——
    低于清晰下限照出并留痕，只有连绝对下限都装不进才轮到 brief.json 的 cover_title。
    量的是 `_fit` 而不是跑一遍阶梯，因为这里要的是一个布尔判据，不是那个 note。
    """
    return ns["_fit"](f"《{title}》", ns["TITLE_ABSOLUTE_MIN"])


if __name__ == "__main__":
    main()
