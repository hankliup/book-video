#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 body-timings.json 的字幕头尾钉到「这一行真正被念出来的窗」上——修时间轴的那只手。

配套门禁：`scripts/check-caption-speech-align.py`（两者共用 `scripts/lib/caption_speech.py`，
所以修完必然过；口径分叉是这类修复最容易自欺的地方）。

三步时间轴的第三步：
  1. `node scripts/create-body-timings.mjs` / `create-body-timings-precise.mjs`
     —— 用 ASR 字序把行粗铺到配音上；
  2. 本脚本 —— 用波形能量 VAD 把每行头尾吸到听得见的边界，并保证「字幕窗盖住自己的语音」；
  3. 渲染器按新时间轴重画字幕（改的是行的起止，背景层按 scene-map 行区间切，通常要重合成）。

口径（`--help` 里说不清的部分写在注释里）：
  · 每行的字幕窗以 lib 算出的可听语音窗为**下限**（宁可早出半秒、不可晚出一毫秒），
    然后只向「下一次语音开口之前」和「下一行字幕出现之前」延展，绝不越界；
    那扇自身窗由三条互不替代的证人钉出：自适应能量突发（说话的形状）、ASR token 时刻
    （说了什么）、固定 −35 dBFS 噪声底的静音段（有没有抬起来的声音）。第三条只裁决
    「锚点是不是压扁出来的假时刻」，不参与突发阈值，所以轻声字不会被本期响度判成静音；
  · 短于最短可读时长时先向后借，借不到就把头部拖进前面那段真实的静音里；
    可读时长按**本行字数 ÷ 语速**逐行取（`--chars-per-sec`，默认 5.2 字/秒，与
    `audit-caption-axis.py` 的规则 A 同一口径），`--min-dur` 只是地板；
  · 相邻行既不重叠也不留空窗：连读处由下一行的起点接管。

用法：python3 scripts/snap-body-timings-vad.py "<书名>" [--apply] [--verbose]
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.caption_speech import (audible_windows, clean, read_body_timings,
                                read_script_rows, silence_runs, speech_bursts)
from lib.caption_speech import SILENCE_NOISE_DB   # 打印噪声底用 lib 的那个数，不在这里重抄
from lib.script_version import resolve_segment_version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_DUR = 0.90          # 最短可读时长地板：低于此观众读不完，也容易被当成闪烁
CHARS_PER_SEC = 5.2     # 本系列旁白约 312 字/分钟，与 audit-caption-axis.py 规则 A 同口径
NEXT_SLACK = 0.04       # 相邻行之间的最小间隙，避免两行同帧抢屏
MAX_LEADOUT = 0.60      # 一行念完之后最多留在屏上 0.6s；再长就是拿字幕填静音，T2 会判空放


def next_burst_start(bursts, t):
    """t 之后第一个语音突发的起点 = 这一行的字幕最晚可以留在屏上的位置。"""
    nxt = next((b[0] for b in bursts if b[0] > t + 0.02), None)
    return nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--version", default=None)
    ap.add_argument("--segment", default="body", choices=["body", "hook"],
                    help="三段结构的段：body 走 script-body.csv，hook 走 script-hook.csv；默认 body")
    ap.add_argument("--timings", default=None, help="输入 body-timings.json（默认本期现行；可从上游 ASR 对齐稿重跑）")
    ap.add_argument("--out", default=None, help="输出路径，默认写回 episodes/<书名>/audio/body-timings.json")
    ap.add_argument("--min-dur", type=float, default=MIN_DUR)
    ap.add_argument("--chars-per-sec", type=float, default=CHARS_PER_SEC,
                    help="逐行可读时长口径：本行字数 ÷ 此值（与 audit-caption-axis 规则 A 一致）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    ep = os.path.join(ROOT, "episodes", a.episode) if not os.path.isabs(a.episode) else a.episode
    # 三段结构：钩子段走 script-hook.csv / hook-timings.json / hook-voiceover.mp3，正文走 script-body.csv。
    # 历史单段期只有 script-body.csv，正文侧找不到 script-body.csv 时回落它。
    is_hook = a.segment == "hook"
    base = "hook" if is_hook else "body"
    src = a.timings or os.path.join(ep, "audio", f"{base}-timings.json")
    asr_path = os.path.join(ep, "audio", "asr", f"{base}.json")
    voice = os.path.join(ep, "audio", f"{base}-voiceover.mp3")
    script_candidates = (["script-hook.csv", "script-body.csv"] if is_hook
                         else ["script-body.csv"])
    script_path = next((os.path.join(ep, name) for name in script_candidates
                        if os.path.exists(os.path.join(ep, name))), None)
    # 版本按**本段自己的 CSV** 复核（lib/script_version，与 Node 侧 resolveSegmentVersion 同一条规则）：
    # brief.scriptVersion 说的是正文，《围城》正文 B 版而钩子只有 A 版两行，
    # 直接拿整期版本过滤 script-hook.csv 会得到 0 行，然后在这里除零。
    version, ver_changed, ver_episode = resolve_segment_version(
        ep, script_path or "", a.version)
    if ver_changed:
        print(f"版本按本段文案取 {version}（整期声明 {ver_episode} 在 "
              f"{os.path.basename(script_path)} 里没有行）")
    for p in (src, asr_path, voice):
        if not os.path.exists(p):
            print(f"❌ 缺少文件：{p}")
            return 2
    if not script_path:
        print(f"❌ 缺少文案文件：{ep} 下 {' 或 '.join(script_candidates)}")
        return 2

    rows = read_script_rows(script_path, version)
    caps, data = read_body_timings(src)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", voice], capture_output=True, text=True).stdout.strip())
    bursts, thr = speech_bursts(voice)
    # 第三条证人（固定 −35 dBFS 噪声底的静音段）要和门禁 7 一起传进 audible_windows：
    # 那扇窗里有一处「推窗交叉就整行丢锚点」的判断，只有当锚点大半落在一截真停顿里时才该丢。
    # 修的人按一套口径丢、验的人按另一套口径恢复，这一行就永远吸不到门禁认的那扇窗
    # （《美丽新世界》行86、《道林·格雷的画像》行82 正是这么一红一绿地对不上）。
    sil = silence_runs(voice)
    audible, weak, matched, total = audible_windows(rows, bursts, asr_path, sil=sil)
    print(f"episode={a.episode}  配音 {dur:.2f}s  行 {len(rows)}")
    print(f"VAD 阈值 {thr:.1f} dBFS → {len(bursts)} 个语音突发；噪声底 {SILENCE_NOISE_DB:.0f} dBFS → "
          f"{len(sil)} 个静音段；ASR 字序命中 "
          f"{matched}/{total} = {matched/total*100:.1f}%；锚点薄弱 {len(weak)} 行")

    orders = [o for o, _ in rows if o in caps]
    win = {}
    for o in orders:
        if o in audible:
            win[o] = list(audible[o])
        else:                       # 没有可信锚点的行：保留上游时刻，只参与连续性修正
            s, e = caps[o]
            win[o] = [round(s, 3), round(e, 3)]

    # ① 头尾延展：盖住自身语音 + 不留空窗，但不吃进下一行的语音、不用字幕填长静音
    for i, o in enumerate(orders):
        s, e = win[o]
        nxt = next_burst_start(bursts, e)
        hard_cap = (win[orders[i + 1]][0] - NEXT_SLACK) if i + 1 < len(orders) else dur
        ceiling = min(x for x in (hard_cap, nxt if nxt is not None else dur,
                                 e + MAX_LEADOUT) if x is not None)
        win[o][1] = round(max(e, ceiling), 3)

    # ② 最短可读时长：先向后借，借不到就把头部拖进前面那段真实静音。
    #    「前面的静音」可以越过前一行的字幕尾——那一截本来只是富余停留（lead-out），
    #    只要不超过前一行的**自身语音末尾**，收回去不会让前一行念不完，
    #    却能让本行从 0.64s 回到可读时长（实测《恶女的告白》8 行「题目叫养狗」）。
    #    可读时长按本行字数逐行取，不只是地板：末行往往字多又贴着配音末尾，
    #    向后没有可借的时间，只有逐行口径才会让它去回收前一行的富余停留。
    text_of = dict(rows)
    def need_of(order):
        return max(a.min_dur, len(clean(text_of.get(order, ""))) / a.chars_per_sec)

    for i, o in enumerate(orders):
        s, e = win[o]
        mn = need_of(o)
        if e - s >= mn:
            continue
        need = mn - (e - s)
        room = (win[orders[i + 1]][0] - NEXT_SLACK - e) if i + 1 < len(orders) else (dur - e)
        if room >= need:
            win[o][1] = round(e + need, 3)
            continue
        if i:
            prev = orders[i - 1]
            prev_speech_end = audible.get(prev, (0.0, win[prev][1]))[1]
            win[prev][1] = round(max(prev_speech_end, min(win[prev][1], s - need - NEXT_SLACK)), 3)
        head_floor = max(win[orders[i - 1]][1] + NEXT_SLACK if i else 0.0,
                         s - need - 0.60)               # 头部最多往回吞 0.6s 的纯停顿
        cand = max(head_floor, e - mn)
        # NEXT_SLACK 只是排版余量，覆盖不变量优先。两行共享同一个边界瞬间时
        # （Whisper 段内时间压缩会把下一行的行首 token 压到本行行尾 clamp 后的同一刻，
        #  两行可听窗于是零间隙），这个余量必然把本行行首推到自己的语音起点之后
        # 0.04s，正好压过覆盖复核的 0.02s 容差，整期拒绝写入——而那 0.04s 的缝
        # 根本看不出来（《对我无害之人》2026-09-29 行 65 实测）。
        # 只在「行首是被上一行的尾巴顶出来的、而不是可读时长顶出来的」时让位给覆盖。
        if o in audible and cand > audible[o][0] and head_floor >= cand - 0.0005:
            cand = audible[o][0]
        win[o][0] = round(cand, 3)
        win[o][1] = round(max(win[o][1], min(win[orders[i + 1]][0] - NEXT_SLACK
                                             if i + 1 < len(orders) else dur,
                                             win[o][0] + mn)), 3)

    # ③ 硬约束复核：单调、不重叠。冲突时先把前一行的「尾巴富余」收回去，
    #    绝不为后行去推后一行的头部——那等于让字幕比语音晚开口（正是要修的缺陷）。
    bad = []
    for i in range(1, len(orders)):
        prev, cur = orders[i - 1], orders[i]
        if win[cur][0] < win[prev][1]:
            floor_prev = audible.get(prev, (win[prev][0],))[0]
            win[prev][1] = round(max(floor_prev, min(win[prev][1], win[cur][0] - NEXT_SLACK)), 3)
            if win[cur][0] < win[prev][1]:
                # 同 ②：能落在本行语音起点上就落上去（不破坏单调性为前提），
                # 否则仍按 NEXT_SLACK 把行首推后，并在报告里记为「头被推后」。
                pref = round(min(win[prev][1] + NEXT_SLACK, audible[cur][0]), 3) if cur in audible else None
                win[cur][0] = pref if (pref is not None and pref >= win[prev][1]) \
                    else round(win[prev][1] + NEXT_SLACK, 3)
                bad.append((cur, "头被推后"))
        if win[cur][0] > win[cur][1]:
            win[cur][1] = round(win[cur][0] + 0.30, 3)
    short = [o for o in orders if win[o][1] - win[o][0] < need_of(o) - 0.005]
    uncovered = [o for o in orders if o in audible and
                 (win[o][0] > audible[o][0] + 0.02 or win[o][1] < audible[o][1] - 0.02)]
    last_end = win[orders[-1]][1]
    shifts = [abs(win[o][0] - caps[o][0]) for o in orders]
    print(f"位移 |Δstart| 中位 {sorted(shifts)[len(shifts)//2]:.2f}s 最大 {max(shifts):.2f}s")
    print(f"单调不重叠 {'OK' if not bad else '修正 ' + str(len(bad)) + ' 行'}   "
          f"未盖住自身语音 {len(uncovered)} 行   短于可读时长 {len(short)} 行"
          f"{('（' + ','.join(map(str, short)) + '）') if short else ''}   "
          f"末条 end {last_end:.2f} ÷ {dur:.2f} = {last_end/dur:.3f}")
    if uncovered:
        for o in uncovered[:10]:
            print(f"   ⚠️ 行{o}: 字幕 {win[o][0]:.2f}-{win[o][1]:.2f} / 语音 {audible[o][0]:.2f}-{audible[o][1]:.2f}")
        print("   ❌ 有行没盖住自己的语音——延展逻辑与覆盖不变量冲突，拒绝写入")
        return 1
    if last_end / dur < 0.98:
        print("   ❌ 末条没铺到配音末尾，时间轴塌陷，拒绝写入")
        return 1
    if a.verbose:
        for o in orders:
            txt = dict(rows)[o]
            print(f"  {o:>3} {caps[o][0]:7.2f}-{caps[o][1]:7.2f} → {win[o][0]:7.2f}-{win[o][1]:7.2f}   {txt[:22]}")

    if not a.apply:
        print("\n（dry-run，未写入。加 --apply 生效）")
        return 0

    # 输出跟随输入：钩子段的修补结果必须回到 hook-timings.json。
    # 写死 body-timings.json 会用 2 行钩子轴覆盖 96 行正文轴，且不会有任何报错。
    out = a.out or src
    for c in data.get("captions") or []:
        o = int(c["order"])
        if o in win:
            c["start"], c["end"] = win[o]
    data["source"] = ("ASR sentence alignment snapped to energy-VAD audible windows "
                      "(coverage-forced, monotonic, no gaps)")
    data["alignment"] = dict(data.get("alignment") or {})
    data["alignment"].update({
        "method": "asr-lcs + vad-burst-snap + audible-coverage",
        "tool": "scripts/snap-body-timings-vad.py",
        "gate": "scripts/check-caption-speech-align.py",
        "vad": {"thresholdDb": round(thr, 1), "bursts": len(bursts),
                "minBurstSec": 0.15, "gapMergeSec": 0.18},
        "asrMatchedChars": matched, "asrTotalChars": total,
        "minDisplaySeconds": a.min_dur,
        "readableSecondsPerRow": {"charsPerSec": a.chars_per_sec,
                                  "floor": a.min_dur},
        "weakAnchorRows": weak,
        "requiresAgentReview": bool(weak),
    })
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n✅ 已写入 {os.path.relpath(out, ROOT)}"
          f"{'（输入取自 ' + os.path.relpath(src, ROOT) + '）' if src != out else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
