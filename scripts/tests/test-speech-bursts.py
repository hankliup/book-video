#!/usr/bin/env python3
"""突发阈值的回归测试：0.15 秒的人声必须**恰好**算人声，不能取决于它落在第几帧。

为什么钉这一条（2026-10-04《一个陌生女人的来信》行 28「不断等待。」）：
`speech_bursts()` 以前先把帧索引换算成秒，再拿秒去比长度和间隔。
`(8260-8245)/100 = 0.14999999999999858 < 0.15`，于是这段真语音被 MIN_BURST 判成咔嗒声删掉，
这一行自己的可听窗短了 0.38 秒、窗内语音密度 31%，门禁 7 判红，而轴上无处可挪
（0.85 秒的阅读下限已经吃掉剩下的余量）。修法是全程按整数帧比较，只在返回那一刻换成秒。

合成波形把三个方向一次钉死，并且**自带双向对照**：
  ① 正好 15 帧（0.15s）的人声必须保留 —— 起始帧专门挑一个会让老口径下溢的位置，
     测试同时用老算术（先换秒再比秒）量同一段，断言它确实把这段删了：这条断言不是空转。
  ② 12 帧（0.12s）的孤立人声必须继续被删 —— 阈值还在咬，口径改动不是把门禁调松。
  ③ 6 帧 + 0.10s 间隙 + 6 帧必须合并成一段 0.22s —— GAP_MERGE 走同一套整数帧比较。
  ④ 0.03s 的咔嗒必须删、0.60s 的长语音必须留 —— 预过滤和保留两端都还在。
"""
import math
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.caption_speech import GAP_MERGE, MIN_BURST, SR, WIN, speech_bursts  # noqa: E402

FPS = SR // WIN                     # 100 帧/秒
TONE = 0.30                         # ≈ -8.5 dBFS
NOISE = 0.0015                      # ≈ -54 dBFS 的底噪，静音段不能是数字零，
                                    #               否则 p25=p90，自适应阈值退化成「等于静音」的刀尖
LONG_RUN = 60                       # 让语音帧占到全体的 15% 以上，p90 才落在语音带上


def underflow_start(length):
    """第一个「换算成秒再相减会掉到阈值以下」的起始帧（>= 100，前面留够静音）。"""
    limit = length / float(FPS)
    return next(s for s in range(100, 3000)
                if (s + length) / FPS - s / FPS < limit)


def bursts_float_seconds(flags):
    """改动前的算术：先换秒，再拿秒比 0.05 / GAP_MERGE / MIN_BURST。只为复现缺陷。"""
    segs, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j < len(flags) and flags[j]:
                j += 1
            segs.append((i / FPS, j / FPS))
            i = j
        else:
            i += 1
    merged = []
    for s, e in segs:
        if e - s < 0.05:
            continue
        if merged and s - merged[-1][1] < GAP_MERGE:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [b for b in merged if b[1] - b[0] >= MIN_BURST]


def build_layout(a):
    """[(帧数, 是否人声)]，事件按绝对帧号 ①a ②b ③c ④d ⑤长语音 摆好。"""
    return [(a, False), (15, True),                      # ① 阈值边界上的真语音
            (50, False), (12, True),                     # ② 0.12s 孤立
            (40, False), (6, True), (10, False), (6, True),   # ③ 0.10s 间隙合并
            (100, False), (3, True),                     # ④ 0.03s 咔嗒
            (60, False), (LONG_RUN, True),               # ⑤ 0.60s 长语音
            (200, False)]


def write_wav(path, layout):
    buf = bytearray()
    step = 0
    for count, voiced in layout:
        for _ in range(count * SR // FPS):
            if voiced:
                v = int(TONE * 32767 * math.sin(2 * math.pi * 220 * step / SR))
            else:
                v = int(NOISE * 32767 * math.sin(step * 0.37) * math.cos(step * 0.011))
            buf += struct.pack("<h", max(-32768, min(32767, v)))
            step += 1
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(buf))


def frame_flags(layout):
    return [1 if v else 0 for count, v in layout for _ in range(count)]


def at(bursts, frame, tol=0.011):
    return next((b for b in bursts if abs(b[0] - frame / FPS) < tol), None)


def main():
    a = underflow_start(15)
    layout = build_layout(a)
    starts = {}
    acc = 0
    for idx, (count, voiced) in enumerate(layout):
        if voiced:
            starts[idx] = acc
        acc += count
    path = f"/tmp/test_speech_bursts_{os.getpid()}.wav"
    write_wav(path, layout)
    try:
        got, thr = speech_bursts(path)
    finally:
        os.remove(path)
    old = bursts_float_seconds(frame_flags(layout))
    # 语音段在 layout 里的下标：① 1  ② 3  ③ 5(6帧)+7(6帧)  ④ 9  ⑤ 11
    s1, s2, s3, s4, s5 = starts[1], starts[3], starts[5], starts[9], starts[11]

    fails = []
    hit = at(got, s1)
    if hit is None:
        fails.append(f"① 恰好 0.15s 的人声被删了（起始帧 {s1}）：{_dump(got)}")
    elif abs(hit[1] - hit[0] - 0.15) > 1e-9:
        fails.append(f"① 0.15s 的人声被量成 {hit[1] - hit[0]:.17f}s")
    if at(old, s1) is not None:
        fails.append(f"对照失效：老口径在起始帧 {s1} 没有下溢，这条断言是空转")
    if at(got, s2) is not None:
        fails.append("② 0.12s 的孤立人声被保留了 —— 阈值不再咬，口径改动等于把门禁调松")
    m = at(got, s3)
    if m is None:
        fails.append(f"③ 6+10+6 帧没出现在结果里：{_dump(got)}")
    elif abs(m[1] - m[0] - 22 / FPS) > 1e-9:
        fails.append(f"③ 合并后 {m[1] - m[0]:.3f}s，应为 0.22s")
    elif abs(m[0] - s3 / FPS) > 1e-9:
        fails.append(f"③ 合并段的行首被挪到了 {m[0]:.3f}s，合并只该加长不该改行首")
    if at(got, s4) is not None:
        fails.append("④ 0.03s 的咔嗒声被保留了")
    if at(got, s5) is None:
        fails.append(f"⑤ 0.60s 的长语音不见了 —— 合成波形或阈值出了问题（thr={thr:.1f} dB）")
    if not (-40.0 < thr < -20.0):
        fails.append(f"自适应阈值 {thr:.1f} dB 没落在底噪(-54)和语音(-8)之间，夹具失效")

    print(f"自适应阈值 {thr:.1f} dB  下溢起始帧 {a}  新口径 {len(got)} 段 / 老口径 {len(old)} 段 "
          f"{_dump(got)}")
    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print("✅ 突发阈值按整数帧比较：0.15s 保留 / 0.12s 删除 / 0.10s 间隙合并 / 0.03s 咔嗒删除 / "
          "0.60s 保留，且老口径的浮点误杀已在同一段波形上复现")


def _dump(bursts):
    return "[" + ", ".join(f"{s:.2f}-{e:.2f}" for s, e in bursts) + "]"


if __name__ == "__main__":
    main()
