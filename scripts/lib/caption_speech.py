#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「这一行被念出来的时间窗」的唯一算法。修时间轴的脚本和验收时间轴的门禁必须共用它，
否则修的人按一套口径吸、验的人按另一套口径判，永远修不到绿。

独立证人里这里占两条：
  · 波形能量 VAD（speech_bursts）—— 只听得到「有没有声音」，不知道说了什么；
  · Whisper token 时刻（asr_char_stream + align）—— 知道说了什么，但时刻不可信
    （无 DTW 时 `t_dtw = -1`，段内时刻是模型自预测并被线性压扁的）。
逐行可听语音窗 = 用字序把行钉到 token，再用波形把钉到的头尾吸到听得见的边界。

第三条独立证人是 silence_runs()：固定 −35 dBFS 噪声底的静音段。以前它只活在
`retime-asr-segments.py` 里给 ASR 时刻打分，2026-10-10 搬进本 lib 当真源，因为门禁 T2 的
兜底线与自身窗恢复都需要一条**不随本期响度漂**的电平证人：自适应突发阈值按本期 p25/p90
定（实测各期落在 −16 ~ −21 dBFS），同一声轻声字在响的期里算语音、在轻的期里算静音，于是
「整窗听不见人声」这句话实际上是由录音有多响决定的；它还给推窗交叉做「是卡在气口里的轻声，
还是压扁出来的假时刻」的分界（见 audible_windows）。

已知不可约的噪声：Whisper 的同音错字（本期「体量→体谅」「叶真→夜珍」「原名→元明」）。
所以 align() 对未命中的字符做**锚点间线性内插**，既不退回首 token，也不跳到下一块的
第一个 token —— 跳块会把行尾虚涨到下一句的词上（实测虚涨 0.53s，把正确的时间轴判成假红）。
"""
import bisect
import csv
import json
import os
import re
import subprocess

import numpy as np

SR = 16000
WIN = 160                        # 10ms 一格
GAP_MERGE = 0.18                 # 词内元音低谷 / 轻声字的假间隙，合并掉
MIN_BURST = 0.15                 # 短于此的「突发」是咔嗒声，不是语音
WEAK_ANCHOR = 0.40               # 该行 ASR 命中字符占比低于此 → 时刻不可信，只标记不判红
MAX_OWN_PAUSE = 1.50             # 本行自己词与词之间允许被当成「表演」的停顿上限
READ_FLOOR = 0.55                # 一闪而过也要能看清：audit 规则 A 与 T2 的富余停留同一值
READ_RATE = 5.2                  # 本系列旁白约 312 字/分钟，逐行可读时长按此折算
# 绝对电平的静音证人：本仓库另一条独立第三方（retime-asr-segments.py 用它给 ASR 时刻打分，
# audit 的头规则用同形状的 −50dB）。它和 speech_bursts 的差别正是这一节口径缺陷的根源——
# 自适应阈值按**本期响度**定（实测 −16 ~ −21 dBFS），轻声字（「所以，」的「以」、「而是：」）
# 被它当成静音；−35 dBFS 是固定噪声底，只回答「这里有没有抬起来的声音」，不随响度漂移。
SILENCE_NOISE_DB = -35.0
SILENCE_MIN_RUN = 0.15
ANCHOR_PAUSE_SHARE = 0.50        # 锚点过半落在一截真停顿里 → 这个时刻是压扁出来的，不是语音


def reading_need(chars, rate=READ_RATE, floor=READ_FLOOR):
    """读完 `chars` 个上屏字需要几秒。规则 A（读不完）和 T2（富余停留算不算空放）共用，
    两边一旦各算一套，就会出现「修轴的按 0.90 铺、门禁的按 0.55 判」那种互相判红。
    rate/floor 让调用方把它自己那份口径参数传进来（audit 的 --chars-per-sec / --read-floor），
    默认值就是这两个常量，所以没有显式改口径的调用方与门禁逐位一致。"""
    return max(floor, (chars or 0) / rate)

# 字幕里不上屏的装饰符号在这里也必须消失，否则 script 与 ASR 的字序对不上
PUNCT = re.compile(r"[，。？！、；：…—·“”‘’\"'《》（）()\[\]\s,.?!;:'\"\\-]+")

# Whisper 会把念出来的数词写成阿拉伯数字（本期《嫌疑人X的献身》2026-10-04：文案
# 「六十八岁」被听成 "68岁"，token "68"@213.77-214.23 里一个字符都对不上文案），
# 于是这一行拿不到自身可听窗 → 锚点薄弱 → T2 退回原始密度判红，而轴上无处可挪：
# 行首只能落在它自己的第一个字上，而那三个字被一个数字 token 吞掉了。
# 折成逐位汉字让**两侧**都落在同一个字母表里：文案写「68」也一样折成「六八」，
# 所以数字对数字的历史期不受影响（照旧逐位相等），数字对汉字的历史期只会多命中。
# 逐位而不是读成「六十八」：十/百/千这类位权字留在文案侧当悬空字符，由 align()
# 在两个真锚点之间内插，窗头窗尾仍然钉在听得见的 六 和 岁 上；而年份 "19"/"28"
# 这种 BPE 切片只有逐位才折得对（一九二八），读成「十九二十八」会把整行钉歪。
DIGIT_CANON = str.maketrans("0123456789０１２３４５６７８９",
                            "零一二三四五六七八九零一二三四五六七八九")

# whisper.cpp 的控制 token：[_TT_数字]（时间戳）/ [_BEG_] / [_EOT_] / [_EP_数字] / [zh]
# 这类「整对括号、括号内没有别的括号」。判据是形状而不是前缀表——[_TT_..] 的后缀形状
# 以后还会加，写死前缀就是下一次「正则漏了 ]」那类静默失效（见 asr_char_stream）。
# 两侧（Python 建窗 / Node 建轴）必须同形状，Node 侧是同名正则 create-body-timings-precise.mjs。
CONTROL_TOKEN = re.compile(r"^\s*\[[^\]]*\]\s*$")


def clean(text):
    return PUNCT.sub("", (text or "").translate(DIGIT_CANON))


def resolve_script_path(episode_dir, segment="body"):
    """三段结构下钩子段走 script-hook.csv、正文走 script-body.csv；历史单段期只有 script-body.csv。

    段内先找语义化文件名，找不到再回落 script-body.csv。找不到才抛错——绝不静默拿错段，
    钩子轴只有 2 行、正文轴 96 行，拿错会让门禁在完全正常的轴上误报漂移。
    """
    candidates = (["script-hook.csv", "script-body.csv"] if segment == "hook"
                  else ["script-body.csv"])
    for name in candidates:
        p = os.path.join(episode_dir, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        f"缺少文案文件：{episode_dir} 下 {' 或 '.join(candidates)}"
    )


def read_script_rows(path, version=None):
    """script CSV → [(order, text)]，按 order 升序。文本真源永远是 CSV，不是 ASR。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if version and r.get("version") and r["version"] != version:
                continue
            rows.append((int(r["order"]), r["text"]))
    rows.sort()
    return rows


def read_body_timings(path):
    """body-timings.json → {order: (start, end)}。"""
    data = json.load(open(path, encoding="utf-8"))
    caps = {}
    for c in data.get("captions") or []:
        caps[int(c["order"])] = (float(c["start"]), float(c["end"]))
    return caps, data


def speech_bursts(path):
    """能量 VAD：阈值自动落在「静音带」和「语音带」之间（p25 与 p90 之间），不写死 dB 数。

    实测本期配音语音 -1~-8 dB、间隙 -40~-180 dB：写死 -35 dBFS 会落到噪声底以下，
    整条波形被判成一整个突发，行边界再也吸不动。
    """
    raw = f"/tmp/caption_speech_vad_{os.getpid()}.raw"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                    "-ac", "1", "-ar", str(SR), "-f", "f32le", raw], check=True)
    x = np.fromfile(raw, dtype="<f4")
    os.remove(raw)
    fps = SR // WIN             # ⚠️ 帧索引换算秒要除帧率(100)而不是采样率(16000)
    # 长度/间隔一律在整数帧上比，只在 return 那一刻换秒。先换秒再比秒会让恰好 0.15s
    # 的真语音下溢成 0.14999999999999858 被 MIN_BURST 当咔嗒声删掉：《一个陌生女人的来信》
    # 行 28「不断等待。」的 82.45-82.60 那 15 帧就是这么丢的，该行自身可听窗短了 0.38s、
    # 窗内密度 31%，门禁 7 判红而轴上无处可挪（0.9s 阅读下限已吃掉余量）。
    pre_frames = int(round(0.05 * fps))       # 预过滤：0.05s = 5 帧
    gap_frames = int(round(GAP_MERGE * fps))  # 合并：0.18s = 18 帧
    min_frames = int(round(MIN_BURST * fps))  # 保留：0.15s = 15 帧
    n = len(x) // WIN
    db = 20 * np.log10(np.maximum(
        np.sqrt((x[: n * WIN].reshape(n, WIN) ** 2).mean(axis=1)), 1e-10))
    floor, peak = np.percentile(db, 25), np.percentile(db, 90)
    thr = floor + 0.55 * (peak - floor)
    v = (db > thr).astype(np.int8)
    d = np.diff(v)
    st, en = np.flatnonzero(d == 1) + 1, np.flatnonzero(d == -1) + 1
    if v[0] == 1:
        st = np.r_[0, st]
    if v[-1] == 1:
        en = np.r_[en, n]
    segs = [(int(s), int(e)) for s, e in zip(st, en) if e - s >= pre_frames]
    merged = []
    for s, e in segs:
        if merged and s - merged[-1][1] < gap_frames:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(a / fps, b / fps) for a, b in merged if b - a >= min_frames], float(thr)


def silence_runs(path, noise_db=SILENCE_NOISE_DB, min_run=SILENCE_MIN_RUN):
    """波形里 ≥min_run 秒的**绝对静音**区间 [(start, end)]（秒）。裁判只用波形，不用任何文本。

    与 speech_bursts 是两条互不替代的证人：突发阈值按本期响度自适应（p25+0.55·(p90−p25)，
    实测各期落在 −16 ~ −21 dBFS），回答「这段算不算说话」；本函数按固定噪声底 −35 dBFS，
    回答「这里有没有抬起来的声音」。轻声字在第一条下是静音、在第二条下不是 —— T2 的兜底线
    和自身窗恢复都必须听第二条的，否则「整窗听不见人声」这句话实际上是被本期响度决定的。
    真源只在这里：scripts/retime-asr-segments.py 的 silences() 就是调它，别再抄一份 ffmpeg
    解析（同一形状在 4 个 per-episode 兜底渲染器里各写了一遍，那些是历史期，不接门禁判决）。
    """
    res = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                          f"silencedetect=noise={noise_db}dB:d={min_run}", "-f", "null", "-"],
                         capture_output=True, text=True)
    times = [float(m.group(1)) for m in
             re.finditer(r"silence_(?:start|end):\s*([0-9.]+)", res.stderr)]
    return [(times[i], times[i + 1]) for i in range(0, len(times) - 1, 2)]


def silence_share(runs, a, b):
    """[a,b] 里落在静音段（多段取并集，重叠不重复计）的占比。b<=a 时返回 0（空窗不谈静音）。"""
    if b <= a or not runs:
        return 0.0
    merged = []
    for s, e in sorted((max(a, s0), min(b, e1)) for s0, e1 in runs if e1 > a and s0 < b):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return sum(e - s for s, e in merged) / (b - a)


def above_noise_share(runs, a, b):
    """窗内「电平抬到噪声底之上」的占比 = 1 − silence_share。声学在场量的就是它。"""
    return 1.0 - silence_share(runs, a, b)


def burst_share(bursts, a, b):
    """[a,b] 里被可听语音覆盖的比例 —— 判「这段空隙是不是真的在说话」用的唯一口径。"""
    if b <= a:
        return 1.0
    return sum(max(0.0, min(b, b1) - max(a, b0)) for b0, b1 in bursts) / (b - a)


def voiced_share(bursts, cs, ce, own=None, max_pause=MAX_OWN_PAUSE, readable=None):
    """字幕窗 [cs,ce] 里「看得见这一行在被念出来」的占比：门禁 T2 与修轴的手共用。

    只把**落在本行自身可听窗之外**的静音计成空放。窗内静音如果夹在本行自己的两个词
    之间、且短于 max_pause，那是这一行的朗读节奏，不是空放：《奥兰多》行 42
    「吹着真相，真相，真相。」八次音节摊在 92.14–95.77 三整秒里，两次重复之间各留
    1.05 / 1.21 秒的戏剧性停顿，按原始密度算只有 35%，可这行字幕无处可挪 ——
    T1 要求它盖住 92.14–95.77 整段，收到 95.87 也只有 37%。一条要求「窗内语音 ≥45%」
    的门禁在这里等于要求旁白不许停顿，所以判红的是门禁的口径，不是时间轴。
    反过来，行 29「她没跟。」尾巴多挂的 0.67 秒是本行说完之后的气口，落在这行自己的
    可听窗之外，照旧计成空放。

    own=None（该行锚点薄弱、钉不出自身窗）时退回原始密度，宁可严。
    max_pause 给句内停顿设上限：更长的空洞说明字序把两行钉到了一起，那是配错，
    由 T1 和 weak 标记去管，不该在这里被豁免。

    readable 给这一行**读完所需**的秒数（同一口径：max(0.55, 字数 ÷ 5.2)，即
    audit-caption-axis.py 规则 A 的 need）。窗内落在真空静上的富余停留，只要总长不超过
    `readable − 本行在自己可听窗里的语音时长`，就不计空放：一行只有两个字、被念出来
    0.14 秒，可它必须在上屏待满 0.55 秒才读得完，那 0.41 秒是阅读时间，不是空放。
    不给 readable 时行为与旧口径逐位相同，所以没接上的调用方不受影响。
    《人性的枷锁》2026-10-04 行 72「而是：」：窗 197.01–197.91（0.90s 地板）里自己的
    语音只有 197.68–197.82，原始密度 26%；行尾被下一行的行首 197.95 顶死、行首又只能
    往回收进 196.97–197.68 那段停顿，任何轴移动都追不上 45%，判红的是口径不是轴。
    兜底线不受影响：`burst_share` 那份原始密度仍然要求 ≥ --min-raw，一条从头到尾听不见
    人声的字幕照旧红。
    """
    if ce <= cs:
        return 0.0
    w = ce - cs
    pts = {cs, ce}
    for b0, b1 in bursts:
        for t in (b0, b1):
            if cs < t < ce:
                pts.add(t)
    if own:
        for t in own:
            if cs < t < ce:
                pts.add(t)
    ordered = sorted(pts)
    silent = 0.0
    for a, b in zip(ordered, ordered[1:]):
        mid = (a + b) / 2
        if any(x <= mid <= y for x, y in bursts):
            continue                            # 这段有语音
        if own and own[0] <= mid <= own[1] and b - a <= max_pause:
            continue                            # 本行自己的句内停顿
        silent += b - a
    if readable and own:                          # 钉不出自身窗的行不配这份信任（宁可严）
        own_in = max(0.0, min(ce, own[1]) - max(cs, own[0]))
        silent = max(0.0, silent - max(0.0, float(readable) - own_in))
    return 1.0 - silent / w


def asr_char_stream(path):
    """whisper.cpp json → (拼接字符流, 每个字符的 token 下标, [(text, from, to)])。

    控制 token 必须整族剔除：实测 60 份 body.json 里有 [_TT_数字] 2636 个、
    [_BEG_] 510 个、[_EOT_] 10 个。本函数以前用 `^\\[[_A-Z]+\\d*$` 过滤，正则里**漏了
    闭合的 `]`**，于是 `[` 一开头就永远匹配不上，一个都没被剔掉。clean() 会剥掉
    `[]-` 但保留下划线和字母，并把数字折成汉字，所以 [_TT_628] 实际进了
    `_TT_六二八` 七个幽灵字符。两个后果：① ASR 流被灌水（《无名的裘德》2215 字符
    vs 文案 1238），把两块真命中的「空档」拉长到计数不等，行 103/129 这类同音错字行
    就再也对不上位；② 折出来的 `_TT_六二八` 和文案里真念出来的数字（同样折成汉字）
    是同一串字符，能抢走一次假命中。所以按「任何成对括号的整 token」剔，不认前缀形状。
    """
    d = json.load(open(path, encoding="utf-8"))
    chars, toks = [], []
    for seg in d.get("transcription", []):
        for t in seg.get("tokens", []):
            txt = str(t.get("text") or "")
            if not txt or CONTROL_TOKEN.match(txt):
                continue
            off = t.get("offsets") or {}
            f0 = off.get("from")
            f1 = off.get("to", f0)
            if f0 is None:
                continue
            ti = len(toks)
            toks.append((txt, f0 / 1000.0, f1 / 1000.0))
            for ch in clean(txt):
                chars.append((ch, ti))
    return "".join(c for c, _ in chars), [ti for _, ti in chars], toks


def align(script_concat, asr_concat, asr_tok_idx, toks):
    """单调对齐 script 每个字符到 token 时刻。

    返回 (times, etimes, flags, matched)：times[k] = 第 k 个 script 字符「开口」的秒数，
    etimes[k] = 它收尾的秒数，flags[k] = True 表示这个位置的时刻来自真正的 ASR token
    （字面命中，或下面「计数相等的空档按位对位」的近音命中），不是内插的。
    matched 仍然只数字面命中的字符数，所以日志里的「命中 X / Y」可比历史。
    """
    from difflib import SequenceMatcher

    sm = SequenceMatcher(None, script_concat, asr_concat, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return ([None] * len(script_concat), [None] * len(script_concat),
                [False] * len(script_concat), 0)

    # 计数相等的空档 → 按位对位。两块真命中之间，文案侧缺 gs 个字符、ASR 侧缺 ga 个，
    # gs == ga 说明「听见了这么多音节，只是字写错」：《无名的裘德》2026-10-07 实测逐格
    # 证据（tmp/probe-ladder-noop.py ②）—— 行 103「郁郁地病着」被听成「浴浴的病者」
    # （郁郁地↔浴浴的 3 对 3、病字面命中、着↔者 1 对 1），行 129「钟声很响」被听成
    # 「终身很想」（钟声↔终身 2 对 2、很字面命中、响↔想 1 对 1）。改之前这两行的字面命中
    # 各只有 1 个字符（病 / 很），命中率 1/5 和 1/4 都低于 WEAK_ANCHOR → 锚点薄弱 →
    # 门禁 7 对它弃权，修轴的规则 D2 也看不见它的自身窗，于是字幕错多久都没人判。
    # 第 i 个缺口字符取第 i 个缺口 token 自己的 from/to，这比「两个真锚点之间线性内插」
    # 更贴近听得见的东西：内插假设每个音节等长，而同音错字恰恰说明音节是逐个存在的。
    # 数量不等（漏听 / 多听 / 吞字）一律不猜，照旧走内插 —— 宁可内插不可编时刻。
    # 判定按**空档整体**，不逐字符挑：一个空档要么全部对位、要么全部内插（实测 136 期
    # 「同段既对位又内插」0 处）。这也是下面那条梯级合并今天必然是 no-op 的原因。
    # 键的起点是 b0.a + b0.size（真命中块**之后**的第一个字符），不是 b0.a：
    # 写成 b0.a 会把时刻盖到上一个真命中块自己的字符上，行 103 那种「本该新锚定」的
    # 行一个都没得到，反而把邻居的 token 时刻换掉。第一版探针和这里的初稿都踩过，
    # 于是全库 A/B 报出「46 行获得锚点」——那是被污染的对照，不是本改动的效果面。
    pos = {}
    for b0, b1 in zip(blocks, blocks[1:]):
        gs = b1.a - (b0.a + b0.size)
        ga = b1.b - (b0.b + b0.size)
        if gs > 0 and gs == ga:
            for off in range(gs):
                pos[b0.a + b0.size + off] = b0.b + b0.size + off

    # 同一条规则也必须管**首尾两档空档**，因为 `zip(blocks, blocks[1:])` 在结构上看不见它们：
    # 第一块之前和最后一块之后各有一段悬空字符，它们不是「两块之间」，于是落到下面两条
    # 内插兜底分支——那两条分支不是内插，是**编时刻**：行首档把每个悬空字符都贴到第一个锚点
    # 自己的时刻（`p < 0`，所以整段共用一个 from/to），行尾档给每个悬空字符编出 `te0 + 0.20`
    # （`nxt is None`，凭空多给 0.20s 收尾）。《挪威的森林》2026-10-10 钩子段就是后者：
    # 行 2「一响照旧」被听成「一想招救」，最后一块之后文案侧缺 3 字、ASR 侧缺 3 字
    # （gs == ga == 3，正是「听见了这么多音节、只是字写错」），却因它是尾档而不进对位，
    # 被编成 8.18s 收尾，而这一行的真实语音到 8.65s 才停。snap ① 的天花板因此只有 8.78s，
    # 末条 8.78 ÷ 9.12 = 0.963 < 0.98 → 「时间轴塌陷，拒绝写入」，整期卡在轴上。
    # 同一期 面纱 尾部静音 0.556s 却过 0.9927，差别只在 Whisper 末 token 的时刻碰巧贴到了
    # 突发末尾——0.98 是代理量，本体是「末条有没有盖住最后一个听得见的瞬间」。
    # 条件与内部空档**完全同一条**（gs == ga > 0，数量不等一律不猜），不新增阈值：
    # 首档的 ASR 侧从 0 数到 first.b，尾档数到 len(asr_concat)，都是「同一句话里被听成
    # 别字的那几个音节」，没有理由因为位置不同就退回编时刻。
    # 全库对照（tmp/ab-align-tails-1010.py，2026-10-10，246 个段）：**只**加首尾两档之后
    # 与生产逐字符比较——含等计数首尾空档 52 段、其余 194 段逐字符全等、「不该动却动了」0、
    # 行窗变动 36 段、新得锚点 7 行 / 失锚点 0 行、weak 变多的期 0 个、末条比例翻绿 1 期
    # （挪威的森林 hook 0.9627→1.0）、绿翻红 0、出错 0 段；变窗行的 Δstart 无一为正
    # （30 行中位 −0.155，只有更早没有更晚，符合「行首只许往前」）。
    # 上一版探针 tmp/ab-align-tails.py 报的「238 段 / 11 行 / 46 行获得锚点」已撤回：
    # 它的首档映射写成 `first.b + off`（把悬空字符盖到第一个**已命中**块上），对照本身被污染。
    first = blocks[0]
    gs, ga = first.a, first.b
    if gs > 0 and gs == ga:
        for off in range(gs):
            pos[off] = off

    last = blocks[-1]
    gs = len(script_concat) - (last.a + last.size)
    ga = len(asr_concat) - (last.b + last.size)
    if gs > 0 and gs == ga:
        for off in range(gs):
            pos[last.a + last.size + off] = last.b + last.size + off

    anchors = []
    for b in blocks:
        for off in range(b.size):
            ti = asr_tok_idx[b.b + off]
            anchors.append((b.a + off, toks[ti][1], toks[ti][2]))
    times = [None] * len(script_concat)
    etimes = [None] * len(script_concat)
    flags = [False] * len(script_concat)
    for k, ai in pos.items():
        ti = asr_tok_idx[ai]
        times[k], etimes[k], flags[k] = toks[ti][1], toks[ti][2], True
    # 按位对位的位置也进内插的锚点梯级。**今天它是 no-op，留着是因为它只在一种情况下
    # 有用**：空档按整体判定（要么整段对位要么整段内插），所以一个内插字符的「前一锚点」
    # 永远是隔开两段空档的那个字面块，永远看不见夹在中间的近音锚点，均分不可能越过它。
    # 实测 136 期：把这一行换成 `sorted(anchors)` 之后逐字符时刻/收尾/flags 零差异，
    # 「同一段既对位又内插」的空档 0 处（tmp/probe-ladder-noop.py ①）。
    # 那它为什么写在这里：第一版的键从 b0.a 起算（off-by-one），对位段和内插段能在同一个
    # 空档里混起来，那时的形状确实是「近音锚点被后面的均分越过、逐字符时刻倒退」；这一行
    # 是那种形状重新出现时的地板。将来若把对位改成逐字符挑选，它就是单调性的唯一保证。
    # 单调性的现状也量过（tmp/ab-monotone.py，133 期）：逐字符倒退 改前 115 → 改后 116，
    # 「行首晚于行尾」0 → 0 —— 倒退来自 ASR token 自身的时刻，不是对齐造的。
    # 顺带一个真收益：控制 token 的时间戳本身就是倒数的东西，剔掉以后 token 起始时刻倒退
    # 的个数 165 → 26，即 139 处倒退回属于 [_TT_..] 这类段界假 token。
    anchors = sorted(anchors + [(k, times[k], etimes[k]) for k in pos])
    keys = [a[0] for a in anchors]
    for k in range(len(script_concat)):
        if k in pos:                       # 已按位对位，不再内插
            continue
        p = bisect.bisect_right(keys, k) - 1
        if p < 0:                          # 开头就被漏听：贴到第一个锚点
            times[k], etimes[k] = anchors[0][1], anchors[0][2]
            continue
        a0, t0, te0 = anchors[p]
        if a0 == k:                        # 真命中
            times[k], etimes[k] = t0, te0
            flags[k] = True
            continue
        nxt = anchors[p + 1] if p + 1 < len(anchors) else None
        if nxt is None:                    # 结尾就被漏听：贴在本锚点末尾
            times[k], etimes[k] = te0, te0 + 0.20
            continue
        lo_t, hi_t = te0, nxt[1]           # 悬空字符：锚点末尾 → 下一锚点开口，线性内插
        if hi_t <= lo_t:
            times[k], etimes[k] = lo_t, hi_t
            continue
        step = (hi_t - lo_t) / max(1, nxt[0] - a0)
        times[k] = round(lo_t + step * (k - a0), 3)
        etimes[k] = round(min(hi_t, lo_t + step * (k - a0 + 1)), 3)
    return times, etimes, flags, sum(b.size for b in blocks)


def enclosing_burst(bursts, starts, t):
    i = bisect.bisect_right(starts, t) - 1
    if i >= 0 and bursts[i][0] <= t <= bursts[i][1]:
        return bursts[i]
    return None


def in_gap(bursts, starts, t, slack=0.0):
    """t 是否落在静音间隙里（行边界只可能落在间隙里）。slack 给一点余量。"""
    i = bisect.bisect_right(starts, t) - 1
    if i >= 0 and bursts[i][0] - slack <= t <= bursts[i][1] + slack:
        return False
    return True


def audible_windows(rows, bursts, asr_path, sil=None):
    """逐行算「这一行被念出来的窗」：{order: (start, end)}，以及锚点薄弱的行号。

    只信字序 + 波形，不信任何上游时间轴。缺锚点的行（命中 <40%）不进结果——
    它的时刻本身不可信，门禁对它只标记不判红。

    sil=静音段（`silence_runs` 的输出，同一条配音算出来的）时，被推窗交叉丢弃的锚点
    有机会恢复。**为什么要恢复**：头推进到下一个突发起点、尾回退到上一个突发终点，两个动作
    撞上（e0 ≤ s0）就整行没有自身窗，T2 只能退回自适应阈值的原始密度。可这一步里「卡在两个
    突发之间」的锚点有两类，混在一起就误杀轻声字：
      · 真音节卡在气口里 —— 《美丽新世界》2026-10-10 行 86「所以，」锚 217.44-217.54，
        突发 (215.60,216.81)/(217.60,218.08) 之间，静音段只到 217.41 为止：这段电平抬在
        −35 dBFS 之上、掉在本期 −16.4 dBFS 突发阈值之下，正是「以」的轻声。字幕 216.85-217.64
        把它整段盖住。《道林·格雷的画像》行 82「而是：」同一形状（锚 250.19-250.54，
        静音段 249.76-250.30 只吃掉前 0.11s）。丢掉这两个窗，门禁就把「听得到的轻声」判成空放。
      · 压扁出来的假时刻 —— 《汤姆叔叔的小屋》行 34「听人说，」锚 64.05-64.66 里 89% 落在
        静音段 64.12-64.75 之内，而这一行的字幕在 62.37-63.01（正压在突发 61.60-64.01 上）：
        这不是轻声，是 Whisper 把这一句钉晚了一整秒。恢复它等于凭空造出一条 T1「提前消失」红，
        把该走 ASR 修复路线（retime-asr-segments.py）的缺陷伪装成轴的缺陷。
    所以分界用形状而不是距离：**锚点过半落在一截真停顿里就不恢复**（silence_share ≥
    ANCHOR_PAUSE_SHARE），过半是静音的时刻不可信；只卡着一点边的锚点是音节，恢复。
    恢复的是**推进/回退之前**的锚点（仍已让位给下一行的开口），所以它天然是个保守量。

    sil=None 时行为与旧口径逐位相同（未接上的调用方不受影响）。
    """
    asr_concat, asr_tok_idx, toks = asr_char_stream(asr_path)
    schars, span = [], {}
    for order, text in rows:
        c = clean(text)
        span[order] = (len(schars), len(schars) + len(c))
        schars.extend(c)
    times, etimes, flags, matched = align("".join(schars), asr_concat, asr_tok_idx, toks)
    starts = [b[0] for b in bursts]

    anchors, weak = {}, []
    for order, text in rows:
        lo, hi = span[order]
        if hi == lo:
            weak.append(order)
            continue
        n_hit = sum(1 for k in range(lo, hi) if flags[k])
        if n_hit == 0 or n_hit / (hi - lo) < WEAK_ANCHOR:
            weak.append(order)
            continue
        anchors[order] = (times[lo], etimes[hi - 1])

    orders = [o for o, _ in rows if o in anchors]
    out = {}
    for k, order in enumerate(orders):
        s0, e0 = anchors[order]
        if k + 1 < len(orders):            # 行尾不得吃进下一行的开口
            e0 = min(e0, anchors[orders[k + 1]][0])
        pinned = (s0, e0)                 # 推进/回退之前、已让位给下一行的锚点
        # 无 DTW 的 token 时刻会被压扁，落在间隙里的锚点不是「听得见的语音」：
        # 一律推进/回退到间隙另一侧的突发边界，不设距离上限（本期 2 行「一个」被标在
        # 1.35s，真实突发起点 2.68s，差 1.33s —— 带距离上限就会把正确的时间轴判成假红）。
        if in_gap(bursts, starts, s0):
            nxt = next((b[0] for b in bursts if b[0] > s0), None)
            if nxt is not None:
                s0 = nxt
        if in_gap(bursts, starts, e0):
            prv = next((b[1] for b in reversed(bursts) if b[1] < e0), None)
            if prv is not None:
                e0 = prv
        if e0 > s0:
            out[order] = (round(s0, 3), round(e0, 3))
        elif sil is not None and pinned[1] > pinned[0] \
                and silence_share(sil, *pinned) < ANCHOR_PAUSE_SHARE:
            out[order] = (round(pinned[0], 3), round(pinned[1], 3))
    return out, weak, matched, len("".join(schars))
