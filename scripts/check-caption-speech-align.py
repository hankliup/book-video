#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐行验收「字幕上屏窗 == 这一行被念出来的窗」——交付门禁缺的最后一块。

为什么现有门禁抓不到这类缺陷（2026-09-28《恶女的告白》实测）：
  · `qc-caption-alignment.py` / 边界压测只证「画面上的字 == 字幕轨」；
  · `check-caption-punctuation.py` 只证字里没标点；
  · `check-water-ripple.py` / 响度 / A-V 相关度与逐行时刻无关。
  于是静音分段法产出的 body-timings 可以把 88 行铺满 227.6s、上述门禁全绿，
  而每一行都比配音早到或晚到 2~5s（本期 |Δstart| 中位 3.04s、最大 11.82s）。
  唯一能独立证伪的第三方是配音本身。

两条判据（逐行给结论，任一不成立即判红）：
  T1 覆盖：这一行自己念出来的窗（lib/caption_speech.audible_windows），至少 70% 落在
      字幕上屏窗内（±0.35s 富余）。「行首落后」（语音已开口字还没上屏）和「提前消失」
      （字还在念，字幕已换行）都在这条上露出来。
  T2 不空放：字幕窗内真的有语音的时间占比 ≥ 45%。整行错配到别句上、时间轴塌陷成空窗
      会在这条上露出来。两类静音不计空放，都是「轴上无处可挪」而非缺陷：
      ① 落在本行自己可听窗之内、短于 1.5s 的句内停顿（朗读节奏，见《奥兰多》行 42）；
      ② 本行**读完所需**的富余停留（同一口径 max(0.55, 上屏字数÷5.2)，
      `lib/caption_speech.reading_need`）：两个字被念出来 0.14 秒，可它必须在上屏待满
      0.55 秒才读得完，那截落在真空静里的时间不是空放（《人性的枷锁》2026-10-04 行 72
      「而是：」——行尾被下一行行首顶死、行首只能收回上一句的停顿里，任何轴移动都追不上
      45%）。兜底线：整窗听不见人声的字幕照旧红，但「听不见人声」由**两条**证人共同作证——
      自适应阈值的原始密度 <20%（burst_share，会被本期响度拖着走，轻声字算成静音）**并且**
      窗内抬在 −35 dBFS 固定噪声底之上的占比也 <20%（above_noise_share）。只少一条就不红
      （《美丽新世界》2026-10-10 行 52「所以，」：原始 3% 却有 67% 在噪声底之上、峰值 −4.5 dB，
      是一条听得到的字幕；同一期行 78「最后，」96% 落在静音里，两条一起过，照旧红）。
      行锚点被推窗交叉丢弃时，若它并非大半落在一截真停顿里，lib 会恢复它（自身窗），
      卡两个突发之间的那个轻声字因此不再被当成空放。--no-acoustic-floor 整体退回旧口径。
  行边界落在静音间隙里的另计 0.10 容差（间隙里没有词，谈不上落后）。
  锚点薄弱（该行 ASR 命中 <40% 字符）只标记不判红：Whisper 同音错字多时这行的时刻本身不可信。

⚠️ 修时间轴用 `scripts/snap-body-timings-vad.py`，它和这里共用同一个 lib——
   两边口径不一致会永远修不到绿。

用法：
  python3 scripts/check-caption-speech-align.py "<书名>" [--tol 0.70] [--verbose]
  python3 scripts/check-caption-speech-align.py "<书名>" --timings <别的body-timings.json>   # 复算对照
退出码非 0 = 有行错配或时间轴塌陷，不可交付。
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.caption_speech import (above_noise_share, audible_windows, burst_share, clean, in_gap,
                                read_body_timings, read_script_rows, reading_need,
                                resolve_script_path, silence_runs, speech_bursts, voiced_share)
from lib.caption_speech import SILENCE_NOISE_DB   # 打印噪声底用 lib 的那个数，不在这里重抄

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--segment", default="body", choices=["body", "hook"],
                    help="三段结构的段：body 走 script-body.csv，hook 走 script-hook.csv；默认 body")
    ap.add_argument("--tol", type=float, default=0.70, help="T1 要求的自身语音覆盖率")
    ap.add_argument("--cover-slack", type=float, default=0.35)
    ap.add_argument("--min-voiced", type=float, default=0.45, help="T2 字幕窗内语音占比下限")
    ap.add_argument("--min-raw", type=float, default=0.20,
                    help="T2 兜底：窗内听不见任何人声的占比下限（整条压在静音上也算空放）")
    ap.add_argument("--no-read-pad", action="store_true",
                    help="T2 退回旧口径：读完所需的富余停留也计成空放（只用于对照审计，别拿它判交付）")
    ap.add_argument("--no-acoustic-floor", action="store_true",
                    help="退回旧口径：兜底线只看自适应阈值的原始密度，且不恢复被推窗交叉丢弃的自身窗"
                         "（只用于 old-vs-new 对照审计，别拿它判交付）")
    ap.add_argument("--version", default=None)
    ap.add_argument("--timings", default=None, help="指定别的 body-timings.json（复算对照用）")
    ap.add_argument("--track", default=None, action="store_true",
                    help="验收渲染产物 caption-track.json（真正上屏的那份）而不是 body-timings.json")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    ep = os.path.join(ROOT, "episodes", a.episode) if not os.path.isabs(a.episode) else a.episode
    brief = os.path.join(ep, "brief.json")
    version = a.version or (json.load(open(brief, encoding="utf-8")).get("scriptVersion")
                            if os.path.exists(brief) else None) or "A"
    seg_base = "hook" if a.segment == "hook" else "body"
    timings_path = a.timings or os.path.join(ep, "audio", f"{seg_base}-timings.json")
    asr_path = os.path.join(ep, "audio", "asr", f"{seg_base}.json")
    script_path = resolve_script_path(ep, a.segment)
    voice = os.path.join(ep, "audio", f"{seg_base}-voiceover.mp3")
    for p in (timings_path, asr_path, script_path, voice):
        if not os.path.exists(p):
            print(f"❌ 缺少文件：{p}")
            return 2

    rows = read_script_rows(script_path, version)
    caps, _ = read_body_timings(timings_path)
    text_of = dict(rows)
    # T2 逐条上屏字幕判：要读完的是这一屏的字，不是整行文案，所以字数按条目取
    entries = [(o, s, e, len(clean(text_of.get(o, "")))) for o, (s, e) in caps.items()]
    if a.track:
        # ⚠️ 渲染器会按「字数/语速」把过短的行向后延长（render_scene_map_template_fallback.py
        #    的 AVG_RATE 兜底），所以 body-timings 过了不等于成片过了。成片字幕轨才是真上屏的东西。
        track_path = os.path.join(ep, "caption-track.json")
        if not os.path.exists(track_path):
            print(f"❌ 缺少字幕轨：{track_path}")
            return 2
        items = json.load(open(track_path, encoding="utf-8"))
        caps = {}
        entries = []
        for it in items:
            o, s, e = int(it["order"]), float(it["start"]), float(it["end"])
            # display 才是观众真正看到的字（text 带着不上屏的标点）
            entries.append((o, s, e, len(clean(it.get("display") or it.get("text") or ""))))
            lo, hi = caps.get(o, (s, e))
            caps[o] = (min(lo, s), max(hi, e))     # 一行被排成多条时取并集判覆盖
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", voice], capture_output=True, text=True).stdout.strip())
    bursts, thr = speech_bursts(voice)
    starts = [b[0] for b in bursts]
    # 绝对电平的静音证人：同一条配音、同一次交付判决里第三个第三方（−35 dBFS 固定噪声底）。
    sil = None if a.no_acoustic_floor else silence_runs(voice)
    audible, weak, matched, total = audible_windows(rows, bursts, asr_path, sil=sil)

    print(f"episode={a.episode}  版本={version}  行 {len(rows)}  配音 {dur:.2f}s")
    print(f"验收对象：{'caption-track.json（真正上屏的字幕轨）' if a.track else os.path.relpath(timings_path, ROOT)}")
    print(f"ASR 字序命中 {matched}/{total} = {matched/total*100:.1f}%   "
          f"VAD 阈值 {thr:.1f} dBFS → 语音突发 {len(bursts)} 个"
          + ("" if sil is None else f"   噪声底 {SILENCE_NOISE_DB:.0f} dBFS → 静音段 {len(sil)} 个"))
    print(f"判据：T1 自身语音覆盖 ≥{a.tol*100:.0f}%（±{a.cover_slack}s 富余）  "
          f"T2 窗内语音 ≥{a.min_voiced*100:.0f}%"
          f"（{'本行句内停顿与读完所需的富余停留都不计空放' if not a.no_read_pad else '只免本行句内停顿，富余停留照计（--no-read-pad）'}，"
          f"兜底线 {a.min_raw*100:.0f}%"
          f"{'，且窗内抬在噪声底之上的占比也要 <' + f'{a.min_raw*100:.0f}%' if sil is not None else '（--no-acoustic-floor：只看自适应密度的原始占比）'}）\n")

    fails, table = [], []
    seen = set()
    for order, text in rows:
        if order not in caps:
            fails.append((order, "缺字幕条目", text[:24]))
            continue
        cs, ce = caps[order]
        seen.add(order)
        if order in audible:
            s_speech, e_speech = audible[order]
            cov = overlap(max(cs, s_speech - a.cover_slack), min(ce, e_speech + a.cover_slack),
                          s_speech, e_speech) / max(1e-6, e_speech - s_speech)
            gap_ok = in_gap(bursts, starts, cs, 0.10) or in_gap(bursts, starts, ce, 0.10)
            tol = a.tol - (0.10 if gap_ok else 0.0)
            if a.verbose or cov < tol:
                table.append((order, cs, ce, s_speech, e_speech, cov, 1.0, text))
            if cov < tol:
                head = ("行首落后" if cs > s_speech + a.cover_slack else
                        "提前消失" if ce < e_speech - a.cover_slack else "覆盖不足")
                fails.append((order, head,
                              f"覆盖 {cov*100:.0f}%<{tol*100:.0f}%  字幕 {cs:.2f}-{ce:.2f} / "
                              f"可听语音 {s_speech:.2f}-{e_speech:.2f}  {text[:24]}"))
    for order, cs, ce, n_chars in entries:            # T2 逐条上屏字幕判（一行拆多条时每条都不能空放）
        if order not in seen:
            continue
        # 窗内语音占比按 lib 的口径算：落在本行自身可听窗**之内**的短停顿是这一行自己的
        # 朗读节奏（《奥兰多》行 42 三次「真相」之间各留一秒多），不计空放，窗外才是缺陷；
        # 本行**读完所需**的那截富余停留（reading_need 同规则 A 的口径）也不计空放，
        # 否则一行 2 个字、念 0.14s、要待 0.55s 才读得完 = 数学上永远追不上 45%。
        voiced = voiced_share(bursts, cs, ce, audible.get(order),
                              readable=None if a.no_read_pad else reading_need(n_chars))
        # 兜底：一条从头到尾听不见人声的字幕，无论谁的停顿都算空放。
        # 「听不见人声」由**两条**证人共同作证，缺一条就不红：自适应阈值的原始密度
        # （burst_share，本期 −16 dBFS 上下）会说轻声字是静音，因为它按本期响度定；固定
        # −35 dBFS 噪声底（above_noise_share）只问「这里有没有抬起来的声音」。《美丽新世界》
        # 2026-10-10 行 52「所以，」窗内原始密度 3%、但 67% 的时间抬在噪声底之上（峰值 −4.5 dB），
        # 旧口径把一条听得到的字幕按「整窗听不见人声」判红，而这一行只有两个字、念 0.34 秒、
        # 必须待 0.55 秒才读得完 —— 轴上无处可挪，判红的是兜底线不是轴。
        # 反方向照旧红：真压在纯静音上的字幕两条证人一起过（《美丽新世界》行 78「最后，」
        # 窗内 96% 是 −35 dBFS 以下的静音，原始密度 12%，红；这是 ASR 把这一句钉早了 0.45s
        # 的真缺陷，走 retime 而不是改口径）。
        raw = burst_share(bursts, cs, ce)
        quiet = raw < a.min_raw
        if quiet and sil is not None:
            quiet = above_noise_share(sil, cs, ce) < a.min_raw
        if voiced < a.min_voiced or quiet:
            # 只报**被跨过的那一条**。旧写法恒定打印「窗内语音 X% <45%」，于是富余停留
            # 豁免生效、密度本身 100% 的行会显示成「窗内语音 100% <45%」——读者被推去追
            # 一条并没有红的判据，而真正红的是兜底线（《无名的裘德》2026-10-07 行 103）。
            why = []
            if voiced < a.min_voiced:
                why.append(f"窗内语音 {voiced*100:.0f}% <{a.min_voiced*100:.0f}%")
            if quiet:
                why.append(f"原始密度 {raw*100:.0f}% <{a.min_raw*100:.0f}%"
                           + ("" if sil is None else
                              f" 且窗内只有 {above_noise_share(sil, cs, ce)*100:.0f}% 抬在噪声底之上")
                           + "（整窗听不见人声，谁的停顿都不豁免）")
            fails.append((order, "T2 空放",
                          "  ".join(why) + f"  {dict(rows).get(order, '')[:24]}"))

    if table:
        print(f"{'行':>3} {'字幕':>15} {'语音':>15} {'覆盖':>6} {'窗内语音':>8}  文本")
        for order, cs, ce, s0, s1, cov, voiced, text in table:
            print(f"{order:>3} {cs:6.2f}-{ce:6.2f}  {s0:6.2f}-{s1:6.2f}  "
                  f"{cov*100:5.0f}% {voiced*100:7.0f}%  {text[:26]}")
        print()
    print(f"判红 {len(fails)} 行   锚点薄弱只标记 {len(weak)} 行 "
          f"{('' if not weak else '（' + ','.join(map(str, weak)) + '）')}")
    for order, kind, detail in fails[:40]:
        print(f"   ❌ 行{order} {kind}: {detail}")

    last_end = max((ce for _, ce in caps.values()), default=0.0)
    print(f"末条 end {last_end:.2f} ÷ 配音 {dur:.2f} = {last_end/dur:.3f}")
    if last_end / dur < 0.98:
        print("   ❌ 时间轴没铺到配音末尾（塌陷/截断）")
        return 1
    if fails:
        print("\n❌ 字幕与配音语音逐行错配，不可交付")
        return 1
    print("\n✅ 每行字幕都盖住它自己念出来的语音（逐行对齐通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
