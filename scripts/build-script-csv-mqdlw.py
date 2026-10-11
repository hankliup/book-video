#!/usr/bin/env python3
"""把 episodes/<书名>/文案.md 的编号句式草稿切成 script-body.csv 的朗读单元。

规则（对齐 docs/book-video-playbook.md 与 AGENTS.md）：
- 每行一个完整朗读单元，按中文标点切句后贪心打包，行长度不超过 MAX_ROW。
- 标点紧跟前文，绝不插入空格（历史上空格会伪装成 phrase 排版并劈开词组）。
- 拼接后逐字必须等于草稿正文，保证字符数与时间轴一致。
"""
import csv
import re
import sys
from pathlib import Path

EPISODE = sys.argv[1] if len(sys.argv) > 1 else "麦琪的礼物"
MAX_ROW = 20
ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "episodes" / EPISODE / "文案.md"
out = ROOT / "episodes" / EPISODE / "script-body.csv"

SPLIT = re.compile(r"(?<=[，。？！；：、])|(?<=——)")

rows: list[str] = ["麦琪的礼物"]
for line in src.read_text(encoding="utf-8").splitlines():
    text = re.sub(r"^\s*\d+\.\s*", "", line).strip()
    if not text:
        continue
    # 先把长句切成标点单元，再贪心打包到 MAX_ROW
    for sentence in [text]:
        units = [u for u in SPLIT.split(sentence) if u]
        buf = ""
        for unit in units:
            if buf and len(buf) + len(unit) > MAX_ROW:
                rows.append(buf)
                buf = unit
            else:
                buf += unit
        if buf:
            rows.append(buf)

body = "".join(rows[1:])
draft = "".join(
    re.sub(r"^\s*\d+\.\s*", "", line).strip()
    for line in src.read_text(encoding="utf-8").splitlines()
    if line.strip()
)
assert body == draft, "切分后正文与草稿逐字不一致"
assert not any(re.search(r"\s", r) for r in rows), "行内出现空白字符"

with out.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(["version", "order", "text"])
    for i, text in enumerate(rows, start=1):
        writer.writerow(["A", i, text])

chars = sum(len(t) for t in rows[1:])
hanzi = len(re.findall(r"[\u4e00-\u9fff]", body))
long_rows = [(i, t) for i, t in enumerate(rows[1:], start=2) if len(t) > MAX_ROW]
clauses = [c for c in re.split(r"[，。？！；：、]|——", body) if c]
fat = [(i + 1, c) for i, c in enumerate(clauses) if len(c) > 14]
est = hanzi / 4.3
print(f"rows={len(rows)}（含书名锚点行）body_chars={chars} hanzi={hanzi} 预估正文≈{est:.0f}s")
print(f"row_over_{MAX_ROW}={long_rows}")
print(f"clause>14字={fat}")
print(f"written -> {out}")
