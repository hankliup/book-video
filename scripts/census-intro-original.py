#!/usr/bin/env python3
"""第 11 道门全库普查：把每一部交付成片的片头逐条问一遍，按**当前**源码基准重列红名单。

为什么每次回滚之后都要重跑：这道门判的是**交付像素**，而它自己的基准（W2 比对 media/_raw
原图平台、W3 要求砸黑一拍）在 10-05 与 10-06 之间反演过两次。拿旧普查的红名单当现状，就会
去重渲一批今天已经健康的片子，或者放过一批今天新红的 —— 本仓库「绿灯是旧的，缺陷是新的」
这个缺陷两个方向都犯过。

为什么分类只认**每行行首的判决符号**，不认整段输出里出现过哪个词：
门自己的汇总末行恒为「片头原样核对：N 通过 / M 不通过 / K 无从证明」，也就是说
「无从证明」这四个字在每一部健康片子的输出里都存在。按 `if "无从证明" in out` 分类的
普查会把全库判成 ⚠️ —— 2026-10-06 的第一版 tmp 驱动正是这样，报出「绿 0 / 无从证明 118」，
而同一批片子逐行看是 118 个 ✅。一道只能数对自己行首符号的门才是证据；一个数不清行首的
普查是 theatre。这里同时用行首符号计数与末行 tally 交叉核对，两边不一致直接报错而不是
默默给出一个数字。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(_ROOT, "scripts", "check-intro-is-original.py")

MARK_RE = re.compile(r"^  (✅|❌|⚠️) (\S+)(?:\s+(.*))?$")
# 红判决的罪证另起一行：`     · W1 接缝不到黑：…`。4 个空格是门槛，因为末段的花名列表
# 打的是 `  · 白夜行`（两个空格），那既不是判决行也不是罪证行。
CONT_RE = re.compile(r"^\s{4,}· (.*)$")
TALLY_RE = re.compile(r"片头原样核对：(\d+) 通过 / (\d+) 不通过 / (\d+) 无从证明")
VERDICT = {"✅": "green", "❌": "red", "⚠️": "unknown"}


class CensusError(RuntimeError):
    pass


def delivered_finals() -> list[str]:
    """所有交付成片，按书名排序。"""
    root = os.path.join(_ROOT, "episodes")
    out = []
    for book in sorted(os.listdir(root)):
        f = os.path.join(root, book, "renders", f"{book}-final.mp4")
        if os.path.isfile(f):
            out.append(f)
    return out


def classify(out: str, expect: int | None = None) -> list[tuple[str, str, str]]:
    """把门的 stdout 拆成 [(verdict, name, detail)]，并与末行 tally 交叉核对。

    只认行首符号：`VERDICT` 之外的任何词都不参与判决。tally 与行首计数不一致即抛
    CensusError —— 这正是第一版 tmp 驱动会静默把全库判成 ⚠️ 的地方。
    """
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    closed = False          # 末段的「红名单」花名列表不是判决行，一概不认
    for line in out.splitlines():
        if line.startswith("不通过的都是"):
            closed = True
            continue
        if closed:
            continue
        m = MARK_RE.match(line)
        if m:
            mark, name, detail = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
            if name in seen:          # 同一期不会被判两次；重复只取第一次
                continue
            seen.add(name)
            rows.append((VERDICT[mark], name, detail))
            continue
        # 红的判决把每条罪证另起一行打 `     · W1 …`：合并进上一条判决的 detail，
        # 否则红名单里的五部片子打印出来是空白 —— 而空白读起来和「没查出问题」一样。
        c = CONT_RE.match(line)
        if c and rows:
            rows[-1] = (rows[-1][0], rows[-1][1],
                        (rows[-1][2] + " ; " if rows[-1][2] else "") + c.group(1).strip())
    t = TALLY_RE.search(out)
    if not t:
        raise CensusError("门没有打印 tally 末行：无法核对普查结果")
    green, red, unknown = (int(x) for x in t.groups())
    mine = (sum(1 for v, _, _ in rows if v == "green"),
            sum(1 for v, _, _ in rows if v == "red"),
            sum(1 for v, _, _ in rows if v == "unknown"))
    if mine != (green, red, unknown):
        raise CensusError(
            f"行首符号计数 {mine} 与 tally {((green, red, unknown))} 不一致 —— "
            f"要么门的输出格式变了，要么普查在猜")
    if expect is not None and len(rows) != expect:
        raise CensusError(f"送入 {expect} 部，只认出 {len(rows)} 部判决行")
    return rows


def run(paths: list[str], batch: int = 40) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for i in range(0, len(paths), batch):
        chunk = paths[i:i + batch]
        p = subprocess.run([sys.executable, GATE, *chunk],
                           capture_output=True, text=True, cwd=_ROOT)
        out = p.stdout + p.stderr
        rows += classify(out, expect=len(chunk))
        print(f"  … {min(i + batch, len(paths))}/{len(paths)}", flush=True)
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tmp/intro-original-census.txt")
    args = ap.parse_args(argv)

    paths = delivered_finals()
    print(f"第 11 道门全库普查｜{len(paths)} 部交付成片")
    rows = run(paths)

    green = [r for r in rows if r[0] == "green"]
    red = [r for r in rows if r[0] == "red"]
    unknown = [r for r in rows if r[0] == "unknown"]
    os.makedirs(os.path.join(_ROOT, "tmp"), exist_ok=True)
    dest = os.path.join(_ROOT, args.out)
    with open(dest, "w", encoding="utf-8") as w:
        w.write(f"第 11 道门全库普查  共 {len(rows)} 部｜绿 {len(green)}｜红 {len(red)}"
                f"｜无从证明 {len(unknown)}\n\n")
        w.write("=== 红名单（每一部的补救都是重渲该期 hook+intro 段）===\n")
        for _, name, detail in red:
            w.write(f"❌ {name}\n   {detail}\n")
        w.write("\n=== 无从证明（两段式片子没有片头接缝可问）===\n")
        for _, name, detail in unknown:
            w.write(f"⚠️ {name}  {detail}\n")
        w.write("\n=== 绿 ===\n")
        for _, name, _d in green:
            w.write(f"✅ {name}\n")
    print(f"绿 {len(green)}｜红 {len(red)}｜无从证明 {len(unknown)} → {dest}")
    for _, name, detail in red:
        print(f"❌ {name}: {detail[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
