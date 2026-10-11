#!/usr/bin/env python3
"""把 episodes/<书名>/文案.md 切成 script-body.csv 的「完整朗读单元」行（保留分句标点）。

与 md-to-sentence-csv.py 的分工：
  · md-to-sentence-csv.py 是旧的 phrase/sentence 模式——它自己折行、用空格拼视觉行，
    2026-09-27 的 line-break 口径已经废止了「空格即换行」（屏上出现空洞像漏字）。
  · 本脚本只做「一行 = 一个朗读单元」这一件事，把折行完全交给渲染层：
    渲染器读 scripts/lib/caption_display.py，按 displayBreak 在标点处换行、
    按 652px/56px 折行、超过 displayMaxLines 再拆成衔接的下一条字幕。
  · 因此本脚本绝不插入空格，也绝不删除 ，。？！；、：…（它们是分句锚点、
    停顿吸附点和逐字 LCS 对 ASR 的字形）。

装饰符号（《》“”「」（）——）在本脚本内就按 `strip-subtitle-symbols.py` 的同一套
常量清掉（连用引号脱掉后补顿号），因为脱引号补出来的 、要能当分句锚点用；
交付前仍必须跑 `python3 scripts/strip-subtitle-symbols.py "<书名>" --apply` 复核
（它做逐字校验并兜住手写 CSV），再跑 `node scripts/validate-script.mjs "<书名>"`。
本脚本另外自带一道逐字对账：清洗与打包前后汉字序列必须完全一致，否则报错退出。

规则：
  - 首个非空行若只是《书名》，输出为正文第 1 行的裸书名（常驻书名层与标题卡锚点）
  - 源文件每一行是硬边界，不跨行合并（一行一个语气单位）
  - 行内按分句标点（，、；：。！？…）切原子，DP 打包成尽量贴近 max_row 的行
  - 标点紧跟前文，绝不插空格；单个超长分句自成一行的部分交给渲染层折行

用法：
  python3 scripts/md-to-spoken-rows.py "<书名>" [--max-row 18] [--version A] [--apply]
默认打印预览；--apply 才写 episodes/<书名>/script-body.csv。
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENT_END = "。！？；…"
CLAUSE = "，、；："
TITLE_LINE = re.compile(r"^《(?P<title>[^》]{1,30})》$")

# 装饰符号清洗与 strip-subtitle-symbols.py 共用同一份真源，不各自抄字符表：
# 那一版是交付门禁，本脚本只是提前一步把符号处理掉，让「脱引号补的顿号」
# 在本脚本里就能当分句锚点用（否则「不断告诉另一个人“这就是爱情”“这就是成长”
# “这是你自愿的”」整串没有标点，只能被按字符硬切成「这就是成 / 长」）。
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "strip_subtitle_symbols", ROOT / "scripts" / "strip-subtitle-symbols.py"
)
_sss = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_sss)
DROP, DASH_RE, ADJ_QUOTE_RE = _sss.DROP, _sss.DASH_RE, _sss.ADJ_QUOTE_RE


def drop_symbols(text: str) -> str:
    """写稿层去装饰符号：删 《》“”（）等，破折号换成逗号，连用引号之间补顿号。"""
    text = ADJ_QUOTE_RE.sub("、", text)
    text = DASH_RE.sub("，", text)
    return "".join(ch for ch in text if ch not in DROP)



def split_keep(text: str, pattern: str) -> list[str]:
    """按标点切，标点留在前一段末尾；纯标点段并入前段。"""
    parts = re.split(rf"([{pattern}])", text)
    out: list[str] = []
    buf = ""
    for i in range(0, len(parts), 2):
        body = parts[i]
        tail = parts[i + 1] if i + 1 < len(parts) else ""
        piece = (body + tail).strip()
        if not piece:
            continue
        if tail and not body.strip():          # 只有标点（如「……」被劈成两段）
            if out:
                out[-1] += tail
            continue
        buf = piece
        out.append(buf)
    return out


def units(line: str, max_row: int) -> list[str]:
    """一行文案 → 若干朗读单元，长度尽量贴近但不超过 max_row。

    原子单位是「带标点的分句」（，、；：。！？… 之后断开）。行内用 DP 做一次
    最优打包：代价 = 离 max_row 的差值，超长惩罚加倍，所以既不会出现
    「所以，」这种 3 字碎行，也不会为了凑满把两句话塞进一屏。
    单个分句本身就超长时（没有可断的标点）它自己成一行，交给渲染层折视觉行——
    这里绝不按字符硬切，硬切会劈开词（实测「这就是成/长」「权利/。」）。
    """
    atoms = split_keep(line, SENT_END + CLAUSE)
    prefix = [0]
    for a in atoms:
        prefix.append(prefix[-1] + len(a))

    def cost(total: int) -> float:
        if total <= max_row:
            return float(max_row - total)
        return float((total - max_row) * 20)   # 宁可碎行，也别撑出 3 个视觉行的字幕

    n = len(atoms)
    best = [float("inf")] * (n + 1)
    nxt = [-1] * (n + 1)
    best[n] = 0.0
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n + 1):
            total = prefix[j] - prefix[i]
            if total > max_row * 2:
                break
            c = cost(total) + best[j] + 0.5      # 0.5/行：同等代价下偏好更少的行
            if c < best[i]:
                best[i] = c
                nxt[i] = j
    rows: list[str] = []
    i = 0
    while i < n:
        j = nxt[i] if nxt[i] > i else i + 1
        rows.append("".join(atoms[i:j]))
        i = j

    # 短到难以独立对齐的碎片（「所以，」）并进邻行：只有当邻居本来就已经超长、
    # 反正要被渲染层拆成前后两条字幕时，合并才不会多占一屏。
    out: list[str] = []
    idx = 0
    while idx < len(rows):
        cur = rows[idx]
        nxt_row = rows[idx + 1] if idx + 1 < len(rows) else ""
        if len(cur) < 6 and nxt_row and len(nxt_row) > max_row:
            out.append(cur + nxt_row)
            idx += 2
            continue
        if len(cur) < 6 and out and len(out[-1]) > max_row:
            out[-1] = out[-1] + cur
            idx += 1
            continue
        out.append(cur)
        idx += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--max-row", type=int, default=20)
    ap.add_argument("--version", default="A")
    ap.add_argument("--src", default="文案.md")
    ap.add_argument("--title", help="常驻书名层的裸书名，默认取 brief.json 的 display_title")
    ap.add_argument("--keep-symbols", action="store_true",
                    help="不清洗装饰符号（留给 strip-subtitle-symbols.py --apply 处理）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ep_dir = ROOT / "episodes" / args.episode
    src = ep_dir / args.src
    if not src.exists():
        sys.exit(f"缺少源文案：{src}")

    brief = ep_dir / "brief.json"
    title = args.title
    if not title and brief.exists():
        import json

        title = json.loads(brief.read_text(encoding="utf-8")).get("display_title")
    raw_lines = [ln.strip() for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not title and raw_lines:
        m = TITLE_LINE.match(raw_lines[0])
        title = m.group("title") if m else raw_lines[0]

    body_src = list(raw_lines)
    if body_src and TITLE_LINE.match(body_src[0]):
        body_src = body_src[1:]

    rows = [title or args.episode]
    for line in body_src:
        line = re.sub(r"^\s*[-*•]\s*", "", line).strip()
        if not args.keep_symbols:
            line = drop_symbols(line)
        rows.extend(units(line, args.max_row))

    body_chars = sum(len(r) for r in rows[1:])
    hanzi = re.compile(r"[㐀-鿿豈-﫿]")
    src_seq = "".join(hanzi.findall("".join(body_src)))
    row_seq = "".join(hanzi.findall("".join(rows[1:])))
    if src_seq != row_seq:
        for k, (a, b) in enumerate(zip(src_seq, row_seq)):
            if a != b:
                sys.exit(f"逐字校验失败：源文案第 {k} 个字是「{a}」，生成行是「{b}」\n"
                         f"源上下文 …{src_seq[max(0,k-12):k+12]}…\n行上下文 …{row_seq[max(0,k-12):k+12]}…")
        sys.exit(f"逐字校验失败：字数不同（源 {len(src_seq)} / 行 {len(row_seq)}）")

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["version", "order", "text"])
    for i, text in enumerate(rows, start=1):
        w.writerow([args.version, i, text])
    print(f"# rows={len(rows)} body_units={len(rows) - 1} body_chars={body_chars} max_row={args.max_row}")
    print(buf.getvalue(), end="")

    if args.apply:
        out = ep_dir / "script-body.csv"
        out.write_text(buf.getvalue(), encoding="utf-8")
        print(f"\n# wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
