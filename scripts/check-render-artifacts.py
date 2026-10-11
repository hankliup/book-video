#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 HyperFrames 渲出的段视频里的单帧合成伪影（部分栅格化露出下层）。

伪影形状（2026-10-05《少年Pi的奇幻漂流》钩子段实测，用户报「钩子视频闪现」）：
某个水平区带的均亮度**单帧脉冲**——±10 级以上、下一帧立刻回落。机制是 headless Chrome
在合成层部分栅格化未完成的那一帧透出了下层。2026-10-07《一颗简单的心》钩子把形状量清了：
第 203 帧底带 y 880..959 由照片的 37→27 渐变塌成 15..19 的**平场**（照片那块瓦片根本没画），
而那一帧没有任何设计内事件——它不是「两个画面之间的一记跳变」，是「一层没栅格化完」。

用法：
  python3 scripts/check-render-artifacts.py <mp4> --composition <index.html|工程目录>
  python3 scripts/check-render-artifacts.py <mp4>                    # 不判豁免：每个脉冲都算伪影
  python3 scripts/check-render-artifacts.py <mp4> --baseline <mp4>   # 只取证，不豁免
  python3 scripts/check-render-artifacts.py <mp4> --json
退出码：0 = 干净（或剩下的脉冲都落在组合声明的事件窗里）；1 = 有伪影；2 = 用法/读取错误。

豁免的判据是**组合里声明过的单帧刀口**，不是「另一次渲染也有」：
- 旧实现把《少年Pi》那条钩子的四组转场帧号钉死成共享门禁的豁免表，换一条钩子时长就两头
  判错——设计事件会误判成伪影，真伪影会漏判。
- 中间一版改成「拿同一工程另一次渲染当 baseline，能复现就算设计内」。2026-10-07 证伪：
  这一记底带塌陷在连续两次渲染里落在**同一帧同号**，被 baseline 放成了设计内，于是「四次
  都过扫描」的成片仍然带着用户报的那个闪现。可复现既不是设计内的充分条件，也不是伪影的
  必要条件——同一份组成连渲三次实测是「有 / 干净 / 有」，栅格化只是让某一帧特别容易没画完。
- 第三版读组合的全部事件时刻并按时长开窗，仍然漏：字幕**出场**是一条 0.16s 的 `fromTo` 渐变，
  渐变在帧序列上是单调的，它造不出「跳一帧又立刻跳回」，可它把 r3 那记 t=5.03s 的底带塌陷买成了
  「设计内」（r1/r2 同一帧干净）。
- 现在的唯一来源是组合自己，且只认**刀口**：`create-episode-preview.mjs` 写进 index.html 的
  GSAP 调用里，`tl.set`、`tl.call`、声明时长 ≤ 一帧半的 `to/from/fromTo`（片头 `.template-blackout`
  的 0.04s 砸黑一拍），以及 `revealCaption` 的收场时刻（函数体里那条 `tl.set`）。落在这些刀口
  ±KNIFE_PAD 之外的单帧脉冲，无论能不能复现，都是伪影。渐变不豁免——包括照片推镜和慢溶解，
  它们本来也不该出现在脉冲形状的判据里。
baseline 仍然跑，但降级成**取证**：同区带同号且在 ±FRAME_TOL 帧内 → 打印「上一轮同一帧也有」。
共享片头因此不需要特例：它的砸黑每次渲都在同一帧，而那条 `tl.to(..., duration: 0.04)` 就在
index.html 里；实测交付的 intro.mp4 在**不传组成**的严格模式下也是 0 脉冲，所以这条豁免宽松与
否都不会把片头渲成红。
"""
import json
import os
import re
import subprocess
import sys

import numpy as np

ZONES = {"top(0-224)": (0, 224), "mid(224-660)": (224, 660),
         "cap(660-834)": (660, 834), "bot(834-960)": (834, 960)}
PULSE_MIN = 10.0      # 单帧跳变的最低灰度级
KNIFE_PAD = 0.10      # 刀口时刻两侧的取整余量（≈3 帧：fps 重采样与栅格化都吃不满一帧）
KNIFE_SPAN_MAX = 0.05  # 自己在一两帧内完成的改动才算刀口；比这更长的是渐变，不是这一帧的一刀
FRAME_TOL = 1         # baseline 匹配允许的帧位差

CALL_RE = re.compile(
    r"(?:\btl\b|\btimeline\b|_tl)\s*\.\s*(?:set|to|from|fromTo|call|add|label|insert)\s*\("
    r"|\b(?:revealCaption|addCaption)\s*\(")


def zone_means(path):
    """按区带返回每帧平均亮度（30fps、720x960、灰度）。"""
    if not os.path.exists(path):
        print(f"文件不存在：{path}")
        raise SystemExit(2)
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path,
                        "-vf", "fps=30,scale=720:960,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        print(f"读不出像素：{path}\n   {r.stderr.decode('utf-8', 'ignore').strip()[:200]}")
        raise SystemExit(2)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(a) // (720 * 960)
    if n == 0:
        print(f"解码到 0 帧：{path}")
        raise SystemExit(2)
    a = a[:n * 720 * 960].reshape(n, 960, 720).astype(float)
    return {name: a[:, lo:hi, :].mean(axis=(1, 2)) for name, (lo, hi) in ZONES.items()}, n


def pulses(means):
    """单帧脉冲＝这一帧跳变 ≥PULSE_MIN 且下一帧立刻反向跳回同样量级。"""
    out = []
    for name, m in means.items():
        d = np.diff(m)
        for i in range(len(d) - 1):
            if abs(d[i]) >= PULSE_MIN and abs(d[i + 1]) >= PULSE_MIN and d[i] * d[i + 1] < 0:
                out.append({"zone": name, "frame": i, "t": round(i / 30.0, 2),
                            "from": round(float(m[i]), 1), "to": round(float(m[i + 1]), 1),
                            "step": round(float(d[i]), 1)})
    return out


def _split_args(text):
    """按顶层逗号切分一条调用的实参，跳过字符串与 {} () [] 内部。"""
    args, depth, buf, quote = [], 0, "", None
    for ch in text:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if buf.strip():
        args.append(buf.strip())
    return args


def _find_calls(text):
    """yield (调用名, 实参列表)。从命中处向后配平括号，支持跨行调用。"""
    for m in CALL_RE.finditer(text):
        i, depth, buf, quote = m.end(), 1, "", None
        while i < len(text) and depth:
            ch = text[i]
            if quote:
                buf += ch
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf += ch
            elif ch in "([{":
                depth += 1
                buf += ch
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    break
                buf += ch
            else:
                buf += ch
            i += 1
        head = m.group(0).strip().rstrip("(").split(".")[-1]
        yield head, _split_args(buf)


def _num(token):
    t = (token or "").strip()
    return float(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def design_events(html_path):
    """读出组合**声明过的单帧刀口**，返回 [(时刻秒, 刀口自身时长秒)]。

    判据是这一条：**只有「在一帧之内完成的改动」才可能造出「跳一级、下一帧立刻跳回」**。
    渐变（淡入、推镜、溶解）在解码帧序列上是单调的，它给不出回头，所以渐变一律不豁免。
    认识的刀口只有三种，全部读 GSAP 调用的**位置参数**（最后一个实参）：
    - `tl.set(sel, {opacity: 0, y: -10}, 2.31)` → 2.31s 一帧消失；
    - `tl.to(".template-blackout", {opacity: 1, duration: 0.04}, 2.13)` → 声明时长 ≤ 一帧半，
      共享片头那记砸黑一拍就是它，砸完下一帧就回来，形状与伪影相同，必须豁免；
    - `tl.call(fn, [], t)` → 回调里写的一切都是那一帧生效的。
    `revealCaption(sel, start, hold)` 的 **收场**（start+hold，函数体里那条 `tl.set`）是刀口，
    而它的**出场**是 0.16s 的 `fromTo` 渐变——出场不给窗。上一版给了，于是 2026-10-07《一颗
    简单的心》r3 那次渲染底带第 151 帧（t=5.03s，53.0→40.6，而 r1/r2 同一帧干净）被 4.69s 的
    字幕淡入买成了「设计内」：一记不可复现、也不可能是渐变造成的塌陷就这么放过去了。
    花括号里的样式数值（scale/opacity/x/y/像素）是位移量不是时刻，`<video data-start>` 这类
    标签属性也不读；更早一版按行读数字，把 revealCaption 的 hold 当成了时刻，1.74s 凭空多出一个
    窗，放过了第 49 帧底带那一记真伪影——这就是只认位置参数的原因。
    变量型位置参数（`startK`、`start + hold`）读不出具体秒，跳过：生成器在需要判决的位置都另有
    字面量调用点（revealCaption 的调用行就是），不靠猜。没写 `duration` 的 `to/from/fromTo`
    按 GSAP 默认 0.5s 算，也就是渐变，同样不豁免。
    """
    text = open(html_path, encoding="utf-8").read()
    events = []
    for name, args in _find_calls(text):
        dur, has_dur = 0.0, False
        for a in args:
            for m in re.finditer(r"duration\s*:\s*([0-9]*\.?[0-9]+)", a):
                dur, has_dur = max(dur, float(m.group(1))), True
        if name in ("revealCaption", "addCaption"):
            if len(args) >= 3:
                start, hold = _num(args[1]), _num(args[2])
                if start is not None and hold is not None:
                    events.append((start + hold, 0.0))      # 收场那一刀；出场是渐变，不给窗
            continue
        if not args:
            continue
        pos = _num(args[-1])
        if pos is None:
            continue
        if name in ("set", "call"):
            events.append((pos, 0.0))
        elif name in ("to", "from", "fromTo") and has_dur and dur <= KNIFE_SPAN_MAX:
            events.append((pos, dur))                      # 一两帧完成的：砸黑一拍
    return sorted(events)


def near_design(t, events):
    for center, span in events:
        if center - KNIFE_PAD <= t <= center + span + KNIFE_PAD:
            return center, span
    return None


def matched_in_baseline(pulse, baseline):
    """同一区带、±FRAME_TOL 帧内、跳变方向一致 → 这一记在两次渲染里都出现（取证用）。"""
    for b in baseline:
        if b["zone"] != pulse["zone"]:
            continue
        if abs(b["frame"] - pulse["frame"]) > FRAME_TOL:
            continue
        if b["step"] * pulse["step"] > 0:
            return b
    return None


def resolve_index(arg):
    if os.path.isdir(arg):
        for cand in ("index.html", os.path.join("hook", "index.html"),
                     os.path.join("intro", "index.html")):
            p = os.path.join(arg, cand)
            if os.path.exists(p):
                return p
        print(f"目录里找不到 index.html：{arg}")
        raise SystemExit(2)
    if not os.path.exists(arg):
        print(f"组合文件不存在：{arg}")
        raise SystemExit(2)
    return arg


def main(argv):
    def opt(name):
        if name in argv:
            i = argv.index(name)
            if i + 1 >= len(argv):
                print(f"{name} 需要一个参数")
                return None, False
            return argv[i + 1], True
        return None, True

    comp_arg, ok_c = opt("--composition")
    baseline_arg, ok_b = opt("--baseline")
    if not ok_c or not ok_b:
        return 2
    as_json = "--json" in argv
    legacy_hook = "--hook" in argv      # 空操作：历史调用点保持可跑
    skip_next = False
    paths = []
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a in ("--composition", "--baseline"):
            skip_next = True
            continue
        if a.startswith("--"):
            continue
        paths.append(a)
    if len(paths) != 1:
        print(__doc__)
        return 2
    target = paths[0]

    means, n_frames = zone_means(target)
    found = pulses(means)

    index_path = resolve_index(comp_arg) if comp_arg is not None else None
    events = design_events(index_path) if index_path else []
    b_list = []
    if baseline_arg is not None:
        b_means, _ = zone_means(baseline_arg)
        b_list = pulses(b_means)

    design, artifact, deterministic = [], [], []
    for p in found:
        rep = matched_in_baseline(p, b_list)
        if rep is not None:
            deterministic.append(p)
        hit = near_design(p["t"], events) if events else None
        if hit:
            design.append({"pulse": p, "event_at": hit[0], "event_span": hit[1],
                           "also_in_baseline": rep})
        else:
            artifact.append({"pulse": p, "also_in_baseline": rep})

    if as_json:
        print(json.dumps({"target": target, "composition": index_path,
                          "events_declared": len(events), "baseline": baseline_arg,
                          "frames": n_frames, "legacy_hook_flag_ignored": legacy_hook,
                          "pulses": [a["pulse"] for a in artifact],
                          "deterministic": deterministic,
                          "design_events_in_film": [d["pulse"] for d in design]},
                         ensure_ascii=False))
    else:
        print(f"扫描 {target}（{n_frames} 帧）"
              + (f"｜组成 {index_path} 声明 {len(events)} 个单帧刀口" if index_path
                 else "｜无组成声明：每个脉冲都算伪影")
              + (f"｜baseline={baseline_arg}（只取证，不豁免）" if baseline_arg else ""))
        for a in artifact:
            p = a["pulse"]
            tag = "上一轮同一帧也有" if a["also_in_baseline"] else "上一轮没有"
            print(f"ARTIFACT {p['zone']} frame {p['frame']} t={p['t']}s "
                  f"{p['from']}->{p['to']}（step {p['step']:+.1f}）｜{tag}")
        for d in design:
            p = d["pulse"]
            print(f"设计内（组成在 {d['event_at']}s 声明了一记 {d['event_span']}s 的单帧刀口） "
                  f"{p['zone']} frame {p['frame']} t={p['t']}s {p['from']}->{p['to']}")
        if not found:
            print(f"clean ({n_frames} frames)")

    return 1 if artifact else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
