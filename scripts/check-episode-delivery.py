#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一期成片的交付门禁总入口：一条命令跑完「交付前必须过的那几道门」，缺任何一道即红灯。

为什么需要它：水波同窗、字幕去标点、背景图溯源这三条规则早就写进 AGENTS.md 和手册，
各自也都有独立门禁（`check-water-ripple.py` / `check-caption-punctuation.py` /
`check-image-provenance.py`），但交付时靠人记着挨个跑。《围城》2026-09-26 首版就是这么翻车的：
用了并行会话改到一半的共享渲染器出片，三项全违（AI 带水印图、只有水声没有水波、字幕带着标点），
而时长、分辨率、响度、音画对齐四项常规检查一个都没报错——这三道门恰好是唯一能拦住它们的。
所以把三道门（外加字幕边界压测）收成一个入口，交付前只记一条命令。

第五道门（2026-09-28《茶馆》补上）：字幕轴本身对不对得上配音。
  边界压测证明的是「画面上显示的字幕 == 字幕轨在该时刻写的那条」——它拿字幕轨自己当真值，
  所以轴整段漂 16 秒它照样 156/156 全绿。《茶馆》首版正是这样：silence-segments 轴顺序铺满
  （末条覆盖比 0.998、行间零空隙、能量包络全对），形状完美但**行归属**全错，第 38-45 行比
  实际语音早了 14-16 秒，观众看到的是「话还没说到，字幕已经换了；话说完了，字幕还没换」。
  能独立作证的两方：ASR token 的时间戳（文本↔时间，`create-body-timings-precise.mjs --verify`）
  和 silencedetect 的能量边界（空窗/读不完/行首落后，`audit-caption-axis.py`）。两道都收进来。

第七道门（2026-09-28《恶女的告白》补上）：每一行字幕单独对自己的语音负责。
  上面两道门都还是「轴对轴」——第 5 道拿 ASR 重算的轴比交付轴，两者同源于 ASR，ASR 时刻被
  压扁时一起错；第 6 道只看能量边界上的空窗和阅读时间，行首只要「差不多」就放行。
  《恶女的告白》首版：88 行铺满 227.6s，前六道全绿，逐行量 |Δstart| 中位 3.04s、最大 11.82s，
  40 行的字幕窗根本没盖住它自己念出来的那句话。
  `check-caption-speech-align.py --track` 用 ASR 字序把每一行钉到 token，再用波形突发把
  钉到的头尾吸到听得见的边界，逐行要求「字幕窗 ⊇ 自身语音窗（≥70% 覆盖）+ 窗内确有语音」。
  修的手是 `scripts/snap-body-timings-vad.py`，两者共用 `scripts/lib/caption_speech.py`。

第八道门（2026-10-01《恶意》补上）：钩子段画面上不得压着常驻《书名》/ 作者层。
  规则在 AGENTS.md 里写了，生成器 `create-episode-preview.mjs` 也用 `showBookMark: false`
  落实了，可成片拼进去的 `tmp/preview-<slug>/hook/renders/hook.mp4` 是改标志**之前**渲的旧文件，
  书名层在钩子段上活了整整 6.77 秒，前七道门全绿——它们看字幕轨、看背景溯源、看水波同窗，
  没有一道看画面顶部的书名带。`check-hook-no-bookmark.py` 拿正文段真实书名层几何
  （渲染中间产物 title.png 的 alpha）当模板，逐帧量「字形亮 / 字缝暗」的反差：钩子窗有反差即红，
  正文窗必须有反差（否则这道门是空转的，同样判红）。

第十道门（2026-10-04《嫌疑人X的献身》补上）：整片不许有单帧亮度硬跳，被判片段另加五条形状判据。
  用户报「片头钩子出现闪屏」。前九道门里看画面的三道（水波同窗 / 字幕边界压测 / 钩子图上屏）
  量的都是「某个局部区域画面对不对」，没有一道量「相邻两帧之间跳了多少」——所以成片 8405 帧里
  8 帧单帧均亮度跳 42~93（全在共享片头那 3 秒），九道门照样全绿。正文每个场景切换都是 0.64s
  交叉溶解，全片 p99 只有 2.7，「一帧砸下去一百级灰阶」在这类片子里本来就是缺陷信号。
  随后按真实缺陷逐条加形状：②眨眼、③摊不开（跨度必须摊在时间上，参照物是本片正文）、
  ④来回（「去了又回」只看腿长与深度，不看绝对亮度）、⑤换页（整幅换了画而均值没变时 ①~④
  全盲，量的是相邻帧 MAD）、⑥漏底（凸组合越不出两侧电平夹出的带，越带就是黑底漏进两层之间）。
  每一条都是改**判据的形状**，不是把画面压暗来过门——压暗在这套度量里是等价变换，⑥ 尤其明显。
  2026-10-05 用户裁决「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」之后，
  **判决范围**改了而形状没改：共享片头窗（加钩子段尾那 0.4s 淡出）只报告不判红，被判的是
  钩子段本体 + 正文起幅 1 秒；判据①只让开两端都在片头内的帧对，跨边界那两对（段尾淡出末帧→
  片头第一帧、片头末帧→正文第一帧）照判，所以钩子段尾淡出被摘掉仍然是红。豁免窗那一行会印出
  「按同一套判据本会翻几项红」，不留暗账。两道结构都跑：两段结构（没有 script-hook.csv）时
  片头就是第一个镜头、前 FADE_IN_GRACE 秒不判下限，这道门无条件跑。

第十一道门（2026-10-05 同一份裁决补上）：交付像素里的片头必须是**回滚之后**那一版。
  它存在的理由恰好是第十道门的设计：裁决把共享片头窗从判决范围里摘走了，于是十道门里再没有
  任何一道看得见那段。组成指纹（composition-digest）也看不见——它比的是生成器，而生成器此刻
  确实已经是回滚后的样子。结果就是 10-04 14:27 ~ 10-05 10:58 之间渲的 6 部片子（嫌疑人X的献身 /
  乡下人的悲歌 / 白夜行 / 李尔王 / 群魔 / 格兰特船长的儿女）带着被否决的那版片头（底板归一亮带、
  书单改成卡片网格、接缝垫帧溶解、砸黑改溶解）十道全绿地交付了。这道门用三个证人问成片自己：
  接缝到不到黑、滚动书单的每个电平在不在 media/_raw 底图的平台上、书单→结果页那一拍黑还在不在。
  基准全部现场量自模板真源（底图文件 + lib/intro-template.json 的时刻表），不抄数字、不加亮度
  上下限；电平比的是**集合**而不是逐帧对齐，因为页序本身是编辑细节、已经动过一次（13:13），
  逐帧比会把健康片判红而调色版躲得过。认不出三段结构 ⇒ ⚠️ 无从证明、不判红。
  修法只有一个：重渲该期的钩子段与片头段。压暗底板、放宽容差、改装配层都不解决它。

第十二道门（2026-10-05 用户要求补上）：正文段底部那一行常驻声明在不在像素里。
  用户要求「渲染的视频里加一行『内容为书评与感悟 素材来源于网络』，位置在中间底部、
  颜色偏暗灰、字体偏小」。文案与几何只有一个真源（scripts/lib/disclaimer.json，
  Python 侧 lib/disclaimer.py、JS 侧 lib/disclaimer.mjs），两条渲染路径都从它画，
  但这恰好是第八道门量过的失败形状：声明在源文件里 ≠ 声明在成片里。这道门拿 lib 现算的
  预期墨迹在声明带里比像素（有 body-bg.mp4 时用作差，没有时用「静止层证据」——
  照片在缓推和溶解，只有叠层不变），并反推字的实际灰度（换成白字保可读性即红），
  同时要求钩子段不带这层。存量期数（中间产物里没有 disclaimer.png）只报 ⚠️ 无从证明。

第十三道门（2026-10-06 用户要求）：正文《书名》+ 作者层上屏的是请求的那一款字体吗。
  用户原话「优化正文部分视频中的书籍字体，改为微软雅黑」；这台 Mac 没装雅黑，确认改用
  苹方-简，雅黑留在 scripts/lib/title-font.json 的优先级第一位（装了就在本机生效）。
  它需要门的原因和第八/十二道门同一条：字体名写在 lib 里 ≠ 那一笔墨在成片里，而字体
  还多一层只有它才有的失败形状——.ttc 按 index 取 face 会静默拿到繁体（STHeiti Medium.ttc
  的 index 0 是黑体-繁），装不到就静默回退。这道门按 (family, style) 反查 index 并核
  渲染器写下的 title-meta.json（渲染时真正加载的 face + title.png 的 sha），确认本期
  书名/作者文案在这款 face 上每个字都画得出墨，比对 Pillow 与 Chrome 两条路径的
  font-family 栈同序同源，并且反向守住范围：字幕字体与共享片头的字体不许被顺手换掉。
  成片比 lib 新 ⇒ 要求证人存在；存量期数照旧 ⚠️ 无从证明。

第十四道门（2026-10-07 用户要求）：正文起幅那张「本书封面图」在不在像素里，以及有没有按时离开。
  用户原话「增加本书的封面图，从微信读书或网上获取，在固定开场白结束后跟水波效果一同出现」，
  追问后选定「只跟水波那 1.4 秒」。卡框/投影/时序在 scripts/lib/book-cover.json，层由渲染器
  画成 TMP/book-cover.png 叠在 body-bg 之上——「写在 lib 里 ≠ 进了成片」同第八/十二/十三道门。
  这道门判三个方向：该有没有（红）、该走没走（红：常驻的 300px 竖卡会爬进字幕取样带 680..850
  与闪屏取证窗口 y<700，把第四/十道门变成假绿灯）、钩子段压着它（红，同《书名》层不进钩子）。
  中间产物里没有 book-cover.png 且本期没有 book-cover.jpg 的存量期数照旧 ⚠️ 无从证明。

用法：
  python3 scripts/check-episode-delivery.py <书名>
  python3 scripts/check-episode-delivery.py <书名> --tmp tmp/<期>_tpl --intro-trim 3.0
  --tmp   渲染中间目录（放 cap-*.png / body-bg.mp4 / caption-track.json）。
          默认依次尝试 $FALLBACK_TMP、tmp/<书名>_tpl、/tmp/<书名>_tpl。
  --final 成片路径，默认 episodes/<书名>/renders/<书名>-final.mp4。
  --skip-axis 明确跳过字幕轴门（仅用于审计历史期数；新交付不得跳过）。

退出码：0 全部通过；1 至少一道门未通过（供交付流程拦截）。
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable or "python3"


def run_gate(label, argv, note=""):
    """跑一道门禁，原样转印输出，返回 (是否通过, 一行结论)。"""
    print(f"\n{'=' * 68}\n[{label}] $ {' '.join(argv)}\n{'-' * 68}")
    r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip())
    ok = r.returncode == 0
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    verdict = lines[-1][:110] if lines else ("通过" if ok else "无输出")
    if note:
        verdict = f"{note} ｜ {verdict}"
    return ok, verdict


def find_tmp_dir(ep_name, explicit):
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("FALLBACK_TMP"):
        cands.append(os.environ["FALLBACK_TMP"])
    cands += [os.path.join(ROOT, "tmp", f"{ep_name}_tpl"), f"/tmp/{ep_name}_tpl"]
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def ffprobe_frames(path):
    """视频流的帧数（元数据里的 nb_frames，不逐帧 decode）。取不到返回 0。"""
    if not path or not os.path.exists(path):
        return 0
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_frames", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def boundary_times(track_path, intro_trim, edge=0.06):
    """每条字幕取 start+edge 与 end−edge：边界压测。

    均匀采样会漏掉「短字幕被挤成空屏」那类缺陷（《少年维特的烦恼》24 个均匀点全绿，
    压在两端才暴露），所以这里默认按边界取点。
    """
    track = json.load(open(track_path, encoding="utf-8"))
    ts = []
    for c in track:
        ts.append(round(intro_trim + c["start"] + edge, 3))
        ts.append(round(intro_trim + c["end"] - edge, 3))
    return ts, len(track)


GATE_NAMES = ["背景图溯源", "字幕零标点", "水波声画同窗", "字幕边界压测",
              "字幕轴对上语音", "字幕轴能量边界", "逐行盖住自身语音", "钩子无书籍信息",
              "钩子图上屏核对", "整片无亮度闪屏", "片头原样核对", "常驻声明上屏",
              "书名字体核对", "本书封面上屏"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--final", default="")
    ap.add_argument("--tmp", default="")
    ap.add_argument("--intro-trim", type=float, default=3.0)
    # 深核：按清单里的 photo id 重放下 CDN 取图，与磁盘 _raw/ 逐像素比。
    # 默认不开：它要联网，而交付门禁必须能在断网/限流时照样给出可信的绿灯；
    # 需要证明「盘上图 = 清单那张照片」时（换过图、拷过图、门禁绿得可疑）再显式加。
    ap.add_argument("--verify-photos", action="store_true",
                    help="追加 scripts/verify-pexels-fetch.py 的逐张重放核对（联网）")
    ap.add_argument("--voice", default="audio/body-voiceover-story.mp3",
                    help="字幕轴能量边界门用的干配音；相对 episode 目录或仓库根均可")
    ap.add_argument("--skip-align", action="store_true",
                    help="跳过字幕边界压测（无渲染中间产物时）")
    ap.add_argument("--skip-axis", action="store_true",
                    help="跳过字幕轴两道门（只用于审计历史期数，新交付不得跳过）")
    args = ap.parse_args()

    ep = args.episode
    ep_dir = os.path.join(ROOT, "episodes", ep)
    if not os.path.isdir(ep_dir):
        sys.exit(f"episode 目录不存在：{ep_dir}")
    # --intro-trim 不许低于结果页定格点：切在滚动书单上时，正文前一帧是别本书的封面卡，
    # 而水波窗、字幕取点、钩子门全都按这个偏移算，会把一版切错的成片验成八道全绿。
    # 渲染器侧有同一道断言（读同一份 lib/intro-template.json），这里补的是「拿旧命令重跑门禁」这条路。
    with open(os.path.join(HERE, "lib", "intro-template.json"), encoding="utf-8") as _f:
        result_page_at = json.load(_f)["result_page_at"]
    if args.intro_trim < result_page_at:
        sys.exit(f"--intro-trim {args.intro_trim} 早于片头结果页定格点 {result_page_at}s："
                 f"这一版成片切在滚动书单上，先按 INTRO_TRIM_SECONDS=3.0 重渲再验收"
                 f"（见 scripts/lib/intro-template.json）")
    final = args.final or os.path.join(ep_dir, "renders", f"{ep}-final.mp4")
    if not os.path.exists(final):
        sys.exit(f"找不到成片：{final}（先渲染，或用 --final 指定）")
    tmp = find_tmp_dir(ep, args.tmp)
    track = os.path.join(ep_dir, "caption-track.json")
    if not os.path.exists(track) and tmp:
        track = os.path.join(tmp, "caption-track.json")

    # --voice 的两层含义：audit-caption-axis.py 拿它拼 ep_dir，而这里 subprocess 的 cwd 是 ROOT。
    # 传 episodes/<书名>/audio/... 会被拼成 episodes/<书名>/episodes/<书名>/audio/... 而缺文件红灯，
    # 红灯看起来像「这一期的轴没过门」，实际是路径写错——《玩偶之家》《哈姆雷特》2026-09-29 各踩过一次。
    # 所以在这里统一解析成绝对路径：先按仓库根找，再按期目录找，都找不到就原样传下去让子门禁报错。
    if not os.path.isabs(args.voice):
        for cand in (os.path.join(ROOT, args.voice), os.path.join(ep_dir, args.voice)):
            if os.path.exists(cand):
                args.voice = cand
                break

    # 三段结构（有 script-hook.csv）下，正文在成片时间轴上的起点不是模板裁切点，而是
    # 「钩子 + 模板裁切段」。水波窗、字幕边界压测的取点都按成片时间算，只传 --intro-trim 1.83
    # 会整体早 6.77 秒：压测在钩子段里找正文字幕，找到的全是空屏，六道门都能对着一段
    # 根本没被检查的画面绿灯。所以这里按本期实际存在的钩子配音把偏移补齐。
    pre_body = args.intro_trim
    has_hook = os.path.exists(os.path.join(ep_dir, "script-hook.csv"))
    if has_hook:
        hook_voice = os.path.join(ep_dir, "audio", "hook-voiceover-story.mp3")
        if os.path.exists(hook_voice):
            _r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", hook_voice], capture_output=True, text=True)
            try:
                hook_dur = round(float(_r.stdout.strip()), 2)
            except ValueError:
                hook_dur = 0.0
            if hook_dur:
                pre_body = round(args.intro_trim + hook_dur, 2)
                print(f"[hook] 三段结构：正文起点 = 钩子 {hook_dur}s + 模板 {args.intro_trim}s "
                      f"= {pre_body}s（水波窗与字幕取点按此偏移）")

    results = []

    # 1) 背景图溯源：Pexels photo id + 画幅 + 调色备份 + 书名/字幕区亮度 + scene-map 图层
    results.append(run_gate("image-provenance", [PY, "scripts/check-image-provenance.py", ep]))

    # 2) 字幕上屏零标点 + 零空白间隙（有 --png-dir 时连像素一起核）。
    #    交付就是「新渲的期」，所以这里一律 --strict：不管字幕轨有没有落 captionStyle 标记，
    #    空格间隙和超行都判红。全仓库审计（--all）才按标记分档，把历史轨降级成待重渲清单。
    punct = [PY, "scripts/check-caption-punctuation.py", track, "--strict"]
    # 字幕文本真源：三段结构是 script-body.csv，历史单段期只有 script-body.csv。
    # 只找 script-body.csv 的话，改名后的每一期都会静默丢掉「逐行对 CSV」这半边证据。
    csv_path = next((os.path.join(ep_dir, n) for n in ("script-body.csv",)
                     if os.path.exists(os.path.join(ep_dir, n))), "")
    if csv_path:
        punct += ["--csv", csv_path]
    if tmp and any(f.startswith("cap-") for f in os.listdir(tmp)):
        punct += ["--png-dir", tmp]
    results.append(run_gate("caption-punctuation", punct))

    # 3) 水波声画同窗：这是最坏的一种缺陷——只有声音没有画面，其余检查全绿
    #    封面层与水波**同窗**（2026-10-08 用户要求「出现时增加放大的动态效果」之后必然如此），
    #    所以把渲染时那份 book-cover.json 证人交给门：门按逐帧真实足迹排除卡片动效、每环按
    #    可见像素归一，否则会把「卡片盖住的那几个内环」读成涟漪在向内收缩（《荒凉山庄》
    #    实测：静态并框 + 整环分母 +15px/ρ0.26 假红，逐帧足迹 + 可见分母 +74px/ρ0.81 真绿）。
    #    证人缺席时门自己退回全画面判决（宁可多判不可漏判），这里不替它编证据。
    water = [PY, "scripts/check-water-ripple.py", final, "--intro-trim", str(pre_body)]
    _water_wit = os.path.join(tmp, "book-cover.json") if tmp else ""
    if _water_wit and os.path.exists(_water_wit):
        water += ["--cover-witness", _water_wit, "--exclude-cover"]
    results.append(run_gate("water-ripple", water))

    # 4) 字幕边界压测：画面实际显示的字幕 == 该时刻应显示的那一条
    if not args.skip_align:
        if not tmp or not os.path.exists(track):
            results.append((False, "找不到渲染中间目录或 caption-track.json，无法做边界压测"
                                   "（用 --tmp 指定，或 --skip-align 明确跳过）"))
        else:
            bg = os.path.join(tmp, "body-bg.mp4")
            if not os.path.exists(bg):
                results.append((False, f"{bg} 不存在：正文背景没留中间产物，无法证明画面上就是这条字幕"))
            else:
                times, n = boundary_times(track, pre_body)
                # 正文第 0 帧在成片里的**真实帧号** = 成片帧数 − 正文背景帧数。
                # pre_body 那个秒数是「钩子配音时长(两位小数) + 模板裁切秒数」加出来的，
                # 而装配是按帧 concat 的：《阿特拉斯耸耸肩》2026-10-07 钩子 215 帧 + 模板 90 帧
                # = 305 帧 = 10.1667s，秒数口径给的是 10.18s，差 0.4 帧。边界压测问的正是
                # 行尾 ±0.06s 那一帧，半帧的坐标错就够读到两行之间那 1~2 帧真空，把
                # 「画面上字幕在」报成「带内零墨迹」（当期 10 个红点全是这一类，逐帧核对
                # 成片第 1636~1701 帧确有行 19 的墨迹）。水波与常驻声明按 ~1s 的窗取样，
                # 半帧不改变判决，所以只把这一道改成按帧对齐，不动它们的秒数口径。
                body_frames = (ffprobe_frames(bg)
                               or ffprobe_frames(os.path.join(tmp, "body.mp4")))
                final_frames = ffprobe_frames(final)
                pre_frames = (final_frames - body_frames
                              if final_frames and body_frames and final_frames > body_frames
                              else -1)
                cmd = [PY, "scripts/qc-caption-alignment.py", final, track, tmp, bg,
                       "--intro-trim", str(pre_body), "--times", ",".join(map(str, times)),
                       "--work", os.path.join(tempfile_work(ep))]
                if pre_frames >= 0:
                    cmd += ["--pre-body-frames", str(pre_frames)]
                    print(f"[caption-align] 正文起点 = 成片第 {pre_frames} 帧"
                          f"（成片 {final_frames} − 正文背景 {body_frames}），取点按帧网格对齐")
                else:
                    print(f"[caption-align] ⚠️ 取不到成片/正文背景的帧数，正文起点退回 "
                          f"{pre_body}s 换算：行尾 ±0.06s 的采样可能读到隔壁那一帧")
                results.append(run_gate("caption-align", cmd,
                                        note=f"{n} 条字幕 × 两端 = {len(times)} 点"))

    # 5) 字幕轴 vs 配音语音（文本↔时间）：按 ASR token 现算一遍轴，和交付用的轴逐行比。
    #    这一道专治「铺满轴整段漂十几秒」——形状类判据（覆盖比、行间空隙、能量包络）全绿，
    #    边界压测也全绿，因为它比的是字幕轨自己。只有重算轴能看见行归属错了。
    if not args.skip_axis:
        results.append(run_gate(
            "caption-axis-token",
            ["node", "scripts/create-body-timings-precise.mjs", ep, "--verify"],
            note="行归属漂移检查"))

        # 6) 字幕轴 vs 能量边界：行首落后 / 行内空窗 / 读不完 / 行尾拖
        results.append(run_gate(
            "caption-axis-energy",
            [PY, "scripts/audit-caption-axis.py", ep, "--json", "--voice", args.voice],
            note="空窗与阅读时间检查"))

        # 7) 逐行覆盖：这一行的字幕窗必须盖住这一行自己念出来的窗。
        #    第 5 道比的是「重算轴 vs 交付轴」（两条轴同源于 ASR，同错即同绿），第 6 道比的是
        #    能量边界上的空窗与读不完；只有这一道拿 ASR 字序 + 波形突发给每一行单独作证。
        #    2026-09-28《恶女的告白》：silence-segments 轴 88 行铺满 227.6s，前六道全绿，
        #    而 |Δstart| 中位 3.04s、最大 11.82s —— 40 行没盖住自己的语音。
        #    ⚠️ 用 --track 验收真正上屏的 caption-track.json：渲染器还会按字数/语速把过短的行
        #    向后延长，拿 body-timings.json 过关不等于成片过关。
        results.append(run_gate(
            "caption-speech-align",
            [PY, "scripts/check-caption-speech-align.py", ep, "--track"],
            note="逐行覆盖检查"))

    # 8) 钩子段不得压常驻《书名》/ 作者层：只看成片像素，不信生成器声明。
    #    《恶意》2026-10-01：showBookMark:false 早就在 create-episode-preview.mjs 里了，
    #    可成片拼的 hook.mp4 是改标志之前渲的旧文件，书名层在钩子段上活了 6.77 秒，
    #    前七道门一个都不看顶部书名带 —— 它们要么看字幕轨、要么看背景、要么看水波。
    if has_hook:
        results.append(run_gate(
            "hook-no-bookmark",
            [PY, "scripts/check-hook-no-bookmark.py", ep, "--intro-trim", str(args.intro_trim)]
            + (["--tmp", tmp] if tmp else []),
            note="钩子段书籍信息层检查"))
        # 9) 钩子段画面真的是清单里那两张图吗？前面所有门只看文件与记录，从不看成片。
        #    《偏跟山过不去》2026-10-03：钩子渲染整段失败（npx 缓存抖动），兜底渲染器拒收，
        #    磁盘上留的是上一版 11:51 的成片，八道门对着那部旧片打了八个 ✅。
        #    这一道抽钩子窗口的帧，跟 images-hook/ 里的每张底图比，认不出来的判红。
        results.append(run_gate(
            "hook-frame-match",
            [PY, "scripts/check-hook-frame-match.py", ep],
            note="钩子段实拍图对账"))

    if args.verify_photos:
        results.append(run_gate(
            "pexels-replay",
            [PY, "scripts/verify-pexels-fetch.py", ep, "--segment", "all"],
            note="按 photo id 重放取图核对盘上底图（联网）"))

    # 10) 整片亮度闪屏：前九道门里看画面的三道（水波 / 字幕边界压测 / 钩子图上屏）量的都是
    #     「局部区域对不对」，没有一道量「相邻两帧之间跳了多少」。2026-10-04《嫌疑人X的献身》
    #     用户报闪屏，成片 8405 帧里 8 帧单帧均亮度跳 42~93，全在共享片头那段，九道门全绿。
    #     正文每个切点都是 0.64s 交叉溶解（p99 只有 2.7），所以「单帧硬跳」在这类片子里
    #     本来就是缺陷信号，不分段。这道门无条件跑：判据①看整片（只让开两端都在片头内的对），
    #     ②~⑥ 看被判片段——有钩子时是钩子段本体 + 正文起幅，两段结构时只剩正文起幅
    #     （片头就是第一个镜头，前 FADE_IN_GRACE 秒不判下限），两种结构都不会空转。
    flicker = run_gate(
        "luminance-flicker",
        [PY, "scripts/check-luminance-flicker.py", final])
    results.append(flicker)

    # 闪屏红的时候，「红」本身不足以决定下一步：判据①~⑥ 报的是帧号，而修法分属三层
    # （本期素材 / 装配层 / 共享片头模板——第三层按 2026-10-05 用户裁决只报告不判红）。
    # 2026-10-04 量 119 部时，每一部都得重渲一遍、再逐帧重看，才知道红是旧像素还是当前代码。
    # 这道归因读门禁自己的同一批帧与同一个豁免范围，把开场窗口
    # 按段落切开、点名该改哪一层，所以只在闪屏红时跑（诊断用，不参与汇总的十个 ✅）。
    if not flicker[0]:
        run_gate("flicker-attribution",
                 [PY, "scripts/check-flicker-attribution.py", final],
                 note="闪屏红因归因（诊断，不计入门禁计数）")

    # 11) 片头是不是用户 2026-10-05 裁决要的那版原样。第十道门按同一份裁决把片头窗
    #     豁免成「只报告不判红」，所以它证明不了交付像素里的片头是回滚之后渲的：
    #     判据②~⑥ 不看那段，判据① 只放行「两端都在片头内」的帧对，于是一部 10-04
    #     那版（底板被归一亮带、书单被改成 3×2 卡片网格、接缝被垫帧溶解、砸黑被改成
    #     0.50s 溶解）的成片，十道门照样全绿。组成指纹（composition-digest）也看不见它，
    #     因为那七张底板和 index.html 此刻确实和记录一致——回滚改的是源，不是已经落盘的像素。
    #     这道门只问交付像素：接缝到不到黑、底板电平在不在原图上、砸黑那一拍还在不在。
    results.append(run_gate(
        "intro-original",
        [PY, "scripts/check-intro-is-original.py", final],
        note="片头原样核对（2026-10-05 裁决）"))

    # 12) 正文段底部那一行常驻声明「内容为书评与感悟　素材来源于网络」在不在像素里。
    #     规则很轻，需要门的原因和第八道门一模一样：文案写在 lib、层画在渲染器里，
    #     都不等于它进了成片。这道门拿 lib/disclaimer.py 现算的预期墨迹，在声明带
    #     （lib 钉死在 y 850 以下，正好避开第四道门取样用的字幕带 680..850）里比像素：
    #     正文窗找不到 → 红；钩子窗找到静止的同一层 → 红；盘上 disclaimer.png 与 lib
    #     对不上（改过文案没重渲）→ 红。由加这层之前的渲染器出片的存量期数打印
    #     ⚠️ 无从证明、不判红（同 Pexels 溯源标记的爆炸半径控制）。
    results.append(run_gate(
        "disclaimer-overlay",
        [PY, "scripts/check-disclaimer-overlay.py", ep, "--body-at", str(pre_body)]
        + (["--tmp", tmp] if tmp else []),
        note="正文段常驻声明核对"))

    # 13) 正文《书名》+ 作者层上屏的是不是 lib 里那一款（用户 2026-10-06 要求改字体）。
    #     lib 改了不等于像素改了：本机书名层由 Pillow 画成 title.png，face 是渲染时
    #     按 (family, style) 反查 index 得到的，所以渲染器会写下 title-meta.json 当证人。
    #     成片比 lib 新 ⇒ 这一代渲染器必须写下证人，于是按 mtime 决定要不要 --require，
    #     而不是把「没证人」一律放成绿（存量期数照旧 ⚠️ 无从证明）。
    font_argv = [PY, "scripts/check-title-font.py", ep] + (["--tmp", tmp] if tmp else [])
    if os.path.getmtime(final) > os.path.getmtime(
            os.path.join(ROOT, "scripts", "lib", "title-font.json")):
        font_argv.append("--require")
    results.append(run_gate(
        "title-font", font_argv,
        note="正文书名层字体核对（含字幕与片头字体未被波及）"))

    # 14) 正文起幅那张「本书封面图」在不在像素里、有没有按时离开（用户 2026-10-07
    #     「增加本书的封面图……在固定开场白结束后跟水波效果一同出现」+「只跟水波那 1.4 秒」）。
    #     同一类缺陷、同一类门：卡框与时序写在 lib、层画在渲染器里，都不等于它进了成片。
    #     这道门还管另一半——该走没走的常驻封面会爬进字幕取样带（680..850）和闪屏取证窗口
    #     （y<700），把第四道和第十道门变成假绿灯，所以「它在」和「它走了」是一句话的两半。
    #     成片比 lib 新 ⇒ 这一代渲染器必须写下 book-cover.png/json 证人，按 mtime 决定 --require。
    cover_argv = [PY, "scripts/check-book-cover-overlay.py", ep,
                  "--body-at", str(pre_body)] + (["--tmp", tmp] if tmp else [])
    if os.path.getmtime(final) > os.path.getmtime(
            os.path.join(ROOT, "scripts", "lib", "book-cover.json")):
        cover_argv.append("--require")
    results.append(run_gate(
        "book-cover-overlay", cover_argv,
        note="正文起幅本书封面核对（出现时刻与退场时刻）"))

    names = list(GATE_NAMES[:3])
    if not args.skip_align:
        names.append(GATE_NAMES[3])
    if not args.skip_axis:
        names += list(GATE_NAMES[4:7])
    if has_hook:
        names += list(GATE_NAMES[7:9])
    if args.verify_photos:
        names.append("取图重放核对")
    names.append(GATE_NAMES[9])
    names.append(GATE_NAMES[10])
    names.append(GATE_NAMES[11])
    names.append(GATE_NAMES[12])
    names.append(GATE_NAMES[13])
    print(f"\n{'=' * 68}\n交付门禁汇总  《{ep}》  {os.path.relpath(final, ROOT)}\n{'-' * 68}")
    for name, (ok, verdict) in zip(names, results):
        print(f"  {'✅' if ok else '❌'} {name:14} {verdict}")
    bad = [n for n, (ok, _) in zip(names, results) if not ok]
    if bad:
        print(f"\n❌ 未通过：{'、'.join(bad)} —— 不得交付，修完重跑")
        sys.exit(1)
    print(f"\n✅ {len(names)} 道交付门禁全部通过")


def tempfile_work(ep):
    """抽帧工作目录按剧集私有：共用 /tmp/qc-caption-align 会读到上一期/上一轮的旧帧，
    一次「Overwrite? [y/N]」就能把 12/16 个正确时刻报成错配。"""
    d = os.path.join("/tmp", f"qc-caption-align-{ep}")
    os.makedirs(d, exist_ok=True)
    return d


if __name__ == "__main__":
    main()
