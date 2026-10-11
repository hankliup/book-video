#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口（已废弃）：真正的实现是同目录的 `caption_display.py`。

历史背景：这里曾经独立实现过一版「直接删除所有 displayStrip 字符」的清洗，
它会把句中断点号一起删掉，导致「漂亮，聪明，有钱」在屏上粘成「漂亮聪明有钱」。
第二版口径（把断点号换成全角空格 U+3000）在 2026-09-27 被用户否掉——屏上的空格间隙
读起来像漏字，而且是否折行交给容器宽度，同一句文案在两条渲染路径上会得到两种排版。
现行口径由 `subtitle-punctuation.json` 的 `displayBreak` / `displayBreakTo` 决定：
断点号一律换成换行，屏上零标点、零空白。两套实现留在仓库里迟早有人接错，
所以本文件不再自带规则，只做 `caption_display` 的再导出。

新代码请直接：
    from caption_display import display_lines       # 上屏视觉行列表（推荐）
    from caption_display import strip_display       # 上屏文本（可能含 \n）
    from caption_display import FORBIDDEN_SET       # 残留标点判定
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from caption_display import (  # noqa: E402
    BREAK_CHARS,
    BREAK_SET,
    BREAK_TO,
    FORBIDDEN_SET,
    KEEP_SET,
    MAX_LINES,
    POLICY,
    caption_style,
    display_lines,
    find_gap,
    find_punct,
    strip_display,
)

# 旧调用名
display_text = strip_display
PAUSE_CHAR = BREAK_TO        # 已废止的旧键名，指向同一个换行，别再拿它当空白用
residual_punct = lambda s: sorted({ch for ch in str(s) if ch in FORBIDDEN_SET})  # noqa: E731

__all__ = ["POLICY", "FORBIDDEN_SET", "KEEP_SET", "BREAK_CHARS", "BREAK_SET", "BREAK_TO",
           "MAX_LINES", "PAUSE_CHAR", "strip_display", "display_lines", "caption_style",
           "display_text", "residual_punct", "find_punct", "find_gap"]
