#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享模板样式兜底渲染（scene-map 驱动，N 张氛围图）。

为什么走这条路：HyperFrames 0.7.33 在本机对 200s+ 正文的单次会话渲染稳定崩溃
（Page.captureScreenshot: Target closed），而分段渲染又因运行时只认「创作时」写入的
字幕时间（事后 tl.shiftChildren() 被忽略）导致每段重播正文开头 —— 已交付的
《霍比特人》《白鲸》即因此疑似音画错位。故沿用《道林·格雷的画像》《白鲸》已验证的
ffmpeg + Pillow 浮点合成路线，并把它泛化成按语义段落铺图的通用版本。

与 render_baijing_template_fallback.py 的差异：
  · 场景不再「3 张图均分整段」，而是读 episodes/<书名>/scene-map.json，
    把 script-body.csv 的行号区间映射到具体氛围图，切镜时间取相邻行配音间隙的中点，
    保证换图一定落在停顿里，不会话没说完就跳画面。
  · 慢推倍率按源图分辨率自适应：源图只有 720x960（Pexels 裁切）时，
    1.02→1.062 的推镜等于把画面放大 6% 再采样，会糊；这里按 cover 是否大于 1
    决定推镜幅度，保证永远不做明显上采样。

视觉与混音规格仍与 templates/shared-video-template 一致：
  · 片头复用本期 HyperFrames 渲染的 intro.mp4，裁 INTRO_TRIM（默认 3.0s，切在结果页定格）
  · 正文 result-bridge 起幅 → 语义场景，0.64s 交叉溶解 + 居中缓慢推镜
    推镜幅度：scene-map.json 带 "motion" 块时按镜头时长换算成速度（87 秒一镜不会被固定 4%
    摊成「看起来是静止的图」），不带则沿用按源图重采样余量的历史幅度，老期数重渲画面不变
  · 常驻《书名》+「作者 / 著」（top 70/140，60/34px，逐字字距 + 黑色投影）
  · 字幕白字黑投影（bottom 126px，56/50px，无卡片底框），文本以 script-body.csv 为准，
    但**上屏不显示标点、也不留空白间隙**：CSV 的标点只用于断句，画字前按
    lib/subtitle-punctuation.json 处理——落在行中间的断点号换成换行，其余标点直接消失
  · 混音与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

用法：
  EPISODE=美丽新世界 python3 scripts/render_scene_map_template_fallback.py
  EPISODE=美丽新世界 PREVIEW=... INTRO_SOURCE=... FORCE_BG=1 python3 scripts/render_scene_map_template_fallback.py
"""
import csv
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 仓库根从脚本自身位置推出。写死某一次 clone 的绝对路径，换一个目录（读书视频 / 短视频）
# 就会去读一个不存在的路径，报出来的却是「缺少文件」，查半天。
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def episode_slug(name):
    """与 scripts/lib/episode-slug.mjs 同一套算法：预览目录名两边必须一致。"""
    ascii_part = re.sub(r"[^A-Za-z0-9_.-]+", "-", unicodedata.normalize("NFKD", name)).strip("-").lower()
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{ascii_part}-{digest}" if ascii_part else f"book-{digest}"


EPISODE = os.environ.get("EPISODE", "美丽新世界")
EP = os.path.join(ROOT, "episodes", EPISODE)
# 正文氛围图目录：三段结构改名后叫 images-body/，历史期数只有 images/。
# 解析口径不在这里重写，统一取自 lib/episode_paths.py（出图、调色、溯源门禁、封面同一个函数），
# 否则改名后的第一期会在渲染中途 FileNotFoundError，而图其实就在隔壁目录。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from episode_paths import body_images_dir as _body_images_dir  # noqa: E402
IMG = os.environ.get("IMG_DIR", _body_images_dir(EP))
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.environ.get("PREVIEW", os.path.join(ROOT, "tmp", f"preview-{episode_slug(EPISODE)}"))
INTRO_SOURCE = os.environ.get(
    "INTRO_SOURCE", os.path.join(PREVIEW, "intro", "renders", "intro.mp4"))
# 三段结构：钩子段（本期专属）→ 固定开场白（模板裁到 INTRO_TRIM）→ 正文。
# 有没有钩子段只看 script-hook.csv 在不在，和 create-episode-preview.mjs /
# render-episode-final.mjs 用同一个信号；判据不一致就会出现「有钩子画面没配音」。
HOOK_SOURCE = os.environ.get("HOOK_SOURCE", os.path.join(PREVIEW, "hook", "renders", "hook.mp4"))
HAS_HOOK = os.path.exists(os.path.join(EP, "script-hook.csv"))
TMP = os.environ.get("FALLBACK_TMP", f"/tmp/{EPISODE}_tpl")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
# 字幕字体与《书名》+ 作者层**同源**：都走 scripts/lib/title-font.json 的 resolve()
# （用户 2026-10-06「优化书籍字体，改为微软雅黑」定下书名层口径；2026-10-07「重新优化视频
# 字幕字体，用更为适合的字体样式」把字幕并入同一条解析——书名与字幕永远同族同 weight，
# 本机装了雅黑就一起换雅黑，没有就一起用苹方-简）。
# 旧的字幕常量 FONT = "STHeiti Medium.ttc" 在这里退役，不只是换家族：ImageFont 默认
# index 0 是 **Heiti TC（黑体-繁）**，简体文案一直在拿繁体 face 排——正是书名层注释里
# 那个「写死 .ttc 的 index 拿繁体 face」的坑（AGENTS.md 封面条）。resolve() 按 face 名
# 反查 index，这台机器落到 PingFang SC Medium index=7。钩子段字幕的 CSS 栈
# （create-episode-preview.mjs 的 .caption）用同一个 lib 的 titleFontFamilyCss/WeightCss，
# 两条渲染路径同族同字重。共享片头的字体仍是它自己的（2026-10-05 裁决冻结，不在此列）。
import title_font as _title_font  # noqa: E402

TITLE_FACE = _title_font.resolve()
# 中英混排的拉丁字体：STHeiti 自带的拉丁字形又小又轻，「少年Pi」「理查德·帕克」里的
# Pi 和 · 在屏上明显失重（2026-10-05《少年Pi的奇幻漂流》用户报「中英文字体和特殊符号的
# 排版」）。Arial Bold 同字号 cap 高 = 0.82×汉字墨高（实测 40/49），正是中英混排的常规
# 比例，同字号直接混排即可；基线用各字体自己的 ascent 对齐（见 _mixed_items）。
FONT_LATIN = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
_is_latin_ch = re.compile(r"[0-9A-Za-zＡ-Ｚａ-ｚ０-９]").match

brief = json.load(open(os.path.join(EP, "brief.json"), encoding="utf-8"))
TITLE_TEXT = f"《{brief['display_title']}》"
AUTHOR_TEXT = f"{brief['author']} / 著"

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.38s 起「结果页」（桥接图 + 书名/作者）
# 稳定定格、page-bg 从 scale 1 缓推到 1.035。裁 3.0s 时定格倍率约 1.0087，
# 正文首帧必须用同一倍率起幅，否则切点会有一跳（见下方 BRIDGE_ZOOM 推导）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
BGM_VOL = 0.32
# 片头时刻表（滚动书单调什么时候停、结果页什么时候定格）只有一个真源：
# scripts/lib/intro-template.json，交付门禁读同一份，两边不许各写一遍秒数。
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib",
                       "intro-template.json"), encoding="utf-8") as _f:
    INTRO_TIMING = json.load(_f)
SCROLL_START, SCROLL_END = INTRO_TIMING["scroll_start"], INTRO_TIMING["scroll_end"]
RESULT_PAGE_AT = INTRO_TIMING["result_page_at"]
if INTRO_TRIM < RESULT_PAGE_AT:
    # 《恶意》2026-10-01 首版就是 1.83（= 片头配音长度）出的片：切点落在滚动书单里，
    # 观众在进正文前最后一帧看到的是**别本书**的封面卡（本期是《百年孤独》），
    # 而「把书名交给观众」这件事正是结果页要做的。切点跟着画面走，不跟着配音走。
    raise SystemExit(
        f"INTRO_TRIM_SECONDS={INTRO_TRIM} 早于结果页定格点 {RESULT_PAGE_AT}s："
        f"片头会被裁在滚动书单上，正文第一帧直接接上别本书的封面卡。"
        f"模板配音只有 {INTRO_TIMING['voiceover_seconds']}s，多出来的静默是结果页定格，"
        f"不是可以省掉的长度（见 scripts/lib/intro-template.json）。")


def _probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"取时长失败：{path}")
    return float(r.stdout.strip())


# 三段结构里钩子段占成片最前面 hook_dur 秒，模板段整体右移，所以「正文起点」=
# PRE_BODY = hook_dur + INTRO_TRIM。下面排音频的 adelay、水波窗口、切点校验一律用 PRE_BODY，
# 不再各自写一遍 INTRO_TRIM；两段结构（没有 script-hook.csv）时 hook_dur=0，PRE_BODY 退回 INTRO_TRIM。
hook_dur = 0.0
if HAS_HOOK:
    hook_voice_path = os.path.join(AUDIO, "hook-voiceover-story.mp3")
    for _p in (HOOK_SOURCE, hook_voice_path):
        if not os.path.exists(_p):
            raise SystemExit(f"有 script-hook.csv 但缺钩子段素材：{_p}")
    hook_dur = round(_probe_duration(hook_voice_path), 2)
PRE_BODY = round(hook_dur + INTRO_TRIM, 2)
PRE_BODY_MS = round(PRE_BODY * 1000)
HOOK_OFFSET_MS = round(hook_dur * 1000)
INTRO_OFFSET_MS = HOOK_OFFSET_MS          # 固定开场白的配音跟在钩子后面
if hook_dur:
    print(f"[hook] 钩子段 {hook_dur:.2f}s → 模板段 {INTRO_TRIM}s → 正文起点 {PRE_BODY:.2f}s")
# 水波窗口 = 片头→正文切点后 WATER_OFFSET 秒起、持续 WATER_DUR 秒。
# 常数真源在 scripts/lib/water_ripple.py（与音效/视觉同源），在下方导入后落地为
# WATER_START / WATER_FRAME0 / WATER_NFRAMES。

XFADE = 0.64          # 场景交叉溶解时长
SMALL_CHARS = 11      # 单行超过此字数用 50px（56px 档容量 652/56=11，12 字行按 56 排会缩到 54，脱档）

# 上屏标点策略的唯一真源是 scripts/lib/subtitle-punctuation.json，
# Python 侧入口是 scripts/lib/caption_display.py（JS 侧是 lib/subtitle-display.mjs）。
# 约定：script-body.csv 保留标点（TTS 语气 + 断句折行都靠它），只在真正画字那一刻剥掉标点；
# 落在行中间的断点号一律**换成换行**（旧口径的全角空格 U+3000 已于 2026-09-27 废止：
# 屏上的空格间隙读起来像漏字，且是否折行交给容器宽度，两条渲染路径会排出两种版）；
# 译名间隔号 · 属于专有名词，保留。一条字幕最多 MAX_LINES 行，装不下就顺延成下一条字幕。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from caption_display import (  # noqa: E402
    FORBIDDEN_SET,
    MAX_LINES,
    BREAK_CHARS,
    caption_style,
    display_lines as split_display_lines,
    strip_display as display_text,
)
# 水波音效与水波视觉效果共用同一对常数（WATER_OFFSET / WATER_DUR），见 lib/water_ripple.py。
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402
# 正文起幅那张「本书封面图」：卡框、投影、时序推导只有一个真源 lib/book-cover.json，
# 画字入口 lib/book_cover.overlay()，验收门禁 check-book-cover-overlay.py 调同一个函数。
from book_cover import (card_box as _cover_card_box,  # noqa: E402
                        load as _cover_load,
                        motion as _cover_motion,
                        overlay as _cover_overlay,
                        provenance_ok as _cover_provenance_ok,
                        source_path as _cover_source_path,
                        timing as _cover_timing)
# 字幕文本真源：三段结构的正文是 script-body.csv，历史单段期只有 script-body.csv。
# 与门禁共用 lib/caption_speech 的同一个解析函数，避免渲染读一份、门禁读另一份。
from caption_speech import resolve_script_path  # noqa: E402

SCRIPT_CSV = resolve_script_path(EP)

WATER_START = round(PRE_BODY + WATER_OFFSET, 2)            # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)                     # 涟漪帧数
# 正文背景的第 0 帧对应成片 PRE_BODY，所以背景循环里的起点帧仍是「相对正文」的偏移秒数换算
WATER_FRAME0 = round(WATER_OFFSET * FPS)


def residual_punct(s):
    return sorted({ch for ch in str(s) if ch in FORBIDDEN_SET})



def run(cmd):
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(f"CMD failed ({r.returncode}): {' '.join(str(c) for c in cmd)}")


def ff(args):
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args])


def probe_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", p],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


# ---------------------------------------------------------------- 时间轴与文案
timings = json.load(open(os.path.join(AUDIO, "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: int(c["order"]))
body_dur = float(timings["duration"])
final_dur = round(PRE_BODY + body_dur, 2)

_text_by_order_early = {}
with open(SCRIPT_CSV, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        _text_by_order_early[int(row["order"])] = row["text"].strip()

# 低置信时间兜底：ASR 行尾锚点偶发过早（一句话还没念完就判定结束），这类行会闪没。
# 按「字数 / 平均语速」估一个最短可见时长，向后延到下一条开始之前，不改变文本。
AVG_RATE = float(os.environ.get("AVG_RATE", "4.5"))   # 本系列旁白平均约 4.5 字/秒
# 地板默认 0.9s（一闪而过看不清）。但它是「一条字幕至少给 0.9 秒」，不是「一行字至少读 0.9 秒」：
# 单字/双字行（书名列，如《家》《白鲸》《恶意》）本来 0.6s 就读得完，硬撑到 0.9s 只会把字幕钉在
# 已经闭嘴的画面上 —— 第七道门禁 check-caption-speech-align.py 的 T2「窗内语音 ≥45%」正是抓这个，
# 而 0.9s 地板 + 单字语音 0.3s 在数学上永远过不了（0.3/0.9=33%）。
#
# 这个容量上限按门禁同一套自适应突发自动算，不要再靠手工 CAP_MIN_KEEP（《恶意》2026-10-01 行1
# 「恶意」：轴 0.36–0.95 密度 51%，被地板延到 0.36–1.26 只剩 33%，判红）。规则：延长之后的整窗
# 密度仍然站在 VOICED_MIN 上才允许延，掉线就收回——「延出去那一段有没有语音」不够，因为紧邻行尾
# 的语音往往是**下一行**的第一个字（《达洛维夫人》2026-10-02 行 130，见下方判据注释）。
# CAP_MIN_KEEP 仍然可用作总开关，只是不再是修这一类的唯一手段。
MIN_KEEP = float(os.environ.get("CAP_MIN_KEEP", "0.9"))
_floor_bursts = None


def floor_bursts():
    """地板复核用的能量突发：和门禁、子条切点走同一个 lib、同一个音频文件。"""
    global _floor_bursts
    if _floor_bursts is None:
        _src = next((os.path.join(AUDIO, n) for n in
                     ("body-voiceover.mp3", "body-voiceover-story.mp3", "配音.mp3")
                     if os.path.exists(os.path.join(AUDIO, n))), None)
        try:
            from caption_speech import speech_bursts as _sb
            _floor_bursts = list(_sb(_src)[0]) if _src else []
        except Exception as _exc:
            _floor_bursts = []
            print(f"[warn] 地板语音容量读不到能量突发（{_exc}），沿用 {MIN_KEEP}s 硬地板")
    return _floor_bursts


def voiced_between(a, b, bursts):
    if b <= a:
        return 0.0
    return sum(max(0.0, min(b, b1) - max(a, b0)) for b0, b1 in bursts)
# 一行拆多条时，末条在本行**语音终点**之后还允许挂多久。行级轴为整行留的阅读留白
# （snap 的 MAX_LEADOUT 0.60s）不该整段砸在只有两三个字的末条字幕上，所以这里只留一点
# 收尾余量，剩下的交给两条字幕之间的真空窗（真空窗落在真停顿里，不会被 B 判成闪断）。
TAIL_HOLD = float(os.environ.get("CAP_TAIL_HOLD", "0.12"))
# 子条自己的可读地板：用 audit-caption-axis.py 的同一套口径（read_floor 0.55、5.2 字/秒），
# 而不是「整行时长 ÷ 条数」那种摊平份额 —— 一条 13 字的字幕和一条 2 字的字幕需要的阅读
# 时间不一样，摊平会给短的那条强塞 1.25s 地板，于是它必然盖住半秒停顿（T2 判空放）。
# 地板仍然用原 span/(n*1.8) 封顶，免得把长字的子条提得比旧口径还高、动了别期的行为。
SUB_FLOOR = float(os.environ.get("CAP_SUB_FLOOR", "0.55"))
SUB_RATE = float(os.environ.get("CAP_CHARS_PER_SEC", "5.2"))
# 每条上屏字幕的「窗内语音占比」低于此就重挑切点。门禁 T2 的红线是 0.45，这里留 5 个点的余量，
# 免得渲出来卡在 45.2% 上——门禁和渲染器用的不是同一个 ffmpeg 解码路径，差 1~2 个点很正常。
VOICED_MIN = float(os.environ.get("CAP_VOICED_MIN", "0.50"))
_adjusted = []

for _i, _c in enumerate(caps):
    _s, _e = float(_c["start"]), float(_c["end"])
    _span = _e - _s
    _need = max(MIN_KEEP, len(_text_by_order_early.get(int(_c["order"]), "")) / AVG_RATE * 0.55)
    _next = float(caps[_i + 1]["start"]) if _i + 1 < len(caps) else body_dur
    _cand = min(_s + _need, _next - 0.05)
    _bs = floor_bursts()
    # 地板延长要按「整窗听不听得见话」收口，两种失效是同一件事：
    #   · 延出去全是静音 —— 《恶意》2026-10-01 行 1「恶意」：轴 0.36–0.95 密度 51%，
    #     被 0.9s 地板延到 1.26 只剩 33%，第七道门判红；
    #   · 延出去那一段**有语音，但不是这一行的**——紧邻行尾的第一个突发往往是下一行
    #     的第一个字。《达洛维夫人》2026-10-02 行 130「她想，」：轴 235.36–235.91 密度
    #     51%，下一行 236.20 开口，旧判据 `_tail_speech > 0.02` 只看「235.91→236.26 之间
    #     有没有能量」（有，0.06s 是「活着」的「活」），于是放行到 236.15，整窗 0.79s 里
    #     只有 0.28s 是这一行念的 → 35% 判红。这种延长同时把下一行的字提前压在上一条字幕
    #     上，正是 AGENTS.md 禁止的「把行尾推进后面的静音里、顺手偷走下一行的语音」。
    # 判据只问密度，和门禁 T2 同一口径（这里留 5 个点余量）：密度掉到线下就不再延，
    # 于是「本行话还没念完」（行尾锚点过早）那种真需要延长的场景照旧通过——它买的是自己的语音。
    if _bs and _cand > _e:
        while _cand > _e and voiced_between(_s, _cand, _bs) < VOICED_MIN * (_cand - _s):
            _cand = round(_cand - 0.02, 3)
    if _cand > _e + 1e-6:
        _adjusted.append((int(_c["order"]), round(_span, 2), round(_cand - _s, 2)))
        _c["end"] = round(_cand, 3)
if _adjusted:
    print(f"[warn] {len(_adjusted)} 行字幕时长低于语速估计，已按说话时长兜底延长（不改变文本）：")
    for _o, _was, _now in _adjusted:
        print(f"       order {_o:>3}: {_was}s → {_now}s")

span = {int(c["order"]): (float(c["start"]), float(c["end"])) for c in caps}


text_by_order = {}
with open(SCRIPT_CSV, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        text_by_order[int(row["order"])] = row["text"].strip()

scene_map = json.load(open(os.path.join(EP, "scene-map.json"), encoding="utf-8"))
layers_cfg = scene_map["layers"]
max_order = max(text_by_order)
for cfg in layers_cfg:
    a, b = int(cfg["order_start"]), int(cfg["order_end"])
    if not (1 <= a <= b <= max_order):
        raise SystemExit(f"scene-map 行号越界：{cfg['name']} {a}-{b}（script-body.csv 共 {max_order} 行）")
covered = sorted({o for c in layers_cfg for o in range(int(c["order_start"]), int(c["order_end"]) + 1)})
if covered != list(range(1, max_order + 1)):
    raise SystemExit(f"scene-map 未完整覆盖 1..{max_order}，缺 {sorted(set(range(1, max_order + 1)) - set(covered))[:10]}")
for i in range(1, len(layers_cfg)):
    if layers_cfg[i]["name"] == layers_cfg[i - 1]["name"]:
        raise SystemExit(f"scene-map 出现相邻同图：{layers_cfg[i]['name']}")

# 切镜时间 = 上一层末行结束与本层首行开始的中点，必定落在配音停顿里
layer_names = [c["name"] for c in layers_cfg]
layer_start = [0.0]
for i in range(1, len(layers_cfg)):
    prev_end = span[int(layers_cfg[i - 1]["order_end"])][1]
    cur_start = span[int(layers_cfg[i]["order_start"])][0]
    layer_start.append(round((prev_end + cur_start) / 2.0, 3))

# 承接段一收到 0.78s（《教父》），桥接卡→首镜的交叉溶解就落进了水波窗口里，而门禁量的是
# 「逐帧差质心一路外移」：溶解是一次全幅画面替换，会把质心反手拽回中心。实测窗口 32 帧里
# 只有前 13 帧还在静止卡上，那 13 帧的涟漪干净得超出判据（drift +88px / ρ +1.00），
# 后面 19 帧溶解中和成 drift -2px / ρ -0.11，门禁于是判「只有水声没有画面」。
# 所以涟漪必须整段跑在静止桥上：溶解不得早于水波收口。窗口常量仍取自 lib，不写秒数。
_RIPPLE_END = WATER_OFFSET + WATER_DUR
if len(layer_start) > 1 and layers_cfg[0].get("static") and layer_start[1] < _RIPPLE_END:
    ceil = layer_start[2] - XFADE if len(layer_start) > 2 else body_dur
    _pushed = min(_RIPPLE_END, max(layer_start[1], ceil))
    print(f"[water] 首镜切点 {layer_start[1]:.3f}s 早于涟漪收口 {_RIPPLE_END:.2f}s，"
          f"推到 {_pushed:.3f}s（交叉溶解不得与水波同窗）")
    layer_start[1] = round(_pushed, 3)

layer_end = layer_start[1:] + [body_dur]
layer_len = [round(e - s, 3) for s, e in zip(layer_start, layer_end)]
print(f"[layers] {len(layer_names)} 层 / 正文 {body_dur:.2f}s")
for cfg, n, s, ln in zip(layers_cfg, layer_names, layer_start, layer_len):
    # theme 只用于这行日志（图层描述字段在历史 scene-map 里有 theme / note 两种写法）。
    # 写成 cfg['theme'] 的话，一期缺这个字段就会在「已经算完全部切点、正要合成 300s 背景」
    # 的位置整场崩溃 —— 一行 print 不该有终止渲染的权力。
    print(f"  {n:16} {s:7.2f}s +{ln:6.2f}s  {str(cfg.get('theme') or cfg.get('note') or '')[:34]}")


# ------------------------------- 1. 正文背景：亚像素缓推 + 交叉溶解（单趟 Pillow 合成）
# 不用 ffmpeg zoompan：它的 x/y/裁剪宽高都会取整到源图整数像素，源图越小量化台阶越大，
# 慢镜头里就是持续抖动。这里用 Image.transform 的浮点仿射矩阵，每帧缩放与中心连续。
# 幅度按重采样余量（1/cover）自适应：源图≈画幅时几乎不推，避免放大上采样发糊。
def ease_in_out(u):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


def zoom_range(cover):
    """慢推幅度按重采样余量判断（不是按 cover 本身）。

    cover = max(W/iw, H/ih)：源图相对画幅越大，cover 越小、余量 1/cover 越大。
    旧写法 `if cover > 1.15` 与注释方向相反（cover 大其实代表源图比画幅**小**），
    结果所有 1024x1365 的实拍图都被降到 1.006→1.042，推镜几乎看不出来。
    余量 ≥1.15 才走共享模板原幅度 1.020→1.062（与《人性的枷锁》《荆棘鸟》一致）；
    源图≈画幅（余量≈1）仍留轻微档，避免放大 6% 重采样发糊。
    """
    if 1.0 / max(cover, 1e-6) >= 1.15:
        return 1.020, 1.062
    return 1.006, 1.042   # 源图≈画幅：只做轻微推镜，全程基本 1:1 采样


# scene-map.json 可选 "motion" 块：把运镜从「固定幅度摊满整段」改成「按镜头时长换算速度」。
#   {"zoom_start":1.05,"zoom_per_10s":0.045,"zoom_travel_min":0.09,"zoom_travel_max":0.15,
#    "drift_y_px":-12}
# 为什么需要：固定 1.020→1.062 的 4% 摊到 87 秒一镜，边缘位移只有 0.14px/s，观众读出来的是
# 「一张静止的氛围图」而不是「镜头在慢慢靠近」。摊到 20 秒一镜才勉强算慢推。所以幅度必须由
# 镜长决定；同时上限要卡在 1:1 采样线以内，否则长镜为了看得见动就开始放大发虚。
# 不写 motion 块时行为完全不变（沿用 zoom_range 的历史固定幅度），老期数重渲不会改画面。
_MOTION = scene_map.get("motion") or {}


def motion_range(cover, length):
    """(z0, z1, drift_px)：给了 motion 块就按镜长换算，否则退回历史固定幅度、零位移。"""
    if not _MOTION:
        z0, z1 = zoom_range(cover)
        return z0, z1, 0.0
    z0 = float(_MOTION.get("zoom_start", 1.05))
    per10 = float(_MOTION.get("zoom_per_10s", 0.045))
    lo = float(_MOTION.get("zoom_travel_min", per10 * 2.0))
    hi = float(_MOTION.get("zoom_travel_max", 0.15))
    travel = min(hi, max(lo, per10 * max(length, 1e-6) / 10.0))
    # 1:1 采样线：factor = cover × z ≥ 1 之后每一分都是真的在上采样，留 3% 余量封顶
    z1 = min(z0 + travel, 0.97 / max(cover, 1e-6))
    return z0, max(z1, z0), float(_MOTION.get("drift_y_px", 0.0))


def zoom_frame(im, factor, dy_out=0.0):
    """以图片中心为锚点，按浮点倍率取景到 720x960（无整数抖动）。

    dy_out：取景中心在输出像素上的纵向偏移（负=向上看）。逐帧钳到当前裁切余量内，
    起幅时裁切框几乎占满源图、余量最小，位移会被自动压小，绝不露黑边。
    """
    iw, ih = im.size
    inv = 1.0 / factor
    cw, ch = W * inv, H * inv
    head = max(0.0, (ih - ch) / 2.0) * factor          # 余量换算到输出像素
    dy = max(-head, min(head, dy_out))
    return im.transform(
        (W, H), Image.AFFINE,
        (inv, 0.0, iw / 2.0 - cw / 2.0,
         0.0, inv, ih / 2.0 - ch / 2.0 + dy),
        resample=Image.BICUBIC)


# 桥接图静止倍率：对齐片头定格在 INTRO_TRIM 处的 page-bg scale
_intro_u = min(1.0, max(0.0, (INTRO_TRIM - 2.38) / 1.87))
BRIDGE_ZOOM = 1.0 + 0.035 * ease_in_out(_intro_u)
print(f"[bridge] 静止倍率 {BRIDGE_ZOOM:.4f}（对齐片头 {INTRO_TRIM}s 定格）")

bg = os.path.join(TMP, "body-bg.mp4")
force = os.environ.get("FORCE_BG") == "1"


def _layer_image_fingerprint():
    """本期用到的每张氛围图的内容指纹（sha1 前 12 位）。

    为什么必须进签名：签名原来只覆盖「时长 + 切镜表 + 水波窗口 + 推镜参数」。换了一张
    Pexels 照片、或者整组重新调色（`--restore` 后重跑 grade/fix-bright），这些量一个都没变，
    于是 `[bg] reuse body-bg.mp4` 把**旧画面**当成新画面复用，而八道交付门禁没有一道看得见
    ——它们查的是溯源、字幕、水波、钩子层，全部与像素内容无关。《德米安》2026-10-02 实测：
    为排除抱枕上可读的现代法文招牌给 atmosphere-1 加了 crop_box 并重调色，重渲日志
    `[bg] reuse`，成片 20s 处仍是换掉前那一帧，靠人眼抽帧才发现。
    按内容哈希而不是 mtime：`--restore` 会把 _raw 原样拷回，字节没变就不该重合成。
    """
    fp = {}
    for name in sorted(set(layer_names)):
        path = os.path.join(IMG, f"{name}.png")
        try:
            with open(path, "rb") as fh:
                fp[name] = hashlib.sha1(fh.read()).hexdigest()[:12]
        except OSError:
            fp[name] = "missing"
    return fp


# body-bg.mp4 只在「所有输入都没变」时才可复用：时长、切镜表、水波窗口、**氛围图本身**
# 任一变化都必须重合成，否则改了 scene-map、加了水波、换了图或重调色却静默拿到旧背景，
# 验收还全绿。标记落 body-bg.json。
_bg_sig = {"body_dur": round(body_dur, 3), "layer_start": layer_start,
           "layers": layer_names, "fps": FPS,
           "water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES,
           "motion": _MOTION, "images": _layer_image_fingerprint()}
_bg_marker = os.path.join(TMP, "body-bg.json")


def _bg_reusable():
    if not (os.path.exists(bg) and abs(probe_dur(bg) - body_dur) < 0.2):
        return False
    try:
        return json.load(open(_bg_marker, encoding="utf-8")) == _bg_sig
    except Exception:
        return False


# CAPS_ONLY：只预检字幕切条与时间分配，跳过几分钟的重量级背景合成。
CAPS_ONLY = os.environ.get("CAPS_ONLY") == "1"
# PREBUILT_BG=<mp4>：正文背景用外部已经渲好的整段背景（含慢推近、交叉淡入与画面水波涟漪），
# 不再由 Pillow 逐帧重合成。为什么需要：本仓那条 HyperFrames 组合（14 个 will-change:transform
# 场景 + 148 条字幕 tween）在单个 Chrome 会话里跑不完 4.6 分钟——渲染页在 25%-64% 之间必然
# "Target closed"，换 worker 数、低内存档、本地 GSAP、去掉字幕 will-change 都无效。
# 分块渲染可以出背景，但字幕层交给 Pillow 这条成熟路径更稳（已交付各期走的就是它）。
# 只接受时长与正文一致的候选，且必须自证水波（水波窗口是画面里量出来的，不靠信任）。
_prebuilt = os.environ.get("PREBUILT_BG")
if _prebuilt and os.path.exists(_prebuilt):
    if abs(probe_dur(_prebuilt) - body_dur) > 0.35:
        raise SystemExit(f"PREBUILT_BG 时长 {probe_dur(_prebuilt):.2f}s 与正文 {body_dur:.2f}s 不符")
    print(f"[bg] PREBUILT_BG {_prebuilt} {probe_dur(_prebuilt):.2f}s（外部预渲染背景）")
    ff(["-i", _prebuilt, "-t", f"{body_dur:.3f}", "-vf", f"fps={FPS},format=yuv420p",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-crf", "17", "-preset", "medium", bg])
    json.dump(_bg_sig, open(_bg_marker, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
elif CAPS_ONLY:
    print("[bg] CAPS_ONLY 预检模式：跳过正文背景合成")
elif _bg_reusable() and not force:
    print(f"[bg] reuse body-bg.mp4 {probe_dur(bg):.2f}s（切镜表与水波窗口未变）")
else:
    covers = {}
    for name in set(layer_names):
        src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
        iw, ih = src.size
        covers[name] = (src, max(W / iw, H / ih))
        print(f"[img] {name:16} {iw}x{ih} cover={max(W / iw, H / ih):.3f}")

    n_total = round(body_dur * FPS)
    pipe = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-crf", "17", "-preset", "medium", bg],
        stdin=subprocess.PIPE)

    motions = [motion_range(covers[n][1], ln) for n, ln in zip(layer_names, layer_len)]
    for i, ((z0, z1, dr), n, ln) in enumerate(zip(motions, layer_names, layer_len)):
        if layers_cfg[i].get("static"):
            continue
        print(f"[motion] {n:16} {z0:.3f}→{z1:.3f}（+{(z1 - z0) * 100:.1f}% / {ln:.0f}s"
              f"≈{(z1 - z0) / max(ln, 1e-6) * 100:.2f}%每秒） 纵向位移 {dr:+.0f}px")

    def layer_frame(k, t):
        name = layer_names[k]
        src, cover = covers[name]
        if layers_cfg[k].get("static"):
            return zoom_frame(src, cover * BRIDGE_ZOOM)
        z0, z1, drift = motions[k]
        u = (t - layer_start[k]) / max(1e-6, layer_len[k])
        e = ease_in_out(u)
        return zoom_frame(src, cover * (z0 + (z1 - z0) * e), drift * e)

    idx = 0
    for frame in range(n_total):
        t = frame / FPS
        while idx < len(layer_start) - 1 and t >= layer_start[idx + 1]:
            idx += 1
        cur = layer_frame(idx, t)
        # 交叉溶解：进入本层后的前 XFADE 秒内与上一层线性混合（等价 xfade=transition=fade）
        if idx > 0 and t - layer_start[idx] < XFADE:
            p = (t - layer_start[idx]) / XFADE
            cur = Image.blend(layer_frame(idx - 1, t), cur, min(1.0, max(0.0, p)))
        # 水波视觉效果：常驻规则，必须与水波音效同帧起、同帧止。
        # 只作用在背景层——书名层与字幕层在其后绘制，所以涟漪不会把字扭糊。
        _wu = water_u(frame, FPS, WATER_FRAME0, WATER_NFRAMES)
        if _wu is not None:
            cur = ripple(cur, _wu)
        pipe.stdin.write(cur.tobytes())
        if frame and frame % (FPS * 30) == 0:
            print(f"[bg] {frame / FPS:.0f}/{body_dur:.0f}s")
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文背景合成失败: {pipe.returncode}")
    json.dump(_bg_sig, open(_bg_marker, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[bg] {probe_dur(bg):.2f}s (target {body_dur:.2f}s)")
    print(f"[water] 水波背景 {WATER_FRAME0}→{WATER_FRAME0 + WATER_NFRAMES - 1} 帧 "
          f"= 成片 {WATER_START:.2f}–{round(WATER_START + WATER_DUR, 2)}s（与水波音效同窗）")


# ------------------------------------------- 2. 常驻书名层 + 字幕层（Pillow）
def _shadow_layers(sil):
    """宽而软的落影 + 紧贴笔画的窄描边。

    单层 GaussianBlur(9) 在《少年维特的烦恼》的金色大厅/面包桌这类高亮低对比氛围图上
    会把白字糊掉（《围城》的落日甲板、金色晨雾两层同样偏软）。双通道仍是「白字黑投影」，
    没有引入卡片底框，暗背景观感不变。
    """
    return (sil.filter(ImageFilter.GaussianBlur(14)),
            sil.filter(ImageFilter.GaussianBlur(3)))


_latin_font_cache = {}


def _latin_font(size):
    """与 CJK 同字号的拉丁字体（Arial Bold）；measure 见 FONT_LATIN 的注释。"""
    f = _latin_font_cache.get(size)
    if f is None:
        f = ImageFont.truetype(FONT_LATIN, size)
        _latin_font_cache[size] = f
    return f


_dot_ink_cache = {}


def _dot_ink(font):
    """当前这个 font 的 · 墨迹在自身 em 内的 (x0, 宽)：getbbox 返回的是带 bearing 的全 em
    框（STHeiti 34px 实测 (0,17,34,30)），不是墨迹框，只能真画一次量（结果按字号缓存）。
    书名层 2026-10-06 换成 title-font.json 那款之后，这里量的就是那一款——所以它必须是
    量出来的而不是写死的：各 face 的 · 位置天生不同（STHeiti 把点画在 em 左侧，
    于是有「扬· 马特尔」那种大空隙），写死一份数字就会把新字体排版排歪。"""
    key = font.size
    if key not in _dot_ink_cache:
        pad = int(font.size) + 10
        im = Image.new("L", (pad * 2, pad * 2), 0)
        ImageDraw.Draw(im).text((pad, pad), "·", font=font, fill=255)
        bb = im.getbbox()
        _dot_ink_cache[key] = (bb[0] - pad, bb[2] - bb[0]) if bb else (0.0, font.size * 0.1)
    return _dot_ink_cache[key]


def _mixed_items(text, font):
    """把一行文本拆成 (字符, 字体, 步进, y偏移) 列表。

    · 是译名间隔号：STHeiti 的 · 占满一个 em、点画在 em 左侧，直接排就是
    「扬· 马特尔」那个大空隙。这里把它的步进压到 0.5em，画字时按**墨迹**
    在步进内居中（见 _dot_ink）。拉丁字母/数字换 Arial Bold，y 偏移按各字体
    自己的 ascent 推（baseline = top + ascent_cjk，两种字体共用一条基线）。
    """
    asc_cjk = font.getmetrics()[0]
    items = []
    for ch in text:
        if ch == "·":
            items.append((ch, font, font.size * 0.5, 0))
        elif _is_latin_ch(ch):
            f = _latin_font(font.size)
            items.append((ch, f, f.getlength(ch), asc_cjk - f.getmetrics()[0]))
        else:
            items.append((ch, font, font.getlength(ch), 0))
    return items


def _draw_items(dr, items, x0, top, spacing, fill, drop=0):
    x = x0
    for ch, f, cw, y_off in items:
        pen = x
        if ch == "·":                       # 步进 0.5em，墨迹在步进内居中
            ix0, iw = _dot_ink(f)
            pen = x + (cw - iw) / 2 - ix0
        dr.text((pen, top + y_off + drop), ch, font=f, fill=fill)
        x += cw + spacing


def _items_total(items, spacing):
    return sum(cw for _, _, cw, _ in items) + spacing * max(0, len(items) - 1)


def spaced_text(im, text, font, top, spacing_em):
    """逐字排布模拟模板 letter-spacing，并加 0 7px 18px rgba(0,0,0,.96) 投影。

    中英混排（2026-10-05）：拉丁/数字走 Arial Bold、· 半宽居中，其余不变。
    """
    spacing = font.size * spacing_em
    items = _mixed_items(text, font)
    x0 = (W - _items_total(items, spacing)) / 2
    sil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_items(ImageDraw.Draw(sil), items, x0, top, spacing, (0, 0, 0, 255), drop=7)
    soft, tight = _shadow_layers(sil)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_items(ImageDraw.Draw(layer), items, x0, top, spacing, (255, 255, 255, 255))
    im.alpha_composite(soft)
    im.alpha_composite(tight)
    im.alpha_composite(layer)


# 书名/作者字号与行位对齐 templates/shared-video-template 的 getTitleLayout：
# 片头结果页用的就是它，兜底渲染若写死 60px，短书名会在片头→正文切点出现字号跳变。
_TITLE_SIZE = max(46, min(82, math.floor(660 / max(1, len(TITLE_TEXT) + 1))))
# 作者行 = 书名顶(70) + 书名字号 + 26px 恒定行距，与 create-episode-preview.mjs 的
# getTitleLayout 同式同值（intro 结果页用的就是它）——两处不同步，intro→正文切点作者行会跳位。
# 旧的两档阶梯（≥70→166，否则 140）在 66~69px 书名上只剩 4px 行距，用户报「书籍信息行距太紧」。
_AUTHOR_TOP = 96 + _TITLE_SIZE
title_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
spaced_text(title_im, TITLE_TEXT, _title_font.truetype(_TITLE_SIZE), 70, 0.04)
spaced_text(title_im, AUTHOR_TEXT, _title_font.truetype(34), _AUTHOR_TOP, 0.06)
print(f"[title] {TITLE_TEXT} {_TITLE_SIZE}px / {AUTHOR_TEXT} top={_AUTHOR_TOP}"
      f"  字体={_title_font.describe(TITLE_FACE)}")
title_png = os.path.join(TMP, "title.png")
title_im.save(title_png)
# 书名层用的是哪一款 face，落盘成一份**渲染时**的证人（scripts/check-title-font.py 读它）。
# 只把 face 记在 lib 里证明不了它进了成片：本仓库已经两次栽在「源改了、盘上还是旧像素」
# （showBookMark 已在生成器里而 hook.mp4 整段印着书名 / hook.mp4 没重渲而成片用旧帧）。
with open(os.path.join(TMP, "title-meta.json"), "w", encoding="utf-8") as _f:
    json.dump({
        "font": {"family": TITLE_FACE["family"], "style": TITLE_FACE["style"],
                 "file": TITLE_FACE["path"], "index": TITLE_FACE["index"],
                 "source": TITLE_FACE["source"]},
        "title": {"text": TITLE_TEXT, "size": _TITLE_SIZE, "top": 70, "spacing_em": 0.04},
        "author": {"text": AUTHOR_TEXT, "size": 34, "top": _AUTHOR_TOP, "spacing_em": 0.06},
        "title_png_sha256": hashlib.sha256(open(title_png, "rb").read()).hexdigest(),
    }, _f, ensure_ascii=False, indent=2)

# 常驻声明「内容为书评与感悟[全角空格]素材来源于网络」：文案、字号、灰度、下边距全部取自
# scripts/lib/disclaimer.json（lib/disclaimer.py 是唯一画字入口），验收门禁
# check-disclaimer-overlay.py 用同一个 overlay() 生成预期墨迹——两边各画一遍就是
# 「渲染读一份、门禁读另一份」的那个老分叉口。
# 它和书名层一样是**全段常驻**的一层：单独一张 PNG 在合成时叠一次，不进逐帧字幕管道，
# 所以不会跟着场景淡入淡出出现或消失（那会变成判据⑤看得见的一次单帧内容交换）。
import disclaimer as _disclaimer  # noqa: E402

DISCLAIMER_CFG = _disclaimer.load()
DISCLAIMER_GEO = _disclaimer.geometry(DISCLAIMER_CFG, (W, H))
disclaimer_im = _disclaimer.overlay(DISCLAIMER_CFG, (W, H))
disclaimer_png = os.path.join(TMP, "disclaimer.png")
disclaimer_im.save(disclaimer_png)
import hashlib  # noqa: E402  # 复用凭证见下方 REUSE_BODY

_disclaimer_sha = hashlib.sha256(disclaimer_im.tobytes()).hexdigest()
print(f"[disclaimer] {_disclaimer.display_text(DISCLAIMER_CFG)} "
      f"{DISCLAIMER_GEO['font_size']}px / 灰度 {DISCLAIMER_CFG['color_rgba'][:3]} "
      f"@ bottom {DISCLAIMER_GEO['bottom']}px → band y {DISCLAIMER_GEO['band'][1]}.."
      f"{DISCLAIMER_GEO['band'][3]}（字幕取样带 680..850 之外）")

# 本书封面图（用户 2026-10-07「增加本书的封面图，从微信读书或网上获取，在固定开场白结束后
# 跟水波效果一同出现」，并在追问里选了「只跟水波那 1.4 秒」）：它只属于正文段的**起幅**——
# 在水声起播那一帧到达不透明，在涟漪收口那一帧开始离开，第一张氛围图站稳之前它必须已经走完。
# 三个时刻全部推导（lib/book_cover.timing）：淡入长度 = 水波音效相对切点的偏移
# （lib/water-ripple.json），退场起点 = 涟漪收口，退场长度 = 场景交叉溶解时长 XFADE。
# 于是淡入与淡出产生的逐帧变化都整段落在水波窗口 [offset, offset+dur) 之外——那道门量的是
# 涟漪的径向逐帧差质心，窗口里多一块局部淡入/淡出会把它拽偏（同「溶解不得与水波同窗」那条
# 注释的理由）。退场为什么跟着涟漪而不是跟着桥接卡：桥接卡多长是本期切镜表的事，跟着它就
# 成了每期一个时长、并向用户否决掉的「常驻整段」漂移。卡框与投影见 lib/book-cover.json。
_COVER_CFG = _cover_load()
_cover_src = _cover_source_path(EP, _COVER_CFG)
cover_png, COVER_T, _cover_sha = None, None, "none"
# 复用凭证（叠层字节 + 动效 + 时刻）与叠层字节是两份东西：见下面 `_cover_cred` 的理由。
_cover_cred = "none"
if not _cover_src:
    print(f"[cover] ⚠️ 本期没有封面图（episodes/<书名>/{_COVER_CFG['asset']['filename']}）："
          f"这一层跳过。取图：python3 scripts/fetch-book-cover.py \"<书名>\"（微信读书优先）。"
          f"交付门禁 14 对缺这层的期数打印 ⚠️ 无从证明，新渲期 --require 判红。")
else:
    _cov_ok, _cov_note = _cover_provenance_ok(EP, _COVER_CFG)
    if not _cov_ok:
        raise SystemExit(f"[cover] 封面图旁证核对未通过：{_cov_note}"
                         f"（图上屏的前提是它溯源得到，同 Pexels 那套标记链的理由）")
    _bridge_exit = layer_start[1] if len(layer_start) > 1 else round(_RIPPLE_END, 3)
    COVER_T = _cover_timing(_bridge_exit, XFADE, _COVER_CFG)
    cover_im = _cover_overlay(_cover_src, _COVER_CFG, (W, H))
    cover_png = os.path.join(TMP, "book-cover.png")
    cover_im.save(cover_png)
    _cover_sha = hashlib.sha256(cover_im.tobytes()).hexdigest()
    # 复用凭证必须把**动效与时刻**一起算进去，不能只记静态叠层字节。这一点不是补漏式的美化：
    # 2026-10-08 用户第二次要求「放大过程不流畅，抖动得厉害」之后，改的是逐帧缩放轨迹
    # （整数取整 resize + 线性 → 浮点仿射 AFFINE+BICUBIC + cosine、1.00→1.12 → 0.82→1.00），
    # 而 `overlay()` 画的那张图**一个字节都没变**——它是叠层的形状，缩放发生在每一帧上。
    # 所以只比 `_cover_sha` 的 REUSE_BODY 会把上一版抖动的像素判成「层对得上」复用回来，
    # 而这正是本仓库点名的「lib 改了 ≠ 像素改了」缺陷类在复用分支上的形状。
    _cover_cred = hashlib.sha256(json.dumps(
        {"overlay": _cover_sha,
         "motion": _cover_motion(_COVER_CFG),
         "timing": COVER_T}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    _cov_box = _cover_card_box(_cover_src, _COVER_CFG, (W, H))
    # 封面层的帧序列（逐帧按 zoom_at 画好，含淡入淡出与「出现时放大」动效）。
    # ffmpeg 的 scale 滤镜在 init eval_mode 下拒绝 t/n 表达式，只能 PIL 侧打帧——
    # 生成器（tab 渲染）只合成一遍，是稳定的；改时刻/换图就得重建这个目录。
    from book_cover import cover_frame_sequence as _cover_frames
    _cover_seq = _cover_frames(cover_im, COVER_T, FPS, body_dur,
                               os.path.join(TMP, "cover-seq"), _COVER_CFG)
    print(f"[cover] 帧序列 {_cover_seq['count']} 帧 → {os.path.relpath(_cover_seq['dir'], ROOT)}")
    # 渲染时的证人（同 title-meta.json 的写法）：门禁拿 lib + body-bg.json 的切镜表**重算**
    # 这三个时刻，和这里记的对不上就是「lib 改了、成片还是旧的」。证人不是真源——
    # 它只是把「这一次合成到底用了什么」钉在盘上，免得事后凭记忆。
    with open(os.path.join(TMP, "book-cover.json"), "w", encoding="utf-8") as _f:
        json.dump({"bridge_exit_seconds": round(float(_bridge_exit), 3),
                   "scene_xfade_seconds": float(XFADE),
                   "timing": COVER_T, "card_box_px": list(_cov_box),
                   "overlay_sha256": _cover_sha,
                   "motion": _cover_motion(_COVER_CFG),
                   "source": os.path.relpath(_cover_src, EP)},
                  _f, ensure_ascii=False, indent=2)
    print(f"[cover] {os.path.relpath(_cover_src, ROOT)} → 卡框 x {_cov_box[0]}..{_cov_box[2]} "
          f"y {_cov_box[1]}..{_cov_box[3]}（旁证 {_cov_note}）")
    print(f"[cover] 淡入 0→{COVER_T['fade_in_seconds']}s（水波起振那一帧到不透明）/ "
          f"{COVER_T['fade_out_at']}s +{COVER_T['fade_out_seconds']}s 随涟漪收口淡出 / "
          f"{COVER_T['gone_at']}s 起画面无封面（本期桥接卡 {COVER_T['bridge_exit']:.2f}s 退场）")

TRAILING_PUNCT = "，。、；：！？…—·”’）》"
# 「——」是成对字符：只能在完整的破折号之后断条，绝不能把两根横线劈开（否则半根上屏）。
CLAUSE_RE = re.compile(r"(?<=[，。、；：！？…])|(?<=——)")
# 默认 24：两条 12 字短句合并上屏。长分句的本期（如《围城》整段合并的 CSV 行）
# 用 CAP_MAX_UNIT 调小，否则 pack_units 会凑出 17 字的单行，被 build_caption 缩到 38px，
# 与邻居字幕的 50/56px 明显不一致。
MAX_UNIT = int(os.environ.get("CAP_MAX_UNIT", "24"))
CLAUSE_PUNCT = "，。、；：！？…—"
# 断行硬约束：闭合标点不能起头，开引号/书名号不能收尾，词中间不硬切。
BAD_LINE_START = set("”’）》。！？，、；：的了着地得吗呢吧啊之")
# 单字虚词/限定词收尾会把「最/令人心痛」「得不/到」「有/一天」这类结构劈开，视为次优断点。
BAD_LINE_END = set("地的得着过于和与及或就也都更最很却并且而但向从到以之其此让被使令有你在这那由用不出的")
# jieba 不可用时的兜底词表（只保证不把常见双字词切断）
NO_SPLIT = {"只能", "可能", "能够", "才能", "得不到", "一个", "某个", "于是", "所以", "因为", "无法",
            "执着", "错过", "值得", "今天", "重读", "地方", "存在", "回忆", "一段", "人生",
            "不是", "也是", "就是", "还有", "这样", "那样", "自己", "我们", "你们", "他们",
            "成为", "怎样", "如何", "必须", "需要", "可以", "经历", "回应", "愿望", "终于"}

try:                                            # 词边界优先用 jieba；缺失时退回虚词启发式
    import jieba

    jieba.setLogLevel(60)
except Exception:                               # noqa: BLE001
    jieba = None

# 译名保护：jieba 会把「替哈丽特」切成「替哈丽 / 特」（《爱玛》order 26 实测被劈成
# 她心里已经替哈丽 / 特选好了牧师埃尔顿），词边界机制本身拦不住这种误切，因为断点
# 在 jieba 眼里正好是边界。所以本期不得跨行的词（译名、数量短语等）由 brief.json 的
# `terms` 显式声明，这里既注册进 jieba 词典（改边界），又在 _can_break 里做硬 mask
# （兜底 + 供预检报警）。
PROTECTED_TERMS = []
for _t in list(brief.get("terms", [])) + [brief.get("display_title", ""), brief.get("author", "")]:
    for _w in re.split(r"[·、,，\s/]+", str(_t or "")):
        if len(_w) >= 2 and _w not in PROTECTED_TERMS:
            PROTECTED_TERMS.append(_w)
if jieba is not None:
    for _w in PROTECTED_TERMS:
        jieba.add_word(_w, freq=1_000_000)      # 高词频保证整块不被切开

_term_mask_cache = {}


def _term_mask(body):
    """mask[j] 为真表示断点 j 落在某个受保护词的中间（首尾边界不算）。"""
    got = _term_mask_cache.get(body)
    if got is None:
        mask = [False] * (len(body) + 1)
        for w in PROTECTED_TERMS:
            start = body.find(w)
            while start != -1:
                for j in range(start + 1, start + len(w)):
                    mask[j] = True
                start = body.find(w, start + 1)
        got = tuple(mask)
        _term_mask_cache[body] = got
    return got


_edge_cache = {}


def _edges(s):
    """返回 s 的词边界下标集合（j 表示可在 s[j-1] 与 s[j] 之间断行）；jieba 不可用时返回 None。"""
    if jieba is None:
        return None
    got = _edge_cache.get(s)
    if got is None:
        pos, acc = set(), 0
        for w in jieba.cut(s):
            acc += len(w)
            pos.add(acc)
        got = frozenset(pos)
        _edge_cache[s] = got
    return got


# 成对标点（书名号/引号/括号）：断点绝不能落在内部，否则「这就是《小 / 公务员之死》」
# 这样会把书名劈成两行——jieba 会把「小」「公务员」当成两个词，只靠词边界拦不住。
_BRACKET_PAIRS = {"《": "》", "“": "”", "‘": "’", "（": "）", "「": "」", "【": "】"}
_bracket_cache = {}


def _bracket_mask(body):
    """mask[j] 为真表示下标 j 处在某个未闭合的成对标点内部。"""
    got = _bracket_cache.get(body)
    if got is None:
        stack, mask = [], [False] * (len(body) + 1)
        for i, ch in enumerate(body):
            mask[i] = bool(stack)
            if ch in _BRACKET_PAIRS:
                stack.append(_BRACKET_PAIRS[ch])
            elif stack and stack[-1] == ch:
                stack.pop()
        mask[len(body)] = bool(stack)
        got = tuple(mask)
        _bracket_cache[body] = got
    return got


def _can_break(body, j):
    """j 是下一行的起始下标；判断能否在 j 处断行。"""
    if not (0 < j < len(body)):
        return False
    if body[j] in BAD_LINE_START or body[j - 1] in "《“‘（":
        return False
    if _bracket_mask(body)[j]:
        return False
    if _term_mask(body)[j]:                     # 译名/书名内部不断行
        return False
    # 「——」成对：不能切在两根横线之间，也不能只把半根留在上一行末尾。
    if body[j - 1] == "—" and body[j] == "—":
        return False
    # 数字与紧随其后的量词是一个整体：「19/世纪」「2021/年」这样断会读成半截数字。
    if body[j - 1] in "0123456789":
        return False
    ed = _edges(body)
    if ed is not None and j not in ed and body[j - 1] not in CLAUSE_PUNCT:
        return False
    if body[j - 1] + body[j] in NO_SPLIT:
        return False
    return True


# 断点代价：分句标点最优，干净词边界次之（轻微失衡可接受），虚词收尾尽量避开。
TIER_PENALTY = {1: 0.0, 2: 3.0, 3: 9.0}


def _tier_of(body, j):
    if body[j - 1] in CLAUSE_PUNCT:
        return 1                                # 分句标点之后最自然
    if body[j - 1] in BAD_LINE_END:
        return 3                                # 虚词收尾：能避开就避开
    return 2


def pick_break(s, lo, hi):
    """在平衡区间附近挑断点：分句标点 > 干净词边界 > 虚词边界，同级再看与中点的距离。"""
    lo, hi = max(1, lo), min(hi, len(s) - 1)
    mid = (lo + hi) // 2
    best, best_key = None, None
    for j in range(max(1, lo - 4), min(len(s) - 1, hi + 4) + 1):
        if not _can_break(s, j):
            continue
        tier = _tier_of(s, j)
        key = (abs(j - mid) + TIER_PENALTY[tier], tier, abs(j - mid))
        if best_key is None or key < best_key:
            best, best_key = j, key
    if best is None:                            # 区间内外都没有合格断点：放宽到整句
        pool = [j for j in range(1, len(s)) if _can_break(s, j)]
        if not pool:
            return max(1, min(mid, len(s) - 1))
        return min(pool, key=lambda j: abs(j - mid))
    return best


def _balance_lines(text):
    """没有断点号可依赖时的折行：每行约 12 字（>26 字才允许第 3 行）；
    13 字以内保持单行（50px 档容量 652/50=13），更长的均衡折成两行。

    旧阈值是 14：「14 字以内单行靠字号自适应」实际把 14 字行缩到 46px、
    16 字行缩到 40px（《少年Pi的奇幻漂流》order 38/61 实测 42px 字高），
    字幕字号在 56/50 之间来回跳——2026-10-05 用户报「排版」后收进阶梯。"""
    body = text.strip()
    if len(body) <= 13:
        return [body]
    lines = []
    rest = body
    while rest and len(lines) < 2:
        slots = 3 if len(rest) > 26 else 2
        target = math.ceil(len(rest) / slots)
        if len(rest) <= 13:
            break
        lo = max(5, target - 2)
        hi = min(len(rest) - 5, target + 2)
        if hi < lo:
            break
        j = pick_break(rest, lo, hi)
        lines.append(rest[:j])
        rest = rest[j:]
    lines.append(rest)
    return [ln for ln in lines if ln] or [body]

# 断点号即换行点：真源是 lib/subtitle-punctuation.json 的 displayBreak。
# phrase 模式 CSV 用空格预排视觉行，空格在这里和断点号同权——两者都只是行分隔，
# 屏上任何时候都不留空白。
LINE_END_STRIP = "".join(BREAK_CHARS)
_MARK_RUN_RE = re.compile(r"[\s" + re.escape(LINE_END_STRIP) + r"]+")
MAX_CAPTION_LINES = MAX_LINES


def _segments(text):
    """[(段, 该段末尾的断点号)]：断点号先留着，上屏前才由 caption_display 剥掉。

    留着不是为了画它，是因为预检要判断「断点落在词边界还是劈开了词」——判据是
    `joined[j-1] in CLAUSE_PUNCT`，把逗号抹掉之后再拼，断点前一个字就变成了正文的「事」，
    于是每一处按标点断的行都会被误报成劈词（改这条规则时实测 3 处假阳性）。
    """
    parts = _MARK_RUN_RE.split(text)
    marks = _MARK_RUN_RE.findall(text)
    out = []
    for i, seg in enumerate(parts):
        if not seg:
            continue
        out.append((seg, marks[i] if i < len(marks) else ""))
    return out


def caption_lines(text):
    """一条字幕的视觉行（行尾断点号保留）：先按断点号/空格硬拆段，再折超长的单段。

    2026-09-27 之前这里是「先按字数平衡折行，再把行中断点号换成一个全角空格」，
    于是屏上出现「漂亮[全角空格]聪明[全角空格]有钱」这种间隙（这里刻意不写出那个字符：
    静态守卫 `npm run check` 会盯住代码里的全角空格），而且到底会不会在间隙处折行由容器宽度
    决定，同一句文案在 Pillow 与 HyperFrames 两条路径上会排出两种版。现在断点号本身就是
    换行点：先拆段，每段各自折行，屏上零标点、零空白。一条字幕装不下 MAX_CAPTION_LINES
    行以上的段，由 pack_units 顺延成下一条字幕，而不是缩字号或留间隙。
    """
    lines = []
    for seg, mark in _segments(str(text).strip()):
        wrapped = _balance_lines(seg)
        wrapped[-1] += mark
        lines.extend(wrapped)
    return lines or [str(text).strip()]


def caption_display(text):
    """折行 → 标点归位 → 去标点，得到真正上屏的那几行（每行零标点、零空白）。

    build_caption 与 CAPS_ONLY 预检共用；返回值就是画面上看到的那几行。
    """
    lines = [ln.rstrip(LINE_END_STRIP) for ln in caption_lines(text)]
    # 闭合标点不单独出现在行首：并回上一行行尾
    while len(lines) >= 2 and lines[1][:1] in "”’）》。！？，、；：":
        lines[0] += lines[1][0]
        lines[1] = lines[1][1:]
        if not lines[1]:
            lines.pop(1)
    # 折行完成后才去标点（去早了就没有断句依据了）。清洗后仍带换行说明这行还得再拆。
    out = [piece for ln in lines for piece in split_display_lines(ln)]
    return out or split_display_lines(text)


# （去标点的真源在 scripts/lib/subtitle-punctuation.json，
#  上屏折行与剥字见上方 caption_lines / caption_display。）


MIN_PIECE = 6         # 无标点长行的最小切片长度，防止切出「烦」这种孤字条


def split_long_clause(clause):
    """无标点的超长子句：按词边界均分，保证每条 ≤ MAX_UNIT 且不留孤字尾巴。

    正常口径下 CSV 保留句读，`pack_units` 会先按 `CLAUSE_RE` 分句，这里只兜底处理
    整行没有分句标记的情况（如 `strip-subtitle-symbols.py --strip-punctuation` 那类
    历史期）。旧写法 `while len(rest) > size` 会在余下 12 字时按固定 size=11 硬切，
    留下 1 个字的孤条（《素食者》第 7 行「…带来了麻 / 烦」实测）。改成：只在超过
    MAX_UNIT 时继续切，且断点区间两端都留出 MIN_PIECE 的余量。
    """
    n = math.ceil(len(clause) / MAX_UNIT)
    size = math.ceil(len(clause) / n)
    out, rest = [], clause
    while len(rest) > MAX_UNIT:
        lo = max(MIN_PIECE, size - 2, len(rest) - MAX_UNIT)
        hi = min(len(rest) - MIN_PIECE, size + 2, MAX_UNIT)
        cut = pick_break(rest, lo, hi) if hi >= lo else size
        out.append(rest[:cut])
        rest = rest[cut:]
    out.append(rest)
    # 兜底：词边界全落在别处时 pick_break 仍可能留下孤字，直接并回上一条
    if len(out) > 1 and len(out[-1]) < MIN_PIECE:
        out[-2] += out[-1]
        out.pop()
    return [u for u in out if u]


def pack_units(text):
    """按标点切子句，再贪心打包成一条字幕。

    预算是**行数**而不是只有字数：断点号现在一律换成换行，一个分句就是屏上一行，
    所以一条字幕最多装 `MAX_CAPTION_LINES`（真源 displayMaxLines）行，装不下的分句
    顺延成下一条字幕。只按字数打包会重现旧缺陷——三条短句凑成 20 字挤在一条里，
    要么撑成 3 行顶穿字幕安全区，要么退回旧口径的空格间隙。字数上限仍然生效，
    它管的是「一行别太长」，行数上限管的是「别叠太多行」。
    """
    units = []
    for clause in (c for c in CLAUSE_RE.split(text) if c.strip()):
        pieces = [clause] if len(clause) <= MAX_UNIT else split_long_clause(clause)
        for piece in pieces:
            if units:
                merged = units[-1] + piece
                if (len(merged) <= MAX_UNIT
                        and len(caption_lines(merged)) <= MAX_CAPTION_LINES):
                    units[-1] = merged
                    continue
            units.append(piece)
    return [u.strip() for u in units if u.strip()]


def silencedetect(path, noise="-35dB", min_dur=0.18):
    """返回真停顿区间 [(start, end), ...]。中点单独派生，别丢区间——
    allocate_times 需要知道「这一行的末尾是不是压在停顿里」，只有中点算不出来。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                        f"silencedetect=noise={noise}:d={min_dur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start:\s*([-\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([-\d.]+)", r.stderr)]
    return [(s, e) for s, e in zip(starts, ends) if e > s]


_sil_src = next((os.path.join(AUDIO, n) for n in
                 ("body-voiceover.mp3", "body-voiceover-story.mp3", "配音.mp3")
                 if os.path.exists(os.path.join(AUDIO, n))), None)
silences = silencedetect(_sil_src) if _sil_src else []
pauses = [(s + e) / 2 for s, e in silences]
if pauses:
    print(f"[pauses] {_sil_src.split(os.sep)[-1]} 检出 {len(pauses)} 个停顿锚点")

# 第七道门禁 check-caption-speech-align.py 判 T2 用的是 lib/caption_speech 的自适应能量突发
# （阈值落在本期的噪声底与语音带之间，实测 -15.7 dBFS），不是这里的 -35dB silencedetect。
# 子条什么时候「空放」必须按门禁那套突发来算，否则渲染器自认为铺满了、门禁照样判红
# （《自深深处》2026-09-29：-35dB 看行25 末条有 44% 语音，门禁只认 26%）。
try:
    from caption_speech import speech_bursts as _speech_bursts
    bursts, _burst_thr = _speech_bursts(_sil_src) if _sil_src else ([], None)
    if bursts:
        print(f"[bursts] 自适应能量突发 {len(bursts)} 个（阈值 {_burst_thr:.1f} dBFS），"
              f"子条切点按门禁同一口径复核")
except Exception as _exc:                       # numpy/ffmpeg 缺失时不阻塞渲染，退回旧口径
    bursts = []
    print(f"[warn] 读不到 lib/caption_speech 的能量突发（{_exc}），子条时间只用 -35dB 停顿口径")


def voiced_span(a, b):
    return sum(max(0.0, min(b, b1) - max(a, b0)) for b0, b1 in bursts)


def row_speech_end(start, end):
    """这一行**念到**哪儿为止：窗口末尾那段静音不算它念的。

    轴为了阅读时间会把行尾铺进后面的停顿（audit 的 keep_visible / snap 的 0.60s hold），
    这段留白属于整行，不属于拆出来的最后一条字幕。按 end 摊字数会把内部切点整体推晚，
    于是上一条字幕盖住了下一条要念的话，下一条却在静音里空放
    （《克林索尔的最后夏天》2026-09-28 行70：切点 245.60 把「拼杀」留在上一条，
    「拼杀 / 死去」这条 1.48s 里只有 0.53s 有语音 → T2 36% 判红）。

    有门禁那套突发时用它：最后一个「起点落在窗口里」的突发末尾就是本行念到的地方
    （行25 → 100.49、行60 → 257.07、行64 → 274.06，与 audible_windows 逐行一致）。
    -35dB 停顿只能作兜底：它的尾停顿经常差 0.01~0.03s 就"没盖住 end"，行尾判定失效
    （行25 的停顿是 100.56–101.04，而轴把 end 卡在下一行开口 101.05）。
    """
    if bursts:
        ends = [b1 for b0, b1 in bursts if b0 < end and b1 > start]
        return min(end, max(ends)) if ends else end
    for s, e in silences:
        if s >= end:
            break
        if s > start and e >= end:
            return s
    return end


def allocate_times(units, start, end):
    """按字数比例分配子条时间，再把内部切换点吸附到最近的配音停顿。

    比例摊在**语音跨度** [start, row_speech_end] 上而不是摊在含阅读留白的窗口上。
    末条也收在语音跨度上（TAIL_HOLD 之后就是空白），不再一路铺到行尾：
    行级轴给整行留的那 ≤0.60s 阅读留白属于**整行**，一条一行时由它自己承担没问题，
    可一行拆出多条时，整段留白会全砸在最后一条上，而那条往往只有两三个字——
    《自深深处》2026-09-29 行25「很少」窗 1.25s 里只有 0.39s 有语音（T2 31%）、
    行60「四十六岁」36%、行64「已经很少见了」41%，三道 T2 空放全红。
    收在语音终点后，留白变成两条字幕之间的真空窗——落在真停顿里，
    audit 的 B（连读处掉字幕）不会因此判红，读不读得完由每条自己的地板兜。

    最后再按门禁口径复核一遍：哪一条的窗内语音占比低于 VOICED_MIN，就把它的起点在
    停顿两端重新挑一次（挑让相邻两条最平衡的那个突发边界），只在真的要判红时才动，
    不动没病的行。
    """
    n = len(units)
    span_t = end - start
    speech_end = row_speech_end(start, end)
    vspan = speech_end - start
    if vspan < span_t * 0.5:          # 语音跨度异常短就别用它摊比例，退回整窗
        vspan = span_t
    total_chars = sum(len(u) for u in units) or 1
    uniform = span_t / (n * 1.8)
    floors = [min(uniform, max(SUB_FLOOR, len(u) / SUB_RATE)) for u in units]
    # 收末条：只有「收完还撑得住每条自己的地板」才收，否则宁可让末条继续铺到行尾
    tail_end = min(end, speech_end + TAIL_HOLD) if (n > 1 and bursts) else end
    if tail_end - start < sum(floors):
        tail_end = end
    bounds = [start]
    acc = 0
    for u in units[:-1]:
        acc += len(u)
        bounds.append(start + vspan * acc / total_chars)
    bounds.append(tail_end)
    for i in range(1, n):
        lo = bounds[i - 1] + floors[i - 1]
        hi = bounds[i + 1] - floors[i]
        cands = [p for p in pauses if lo <= p <= hi and abs(p - bounds[i]) <= 0.9]
        if cands:
            bounds[i] = min(cands, key=lambda p: abs(p - bounds[i]))
    for i in range(1, n):            # 吸附后仍需单调且各条不短于自己的地板
        bounds[i] = max(bounds[i], bounds[i - 1] + floors[i - 1])
    bounds[-1] = tail_end
    for i in range(n - 1, 0, -1):
        bounds[i] = min(bounds[i], bounds[i + 1] - floors[i])

    if bursts and n > 1:
        def balance(i, p):
            l = voiced_span(bounds[i - 1], p) / max(1e-6, p - bounds[i - 1])
            r = voiced_span(p, bounds[i + 1]) / max(1e-6, bounds[i + 1] - p)
            return min(l, r), r

        for _round in range(3):
            moved = False
            for i in range(1, n):
                lo = bounds[i - 1] + floors[i - 1]
                hi = bounds[i + 1] - floors[i]
                if hi <= lo:
                    continue
                cur = min(max(bounds[i], lo), hi)
                if balance(i, cur)[0] >= VOICED_MIN:
                    continue
                cands = []
                for b0, b1 in bursts:
                    for edge in (b0, b1):
                        p = min(hi, max(lo, edge))
                        if abs(p - cur) <= 0.9:
                            cands.append(p)
                if not cands:
                    continue
                best = max(cands, key=lambda p: balance(i, p)[0])
                if balance(i, best)[0] > balance(i, cur)[0] + 0.02:
                    bounds[i] = best
                    moved = True
            if not moved:
                break
    return [(u, bounds[i], bounds[i + 1]) for i, u in enumerate(units)]



def build_caption(text, path):
    lines = caption_display(text)
    if not lines:
        raise SystemExit(f"[punct] 上屏文本为空（标点剥离把整条剥没了）：{text}")
    stray = {ch for ln in lines for ch in residual_punct(ln)}
    if stray:
        raise SystemExit(f"[punct] 上屏字幕残留标点 {''.join(sorted(stray))}：{text}")
    # 断点号现在一律换成换行，屏上不该再有任何空白：留空白就是本次规则要修的缺陷本身。
    gaps = {ch for ln in lines for ch in ln if ch.isspace()}
    if gaps:
        raise SystemExit(f"[gap] 上屏字幕仍有空白间隙（断点号必须换成换行）："
                         f"{text!r} → {lines}")
    if len(lines) > MAX_CAPTION_LINES:
        raise SystemExit(
            f"[lines] 一条字幕上屏 {len(lines)} 行 > displayMaxLines={MAX_CAPTION_LINES}："
            f"{text!r} → {lines}\n"
            f"       pack_units 的行数预算没兜住，就在 script-body.csv 里把这一行拆开。")
    size = 56 if max(len(ln) for ln in lines) <= SMALL_CHARS else 50
    font = _title_font.truetype(size)

    def _widest():
        # 行宽按混排口径量：拉丁段更窄（Arial），按字符数估会把 Pi 这类行提前缩号
        return max(_items_total(_mixed_items(ln, font), 0) for ln in lines)

    while _widest() > W - 68 and size > 36:
        size -= 2
        font = _title_font.truetype(size)
    line_h = round(size * 1.16)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    top = H - 126 - line_h * len(lines)
    for i, ln in enumerate(lines):
        y = top + i * line_h
        items = _mixed_items(ln, font)
        x0 = (W - _items_total(items, 0)) / 2
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        _draw_items(ImageDraw.Draw(sh), items, x0, y, 0, (0, 0, 0, 255), drop=7)
        for layer in _shadow_layers(sh):     # 与书名层同一套双通道投影
            im.alpha_composite(layer)
        _draw_items(ImageDraw.Draw(im), items, x0, y, 0, (255, 255, 255, 255))
    im.save(path)


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

# 一行配音可以出多条字幕：长行先按子句打包，切换点再吸附到配音真实停顿上。
# 打包预算是**上屏行数**：断点号换成换行之后，一个分句就是一行。
pieces = []
for c in caps:
    order = int(c["order"])
    s, e = float(c["start"]), float(c["end"])
    text = text_by_order.get(order, "")
    # 切条条件：超过行宽，或超过行数上限。只看 len(text) > MAX_UNIT 会漏掉
    # 「短而多分句」的行——20 字三个短句如今会撑成三行顶穿字幕区。
    # 已经用空格预排视觉行的 CSV（phrase 模式）在 2 行以内保持原样，不重复切分。
    units = (pack_units(text)
             if len(text) > MAX_UNIT or len(caption_lines(text)) > MAX_CAPTION_LINES
             else [text])
    while len(units) > 1 and (e - s) / len(units) < 0.7:   # 行太短撑不起多条，回退整行同屏
        merged = units[-2] + units[-1]
        if len(caption_lines(merged)) > MAX_CAPTION_LINES:
            break        # 合并会顶穿行数上限：宁可多切一条，也不把三行堆上屏
        units[-2] = merged
        units.pop()
    if len(units) == 1:
        pieces.append((order, text, s, e))
    else:
        pieces.extend((order, u, us, ue) for u, us, ue in allocate_times(units, s, e))

if CAPS_ONLY:   # 字幕切条预检：把每条字幕的实际上屏折行打印出来，人工核有无劈词/半根破折号
    bad = 0
    for order, text, us, ue in pieces:
        lines = caption_lines(text)
        flag = ""
        for ln in lines:
            if ln[:1] in TRAILING_PUNCT or ln[-1:] in "“‘《（(":
                flag = "  ⚠️标点悬空"
        disp = caption_display(text)          # 真正上屏的行（已按共享策略去标点）
        if any(residual_punct(ln) for ln in disp):
            flag += "  ⚠️上屏残留标点"
        if any(ch.isspace() for ln in disp for ch in ln):
            flag += "  ⚠️上屏留了空白间隙（应换成换行）"
        if len(disp) > MAX_CAPTION_LINES or max(len(ln) for ln in disp) > 14:
            flag += "  ⚠️行过长"
        # 劈词检测：断点既不在受保护词内，也必须落在词边界上，否则「替哈丽 / 特」
        # 这种会一路通过标点/行长检查、到成片抽帧才看得见。
        if len(lines) == 2:
            joined = "".join(lines)
            j = len(lines[0])
            if _term_mask(joined)[j]:
                flag += "  ⚠️劈开保护词"
            else:
                ed = _edges(joined)
                if ed is not None and j not in ed and joined[j - 1] not in CLAUSE_PUNCT:
                    flag += "  ⚠️断点非词边界"
        if flag:
            bad += 1
        print(f"  {order:>3} [{us:7.2f},{ue:7.2f}] {' / '.join(disp)}{flag}")
    print(f"[caps] {len(pieces)} 条，{bad} 条需复核")
    raise SystemExit(0)

# 字幕轨必须按「绝对帧号」锚定，不能逐条累加取整帧数：
# 累加式会把每条 ≤0.5 帧的舍入误差攒起来，101 条字幕的长正文后段会整体滞后
# 约 8-9 帧（270ms），抽帧核对时表现为「字幕比配音慢一行」（《小王子》已踩，见 memory）。
target = round(body_dur * FPS)


def frame_of(t):
    return max(0, min(target, round(t * FPS)))


entries = []
cursor = 0
# 落一份 caption-track.json：scripts/qc-caption-alignment.py 靠它做「画面上实际显示的字幕」
# 与「该时刻应显示的字幕」的 IoU 比对 —— 分段渲染错位那类问题必须这样量化才看得见。
# 先清掉上一版的 cap-*.png：字幕重切（改 CAP_MAX_UNIT 或改文本长度）后同一 index 会对应
# 不同 order，旧文件留在 TMP 里会被 QC 当候选，实测报出 86/212 条假 MISMATCH。
for stale in os.listdir(TMP):
    if stale.startswith("cap-") and stale.endswith(".png"):
        os.remove(os.path.join(TMP, stale))
track = []
_cap_style = caption_style()      # 「line-break@<版本>」：门禁据此判定这期是否按换行口径渲过
for i, (order, text, s, e) in enumerate(pieces):
    track.append({"index": i, "order": order, "text": text,
                  "display": " / ".join(caption_display(text)),
                  "captionStyle": _cap_style,
                  "start": round(s, 3), "end": round(e, 3)})
    f0, f1 = frame_of(s), frame_of(e)
    if f1 <= f0:
        f1 = min(target, f0 + 1)   # 极短条至少占 1 帧，避免帧数为 0 写坏管道
    if f0 > cursor:
        entries.append((f0 - cursor, transparent))
    p = os.path.join(TMP, f"cap-{i:03d}-{order}.png")
    build_caption(text, p)
    if f1 > f0 and f1 > cursor:
        entries.append((f1 - max(f0, cursor), p))
        cursor = max(cursor, f1)
json.dump(track, open(os.path.join(TMP, "caption-track.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
# 字幕轨是「画面上到底上了什么字」的唯一证据，但 TMP 会被清；所以在 episodes/<书名>/
# 也持久落一份，交付前门禁与全仓库审计都读它：
#   python3 scripts/check-caption-punctuation.py --all
json.dump(track, open(os.path.join(EP, "caption-track.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
if cursor < target:
    entries.append((target - cursor, transparent))
n_frames = sum(n for n, _ in entries)
if n_frames != target:
    raise SystemExit(f"字幕轨帧数 {n_frames} != 正文 {target} 帧（绝对帧号锚定失效）")
print(f"[caps] {len(caps)} 行 → {len(pieces)} 条字幕 / {n_frames} 帧 = {n_frames / FPS:.2f}s")

# REUSE_BODY=1：只换片头（如书名层字号/书名文本改了）时复用已验收的 body.mp4，
# 跳过几分钟的字幕层管道 + 正文合成。正文画面只依赖 INTRO_TRIM，不依赖片头内容，
# 所以片头重渲而 INTRO_TRIM 不变时复用是安全的；时长对不上会自动重做。
body_mp4 = os.path.join(TMP, "body.mp4")
REUSE_BODY = (os.environ.get("REUSE_BODY") == "1" and os.path.exists(body_mp4)
              and abs(probe_dur(body_mp4) - body_dur) < 0.2)
if REUSE_BODY:
    # 时长对得上不等于层对得上：上一次合成的 body.mp4 里可能没有常驻声明层
    # （或者层的文案/几何与本次 lib 渲出来的不是一份）。凭证缺失或不一致就重做正文，
    # 这正是本仓库「绿灯是旧的、缺陷是新的」那类缺陷的复用版本。
    _sha_file = body_mp4 + ".disclaimer-sha"
    _prev = open(_sha_file, encoding="utf-8").read().strip() if os.path.exists(_sha_file) else ""
    if _prev != _disclaimer_sha:
        print(f"[body] REUSE_BODY 取消：已有 body.mp4 的叠层凭证 "
              f"{'缺失' if not _prev else '与本次 lib 不一致'}（{_prev[:12] or '—'} vs "
              f"{_disclaimer_sha[:12]}）→ 重做正文，常驻声明层必须真的在像素里")
        REUSE_BODY = False
    # 封面层同一件事：复用的 body.mp4 里可能压根没有这一层（它是 2026-10-07 才加的），
    # 也可能叠的是上一版图。"盘上没凭证 + 本期也没有封面" 算一致——那正是这层还不存在的
    # 存量期，把一百多期一律打成待重做就是拿爆炸半径换心安。
    # 比的是 `_cover_cred`（叠层字节 + 动效 + 时刻）而不是 `_cover_sha`：动效改的是逐帧
    # 缩放轨迹，静态叠层一个字节都不变，只比叠层字节就会把上一版抖动的像素复用回来。
    _cov_file = body_mp4 + ".book-cover-sha"
    _cov_prev = open(_cov_file, encoding="utf-8").read().strip() if os.path.exists(_cov_file) else ""
    if _cov_prev != _cover_cred and not (_cov_prev == "" and _cover_cred == "none"):
        print(f"[body] REUSE_BODY 取消：已有 body.mp4 的封面层凭证 "
              f"{'缺失' if not _cov_prev else '与本次不一致'}（{_cov_prev[:12] or '—'} vs "
              f"{_cover_cred[:12]}）→ 重做正文，本书封面必须真的在像素里"
              f"（叠层字节／动效／时刻任一不对上都算不一致）")
        REUSE_BODY = False
if REUSE_BODY:
    print(f"[body] REUSE_BODY: {body_mp4} {probe_dur(body_mp4):.2f}s")
else:
    sub_mov = os.path.join(TMP, "sub.mov")
    pipe = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "argb", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", sub_mov],
        stdin=subprocess.PIPE)
    current_path, current_raw = None, None
    for n, p in entries:
        if p != current_path:
            current_raw = Image.open(p).convert("RGBA").tobytes()
            current_path = p
        for _ in range(n):
            pipe.stdin.write(current_raw)
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"字幕层管道失败: {pipe.returncode}")

    _layers = ["-i", bg, "-i", sub_mov, "-i", title_png, "-i", disclaimer_png]
    if cover_png:
        # 封面层是一条自己的流：带 alpha 的淡入淡出必须作用在**有帧序列**的输入上，
        # 单张图片只有一帧、fade 的 st/d 永远等不到，所以 -loop 1 按正文时长铺满。
        # 它叠在 bg 之上、书名/字幕/声明之下——卡框由 lib/book-cover.json 保证与那三条带
        # 不相交，谁压着谁都不抢字；而 body-bg.mp4 里**没有**这层，交付门禁 14 才能拿
        # 「成片帧 − 同期 body-bg 帧」当残差墨迹来验它真的上了屏。
        _layers += ["-framerate", str(FPS),
                    "-i", os.path.join(TMP, "cover-seq", "f_%04d.png")]
        # 透明度动效已经按 zoom_at 公式烘进 PNG 序列里了，overlay 直接接、不再加 fade 滤镜：
        # fade 之前是对 static PNG 的 alpha 通道乘时变系数，和帧序列的 PNG alpha 同一回事，
        # 两处一起做反而会双倍衰减。
        _chain = (f"[4:v]format=rgba[cov];"
                  f"[0:v][cov]overlay=0:0:format=auto[c0];"
                  f"[c0][1:v]overlay=0:0:format=auto[t];"
                  f"[t][2:v]overlay=0:0:format=auto[t2];"
                  f"[t2][3:v]overlay=0:0:format=auto,format=yuv420p[v]")
    else:
        _chain = ("[0:v][1:v]overlay=0:0:format=auto[t];[t][2:v]overlay=0:0:format=auto[t2];"
                  "[t2][3:v]overlay=0:0:format=auto,format=yuv420p[v]")
    ff(_layers + ["-filter_complex", _chain,
                  "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
                  "-crf", "18", "-preset", "medium", body_mp4])
    # 复用凭证：body.mp4 里到底有没有这一层，不能靠「我记得这次加了」——
    # REUSE_BODY=1 复用的正是**上一次**合成的文件，那一次可能根本没有常驻声明层
    # （《恶意》2026-10-01 的钩子旧像素就是同一类缺陷）。所以把本次叠层的字节 sha 记在
    # 旁边，下次复用前先比对；对不上或没有凭证就重做正文。
    with open(body_mp4 + ".disclaimer-sha", "w", encoding="utf-8") as _f:
        _f.write(_disclaimer_sha)
    # 封面层单独记一份：它和常驻声明是两个独立的可缺层（存量期没有封面是合法的，
    # 声明是 12 期门禁后才有的），一份凭证只能证一层。"本期没有封面" 写 "none" 而不是不写，
    # 这样"缺凭证"和"凭证为无"在上面的复用检查里是两种可读的状态，不会互相冒充。
    # 写的是 `_cover_cred`（叠层字节 + 动效 + 时刻）而不是 `_cover_sha`：这一层真正上屏的是
    # `cover_frame_sequence()` 按 zoom_at 逐帧画出的序列，只比静态叠层字节的话，改完动效
    # 复用旧 body.mp4 会一路绿到门禁 14 才红——而那时唯一的修法本来就是重渲。
    with open(body_mp4 + ".book-cover-sha", "w", encoding="utf-8") as _f:
        _f.write(_cover_cred)
    print(f"[body] {probe_dur(body_mp4):.2f}s")


# ------------------------------------------- 3. 钩子段与片头（本期 HyperFrames 渲染）
# 钩子→片头那道缝是**钩子段尾淡出到黑 + 硬 concat**：共享片头按 2026-10-03 的原样整段搬用，
# 不在它头上做溶解或垫帧。2026-10-05 11:39「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」、
# 13:30「三处全回滚（视觉 100% 回到从前）」、14:46「重新恢复固定开场白，保留原始效果」三次同向裁决
# 之后，片头重新以「0.13s 全黑起幅 + ~0.47s 飞到亮度平台」开头——那是原效果的一部分，不许在片头上
# 动刀。它前面是本期那张均亮度 40~60 的钩子照片，硬切过去就是 2026-10-04 第一次量到的那次眨眼
# （《嫌疑人X的献身》成片第 8.27s 单帧 42.4→0.0，判据①）。让钩子自己沉到黑去接片头的黑起幅，
# 两侧同暗，接缝就没有可翻的案。
# 淡出长度只有 lib 一份真源（hook_fade_out_seconds），第 10 道门的豁免窗读同一个数；它必须
# ≥ 判据④的时间尺度 0.35s，否则这段淡出本身会被数成「去了又回」——两头都由
# scripts/tests/test-hook-seam-fade.py 按死。13:04 那轮「只改过渡形状」的授权（亮面起幅 + 就地
# 等长溶解）已被 14:46 那句收回，旧件在 tmp/retired-20261005c/。
hook_mp4 = os.path.join(TMP, "hook.mp4")
HOOK_FADE = float(INTRO_TIMING.get("hook_fade_out_seconds", 0.0))
if hook_dur:
    if HOOK_FADE and not (0.0 < HOOK_FADE < INTRO_TRIM):
        raise SystemExit(f"hook_fade_out_seconds={HOOK_FADE} 必须落在 (0, INTRO_TRIM={INTRO_TRIM}) 内，"
                         f"否则段尾淡出会吃掉整段钩子，或者长度根本不合法")
    # 钩子段和正文同源同规格：同样 720x960/30fps，裁到配音时长，不带自己的音轨（配音在混音里排）。
    # fade 的 st 相对 setpts 归零之后的**这一段**，所以是 hook_dur − HOOK_FADE。
    # fps 必须在 trim **之前**：钩子渲染是 15fps，先 trim 再升帧会在 1/15s 网格上取整，
    # 视频比音频时间线长出至多 2 帧，正文整体后移——《乞力马扎罗的雪》2026-10-07 换钩子后
    # 水波声画差 +2.05 帧（容差 ±2.0），正是这 2 帧。先归一到 30fps 再 trim，裁切误差 ≤1 帧，
    # 涟漪首帧与音效点回到同一帧窗。
    hook_vf = f"fps={FPS},trim=0:{hook_dur},setpts=PTS-STARTPTS,scale={W}:{H},format=yuv420p"
    if HOOK_FADE:
        hook_vf += f",fade=t=out:st={round(hook_dur - HOOK_FADE, 3)}:d={HOOK_FADE}"
        print(f"[hook-fade] 段尾淡出 {HOOK_FADE}s（st={round(hook_dur - HOOK_FADE, 3)}）→ 接片头的黑场起幅")
    ff(["-i", HOOK_SOURCE, "-vf", hook_vf,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", hook_mp4])
if not os.path.exists(INTRO_SOURCE):
    raise SystemExit(f"缺少 HyperFrames 片头渲染结果: {INTRO_SOURCE}")
intro_mp4 = os.path.join(TMP, "intro.mp4")
ff(["-i", INTRO_SOURCE,
    "-vf", f"trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p",
    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", intro_mp4])


# ------------------------------------------------------------------- 4. 混音合成
intro_story = os.path.join(ASSETS, "template-audio", "intro-voiceover-story.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
for p in (intro_story, body_story):
    if not os.path.exists(p):
        raise SystemExit(f"缺少配音：{p}")
# 共享 BGM 的文件名被手工改过（全角/半角括号不一致），按目录枚举解析避免硬编码失配
bgm_dir = os.path.join(ASSETS, "bgm")
bgm_env = os.environ.get("BGM")
shared_bgm = sorted(
    os.path.join(bgm_dir, n) for n in os.listdir(bgm_dir) if n.lower().endswith(".mp3"))
bgm = os.path.join(bgm_dir, bgm_env) if bgm_env else (shared_bgm[0] if shared_bgm else None)
if not bgm or not os.path.exists(bgm):
    raise SystemExit(f"缺少共享 BGM：{bgm_dir} 下没有 mp3（BGM={bgm_env}）")
print(f"[bgm] {os.path.basename(bgm)}")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")

scroll_dur = round(SCROLL_END - SCROLL_START, 2)
bgm_atrim = round(final_dur - PRE_BODY, 2)
# 三段拼接：钩子（可缺省）→ 模板裁切段 → 正文。视频与配音都按同一个列表生成，
# 两段结构只是少一路，不留两条会各自漂移的分支。
video_parts = ([hook_mp4] if hook_dur else []) + [intro_mp4, body_mp4]
voice_parts = ([hook_voice_path] if hook_dur else []) + [intro_story, body_story]
ff_inputs = []
for _p in video_parts + voice_parts:
    ff_inputs += ["-i", _p]
ff_inputs += ["-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx]

fc = []
# 三段是**硬 concat**：钩子段已经在自己那一路淡到黑（上面 hook_vf 里的 fade），片头整段原样搬用，
# 正文起幅是本期自己的结果页桥接图。装配层不对共享片头做任何合成——这是 2026-10-05 裁决的本体。
fc.append("".join(f"[{i}:v]" for i in range(len(video_parts)))
          + f"concat=n={len(video_parts)}:v=1:a=0[v]")
next_input = len(video_parts)          # 视频占 0..n-1，其后依次是配音、BGM、两个音效
mix_labels = []
if hook_dur:
    fc.append(f"[{next_input}:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[hooka]")
    mix_labels.append("[hooka]")
    next_input += 1
fc.append(f"[{next_input}:a]asetpts=PTS-STARTPTS,aresample=48000,"
          f"adelay={HOOK_OFFSET_MS}|{HOOK_OFFSET_MS},volume=1.0[introa]")
mix_labels.append("[introa]")
next_input += 1
fc.append(f"[{next_input}:a]asetpts=PTS-STARTPTS,aresample=48000,"
          f"adelay={PRE_BODY_MS}|{PRE_BODY_MS},volume=1.0[bodya]")
mix_labels.append("[bodya]")
next_input += 1
fc.append(f"[{next_input}:a]atrim=0:{bgm_atrim},asetpts=PTS-STARTPTS,aresample=48000,"
          f"adelay={PRE_BODY_MS}|{PRE_BODY_MS},"
          f"volume={BGM_VOL},afade=t=in:st=0:d=0.5,afade=t=out:st={round(bgm_atrim - 0.8, 2)}:d=0.8[bgm]")
mix_labels.append("[bgm]")
next_input += 1
# 齿轮滚动音效相对「模板段起点」定位（1.08s 起、2.38s 止），有钩子就整体右移。
_scroll_delay_ms = round((hook_dur + SCROLL_START) * 1000)
fc.append(f"[{next_input}:a]atrim=0:{scroll_dur},asetpts=PTS-STARTPTS,aresample=48000,volume=1.4,"
          f"afade=t=in:st=0:d=0.01,afade=t=out:st={round(scroll_dur - 0.2, 2)}:d=0.2,"
          f"adelay={_scroll_delay_ms}|{_scroll_delay_ms}[scrollsfx]")
mix_labels.append("[scrollsfx]")
next_input += 1
fc.append(f"[{next_input}:a]atrim=0:{WATER_DUR},asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,"
          f"adelay={round(WATER_START * 1000)}|{round(WATER_START * 1000)}[watersfx]")
mix_labels.append("[watersfx]")
fc.append("".join(mix_labels)
          + f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]")

out_final = os.path.join(RENDERS, f"{EPISODE}-final.mp4")
candidate = os.path.join(TMP, f"{EPISODE}-final.mp4")
ff([*ff_inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", candidate])

# --------------------------------------------- 技术验收：通过后才激活到 renders/
# 与逐期兜底脚本一致：候选先落 TMP，分辨率/时长/音频流全过关才 move 进 renders/，
# 避免半成品覆盖掉已交付成片。
probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", candidate], capture_output=True, text=True).stdout)
v = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
a_stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
dur = float(probe["format"]["duration"])
assert v.get("width") == W and v.get("height") == H, f"分辨率错误: {v}"
assert a_stream, "缺少音频流"
assert abs(dur - final_dur) < 1.0, f"时长异常: {dur:.2f}s vs 计划 {final_dur:.2f}s"

# 片头归属校验：PREVIEW / INTRO_SOURCE 都有默认值，换期数时忘了传就会把**另一本书**的片头
# 接进来，而分辨率、时长、音频流、字幕 IoU 全都能过验收 —— 唯一暴露问题的是切点画面。
# 《蝇王》实测：误用默认（《美丽新世界》）片头时画面区 MAD 57.2 / 书名区 98.6，
# 用对本期的片头时 3.2 / 11.4（书名区那 11 是 DOM 文字与 Pillow 重绘书名的正常差异）。
def _frame_at(path, n):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                        "-vf", f"select='eq(n\\,{n})'", "-frames:v", "1",
                        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True)
    if r.returncode or len(r.stdout) < W * H * 3:
        raise SystemExit(f"切点校验取帧失败 {path}: {r.stderr.decode()[-200:]}")
    return Image.frombytes("RGB", (W, H), bytes(r.stdout[: W * H * 3]))


def _zone_mad(a, b, box):
    from PIL import ImageChops
    d = ImageChops.difference(a.crop(box), b.crop(box)).convert("L")
    hist = d.histogram()
    return sum(i * c for i, c in enumerate(hist)) / (d.width * d.height)


_cut = round(PRE_BODY * FPS)
# 三段结构下这个索引会差一两帧：钩子段和模板段是按**秒**裁的（trim=0:6.77 / 0:1.83），
# 再按 30fps 重编码，边界落在 259 而 round(8.60*30)=258。拿 258/259 比，量的其实是
# 滚动书单自己的翻页速度（《恶意》实测 20.4），于是本期正确的片头被判成「接错了书」。
# 所以先在切点附近几帧里找真正的那一跳，再对真正的边界下判断。
_best = (0.0, _cut)
for _n in range(max(1, _cut - 3), _cut + 4):
    _d = _zone_mad(_frame_at(candidate, _n), _frame_at(candidate, _n + 1), (0, 210, W, 680))
    if _d > _best[0]:
        _best = (_d, _n + 1)
_seam_pic, _cut = _best[0], _best[1]
_seam_title = _zone_mad(_frame_at(candidate, _cut - 1), _frame_at(candidate, _cut), (0, 55, W, 210))
print(f"[seam] 切点 {_cut / FPS:.2f}s 画面区 MAD={_seam_pic:.2f} 书名区 MAD={_seam_title:.2f}")

# 片头归属：INTRO_TRIM=1.83（对齐 1.83s 的共享开场白配音）时，切点前那一帧还是**六本书固定
# 书单**的翻页画面，每本书都一样 —— 切点 MAD 在这个裁切点上根本区分不了「哪一期的片头」，
# 阈值 12 只在裁到结果页（≥2.38s）时才是归属证据。所以三段结构改用一条精确的同源证明：
# 片头工程目录的 media/pages/result.png 就是 create-episode-preview 从本期 result-bridge.png
# 复制过去的定格页，两者必须逐字节相同；接了别的书的片头，这一步立刻红灯。
_result_png = os.path.join(os.path.dirname(os.path.dirname(INTRO_SOURCE)), "media", "pages", "result.png")
_bridge_png = os.path.join(IMG, "result-bridge.png")
if os.path.exists(_result_png) and os.path.exists(_bridge_png):
    _same = (open(_result_png, "rb").read() == open(_bridge_png, "rb").read())
    print(f"[seam] 片头定格页与本期 result-bridge 同源：{_same}（{_result_png}）")
    if not _same:
        raise SystemExit(
            f"片头归属校验失败：{_result_png} 与本期 {_bridge_png} 不是同一张图，"
            f"接进来的不是本期的片头。请显式传 INTRO_SOURCE，当前用的是 {INTRO_SOURCE}")
elif _seam_pic > 12.0:
    raise SystemExit(
        f"片头→正文切点跳变 {int(_seam_pic)}（阈值 12）：接进来的多半不是本期的片头。"
        f"请显式传 INTRO_SOURCE（本期片头渲染结果）后重跑，当前用的是 {INTRO_SOURCE}")
print(f"[check] {W}x{H} / {dur:.2f}s / 音频 {a_stream['codec_name']} 验收通过")

# 水波同窗校验（常驻规则，见 AGENTS.md「Visual And Audio Rules」）：water.mp3 在切点后起播，
# 画面必须同时出现一圈外扩涟漪。这道校验必须卡在 move 之前——「只加声音不加画面」是唯一
# 一个分辨率/时长/音频流/字幕/切点全部照常通过的缺陷：成片看起来完全正常，只有量化门禁拦得住。
# 门禁自己 import lib 的 WATER_OFFSET/WATER_DUR，所以这里只传切点与帧率，不传任何秒数。
# 封面同窗时传的是**这份证人**（render 时刻的 timing + card_box + motion），不是「请排除封面」
# 这一句意图：门据此逐帧重算卡片真正盖住的足迹，把卡片动效从涟漪质心里摘出去。证人就是上面
# 写盘的那份，所以「lib 改了、成片还是旧的」和「门按当下的 lib 猜了一张并不存在的动效」两边都堵住。
_water_gate = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "check-water-ripple.py"),
     candidate, "--intro-trim", str(PRE_BODY), "--fps", str(FPS)]
    + (["--cover-witness", os.path.join(TMP, "book-cover.json"), "--exclude-cover"]
       if _cover_src else []),
    capture_output=True, text=True)
print(_water_gate.stdout.strip())
if _water_gate.returncode != 0:
    raise SystemExit(
        "水波同窗门禁未通过，候选不激活到 renders/："
        "水声与可见涟漪必须落在同一帧窗口。查背景层是否调了 lib 的 ripple()、"
        "混音是否引了 assets/sfx/water.mp3。已叠字幕的成片不能事后补波（会扭糊字幕），"
        "必须重渲正文背景（FORCE_BG=1）。\n" + (_water_gate.stderr or "")[-500:])
shutil.move(candidate, out_final)
print(f"[final] {out_final} {probe_dur(out_final):.2f}s")


# --------------------------------------------------------------- 5. 封面（1080x1920）
# 封面统一交给 scripts/make_episode_cover.py：那才是本系列的封面规格（result-bridge 铺满、
# 底部渐变压暗、书名按画幅自适应并可折行、tag + 钩子两行落在 1540/1620）。
# 这里以前内嵌过一段自己的封面代码：书名顶到画幅上方 250px、没有钩子行，
# 于是《恶意》2026-10-01 交出的封面只有「《恶意》/ 东野圭吾 / 著」——
# 而 brief.json 里的 cover_hook 是这一期封面与开头三秒同主题的那句话，漏掉它等于
# 把 AGENTS.md 的封面规则整条丢掉。内嵌副本还会和通用版各自漂移，所以只留一条路径。
cover_out = os.path.join(RENDERS, f"封面-{EPISODE}.png")
_cover_script = os.path.join(ROOT, "scripts", "make_episode_cover.py")
if os.path.exists(_cover_script):
    _env = dict(os.environ, EPISODE=EPISODE, COVER_OUT=cover_out)
    _rc = subprocess.run([sys.executable, _cover_script], env=_env,
                         capture_output=True, text=True)
    print(_rc.stdout.strip())
    if _rc.returncode != 0:
        raise SystemExit(f"封面生成失败：\n{_rc.stderr.strip()}")
else:
    raise SystemExit(f"缺封面脚本：{_cover_script}")
print(f"[cover] {cover_out}")
