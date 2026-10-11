#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制 token 剔除 + 计数相等空档按位对位的回归测试。

为什么钉这两条（2026-10-07《无名的裘德》行 103「郁郁地病着」/ 行 129「钟声很响」）：
  · `asr_char_stream()` 以前用 `re.match(r"^\\[[_A-Z]+\\d*$", txt)` 剔 whisper.cpp 的控制
    token，正则**漏了闭合的 ]**，于是以 [ 开头的 token 一个都没被剔掉。实测 60 份
    body.json 里 [_TT_数字] 2636 个、[_BEG_] 510 个、[_EOT_] 10 个全部进了字符流。
    clean() 剥掉 []- 但保留下划线和字母，还把数字折成汉字，所以 [_TT_628] 留下
    `_TT_六二八` 七个幽灵字符——数字一折，它和文案里真念出来的数字是同一串字符，
    会抢走一次假命中（下面 ② 就是这个方向）。
  · align() 只会「字面命中 → 用 token 时刻」「没命中 → 两个锚点之间线性内插」。
    同音错字行两个都没有：命中 0 个字符 → 锚点薄弱 → 门禁 7 对它弃权，修轴的规则 D2
    也看不见它的自身窗，于是这一行的字幕错多久都没人判。中间态是「听见了这么多音节、
    只是字写错」：文案侧和 ASR 侧在两块真命中之间缺的**字符数相等**，这时第 i 个缺口
    字符就该取第 i 个缺口 token 自己的时刻（①③④⑤）。
  · 2026-10-10 补：那条规则最初只写在 `zip(blocks, blocks[1:])` 上，也就是**只认两块的中间**，
    而一句话还有「第一块之前」和「最后一块之后」两段空档。它们掉进两条内插兜底分支，而这两条
    分支不是内插，是编时刻：`p < 0` 把每个悬空首字符都贴到第一个真命中字的 from/to（整段共用
    一个时刻），`nxt is None` 给每个悬空尾字符编出 `te0 + 0.20`。《挪威的森林》钩子段因此
    整期渲不出来（⑦⑧⑨⑩）。

六组断言，每组都自带「改动前那个形状确实会做错」的对照，免得断言空转：
  ① 形状对照：CONTROL_TOKEN 认得全部实测形态，被撤回的旧正则一个都不认。
  ② 幽灵数字抢命中：[_TT_一六] 里的「一六」在旧口径下会命中文案的「一六」，新口径必须不命中。
  ③ 按位对位：郁郁地↔浴浴的（3 对 3）拿到**各自 token** 的时刻，且明确不等于线性内插的值。
  ④ 计数不等不许猜：漏听一个音节（3 对 2）时保持内插，flags 一律 False。
  ⑤ 按位对位的位置进内插梯级：夹在中间的近音锚点不能被后面的均分时间越过（单调）。
  ⑥ 真片对照：《无名的裘德》这两行现在拿得到自身可听窗，且窗落在真语音突发里。
  ⑦ 行尾空档（最后一块之后）按位对位，末格收尾比被撤形状编出的 0.80 晚 0.40s 以上。
  ⑧ 行首空档（第一块之前）按位对位，两格不再共用第一个真命中字的时刻；键必须是 pos[off]=off。
  ⑨ 首尾档**计数不等**（4 对 3）照旧内插、flags 全 False——新分支有条件，不是默认全锚。
  ⑩ 真片对照：《挪威的森林》钩子行 2 的窗尾越过被撤形状的 8.18，盖住最后一个听得见的瞬间。
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.caption_speech import (CONTROL_TOKEN, WEAK_ANCHOR, align, asr_char_stream,  # noqa: E402
                                audible_windows, burst_share, clean, read_script_rows,
                                resolve_script_path, speech_bursts)

RETIRED = re.compile(r"^\[[_A-Z]+\d*$")        # 改动前生产代码里那一条，原样复现
fails = []


def write_asr(tokens):
    """tokens: [(text, from_s, to_s)] → whisper.cpp 那套 json，落临时文件。"""
    d = {"transcription": [{"tokens": [
        {"text": t, "offsets": {"from": int(round(a * 1000)), "to": int(round(b * 1000))}}
        for t, a, b in tokens]}]}
    fd, p = tempfile.mkstemp(suffix=".json", prefix="asr-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return p


# ─────────────────────────────── ① 形状 ───────────────────────────────
OBSERVED = ["[_TT_628]", "[_TT_1376]", "[_BEG_]", "[_EOT_]", "[_EP_12]", "[zh]"]
for txt in OBSERVED:
    if not CONTROL_TOKEN.match(txt):
        fails.append(f"① 新正则认不出实测形态 {txt}")
    if RETIRED.match(txt):
        fails.append(f"① 被撤回的旧正则竟然能匹配 {txt}，这条对照是空转")
if clean("[_TT_628]") != "_TT_六二八":
    fails.append(f"① clean('[_TT_628]') = {clean('[_TT_628]')!r}，"
                 "幽灵字符的前提变了，下面的断言要重写")

# ─────────────────────── ② 幽灵数字抢一次假命中 ───────────────────────
# 文案「他一六」，ASR 只真听见「他」，后面跟一个控制 token [_TT_一六]。
# 旧口径：字符流 = 他 + _TT_一六 → 文案的「一六」命中控制 token 的时刻（假锚点，时刻是
# 段界而不是说话点）。新口径：字符流只有「他」，那两格必须没命中。
p = write_asr([("他", 1.0, 1.3), ("[_TT_一六]", 90.0, 90.0), ("年", 90.1, 90.4)])
A, ti, toks = asr_char_stream(p)
if "_TT_" in A or "一六" in A:
    fails.append(f"② 控制 token 的字符还留在流里：{A!r}")
if len(toks) != 2:
    fails.append(f"② token 数 {len(toks)} != 2，控制 token 没被整族剔掉")
times, etimes, flags, matched = align("他一六", A, ti, toks)
if any(flags[k] for k in (1, 2)):
    fails.append("② 文案的「一六」被控制 token 的时刻假命中了（旧口径的形状）")
if not flags[0] or times[0] != 1.0:
    fails.append(f"② 真命中「他」丢了：flags[0]={flags[0]} times[0]={times[0]}")
# 同一条流喂给旧正则：确认它**确实**会把幽灵字符放进来（对照不空转）
old_stream = "".join(clean(t) for t, _, _ in [("他", 0, 0), ("[_TT_一六]", 0, 0), ("年", 0, 0)]
                     if not (t and RETIRED.match(t)))
if "一六" not in old_stream:
    fails.append(f"② 旧口径复现失败（{old_stream!r} 里没有幽灵数字），这条断言是空转")

# ─────────────── ③ 计数相等的空档 → 各自 token 的时刻 ───────────────
# 音节时长刻意做成**不相等**（0.20 / 0.28 / 0.32），线性内插不可能恰好等于它们，
# 于是「按位对位」和「内插」在数值上必然分家，断言不会空转。
S3 = "日喝酒郁郁地病着"
toks3 = [("日", 0.00, 0.20), ("喝", 0.20, 0.40), ("酒", 0.40, 0.60),
         ("浴", 0.60, 0.80), ("浴", 0.80, 1.08), ("的", 1.08, 1.40),
         ("病", 1.40, 1.60), ("者", 1.60, 1.80)]
p = write_asr(toks3)
A, ti, toks = asr_char_stream(p)
times, etimes, flags, matched = align(S3, A, ti, toks)
if A != "日喝酒浴浴的病者":
    fails.append(f"③ ASR 流 {A!r} 不符合夹具")
if not all(flags[k] for k in (3, 4, 5)):
    fails.append(f"③ 郁郁地 三格没拿到近音锚点：flags={flags[3:6]}")
for k, want_t, want_e in ((3, 0.60, 0.80), (4, 0.80, 1.08), (5, 1.08, 1.40)):
    if abs(times[k] - want_t) > 1e-6 or abs(etimes[k] - want_e) > 1e-6:
        fails.append(f"③ 第 {k} 格应该是对位 token 的 {want_t}-{want_e}，"
                     f"实得 {times[k]}-{etimes[k]}")
# 对照：线性内插会把这三格算成什么（锚点 0.60 → 1.40 之间按字符数均分）
lo_t, hi_t, n = 0.60, 1.40, 4
interp = [round(lo_t + (hi_t - lo_t) / n * (i + 1), 3) for i in range(3)]
if max(abs(a - b) for a, b in zip(interp, [times[3], times[4], times[5]])) < 0.05:
    fails.append(f"③ 对位值和内插值几乎一样（{interp} vs 实得），这条断言是空转")

# ─────────────── ④ 计数不等：宁可内插，不可编时刻 ───────────────
S4 = "日喝酒郁郁地病着"
toks4 = [("日", 0.00, 0.20), ("喝", 0.20, 0.40), ("酒", 0.40, 0.60),
         ("浴", 0.60, 1.00), ("的", 1.00, 1.40),          # 少听一个音节：3 对 2
         ("病", 1.40, 1.60), ("者", 1.60, 1.80)]
p = write_asr(toks4)
A, ti, toks = asr_char_stream(p)
times, etimes, flags, matched = align(S4, A, ti, toks)
if any(flags[k] for k in (3, 4, 5)):
    fails.append(f"④ 计数不等的空档被按位对位了（flags={flags[3:6]}），门禁会信一个编出来的时刻")
if not (0.60 <= times[3] <= 1.40 and times[3] <= times[4] <= times[5]):
    fails.append(f"④ 内插没落在两锚点之间且单调：{times[3:6]}")

# ─────────── ⑤ 两类空档混在一条流里：各走各的，且逐字符单调 ───────────
# 一个「计数相等」空档紧接一个「计数不等」空档，中间由一个字面命中（丁）隔开。
# 这一组钉的是**边界**：对位只许落在计数相等的那两格上，多听出两个音节（嘶/哑）的
# 那一格必须照旧内插，而且整条流的逐字符时刻不许倒退、命中计数不许被近音灌水。
# 注意「整段对位 / 整段内插」是 by construction 的：gs/ga 按空档整体算，一个字符都
# 不会半对位半内插，所以这里断言的是两段的分界，不是逐字符的挑选。
S5 = "甲乙丙郁郁丁戊庚"
toks5 = [("甲", 0.00, 0.20), ("乙", 0.20, 0.40), ("丙", 0.40, 0.60),
         ("浴", 0.60, 0.70), ("浴", 0.70, 0.80),      # 郁郁 → 2 对 2 → 按位对位
         ("丁", 0.80, 0.90),                          # 字面命中，把两处空档隔开
         ("嘶", 0.90, 1.00), ("哑", 1.00, 3.00),      # 戊 ↔ 嘶哑：1 对 2 → 不许对位
         ("庚", 3.00, 3.20)]
p = write_asr(toks5)
A, ti, toks = asr_char_stream(p)
if A != "甲乙丙浴浴丁嘶哑庚":
    fails.append(f"⑤ ASR 流 {A!r} 不符合夹具（嘶/哑 被 clean 掉了？换掉填充字）")
times, etimes, flags, matched = align(S5, A, ti, toks)
if not (flags[3] and flags[4] and times[3] == 0.60 and etimes[3] == 0.70
        and times[4] == 0.70 and etimes[4] == 0.80):
    fails.append(f"⑤ 郁郁两格没对位到自己 token：{[(times[k], etimes[k], flags[k]) for k in (3, 4)]}")
if not flags[5]:
    fails.append("⑤ 字面命中的「丁」丢了锚点")
if flags[6]:
    fails.append("⑤ 计数不等的空档被按位对位了（戊 ↔ 嘶哑 1 对 2）")
if not (0.90 <= times[6] <= 3.00):
    fails.append(f"⑤ 悬空格的内插没落在两锚点之间：{times[6]}")
seq = [times[k] for k in range(len(S5)) if times[k] is not None]
if any(b < a - 1e-9 for a, b in zip(seq, seq[1:])):
    fails.append(f"⑤ 逐字符时刻倒退：{seq}")
if matched != 5:
    fails.append(f"⑤ matched 应只数字面命中（甲乙丙丁庚）= 5，实得 {matched}："
                 "近音锚点灌进命中数，日志里的「命中 X / Y」就和历史不可比了")

# ─────────────────────── ⑥ 真片：无名的裘德 ───────────────────────
ep = os.path.join(ROOT, "episodes", "无名的裘德")
asr = os.path.join(ep, "audio/asr/body.json")
wav = os.path.join(ep, "audio/body-voiceover.mp3")
if os.path.exists(asr) and os.path.exists(wav):
    rows = read_script_rows(resolve_script_path(ep, "body"))
    bursts, _thr = speech_bursts(wav)
    win, weak, matched, total = audible_windows(rows, bursts, asr)
    for order in (103, 129):
        if order in weak:
            fails.append(f"⑥ 行{order} 仍然锚点薄弱（同音错字没被对位）")
        if order not in win:
            fails.append(f"⑥ 行{order} 拿不到自身可听窗")
        else:
            s0, e0 = win[order]
            share = burst_share(bursts, s0, e0)
            if share < 0.90:
                fails.append(f"⑥ 行{order} 的窗 {s0:.2f}-{e0:.2f} 只有 {share*100:.0f}% "
                             "落在真语音里，对位钉到了静音上")
    # 行 103「郁郁地病着」在 194.25-195.61 这一段（浴/浴/的/病/者 的实测 token 时刻）
    if 103 in win and not (194.0 <= win[103][0] <= 195.2):
        fails.append(f"⑥ 行103 行首 {win[103][0]:.2f} 不在浴字所在的 194.25-195.05 附近")
    if 129 in win and not (244.0 <= win[129][0] <= 245.0):
        fails.append(f"⑥ 行129 行首 {win[129][0]:.2f} 不在终/身所在的 244.41-244.76 附近")
    if matched <= 0:
        fails.append("⑥ matched 掉了，字面命中数不该被这两条改动影响")
else:
    print("⚠️ 缺《无名的裘德》的 asr/配音，⑥ 未跑")

# ─────────── ⑦ 行尾空档（最后一块之后）：按位对位，不是编 0.20s ───────────
# 被撤掉的形状只 `zip(blocks, blocks[1:])`，所以**最后一块之后**那段悬空字符永远进不了
# 对位，落进 `nxt is None` 分支：每个悬空字符都被编成 `te0 .. te0+0.20`——同一个时刻，
# 凭空多给 0.20s 收尾。《挪威的森林》2026-10-10 钩子段就卡在这条分支上（⑩ 是真片对照）。
# 夹具里 定/物/机 三个 token 的时长刻意不相等（0.25 / 0.25 / 0.45），
# 所以「各自 token 的时刻」与「三格共用 0.60-0.80」在数值上必然分家。
S7 = "甲乙丙丁戊己"
toks7 = [("甲", 0.00, 0.20), ("乙", 0.20, 0.40), ("丙", 0.40, 0.60),
         ("定", 0.60, 0.85), ("物", 0.85, 1.10), ("机", 1.10, 1.55)]
p = write_asr(toks7)
A, ti, toks = asr_char_stream(p)
if A != "甲乙丙定物机":
    fails.append(f"⑦ ASR 流 {A!r} 不符合夹具")
times, etimes, flags, matched = align(S7, A, ti, toks)
if not all(flags[k] for k in (3, 4, 5)):
    fails.append(f"⑦ 尾档三格没拿到近音锚点：flags={flags[3:6]}")
for k, want_t, want_e in ((3, 0.60, 0.85), (4, 0.85, 1.10), (5, 1.10, 1.55)):
    if abs(times[k] - want_t) > 1e-6 or abs(etimes[k] - want_e) > 1e-6:
        fails.append(f"⑦ 第 {k} 格应该是对位 token 的 {want_t}-{want_e}，"
                     f"实得 {times[k]}-{etimes[k]}")
# 对照：被撤形状在这一格上会编出什么（te0=0.60，末格 end 只会到 0.80）
WITHDRAWN_TAIL_END = 0.80
if etimes[5] - WITHDRAWN_TAIL_END < 0.40:
    fails.append(f"⑦ 末格收尾 {etimes[5]} 距被撤形状的 {WITHDRAWN_TAIL_END} 不到 0.40s，"
                 "这条断言量不到东西（token 时刻被改得过近了）")
if matched != 3:
    fails.append(f"⑦ matched 应只数字面命中（甲乙丙）= 3，实得 {matched}")
seq7 = [times[k] for k in range(len(S7)) if times[k] is not None]
if any(b < a - 1e-9 for a, b in zip(seq7, seq7[1:])):
    fails.append(f"⑦ 逐字符时刻倒退：{seq7}")

# ─────────── ⑧ 行首空档（第一块之前）：按位对位，不是全体贴第一个锚点 ───────────
# 同一处结构盲角的另一面：`p < 0` 分支把**每一个**悬空的首字符都贴到 anchors[0] 自己的
# 时刻上，也就是整段共用「第一个真命中字」的 from/to——早说的音节被搬到了晚的时刻。
# 键必须是 `pos[off] = off`（首档的 ASR 悬空字符就在 `[0, first.b)`）；写成
# `first.b + off` 会把它们映射到第一个真命中块自己的字符上。探针的第一版正是这么写的，
# 于是它报出的「11 行变窗」含这条错位，那组数字作废（tmp/ab-align-tails-1010.py 头注）。
S8 = "低搭甲乙"
toks8 = [("地", 0.30, 0.55), ("大", 0.55, 0.80), ("甲", 0.80, 1.00), ("乙", 1.00, 1.20)]
p = write_asr(toks8)
A, ti, toks = asr_char_stream(p)
if A != "地大甲乙":
    fails.append(f"⑧ ASR 流 {A!r} 不符合夹具")
times, etimes, flags, matched = align(S8, A, ti, toks)
if not all(flags[k] for k in (0, 1)):
    fails.append(f"⑧ 首档两格没拿到近音锚点：flags={flags[0:2]}")
for k, want_t, want_e in ((0, 0.30, 0.55), (1, 0.55, 0.80)):
    if abs(times[k] - want_t) > 1e-6 or abs(etimes[k] - want_e) > 1e-6:
        fails.append(f"⑧ 第 {k} 格应该是 {want_t}-{want_e}，实得 {times[k]}-{etimes[k]}")
# 对照：被撤形状把两格都贴成 anchors[0]（甲字的 0.80-1.00），两格时刻**相同**且比真语音晚 0.5s
WITHDRAWN_HEAD = (0.80, 1.00)
if abs(times[0] - WITHDRAWN_HEAD[0]) < 0.2 and abs(times[1] - WITHDRAWN_HEAD[0]) < 0.2:
    fails.append(f"⑧ 首档两格仍被贴到同一个时刻 {times[0]}/{times[1]}＝被撤形状")
if not (times[0] < times[1]):
    fails.append(f"⑧ 首档两格没有各自错开：{times[0]}, {times[1]}")
if matched != 2:
    fails.append(f"⑧ matched 应只数字面命中（甲乙）= 2，实得 {matched}")

# ─────────── ⑨ 首尾档计数不等：照旧不许对位（新分支是有条件的，不是默认全锚） ───────────
# 文案尾 4 字、ASR 尾 3 字（多吞一个音节）。若这里也对位，就是拿一个不存在的对应关系
# 编时刻，而门禁会信它。这一组是 ⑦⑧ 的反面对照：同一处代码，条件不成立时必须退回内插，
# flags 全 False。
S9 = "甲乙丙丁戊己庚"
toks9 = [("甲", 0.00, 0.20), ("乙", 0.20, 0.40), ("丙", 0.40, 0.60),
         ("定", 0.60, 1.00), ("物", 1.00, 1.40), ("机", 1.40, 1.80)]
p = write_asr(toks9)
A, ti, toks = asr_char_stream(p)
times, etimes, flags, matched = align(S9, A, ti, toks)
if any(flags[k] for k in (3, 4, 5, 6)):
    fails.append(f"⑨ 尾档计数不等（4 对 3）却被对位了：flags={flags[3:7]}")
if matched != 3:
    fails.append(f"⑨ matched 应只数字面命中 = 3，实得 {matched}")

# ─────────── ⑩ 真片：挪威的森林的钩子尾档把轴撑回配音末尾 ───────────
# 这一组钉的是「本改动到底解锁了什么」：行 2「一响照旧」被听成「一想招救」，
# 尾档 gs == ga == 3。被撤形状编出 8.18s 收尾 → snap ① 的天花板只有 8.78 →
# 末条 8.78 ÷ 9.12 = 0.963 < 0.98 →「时间轴塌陷，拒绝写入」，整期渲不出来。
# 现在末格落在自己 token 的 8.54s（真语音突发到 8.65s），高出被撤形状 0.36s。
ep2 = os.path.join(ROOT, "episodes", "挪威的森林")
asr2 = os.path.join(ep2, "audio/asr/hook.json")
wav2 = os.path.join(ep2, "audio/hook-voiceover.mp3")
if os.path.exists(asr2) and os.path.exists(wav2):
    rows2 = read_script_rows(resolve_script_path(ep2, "hook"))
    bursts2, _thr2 = speech_bursts(wav2)
    win2, weak2, matched2, total2 = audible_windows(rows2, bursts2, asr2)
    last_order = max(win2) if win2 else None
    if last_order is None:
        fails.append("⑩ 挪威的森林钩子段一个自身窗都没有，夹具失效")
    else:
        s0, e0 = win2[last_order]
        WITHDRAWN_ROW2_END = 8.18        # 被撤形状编出来的收尾（te0 0.60 段末 + 0.20）
        if e0 - WITHDRAWN_ROW2_END < 0.30:
            fails.append(f"⑩ 行{last_order} 收尾 {e0:.2f} 没超出被撤形状的 "
                         f"{WITHDRAWN_ROW2_END}，尾档没真的对位")
        last_burst_end = max(b for _, b in bursts2)
        if last_burst_end - e0 > 0.20:
            fails.append(f"⑩ 行{last_order} 窗尾 {e0:.2f} 距最后一个听得见的瞬间 "
                         f"{last_burst_end:.2f} 还差 {last_burst_end - e0:.2f}s，"
                         "末条盖不住自身语音")
    if weak2:
        fails.append(f"⑩ 仍有锚点薄弱行 {weak2}，尾档对位没生效")
else:
    print("⚠️ 缺《挪威的森林》的 hook asr/配音，⑩ 未跑")

print("① 形状 6 个实测形态 新正则全认 / 旧正则全不认")
print("② 幽灵数字：控制 token 不再进字符流，文案数字不会被段界时刻假命中")
print("③ 3 对 3 空档按位对位，且与线性内插数值分家")
print("④ 3 对 2 空档保持内插，flags 全 False")
print("⑤ 混合空档：计数相等段对位 / 多听出的格子内插 / matched 只数字面命中")
print("⑦ 行尾空档按位对位，末格收尾比「编 0.20s」的形状晚 0.40s 以上")
print("⑧ 行首空档按位对位，两格不再共用第一个真命中字的时刻")
print("⑨ 首尾档计数不等（4 对 3）照旧内插——新分支是有条件的，不是默认全锚")
for line in fails:
    print("❌", line)
if fails:
    raise SystemExit(1)
print("✅ 控制 token 剔除 + 计数相等空档按位对位（内部 / 行首 / 行尾三档）：所有方向按预期翻牌，"
      "且每组的「改动前形状」都已在同一份夹具上复现")
