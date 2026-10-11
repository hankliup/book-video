#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成本期 1080x1920 封面（本系列封面的唯一出图口，渲染脚本调用它，不再各自内嵌副本）。

用法：
  EPISODE=美丽新世界 python3 scripts/make_episode_cover.py
  EPISODE=雪国列车 COVER_TAG="雅克·罗布 / 反乌托邦图像小说" \
    COVER_HOOK="真正可怕的，从来不是一列永不停驶的火车" python3 scripts/make_episode_cover.py

规格（2026-10-01 重定，见下面「排版规则」注释）：
  · result-bridge 铺满 1080x1920，先 UnsharpMask 补回上采样丢的边；
  · 书名/作者/钩子整块**垂直居中**在画幅中央，不是沉在底边；
  · 书名**一律单行**（2026-10-08 用户裁决），装不下就**缩小字号**（2026-10-09 用户裁决
    「根据合适排版进行缩小字号」）：常规 ≥132px，清晰下限 96px（低于它照出但每次打警告），
    绝对下限 60px＝钩子行最小字号；笔画只靠投影和垫底色分离，不再描 4px 粗边；
  · 到绝对下限还装不进，才在 brief.json 写 `cover_title`（只给封面用的短书名，display_title
    不动）——现目录 152 期里没有一期需要它；
  · 钩子 ≤22 字，超宽在逗号处折行，最多 2 行。
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 分词只是书名折行的偏好，不是运行依赖：装了就用它避开「把词劈开」的折法，
# 没装（clean clone、CI）就退回纯字数均衡，出图口不能因为缺一个可选包而挂。
try:
    import jieba as _jieba
except ImportError:      # pragma: no cover - 没有分词器时按字数折行
    _jieba = None

# 仓库根从脚本自身位置推导。这里曾经写死一个绝对路径，换一次 clone（或把仓库
# 从「短视频」挪到「读书视频」）就找不到 episodes/ 了——AGENTS.md 第一条就要求
# 解析当前仓库根、不硬编码既往 clone 路径。
ROOT = os.environ.get("ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPISODE = os.environ.get("EPISODE")
if not EPISODE:
    raise SystemExit("用法：EPISODE=<书名> [COVER_TAG=...] [COVER_HOOK=...] python3 scripts/make_episode_cover.py")

EP = os.path.join(ROOT, "episodes", EPISODE)
# 正文段氛围图目录：三段结构改名后叫 images-body/，历史剧集只有 images/。
# 解析统一走 lib/episode_paths.py，和出图、调色、溯源门禁、渲染同一套名字，
# 否则改名后的第一期会报「缺少封面底图」，而图就在隔壁目录。
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from episode_paths import body_images_dir  # noqa: E402
SRC = os.environ.get("COVER_SOURCE", os.path.join(body_images_dir(EP), "result-bridge.png"))
# EPISODE 可以是 "待上传/<书名>"（成片交付后整期挪进待上传再补封面），
# 文件名只取末段书名，否则会拼出 renders/封面-待上传/<书名>.png 这种不存在的目录。
OUT = os.environ.get(
    "COVER_OUT",
    os.path.join(EP, "renders", f"封面-{os.path.basename(EPISODE)}.png"))

# ---------------------------------------------------------------- 排版规则
# 2026-10-01 用户验收《恶意》封面：「字体模糊不清晰，字体放大，放到图片中间位置」。
# 三条病根，都能量化，所以写成断言而不是口头约定：
#  1. 字号。旧版书名 96px = 画幅宽的 8.9%，在 1080x1920 的信息流里被缩到 ~270px 宽，
#     书名只剩 24px，笔画糊成一团。规则：书名 ≥ HOOK_MIN（绝对下限见下面
#     TITLE_ABSOLUTE_MIN），钩子 ≥ HOOK_MIN。TITLE_MIN 132 从「硬下限」降为「舒适下限」——
#     2026-10-08 的一律单行 + 2026-10-09 的缩小字号让字数成了字号的唯一约束。
#  2. 位置。旧版整块文字底边落在 1430/1540/1620，即画面下三分之一，
#     而 result-bridge 的视觉主体（那扇亮着的窗）在中上部，字和图各说各话。
#     规则：文字块垂直中心落在画幅中线 ±BLOCK_CENTER_TOL 内。
#  3. 分离方式。旧版用 stroke_width=4 的黑边把白字从背景里抠出来，96px 的字
#     加 4px 双边等于把「恶」「意」的字心里外糊掉一圈——越描越糊。
#     规则：不描边，改用「柔和投影 + 居中暗色垫底带」，笔画原样落纸。
# 字体与正片**同源**（2026-10-07）：封面文字 = 书名/作者/钩子，本来就是「书籍信息」，
# 走 scripts/lib/title-font.json 的 resolve()——与正文常驻书名层、正片字幕同一款 face。
# 旧的 `STHeiti Medium.ttc + index=1` 注释理由是「本系列正片字幕那一款」，正片字幕已于
# 同日并入同源解析（旧常量 index 0 是 Heiti TC 的坑见渲染器注释），封面跟着走同一条路，
# 不再留 COVER_FONT/COVER_FONT_INDEX 环境变量做第二真源。
import title_font as _title_font  # noqa: E402


def _face(size):
    return _title_font.truetype(size)

W, H = 1080, 1920
SAFE_W = int(W * 0.88)                 # 文字安全宽度：两侧各留 6%
TITLE_MAX, TITLE_MIN = 168, 132        # 书名：2 字书名用上限，长书名逐级回退到下限
# 超长书名折到 3 行仍可能装不进 132px。旧阶梯（2026-10-08 退役）拿它做最后一档。
# 现在它只剩一个用途：`check-cover-wrapping.py::old_ladder()` 要**逐字回放那条被撤掉的
# 阶梯**，才能证明「一律单行」不是一句空话（新阶梯交出 1 行、老阶梯交出 2~3 行的期数
# 就是这条裁决真正改变的东西）。出图口不许再调它——`_wrap_title` 也只为那个对照而活。
HARD_MIN_TITLE = int(os.environ.get("COVER_TITLE_HARD_MIN", "112"))
# 2026-10-08 用户裁决「修改视频封面图中的书名排版，不要有换行，并沉淀这个规则」。
# 这是**绝对**的：不再有 2026-10-07 那一版「装不下就折行」的第②支。
# 但绝对不等于可以悄悄出小字——1080px 画布、88% 安全宽度下，字大和不折行是同一笔算术
# （`字号 × 字数 ≈ 墨宽`）。2026-10-09 用户就这 13 期给了第三条裁决「根据合适排版进行缩小
# 字号」：**既不折行，也不改封面用名，而是缩字号**。于是 TITLE_LEGIBLE_MIN 从「拒绝出图的红线」
# 变成「必须逐期留痕的告警线」，真正的拒绝线交给下面的绝对下限。
# 96 这个数不是拍的：2026-10-01 用户否掉《恶意》封面「字体模糊不清晰」时那版正是 96px，
# 全库实测 152 期里 13 期的正式书名单行只有 60~92px（《一个无政府主义者的意外死亡》
# 15 个字符 → 60px），它们全部落在这条线以下。缩到这一档是用户 10-09 点名要的代价，
# 代价必须每次出图都在日志里看得见，所以低于 96 一定写一条 note；把 96 调到和绝对下限同值
# 来让日志闭嘴，就是这条裁决被偷偷撤回的形状。
TITLE_LEGIBLE_MIN = int(os.environ.get("COVER_TITLE_LEGIBLE_MIN", "96"))
# 缩小字号的**绝对**下限：到这儿还装不进单行，才轮到 brief.json 的 cover_title。
# 60 = 钩子行的最小字号（HOOK_MIN），书名再小就不该比钩子还小了——那是层级倒了。
# 全库最长书名 15 个字符正好落在 60px（15×60=900 ≤ 950），所以现目录下没有一期的封面用名被改。
TITLE_ABSOLUTE_MIN = int(os.environ.get("COVER_TITLE_ABSOLUTE_MIN", "60"))
TAG_SIZE = 64
HOOK_MAX, HOOK_MIN = 72, 60            # 钩子：一行装得下就 72，折行后按宽度回退
HOOK_CHARS = 22                        # AGENTS.md 的封面钩子字数上限
GAP_TITLE_TAG = 34
GAP_TAG_HOOK = 40
BLOCK_CENTER_TOL = 90                  # 文字块中心允许偏离画幅中线多少像素

# 中文排印禁则：这些标点不得出现在**行首**（GB/T 15834 一行不放的点号：、。；：？！
# 加上后半括号与间隔号）。方向很容易记反——2026-10-02 就记反过一次：以为「·」不能收尾，
# 于是把《魔戒3·王者归来》折成「《魔戒3 / ·王者归来》」，那颗点落到第二行开头，
# 既违禁则又在居中大字的左边读成一个游离的项目符号。合规的折法恰恰是让它留在上一行末尾。
# 所以这里不做任何「让位」，只把禁则写成断言，谁改反都会被行数档位直接挡下。
NO_LINE_START = ("·", "、", "，", "。", "；", "：", "？", "！", "》", "）", "”", "’")

# 单字虚词不得领起一行。断点只按字数均衡找，于是《一个人的村庄》被折成
# 「一个人 / 的村庄」——居中大字里第二行以一个「的」起头，读起来像错行也像漏字。
# 这条按**偏好**实现（放宽后仍可折），不像点号那样硬挡：书名是作者起的，
# 有时真没有既装得下又不以虚词领行的折法，硬挡会把封面挡没了。
NO_LINE_START_WORD = set("的地得了着过们之也就很更和与或把被在从对为")


def resolve_cover_title(brief_dict):
    """封面上印的那一行书名——**唯一**解析处，出图口和回归脚本都调它。

    优先 `cover_title`（本期为封面另定的短名），退回 `display_title`（正式书名）。
    为什么必须有这个函数而不是在画图段写一句 `brief.get("cover_title") or ...`：
    `check-cover-wrapping.py` 是拿全库书名跑这条排版规则的，它若只读 display_title，
    而封面实际画的是 cover_title，回归就在测另一个程序（本仓库反复踩的第二真源）。
    cover_title 只影响封面 PNG：视频里的常驻《书名》层、固定开场白书单、字幕全部走
    display_title，正式书名一个字都不改。
    """
    return (brief_dict.get("cover_title") or brief_dict["display_title"]).strip()


brief = json.load(open(os.path.join(EP, "brief.json"), encoding="utf-8"))
title = f"《{resolve_cover_title(brief)}》"
if brief.get("cover_title") and brief["cover_title"] != brief["display_title"]:
    print(f"[cover] 封面书名用 cover_title《{brief['cover_title']}》"
          f"（正式书名《{brief['display_title']}》不变）"
          + (f"；{brief['cover_title_note']}" if brief.get("cover_title_note") else ""))
# 封面上的书名/作者/钩子优先读 brief.json（AGENTS.md 要求 cover_hook / cover_tag 落盘到
# brief），环境变量只作为临时覆盖。否则下一期重跑封面时会静默丢掉钩子——
# render 命令行传的参数不会留痕，brief 才是这一期的事实来源。
tag = os.environ.get("COVER_TAG") or brief.get("cover_tag") or f"{brief['author']} / 著"
hook = os.environ.get("COVER_HOOK") or brief.get("cover_hook") or ""
hook_src = ("COVER_HOOK" if os.environ.get("COVER_HOOK")
            else "cover_hook" if brief.get("cover_hook") else "")
# 第三级兜底：旧封面上印着、当年只存在于命令行里的存量钩子行（见
# data/封面钩子-历史存量.json）。没有这一级，重跑 20 期旧版式封面就会把
# 当期实际发出去的文案抹成「只有书名+作者」——用户看到的正是这个缺钩子的封面。
if not hook and brief.get("cover_hook_legacy"):
    hook = brief["cover_hook_legacy"]
    hook_src = "cover_hook_legacy（旧封面存量，尚未走本期审批）"
if hook:
    print(f"[cover] 钩子行取自 {hook_src}：{hook}")

if not os.path.exists(SRC):
    raise SystemExit(f"缺少封面底图：{SRC}")

_probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _w(text, size):
    return _probe.textlength(text, font=_face(size))


def _fit(text, size):
    return _w(text, size) <= SAFE_W


def _fit_size(text, hi, lo, step=4):
    for size in range(hi, lo - 1, -step):
        if _fit(text, size):
            return size
    return lo


def _strip_brackets(text):
    """拆成（裸书名, 前括号, 后括号）。括号可能只有一侧——折行中途的片段就是这样。"""
    lead = "《" if text.startswith("《") else ""
    rest = text[len(lead):]
    tail = "》" if rest.endswith("》") else ""
    return rest[: len(rest) - len(tail)], lead, tail


def _word_boundaries(text):
    """jieba 在不在都能跑：分词只是偏好，不是依赖。

    本机装了 jieba 0.42.1，用它挡掉《被伤害与侮 / 辱的人们》《在森崎书 / 店的日子》
    这类把一个词劈成两行的折法——字数均衡只看长度，看不见词。clean clone 上没有它，
    那就退回原来的折法：宁可折得普通一点，也不能因为缺一个可选依赖就出不了封面。
    """
    if _jieba is None:
        return None
    offs, n = {0, len(text)}, 0
    for w in _jieba.cut(text):
        n += len(w)
        offs.add(n)
    return offs


def _better_cut(body, cut, lo, hi, bounds, on_mark, ban_words=True):
    """在断点附近挑一个「不劈词、下一行不以虚词或点号领行」的位置；都不满足就保持原位。

    只在同一行内让位，不改变行数：《一个人的村庄》按字数均衡落在「一个人 / 的村庄」，
    挪一格就是「一个人的 / 村庄」。往哪边先试按离原断点近的先来，等距时往右（上一行多一个字，
    居中大字读起来是「上面重、下面轻」，比反过来稳）。落在标点之后的断点最强，不让位。

    两层判据必须分开强弱（2026-10-02）：`ok` 是 GB/T 15834 的硬禁则（点号、间隔号、后半括号
    不许领行），`clean` 只是排版偏好（虚词不领行、不劈词）。旧写法把虚词也塞进 `ok`，于是
    偏好越过了硬锚点：《三体1·地球往事》的均衡断点正好落在间隔号之后（三体1· / 地球往事），
    却因为下一行以「地」开头被 `ok` 判死，让位到「三体1·地球 / 往事」，把「地球往事」劈成两半。
    地/得/过/着 都还是实词用字（地球、往事、过去），它们挡不住一个真正的标点断点。

    孤字层（2026-10-07）：均衡断点被词边界挪位后，末行可能只剩 1 个字＋后书名号——
    《乞力马扎罗的雪》折成「《乞力马扎罗的 / 雪》」，居中大字的第二行孤零零一个字，
    比虚词领行难看得多。优先级：clean＋不孤字 > 词边界＋不孤字（允许虚词领行，它本来就是
    偏好级）> clean > 不孤字 > ok。标点断点仍是最强锚点，不让位。

    让位半径一律 ±1（2026-10-07）：旧 ±2 会把断点拉到离均衡位置太远的地方——《乞力马扎罗
    的雪》在词边界＋不孤字层被拉到 2（「《乞力 / 马扎罗的雪》」，山名劈开、2/6 头轻脚重），
    而正确答案就在 ±1 内的 5（「《乞力马扎罗 / 的雪》」）。让位是**就地**微调，不是重新找重心。
    """
    def ok(c):
        return lo <= c <= hi and body[c] not in NO_LINE_START
    def clean(c):
        if not ok(c):
            return False
        if ban_words and body[c] in NO_LINE_START_WORD:
            return False
        return bounds is None or c in bounds
    def not_orphan(c):
        # 这个断点之后只剩 1 个字时，末行就是孤字（后续每行至少吃 1 字，只会更糟）。
        return len(body) - c != 1
    def in_bounds(c):
        return bounds is None or c in bounds
    if on_mark and ok(cut):
        return cut
    for tier in (lambda c: clean(c) and not_orphan(c),
                 lambda c: ok(c) and in_bounds(c) and not_orphan(c),
                 clean,
                 lambda c: ok(c) and not_orphan(c),
                 ok):
        for step in (0, 1, -1):
            if tier(cut + step):
                return cut + step
    return cut


def _split_balanced(body, n, ban_words=True):
    """把裸书名均衡折成 n 行，断点优先落在标点上（目标位置左右各 4 字内找）。

    每段至少留 1 字、且给后面的段留够字数（hi = len(body) - (n - k)），否则会出现
    空段——旧写法靠调用方 `if _fit(part)` 再递归，一旦某段落在同一个标点上就折不动了。

    断点选在标点**之后**（`cut = j`，标点留在上一行末尾），这正好符合中文禁则：
    点号与间隔号不许出现在行首。调用方 _wrap_title 会把这条禁则再断言一遍。
    """
    if n <= 1:
        return [body]
    marks = ("：", ":", "·", "，", "、")
    bounds = _word_boundaries(body)
    parts, i = [], 0
    for k in range(1, n):
        # 每一段都按「剩下多少字 / 还剩几行」向上取整，末行因此最短：
        # 《涅朵奇卡：/ 一个女人 / 的一生》读得通，向下取整会折成「一个女 / 人的一生」，
        # 把「一生」劈开，竖版封面上看着像错字。
        remaining = len(body) - i
        target = i + -(-remaining // (n - k + 1))
        lo, hi = i + 1, len(body) - (n - k)
        cut = min(max(target, lo), hi)
        on_mark = False
        for j in range(max(lo, target - 4), min(hi, target + 4) + 1):
            if body[j - 1] in marks:
                cut = j
                on_mark = True
                break
        cut = _better_cut(body, cut, lo, hi, bounds, on_mark, ban_words)
        parts.append(body[i:cut])
        i = cut
    parts.append(body[i:])
    return [p for p in parts if p]


def _wrap_title(text, size, max_lines=3, ban_words=True):
    """折成装得下的最少行数；书名号只在整组的首尾各出现一次。

    旧写法把已经折过、只剩单侧括号的片段再丢回 _split_title，那个函数用
    `text[1:-1]` 剥括号，遇到 '一个女人的一生》' 这种开头没有 《 的片段就不剥，
    照样给它补一个 《 在前、再在末尾贴一个 》，于是 13 字的本期书名交出了
    ['《涅朵奇卡：', '《一个女人', '的一生》》'] 这种双括号三行（2026-10-01 封面）。
    先把括号剥干净再折行，最后统一贴回首尾，就不会重复。
    """
    body, lead, tail = _strip_brackets(text)
    for n in range(1, max_lines + 1):
        parts = _split_balanced(body, n, ban_words)
        if len(parts) != n:
            continue
        if n == 1:
            lines = [lead + parts[0] + tail]
        else:
            lines = [lead + parts[0]] + parts[1:-1] + [parts[-1] + tail]
        # 中文禁则断言：任何一行都不许以点号/后半括号/间隔号开头。
        # 断点默认落在标点之后，所以这条通常不响；它挡的是「把禁则记反了去让位」这类改动，
        # 以及 _split_balanced 在没有标点可落时按字数硬切出来的怪行。
        if any(ln.startswith(NO_LINE_START) for ln in lines[1:]):
            continue
        if all(_fit(ln, size) for ln in lines):
            return lines
    return None


def _wrap_hook(text, size):
    """钩子最多两行：优先在句读处断，断完还超宽就降一档字号再断。

    没有钩子时返回 `([], size)` 而不是 `[]`：调用方是 `hook_lines, hook_size = _wrap_hook(...)`
    两元解包，返回空列表会当场 ValueError 崩在解包那一行（2026-10-02 实测：
    `EPISODE=一个人的村庄` 即任何 brief.json 还没有 cover_hook 的期，重跑封面必挂）。
    三段结构要求钩子行，但「这一期还没补钩子」是回补期的正常中间态，
    该警告不该变成崩溃；缺钩子这件事由 hook-backfill-status 的封面那一格判红。
    """
    if not text:
        print(f"[warn] 本期没有 cover_hook：封面将只有书名与作者，没有钩子行。"
              f"补上钩子文案后重跑本脚本。")
        return [], size
    if len(text) > HOOK_CHARS:
        raise SystemExit(f"COVER_HOOK {len(text)} 字，超过封面钩子上限 {HOOK_CHARS} 字")
    marks = ("，", "。", "？", "！", "；", "、")
    for size in range(size, HOOK_MIN - 1, -4):
        if _fit(text, size):
            return [text], size
        best = None
        for i, ch in enumerate(text):
            if ch in marks and 0 < i < len(text) - 1:
                # 同一条禁则也管钩子行：断出来的第二行不能以点号/间隔号开头
                if text[i + 1] in NO_LINE_START:
                    continue
                cand = (i + 1, text[:i + 1], text[i + 1:])
                if best is None or abs(len(cand[1]) - len(cand[2])) < abs(len(best[1]) - len(best[2])):
                    best = cand
        if best and _fit(best[1], size) and _fit(best[2], size):
            return [best[1], best[2]], size
    raise SystemExit(f"COVER_HOOK 在 {HOOK_MIN}px 下仍宽 {int(_w(text, HOOK_MIN))}px，"
                     f"超过安全宽度 {SAFE_W}px")


def wrap_title_lines(text):
    """书名排版的**唯一入口**：返回 (行, 字号, 字号下限, 警告)。

    这段阶梯原来直接摊在画图代码里，于是 scripts/check-cover-wrapping.py 想回归全部书名
    时只能照抄一遍——抄出来的那份和真的那份一旦漂移，回归就是在测另一个程序
    （AGENTS.md 反复踩的那条：判据只能有一处定义）。现在出图口和回归脚本调同一个函数，
    画图段只负责把返回的行贴到纸上。

    一律单行——2026-10-08 用户裁决「修改视频封面图中的书名排版，不要有换行，并沉淀这个规则」。
    原「单行装不下就折行」的第②支（2026-10-07 那一版）已退役，`_wrap_title` 只活在
    `check-cover-wrapping.py::old_ladder()` 的对照回放里。

    装不下就缩字号——2026-10-09 用户裁决「根据合适排版进行缩小字号」。上一版把两条裁决的冲突
    交给 `cover_title`（给封面另起一个短名）去解，用户否掉了这条路：封面印的必须是正式书名。
    所以现在阶梯只有一支：从 TITLE_MAX 往下找单行装得下的最大字号，一直找到 TITLE_ABSOLUTE_MIN。
    掉到 TITLE_LEGIBLE_MIN（96px，2026-10-01 那条「字体模糊不清晰」的量）以下**照出**，但必须
    留一条 note——这是用户点名要的代价，代价要每次都在输出里看得见，不许靠调阈值让它消失。
    到绝对下限还装不进（现目录没有这种期，最长 15 字正好 60px），才报错指名 `cover_title`。
    """
    notes = []
    size = _fit_size(text, TITLE_MAX, TITLE_ABSOLUTE_MIN)
    if not _fit(text, size):
        raise SystemExit(
            f"书名在绝对下限 {TITLE_ABSOLUTE_MIN}px 下单行宽 {int(_w(text, TITLE_ABSOLUTE_MIN))}px，"
            f"超过安全宽度 {SAFE_W}px：{text}（{len(text)} 个字符）。"
            f"按 2026-10-08 裁决封面书名不折行、按 2026-10-09 裁决装不下就缩小字号，"
            f"但缩小有绝对下限 {TITLE_ABSOLUTE_MIN}px（＝钩子行的最小字号，再往下书名比钩子还小）——"
            f"请在 episodes/<书名>/brief.json 写 cover_title 用一个更短的封面用名，"
            f"display_title 保持不变。")
    if size < TITLE_LEGIBLE_MIN:
        notes.append(
            f"书名低于清晰下限：单行只能长到 {size}px（清晰下限 {TITLE_LEGIBLE_MIN}px），"
            f"{len(text)} 个字符在 {SAFE_W}px 单行里的物理上限就是这么大，"
            f"信息流缩略图里约 {max(1, round(size / 4))}px 高。"
            f"这是 2026-10-09「根据合适排版进行缩小字号」点名要的代价，所以它必须每次都被打出来："
            f"不要动 TITLE_LEGIBLE_MIN / TITLE_ABSOLUTE_MIN / SAFE_W 这三个数让警告消失，"
            f"也别在这一期偷偷写 cover_title 改名——改名只留给连绝对下限都装不进的书名")
    elif size < TITLE_MIN:
        notes.append(f"书名保持单行，字号降到 {size}px（常规舒适下限 {TITLE_MIN}px）："
                     f"{len(text)} 个字符在 {SAFE_W}px 单行里只能长这么大，信息流里会偏小")
    return [text], size, TITLE_ABSOLUTE_MIN, notes


# ---------------------------------------------------------------- 1. 底图
im = Image.open(SRC).convert("RGB")
scale = max(W / im.width, H / im.height)
im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
left = (im.width - W) // 2
top = (im.height - H) // 2
cover = im.crop((left, top, left + W, top + H))
# 上采样（bridge 1024x1365 → 1440x1920，1.4x）会把照片细节抹平，整张读起来就是「糊」。
# 文字要清晰，底图也得先把边补回来，否则再大的字也贴在一团雾上。
cover = cover.filter(ImageFilter.UnsharpMask(radius=2.2, percent=85, threshold=3))

# ---------------------------------------------------------------- 2. 文字块布局
# 折行阶梯本身在 wrap_title_lines()（出图口和 check-cover-wrapping.py 共用一份），
# 这里只把它的警告打出来。旧写法把这段摊在画图代码里，回归脚本只能照抄，
# 一抄就有两份判据会漂移。
title_lines, title_size, title_floor, _wrap_notes = wrap_title_lines(title)
for _n in _wrap_notes:
    print(f"[warn] {_n}")
title_leading = int(title_size * 1.14)

if _fit(tag, TAG_SIZE):
    tag_size = TAG_SIZE
elif _fit(tag, 48):
    tag_size = _fit_size(tag, TAG_SIZE, 48)
else:
    raise SystemExit(f"COVER_TAG 在 48px 下宽 {int(_w(tag, 48))}px，超过安全宽度 {SAFE_W}px")

hook_lines, hook_size = _wrap_hook(hook, HOOK_MAX)

# 断言而不是口头约定：字号低于绝对下限就说明阶梯被绕过了。
# 一律单行（2026-10-08）＋ 装不下就缩字号（2026-10-09「根据合适排版进行缩小字号」）之后，
# 低于 TITLE_LEGIBLE_MIN 是**合法且用户点名要的代价**，在这里打警告而不是报错；
# 报错只留给「连 TITLE_ABSOLUTE_MIN 都装不进」那一种，那种由 wrap_title_lines() 拦。
if title_size < TITLE_ABSOLUTE_MIN:
    raise SystemExit(f"书名 {title_size}px < 绝对下限 {TITLE_ABSOLUTE_MIN}px：{title_lines}，"
                     f"wrap_title_lines 本该在出图前拦住它——走到这里说明判据被绕过了")
elif title_size < TITLE_LEGIBLE_MIN:
    print(f"[warn] 本期封面书名 {title_size}px，低于清晰下限 {TITLE_LEGIBLE_MIN}px："
          f"{len(title)} 个字符在安全宽度 {SAFE_W}px 里的物理上限就是这么大，"
          f"信息流缩略图里约 {max(1, round(title_size / 4))}px 高"
          f"（2026-10-09 裁决点名要的代价。不许动阈值来让这行消失；写 cover_title 也不是出路，"
          f"那一档只留给连 {TITLE_ABSOLUTE_MIN}px 都装不进的书名——真嫌糊要改的是 display_title 本身）")
elif title_size < TITLE_MIN:
    print(f"[warn] 本期封面书名 {title_size}px，低于常规下限 {TITLE_MIN}px："
          f"单行不折行的代价（{len(title)} 个字符 ÷ 安全宽度 {SAFE_W}px），信息流里会偏小")
if not all(_fit(ln, title_size) for ln in title_lines):
    raise SystemExit(f"书名单条超宽：{title_lines} @{title_size}px > {SAFE_W}px")
if len(title_lines) != 1:
    raise SystemExit(f"封面书名按 2026-10-08/10-09 裁决必须单行（装不下缩字号），实得 {title_lines}")
# 层级检查：缩字号合法的边界上还有一种「不合适」的排版——书名比钩子还小。
# 只报警不拦出图：13 期里有几期的正式书名在 950px 单行里最多 60~64px，而钩子按
# HOOK_MIN=60 出字，两者贴平；真要拉开层级该改的是这一期的封面用名或钩子字数，
# 不是把 HOOK_MIN 调小（那会伤到其余 139 期）。
if hook_lines and hook_size > title_size:
    print(f"[warn] 层级倒了：书名 {title_size}px 比钩子 {hook_size}px 还小"
          f"（《{title}》{len(title)} 个字符装不进更大的一行）")

tag_box = int(tag_size * 1.24)
hook_box = int(hook_size * 1.32)
block_h = (len(title_lines) * title_leading + GAP_TITLE_TAG + tag_box
           + (GAP_TAG_HOOK + len(hook_lines) * hook_box if hook_lines else 0))
block_top = int((H - block_h) / 2)
if abs((block_top + block_h / 2) - H / 2) > BLOCK_CENTER_TOL:
    raise SystemExit("文字块没有落在画幅中部，检查布局常量")


def _measure(line_sizes):
    return max(_w(t, s) for t, s in line_sizes)


pad_x = 76
band_w = min(W, _measure(
    [(ln, title_size) for ln in title_lines] + [(tag, tag_size)] +
    [(ln, hook_size) for ln in hook_lines]) + pad_x * 2)

# ---------------------------------------------------------------- 3. 居中暗色垫底带
# 一条上下羽化的横向暗带，把文字从画面里「垫」出来；亮度只有 0~118/255，
# 不改变这张图的氛围，只把对比度抬到能读清。
band = Image.new("L", (W, H), 0)
bd = ImageDraw.Draw(band)
FEATHER = 150
y0, y1 = block_top - FEATHER, block_top + block_h + FEATHER
x0 = max(0.0, (W - band_w) / 2)
for y in range(max(0, y0), min(H, y1)):
    d = min(y - y0, y1 - y) / FEATHER
    a = int(118 * min(1.0, d))
    bd.line([(x0, y), (min(W - 1.0, x0 + band_w), y)], fill=a)
band = band.filter(ImageFilter.GaussianBlur(26))
cover = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), cover, band)

# ---------------------------------------------------------------- 4. 投影 + 落字
# 一个游标从上往下走，每行都按 anchor="ma"（水平居中、垂直按 ascender 顶）落笔，
# 行高由各自的 box 决定——旧版把 1430/1540/1620 写死成三个绝对 y，
# 换字号就会互相重叠，也没法整体居中。
text_layer = Image.new("L", (W, H), 0)
td = ImageDraw.Draw(text_layer)
y = block_top
for ln in title_lines:
    td.text((W // 2, y), ln, font=_face(title_size), fill=255, anchor="ma")
    y += title_leading
y += GAP_TITLE_TAG
td.text((W // 2, y), tag, font=_face(tag_size), fill=245, anchor="ma")
y += tag_box
if hook_lines:
    y += GAP_TAG_HOOK
    for ln in hook_lines:
        td.text((W // 2, y), ln, font=_face(hook_size), fill=250, anchor="ma")
        y += hook_box

# 柔和投影：把字形蒙版高斯模糊后当「压暗量」用，笔画边缘干净，字心里不糊任何东西。
shadow = text_layer.filter(ImageFilter.GaussianBlur(7)).point(lambda v: int(v * 0.55))
cover = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), cover, shadow)
white = Image.new("RGB", (W, H), (255, 255, 255))
cover = Image.composite(white, cover, text_layer)

print(f"[cover] 书名 {title_size}px / {len(title_lines)} 行 {title_lines}")
print(f"[cover] tag {tag_size}px / 钩子 {hook_size}px / {len(hook_lines)} 行 {hook_lines}")
print(f"[cover] 文字块 y {block_top}–{block_top + block_h}（画幅中心 {H // 2}）")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
cover.save(OUT)
print(f"[cover] {OUT} {cover.size}")
