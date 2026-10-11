#!/usr/bin/env python3
"""把某一期的字幕正文（script-body.csv）清掉装饰性符号：书名号、引号、括号、破折号。

口径分工（重要，别改反）：
  · 「画面上不显示标点」由**渲染层**负责：`caption_display()` 在折行之后按
    `scripts/lib/subtitle-punctuation.json` 剥字，验收看 `scripts/check-caption-punctuation.py`。
  · 本脚本只管**写稿层**：把 《》“”（）—— 这类装饰符号从 CSV 正文里去掉，
    因为 `validate-script.mjs` 把它们判成错误（正文里写书名号/引号是稿子没洗干净）。
  · 句读 ，。？！、；：… **默认保留**：它们是渲染器 `CLAUSE_RE` 的分句依据、
    `allocate_times` 吸附配音停顿的锚点、逐字 LCS 对 ASR 的字形。真删了会退化出
    「1942年2月巴西一座山里的小城一个」这种 24 字无断点长串（《昨日的世界》实测）。
  · 译名间隔号 · 永不删除（去掉会把「斯蒂芬·茨威格」粘成「斯蒂芬茨威格」）。
  · 常驻书名层的《书名》是模板视觉标识，不走这条规则，也不受本脚本影响。

`--strip-punctuation` 是显式 opt-in 的历史口径（《素食者》式：连句读一起从 CSV 删掉）。
只有确认这一期不靠标点分句时才用；删完必须跑
`EPISODE=<书名> CAPS_ONLY=1 python3 scripts/render_scene_map_template_fallback.py`，
把「行过长 / 劈开保护词 / 断点非词边界」压到 0 条，必要时调 `CAP_MAX_UNIT`。

只删符号，不改字：脚本按「汉字序列」做逐字校验，并可选与 文案.md 对账，
清洗前后汉字必须完全一致，否则直接报错退出。

用法：
  python3 scripts/strip-subtitle-symbols.py "<书名>" [--version A] [--apply]
  python3 scripts/strip-subtitle-symbols.py "<书名>" --strip-punctuation   # 谨慎：连句读一起删
默认 dry-run 只打印 diff；--apply 才写回 script-body.csv（备份 tmp/script-presym-<书名>.bak.csv）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 直接删除：装饰性括号类
DROP = set("《》“”‘’「」『』【】()（）")
# 破折号/连线
DASH_RE = re.compile(r"—{1,}|―{1,}|─{1,}")
# 连用的引号 closing->opening：脱引号要补顿号；--strip-punctuation 下补了也会被删掉，无副作用
ADJ_QUOTE_RE = re.compile(r"[”’」』](?=[“‘「『])")
CJK_RE = re.compile(r"[\u3400-\u4cff\u4e00-\u9fff\uf900-\ufaff]")  # 汉字 + 扩展A + 兼容表意文字

# 句读真源同样是 lib/subtitle-punctuation.json：那里列的上屏剥离字符里，
# 非 ASCII、又不属于装饰符号的那些，就是渲染器当分句标记用的 ，。、；：？！…
# displayBreak（断点号 → 换行）本来就是 displayStrip 的子集，并集仍是 displayStrip；
# 这里显式读它，是为了让「哪些标点在上屏时变成换行」这件事在代码里说得清。
with open(os.path.join(ROOT, "scripts", "lib", "subtitle-punctuation.json"),
          encoding="utf-8") as _fh:
    _POLICY = json.load(_fh)
_ALL_STRIP = set(_POLICY["displayStrip"]) | set(_POLICY.get("displayBreak", []))
SENT_PUNCT = "".join(ch for ch in sorted(_ALL_STRIP)
                     if ord(ch) > 127 and ch not in DROP and ch != "·")
CLAUSE_MARKS = SENT_PUNCT

PAUSE = SENT_PUNCT + "·"


def hanzi(text: str) -> str:
    return "".join(CJK_RE.findall(text))


def clean(text: str, strip_punctuation: bool = False) -> str:
    t = ADJ_QUOTE_RE.sub("、", text)

    def dash(m):
        """保留句读时，破折号只在它确实承担停顿时转成逗号；--strip-punctuation 下直接消失。"""
        if strip_punctuation:
            return ""
        prev = t[m.start() - 1] if m.start() else ""
        nxt = t[m.end():m.end() + 1]
        if prev and nxt and prev not in PAUSE and nxt not in PAUSE:
            return "，"
        return ""

    t = DASH_RE.sub(dash, t)
    t = "".join(ch for ch in t if ch not in DROP)
    if strip_punctuation:
        t = "".join(ch for ch in t if ch not in SENT_PUNCT)
    t = re.sub(r"^[，。、；：？！]+", "", t)
    return t


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        rdr = csv.DictReader(fh)
        headers = rdr.fieldnames or []
        return headers, [dict(r) for r in rdr]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--version", default=None, help="script-body.csv 的 version；默认取 brief.json")
    ap.add_argument("--strip-punctuation", action="store_true",
                    help="谨慎：连句读 ，。？！；、：… 一起从 CSV 删掉（会削弱渲染器分句）；"
                         "默认保留句读，上屏去标点由渲染层负责")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ep = os.path.join(ROOT, "episodes", args.episode)
    script = os.path.join(ep, "script-body.csv")
    if not os.path.exists(script):
        raise SystemExit(f"缺少 script-body.csv: {script}")

    version = args.version
    if not version:
        import json
        with open(os.path.join(ep, "brief.json"), encoding="utf-8") as fh:
            version = json.load(fh).get("scriptVersion")
    if not version:
        raise SystemExit("无法确定 scriptVersion，请用 --version 指定")

    headers, rows = read_rows(script)
    targets = [r for r in rows if (r.get("version") or "").strip() == version]
    if not targets:
        raise SystemExit(f"version {version} 没有匹配到任何行")

    before_all = "".join(hanzi(r["text"]) for r in targets)
    changed, diffs = 0, []
    for r in targets:
        new = clean(r["text"], strip_punctuation=args.strip_punctuation)
        if new != r["text"]:
            changed += 1
            diffs.append((r["order"], r["text"], new))
        r["text"] = new

    after_all = "".join(hanzi(r["text"]) for r in targets)
    if before_all != after_all:
        print("!! 汉字序列被改动，拒绝写入", file=sys.stderr)
        for i, (a, b) in enumerate(zip(before_all, after_all)):
            if a != b:
                print(f"   第 {i} 个字起不一致：{before_all[i:i+20]} / {after_all[i:i+20]}", file=sys.stderr)
                break
        return 1

    # 可选：与 文案.md 逐字对账（只比汉字，忽略 Markdown 标题行）
    essay = os.path.join(ep, "文案.md")
    if os.path.exists(essay):
        raw = re.sub(r"^#.*$", "", open(essay, encoding="utf-8").read(), flags=re.M)
        src = hanzi(raw)
        if src != after_all:
            print("!! 清洗后字幕与 文案.md 汉字序列不一致，请人工确认", file=sys.stderr)
            for i, (a, b) in enumerate(zip(src, after_all)):
                if a != b:
                    print(f"   第 {i} 字：文案.md …{src[max(0,i-12):i+12]}… / 字幕 …{after_all[max(0,i-12):i+12]}…", file=sys.stderr)
                    break
            return 1
        print(f"[guard] 与 文案.md 汉字逐字一致（{len(after_all)} 字）")

    banned = (set(DROP) | set(SENT_PUNCT)) if args.strip_punctuation else set(DROP)
    left = [(r["order"], ch) for r in targets for ch in r["text"] if ch in banned or DASH_RE.match(ch)]
    print(f"[scan] version={version} 行数={len(targets)} 改动行={changed} "
          f"口径={'连句读一起删(CSV)' if args.strip_punctuation else '只去装饰符号(句读留给渲染层剥)'} "
          f"残留符号={len(left)}")
    for order, old, new in diffs:
        print(f"  {order:>3} - {old}")
        print(f"      + {new}")
    if not args.apply:
        print("[dry-run] 未写回；加 --apply 生效")
        return 0

    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
    wr.writeheader()
    wr.writerows(rows)
    # 备份按期命名：同一仓库可能同时在做好几期，固定文件名会互相覆盖
    backup = os.path.join(ROOT, "tmp", f"script-presym-{args.episode}.bak.csv")
    if not os.path.isdir(os.path.dirname(backup)):
        backup = script + ".bak"
    shutil.copy2(script, backup)
    with open(script, "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())
    print(f"[backup] {backup}")
    print(f"[write] {script}")
    print("[next] EPISODE=<书名> CAPS_ONLY=1 python3 scripts/render_scene_map_template_fallback.py 复核切条；"
          "成片后 python3 scripts/check-caption-punctuation.py episodes/<书名>/caption-track.json 验收上屏零标点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
