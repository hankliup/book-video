#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕轴体检：用「配音能量边界」逐行复核 body-timings.json 是否真的对上语音。

为什么需要它（2026-09-28《茶馆》实测）：
  交付门禁里的字幕边界压测（qc-caption-alignment.py）证明的是「画面上显示的字幕 ==
  字幕轨在该时刻写的那条」——它拿字幕轨自己当真值，所以轴整体漂十几秒它照样 156/156 全绿。
  能量包络、响度、时长、末条覆盖比这几项同理：silence-segments 轴是**顺序铺满**的，
  形状完美、行归属全错，这一类判据对「这一行对不对得上这句话」是盲的。
  能独立作证的只有 silencedetect 的能量边界，本脚本用它逐行量三类缺陷，并可原地修好。

三类缺陷（阈值沿用《暮色将尽》《查泰莱夫人的情人》《焦虑的人》实测确认值）：
  D late-start    行首落后：本行 start 之前是连续语音（没有真停顿），且已开口 ≥0.35s
  E head-in-pause 行首抢跑：start 之后立刻进入一段其实属于上一条的真停顿
  C tail-drag     行尾拖：end 压在真停顿里 >0.25s，收回到语音停止 +tail_slack
  B blank         配音没停字幕却掉了：两条之间 >gap_blank 的空隙里静音占比 <silence_share
  A unreadable    窗口短于「按语速读完」所需（字数 / chars_per_sec，地板 read_floor）

行首默认不动（−50dB 下 token 起点与能量起点中位差只有 0.03s，动行头会把句首轻辅音
/s/ /h/ /f/ 判成静音而错误前移），只有 D/E 命中才动。写回带单调护栏
`start_i ≥ end_{i-1} + 0.02`，先备份 .pre-audit。

退出码：0 = 修补后无残留缺陷；1 = 仍有残留（交付前必须归零）。

用法：
  python3 scripts/audit-caption-axis.py "书名"
  python3 scripts/audit-caption-axis.py "书名" --apply
  python3 scripts/audit-caption-axis.py "书名" --json      # 只出一行机器可读结论
"""
import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys

DEFAULTS = dict(
    noise_db="-50dB",       # silencedetect 门限：−50dB 下 token 起点≈能量起点（中位差 0.03s）
    min_sil=0.08,           # 短于 ~2.5 帧的静音不算停顿，否则连读会被切碎
    hold=0.06,              # 两条字幕之间保留的可感知留白（约 2 帧）
    chars_per_sec=5.2,      # 本系列旁白约 312 字/分钟
    read_floor=0.55,        # 一闪而过也要能看清
    read_tol=0.15,          # 只差 0.15s 以内不算读不完（后面没空隙可借时是常态）
    gap_blank=0.15,         # 超过这个空隙才查静音占比
    silence_share=0.30,     # 空隙里静音占比低于此 → 真闪断
    speech_share=0.30,      # 且空隙里听得见的人声占比要高于此，才算「下一条已经在说」
    tail_slack=0.10,
    late_start=0.35,
    head_lookahead=0.25,    # 行首之后多久起静音才算「抢跑」
    head_sil_min=0.30,
    head_sil_cross=0.35,
    min_window=0.45,
    tail_drag=0.25,
)

CLAUSE = re.compile(r"[，。！？；：、…·\s]")

# 交付门禁 check-caption-speech-align.py 的 T2（字幕窗内语音占比下限，--min-voiced 默认值）。
# 修轴的手按这条下限收尾巴，两边才不会一个刚收完、另一个刚判红。
T2_MIN_VOICED = 0.45

# 本文件的 −50 dB silencedetect 会把气口、齿音、房间底噪也算成语音，而门禁的
# lib/caption_speech.speech_bursts（自适应能量突发，约 −17 dBFS）不会：前者比后者平均
# 晚约 0.2 秒停止。AGENTS.md 要求「可听窗」只有 caption_speech 一个来源，正是为了
# 让修轴的手和验收的门不各说各话。
# 同一个理由管着「这段空隙是不是有人在说话」：−50 dB 说 68.1–68.7 之间只有 17% 静音
# （其实是一口气），于是 B 把上一行字幕借过去填满；门禁按突发看，那 0.6 秒一个人声都没有，
# 判这半秒尾巴空放。一个刚借满、一个刚判红，两边永远追不上。所以 B 的触发条件和
# measure() 的空窗计数都改用 burst_share —— 补空窗的手和量空窗的尺必须是同一把。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from lib.caption_speech import (audible_windows, burst_share, reading_need, silence_runs,
                                    speech_bursts, voiced_share)
except Exception:                       # numpy 缺失等环境问题：退回只用 −50 dB 停止点
    audible_windows = burst_share = speech_bursts = voiced_share = reading_need = silence_runs = None
# 规则 A 的「读不完」和门禁 T2 的「富余停留豁免」必须是同一个数：默认值从 lib 的常量取，
# 免得改了这边忘了那边，修轴的手又把验收的门刚放行的一行收回去。
try:
    from lib.caption_speech import READ_FLOOR as _READ_FLOOR, READ_RATE as _READ_RATE
    DEFAULTS["read_floor"], DEFAULTS["chars_per_sec"] = _READ_FLOOR, _READ_RATE
except Exception:
    pass


def burst_end_of(bursts, start, end):
    """属于这一行自己的最后一段可听能量：与 [start,end] 有交叠的突发的最晚结束时刻。"""
    hits = [b1 for b0, b1 in bursts if b0 < end - 0.02 and b1 > start + 0.02]
    return max(hits) if hits else None


def read_rows(path, version):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if version and r.get("version") and r["version"] != version:
                continue
            rows[int(r["order"])] = r["text"].strip()
    return rows


def silence_intervals(voice, noise_db, min_sil, duration):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", voice, "-af",
         f"silencedetect=noise={noise_db}:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True)
    sil, open_start = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start:\s*([\d.]+)", line)
        if m:
            open_start = float(m.group(1))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and open_start is not None:
            sil.append((open_start, float(m.group(1))))
            open_start = None
    if open_start is not None:                      # 静音一直持续到结尾
        sil.append((open_start, duration))
    sil = sorted((min(a, duration), min(b, duration)) for a, b in sil)
    merged = []
    for a, b in sil:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def voiced_from_silence(sil, duration):
    voiced, cur = [], 0.0
    for a, b in sil:
        if a > cur + 1e-6:
            voiced.append((cur, a))
        cur = max(cur, b)
    if cur < duration - 1e-6:
        voiced.append((cur, duration))
    return voiced


def share_of(sil, a, b):
    if b <= a:
        return 1.0
    return sum(max(0.0, min(y, b) - max(x, a)) for x, y in sil) / (b - a)


def onset_of(voiced, t):
    """t 所在连续语音段的起点；t 落在静音里则返回 None。"""
    for a, b in voiced:
        if a < t <= b + 1e-6:
            return a
    return None


def silence_start_of(sil, t):
    """t 落在哪段真停顿里：返回该停顿的起点（= 语音停止时刻）；不在停顿里返回 None。"""
    for a, b in sil:
        if a <= t < b:
            return a
    return None


def measure(items, sil, voiced, chars_of, args, duration, keep_visible=(), bursts=()):
    """按同一套判据量一条轴，返回缺陷清单与统计。

    keep_visible 里的行号是 E（行首抢跑）判给它的「停顿归上一条」那一行：它的行尾**应当**
    铺进那段真停顿，直到下一条开口前，所以不能按 C 的行尾拖计。
    """
    late, drag, holes, unread, tight = [], [], [], [], []
    for i, c in enumerate(items):
        on = onset_of(voiced, c["start"])
        if on is not None and c["start"] - on >= args.late_start:
            prev_end = items[i - 1]["end"] if i else -1.0
            if on > prev_end + 0.10:
                late.append((c["order"], round(c["start"] - on, 2)))
        stop = silence_start_of(sil, c["end"])
        if stop is not None and c["end"] - stop > args.tail_drag:
            # 只有「收回去也不影响读完」才算真缺陷：为保障阅读时间而压进停顿的行尾是设计，
            # 不是缺陷（否则 C 刚收回、A/B 又延回来，门禁永远红）。末行留到片尾同理豁免。
            need = max(args.read_floor, chars_of(c["order"]) / args.chars_per_sec)
            if (stop + args.tail_slack - c["start"] >= need and c is not items[-1]
                    and c["order"] not in keep_visible):
                drag.append((c["order"], round(c["end"] - stop, 2)))
        need = max(args.read_floor, chars_of(c["order"]) / args.chars_per_sec)
        if c["end"] - c["start"] < need - args.read_tol:
            # 已经借到下一条开口前还是短 → 这是配音本身的连读密度，不是轴的缺陷
            nxt_start = items[i + 1]["start"] if i + 1 < len(items) else duration + 1.0
            if nxt_start - args.hold - c["end"] < 0.06:
                tight.append((c["order"], round(need - (c["end"] - c["start"]), 2)))
            else:
                unread.append((c["order"], round(need - (c["end"] - c["start"]), 2)))
        if i + 1 < len(items):
            gap = items[i + 1]["start"] - c["end"]
            hi = items[i + 1]["start"]
            if (gap > args.gap_blank and share_of(sil, c["end"], hi) < args.silence_share
                    and (burst_share(bursts, c["end"], hi) if burst_share else 1.0)
                    >= args.speech_share):
                # 空隙里 −50 dB 不算静音、却又听不见任何人声（气口、齿音、底噪）→ 那不是
                # 「下一条的词已经在说」，是两句之间的停顿。停顿里让字幕消失是正确的，
                # 把它计成空窗就会驱使 B 去借时间，借完又被门禁 T2 判成空放。
                holes.append((c["order"], round(gap, 2),
                              round(share_of(sil, c["end"], hi), 2)))
    return dict(late=late, drag=drag, holes=holes, unread=unread, tight=tight)


def brief(d):
    def mx(pairs):
        return max((v for _, v in pairs), default=0.0)
    extra = f" 连读挤压 {len(d['tight'])} 行(不计缺陷)" if d.get("tight") else ""
    return (f"行首落后 {len(d['late'])} 行(最大 {mx(d['late']):.2f}s) "
            f"行尾拖 {len(d['drag'])}(最大 {mx(d['drag']):.2f}s) "
            f"行内空窗 {len(d['holes'])} 处 读不完 {len(d['unread'])} 行{extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--segment", default="body", choices=["body", "hook"],
                    help="三段结构的段：body 走 script-body.csv，hook 走 script-hook.csv；默认 body")
    ap.add_argument("--voice", default=None,
                    help="缺省按 --segment 取 audio/<seg>-voiceover-story.mp3")
    ap.add_argument("--timings", default=None,
                    help="缺省按 --segment 取 audio/<seg>-timings.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), default=v, type=type(v))
    args = ap.parse_args()

    root = os.environ.get("ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ep_dir = os.path.join(root, "episodes", args.episode)
    # 三段结构：钩子段走 script-hook.csv，正文走 script-body.csv；历史单段期只有 script-body.csv。
    is_hook = args.segment == "hook"
    # --segment 必须同时换掉轴文件和音频文件。以前这两个默认值写死成 body，
    # 于是 `--segment=hook --apply` 会拿 2 行的钩子文案去修 107 行的正文轴
    # （《荒原狼》2026-10-01：正文行 1 的字幕窗被从 0.92s 拖到 2.21s，压进下一行的开口，
    # 而文案缺失的行按「1 字」算可读时长，unreadable/tail-drag 全按错误的字数判）。
    seg_base = "hook" if is_hook else "body"
    if args.timings is None:
        args.timings = f"audio/{seg_base}-timings.json"
    if args.voice is None:
        args.voice = f"audio/{seg_base}-voiceover-story.mp3"
    timings_path = os.path.join(ep_dir, args.timings)
    voice_path = args.voice if os.path.isabs(args.voice) else os.path.join(ep_dir, args.voice)
    script_candidates = (["script-hook.csv", "script-body.csv"] if is_hook
                         else ["script-body.csv"])
    script_path = next((os.path.join(ep_dir, name) for name in script_candidates
                        if os.path.exists(os.path.join(ep_dir, name))), None)
    for p in (timings_path, voice_path):
        if not os.path.exists(p):
            raise SystemExit(f"缺文件：{p}")
    if not script_path:
        raise SystemExit(f"缺文案文件：{ep_dir} 下 {' 或 '.join(script_candidates)}")

    data = json.load(open(timings_path, encoding="utf-8"))
    caps = data["captions"]
    duration = float(data["duration"])
    rows = read_rows(script_path, data.get("scriptVersion"))

    # 轴与文案必须真的是同一段：行数对不上超过两成，就是 --segment 与轴文件配错了，
    # 与其写回一份被错误字数规则污染过的轴，不如当场停下。
    unknown = [c["order"] for c in caps if c["order"] not in rows]
    if caps and len(unknown) > max(1, int(len(caps) * 0.2)):
        raise SystemExit(
            f"轴与文案不同段：{os.path.relpath(timings_path, ep_dir)} 有 {len(caps)} 行，"
            f"其中 {len(unknown)} 行在 {os.path.basename(script_path)} 里找不到。"
            f"检查 --segment（当前 {args.segment}）是否与轴文件配对。")

    def chars_of(order):
        return max(1, len(CLAUSE.sub("", rows.get(order, ""))))

    sil = silence_intervals(voice_path, args.noise_db, args.min_sil, duration)
    voiced = voiced_from_silence(sil, duration)
    # 语音突发（C2 的口径）必须和交付门禁 T2 用同一个音频，见下一段注释：本脚本的 --voice
    # 默认是重编码的 -voiceover-story.mp3，而门禁读 audio/<segment>-voiceover.mp3。
    burst_path = next((os.path.join(ep_dir, "audio", n)
                       for n in (f"{seg_base}-voiceover.mp3",
                                 "body-voiceover.mp3", "hook-voiceover.mp3")
                       if os.path.exists(os.path.join(ep_dir, "audio", n))), voice_path)
    bursts = speech_bursts(burst_path)[0] if speech_bursts else []
    # 上一份的兄弟：交付门禁 check-caption-speech-align.py 验收 caption-track.json 时听的是
    # audio/<segment>-voiceover.mp3，而本脚本默认的 --voice 是重编码的 -voiceover-story.mp3。
    # 同一句配音、同一时长，自适应突发阈值却分别落在 −17.7 dBFS 与 −24.6 dBFS：阈值被抬高的
    # 那份会把同一句的结尾读短 0.1~0.2 秒（《恶意》行 11：修者 52.9%、门禁 44.9%，永远追不上
    # 那条线）。所以按用途分工，且**凡是对齐门禁的判据一律用门禁听的那份**（bursts）：
    #   · 行首落后 / 行尾拖 / 读不完 / 空窗计数 —— 本文件的 −50 dB 停顿证人（sil），保持原样，
    #     否则《恶意》《荒原狼》靠 −50 dB 气口建立的行尾余量会整体重排；
    #   · 窗内语音密度（C2、B 借完之后的复核）与「这段空隙里到底有没有人在说话」（B 的触发
    #     条件、measure 的空窗计数）—— 用 bursts。B 原先只看 −50 dB：68.1–68.7 之间只有 17%
    #     「静音」，于是它把上一行字幕借过去填满；门禁按突发看那 0.6 秒一个人声都没有（只有
    #     一口气），判这半秒尾巴空放。一个刚借满、一个刚判红，《奥兰多》行 29 修不动。
    # 这一行自己被念出来的窗（own span）：T2 的口径要分清「本行的句内停顿」和「本行说完之后的
    # 静音」。钉不出 own span 的行（ASR 同音错字 → 锚点薄弱）退回原始密度，宁可严。
    own_spans = {}
    asr_path = os.path.join(ep_dir, "audio", "asr", f"{seg_base}.json")
    if audible_windows and bursts and os.path.exists(asr_path):
        try:
            # own span 的「锚点是不是压扁出来的假时刻」由 lib 的第三条证人裁决：固定
            # −35 dBFS 噪声底，和交付门禁 T2 听同一份音频（bursts 那份，不是本文件 −50 dB
            # 的停顿证人 sil —— sil 回答「这里有没有停顿」，噪声底回答「这里有没有声音」）。
            # 不传进来就是修的手和验的门分叉：门禁恢复出的自身窗，本文件的 C2 看不见。
            noise_floor = silence_runs(burst_path) if silence_runs else None
            own_spans, _weak, _m, _t = audible_windows(sorted(rows.items()), bursts, asr_path,
                                                        sil=noise_floor)
        except Exception as exc:                    # 轴修得动优先于 own span 齐不齐
            print(f"[warn] own span 读不到（{exc}），C2/B 的窗内密度退回原始口径")
            own_spans = {}

    def density(order, start, end, src=None):
        """字幕窗 [start,end] 的门禁 T2 口径窗内语音占比（src=None 用门禁那份突发）。

        readable 传这一行读完所需的秒数（规则 A 同一个数）：两个字被念出来 0.14 秒、
        却必须在上屏待满 0.55 秒才读得完，那截落在真空静里的富余停留不是空放，C2 也不该
        把它收掉——收了就比观众读得还快（《人性的枷锁》2026-10-04 行 72「而是：」）。
        """
        if voiced_share is None:                    # numpy 缺失：无从判断，退回「不设限」
            return 1.0
        b = bursts if src is None else src
        pad = None if reading_need is None else reading_need(
            chars_of(order), args.chars_per_sec, args.read_floor)
        return voiced_share(b, start, end, own_spans.get(order), readable=pad)

    # 上一次 --apply 记住的「停顿归上一条」行号：那些行的行尾本来就该铺进停顿，
    # 重跑时不能再按行尾拖计（否则一个已修好的轴会被自己判红）
    keep_prev = set(int(o) for o in (data.get("alignment", {}) or {}).get("keep_visible", []))
    before = measure(caps, sil, voiced, chars_of, args, duration, keep_prev, bursts)

    # ---- 修补（顺序不能换：先挪行首 E/D，再收行尾 C/C2，然后向后借时间 B/A，
    #      最后**再补一轮行首**——C2 收回的尾巴会新暴露出下一行的行首落后，见下方注释）
    fixed = [dict(c) for c in caps]
    notes, protected = [], set()
    keep_visible = set(keep_prev)

    def fix_heads():                                       # E 行首抢跑 + D 行首落后
        for i in range(1, len(fixed)):                      # E 行首抢跑
            s = fixed[i]["start"]
            # E 的前提是「行首抢在停顿里，那段停顿该归上一条」。行首已经落在连续语音段
            # 里时前提不成立：那一刻本行自己的词正在被念出来，紧随其后的静音是**句内逗号
            # 停顿**，把头挪到静音末尾就是把本行的第一个词甩在字幕外面。
            # 《雨季不再来》2026-09-29 行 112「看，这阳光。」：行首 248.28 正压在 看 的
            # 能量突发（248.10–248.35）上，E 却看见 0.12s 后起的那段静音 [248.40, 249.21]
            # （在 head_lookahead 0.25 之内），把头推到 249.19、同时把 111 的尾铺到 249.13
            # ——「看」全程没有字幕，而上一行「走到那一天…」压着本行的 这阳光 不放。
            # 能量门自己反而记成「行首落后 0 行」，只有第七道逐行覆盖门（T1）看得见。
            if onset_of(voiced, s) is not None:
                continue
            for a, b in sil:
                if not (s - 0.05 <= a <= s + args.head_lookahead):
                    continue
                if b - a < args.head_sil_min or b - s < args.head_sil_cross:
                    continue
                target = round(b - 0.02, 2)
                if target <= s or target > fixed[i]["end"] - args.min_window:
                    continue
                notes.append(("head-in-pause", fixed[i]["order"], round(target - s, 2)))
                fixed[i]["start"] = target
                if fixed[i - 1]["end"] < target - args.hold:
                    fixed[i - 1]["end"] = round(target - args.hold, 2)
                    protected.add(i - 1)
                    keep_visible.add(fixed[i - 1]["order"])
                break

        for i, c in enumerate(fixed):                       # D 行首落后
            on = onset_of(voiced, c["start"])
            moved = False
            prev_end = fixed[i - 1]["end"] if i else -1.0
            if on is not None and c["start"] - on >= args.late_start and on > prev_end + 0.10:
                notes.append(("late-start", c["order"], round(c["start"] - on, 2)))
                c["start"] = round(on, 2)
                moved = True
            # D2 连读段里 D 认不出「本行的第一个字」。−50 dB 证人只回答「有没有停顿」，
            # 一句话连着念时它把整句并成一个语音段：《李尔王》2026-10-04 行 15「她说，父王，」
            # 的 sil 只到 28.56，之后直到 34.4 再没有停顿，D 拿到的 on=28.56 是**整段**的起点
            # 而不是本行的起点，on > prev_end+0.10 不成立 → 行首 29.65 落后自己的词 0.77s，
            # 两轮 D/E 都看不见，measure 的行首落后也记 0（它同一个证人）。只有第七道逐行覆盖
            # 门（T1 听 lib 的突发窗 28.88–30.38）看得见，判这行只盖住 49%。
            # 兜底就借用**门禁那位证人**：own span 的起点是本行字序锚到 ASR token、再钉到
            # 可听突发上的窗，比 −50 dB 的段起点更贴「这一行什么时候开口」。只允许**往前**挪
            # 到本行听得见的第一个字，绝不后移——后移会把弱声母甩出字幕，那是 E 的方向禁令。
            if not moved and own_spans:
                own_on = own_spans.get(c["order"], (None, None))[0]
                if (own_on is not None and own_on < c["start"] - 1e-6
                        and c["start"] - own_on >= args.late_start
                        and own_on > prev_end + 0.10
                        and c["end"] - own_on >= args.min_window):
                    notes.append(("late-start-own", c["order"], round(c["start"] - own_on, 2)))
                    c["start"] = round(own_on, 2)

    fix_heads()

    # C 与 B/A 会互相牵动（收回的行尾可能又因读不完被延回去），跑两轮取收敛
    for _round in range(2):
        for i, c in enumerate(fixed):                       # C 行尾拖
            if i in protected or c["order"] in keep_visible:
                continue
            stop = silence_start_of(sil, c["end"])
            if stop is None or c["end"] - stop <= args.tail_drag:
                continue
            nxt = fixed[i + 1]["start"] if i + 1 < len(fixed) else duration
            want = min(stop + args.tail_slack, nxt - args.hold)
            need = max(args.read_floor, chars_of(c["order"]) / args.chars_per_sec)
            if want - c["start"] < need:
                continue                      # 收回去就读不完，这一行的阅读时间优先
            if want > c["start"] + 0.20:
                notes.append(("tail-drag", c["order"], round(c["end"] - want, 2)))
                c["end"] = round(want, 2)

        # C2 尾巴把语音密度摊薄：交付门禁 T2 要求字幕窗内听得见的人声占比 ≥45%。
        # 短行最吃这个亏——「怎么动的手」5 个字，可听能量只到 19.25，而 −50 dB 的静音
        # 起点在 19.48，按它留 0.10 秒富余窗就是 1.36 秒，里面 0.61 秒是人声（44.9%），
        # 于是行首分毫不差、把自己的话全盖住的一行，被多出来的 0.33 秒尾巴判成空放。
        # C 的触发条件是「尾巴压在真停顿里 >0.25 秒」，这种行恰好不到 0.25，所以单开一条：
        # 只在确实会踩 T2 线时收到本行最后一段可听能量，其余行原样不动，
        # 免得一次普遍收紧把 21 行的空窗补时全部牵动、轴重新收敛到别处。
        #
        # 这里的密度必须调 lib 的 voiced_share（同一口径），不能就地再算一遍原始密度：
        # 原始密度会把《奥兰多》行 42「吹着真相，真相，真相。」那种**本行自己的句内停顿**
        # 也算成空放（八次音节摊在三秒多里，两次重复各留 1.05 / 1.21 秒），于是修轴的手
        # 看见一条无论怎么挪都追不上 45% 的红线——而门禁按 voiced_share 判它 94%，绿的。
        # 两边各算各的密度，就是 AGENTS.md 禁止的「修的人和验的人两套口径」。
        if bursts:
            for ci, c in enumerate(fixed):
                if c["end"] - c["start"] < 1e-6:
                    continue
                dens = lambda hi: density(c["order"], c["start"], hi)
                if dens(c["end"]) >= T2_MIN_VOICED:
                    continue
                own = own_spans.get(c["order"])
                own_stop = own[1] if own else burst_end_of(bursts, c["start"], c["end"])
                need = max(args.read_floor, chars_of(c["order"]) / args.chars_per_sec)
                nxt_i = ci + 1
                limit = (fixed[nxt_i]["start"] - args.hold) if nxt_i < len(fixed) else duration
                # 收到多紧有两条边界：本行最后一个可听突发（再早就把自己的字切掉了），
                # 和阅读下限（再短就读不完，《恶意》行 21「没有结案。」4 个字只有 0.52 秒
                # 可听能量，收到 40.48 只剩 0.62 秒窗 < 0.96 秒下限）。取两者的**晚**者：
                # 落在 40.84，密度 0.52/0.98 = 53%，既过 T2 也读得完。
                floor_end = c["start"] + need + 0.02
                tighter = (min(max(own_stop + args.tail_slack, floor_end), c["end"], limit)
                           if own_stop else None)
                if not tighter or tighter - c["start"] < need or tighter <= c["start"] + 0.20:
                    continue
                if dens(tighter) < T2_MIN_VOICED:
                    continue
                notes.append(("tail-thin", c["order"], round(c["end"] - tighter, 2)))
                c["end"] = round(tighter, 2)

        for i, c in enumerate(fixed):                       # B 空窗 + A 读不完
            nxt = fixed[i + 1] if i + 1 < len(fixed) else None
            limit = (nxt["start"] - args.hold) if nxt else duration - 0.02
            gap = (nxt["start"] - c["end"]) if nxt else 0.0
            need = max(args.read_floor, chars_of(c["order"]) / args.chars_per_sec)
            short = c["end"] - c["start"] < need - 0.005
            # 「空窗」的定义是**这一段空隙里有人在说话而字幕不见了**，不是「上一行消失了」。
            # 旧口径只问 −50 dB：68.1–68.7 之间只有 17% 算静音（那是一口气，不是词），
            # 于是 B 把上一行的尾巴一路借到下一条开口前，门禁按突发看那 0.6 秒没有人声，
            # 判这截尾巴空放（《奥兰多》行 29「她没跟。」：自己的字 67.53–68.00 念完，
            # C2 收到 68.10 立刻被 B 借回 68.67，密度 40%，修轴的手和门禁原地对拉）。
            # 加上 burst_share 这一条之后，两句之间的停顿就允许字幕空着——那半秒本来也没字。
            blank = (gap > args.gap_blank
                     and share_of(sil, c["end"], nxt["start"]) < args.silence_share
                     and (burst_share(bursts, c["end"], nxt["start"]) if burst_share else 1.0)
                     >= args.speech_share)
            if (short or blank) and limit > c["end"] + 0.05:
                # 空窗（下一条的词已经在说）要一路借到下一条开口前；只是「读不完」的
                # 借到阅读下限就停。多借的那半秒全是这一行没念的静音，会把交付门禁 T2
                # 的窗内语音密度摊薄到 45% 以下（《恶意》行 21：0.96 秒够读，被借到
                # 1.26 秒，密度 41% 判红）。
                target = limit if blank else min(limit, c["start"] + need + 0.02)
                if blank and voiced_share:
                    # 借到门禁线为止：阅读下限优先（短行宁可密度低也要读得完），
                    # 但空隙里越借越多静音时，最多借到 T2 刚好不掉线的那一格。
                    floor_end = c["start"] + need
                    while (target > floor_end
                           and density(c["order"], c["start"], target) < T2_MIN_VOICED):
                        target = round(target - 0.02, 2)
                if target <= c["end"]:
                    continue
                if target > c["end"] + 0.05:
                    notes.append(("blank" if blank else "unreadable",
                                  c["order"], round(target - c["end"], 2)))
                    c["end"] = round(target, 2)

        # C2 收回的尾巴会**新暴露**出下一行的行首落后：D 判「能不能把头挪到自己的
        # 语音起点」时看的是上一条此刻的 end，而上一条的尾巴要等 C/C2 才收，所以第一轮
        # 的 D 必然让位。收完再补一轮 head，那段刚腾出来的静音才真的被下一行用上
        # （《达洛维夫人》2026-10-02 行 40「也很忙。」收尾 0.49s → 行 41 行首落后 0.39s、
        #  行 87「彼得说，」收尾 0.50s → 行 88 落后 0.46s，两道门一个刚收完一个刚判红）。
        fix_heads()

        for i in range(1, len(fixed)):                      # 单调护栏
            if fixed[i]["start"] < fixed[i - 1]["end"] + 0.02:
                fixed[i]["start"] = round(fixed[i - 1]["end"] + 0.02, 2)
            if fixed[i]["end"] <= fixed[i]["start"]:
                fixed[i]["end"] = round(fixed[i]["start"] + 0.30, 2)

    seen, deduped = {}, []
    for kind, order, dt in notes:
        seen[(kind, order)] = dt
    notes = [(k, o, seen[(k, o)]) for (k, o) in seen]

    after = measure(fixed, sil, voiced, chars_of, args, duration, keep_visible, bursts)
    residual = len(after["late"]) + len(after["holes"]) + len(after["unread"]) + len(after["drag"])
    onset_err = [c["start"] - onset_of(voiced, c["start"]) for c in caps
                 if onset_of(voiced, c["start"]) is not None]

    if args.as_json:
        print(json.dumps(dict(episode=args.episode, duration=duration, captions=len(caps),
                              speech_runs=len(voiced), silences=len(sil),
                              before={k: len(v) for k, v in before.items()},
                              after={k: len(v) for k, v in after.items()},
                              applied=bool(args.apply), changes=len(notes),
                              residual=residual,
                              last_end_ratio=round(fixed[-1]["end"] / duration, 4)),
                        ensure_ascii=False))
        return 1 if residual else 0

    print(f"《{args.episode}》配音 {duration:.2f}s  字幕 {len(caps)} 行  "
          f"能量语音段 {len(voiced)} / 静音段 {len(sil)}（{args.noise_db}）")
    if onset_err:
        print(f"  行首落在连续语音里的深度：中位 {statistics.median(onset_err):.2f}s "
              f"最大 {max(onset_err):.2f}s")
    print(f"  修补前 {brief(before)}")
    print(f"  修补后 {brief(after)}")
    print(f"  末条 end {fixed[-1]['end']:.2f}s / 配音 {duration:.2f}s = "
          f"{fixed[-1]['end'] / duration:.4f}")

    changed = [(o, n) for o, n in zip(caps, fixed)
               if abs(o["start"] - n["start"]) > 0.005 or abs(o["end"] - n["end"]) > 0.005]
    if changed:
        print(f"\n{'ord':>4} {'旧头尾':>16} {'新头尾':>16} {'窗':>5}  文本")
        for old, new in changed:
            print(f"{old['order']:>4} {old['start']:>7.2f}-{old['end']:<7.2f} "
                  f"{new['start']:>7.2f}-{new['end']:<7.2f} {new['end'] - new['start']:>5.2f}  "
                  f"{rows.get(old['order'], '')[:26]}")
    kinds = {}
    for kind, _, _ in notes:
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"\n改动 {len(notes)} 条：" + ("  ".join(f"{k}×{v}" for k, v in sorted(kinds.items())) or "无"))
    for k in ("late", "holes", "unread", "drag"):
        if after[k]:
            print(f"  残留 {k}: {after[k][:12]}")
    if after["tight"]:
        print(f"  连读挤压（下一条立刻开口，已借到开口前，不计缺陷）: {after['tight'][:12]}")

    if args.apply:
        shutil.copy(timings_path, timings_path + ".pre-audit")
        data["captions"] = fixed
        al = data.setdefault("alignment", {})
        al["method"] = (al.get("method") or "") + "+axis-audit"
        al["requiresAgentReview"] = False
        al["keep_visible"] = sorted(keep_visible)
        al["agentReviewNotes"] = (
            f"{args.episode}：scripts/audit-caption-axis.py 以 {args.noise_db} 能量边界逐行复核，"
            f"改动 {len(notes)} 条；{brief(before)} → {brief(after)}。备份 .pre-audit")
        json.dump(data, open(timings_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n已写回 {timings_path}（备份 .pre-audit）")
    else:
        print("\ndry-run，未写回（加 --apply 才落盘）")
    return 1 if residual else 0


if __name__ == "__main__":
    sys.exit(main())
