#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕显示文本清洗：script-body.csv 里的标点只是「切条/折行」信号，不是上屏内容。

规则（唯一真源 scripts/lib/subtitle-punctuation.json，与 subtitle-display.mjs 共用）：
  · `script-body.csv` 保留 ，。、；：。！？… 等标点，因为渲染器靠它们把一行切成多条字幕、
    并在标点处平衡折行；提前去掉会让 24 字长条在中间劈开词语。
  · 真正画到画面上的每一行字幕必须没有任何标点符号，也**没有任何空白间隙**。
  · 落在行中间的断点号一律换成换行（2026-09-27 起）：旧口径换成一个全角空格 U+3000，
    屏上会出现「漂亮　聪明　有钱」这种空洞，像漏字，而且是否折行交给容器宽度决定，
    同一句文案在两条渲染路径上会得到两种排版。落在行首/行尾的断点号随 strip 消失。
  · 人名间隔号「·」保留（威廉·戈尔丁）：它是名字的一部分，不是句读。

渲染器应在**最后一步**（`caption_lines()` 折行之后）调用 `display_lines()`，拿到的是
**上屏视觉行的列表**（一行一项），并在写 `caption-track.json` 时同样写入清洗后的文本，
这样 `scripts/check-caption-punctuation.py` 才能拿字幕轨当验收真源。

`strip_display()` 仍然可用，但它的返回值可能含 `\n`：只会清洗、不会拆条。需要行数预算
（一条字幕最多 `MAX_LINES` 行）的调用方请用 `display_lines()` + `MAX_LINES`。
"""
import json
import os
import re

_POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "subtitle-punctuation.json")
with open(_POLICY_PATH, encoding="utf-8") as _fh:
    POLICY = json.load(_fh)

# 旧口径（句中断点号 → 全角空格）已于 2026-09-27 废止。谁把这两个键加回来，谁就是在
# 静默恢复空格间隙，所以导入即失败，而不是等到成片上看见空洞。
for _retired in ("displayPauseChar", "displayPause"):
    if _retired in POLICY:
        raise SystemExit(f"subtitle-punctuation.json 仍带着已废止的 {_retired}：断点号只能换行，"
                         f"不能换成空白字符（见 displayBreak / displayBreakTo）")
if POLICY.get("displayBreakRender") != "line-break":
    raise SystemExit("subtitle-punctuation.json: displayBreakRender 只允许 \"line-break\"，"
                     "屏上出现空白间隙的口径已经作废")

FORBIDDEN_SET = set(POLICY["displayStrip"])
KEEP_SET = set(POLICY["displayKeep"])
BREAK_CHARS = list(POLICY["displayBreak"])
BREAK_SET = set(BREAK_CHARS)
BREAK_TO = POLICY["displayBreakTo"]
MAX_LINES = int(POLICY.get("displayMaxLines", 2))
assert not (FORBIDDEN_SET & KEEP_SET), "displayStrip 与 displayKeep 有冲突字符"
assert not (BREAK_SET & KEEP_SET), "displayBreak 与 displayKeep 有冲突字符"
assert BREAK_SET <= FORBIDDEN_SET, \
    f"displayBreak 必须是 displayStrip 的子集，否则上屏会留下真标点：{sorted(BREAK_SET - FORBIDDEN_SET)}"
assert BREAK_TO == "\n", "displayBreakTo 只能是换行：空白间隙不是节奏，是缺陷"

_WS_RUN = re.compile(r"\s+")
_BREAK_RUN = re.compile(r"\n{2,}")
_WS_AROUND_BREAK = re.compile(r"[ \t\u3000]*\n[ \t\u3000]*")


def strip_display(text):
    """把一行 CSV 文本转成上屏文本：断点号换行、其余标点删除、· 保留。

    返回值可能含 `\n`（多视觉行）。只想拿到画面上那一行的调用方用 `display_lines()`。
    """
    out = []
    for ch in str(text):
        if ch in BREAK_SET:                      # 断点号同时在 displayStrip 里，必须先判
            out.append(BREAK_TO)
        elif ch in FORBIDDEN_SET:
            continue
        else:
            out.append(ch)
    s = "".join(out)
    s = _WS_AROUND_BREAK.sub("\n", s)            # 换行两侧不残留空白
    s = _BREAK_RUN.sub("\n", s)                  # 「，，」「……」只断一次
    return s.strip().strip("\n").strip()         # 首尾断点号与空白一并消失


def display_lines(text):
    """一条字幕真正上屏的视觉行列表（已换行、已去标点、行内零空白）。

    行内残留的空白（半角空格、全角空格 U+3000、制表符）一律吃掉：空格间隙不是节奏，
    节奏由换行承担。两条渲染路径的口径必须逐字一致，`scripts/tests/test-subtitle-punctuation.mjs`
    拿这份实现和 `subtitle-display.mjs` 对拍。
    """
    return [_WS_RUN.sub("", ln) for ln in strip_display(text).split("\n") if _WS_RUN.sub("", ln)]


def caption_style():
    """字幕轨要落的样式标记：门禁据此区分「按新规则渲的」与「历史轨」。"""
    return f"line-break@{POLICY.get('displayBreakVersion', '0')}"


def find_punct(text):
    """返回文本中出现的违规字符（按出现顺序去重），供校验门禁报错。"""
    seen = []
    for ch in str(text):
        if ch in FORBIDDEN_SET and ch not in seen:
            seen.append(ch)
    return seen


def find_gap(text):
    """返回一行上屏文本里的空白间隙字符（换行是行分隔，不算间隙）。"""
    return [ch for ch in str(text) if ch != "\n" and _WS_RUN.match(ch)]
