#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把可信度不足的 Whisper 段重新识别一遍，用能量对表后替换 token 时刻。

为什么要它（2026-09-28《追忆似水年华》实测）：
  whisper.cpp 不开 --dtw 时，token 时间是在**每个 Whisper 段内**按字序线性插值出来的
  （`t_dtw = -1`）。一段 3–9 句、7–12 秒的长段里，越靠后的字被压得越扁：本期第 7 段
  （73.24–81.04s）把「白」记成 75.77，而波形在 75.19–75.44 就已经静音；「一点」被记成
  76.40–76.83，那段实际是 −40~−70 dBFS 的底噪。于是交付轴上第 35 行「能拉长一点。」
  的字幕落在 75.98–77.14 —— 0.98 秒的真静音里挂着一条字幕，而这句话的语音早在 76.22
  就念完了。`check-caption-speech-align.py --track` 的 T2（窗内语音 ≥45%）判红，
  前五道门（溯源/标点/水波/边界压测/token 重算/能量边界）全部照常绿灯。

  把整篇按更小的 TARGET_SEGMENT 重切不是办法：切点落在句内时 base 模型会漏词，
  实测 92 行里 45 行失去锚点、被摊成 0.3 秒的假块，比原轴更差。

本脚本只做一件事、且只做可验证的事：
  1. 挑出需要重定时的 Whisper 段（默认取「被判红/被指定行」覆盖到的那些段）；
  2. 用 --cut-pad 扩出一点点余量，把该段单独切成 16k 单声道 wav 重新识别（短音频里
     Whisper 的段内插值误差天然就小，实测 10s 切片能把「白」定到 75.21）；
  3. **用波形当裁判**：新旧两套 token 各数一次缺陷字数 —— 「整字落在 ≥min-silence 静音里」
     加上「0.5 秒窗里挤进 >8 个实词」（段内插值压扁的字落在别人的语音里，只数静音看不见），
     再加上「整段 token 的时间跨度没盖住本段真正有语音的地方」（整段集体挪位：每个词都只
     压住一点语音，前两项都是 0 分，见 misaligned()），
     只有新结果缺陷更少、且文本内容没被切丢（清洗后字数 ≥ 旧的 min-content 比例）才采纳；
  4. 采纳后按原文件约定同时改写 `timestamps`（绝对墙钟字符串）与 `offsets`（绝对毫秒）。

改完必须照常规流程重导轴并重新渲染（切镜 layer_start 会随字幕行首一起动）：
  python3 scripts/asr-to-simplified.py episodes/<书名>/audio/asr/body.json
  node scripts/create-body-timings-precise.mjs "<书名>"
  python3 scripts/snap-body-timings-vad.py "<书名>" --apply
  python3 scripts/audit-caption-axis.py "<书名>" --apply
  然后 EPISODE=<书名> python3 scripts/render_scene_map_template_fallback.py（不得复用旧 body-bg）

用法：
  python3 scripts/retime-asr-segments.py "<书名>" --rows 35              # dry-run 看对表
  python3 scripts/retime-asr-segments.py "<书名>" --rows 35 --apply      # 写回，留 .pre-retime
  python3 scripts/retime-asr-segments.py "<书名>" --all-long 8.0         # 重打所有 >8s 的段
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from caption_speech import (clean, read_body_timings, read_script_rows,  # noqa: E402
                            resolve_script_path, silence_runs)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.environ.get("WHISPER_MODEL", os.path.join(ROOT, "assets", "models", "whisper", "ggml-base.bin"))
TS = re.compile(r"(\d+):(\d+):(\d+),(\d+)")


def s2ms(s):
    m = TS.match(s or "")
    if not m:
        return None
    h, mi, sec, ms = (int(g) for g in m.groups())
    return ((h * 60 + mi) * 60 + sec) * 1000 + ms


def ms2s(v):
    v = int(round(v))
    return f"{v // 3600000:02d}:{(v % 3600000) // 60000:02d}:{(v % 60000) // 1000:02d},{v % 1000:03d}"


def ffprobe_dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1", p],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def silences(voice, noise="-35dB", d=0.15):
    """波形里 ≥d 秒的静音区间（绝对秒）。裁判只用波形，不用任何文本。

    真源不在这里：形状与解析都在 lib/caption_speech.silence_runs（门禁 7 的声学在场兜底线
    和自身窗恢复读的是同一个证人）。此处只把本脚本的 ffmpeg 风格参数翻译成 lib 的口径，
    这样「−35 dBFS 噪声底」在仓库里只有一个答案，改一处不会让修 ASR 的人和判字幕的人分叉。
    """
    return silence_runs(voice, noise_db=float(str(noise).replace("dB", "")), min_run=float(d))


def implausible(tokens, sil):
    """整字（含标点 token 之外的实词）完全落在静音里的 token 数 —— 时刻不可信的直接证据。"""
    bad = []
    for t in tokens:
        if not clean(t["text"]):
            continue
        a, b = t["offsets"]["from"] / 1000.0, t["offsets"]["to"] / 1000.0
        for s0, s1 in sil:
            if b <= s1 and a >= s0 and (b - a) <= (s1 - s0):
                bad.append((t["text"], round(a, 2), round(b, 2)))
                break
    return bad


CRAM_WINDOW = 0.5      # 滑动窗（秒）
CRAM_CAP = 8           # 窗内实词字数上限：实测语速中位 4.69 字/秒、p90 5.05
                       # （lib/script-policy.mjs NARRATION_RATE_CPS，99 期成片量出来的），
                       # 0.5 秒最多约 3 个字；8 已经是 3.4 倍，只对「二三十个字挤进 0.3 秒」
                       # 这种段内插值压扁报警，正常轴永远不会命中。


def crammed(tokens, win=CRAM_WINDOW, cap=CRAM_CAP):
    """被压扁的字数：落在某个「0.5 秒窗里挤了 > cap 个实词」的窗内的 token 数。

    为什么「落在静音里」这一个判据不够（2026-10-04《一生》段 13，124.40–137.84s）：
    Whisper 不开 --dtw 时段内 token 时刻是线性插值出来的，压扁的后果是把二三十个字
    塞进段尾 0.3 秒 —— 而那 0.3 秒正好是**下一句**的语音（136.57–137.54 有波形），
    于是「整字落在静音里」数为 0，老轴看起来毫发无损，交付轴却把行 38「那批公债后来
    违约，钱一夜之间缩水」钉在 137.01–137.26（14 个字 / 0.25 秒），门禁 7 只能在行 40
    的密度上发现异常。裁判必须同时看见「没声音的地方」和「不可能说那么快的地方」。
    """
    real = [(t["text"], t["offsets"]["from"] / 1000.0, t["offsets"]["to"] / 1000.0)
            for t in tokens if clean(t["text"])]
    real.sort(key=lambda x: x[1])
    flagged = set()
    for idx, (_, s, _) in enumerate(real):
        inside = [j for j in range(idx, len(real)) if real[j][1] < s + win]
        if len(inside) > cap:
            flagged.update(inside)
    return [(real[j][0], round(real[j][1], 2), round(real[j][2], 2)) for j in sorted(flagged)]


MISALIGN_MAX = 0.60    # 段内语音被本段 token 跨度盖住的比例下限（实测 0.272 / 其余 94 段 ≥0.753、中位 1.000）


def misaligned(tokens, seg_a, seg_b, sil):
    """整段 token 的时间跨度没有盖住这段真正有语音的地方 → True（第三类缺陷）。

    为什么前两个判据还不够（2026-10-10《名利场》段 79，179.74–180.62s）：这一段只有
    0.386s 语音（179.82–180.20 说「塌了。」），Whisper 却把两个实词排到 180.10–180.62，
    只盖住其中 27%。第二个词 0.44s 里有 0.415s 落在 180.204 起的静音里，可它的开口还
    压在语音上 21ms，「整字完全落在静音里」因此拿它没办法；两个实词也谈不上压扁。
    两个判据全给 0 分，重打被拒，交付轴把「塌了。」整条字幕停在 0.86s 的静音里，
    逐行覆盖门禁判 T2 空放，而前六道门一片绿。
    token 级的「大半落在静音里」补不上这个洞：实测本期 974 个 token 里有 226 个
    过半时长在词间静音里，那是插值时刻的常态，不是缺陷。所以这一条按**段**量：
    段窗里非静音的秒数，有多少落在本段 token 的时间跨度内。
    全段实测：本段 0.272，其余 94 段最低 0.753、中位 1.000，0.60 两头都留得住余量。
    """
    real = [(t["offsets"]["from"] / 1000.0, t["offsets"]["to"] / 1000.0)
            for t in tokens if clean(t["text"])]
    if not real or seg_b <= seg_a:
        return False

    def nonsil(a, b):
        tot = max(0.0, b - a)
        for s0, s1 in sil:
            lo, hi = max(a, s0), min(b, s1)
            if hi > lo:
                tot -= hi - lo
        return tot

    speech = nonsil(seg_a, seg_b)
    if speech <= 0.02:            # 这段本来就没人在说话：没有「盖住」可言
        return False
    ta, tb = min(r[0] for r in real), max(r[1] for r in real)
    return nonsil(ta, tb) / speech < MISALIGN_MAX


def rerun_whisper(voice, a, b, pad, prompt, tmpdir, next_start=None):
    """切 [a-pad, b+pad] 重新识别，返回绝对毫秒时刻的 token 列表。

    切出来的余量里会混进上一段最后几个字（实测 72.89 起切会拿到 73.0 的「来,」）。
    那些字归上一段所有，这里一律丢掉：整段 token 流必须保持全局单调，
    否则 create-body-timings-precise 会报「时间基准回绕」并被迫做单调性钳制。

    下界对称地也要裁（2026-10-05《曼斯菲尔德庄园》段 32，97.61–127.61s）：重识别用的
    是 30 秒整段，Whisper 会顺着把下一段的「衣服不理解,好几天不跟他」也念回来，时刻
    一直排到 128.06 —— 比下一段的起点 125.57 晚了 2.5 秒。留着这些 token，字序流里
    「不理解」就出现两次，而 create-body-timings-precise 的单调钳制会把后一份压到
    127.97 之后，长度偏向的 LCS 回溯偏偏选中后一份，于是行 52「姨父不理解，」的字幕
    时刻被记成 127.97–128.27（真声音在 125.97–126.68），门禁 5 把这条**正确**的交付轴
    判成「行归属漂移 −2.00s」。所以采纳的 token 一律不得越过下一段的起点。
    """
    start = max(0.0, a - pad)
    dur = min(ffprobe_dur(voice), b + pad) - start
    hi = (next_start if next_start is not None else b + pad) * 1000.0
    wav = os.path.join(tmpdir, "seg.wav")
    base = os.path.join(tmpdir, "seg")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
                    "-t", str(dur), "-i", voice, "-ar", "16000", "-ac", "1", wav], check=True)
    subprocess.run(["whisper-cli", "-ng", "-m", MODEL, "-l", "zh", "-ojf", "--no-fallback",
                    *(["--prompt", prompt] if prompt else []), "-of", base, wav],
                   check=True, capture_output=True)
    jp = f"{base}.json"
    if not os.path.exists(jp):
        raise SystemExit(f"重切识别无输出：{jp}")
    data = json.load(open(jp, encoding="utf-8"))
    toks = []
    for seg in data.get("transcription", []):
        for t in seg.get("tokens", []):
            # whisper 的控制 token（[_BEG_] / [_TT_n] / [_BLK]）不是词，留着会污染
            # 「不可信字数」与 LCS 文本，也和 asr-segmented 的口径不一致
            if (t.get("text") or "").startswith("[_"):
                continue
            # 切片里 whisper 给的是段内相对时间（两种字段都取，缺一个用另一个）
            f = t["offsets"].get("from")
            if f is None:
                f = s2ms(t["timestamps"]["from"])
            to = t["offsets"].get("to")
            if to is None:
                to = s2ms(t["timestamps"]["to"])
            if f is None or to is None:
                continue
            abs_from, abs_to = f + start * 1000.0, to + start * 1000.0
            if abs_to < abs_from:
                abs_to = abs_from
            if abs_from < a * 1000.0 - 15:      # 归上一段的尾巴，不要
                continue
            if abs_from >= hi + 15:             # 归下一段的开头，不要
                continue
            toks.append({"text": t["text"],
                         "timestamps": {"from": ms2s(abs_from), "to": ms2s(abs_to)},
                         "offsets": {"from": int(round(abs_from)), "to": int(round(abs_to))},
                         "id": t.get("id"), "p": t.get("p"), "t_dtw": t.get("t_dtw", -1)})
    return toks, start, dur


def segments_for_rows(asr, rows, timings, extra=0):
    """把「行号」翻成需要重打的 Whisper 段下标：行时间窗与段窗有重叠即入选，可再外扩 extra 段。"""
    wanted = set()
    spans = []
    for r in rows:
        cs, ce = timings[r]
        spans.append((cs, ce))
    for i, seg in enumerate(asr["transcription"]):
        a, b = seg["offsets"]["from"] / 1000.0, seg["offsets"]["to"] / 1000.0
        for cs, ce in spans:
            if min(b, ce) - max(a, cs) > 0.05:
                wanted.add(i)
                break
    if extra:
        for i in list(wanted):
            for j in range(max(0, i - extra), min(len(asr["transcription"]), i + extra + 1)):
                wanted.add(j)
    return sorted(wanted)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--segment", default="body", choices=["body", "hook"],
                    help="三段结构的段：body 走 script-body.csv，hook 走 script-hook.csv；默认 body")
    ap.add_argument("--rows", default="", help="逗号分隔的 script-body.csv 行号，如 35,36")
    ap.add_argument("--all-long", type=float, default=0.0, help="重打所有时长 >N 秒的段（与 --rows 二选一）")
    ap.add_argument("--voice", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("--cut-pad", type=float, default=0.35)
    ap.add_argument("--min-content", type=float, default=0.85, help="新文本清洗后字数不得低于旧的这一比例")
    ap.add_argument("--prompt", default="", help="传给 whisper 的短上下文（书名/专有名词），切勿整段文案")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    ep = os.path.join(ROOT, "episodes", a.episode)
    # 三段结构：路径跟随 --segment，钩子段走 hook-*，正文段走 body-*。
    seg_base = "hook" if a.segment == "hook" else "body"
    asr_path = os.path.join(ep, "audio", "asr", f"{seg_base}.json")
    voice = os.path.join(ep, a.voice) if a.voice else os.path.join(ep, "audio", f"{seg_base}-voiceover.mp3")
    timings, _ = read_body_timings(os.path.join(ep, "audio", f"{seg_base}-timings.json"))
    rows_src = read_script_rows(resolve_script_path(ep, a.segment),
                                a.version or None)
    asr = json.load(open(asr_path, encoding="utf-8"))

    if a.all_long:
        targets = [i for i, s in enumerate(asr["transcription"])
                   if (s["offsets"]["to"] - s["offsets"]["from"]) / 1000.0 > a.all_long]
    else:
        rows = [int(x) for x in re.split(r"[,\s]+", a.rows.strip()) if x]
        if not rows:
            raise SystemExit("给 --rows 35[,36] 或 --all-long 8.0 指定要重打的段")
        by_order = {int(o): t for o, t in rows_src}
        missing = [r for r in rows if r not in by_order]
        if missing:
            raise SystemExit(f"script-body.csv 没有这些行：{missing}")
        targets = segments_for_rows(asr, rows, timings)

    sil = silences(voice)
    print(f"配音 {os.path.relpath(voice, ROOT)}  {ffprobe_dur(voice):.2f}s  "
          f"静音区间 {len(sil)} 个（-35dB / ≥0.15s）")
    tmpdir = tempfile.mkdtemp(prefix=f"retime-{a.episode}-")
    accepted, rejected = [], []
    try:
        for i in targets:
            seg = asr["transcription"][i]
            s0, s1 = seg["offsets"]["from"] / 1000.0, seg["offsets"]["to"] / 1000.0
            nxt = asr["transcription"][i + 1] if i + 1 < len(asr["transcription"]) else None
            next_start = nxt["offsets"]["from"] / 1000.0 if nxt else None
            old_toks = [t for t in seg.get("tokens", [])
                        if "offsets" in t and not (t.get("text") or "").startswith("[_")]
            old_bad = implausible(old_toks, sil)
            old_cram = crammed(old_toks)
            old_mis = misaligned(old_toks, s0, s1, sil)
            new_toks, cut_start, cut_dur = rerun_whisper(
                voice, s0, s1, a.cut_pad, a.prompt, tmpdir, next_start)
            new_bad = implausible(new_toks, sil)
            new_cram = crammed(new_toks)
            new_mis = misaligned(new_toks, s0, s1, sil)
            old_n = len(clean(seg.get("text", "")))
            new_n = len(clean("".join(t["text"] for t in new_toks)))
            # 缺陷分数 = 无声里的字数 + 不可能说那么快的字数 + 整段 token 没盖住本段语音。
            # 只比「无声」会让段内插值压扁的轴占优（压扁的字落在别人的语音里，不算「无声」），
            # 见 crammed() 的说明；再只比前两项，又会让「整段集体挪位」的轴占优，见
            # misaligned() 的说明（《名利场》段 79：两个实词挪进 0.86s 静音，前两项都是 0 分）。
            old_score = len(old_bad) + len(old_cram) + (1 if old_mis else 0)
            new_score = len(new_bad) + len(new_cram) + (1 if new_mis else 0)
            content_ok = new_n >= a.min_content * old_n
            ok = content_ok and new_score < old_score and new_toks
            why = []
            if not content_ok:
                why.append(f"文本变少 {old_n}→{new_n} 字")
            if new_score >= old_score:
                why.append(f"缺陷没减少 {old_score}→{new_score}"
                           f"（无声 {len(old_bad)}→{len(new_bad)} / "
                           f"压扁 {len(old_cram)}→{len(new_cram)} / "
                           f"错位 {int(old_mis)}→{int(new_mis)}）")
            print(f"段{i} {s0:7.2f}–{s1:7.2f}s  缺陷 {old_score}→{new_score}  "
                  f"[无声 {len(old_bad)}→{len(new_bad)}｜压扁 {len(old_cram)}→{len(new_cram)}｜"
                  f"错位 {int(old_mis)}→{int(new_mis)}]  "
                  f"清洗字数 {old_n}→{new_n}  {'采纳' if ok else '不采纳 ' + '；'.join(why)}")
            for tag, lst in (("旧", old_bad[:8]), ("新", new_bad[:8])):
                if lst:
                    print(f"     {tag}无声:", " ".join(f"{t}({a_:.2f}-{b_:.2f})" for t, a_, b_ in lst))
            for tag, lst in (("旧", old_cram[:8]), ("新", new_cram[:8])):
                if lst:
                    print(f"     {tag}压扁:", " ".join(f"{t}({a_:.2f})" for t, a_, _ in lst))
            print(f"     新文本: {''.join(t['text'] for t in new_toks)[:56]}")
            (accepted if ok else rejected).append((i, new_toks))
            if ok:
                seg["text"] = "".join(t["text"] for t in new_toks)
                seg["tokens"] = new_toks
                # 段级窗口保持原边界：切余量只是给识别器一点上下文，不代表这段真的
                # 从 pad 处开始说话，改了会和上一段的段级窗重叠。
                seg["retime"] = {"source": "retime-asr-segments.py", "voice": os.path.relpath(voice, ROOT),
                                 "cut": [round(cut_start, 2), round(cut_start + cut_dur, 2)],
                                 "cut_pad": a.cut_pad, "silence": "-35dB/0.15s",
                                 "next_start": round(next_start, 3) if next_start is not None else None,
                                 "cram": [CRAM_WINDOW, CRAM_CAP],
                                 "misalign_max": MISALIGN_MAX,
                                 "implausible_before": len(old_bad), "implausible_after": len(new_bad),
                                 "crammed_before": len(old_cram), "crammed_after": len(new_cram),
                                 "misaligned_before": bool(old_mis), "misaligned_after": bool(new_mis)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not accepted:
        print("没有段通过能量对表，未改动 body.json")
        return 1
    if not a.apply:
        print(f"dry-run：{len(accepted)} 段可采纳（{','.join(str(i) for i, _ in accepted)}），加 --apply 写回")
        return 0
    shutil.copy2(asr_path, asr_path + ".pre-retime")
    json.dump(asr, open(asr_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已写回 {os.path.relpath(asr_path, ROOT)}（备份 body.json.pre-retime）")
    print("下一步：asr-to-simplified → create-body-timings-precise → snap-body-timings-vad --apply "
          "→ audit-caption-axis --apply，然后重渲正文背景（layer_start 变了，不能复用 body-bg.mp4）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
