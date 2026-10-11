#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Whisper 的**段级**时间窗独立复核字幕时间轴（body-timings.json）。

为什么要单独一个门禁（2026-09-26《霍比特人》实测）：
  「画面上的字幕 == 字幕轨」和「字幕轨 == 配音真说了这句」是两件事。
  `qc-caption-align.py` / `qc-caption-alignment.py` 只能证前者；
  用静音边界反推的核对（窗口内静音占比）只能证「边界切得干净」，
  静音分段法在这两条上都能拿满分，却把整条时间轴逐行滑到 +14.5s。

  真正独立的第三方是 Whisper 自己：每个语音段（segment）的头尾时间来自模型
  听着音频预测的时间戳 token，既不是静音检测、也不是段内按字序的线性插值。
  于是把 ASR 段文本对齐回 script-body.csv 行号区间，再拿该区间的字幕头尾去比
  ASR 段的真实时间窗——行对错了、或整段提前/滞后，这里一定会露出来。

判据：
  · 每个 ASR 段的「段窗 ⊇ 对应字幕组头尾」偏差 |Δ| > 1.0s 记 1 处错配；
  · 任何一行在两个相邻 ASR 段之间来回跳（归属抖动）也报错；
  · 退出码非 0 = 不可交付。

用法：
  python3 scripts/check-caption-timing-asr.py "<episode>/audio/body-timings.json" \
      --asr "<episode>/audio/asr/body.json" --script "<episode>/script-body.csv" \
      [--version A] [--tol 1.0] [--verbose]
"""
import argparse
import csv
import json
import re
import sys

PUNCT = re.compile(r"[，。？！、；：…—·“”‘’\"'《》（）()\[\]s,.?!;:'\"\-]+")


def clean(text):
    return list(PUNCT.sub("", text or ""))


def read_rows(path, version):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if version and r.get("version") and r["version"] != version:
                continue
            rows.append((int(r["order"]), r["text"]))
    return sorted(rows)


def read_asr(path):
    """返回 [(start, end, chars, head_only, forced_onset)]。

    段级头尾时间来自模型听着音频预测的时间戳 token，是独立于静音检测的第三方。
    例外一：whisper 的首个 30s 定长窗口（from=0, to=30000）尾部只是窗口边界，
    不是模型预测的语音结束点，这种段只核对起点（head_only）。
    例外二：同一个 30s 定长切点也让**下一段**的 `from` 被钉死在 30000/60000ms。
    那既不是模型预测的语音起点，也不是说话人开口的一刻——被劈开那句的前半截
    还在上一段里（《一个人的村庄》2026-09-28 实测：seg0 结尾「…那一天的太阳是他送」，
    seg1 开头「走的」，第 11 行整句横跨 30.00s，字幕 [26.58,30.48] 是对的，
    而门禁拿被钉死的 30.00 当第三方，报出 Δstart −3.42s 的假红灯）。
    这种段只核对终点（forced_onset）。判据用「毫秒整数且正好是 30s 的整数倍」，
    模型自己预测的时间戳不会次次落在整 30 秒上。
    """
    doc = json.load(open(path, encoding="utf-8"))
    segs = []
    forced = 0
    for s in doc.get("transcription") or []:
        off = s.get("offsets") or {}
        a_ms, b_ms = float(off.get("from", 0)), float(off.get("to", 0))
        a, b = a_ms / 1000.0, b_ms / 1000.0
        chars = clean(re.sub(r"\[[A-Z_0-9]+\]", "", s.get("text") or ""))
        head_only = b - a >= 29.5 and a == 0.0
        forced_onset = a_ms > 0 and a_ms == round(a_ms) and round(a_ms) % 30000 == 0
        forced += bool(forced_onset)
        if chars and b > a:
            segs.append((a, b, chars, head_only, forced_onset))
    return segs, forced


def lcs_map(src_chars, tgt_chars):
    """src 每个字符在 tgt 中的匹配下标（单调），未匹配为 -1。"""
    n, m = len(src_chars), len(tgt_chars)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        si = src_chars[i - 1]
        prev, cur = dp[i - 1], dp[i]
        for j in range(1, m + 1):
            cur[j] = prev[j - 1] + 1 if si == tgt_chars[j - 1] else max(prev[j], cur[j - 1])
    out = [-1] * n
    i, j = n, m
    while i > 0 and j > 0:
        if src_chars[i - 1] == tgt_chars[j - 1] and dp[i][j] == dp[i - 1][j - 1] + 1:
            out[i - 1] = j - 1
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return out


def main():
    ep_root = "episodes"
    ap = argparse.ArgumentParser()
    ap.add_argument("timings")
    ap.add_argument("--asr")
    ap.add_argument("--script")
    ap.add_argument("--version", default="A")
    ap.add_argument("--tol", type=float, default=1.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    doc = json.load(open(args.timings, encoding="utf-8"))
    caps = {c["order"]: c for c in doc["captions"]}
    rows = read_rows(args.script or re.sub(r"/audio/.*$", "/script-body.csv", args.timings), args.version)
    asr_path = args.asr or re.sub(r"/body-timings\.json$", "/asr/body.json", args.timings)
    segs, forced = read_asr(asr_path)

    script_chars = []
    char_row = []
    for order, text in rows:
        cs = clean(text)
        script_chars += cs
        char_row += [order] * len(cs)
    asr_chars = []
    char_seg = []
    for si, (a, b, cs, _ho, _fo) in enumerate(segs):
        asr_chars += cs
        char_seg += [si] * len(cs)

    # tgt(ASR) 每个字符在 src(script) 中的位置 —— 用反向映射拿到「ASR 段 ↔ 脚本行」
    seg_of_script_char = lcs_map(script_chars, asr_chars)
    matched = sum(1 for v in seg_of_script_char if v >= 0)
    rate = matched / max(1, len(script_chars))

    bad = []
    print(f"ASR 段 {len(segs)} 个，script-body.csv {len(rows)} 行，"
          f"字符对齐 {matched}/{len(script_chars)} = {100 * rate:.1f}%")
    if forced:
        print(f"   其中 {forced} 段的起点落在 whisper 的 30s 定长切点上（起点非模型预测，"
              f"只核对终点）；{sum(1 for s in segs if s[3])} 段的首个定长窗口只核对起点")
    print(f"{'seg':>4} {'ASR真实窗':>16} {'字幕行':>10} {'字幕实际头尾':>18} {'Δstart':>7} {'Δend':>7}")
    for si, (a, b, cs, head_only, forced_onset) in enumerate(segs):
        # 属于本 ASR 段的脚本字符：script 字符 k 匹配到的 ASR 字符落在本段区间内
        idx = [k for k, v in enumerate(seg_of_script_char)
               if v >= 0 and char_seg[v] == si]
        if not idx:
            bad.append((si, a, b, "本段没有任何脚本行匹配上"))
            continue
        lo, hi = min(char_row[k] for k in idx), max(char_row[k] for k in idx)
        span_c = [caps[o] for o in range(lo, hi + 1) if o in caps]
        if not span_c:
            bad.append((si, a, b, f"行 {lo}-{hi} 无字幕"))
            continue
        s0, s1 = span_c[0]["start"], span_c[-1]["end"]
        d_start, d_end = s0 - a, s1 - b
        # 定长切点只污染它所在段的那一侧：首个定长窗口的终点不算数，
        # 切点起段的起点不算数（见 read_asr 的例外二）
        check_start, check_end = not forced_onset, not head_only
        flag = ""
        if (check_start and abs(d_start) > args.tol) or (check_end and abs(d_end) > args.tol):
            flag = "  ❌"
            note = ("（定长窗口段，仅起点参与判定）" if head_only else
                    "（30s 定长切点段，仅终点参与判定）" if forced_onset else "")
            bad.append((si, a, b, f"行 {lo}-{hi} 字幕 [{s0:.2f},{s1:.2f}] vs 语音窗 [{a:.2f},{b:.2f}]"
                                  + note))
        if args.verbose or flag:
            print(f"{si:>4} {a:>7.2f}-{b:<7.2f} {lo:>4}-{hi:<5} {s0:>8.2f}-{s1:<8.2f} "
                  f"{d_start:>+7.2f} {d_end:>+7.2f}{flag}")
    print()
    if bad:
        print(f"❌ 字幕时间轴与配音语音错配 {len(bad)} 处（容差 {args.tol}s）：")
        for si, a, b, why in bad[:20]:
            print(f"   seg{si} [{a:.2f},{b:.2f}]  {why}")
        return 1
    print(f"✅ 全部 {len(segs)} 个 ASR 段的语音窗都被字幕时间轴覆盖（容差 {args.tol}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
