#!/usr/bin/env python3
"""收掉「为了避开闪断而铺进静音」的行尾，让第七道门禁的 T2 不再判空放。

为什么需要它：`audit-caption-axis.py` 的 B 规则拿 −50dB 的 silencedetect 判断「配音还在说」，
而交付门禁 `check-caption-speech-align.py` 的 T2 拿 `lib/caption_speech.py` 的自适应能量突发
判断「听得到的语音」。下一句开口前常有 0.1~0.3s 的呼吸/齿音：在 −50dB 算语音，在自适应门（本集约
−18dBFS）却算静音。审计据此把上一条的尾巴铺到下一条开口前（这是它的设计），T2 看见的是一段没有词的窗，
两道门互相判红。《你的夏天还好吗》2026-09-29：行 54「一二，一二，做伸展跳。」44%、
行 61「有人说，会有人来的。」41%，而审计测得 residual 0 —— 只有 T2 看得见。

做法：只动被 T2 判红（低于 --target）的行，把 end 往回收，收到「窗内语音占比 ≥ --target」为止。
三条硬约束保证不制造新缺陷：
  1) 绝不早于本行自己的语音终点（T1 覆盖不动，词不会被甩在字幕外）；
  2) 绝不短于按语速读完所需（与 audit 同口径：read_floor 0.55、5.2 字/秒），阅读时间优先；
  3) 绝不越过下一条开口前 hold(0.06s)，单调性与「不盖住别人的词」都不破。
占比同时按两份配音（raw 与 story）各测一遍，取**更严的那一份**：门禁写死用 raw，轴却是从 story 推的，
两份能量分布不同 → 自适应阈值不同（本期 −18.4 与 −24.8 dBFS），只看一份就会在交付门反弹。
收完仍不达标 → 本脚本只把两条证人的数字打出来（自适应密度 + 抬在 −35 dBFS 噪声底之上的占比），
并给出 retime-asr-segments.py 的命令行；「是不是真的空放」由交付门禁 T2 判，不在这里下结论
（2026-10-10《美丽新世界》行 52「所以，」：密度 20% 却 67% 在噪声底之上——词被轻声念出来，
自适应阈值把它当静音，门禁的声学在场兜底线不判红，旧话术会让人去修一副没坏的 ASR）。
运行顺序：precise → snap → audit → **本脚本**（必须在审计之后，否则 B 规则会把尾巴再铺回去）。

用法：python3 scripts/trim-t2-leadout.py "<书名>" [--version A] [--apply] [--target 0.55]
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from caption_speech import (above_noise_share, audible_windows, clean, read_body_timings,  # noqa: E402
                            read_script_rows, resolve_script_path, silence_runs, speech_bursts)
from caption_speech import SILENCE_NOISE_DB   # 打印噪声底用 lib 的那个数，不在这里重抄

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = dict(
    min_voiced=0.45,      # 门禁 T2 红线，与 check-caption-speech-align.py 一致
    target=0.55,          # 收到这里才停：渲染器 CAP_VOICED_MIN 0.50 + 两条解码路径 1~2 个点的差
    hold=0.06,            # 两条字幕之间的可感知留白（约 2 帧），与 audit 同值
    chars_per_sec=5.2,    # 与 audit 同口径
    read_floor=0.55,
    min_reclaim=0.08,     # 只收回得来 0.08s 以上的行，免得为一两帧制造无意义的抖动
)


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--segment", default="body", choices=["body", "hook"],
                    help="三段结构的段：body 走 script-body.csv，hook 走 script-hook.csv；默认 body")
    ap.add_argument("--version", default=None)
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), default=v, type=float)
    ap.add_argument("--voice", action="append", default=None,
                    help="配音文件，可重复；默认 raw 与 story 两份都测")
    ap.add_argument("--timings", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    ep = os.path.join(ROOT, "episodes", a.episode)
    brief_path = os.path.join(ep, "brief.json")
    version = a.version or (json.load(open(brief_path, encoding="utf-8")).get("scriptVersion")
                            if os.path.exists(brief_path) else None) or "A"
    seg_base = "hook" if a.segment == "hook" else "body"
    timings_path = a.timings or os.path.join(ep, "audio", f"{seg_base}-timings.json")
    asr_path = os.path.join(ep, "audio", "asr", f"{seg_base}.json")
    script_path = resolve_script_path(ep, a.segment)
    voices = a.voice or [p for p in (os.path.join(ep, "audio", f"{seg_base}-voiceover.mp3"),
                                     os.path.join(ep, "audio", f"{seg_base}-voiceover-story.mp3"))
                         if os.path.exists(p)]
    for p in (timings_path, asr_path, script_path, *voices):
        if not os.path.exists(p):
            raise SystemExit(f"缺文件：{p}")
    if not voices:
        raise SystemExit(f"缺配音：{ep}/audio/{seg_base}-voiceover[-story].mp3")

    rows = read_script_rows(script_path, version)
    chars = {o: len(clean(t)) for o, t in rows}
    text = dict(rows)
    caps, data = read_body_timings(timings_path)
    order_list = [o for o, _ in rows if o in caps]

    burst_sets, thrs = [], []
    for p in voices:
        b, thr = speech_bursts(p)
        burst_sets.append(b)
        thrs.append((os.path.basename(p), thr, len(b)))
    # 逐行「自己的语音窗」用轴的来源那份配音推（本管线是 story 处理件），与 snap/audit 同源
    axis_idx = next((i for i, p in enumerate(voices) if p.endswith("body-voiceover-story.mp3")), 0)
    # 第三条证人（lib 的固定 −35 dBFS 噪声底）和门禁 7 / snap / audit 一起传：那扇窗里
    # 「推窗交叉要不要丢锚点」的判断必须四处同一个答案，否则本脚本收回的行尾会在
    # 交付门又被恢复的自身窗判成「甩掉了自己的词」。
    audible, weak, matched, total = audible_windows(rows, burst_sets[axis_idx], asr_path,
                                                   sil=silence_runs(voices[axis_idx]))
    # 交付门禁 T2 的声学在场兜底线听的是 audio/<segment>-voiceover.mp3（voices[0]）：
    # 「收回词尾仍不达标」这句话只由自适应密度得出，同一扇窗在门禁那边可能根本不判红。
    # 所以把门禁那条证人的数字一并打出来——但不在这里重抄阈值，判不判红归门禁说。
    noise_floor = silence_runs(voices[0])
    duration = float(data.get("duration") or 0.0)

    def share(s, e):
        """两份配音里更严（占比更低）的那一份。"""
        return min(sum(overlap(s, e, b0, b1) for b0, b1 in bs) / max(1e-6, e - s) for bs in burst_sets)

    changed, unresolved = [], []
    for i, order in enumerate(order_list):
        cs, ce = caps[order]
        if order not in audible or i == len(order_list) - 1:
            continue                      # 缺锚点的行时刻不可信；末行留到片尾是豁免
        now = share(cs, ce)
        if now >= a.target:
            continue
        _, e_speech = audible[order]
        nxt = caps[order_list[i + 1]][0] if i + 1 < len(order_list) else duration
        need = max(a.read_floor, chars.get(order, 1) / a.chars_per_sec)
        lo = max(e_speech, cs + need)      # 不早于自己的词、不短于读得完
        hi = min(ce, nxt - a.hold)         # 不越过下一条开口
        best = None
        t = hi
        while t >= lo - 1e-9:
            if share(cs, t) >= a.target:
                best = t
                break
            t = round(t - 0.02, 3)
        if best is None:
            unresolved.append((order, round(now, 3), "词尾处仍不达标", text[order][:24],
                               round(above_noise_share(noise_floor, cs, ce), 3)))
            continue
        if ce - best < a.min_reclaim:
            continue                      # 只差两三帧，不值得动轴
        changed.append((order, ce, round(best, 3), round(now, 3), round(share(cs, best), 3)))
        caps[order] = (cs, round(best, 3))

    print(f"episode={a.episode}  版本={version}  行 {len(rows)}  配音 {duration:.2f}s")
    for name, thr, n in thrs:
        print(f"   VAD {name}: {thr:.1f} dBFS → 突发 {n} 个")
    print(f"   判据：窗内语音 ≥{a.target*100:.0f}%（交付门 {a.min_voiced*100:.0f}%），行尾不早于自己的词尾")
    for order, was, now, s0, s1 in changed:
        print(f"   行{order:>3} 行尾 {was:7.2f} → {now:7.2f}   窗内语音 {s0*100:3.0f}% → {s1*100:3.0f}%  {text[order][:24]}")
    for order, sh, why, t, pres in unresolved:
        print(f"   ⚠️ 行{order} 收到自己词尾仍只有 {sh*100:.0f}%（{why}）："
              f"窗内 {pres*100:.0f}% 抬在 {SILENCE_NOISE_DB:.0f} dBFS 噪声底之上"
              f"（自适应密度与声学在场是两条证人，判不判红以 "
              f"check-caption-speech-align.py 为准）："
              f"python3 scripts/retime-asr-segments.py \"{a.episode}\" --rows {order}   {t}")
    if not changed and not unresolved:
        print("   无需收回：没有 T2 空放的行尾")
        return 0
    if not a.apply:
        print("\n（试算，未写回。加 --apply 生效）")
        return 1 if unresolved else 0

    shutil.copyfile(timings_path, timings_path + ".pre-t2")
    out = json.load(open(timings_path, encoding="utf-8"))
    for c in out["captions"]:
        o = int(c["order"])
        if o in caps:
            c["start"], c["end"] = caps[o]
    al = out.setdefault("alignment", {})
    al["method"] = al.get("method", "") + " + t2-leadout-trim"
    al["t2Trim"] = {"rows": [o for o, *_ in changed], "target": a.target,
                    "vad": {name: round(thr, 1) for name, thr, _ in thrs},
                    "backup": os.path.basename(timings_path) + ".pre-t2"}
    note = (f"scripts/trim-t2-leadout.py 收回 {len(changed)} 行铺进静音的行尾"
            f"（{','.join(map(str, [o for o, *_ in changed])) or '无'}），"
            f"两份配音的窗内语音占比都 ≥{a.target*100:.0f}%；"
            f"词尾之后的阅读留白交给两条字幕之间的真空窗，不落进别人的词。")
    if unresolved:
        note += (f" 仍有 {len(unresolved)} 行收不动"
                 f"（{','.join(map(str, [o for o, *_ in unresolved]))}）："
                 f"自适应密度低于本脚本目标，但窗内抬在 {SILENCE_NOISE_DB:.0f} dBFS 噪声底之上分别为 "
                 f"{','.join(f'{o}:{p*100:.0f}%' for o, _s, _w, _t, p in unresolved)}，"
                 f"是否算空放以交付门禁 T2 的声学在场兜底线为准，不在此处下结论。")
    al["agentReviewNotes"] = (al.get("agentReviewNotes") or "") + " " + note
    with open(timings_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n✅ 已写回 {os.path.relpath(timings_path, ROOT)}（备份 .pre-t2）")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
