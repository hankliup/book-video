#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版次打分的回归测试：微信读书搜索里，**书名档必须压过作者档和名次**，外文字样只能降级不能否决。

为什么钉这一条（2026-10-10《小王子》）：
`pick()` 以前把三个维度**相加**成一个分数——书名（100/70/40）+ 作者（30/20）+ 名次（8-i）。
《小王子》的现抓候选里，「小王子（英文版）」(startswith 70 + 作者全等 30 + 名次 7 = **107**)
压过了中文版「小王子」(完全等值 100 + 作者译名用字不同 0 + 名次 6 = **106**)，只差 1 分，
于是简体书评视频上屏那 1.4 秒的书衣是《The Little Prince》的英文封面。
病根不是权重调得不准，是**一个维度可以买通另一个维度**：调权重要求每次重算边界，
而逐档比较把「书名优先」写成了结构。译名用字（絮/苏）本来就不可比,它不该值 30 分。
同一套加法还有个更糟的方向：分数只靠作者堆起来时，它会返回一个**书名完全不相关**的书。

七组断言都带「改动前那个形状确实会做错」的对照（`old_additive()` 在同一份夹具上重放）：
  ① 真·现抓 8 候选：新选中文版「小王子」，旧选「英文版」，且旧分差正好 1 —— 对照不是空转。
  ② 删掉完全等值那条：两者同为 startswith 档，新版次降级把「名家名人译」抬到英文版前面。
  ③ 只剩外文版次：**仍然出图**并打 ⚠️ —— 降级不是否决，「本书长什么样」优先于「哪版最好看」。
  ④ startswith+作者全等 vs 完全等值+作者无关：新选后者，旧选前者 —— 买通被结构挡住。
  ⑤ 候选全是同作者、不同书：新返回 None 退回 --list/--book-id，旧返回一本错书。
  ⑥ 标记表**跑一遍**：现抓 8 条里恰好 2 条判真（英文版 / 英文原版）、6 条判假——
     「英汉对照」这类境内双语读本不算外文版次（《化身博士》交付的就是这种，目检合格）。
  ⑦ 档位取值钉死（书名 3/2/1/0、作者 2/1/0），防止有人把两档并成一档、把名次提进跨档
     比较，或让**空**作者字段靠 `"" in x` 拿到 2 档。
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "scripts", "fetch-book-cover.py")

spec = importlib.util.spec_from_file_location("fbc_under_test", SCRIPT)
fbc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fbc)

QUERY_TITLE = "小王子"
QUERY_AUTHOR = "圣埃克絮佩里"          # episodes/小王子/brief.json 的 author，逐字

# 2026-10-10 用 scripts/fetch-book-cover.py 的 search() 对 keyword=小王子 现抓的 8 条，
# 字段与顺序都按接口原样（bookId 是判红依据，不许凭记忆改）。
REAL = json.loads(r"""
[
 {"title": "小王子（名家名人译）", "author": "[法] 圣埃克苏佩里", "bookId": "3300180150"},
 {"title": "小王子（英文版）", "author": "圣埃克絮佩里", "bookId": "22720170"},
 {"title": "小王子", "author": "[法]安托万·德·圣埃克苏佩里著", "bookId": "216212"},
 {"title": "小王子：新译与注释（中法英三语、作者手绘插图版）", "author": "[法]圣埃克苏佩里", "bookId": "3300201056"},
 {"title": "作家榜名著：小王子", "author": "圣埃克絮佩里", "bookId": "25169066"},
 {"title": "小王子（中英文对照版）", "author": "[法]安东尼·德·圣埃克苏佩里", "bookId": "43318721"},
 {"title": "小王子（旅法译者翻译）", "author": "[法]圣埃克苏佩里", "bookId": "3300212806"},
 {"title": "小王子（英文原版）", "author": "[法]安东尼·德·圣埃克苏佩里", "bookId": "41077445"}
]
""")

NO_EXACT = [b for b in REAL if b["bookId"] != "216212"]

# 只剩外文版次：真实候选里的第 2、8 条，其它中文版次全部拿掉
FOREIGN_ONLY = [b for b in REAL if b["bookId"] in ("22720170", "41077445")]

# 作者全等的 startswith vs 作者无关的完全等值：只有书名档能决定它
BUY_OFF = [
    {"title": "小王子（注释本）", "author": "圣埃克絮佩里", "bookId": "AAA"},
    {"title": "小王子", "author": "张三", "bookId": "BBB"},
]

# 同作者、不同书：书名档全 0，旧写法靠作者分返回一本错书
UNRELATED = [
    {"title": "人类的大地", "author": "圣埃克絮佩里", "bookId": "CCC"},
    {"title": "圣埃克絮佩里传", "author": "圣埃克絮佩里", "bookId": "DDD"},
]

WARN = "只能命中带外文字样标记的版次"


def old_additive(books, title, author):
    """改动前的写法：三个维度**相加**成一个分数，且不过滤 0 分。只为复现缺陷。"""
    nt, na = fbc.norm(title), fbc.norm(author)
    scored = []
    for i, b in enumerate(books):
        bt, ba = fbc.norm(b.get("title")), fbc.norm(b.get("author"))
        s = 100 if bt == nt else 70 if bt.startswith(nt) else 40 if nt in bt else 0
        if na and (na in ba or ba in na):
            s += 30
        else:
            for part in re.split(r"\s+|·", author or ""):
                if len(fbc.norm(part)) >= 2 and fbc.norm(part) in ba:
                    s += 20
                    break
        s += max(0, 8 - i)
        scored.append((s, i, b))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return (scored[0][2], scored[0][0]) if scored else (None, None)


def old_score(books, title, author, bookid):
    """旧分数取指定 bookId 那一条，用来证明「差 1 分」这个边界不是随口写的。"""
    nt, na = fbc.norm(title), fbc.norm(author)
    for i, b in enumerate(books):
        if b["bookId"] != bookid:
            continue
        bt, ba = fbc.norm(b.get("title")), fbc.norm(b.get("author"))
        s = 100 if bt == nt else 70 if bt.startswith(nt) else 40 if nt in bt else 0
        if na and (na in ba or ba in na):
            s += 30
        else:
            for part in re.split(r"\s+|·", author or ""):
                if len(fbc.norm(part)) >= 2 and fbc.norm(part) in ba:
                    s += 20
                    break
        return s + max(0, 8 - i)
    raise AssertionError(f"夹具里没有 {bookid}")


def new_pick(books):
    """跑真实 pick()，顺带把它的 ⚠️ 收回来（日志不该混进测试输出，也便于断言降级不是否决）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = fbc.pick(books, QUERY_TITLE, QUERY_AUTHOR)
    return got, buf.getvalue()


def main():
    fails = []

    # ① 现抓 8 候选：中文版次赢，旧写法确实选错
    got, out = new_pick(REAL)
    old, old_total = old_additive(REAL, QUERY_TITLE, QUERY_AUTHOR)
    if not got or got["bookId"] != "216212":
        fails.append(f"① 新写法没有命中完全等值的中文版次「小王子」(216212)，拿到 "
                     f"{got and (got['title'], got['bookId'])}")
    if old["bookId"] != "22720170":
        fails.append(f"对照失效：旧写法在真夹具上拿到的不是「小王子（英文版）」，而是 "
                     f"{old['title']} —— 这条断言没在复现缺陷")
    s_en = old_score(REAL, QUERY_TITLE, QUERY_AUTHOR, "22720170")
    s_cn = old_score(REAL, QUERY_TITLE, QUERY_AUTHOR, "216212")
    if not (s_en == 107 and s_cn == 106):
        fails.append(f"对照失效：旧分数不是「英文版 107 / 中文版 106」而是 {s_en} / {s_cn}，"
                     f"「差 1 分买通书名档」这个缺陷已被夹具改动抹平")
    if WARN in out:
        fails.append("① 同批有中文版次可用却打了 ⚠️：降级被写成了否决")

    # ② 没有完全等值候选时，靠「无外文字样标记」这一档决胜（两者同为 startswith 档）
    got2, out2 = new_pick(NO_EXACT)
    old2, _ = old_additive(NO_EXACT, QUERY_TITLE, QUERY_AUTHOR)
    if not got2 or got2["bookId"] != "3300180150":
        fails.append(f"② 删掉完全等值后，新写法应选中文版「小王子（名家名人译）」，拿到 "
                     f"{got2 and got2['title']}（作者档又越过版次档了？）")
    if old2["bookId"] != "22720170":
        fails.append(f"对照失效：旧写法在该子集上没有选英文版，拿到 {old2['title']}")
    if fbc.title_tier(fbc.norm("小王子（英文版）"), fbc.norm(QUERY_TITLE)) != \
       fbc.title_tier(fbc.norm("小王子（名家名人译）"), fbc.norm(QUERY_TITLE)):
        fails.append("② 前提破了：英文版与名家名人译本的书名档不再相等，本组断言失去意义")
    if WARN in out2:
        fails.append("② 拿到的是中文版次却打了 ⚠️")

    # ③ 只剩外文版次：仍出图 + ⚠️（降级不是否决）
    got3, out3 = new_pick(FOREIGN_ONLY)
    if not got3:
        fails.append("③ 只剩外文版次时新写法返回了 None —— 那就变成否决，"
                     "观众会看到「没有封面」，而『本书长什么样』优先于『哪个版次最好看』")
    elif not fbc.foreign_edition(got3.get("title")):
        fails.append(f"③ 该子集只剩外文版次，新写法却返回了 {got3['title']}：夹具或标记表坏了")
    if WARN not in out3:
        fails.append("③ 命中外文字样版次却没有把 ⚠️ 打在日志里：人工复核的入口消失了")

    # ④ 作者档买不通书名档
    got4, _ = new_pick(BUY_OFF)
    old4, _ = old_additive(BUY_OFF, QUERY_TITLE, QUERY_AUTHOR)
    if not got4 or got4["bookId"] != "BBB":
        fails.append(f"④ 新写法让作者档越过书名档买到了「小王子（注释本）」：拿到 "
                     f"{got4 and got4['title']}")
    if old4["bookId"] != "AAA":
        fails.append(f"对照失效：旧写法在 ④ 上没有选 startswith+作者全等那条，拿到 {old4['title']}")

    # ⑤ 书名档全 0：宁可报「没有命中」，也不返回一本错书
    got5, _ = new_pick(UNRELATED)
    old5, _ = old_additive(UNRELATED, QUERY_TITLE, QUERY_AUTHOR)
    if got5 is not None:
        fails.append(f"⑤ 候选全是同作者的不同书，新写法仍返回 {got5['title']} —— "
                     f"上屏一张别的书的封面是门禁量不出来的错")
    if not old5 or old5["bookId"] not in ("CCC", "DDD"):
        fails.append(f"对照失效：旧写法在 ⑤ 上没有返回错书，拿到 {old5 and old5['title']}")

    # ⑥ 标记表要**跑**在真夹具上，不能只写在注释里。
    # 判真：只有「正文是英文」的版次。判假：境内出版物带的译名/译注/多语说明——
    # 《化身博士》交付的就是「化身博士：英汉对照」，目检过、封面主标题是中文，
    # 把它算成外文版次会让下一次批量重取把一张合格书衣换成没看过的。
    is_foreign = {b["bookId"]: fbc.foreign_edition(b["title"]) for b in REAL}
    must_true = {"22720170", "41077445"}
    for bid in must_true:
        if not is_foreign[bid]:
            fails.append(f"⑥ 「{next(b['title'] for b in REAL if b['bookId']==bid)}」"
                         f"没被判成外文字样版次：标记表漏了一类英文版次")
    must_false = {"3300180150", "216212", "3300201056", "25169066", "43318721", "3300212806"}
    for bid in must_false:
        if is_foreign[bid]:
            fails.append(f"⑥ 「{next(b['title'] for b in REAL if b['bookId']==bid)}」"
                         f"被误判成外文版次：中文出版物带译注/多语说明不该进这条降级")
    if "对照" in fbc.FOREIGN_EDITION_MARKERS or any("对照" in m for m in fbc.FOREIGN_EDITION_MARKERS):
        fails.append("⑥ 「对照」类的词回到了标记表：境内双语读本会被当成英文版次重取")

    # ⑦ 档位取值钉死
    nt = fbc.norm(QUERY_TITLE)
    tiers = {"小王子": 3, "小王子（英文版）": 2, "作家榜名著：小王子": 1, "人类的大地": 0}
    for t, want in tiers.items():
        got_t = fbc.title_tier(fbc.norm(t), nt)
        if got_t != want:
            fails.append(f"⑦ title_tier({t}) = {got_t}，应为 {want}（档位并档＝书名优先的结构失效）")
    if fbc.title_tier("", nt) != 0 or fbc.title_tier("小王子", "") != 2:
        fails.append("⑦ 空串的兜底档变了：nt 为空要放行(2)，bt 为空要判不相关(0)")
    na = fbc.norm(QUERY_AUTHOR)
    # "": 空作者必须是 0 档 —— `"" in "圣埃克絮佩里"` 为真，少一道守卫一条没填作者的书目
    #     就能压过真写对了的作者。
    auth = {"圣埃克絮佩里": 2, "圣埃克絮佩里 著": 2, "[法] 圣埃克苏佩里": 0, "": 0}
    for a, want in auth.items():
        got_a = fbc.author_tier(fbc.norm(a), na, QUERY_AUTHOR)
        if got_a != want:
            fails.append(f"⑦ author_tier({a}) = {got_a}，应为 {want}")
    if fbc.author_tier(fbc.norm("[挪]格德·布兰腾伯格"), "格德布兰腾伯格", "[挪]格德·布兰腾伯格") != 2:
        fails.append("⑦ 带国籍前缀的作者互相包含这一档没了（伊加利亚的女儿们会掉档）")

    print(f"旧分数：英文版 {s_en} / 中文版 {s_cn}（差 {s_en - s_cn} 分）  "
          f"新命中 {got['title']}({got['bookId']})  "
          f"② {got2['title']} ③ {got3['title'] if got3 else 'None'} "
          f"④ {got4['title']} ⑤ {got5}")
    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print("✅ 版次打分逐档比较：书名档压过作者档与名次 / 外文字样只降级不否决 / "
          "书名档全 0 时退回 --list 而不是返回错书，且旧加法在真夹具上的 107 对 106 已复现")


if __name__ == "__main__":
    main()
