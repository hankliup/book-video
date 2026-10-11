#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已渲好的静默片头混成一条独立交付的 renders/intro-only.mp4（常驻规则）。

为什么要单独一个脚本：render-episode-final.mjs 和兜底渲染器都把片头 trim 到
INTRO_TRIM_SECONDS（默认 3.0s）再和正文拼接，永远拿不到完整 4.27s 片头，而本仓的
交付要求是「除成片外必须同时给一条带自己口播、齿轮音效和水声的独立片头」。以前这段
混音每期手敲 ffmpeg，两个坑反复踩：

  1) -shortest / amix=duration=first 会被 1.829s 的共享片头口播截到 1.8s，
     之后所有响度、时长读数全是废的，而且症状只是「片头变短了一半」，很容易被
     当成渲染出错去重渲 HyperFrames。这里固定 apad + duration=longest + atrim。
  2) 4s 素材上 loudnorm 不可用：它的测量门限约 3s，整段响度会被算成静音附近的值，
     一压就是十几个 dB。所以这里用「先混音 → ebur128 量出实际 I → 静态增益补到
     -14 LUFS → 再过 limiter」两遍法，并复量断言。

混音参数一律从兜底渲染器同源取（BGM 0.32 / 齿轮 1.4 @1.08s / 水声 1.0 @切点+0.32s），
不在本文件里重抄秒数：水波起点由 lib/water-ripple.json 推，改常量两边一起动，
否则就会出现「独立片头有水声没水波」。

用法：
  python3 scripts/render-intro-only.py "<书名>" [--intro <silent.mp4>] [--bgm <name>]
                                             [--out <path>] [--no-ripple]
"""
import argparse
import json
import os
import re
import subprocess
import sys

def _root():
    """优先 git toplevel（AGENTS.md 要求别把路径写死），但这个仓库可能根本没初始化
    git——实测 `git rev-parse` 直接 128，所以退回按脚本自身位置定位仓库根。"""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode == 0 and os.path.isdir(os.path.join(r.stdout.strip(), "scripts")):
        return r.stdout.strip()
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


ROOT = _root()
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET  # noqa: E402

TARGET_I = -14.0   # 与兜底渲染器 loudnorm=I=-14.0 一致
TARGET_TP = -1.0   # dBTP
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # 绝不打印完整命令：混音命令里不含密钥，但 ffmpeg 的报错尾巴塞进 stderr 才够
        # 定位，长命令行只会把日志刷满。
        raise SystemExit(f"命令失败({r.returncode}): {cmd[0]} …\n{(r.stderr or '')[-1500:]}")
    return r.stdout or ""


def slug_of(episode):
    """走 scripts/lib/episode-slug.mjs，避免在 Python 里重抄一份 slugify 而和 JS 分叉。
    用 file:// 绝对路径 import：相对 "./" 会随调用方的 cwd 变化而找不到模块。"""
    mod = "file://" + os.path.join(ROOT, "scripts", "lib", "episode-slug.mjs")
    js = f'import({json.dumps(mod)}).then(m=>process.stdout.write(m.slugifyEpisodeName(process.argv[1])))'
    return run(["node", "-e", js, episode]).strip()


def probe_dur(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path]).strip())


def ebur128(path):
    """返回 (集成响度 LUFS, 真峰值 dBFS)。

    报告在 stderr，且每帧都有一行 `I: … LUFS`——只在最后一帧之后的 Summary: 段里
    才是整段的集成响度。按行首匹配会在起始帧就命中（未测出声音时那里恒为 -70.0），
    于是响度被读成静音、增益被补到 +56 dB，整条片头炸掉。
    """
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ebur128 失败: {r.stderr[-600:]}")
    summary = r.stderr.split("Summary:")[-1]
    i = re.search(r"^\s*I:\s+(-?\d+\.\d)\s+LUFS", summary, re.M)
    p = re.search(r"^\s*Peak:\s+(-?\d+\.\d)\s+dBF", summary, re.M)
    if not i:
        raise SystemExit(f"ebur128 读数失败：{r.stderr[-600:]}")
    return float(i.group(1)), (float(p.group(1)) if p else -999.0)


ap = argparse.ArgumentParser()
ap.add_argument("episode")
ap.add_argument("--intro", default=None, help="静默片头 mp4（默认取预览工程渲出的那条）")
ap.add_argument("--bgm", default=None, help="assets/bgm 下的文件名，默认取目录里第一个")
ap.add_argument("--out", default=None)
ap.add_argument("--no-ripple", action="store_true",
                help="跳过水波后处理（只用于对比实验；交付不允许）")
args = ap.parse_args()

ep_dir = os.path.join(ROOT, "episodes", args.episode)
if not os.path.isdir(ep_dir):
    raise SystemExit(f"episode 目录不存在: {ep_dir}")

intro = args.intro or os.path.join(ROOT, "tmp", f"preview-{slug_of(args.episode)}",
                                   "intro", "renders", "intro.mp4")
if not os.path.exists(intro):
    raise SystemExit(f"缺少静默片头渲染结果: {intro}\n"
                     f"先渲片头：hyperframes render --quality standard "
                     f"--output renders/intro.mp4（在 {os.path.dirname(os.path.dirname(intro))} 下）")

voice = os.path.join(ROOT, "assets", "template-audio", "intro-voiceover-story.mp3")
bgm_dir = os.path.join(ROOT, "assets", "bgm")
bgm = os.path.join(bgm_dir, args.bgm) if args.bgm else \
    os.path.join(bgm_dir, sorted(n for n in os.listdir(bgm_dir) if n.lower().endswith(".mp3"))[0])
scroll = os.path.join(ROOT, "assets", "sfx", "gear-scroll.mp3")
water = os.path.join(ROOT, "assets", "sfx", "water.mp3")
for p in (voice, bgm, scroll, water):
    if not os.path.exists(p):
        raise SystemExit(f"缺少音频素材: {p}")

dur = probe_dur(intro)
fps_in = 30.0
water_start = round(INTRO_TRIM + WATER_OFFSET, 2)
water_end = round(water_start + WATER_DUR, 2)
# 片头工程只有 4.27s，而水声窗口按成片时间轴要到 4.38s。不能为了塞进去把水声剪短
# （音效和涟漪共用同一对常数，剪了就与成片不同窗），也不能把起点提前——提前就是
# 「独立片头的水波比成片早半秒」。这里用最后一帧定格补足：片头尾部本来就是结果页
# 静帧，多冻 4 帧看不出来。
out_dur = max(dur, water_end)
if out_dur > dur + 1e-6:
    print(f"[intro-only] 片头 {dur:.3f}s 装不下 {water_start:.2f}–{water_end:.2f}s 水声窗口，"
          f"末帧定格补到 {out_dur:.3f}s")
scroll_dur = round(SCROLL_END - SCROLL_START, 2)
tmp_dir = os.path.join(ROOT, "tmp", f"intro-only-{slug_of(args.episode)}")
os.makedirs(tmp_dir, exist_ok=True)
mix_wav = os.path.join(tmp_dir, "mix.wav")
gain_wav = os.path.join(tmp_dir, "gain.wav")
out = args.out or os.path.join(ep_dir, "renders", "intro-only.mp4")
os.makedirs(os.path.dirname(out), exist_ok=True)

print(f"[intro-only] 输出 {out_dur:.3f}s  水声 {water_start:.2f}–{water_end:.2f}s  "
      f"BGM={os.path.basename(bgm)}")

# ---- 1. 混音（先不做任何响度归一，只把四路叠起来并补齐到片头时长）
run(["ffmpeg", "-y", "-i", voice,
     "-stream_loop", "-1", "-i", bgm,
     "-i", scroll, "-i", water,
     "-filter_complex",
     ";".join([
         # apad 而不是 -shortest：口播只有 1.829s，任何"跟着最短一路走"的写法都会
         # 把整条片头截到 1.8s。
         "[0:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,apad[va]",
         f"[1:a]atrim=0:{out_dur},asetpts=PTS-STARTPTS,aresample=48000,volume={BGM_VOL},"
         f"afade=t=in:st=0:d=0.5,afade=t=out:st={round(out_dur - 0.8, 2)}:d=0.8[bg]",
         f"[2:a]atrim=0:{scroll_dur},asetpts=PTS-STARTPTS,aresample=48000,volume=1.4,"
         f"afade=t=in:st=0:d=0.01,afade=t=out:st={round(scroll_dur - 0.2, 2)}:d=0.2,"
         f"adelay={round(SCROLL_START * 1000)}|{round(SCROLL_START * 1000)}[sc]",
         f"[3:a]atrim=0:{WATER_DUR},asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,"
         f"adelay={round(water_start * 1000)}|{round(water_start * 1000)}[wt]",
         "[va][bg][sc][wt]amix=inputs=4:duration=longest:dropout_transition=0:normalize=0,"
         f"atrim=0:{out_dur},asetpts=PTS-STARTPTS[a]",
     ]),
     "-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", mix_wav])
i0, pk0 = ebur128(mix_wav)
print(f"[loudness] 混音 I={i0:+.1f} LUFS peak={pk0:+.1f} dBFS → 静态增益 {TARGET_I - i0:+.1f} dB")

# ---- 2. 静态增益补到 -14 LUFS（loudnorm 在 4s 素材上不可用），再限幅保真峰值
gain = TARGET_I - i0
if abs(gain) > 12:
    raise SystemExit(f"需要的增益 {gain:+.1f} dB 异常，多半是混音被截短了（时长 {probe_dur(mix_wav):.2f}s）")
# 限幅天花板跟着 TP 目标算，别写死 0.95：0.95 是 -0.45 dBFS，比 -1.0 的目标高，
# 单靠它压不住，而 aac 编码还会再抬一点真峰值。留 0.3 dB 余量给编码。
lim = round(10 ** ((TARGET_TP - 0.3) / 20.0), 4)
for it in range(4):
    run(["ffmpeg", "-y", "-i", mix_wav, "-filter_complex",
         f"volume={gain:.3f}dB,aresample=192000,"
         # level=false：自动调电平会把刚算好的静态增益又改掉。4x 过采样靠前面那句
         # aresample=192000 手工做（ffmpeg 8.1 的 alimiter 根本没有 oversample 选项，
         # 写了是直接 "Option not found" 报错），限完再降回 48k 才压得住真峰值。
         f"alimiter=limit={lim}:level=false,aresample=48000",
         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", gain_wav])
    ig, pg = ebur128(gain_wav)
    if pg > TARGET_TP:
        adj = -(pg - TARGET_TP)
    elif abs(ig - TARGET_I) > 0.2:
        adj = TARGET_I - ig
    else:
        print(f"[loudness] 收敛于 I={ig:+.1f} LUFS TP={pg:+.1f} dBFS（增益 {gain:+.2f} dB）")
        break
    print(f"[loudness] 第{it + 1}轮 I={ig:+.1f} TP={pg:+.1f} → 增益调到 {gain + adj:+.2f} dB")
    gain += adj
else:
    print(f"[loudness] 4 轮未完全收敛：I={ig:+.1f} TP={pg:+.1f}，按最后结果交付")

mux = ["ffmpeg", "-y", "-i", intro, "-i", gain_wav]
if out_dur > dur + 1e-6:
    hold = round(out_dur - dur, 4)
    mux += ["-vf", f"tpad=stop_mode=clone:stop_duration={hold}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-crf", "18"]
else:
    mux += ["-c:v", "copy"]
mux += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-t", f"{out_dur:.6f}", out]
run(mux)

i1, pk1 = ebur128(out)
got = probe_dur(out)
print(f"[verify] {got:.3f}s  I={i1:+.1f} LUFS peak={pk1:+.1f} dBFS")
assert abs(got - out_dur) < 0.05, f"时长被改动: {got:.3f}s vs 计划 {out_dur:.3f}s"
assert abs(i1 - TARGET_I) <= 1.0, f"响度未达标: {i1:+.1f} LUFS（目标 {TARGET_I}）"
assert pk1 <= TARGET_TP + 0.5, f"峰值过高: {pk1:+.1f} dBFS"

# ---- 3. 水波：片头文字已烧进画面，只能走 protect-type 后处理（顶部书名带整段不动）
if not args.no_ripple:
    ripple_cmd = ["python3", os.path.join(ROOT, "scripts", "add-water-ripple.py"), out,
                  "--start", f"{water_start:.2f}", "--fps", "30"]
    print("[ripple] " + " ".join(ripple_cmd[1:]))
    run(ripple_cmd)

probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", out], capture_output=True, text=True).stdout)
v = next(s for s in probe["streams"] if s["codec_type"] == "video")
a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
num, _, den = (v.get("r_frame_rate") or "0/1").partition("/")
fps = float(num) / float(den or 1)
print(f"\nOK {out}\n   {v['width']}x{v['height']} {v['codec_name']} "
      f"{fps:.0f}fps  {float(probe['format']['duration']):.2f}s  "
      f"audio={'aac' if a else 'MISSING'}")
print("   独立片头只有 ~4.3s，check-water-ripple.py 需要到第 146 帧才能判，"
      "对它必然报「抽帧不足」——这条按人眼验收：抽 3.32–4.38s 的帧与加波前对比，"
      "确认同心圆外扩且顶部书名带纹丝不动。")
