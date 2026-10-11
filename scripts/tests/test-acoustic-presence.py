#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T2 兜底线与自身窗恢复所依据的**第三条证人**（绝对电平 −35 dBFS）的回归测试。

为什么钉这一条（2026-10-10《美丽新世界》行 52「所以，」与行 86「所以，」两种形状）：
以前「整窗听不见人声」这句话实际上是由**本期录音有多响**决定的。`speech_bursts` 的阈值
按本期 p25/p90 自适应（实测各期落在 −16 ~ −21 dBFS），于是一声轻声字（「所以」的「以」、
「而是：」「最后，」）在响的期里算语音、在轻的期里算静音。两个后果，方向正好相反：
  ① 兜底线把一条**听得到**的字幕判成「整窗听不见人声」：行 52 窗内原始密度 3%，可它
     67% 的时间电平抬在 −35 dBFS 之上、峰值 −4.5 dB，而这一行只有两个字、念 0.34 秒、
     必须待 0.55 秒才读得完 —— 轴上无处可挪，红的是口径不是轴。
  ② 自身窗被「推窗交叉」整行丢弃：行首推进到下一个突发起点、行尾回退到上一个突发终点，
     两个动作撞上（e0 ≤ s0）就没有自身窗，T2 只能退回那条随响度漂的密度。可卡在两个
     突发之间的锚点有两类，混在一起就误杀轻声字（行 86 锚 217.44-217.54，静音段只到
     217.41 为止），而另一类是 Whisper 把整句钉晚了一整秒压扁出来的假时刻
     （《汤姆叔叔的小屋》行 34 锚 64.05-64.66 有 89% 落在静音段 64.12-64.75 之内）。
修法是给 lib 加 `silence_runs`/`silence_share`/`above_noise_share`（绝对电平证人，真源只在
lib；`retime-asr-segments.py` 的 `silences()` 现在就是调它），然后
  · T2 兜底线改成**两条证人合取**：原始密度 < --min-raw **并且** 窗内抬在噪声底之上的占比
    也 < --min-raw。复用已有的 --min-raw，不新增阈值；
  · `audible_windows(..., sil=)` 只在锚点**不是**过半落在一截真停顿里（`ANCHOR_PAUSE_SHARE`
    0.50）时才恢复推窗交叉丢弃的自身窗。
全库对照：交付轨判红行 143 → 113 → 本口径下再降，且**没有一行从绿翻红**（口径只会摘掉
随响度漂的误杀，不会新增红）。

六个方向一起钉死，并且自带双向对照：
  A1 silence_share 的并集/空窗/空列表算术 —— `silence_share([], a, b) == 0.0` 是 C 臂那个
     「什么都不算静音」的证人的操作数，它必须是个**实数**而不是巧合，否则 ANCHOR_PAUSE_SHARE
     是一条永不成立的条件。
  A2 `audible_windows` 的三种锚点形状（纯合成突发+静音表，不碰 ffmpeg）：sil=None 与旧口径
     逐位相同 / 过半静音不恢复 / 只卡边恢复，且恢复的是**让位给下一行之后、推进回退之前**
     的那个窗。
  B  同一份合成配音跑**六臂**门禁 CLI，比的是**判红行集合**：
     ① `--no-acoustic-floor`（旧口径）必须红 {行2 轻声, 行3 纯静音} —— 对照不是空转；
     ②  shipped 口径只红 {行3} —— 轻声行放行、真静音行照旧红；
     ②b `--no-read-pad` 与 shipped **同集合** —— 救下轻声行的不是 2026-10-04 那次「富余停留」
         豁免（它自己窗内 0.65s 里有 0.50s 是自己的语音），归因必须能排除这一手；
     ②c 两半**各自**按掉（在进程里只替换点名的那一个名字）：
          · 门禁的 `above_noise_share` 恒 0（= 旧的单证人兜底）→ 行 2 必须翻红，措辞只报兜底线；
          · lib 的 `ANCHOR_PAUSE_SHARE` = 0（= 恢复永不生效）→ 行 2 必须翻红，措辞只报「窗内语音」。
         两条措辞必须不同：合取此刻在其中一条上是假的，「只报被跨过的那一条」（《无名的裘德》
         2026-10-07 的修法）在 shipped 口径下**不可达**（原始密度<20% ∧ 抬在噪声底之上<20%
         而 窗内语音≥45% 是矛盾的），只有 ②c 摆得出那个形状。
     ③ 「证人恒说没有静音」那一版（把恢复做成无条件、把兜底做成恒不触发）必须
        **同时**给出两个错判：行3 从红变绿（压在纯静音上的字幕被洗白）+ 行4 冒出一条
        T1「提前消失」红（假锚点被恢复成自身窗）。一个臂两种翻车，才是这条口径的分量。
     行1/行5 在**所有**臂都绿（无附带伤害），行4 只在 naive 臂红；每一臂都必须报
     「锚点薄弱 0 行」且「字序命中 15/15」——否则测试其实在测一条被弃权的轴，断言会静默失效。
  B0 夹具自证：先量波形本身（突发数与边界、静音段数与边界、轻声段既不是突发也不是静音、
     阈值落在轻声与语音之间且留得住 4 dB 两侧余量），形状不对直接报「夹具失效」，
     不拿错波形去证明任何东西。
"""
import contextlib
import importlib.util
import io
import itertools
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import lib.caption_speech as caplib                                    # noqa: E402
from lib.caption_speech import (ANCHOR_PAUSE_SHARE, SILENCE_NOISE_DB, above_noise_share,  # noqa: E402
                                audible_windows, reading_need, silence_runs, silence_share,
                                speech_bursts)

GATE = os.path.join(ROOT, "scripts", "check-caption-speech-align.py")
SR = 16000
_arm_no = itertools.count(1)          # 每个 in-process 臂用独立模块名，别让 exec 结果互相顶掉

# ── 波形：三档电平，四条语音，五段底噪 ────────────────────────────────────────
# 全部按「帧 RMS dB」算（speech_bursts 用的就是这个量），silencedetect 用的是 mean-abs，
# 正弦的 mean-abs 比 RMS 低 0.9 dB —— 四个 margin 全在 4.5 dB 以上，mp3 编解码推不动它。
AMP = {"loud": 10 ** (-3.0 / 20),      # RMS −6.0 dBFS
       "soft": 10 ** (-26.0 / 20),     # RMS −29.0 dBFS：抬在 −35 之上（不是静音），
                                        #             落在自适应阈值之下（不是突发）
       "bed": 10 ** (-42.0 / 20)}      # RMS −45.0 dBFS：mean-abs −45.9 → 静音
FREQ = {"loud": 220.0, "soft": 300.0, "bed": 137.0}
SHOT = [("bed", 0.00, 0.30), ("loud", 0.30, 1.30),      # 行1 恶意来了
        ("bed", 1.30, 2.60), ("loud", 2.60, 3.80),      # 行5 都过去了（在时间轴另一头，见 rows）
        ("bed", 3.80, 5.90), ("soft", 5.90, 6.50),      # 行2 所以（轻声）
        ("bed", 6.50, 9.80), ("loud", 9.80, 11.00),     # 行3 最后（窗整段压在底噪里）
        ("bed", 11.00, 12.00), ("loud", 12.00, 12.60)]  # 行4 听人说的假锚点落在这段底噪里
DUR = SHOT[-1][2]
ROWS = [(1, "恶意来了", [(0.32, 0.55), (0.55, 0.80), (0.80, 1.05), (1.05, 1.25)], (0.28, 1.35)),
        (2, "所以", [(5.95, 6.20), (6.20, 6.45)], (5.90, 6.55)),
        (3, "最后", [(8.55, 8.80), (8.80, 9.05)], (8.50, 9.10)),
        (4, "听人说", [(11.30, 11.50), (11.50, 11.65), (11.65, 11.80)], (9.85, 10.90)),
        (5, "都过去了", [(12.05, 12.18), (12.18, 12.30), (12.30, 12.42), (12.42, 12.55)],
         (12.00, 12.58))]
CHARS = sum(len(r[1]) for r in ROWS)          # 15，测试用它自证夹具没漏字


def write_voice(path):
    n = int(round(DUR * SR))
    buf = bytearray()
    for i in range(n):
        t = i / SR
        kind = next(k for k, a, b in SHOT if a <= t < b)
        # 三档都是**恒定包络**正弦：speech_bursts 的 p25/p90 必须对得上手算，
        # 底噪一旦做调幅，帧 RMS 会在十几 dB 里漂，自适应阈值就不可预测（B0 会报夹具失效）
        v = AMP[kind] * math.sin(2 * math.pi * FREQ[kind] * t)
        buf += struct.pack("<h", max(-32767, min(32767, int(round(v * 32767)))))
    wav_path = path + ".raw.wav"
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(buf))
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", wav_path,
                    "-c:a", "libmp3lame", "-b:a", "320k", path], check=True)
    os.remove(wav_path)


def write_fixture(ep):
    os.makedirs(os.path.join(ep, "audio", "asr"), exist_ok=True)
    with open(os.path.join(ep, "script-body.csv"), "w", encoding="utf-8") as f:
        f.write("order,text\n")
        for order, text, _toks, _cap in ROWS:
            f.write(f"{order},\"{text}\"\n")
    with open(os.path.join(ep, "audio", "body-timings.json"), "w", encoding="utf-8") as f:
        json.dump({"captions": [{"order": o, "start": a, "end": b}
                                for o, _t, _k, (a, b) in ROWS]}, f, ensure_ascii=False, indent=1)
    tokens = []
    for _order, text, toks, _cap in ROWS:
        for ch, (a, b) in zip(text, toks):
            tokens.append({"text": ch, "offsets": {"from": int(round(a * 1000)),
                                                   "to": int(round(b * 1000))}})
    with open(os.path.join(ep, "audio", "asr", "body.json"), "w", encoding="utf-8") as f:
        json.dump({"transcription": [{"tokens": tokens}]}, f, ensure_ascii=False)
    write_voice(os.path.join(ep, "audio", "body-voiceover.mp3"))


def parse_reds(out):
    """成片判决里**逐行**的红：[(order, kind, detail)]。末条塌陷那条是全局结论，不算行。"""
    return [(int(m.group(1)), m.group(2), m.group(3).strip())
            for m in re.finditer(r"❌ 行(\d+) ([^:]+): (.*)", out)]


def run_gate(ep, extra=()):
    r = subprocess.run([sys.executable, GATE, ep, *extra], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


def run_gate_patched(ep, gate_set=(), lib_set=()):
    """在进程内跑门禁，并只替换点名的那**一个**名字，用来把口径的两半拆开各自验重。

    `gate_set` 改的是门禁模块自己绑定的名字（`from lib… import` 在 exec_module 时已经把函数
    对象绑好了，所以 exec 之后再 setattr 只影响门禁里的那一处调用）；`lib_set` 改的是 lib 模块
    属性，`audible_windows` 每次调用都按名字在模块全局里查，所以能影响它的内部判断。
    两组都必须在 finally 里还原 —— `lib.caption_speech` 在本进程里是共享的，漏还原会把
    后面的臂（和任何别的 in-process 消费者）一起改掉，那测的就不是口径而是残留。
    """
    restore = []
    for name, val in lib_set:
        restore.append((caplib, name, getattr(caplib, name)))
        setattr(caplib, name, val)
    try:
        spec = importlib.util.spec_from_file_location(f"gate_patched_{next(_arm_no)}", GATE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name, val in gate_set:
            setattr(mod, name, val)
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["check-caption-speech-align.py", ep]
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod.main()
        finally:
            sys.argv = argv
        return rc, buf.getvalue()
    finally:
        for obj, name, old in restore:
            setattr(obj, name, old)


def run_gate_no_witness(ep):
    """C 臂：把绝对电平证人换成「什么都听不见说」的那一份，即在进程内把
    silence_runs 桩成恒空 —— 这正是「恢复做成无条件、兜底做成恒不触发」那一版
    第一稿的形状（tmp/t2-silence-probe-1010.py 用 PURE_SILENCE_SHARE=0.90 时也是它）。
    不动任何生产文件，只改门禁模块自己绑定的那个名字。"""
    return run_gate_patched(ep, [("silence_runs", lambda *a, **k: [])])


# ── A1：静音占比的算术（不碰 ffmpeg，操作数直接写死）────────────────────────
def part_a1(fails):
    runs = [(1.0, 2.0), (1.5, 2.5), (5.0, 6.0)]
    if abs(silence_share([(1.0, 2.0)], 0.5, 3.0) - 1.0 / 2.5) > 1e-12:
        fails.append("A1 单段占比算错：" + str(silence_share([(1.0, 2.0)], 0.5, 3.0)))
    if abs(silence_share(runs, 0.0, 8.0) - 2.5 / 8.0) > 1e-12:
        fails.append(f"A1 重叠两段没取并集（1.0-2.0 + 1.5-2.5 该并成 1.5s，再加 5.0-6.0 共 2.5s）："
                     f"量到 {silence_share(runs, 0.0, 8.0) * 8.0:.3f}s")
    if silence_share(runs, 3.0, 3.0) != 0.0 or silence_share(runs, 4.0, 3.0) != 0.0:
        fails.append("A1 空窗/倒置窗必须 0.0，否则 above_noise_share 会凭空给一窗富余")
    for win in [(2.5, 5.0), (3.0, 4.0), (6.0, 7.0)]:
        if silence_share(runs, *win) != 0.0:
            fails.append(f"A1 窗 {win} 只在段端点上相碰，不该量到静音，实得 {silence_share(runs, *win)}")
    if silence_share([], 0.0, 3.0) != 0.0:
        fails.append("A1 空证人表竟然报出非零静音占比")
    # C 臂的操作数：sil=[] 时 silence_share 恒 0 → 每个被丢弃的锚点都「不过半」→ 无条件恢复。
    # 这一条必须在，否则 A2/B 里那个对照就成了「测一个永远成立的条件」。
    if not (silence_share([], 8.55, 9.05) == 0.0 and 0.0 < ANCHOR_PAUSE_SHARE):
        fails.append("A1 对照失效：sil=[] 时 silence_share 不再是 0，ANCHOR_PAUSE_SHARE 无从翻车")
    if abs(above_noise_share(runs, 0.0, 8.0) - (1 - 2.5 / 8.0)) > 1e-12:
        fails.append("A1 above_noise_share 不是 silence_share 的补")
    # 钳到窗内：段跨出窗两侧时只算窗内那一截（恢复判据与兜底线都按窗量）
    if abs(silence_share([(0.0, 10.0)], 2.0, 3.0) - 1.0) > 1e-12:
        fails.append("A1 跨出窗的静音段没被钳住")



# ── A2：audible_windows 的三种锚点形状（合成突发/静音表）────────────────────
def part_a2(fails, tmp):
    asr = os.path.join(tmp, "mini-asr.json")
    toks = [("恶", 300, 400), ("意", 400, 500),          # 锚 (0.30,0.50)：卡在两段突发之前
            ("所", 1200, 1350), ("以", 1350, 1900)]      # 正常行：整锚在突发里
    json.dump({"transcription": [{"tokens": [{"text": t, "offsets": {"from": a, "to": b}}
                                             for t, a, b in toks]}]}, open(asr, "w"))
    rows = [(1, "恶意"), (2, "所以")]
    bursts = [(1.00, 2.00), (4.00, 5.00)]                # 0.30-0.50 既不在突发也不挨着突发

    def win(sil):
        audible, weak, matched, total = audible_windows(rows, bursts, asr, sil=sil)
        return audible, weak, matched, total

    old, weak_o, m_o, t_o = win(None)
    if 1 in old:
        fails.append(f"A2 sil=None 竟然恢复了锚点（{old[1]}）—— 未接上的调用方被这次改动波及")
    if old.get(2) != (1.20, 1.90):
        fails.append(f"A2 正常行的自身窗被改了：{old.get(2)}")
    if (weak_o, m_o, t_o) != ([], 4, 4):
        fails.append(f"A2 夹具失效：迷你轴本应 4/4 命中、0 行薄弱，实得 {m_o}/{t_o} weak={weak_o}")

    deep = [(0.10, 0.90)]                                # 锚 100% 在真停顿里 → 压扁的假时刻
    if 1 in win(deep)[0]:
        fails.append(f"A2 过半落在静音里的锚点被恢复了（{win(deep)[0][1]}）："
                     "等于凭空造出一条 T1「提前消失」红，把该走 ASR 修复的缺陷伪装成轴的缺陷")
    edge = [(0.10, 0.35)]                                # 只卡着 0.05s 边 → 卡在气口里的轻声
    if win(edge)[0].get(1) != (0.30, 0.50):
        fails.append(f"A2 只卡一点边的锚点没恢复成 pinned：{win(edge)[0].get(1)}")
    # 半程：静音段**在锚点中间结束**（0.10–0.45 对锚 0.30–0.50 = 窗内 0.15/0.20 = 75%）。
    # 操作数必须跨过 ANCHOR_PAUSE_SHARE 那条 50% 线：若写成 (0.10,0.60) 则整锚都在静音里、
    # share 恒为 1.0，与 deep 那一档测的是同一个条件，边界就没被量到。
    half = [(0.10, 0.45)]
    got_half = silence_share(half, 0.30, 0.50)
    if abs(got_half - 0.75) > 1e-12:
        fails.append(f"A2 半程夹具算错（该 0.75 实得 {got_half:.4f}），"
                     "ANCHOR_PAUSE_SHARE 的边界没被量到")
    if not (ANCHOR_PAUSE_SHARE < got_half < 1.0):
        fails.append(f"A2 半程夹具没落在 {ANCHOR_PAUSE_SHARE} 与 1.0 之间：{got_half}")
    if 1 in win(half)[0]:
        fails.append("A2 过半静音的锚点被恢复了")
    # 恢复的必须是「让位给下一行之后」的窗：下一行的开口不能被吃进来
    rows3 = [(1, "恶意"), (2, "所以"), (3, "最后")]
    json.dump({"transcription": [{"tokens": [{"text": t, "offsets": {"from": a, "to": b}}
                                             for t, a, b in toks + [("最", 600, 700), ("后", 700, 900)]]}]},
              open(asr, "w"))
    got = audible_windows(rows3, bursts, asr, sil=edge)[0]
    if got.get(1, (0, 0.5))[1] > 0.5001:
        fails.append(f"A2 恢复的窗吃进了下一行的开口：{got.get(1)}")
    print(f"A2 pinned/恢复三形状：sil=None 不恢复 / 全静音不恢复 / 半程不恢复 / 只卡边恢复 → "
          f"{win(edge)[0].get(1)}，正常行 {old.get(2)}")


def part_b(fails, ep):
    voice = os.path.join(ep, "audio", "body-voiceover.mp3")
    bursts, thr = speech_bursts(voice)
    sil = silence_runs(voice)

    near = lambda seq, a, b, tol=0.09: any(abs(s - a) <= tol and abs(e - b) <= tol for s, e in seq)
    loud_shots = [(a, b) for k, a, b in SHOT if k == "loud"]
    soft_shot = next((a, b) for k, a, b in SHOT if k == "soft")
    bed_shots = [(a, b) for k, a, b in SHOT if k == "bed" and b - a >= 0.30]
    # B0 夹具自证：形状不对就报「夹具失效」，绝不拿错波形去证明口径
    if len(bursts) != len(loud_shots):
        fails.append(f"B0 夹具失效：期望 {len(loud_shots)} 段突发，量到 {len(bursts)} 段 {bursts}")
    for a, b in loud_shots:
        if not near(bursts, a, b):
            fails.append(f"B0 夹具失效：语音段 {a:.2f}-{b:.2f} 没被当作突发 {bursts}")
    if any(b0 <= (soft_shot[0] + soft_shot[1]) / 2 <= b1 for b0, b1 in bursts):
        fails.append("B0 夹具失效：轻声段被自适应阈值当成了语音，行2 那条红复现不出来")
    if not (thr > -27.0 and thr < -12.0):
        fails.append(f"B0 夹具失效：阈值 {thr:.1f} dBFS 没落在轻声(-29)与语音(-6)之间")
    for a, b in bed_shots:
        if not near(sil, a, b, 0.12):
            fails.append(f"B0 夹具失效：底噪段 {a:.2f}-{b:.2f} 没被当作静音 {sil}")
    if any(s0 <= (soft_shot[0] + soft_shot[1]) / 2 <= s1 for s0, s1 in sil):
        fails.append("B0 夹具失效：轻声段被 −35 dBFS 判成静音，那它就不是「听得到的轻声」了")

    arms = {}
    for label, extra in (("old", ("--no-acoustic-floor",)), ("shipped", ()),
                         # 富余停留豁免（2026-10-04 那次口径改动）与本次的证人无关，
                         # 抽掉它之后行2 必须照旧绿：否则「是读屏时间救的轻声行」这句
                         # 归因就永远没法排除，而下一次改兜底线的人也就没法知道它会碰到谁。
                         ("noread", ("--no-read-pad",))):
        rc, out = run_gate(ep, extra)
        arms[label] = (rc, out, parse_reds(out))
    rc_c, out_c = run_gate_no_witness(ep)
    arms["naive"] = (rc_c, out_c, parse_reds(out_c))
    # 把兜底线的**第二条证人**单独按掉（恢复照旧生效）：行2 必须翻回红 → 这条红不是
    # 「自身窗恢复」那一半顺手洗白的，两条证人合取的那一半在承重。
    rc_f, out_f = run_gate_patched(ep, [("above_noise_share", lambda *a, **k: 0.0)])
    arms["floor_off"] = (rc_f, out_f, parse_reds(out_f))
    # 反过来只按掉**恢复**（ANCHOR_PAUSE_SHARE 0.0 → 任何被跨掉的锚点都不恢复；
    # above_noise_share 与打印的噪声底不动）：行2 也必须翻回红，且它红的措辞只剩
    # 「窗内语音」——合取兜底在那儿没被跨过，就不该出现在那行字里（《无名的裘德》
    # 2026-10-07 那条「只报被跨过的那一条」的修法，在 shipped 口径下永远不可达，
    # 因为「原始密度<20% 且 抬在噪声底之上<20% 而 窗内语音≥45%」是矛盾的；这个臂
    # 才是它唯一能落地的形状，所以报红措辞由这里钉，不由行3 钉）。
    rc_r, out_r = run_gate_patched(ep, lib_set=[("ANCHOR_PAUSE_SHARE", 0.0)])
    arms["restore_off"] = (rc_r, out_r, parse_reds(out_r))

    reds = {k: {(o, kind) for o, kind, _d in r} for k, _rc, _out, r in
            [(k, *arms[k]) for k in arms]}
    detail = {k: {o: d for o, _kind, d in r} for k, _rc, _out, r in
              [(k, *arms[k]) for k in arms]}
    kinds = {k: {o: kind for o, kind, _d in r} for k, _rc, _out, r in
             [(k, *arms[k]) for k in arms]}

    # 每个臂都不得弃权（否则断言在测一条本来就没有自身窗的轴）
    for k, (_rc, out, _r) in arms.items():
        if "锚点薄弱只标记 0 行" not in out:
            fails.append(f"B 夹具失效（{k}）：有行锚点薄弱，门禁对它只标记不判红，"
                         "本测试就只在测一条被弃权的轴")
        if f"字序命中 {CHARS}/{CHARS}" not in out:
            fails.append(f"B 夹具失效（{k}）：字序没全命中，行时刻来自内插而不是 token")

    # ① 旧口径必须红这两行 —— 对照不是空转
    if (2, "T2 空放") not in reds["old"]:
        fails.append(f"① 对照失效：旧口径把轻声行 2 判绿了 {sorted(reds['old'])}")
    if (3, "T2 空放") not in reds["old"]:
        fails.append(f"① 对照失效：整窗压在底噪上的行 3 在旧口径下竟然不红 {sorted(reds['old'])}")
    # 旧口径的红不许提绝对电平证人
    if "噪声底" in detail["old"].get(2, "") + detail["old"].get(3, "") or "噪声底" in arms["old"][1]:
        fails.append("① --no-acoustic-floor 仍在引用绝对电平证人，退回开关是假的")
    # ② shipped：只红行 3，且那条红必须由**两条**证人共同作证
    if reds["shipped"] != {(3, "T2 空放")}:
        fails.append(f"② shipped 口径判红集合应为 行3/T2 空放，实得 {sorted(reds['shipped'])}"
                     f"\n{''.join(lines for lines in arms['shipped'][1].splitlines() if '❌' in lines)}")
    d3 = detail["shipped"].get(3, "")
    if "原始密度" not in d3 or "抬在" not in d3 or "整窗听不见人声" not in d3:
        fails.append(f"② 行3 的红没把两条证人一起报出来：{d3!r}")
    if "窗内语音" not in d3:
        fails.append(f"② 行3 同时跨过了「窗内语音」与兜底线，却只报了后者："
                     f"读者会以为密度是唯一的问题：{d3!r}")
    if 2 in reds["shipped"]:
        fails.append(f"② 轻声行 2 仍被判红：兜底线的合取没生效（{detail['shipped'].get(2)}）")
    # ②b 富余停留豁免不是救下行 2 的那一手（它 0.65s 窗里自己的语音就占 0.50s）
    if reds["noread"] != reds["shipped"]:
        fails.append(f"②b --no-read-pad 改变了判红集合 {sorted(reds['noread'])} ≠ "
                     f"{sorted(reds['shipped'])}：要么行 2 其实靠富余停留才绿（那这条口径的"
                     "归因就是错的），要么行 3 被这份豁免洗白了（那才是真缺陷）")
    # ②c 两半各自承重：单独按掉第二条电平证人 → 行 2 翻红；单独按掉恢复 → 行 2 也翻红。
    # 缺任何一半都测不出「合取」这件事，只有三臂对照会给出一个说不清是谁救的行 2。
    if reds["floor_off"] != {(2, "T2 空放"), (3, "T2 空放")}:
        fails.append(f"②c 按掉 above_noise_share 后判红集合是 {sorted(reds['floor_off'])}："
                     "行 2 没翻红，说明兜底线的第二条证人根本没在判断，"
                     "绿是「自身窗恢复」单独给的")
    d2f = detail["floor_off"].get(2, "")
    if "原始密度" not in d2f or "窗内语音" in d2f:
        fails.append(f"②c 行 2 在这条臂下的措辞不对（应只报被跨过的兜底线）：{d2f!r}")
    if reds["restore_off"] != {(2, "T2 空放"), (3, "T2 空放")}:
        fails.append(f"②c 按掉恢复（ANCHOR_PAUSE_SHARE=0）后判红集合是 {sorted(reds['restore_off'])}："
                     "行 2 没翻红，说明自身窗恢复根本没在判断，绿是合取兜底单独给的")
    d2r = detail["restore_off"].get(2, "")
    if "窗内语音" not in d2r:
        fails.append(f"②c 行 2 在这条臂下没报被跨过的「窗内语音」：{d2r!r}")
    if "原始密度" in d2r:
        fails.append(f"②c 行 2 报了没被跨过的兜底线（合取此刻是假的），读者会被推去追一条"
                     f"没有红的判据：{d2r!r}")
    if d2r == d2f:
        fails.append("②c 两个单臂措辞逐字相同，说明两条判据其实在同一处短路，隔离没做成")
    # 行 2 在 shipped 下必须有自身窗（T1 也才谈得上），且豁免量受 reading_need 上界管
    if "噪声底" not in arms["shipped"][1]:
        fails.append("② shipped 的头行没打印噪声底，无从核对用的是 lib 的那个数")
    m = re.search(r"噪声底 (-?\d+) dBFS → 静音段 (\d+) 个", arms["shipped"][1])
    if not m:
        fails.append("② 噪声底那行的形状变了，测试与门禁失去共同措辞")
    else:
        if float(m.group(1)) != SILENCE_NOISE_DB:
            fails.append(f"② 打印的噪声底 {m.group(1)} 不是 lib 的 {SILENCE_NOISE_DB}（重抄了一个数）")
        if int(m.group(2)) != len(sil):
            fails.append(f"② 打印的静音段数 {m.group(2)} 不等于证人自己量到的 {len(sil)}")
    if abs(reading_need(2) - 0.55) > 1e-9:
        fails.append(f"② reading_need(2) 不再是 0.55（规则 A 与 T2 分家了）：{reading_need(2)}")

    # ③ 「证人恒说没有静音」那一版必须**同时**给出两个错判：洗白行 3、冒出假 T1 行 4
    if reds["naive"] != {(4, "提前消失")}:
        fails.append(f"③ naive 臂应只红 行4/提前消失（行3 被洗白 + 假锚点变真窗），实得 "
                     f"{sorted(reds['naive'])}")
    if (3, "T2 空放") in reds["naive"]:
        fails.append("③ 对照失效：把绝对证人换成空表后行 3 仍然红，那兜底线的新那一半没在做判断")
    if (4, "提前消失") not in reds["naive"]:
        fails.append(f"③ 无条件恢复没造出假的 T1 红（判红集合 {sorted(reds['naive'])}）："
                     "ANCHOR_PAUSE_SHARE 是一条永不成立的条件，还是夹具没摆出假锚点？")
    if kinds["naive"].get(4) != "提前消失":
        fails.append(f"③ 行4 的红不是「提前消失」而是 {kinds['naive'].get(4)}，"
                     "假锚点恢复出来的窗没有落在字幕之外，B0 的形状不成立")
    # 附带伤害：行1/行5 在任何一条口径下都不该被点红；行4 只许在 naive 臂红（那正是
    # ③ 要的那条假 T1），别的臂把它判红就说明恢复判据改动溢到了正常行上。
    # ⚠️ 这里必须比 **order**，不能拿 `o in reds[k]` —— reds[k] 装的是 (order, kind) 元组，
    #    一个整数永远不在里面，那条写法恒真、恒不报，是「无法失败的断言」的标准形状。
    orders_red = {k: {o for o, _kind in reds[k]} for k in reds}
    for o in (1, 5):
        hit = sorted(k for k in orders_red if o in orders_red[k])
        if hit:
            fails.append(f"B 附带伤害：行{o} 在 {hit} 里被点红："
                         + " / ".join(f"{k}→{detail[k].get(o, '')[:40]}" for k in hit))
    hit4 = sorted(k for k in orders_red if 4 in orders_red[k])
    if hit4 != ["naive"]:
        fails.append(f"B 附带伤害：行4（假锚点）只在 naive 臂该红，实得 {hit4}")
    print("B 判红集合（六臂）："
          + " / ".join(f"{k}={sorted(reds[k]) if reds[k] else '无'}"
                       for k in ("old", "shipped", "noread", "naive", "floor_off", "restore_off")))
    print(f"   阈值 {thr:.1f} dBFS · 突发 {len(bursts)} 段 {bursts} · 静音 {len(sil)} 段")
    print(f"   行2 shipped 绿；floor_off 红：{d2f}\n                       restore_off 红：{d2r}")
    print(f"   行3 shipped 红：{d3}")
    print(f"   行4 naive 红：{detail['naive'].get(4)}")


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix=f"acoustic_presence_{os.getpid()}_")
    ep = os.path.abspath(os.path.join(tmp, "episodes", "合成期"))
    try:
        write_fixture(ep)
        part_a1(fails)
        part_a2(fails, tmp)
        part_b(fails, ep)
    finally:
        subprocess.run(["rm", "-rf", tmp])

    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print(f"✅ 绝对电平证人两头都咬得住：轻声行靠「自身窗恢复 + 两条证人合取」才放行"
          f"（各自单独按掉都翻红，措辞各报自己被跨过的那一条），富余停留豁免与它无关；"
          f"整窗底噪照旧红；推窗交叉的锚点只恢复「不是过半静音」那一类（sil=None 与旧口径逐位相同），"
          f"而把恢复做成无条件会同时洗白纯静音字幕并冒出假 T1 红")


if __name__ == "__main__":
    main()
