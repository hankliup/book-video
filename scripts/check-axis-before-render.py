#!/usr/bin/env python3
"""渲前轴闸：整片十分钟之前，先把字幕轴自己的三道对账量一遍。

2026-10-08 第五批有三期（夜莺与玫瑰 / 呐喊 / 动物农场）各花约十分钟渲完三段，然后在第七道门
「逐行盖住自身语音」翻红。红的不是像素也不是装配，是**轴**：那三期的 body-timings.json 没跑
snap/audit 两道 pass（audit dry-run 报 changes 34、late 2→0，snap 之后 max |Δstart| 0.98s），
而渲染链上没有任何一步在渲染之前问过轴一句。夜莺与玫瑰更绕：轴 11:13 补好了，成片是 11:06
按补之前的轴渲的，交付轨于是和轴对不上（行63 轴 261.35 起 / 轨 262.05 起），第七道门量的是轨，
它红得对，可真正的修法只是「拿现在这份轴重渲一次」。

所以这里把「渲完才知道轴错」换成「渲之前十秒决定放不放行」。三道判据一律**复用**既有脚本自己
打的判决行，不在这里重述阈值——重述就是第二个真源，本仓库为这件事翻过无数次车：
  1. node scripts/create-body-timings-precise.mjs <书名> --verify   （轴是不是 token 派生的）
  2. python3 scripts/check-caption-speech-align.py <书名>            （逐行盖住自身语音，轴模式）
  3. python3 scripts/audit-caption-axis.py <书名> --json             （能量边界残留：行首落后）

红就按 AGENTS.md 的既有次序就地修一轮（snap --apply → audit --apply ×2；跑两遍是行首规则 D/D2
必须在 C/C2 收尾之后再判一次的那条），修完再量一遍，还不绿 → 退出 1，调用方不许渲。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOKEN_NOTE = "行归属没有漂移"
PRECISE = ["node", "scripts/create-body-timings-precise.mjs"]
ALIGN = ["python3", "scripts/check-caption-speech-align.py"]
AUDIT = ["python3", "scripts/audit-caption-axis.py"]
SNAP = ["python3", "scripts/snap-body-timings-vad.py"]
REPAIR_ORDER_NOTE = "snap --apply → audit --apply ×2"


def run(argv: list[str]) -> str:
    """跑一个对账脚本，把它自己的输出原样拿回来（stdout+stderr 合流）。"""
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    return (proc.stdout or "") + (proc.stderr or "")


def witness_token(episode: str) -> tuple[bool, str]:
    out = run(PRECISE + [episode, "--verify"])
    lines = out.rstrip().splitlines()
    tail = lines[-1] if lines else "（无输出）"
    return TOKEN_NOTE in out, tail


def witness_cover(episode: str) -> tuple[bool, str]:
    out = run(ALIGN + [episode])
    m = re.search(r"判红\s+(\d+)\s+行", out)
    if m is None:
        return False, "读不到「判红 N 行」这一行：对账脚本的判决措辞变了"
    n = int(m.group(1))
    reds = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("❌")]
    note = f"判红 {n} 行" + ("；" + " / ".join(reds[:3]) if n else "")
    return n == 0, note


def witness_boundary(episode: str) -> tuple[bool, str]:
    out = run(AUDIT + [episode, "--json"]).strip().splitlines()
    try:
        data = json.loads(out[-1])
    except (json.JSONDecodeError, IndexError):
        return False, "读不到 audit 的 --json 末行"
    late = int(data.get("before", {}).get("late", 0))
    resid = int(data.get("residual", 0))
    changes = int(data.get("changes", 0))
    if late > 0:
        return False, f"行首落后 {late} 行未走（audit 会改 {changes} 条却没落盘）"
    if resid > 0:
        return False, f"能量边界残留 {resid} 处，修完还红"
    return True, f"before.late 0、residual {resid}"


WITNESSES = (("token 派生", witness_token), ("逐行覆盖", witness_cover), ("能量边界", witness_boundary))


def check(episode: str) -> bool:
    """三道判据全绿返回 True；任何一道红返回 False，并把红的那一道的原话打出来。"""
    green = True
    for name, fn in WITNESSES:
        ok, note = fn(episode)
        print(f"  {'✅' if ok else '❌'} {name} ｜ {note}")
        green = green and ok
    return green


def repair(episode: str) -> None:
    print(f"[axis] 轴红 —— 按 AGENTS.md 次序修一轮：{REPAIR_ORDER_NOTE}")
    for argv in (SNAP + [episode, "--apply"],
                 AUDIT + [episode, "--apply"],
                 AUDIT + [episode, "--apply"]):
        lines = [ln for ln in run(argv).splitlines() if ln.strip()]
        print("[repair] " + (lines[-1] if lines else "（无输出）"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="整片渲染之前的字幕轴对账（红则先修，修完仍红就不放行）")
    ap.add_argument("episode")
    ap.add_argument("--no-repair", action="store_true", help="只量不修（自检用）")
    args = ap.parse_args(argv)

    ep = args.episode
    axis = ROOT / "episodes" / ep / "audio" / "body-timings.json"
    if not axis.exists():
        print(f"[axis] 没有 episodes/{ep}/audio/body-timings.json —— 轴还没建，交给交付门禁判，这里不作数")
        return 0

    print(f"----- 渲前轴对账  《{ep}》 -----")
    if check(ep):
        print(f"✅ 轴闸通过  《{ep}》")
        return 0
    if args.no_repair:
        print(f"❌ 轴闸不通过（--no-repair，未尝试修轴）  《{ep}》")
        return 1

    repair(ep)
    print(f"----- 渲前轴对账  《{ep}》（修后） -----")
    if check(ep):
        print(f"✅ 轴闸通过（修轴后才绿：本期像素必须重渲才对得上新轴）  《{ep}》")
        return 0
    print(f"❌ 轴闸不通过（修完仍红）—— 本期不渲，先修 ASR/轴  《{ep}》")
    return 1


if __name__ == "__main__":
    sys.exit(main())
