#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第十四道门：正文段起幅那张「本书封面图」真的在像素里，而且**只**在水波那一段里在。

用户 2026-10-07 提了两件事——「增加本书的封面图，从微信读书或网上获取」和「在固定开场白
结束后跟水波效果一同出现」，并在追问里选了「只跟水波那 1.4 秒」。这两句话各自都有一个
已经量过的失败模式，所以这道门判三个方向：

  1. **该有没有** → 红。规则和《书名》层、常驻声明一模一样：卡框写在 lib、层画在渲染器里，
     都不等于它进了成片。《恶意》2026-10-01 的 `showBookMark: false` 早就在生成器里，
     成片拼的还是改标志之前渲的 `hook.mp4`，七道门全绿。
  2. **该走没走** → 红。这一层是「跟水波那 1.4 秒」，不是常驻层：一张 300×430 的卡片
     常驻整段，第一张氛围图上的字幕会开始和它抢地方，而字幕取样带（y 680..850）与
     闪屏取证窗口（y<700）正是它必须离开才不越界的两道地盘。常驻的封面会把这两道门
     变成假绿灯，所以「它走了」和「它来过」是同一句话的两半。
  3. **在错的地方** → 红。钩子段带它 = 把书目卡片搬回第一屏（同 gate 8 对《书名》层的理由）。

证人（每个都只比**成片像素**，不读 CSS、不读渲染日志）：

  证 0a（盘上对账）：`<TMP>/book-cover.png` 与 `lib/book_cover.overlay()` 现算的墨迹作 IoU。
     对不上 = 换过封面图或改过 slot 却没重渲。这一层是渲染器要合成进画面的那一层，
     它自己就和 lib 不是同一份时，像素再怎么对也没意义。
  证 0b（时序 + 字节对账）：`<TMP>/book-cover.json`（渲染时写下的证人）里的三个时刻必须由
     `lib/book_cover.timing()` 用现算的常量复现，它记下的桥接卡退场时刻必须等于
     `<TMP>/body-bg.json` 切镜表的 `layer_start[1]`，它记下的叠层字节 sha256 必须等于 lib
     现算叠层的 sha256。第一句钉住「lib 改了 ≠ 像素改了」，第二句钉住「封面按上一版切镜表
     定的时，背景后来重切了」，第三句钉住「换了一本书的图」——证 0a 的 IoU 只比 alpha 形状，
     而任何两张封面图的内接形状一模一样（300×400 就是 300×400），换图不换渲染时它是 1.000。
  证 0c（溯源）：`lib/book_cover.provenance_ok()` 不合格就红。渲染器在合成前 raise 过同一条，
     这一遍是成片侧复核：手工把 jpg 拖进期目录、或换图没重写旁证，像素照样能和「盘上那张」
     对账成功，唯一能说出它是哪本书的封面的就是旁证里的 URL + sha256。
  证 A（在）：正文起幅的平台期（水波起振那一帧 → 涟漪收口，即淡出起点）内取若干帧，卡框本体
     （`core_mask`，不含投影裙边）的平均 RGB 必须与 lib 现算的封面像素相符
     （≤ `max_box_rgb_diff`）；有 `body-bg.mp4` 时再加一道「成片帧 − 同期背景帧」的残差
     墨迹，与 lib mask 在卡框内的 IoU ≥ `match_min_iou`。RGB 单独就能证明屏幕上那块
     是**这本书**的封面（不是黑块、不是上一期的卡）；残差 IoU 单独就能证明它是
     **长出来的一层**而不是背景底板自己——把封面烤进 `images-body/result-bridge.png`
     会让 RGB 通过而残差为零，而那张底板是逐字节拷进共享片头结果页的，烤进去就等于
     把封面搬进了 2026-10-05 裁决冻结的片头。两个一起才闭合。
     红分三种说法，各指一条修法：残差为零而 RGB 相符 = 烤进底板；RGB 不符而残差在 = 叠错了图；
     两半都不过 = 这一层没上屏。
  证 B（走）：`gone_at` 之后（含正文中后段和临近结尾）同一批像素必须**不再**与封面相符，
     且有背景时卡框内成片与背景帧的差 ≤ `max_absent_box_rgb_diff`。
  证 C（钩子）：钩子窗内按像素求时间 std，比「封面本体那片」与「卡框内其余」。底下是
     两张在缓推/互溶的照片，静止卡片会让那片的 std 塌下去；再要求那片 RGB 同时与封面
     相符，两个独立巧合同时成立才判红。背景本身没动时打印「无从判定」，不判红。
  证 D（动效轨迹）：平台期每一帧都**反解**封面的缩放系数 ŝ（`fit_cover_scale()`：在缩放网格
     上找「lib 那张封面按 s 摆到该位置」与成片卡芯的平均 RGB 差最小者），判三件事——
     ŝ 与 `lib/book_cover.zoom_at` 的偏差（「烘的是上一版动效形状」）、ŝ 首末帧的行程
     （「根本没长开」）、以及按 ŝ 两端点反解的卡芯矩形是否越出 slot 允许的那条带
     （`_assert_clear_of_reserved_bands()` 只看静态 slot，看不见动效把卡芯顶进书名层那 19px）。
     三条判线全部按 2026-10-08 的二十期普查定线（健康的 3 期 / 静止的 11 期 / 被否决的整数取整
     4 期，数字写在常量区），另有一条**失盲就停手**的线：最优拟合仍差 40 级以上（拿别书封面拟
     本期像素的负对照实测 102.8）时 ŝ 是噪声，打印「无从判定（不是通过）」而不是一个红或一个绿。
     「放大过程不流畅，抖动得厉害」里的**亚像素**抖动不在这里判：残差墨迹沿的噪声底子在
     静止卡上就有 1px 级（实测三期，见 `EDGE_REV_FLOOR_PX` 的注释），那把尺判「抖」和判
     「不抖」都会错，所以它只出报告行；亚像素流畅度由 lib 的 device 断言 +
     `scripts/tests/test-cover-motion-smoothness.py` 持有，成片这一侧持有的是轨迹本身。

存量不炸：只有当该期的渲染中间产物里已经有 `book-cover.png`（= 由加了这层的渲染器出片），
或者本期目录里已经有封面图，才判红；否则打印 ⚠️ 无从证明、退出 0。`--require` 强制判。

用法：
  python3 scripts/check-book-cover-overlay.py <书名>
  python3 scripts/check-book-cover-overlay.py <书名> --final x.mp4 --tmp DIR \\
      --body-at 10.17 --hook-dur 7.17 --require
  python3 scripts/check-book-cover-overlay.py --ep-dir DIR --final x.mp4 --tmp DIR2 \\
      --body-at 0 --hook-dur 4 --require        # 成片与期目录都在仓库外（回归测试）
退出码：0 通过或无从证明；1 至少一项判红。
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import book_cover as B  # noqa: E402
from water_ripple import WATER_DUR, WATER_OFFSET  # noqa: E402

W, H = 720, 960
INK_THRESH = 24          # 残差墨迹阈值：与 qc-caption-alignment.py / 第 12 道门同量级
MOTION_FLOOR = 5.0       # 证 C 前置：卡框内「封面以外」的时间 std，太低就无从判定静止
STATIC_RATIO = 0.40      # 封面那片 std / 其余 std 低于这个比才算有一层静止的东西压着
EDGE_IN = 0.10           # 平台期取样让开淡入收口的秒数
EDGE_OUT = 0.08          # 平台期取样让开淡出起点的秒数
ABSENT_AFTER = 0.25      # 该走之后的第一个取样点：淡出收口 + 这点余量
PNG_IOU_MIN = 0.90       # 盘上叠层 vs lib 现算
# ── 证 D（动效轨迹）的数，每一个都是**量出来的**，不是「调到能过」的旋钮 ──────────
# 这一条证在 2026-10-08 换过一次仪器：原来量「残差墨迹的两条沿」，实测下来那把尺既判不成
# 抖、也判不成不抖（见 EDGE_REV_FLOOR_PX 那条注释），现在量的是逐帧反解的缩放系数 ŝ。
FIT_S_LO, FIT_S_HI = 0.60, 1.40   # ŝ 的搜索区间：要同时装得下 lib 现在的 0.82→1.00、被否决过的
                                  # 1.00→1.12，和「根本没动效」那条 1.00 直线。端点离这三条轨迹
                                  # 都远，不会把最优值截在边界上
FIT_COARSE, FIT_FINE = 0.02, 0.0025   # 两级网格：粗搜定谷、细搜收口。0.0025 × 卡芯 299px =
                                      # 0.75px，比一帧的合法步长（1.4s 行程 ÷ 30fps ≈ 0.65px）
                                      # 还细，所以它不会把真实位移读成量化台阶
FIT_MAX_ERR = 40.0       # 不判线：卡芯按最优 ŝ 仍与成片差这个数以上，说明那块像素根本不是这张
                         # 封面（被别的层盖住／整体压暗／body-bg 与成片不是同一代／盘上 jpg 换过
                         # 而证人没重记），此时 ŝ 是噪声不是轨迹，只能说「无从判定」。
                         # 2026-10-08 二十期普查把这条线定在两个族群之间：能被反解出来的成片，
                         # 谷高（最优拟合的平均 RGB 差）中位 1.83..10.46、最大 14.25；量不出来的
                         # ——拿别书封面拟本期像素的负对照——中位 102.8、最大 109.7。40 距健康
                         # 2.8 倍、距失盲 2.6 倍。**这条线原来是 12.0，被普查否掉了**：健康的逐帧
                         # 浮点仿射成片本身就有 10.28/10.46（封面按 s 重采样过一次、成片又按 s
                         # 缩放并压进 h264，双重重采样的本底），12 会把健康期判成「无从判定」，
                         # 而真正失盲的成片（108）反而在旧线外头——一条只能靠巧合选对对象的线
FIT_MEAN_TOL_PX = 6.0    # |ŝ − zoom_at| 折算成卡芯像素后允许的**平均**偏差。二十期普查的两头：
                         # 健康的逐帧浮点仿射（反抗者／八十天环游地球／昆虫记）1.31..1.34px，
                         # 没有动效的静止卡（11 期）17.33..18.18px，被否决的整数取整 1.00→1.12
                         # （4 期）39.2..40.7px。6.0 = 健康的 4.5 倍、最近缺陷的 1/2.9
FIT_PEAK_TOL_PX = 12.0   # 同一偏差允许的**单帧峰值**：健康成片最坏一帧 2.87px（试过 5 倍／8 倍
                         # 细网格，峰值不降，所以它是仪器本底不是量化台阶），静止卡 39.9..41.9px，
                         # 整数取整 48.3..53.0px
FIT_TRAVEL_MIN = 0.5     # ŝ 首末帧之差折算的行程至少是 lib 预测的一半，否则「动效没落屏」。
                         # 取一半而不是 1.0：普查实测健康成片 41.1px vs 预测 41.3px（99.5%），
                         # 静止卡 0.0px（0%），整数取整 20.2/20.9/29.9px（48.9%/50.7%/72.4%）——
                         # 这一条专门管「压根没缩放」，轨迹错得再多由上面两条线先接住
FIT_BAND_TOL_PX = 3.0    # 按 ŝ 两端点反解的卡芯矩形压进保留带允许的越界。这是**真实足迹**，
                         # 不像 `_assert_clear_of_reserved_bands()` 只能量静态 slot——1.00→1.12
                         # 那一版顶进书名层 19px 时全库门禁全绿，就是缺了这一条
FIT_MIN_FRAMES = 8       # 逐帧反解需要连续样本；少于这个数只能说不判，不能说是通过
MEASURE_PAD = 48         # 残差沿取样窗比包络再让出的像素：实测健康成片的沿落在 slot 外
                         # 9.7..18.2px（投影裙边），整数取整合片落在 25.5..35.0px，两头都要装得下。
                         # 窗口必须比包络宽，否则卡体一旦超出包络（＝正是要抓的那种缺陷），两条沿
                         # 会被裁在窗边、量出「回退 0、外扩 0」——第一次跑这条证时判红来自「没长开」
                         # 的误打误撞而不是来自轨迹本身，一条只能靠巧合变红的证不叫证据
EDGE_REV_FLOOR_PX = 1.0  # 回退计入下限，**只用于那行报告**：残差墨迹剖面的噪声底子在静止卡上
                         # 就有 1px 级（实测《围城》4/1、《呐喊》0/6、《乞力马扎罗的雪》4/2 处回退），
                         # 和被否决的整数取整装置同量级——一把量不出 0.3px 而能量出 1px 噪声的尺，
                         # 判「抖」和判「不抖」都会错，所以它不配持有判决



def ff_probe_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 3)
    except ValueError:
        return 0.0



def grab(path, t, out):
    """抽一帧。必须带 -y：同名输出已存在时 ffmpeg 会弹「Overwrite?」，非交互下放弃写文件，
    留下的上一轮旧帧会把缺失报成通过（qc-caption-alignment.py 同一条注释）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
                        "-pix_fmt", "rgb24", out],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not os.path.exists(out):
        raise SystemExit(f"取帧失败 {path}@{t}: {r.stderr.decode()[-300:]}")
    return np.asarray(Image.open(out).convert("RGB"), dtype=np.int16)


FPS_CACHE = {}


def fps_of(path):
    """成片的帧率（一次 ffprobe 记住它）。取帧口径需要它，而它不许写成 30 的常量：
    兜底渲染器和 HyperFrames 都按 lib 的画幅出 30fps，但**门问的是那份像素**的网格。"""
    if path not in FPS_CACHE:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        try:
            num, den = r.stdout.strip().split("/")
            FPS_CACHE[path] = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            FPS_CACHE[path] = 0.0
    return FPS_CACHE[path]


def frames_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def grab_at(path, t, out, fps):
    """按**帧网格**取样：t 量化到第 round(t*fps) 帧，seek 落在它前半帧处。

    抄 qc-caption-alignment.py 的 seek_time 而不是自己发明：`f"{n/fps:.4f}"` 把无限循环小数
    往上进位，ffmpeg 取「pts ≥ seek」的首帧，于是读回来的是第 n+1 帧。这一层比字幕压测更需要
    它——证 A 的残差是「成片第 a 帧 − 背景第 b 帧」，两侧各自进位一次就会拿相邻帧相减，
    慢推的相邻帧差不到 INK_THRESH 就不算墨迹，而淡入收口附近那 0.02s 恰好够把点推出平台期。
    """
    if not fps:
        return grab(path, t, out)
    n = int(round(t * fps))
    return grab(path, max(0.0, (n - 0.5) / fps), out)



def iou(a, b):
    union = int((a | b).sum())
    return 0.0 if union == 0 else int((a & b).sum()) / union



def to_luma(rgb):
    return np.dot(rgb.astype(np.float64), [0.299, 0.587, 0.114])



def core_diff(crop, ref, core):
    """卡框本体（不含投影裙边）上的平均 RGB 差。

    用布尔索引取前两维得到 (N,3) 再一起求均值，而不是 `np.abs(a-b).mean(where=core)`：
    后者要求 `where` 能广播到通道轴，(H,W) 对 (H,W,3) 直接 shape 崩溃——这类坑在本仓库
    已经写过一次（`check-disclaimer-overlay.py` 的「轴写反了就是 (2047,)·(3,) 崩溃」）。
    """
    d = np.abs(crop.astype(np.float64) - ref.astype(np.float64))
    return float(d[core].mean()) if int(core.sum()) else 999.0



def card_edges_from_residual(res, min_col=6.0):
    """残差帧（成片 − 同期 body-bg）里这一层卡体的**亚像素左右沿**（列），量不到返回 None。

    为什么量两条沿而不是量宽度：观众说的「抖」是卡框边缘在两帧之间来回跳。被否决的整数
    取整装置（resize 到 int 再按整数偏移贴回）里，**宽度**是单调长的（int(W·s) 随 s 上去），
    抖的是**中心**——所以左沿和右沿各自都会偶尔回退一像素，而宽度序列一条道走到黑。
    只量宽度的门对那 24 处回退是瞎的，它绿了而用户照样看见抖。
    两条沿各自都必须单调向外（左沿不增、右沿不减），这条判据的形状才和缺陷一致。

    为什么不用 IoU：IoU 把形状差异摊成一个分数，1.4 秒里 0.98 与 0.97 谁也不像「抖」。
    这里沿列方向求墨迹剖面、在剖面跨越阈值处线性插值，所以 0.3px 的位移是可见的。
    阈值取相对值（`min_col` 与剖面峰值的 22% 取大者）而不是绝对墨迹数：淡入段整层 alpha
    还在爬，绝对阈值会把「半透明」读成「窄一张卡」。
    """
    ink = res > INK_THRESH
    prof = ink.sum(axis=0).astype(np.float64)
    peak = float(prof.max())
    if peak < min_col:
        return None
    k = max(min_col, 0.22 * peak)
    hit = np.where(prof >= k)[0]
    if hit.size < 2:
        return None
    lo, hi = int(hit[0]), int(hit[-1])
    if lo > 0 and prof[lo - 1] < k:
        left = (lo - 1.0) + (k - prof[lo - 1]) / max(1e-9, prof[lo] - prof[lo - 1])
    else:
        left = float(lo)
    if hi < prof.size - 1 and prof[hi + 1] < k:
        right = hi + (k - prof[hi + 1]) / max(1e-9, prof[hi] - prof[hi + 1])
    else:
        right = float(hi + 1)
    return left, right



def reversals(seq, floor_px):
    """序列里「去了又回」的次数：相邻两段位移幅度都 ≥ floor_px 且符号相反。

    和闪屏门禁的判据④同一形状（两条相邻反向腿各过一条幅度下限才算一次），因为「抖」的定义
    是方向回退，不是步子大：一条 1.5px 的匀步走 26 帧不抖，一次 −0.9px 的回退就抖。
    幅度小于 floor_px 的步不计（H.264 在软边缘上的量化噪声），所以一条单调序列必然 0 处。
    """
    steps = [b - a for a, b in zip(seq, seq[1:]) if abs(b - a) >= floor_px]
    return sum(1 for a, b in zip(steps, steps[1:]) if (a > 0) != (b > 0))



CX, CY = W / 2.0, H / 2.0   # 缩放原点：lib 的 zoomed_overlay 绕画幅中心，门必须绕同一个点



def core_rect_at(s, box):
    """卡芯（`card_box`，不含投影裙边）按 s 绕画幅中心缩放后的矩形——和 lib 同一个原点。"""
    bx0, by0, bx1, by1 = box
    return (CX + s * (bx0 - CX), CY + s * (by0 - CY),
            CX + s * (bx1 - CX), CY + s * (by1 - CY))



def cover_patch(card, s, gx0, gy0, gx1, gy1):
    """封面按 s 缩放后落在全局矩形 [gy0:gy1, gx0:gx1] 里的那块像素。

    和 `lib/book_cover.zoomed_overlay` 同一个仿射，只是把输出从整幅画布换成这一块裁片：
    输出列 i 对应全局 X = gx0 + i，源坐标 = CX + (X − CX)/s。整幅 transform 再裁会把
    720×960 的 RGBA 复制几十遍（每帧每个网格点一次），只出裁片的话输出就是卡芯大小。
    """
    inv = 1.0 / s
    return card.transform(
        (gx1 - gx0, gy1 - gy0), Image.AFFINE,
        (inv, 0.0, CX + (gx0 - CX) * inv, 0.0, inv, CY + (gy0 - CY) * inv),
        resample=Image.BICUBIC)



def fit_cover_scale(film, card, box, margin=4):
    """在这一帧里反解封面卡芯的缩放系数 ŝ：让「lib 那张封面按 ŝ 摆到该位置」与成片像素的
    平均 RGB 差最小。返回 (ŝ, 最小差, 谷深) 或 (None, None, None)，谷深＝粗搜网格最差值 − 最优值。

    为什么靠拟合而不是直接量墨迹的两条沿：卡芯是 alpha=1 的不透明区，成片在那儿的像素**就是
    封面本身**，与底下正在缓推的氛围图无关；缩放错一点，封面上的字和图纹就对不上，误差立刻涨。
    反过来，「沿在哪里」问的是墨迹剖面跨过阈值的位置，而那条剖面的噪声底子在静止卡上就有
    1px 级（见 EDGE_REV_FLOOR_PX 的实测三行），和被否决的整数取整装置同量级。

    为什么要谷深：ŝ 只有在**那条误差曲线真的有一个谷**时才是轨迹。卡芯被别的层盖住、整块压暗、
    或者成片那块根本不是这张封面时，曲线可以是平的——平的曲线照样会给出一个 ŝ，而那个 ŝ 是噪声。
    没有谷深这个数，证 D 会拿着一把量不到东西的尺印出一个红或一个绿，两种都是假的。
    """
    def err_at(s):
        x0, y0, x1, y1 = core_rect_at(s, box)
        gx0, gy0 = int(round(x0)) + margin, int(round(y0)) + margin
        gx1, gy1 = int(round(x1)) - margin, int(round(y1)) - margin
        if gx1 - gx0 < 40 or gy1 - gy0 < 40:
            return None
        obs = film[gy0:gy1, gx0:gx1].astype(np.float64)
        pred = np.asarray(cover_patch(card, s, gx0, gy0, gx1, gy1),
                          dtype=np.float64)[:, :, :3]
        if pred.shape != obs.shape:
            return None
        return float(np.abs(pred - obs).mean())

    best_s, best_e, worst_e = None, None, None
    n_coarse = max(2, int(round((FIT_S_HI - FIT_S_LO) / FIT_COARSE)))
    for i in range(n_coarse + 1):
        s = round(FIT_S_LO + FIT_COARSE * i, 4)
        e = err_at(s)
        if e is None:
            continue
        if best_e is None or e < best_e:
            best_s, best_e = s, e
        worst_e = e if worst_e is None else max(worst_e, e)
    if best_s is None:
        return None, None, None
    for i in range(int(round(FIT_COARSE / FIT_FINE)) + 1):
        s = round(best_s - FIT_COARSE + FIT_FINE * i, 4)
        if s < FIT_S_LO or s > FIT_S_HI:
            continue
        e = err_at(s)
        if e is not None and e < best_e:
            best_s, best_e = s, e
    return best_s, best_e, (worst_e - best_e) if worst_e is not None else 0.0



def find_tmp_dir(ep_name, explicit):
    cands = [explicit] if explicit else []
    if os.environ.get("FALLBACK_TMP"):
        cands.append(os.environ["FALLBACK_TMP"])
    if ep_name:
        cands += [os.path.join(ROOT, "tmp", f"{ep_name}_tpl"), f"/tmp/{ep_name}_tpl"]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return None



def body_start_default(ep_dir, intro_trim):
    hook = hook_dur_default(ep_dir)
    return round(hook + intro_trim, 3), hook



def hook_dur_default(ep_dir):
    if not ep_dir or not os.path.exists(os.path.join(ep_dir, "script-hook.csv")):
        return 0.0
    for name in ("hook-voiceover-story.mp3", "hook-voiceover.mp3", "钩子配音.mp3"):
        p = os.path.join(ep_dir, "audio", name)
        if os.path.exists(p):
            return ff_probe_dur(p)
    return 0.0



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", nargs="?", default="")
    ap.add_argument("--final", default="")
    ap.add_argument("--tmp", default="")
    ap.add_argument("--body-at", type=float, default=-1.0,
                    help="正文段在成片时间轴上的起点秒；默认按钩子配音 + 片头裁切点算")
    ap.add_argument("--hook-dur", type=float, default=-1.0)
    ap.add_argument("--ep-dir", default="",
                    help="期目录的绝对/相对路径覆盖：默认 episodes/<书名>。"
                         "回归测试与「成片在仓库外」的核对靠它拿到封面图与旁证，"
                         "没有期目录时这一层无从证明")
    ap.add_argument("--require", action="store_true",
                    help="即使这期没有封面图/中间产物也强制判（回归测试与新渲期用）")
    args = ap.parse_args()

    cfg = B.load()
    geo = B.geometry(cfg, (W, H))
    sx0, sy0, sx1, sy1 = geo["slot"]

    ep_dir = args.ep_dir or (os.path.join(ROOT, "episodes", args.episode) if args.episode else None)
    if ep_dir and not os.path.isdir(ep_dir):
        print(f"⚠️ 期目录不存在（{ep_dir}）：按外部成片核对，钩子段那半边不做判决")
        ep_dir = None
    final = args.final or (os.path.join(ep_dir, "renders", f"{args.episode}-final.mp4")
                           if ep_dir and args.episode else "")
    if not final or not os.path.exists(final):
        raise SystemExit(f"找不到成片：{final or '（未给 --final 且期目录下没有 renders）'}")
    tmp = find_tmp_dir(args.episode, args.tmp)
    png = os.path.join(tmp, "book-cover.png") if tmp else ""
    wit = os.path.join(tmp, "book-cover.json") if tmp else ""
    bg = os.path.join(tmp, "body-bg.mp4") if tmp else ""
    bg_sig = os.path.join(tmp, "body-bg.json") if tmp else ""

    source = B.source_path(ep_dir, cfg) if ep_dir else None

    with open(os.path.join(HERE, "lib", "intro-template.json"), encoding="utf-8") as f:
        intro = json.load(f)
    auto_body_at, hook_dur = body_start_default(ep_dir, float(intro["trim_seconds"]))
    body_at = args.body_at if args.body_at >= 0 else auto_body_at
    if args.hook_dur >= 0:
        hook_dur = args.hook_dur
    final_dur = ff_probe_dur(final)
    fps_final = fps_of(final)

    # 正文起点按**帧**对齐：秒数口径是「钩子配音(两位小数) + 模板裁切」加出来的，而装配是按帧
    # concat 的，两者能差 0.2 帧（qc-caption-alignment 同一条理由）。这一道门更吃紧——它问的窗
    # 只有涟漪收口减水波偏移那 1.06s，秒数早 0.77s（一个手抄的过期 --body-at 就够了）就把平台期
    # 的头两个采样点落进共享片头的结果页；而结果页是本期桥接卡的逐字节拷贝，残差几乎为零、本体
    # RGB 差一百一十二，正是「封面没上屏」的形状。取帧号 = 成片帧数 − 正文背景帧数。
    bg_frames_avail = bool(bg and os.path.exists(bg))
    if bg_frames_avail:
        _fin_f, _bg_f = frames_of(final), frames_of(bg)
        if _fin_f and _bg_f and fps_final and _fin_f > _bg_f:
            frame_at = round((_fin_f - _bg_f) / fps_final, 4)
            if abs(frame_at - body_at) > 1.5 / fps_final:
                print(f"⚠️ 正文起点退回按帧换算：给定 {body_at:.4f}s，而成片第 {_fin_f} − 背景第 "
                      f"{_bg_f} = {frame_at:.4f}s（相差 {abs(frame_at - body_at) * fps_final:.1f} 帧）——"
                      f"按后者取样。秒数口径过期通常意味着钩子段长度变过而调用方还在用旧数")
            body_at = frame_at

    # 带位从 lib 的 reserved_bands 打印，不在这行字里再抄一遍数字：那三个数是**别人**的
    # 取样区，抄来的说明会在它们搬家之后继续印着旧地盘，而 load() 的相交断言早已改成新的。
    _bands = {b["name"]: b for b in B.reserved_bands(cfg)}
    print(f"本书封面门禁  {os.path.relpath(final, ROOT) if final.startswith(ROOT + os.sep) else final}")
    print(f"  卡框可用矩形 x {sx0}..{sx1} y {sy0}..{sy1}（画幅 {W}x{H}，须避开："
          + " / ".join(f"{nm} y {int(_bands[nm]['y0'])}..{int(_bands[nm]['y1'])}"
                       for nm in ("caption_sweep", "disclaimer_line") if nm in _bands)
          + (f" / 书名层 y ≤{int(_bands['book_mark_layer']['y1'])}"
             if "book_mark_layer" in _bands else ""))
    print(f"  正文起点 {body_at:.2f}s　成片 {final_dur:.2f}s"
          + (f"　钩子窗 0..{hook_dur:.2f}s" if hook_dur else "　（无钩子段）"))

    # 爆炸半径：这层由渲染器写进中间产物；盘上没有 png 且本期也没有封面图 = 该期由
    # 加这层之前的渲染器出片。和 Pexels 溯源标记同一套「只在已经有标记的目录里要求标记」。
    demanded = bool(args.require or (png and os.path.exists(png)) or source)
    if not demanded:
        print(f"\n⚠️ 无从证明：{png or '（没有 --tmp）'} 里没有 book-cover.png，"
              f"且 {ep_dir or '（没有期目录）'} 下没有封面图 —— 这一期由加这层之前的渲染器"
              f"出片，不判红（重渲即纳入判决）")
        sys.exit(0)

    if not source:
        print(f"\n❌ 本书封面门禁：{ep_dir} 下没有 {cfg['asset']['filename']}，"
              f"而 --require / 中间产物指定了这一期必须带封面。"
              f"取图：python3 scripts/fetch-book-cover.py \"{args.episode}\"")
        sys.exit(1)

    ok_prov, note_prov = B.provenance_ok(ep_dir, cfg)
    print(f"  封面图 {os.path.relpath(source, ROOT)}　旁证：{note_prov if ok_prov else '❌ ' + note_prov}")

    ov = np.asarray(B.overlay(source, cfg, (W, H)).convert("RGBA"), dtype=np.float64)
    card_rgb = ov[..., :3]
    # 两片的「本体」与「含裙边」都问 lib，不在门里另写一遍阈值：渲染器叠的就是 lib
    # 那一张，门若自算一个 alpha≥0.95 的版本，改了 lib 的投影半径后两边各说各话。
    core = B.core_mask(source, cfg, (W, H))
    exp_mask = B.mask(source, cfg, (W, H))
    box = B.card_box(source, cfg, (W, H))
    bx0, by0, bx1, by1 = box
    crop = (max(0, sx0 - 4), max(0, sy0 - 4), min(W, sx1 + 4), min(H, sy1 + 4))
    cs, ce, cx0, cx1 = crop[1], crop[3], crop[0], crop[2]
    exp_core = core[cs:ce, cx0:cx1]
    exp_mask_c = exp_mask[cs:ce, cx0:cx1]
    card_c = card_rgb[cs:ce, cx0:cx1]
    if int(exp_core.sum()) < 2000:
        print(f"\n❌ 本书封面门禁：卡框本体只有 {int(exp_core.sum())}px，"
              f"封面源图异常或 slot 被收窄——取样本身没有意义")
        sys.exit(1)

    reds = []
    # 溯源不通过就判红，不只是打印：渲染器在合成前 raise 过同一条，但「有人把 jpg 手工
    # 拖进期目录」或「换图没重写旁证」的成片，像素照样能对账成功（门按盘上那张图算预期），
    # 那时唯一能说话的就是这张图到底从哪来。同 Pexels 那条标记链的理由。
    if not ok_prov:
        reds.append(f"封面图溯源不通过：{note_prov}（这一层上了屏，就得能说出它是哪本书的封面）")
    work = os.path.join(tmp or "/tmp", "_book_cover_qc")
    os.makedirs(work, exist_ok=True)

    # ── 证 0a：盘上叠层 vs lib 现算 ──────────────────────────────────────────
    if png and os.path.exists(png):
        disk = np.asarray(Image.open(png).convert("RGBA"))[..., 3] > 128
        v = iou(disk[cs:ce, cx0:cx1], exp_mask_c)
        print(f"\n[证 0a] {os.path.relpath(png, ROOT)} vs lib 现算墨迹：IoU={v:.3f}"
              f"　本体 {int(exp_core.sum())}px  卡框 x {bx0}..{bx1} y {by0}..{by1}")
        if v < PNG_IOU_MIN:
            reds.append(f"盘上 book-cover.png 与 lib 的 slot/封面图不符（IoU={v:.3f}）："
                        f"改过 scripts/lib/book-cover.json 或换过 book-cover.jpg 就得重渲")
    elif args.require:
        reds.append(f"{png or '--tmp 目录'} 里没有 book-cover.png，"
                    f"而 --require 指定了这一期必须叠这一层：渲染器没画它")

    # ── 证 0b：记录的时刻 vs lib + 背景自己的切镜表 ──────────────────────────
    # 三个时刻里只有两个是独立可算的：淡入 = lib 的水波偏移，淡出起点 = 涟漪收口，
    # 淡出长度 = 渲染器当时用的 XFADE。`bridge_exit`（body-bg.json 的 layer_start[1]，
    # 背景切镜表，独立于封面层自己）不参与算时刻，它是**下限自检 + 陈旧产物探针**：
    # 记录里的桥接卡退场和背景现在的切镜表对不上，说明背景重切过而封面层用的还是上一版。
    bridge_exit = None
    xfade = None
    if bg_sig and os.path.exists(bg_sig):
        ls = json.load(open(bg_sig, encoding="utf-8")).get("layer_start") or []
        if len(ls) > 1:
            bridge_exit = float(ls[1])
    COVER_T = None
    # 叠层字节是否对得上，证 A 的红话要用它来分家：`overlay()` 画的形状与 `motion` 无关
    # （缩放是逐帧现算的），所以「0b 绿 + 证 A 红」这个组合唯一可能的成因就是**成片烘的是
    # 上一版动效**，而不是换了一张图——两种红的修法相反（重渲 vs 重取图），不能合成一句。
    sha_ok = None
    if wit and os.path.exists(wit):
        w = json.load(open(wit, encoding="utf-8"))
        xfade = float(w.get("scene_xfade_seconds", 0.64))
        COVER_T = w.get("timing") or {}
        # 叠层字节对账：证 0a 的 IoU 只比 alpha **形状**，而任何两张封面图的内接形状是一样的
        # （300×400 就是 300×400）——换了一本书的图而没重渲，IoU 照样 1.000。渲染器把叠层字节
        # 的 sha256 钉在证人里，这里重算一次：字节不同 = 盘上这张 lib 图不是成片里那张。
        rec_sha = str(w.get("overlay_sha256") or "").strip().lower()
        if rec_sha and rec_sha != "none":
            import hashlib
            now_sha = hashlib.sha256(
                B.overlay(source, cfg, (W, H)).convert("RGBA").tobytes()).hexdigest()
            print(f"\n[证 0b] {os.path.relpath(wit, ROOT)} 叠层字节 sha256 "
                  f"{rec_sha[:12]} vs lib 现算 {now_sha[:12]}")
            sha_ok = (rec_sha == now_sha)
            if rec_sha != now_sha:
                reds.append(f"渲染时记下的封面叠层 sha256（{rec_sha[:12]}）与 lib 现算的叠层"
                            f"（{now_sha[:12]}）不符：换过 book-cover.jpg 没重渲，"
                            f"或改过 scripts/lib/book-cover.json 的 slot/投影")
        if bridge_exit is not None and \
                abs(bridge_exit - float(w.get("bridge_exit_seconds", -1))) > 0.02:
            reds.append(f"封面时序记的是桥接卡退场 {w.get('bridge_exit_seconds')}s，"
                        f"而背景切镜表现在是 {bridge_exit:.3f}s：背景重切过、"
                        f"封面层用的还是上一版时刻")
    elif png and os.path.exists(png):
        reds.append(f"{os.path.relpath(png, ROOT)} 存在，但同一目录没有 book-cover.json 时序证人："
                    f"三个时刻无从复现（重渲即恢复证人，不必手推秒数）")

    if bridge_exit is not None and xfade:
        try:
            fresh = B.timing(bridge_exit, xfade, cfg)
        except SystemExit as exc:
            reds.append(str(exc))
            fresh = None
        if fresh:
            for key in ("fade_in_seconds", "fade_out_at", "fade_out_seconds", "gone_at"):
                if key in COVER_T and abs(float(COVER_T[key]) - fresh[key]) > 0.02:
                    reds.append(f"记录的封面时刻 {key}={COVER_T[key]} 与 lib 现算 "
                                f"{fresh[key]} 不符：改过 scripts/lib/book-cover.json "
                                f"或水波常量没重渲")
                    break
            COVER_T = fresh
    elif COVER_T:
        # 背景切镜表读不到（外部成片、无中间产物）：证人时刻只能沿用记录，
        # 但 lib 的水波偏移是独立可算的，用它钉住淡入半边。
        COVER_T = dict(COVER_T)
    else:
        print("\n⚠️ 无从证明：既没有 body-bg.json 切镜表也没有 book-cover.json 时序证人，"
              "淡出时刻无从复现（残差/像素两半边仍按 lib 推导的水波窗口取样）")
        COVER_T = {"fade_out_at": None, "gone_at": None}

    fade_in = float(WATER_OFFSET)
    exit_at = COVER_T.get("fade_out_at")
    exit_at = fade_in + WATER_DUR if exit_at is None else float(exit_at)
    gone_at = COVER_T.get("gone_at")
    gone_at = exit_at + (xfade or 0.64) if gone_at is None else float(gone_at)
    print(f"\n[时刻] 淡入 0→{fade_in:.2f}s（= 水波偏移）　平台期 {fade_in:.2f}..{exit_at:.2f}s"
          f"　淡出 {exit_at:.2f}s+{gone_at - exit_at:.2f}s　{gone_at:.2f}s 起该走")

    # ── 证 A：平台期内那块像素必须是这本书的封面 ────────────────────────────
    plateau_lo = fade_in + EDGE_IN
    plateau_hi = exit_at - EDGE_OUT
    n_pre = 4 if plateau_hi > plateau_lo + 0.3 else 1
    pre_times = ([round(plateau_lo + (plateau_hi - plateau_lo) * k / max(1, n_pre - 1), 3)
                  for k in range(n_pre)] if n_pre > 1 else
                 [round((plateau_lo + plateau_hi) / 2, 3)])
    # 取样点必须**落在**平台期内，而边界比较要留 1e-6：lo/hi 是 0.32+0.10 / 1.38−0.08 这种
    # 十进制加减的产物（0.42000000000000004、1.2999999999999998），而 `round(t,3)` 把 0.42
    # 舍到 lo 之下、把 1.3 舍到 hi 之上——不加这点余量，四个计划采样点会悄悄只剩中间两个，
    # 平台期的两头（淡入刚收口 / 淡出即将起振）从此无人取样。
    EPS = 1e-6
    pre_times = [t for t in pre_times if plateau_lo - EPS <= t <= plateau_hi + EPS] \
        or [round((plateau_lo + plateau_hi) / 2, 3)]

    has_bg = bool(bg and os.path.exists(bg))
    fps_bg = fps_of(bg) if has_bg else fps_final
    pre_ok = []
    pre_rgb_ok = []
    pre_iou_ok = []
    print(f"\n[证 A] 平台期 {len(pre_times)} 点：卡框本体 RGB 对账"
          + (f" + 成片帧−背景帧残差 IoU（{os.path.relpath(bg, ROOT)}）"
             if has_bg else "（无背景中间产物，残差不判）"))
    for t in pre_times:
        frame = grab_at(final, body_at + t, os.path.join(work, "p.png"), fps_final)
        fc = frame[cs:ce, cx0:cx1]
        # 2026-10-08：封面出现时带 zoom_in 动效，预期帧必须按同一公式逐帧现算——
        # 静态比对会把对的动效像素判成「不是这本书的封面」。zoom_at 现查 timing + cfg。
        s = B.zoom_at(t, COVER_T, cfg)
        exp_ov_t = B.zoomed_overlay(B.overlay(source, cfg, (W, H)), s)
        exp_ov_t_f = np.asarray(exp_ov_t.convert("RGBA"), dtype=np.float64)
        card_c_s = exp_ov_t_f[..., :3][cs:ce, cx0:cx1]
        # 本体（不含投影）：重新按同一缩放现算的 core
        exp_ov_ns = B.zoomed_overlay(B.overlay(source, cfg, (W, H), draw_shadow=False), s)
        core_s = (np.asarray(exp_ov_ns.convert("RGBA"), dtype=np.float64)[..., 3] >= 250)[cs:ce, cx0:cx1]
        exp_mask_s = (exp_ov_t_f[..., 3] > 128)[cs:ce, cx0:cx1]
        d = core_diff(fc, card_c_s, core_s)
        line = f"  {t:7.3f}s  本体RGB差={d:6.2f}  （缩放×{s:.3f}）"
        rgb_ok = d <= cfg["max_box_rgb_diff"]
        ok = rgb_ok
        if has_bg:
            bb = grab_at(bg, t, os.path.join(work, "pb.png"), fps_bg)[cs:ce, cx0:cx1]
            ink = np.abs(fc - bb).max(axis=2) > INK_THRESH
            v = iou(ink, exp_mask_s)
            line += f"  残差IoU={v:.3f}（墨迹 {int(ink.sum())}px）"
            iou_ok = v >= cfg["match_min_iou"]
            ok = ok and iou_ok
            pre_iou_ok.append(iou_ok)
        pre_rgb_ok.append(rgb_ok)
        pre_ok.append(ok)
        print(line + ("　✅" if ok else "　❌"))
    good_a = sum(pre_ok)
    if good_a == 0:
        # 三句话各自对应三种红，合成一句会把手指出向错误的一边：
        #   残差为零而 RGB 相符 → 封面被**烤进 body-bg 底板**（result-bridge.png 是逐字节拷进
        #     共享片头结果页的，烤进去等于动到 2026-10-05 裁决冻结的片头）；
        #   残差在而 RGB 不符 → 那一块**长出的是另一张图**（叠错封面 = 《偏跟山过不去》2026-10-03
        #     那类「取图脚本说完成而盘上是旧照片」的形状错误）；
        #   两半都不过 → 这一层根本没上屏。
        if pre_iou_ok and not any(pre_iou_ok) and any(pre_rgb_ok):
            reds.append(f"正文起幅的卡框里 RGB 与 lib 的封面相符，但成片帧减背景帧的残差为零"
                        f"（{len(pre_times)} 点全部）：那一块是**烤进 body-bg 底板**的，不是合成"
                        f"上去的一层——封面必须留在叠层里，不许进 images-body/result-bridge.png")
        elif pre_iou_ok and all(pre_iou_ok) and not any(pre_rgb_ok):
            # 「长出了一层东西、形状对得上、内容对不上」有两种相反的修法，必须分家：
            # 叠层字节与 lib 一致（证 0b 绿）**只**排除「盘上的 jpg 换过而证人没重记」，因为
            # `overlay()` 画的是盘上那一张——它对「成片里叠的是另一本书」是全盲的（另一本书的
            # 封面从没进过证人）。所以这里真正的分岔问题是：**这块像素还是这张封面吗？**
            # 回答它的是证 D 那把尺，而不是 0b：把盘上那张封面按缩放网格去贴这一帧，
            #   谷高很小 → 是这张封面，只是**缩放走的是另一条 ramp**（lib 的 motion 改过而
            #     像素没重渲；2026-10-08 实测 v1 的 1.00→1.12 整数装置四点全红 29.79–33.30，
            #     对判红线 26.0，而 0b 的 sha 逐字节相同）→ 重渲即消，不换图、不动阈值；
            #   谷高仍然很大 → 任何缩放下都对不上，那是**另一张图**（叠错封面 = 《偏跟山过不去》
            #     2026-10-03 那类「取图脚本说完成而盘上是旧照片」）或这块像素与盘上那张不同代
            #     → 换图/重取，重渲不会把它变成对的。
            # 探针只跑一帧（平台期末点，lib 预测 ≈×1.0，缩放那一维最不需要外推），
            # 因为这是一个修辞判断而不是判决：红已经是红了，只是手指要指对边。
            probe_t = pre_times[-1]
            pf = grab_at(final, body_at + probe_t, os.path.join(work, "cls.png"), fps_final)
            _, probe_err, _ = fit_cover_scale(
                pf, B.overlay(source, cfg, (W, H)).convert("RGBA"), box)
            still_this_cover = probe_err is not None and probe_err <= FIT_MAX_ERR
            if sha_ok is True and still_this_cover:
                reds.append(f"正文起幅的卡框里长出了这一层、形状与 lib 相符（残差 IoU 全过），"
                            f"但画面里的缩放**不是 lib 现在算的那一条**（{len(pre_times)} 点的本体 "
                            f"RGB 全部超过 {cfg['max_box_rgb_diff']}，而证 0b 的叠层字节逐字节相同）："
                            f"这一版成片烘的是**上一版动效形状**（`scripts/lib/book-cover.json` 的 "
                            f"motion 改过而像素没重渲），不是叠错了图——{probe_t:.2f}s 那一帧把盘上"
                            f"这张封面按缩放网格去贴，最优仍能贴到平均差 {probe_err:.2f}"
                            f"（≤{FIT_MAX_ERR}），所以这块像素**就是这本书的封面**"
                            + (f" `bash scripts/render-hook-batch.sh \"{args.episode}\"`"
                               if args.episode else "（render-hook-batch 重渲本期）")
                            + "，唯一的修法就是它")
            elif sha_ok is True:
                reds.append(f"正文起幅的卡框里确实长出了一层东西（残差 IoU 全过），但它**不是这本书的"
                            f"封面**（{len(pre_times)} 点的本体 RGB 全部超过 "
                            f"{cfg['max_box_rgb_diff']}）：叠错了图，或这块像素与盘上那张 book-cover.jpg"
                            f" 不是同一代。分岔的证据是：{probe_t:.2f}s 那一帧把盘上这张封面在 "
                            f"[{FIT_S_LO}, {FIT_S_HI}] 整段缩放里贴到最好仍差 "
                            f"{(probe_err if probe_err is not None else float('nan')):.1f}"
                            f"（>{FIT_MAX_ERR}），所以不是「同一张图走错了 ramp」——那种情况重渲即消，"
                            f"这一种重渲只会把同一张错的图再搬上来一次"
                            + (f"，先 `python3 scripts/fetch-book-cover.py \"{args.episode}\"` 再重渲"
                               if args.episode else "（先重取封面再重渲）"))
            else:
                reds.append(f"正文起幅的卡框里确实长出了一层东西（残差 IoU 全过），但它**不是这本书的"
                            f"封面**（{len(pre_times)} 点的本体 RGB 全部超过 "
                            f"{cfg['max_box_rgb_diff']}）：叠错了图，或盘上的 book-cover.jpg 已经不是"
                            f"渲染时那一张（证 0b 没有叠层字节证人，无法排除换图）")
        else:
            reds.append(f"正文起幅的卡框里找不到这本书的封面（{len(pre_times)} 点全部不过）："
                        f"这一层没上屏，或被别的层盖住")
    elif good_a < len(pre_ok):
        # 平台期是**推导出来的**（水波偏移→涟漪收口），取样点又全部落在它内部，所以「有的点
        # 对账、有的点不对」不可能是一种合法形状：它要么是这一层只出现了一半（跟水波抖一下就
        # 消失），要么是正文起点给错、采样点掉进了淡入之前或淡出之后。两者都得重做，
        # 容忍到一半只是把第二种伪装成第一种。
        # 2026-10-09 加上第三种，而且是这半年最常见的一种：**这一层的缩放不走 lib 预测的那一条**。
        # 2026-10-08 动效改成 0.82→1.00 之后，淡入里那几点 lib 预测 ×0.858/×0.915，而一张静止卡
        # 或一条旧 ramp 在那两点上是别的尺寸——于是「前几点红、后几点绿」看起来和「只出现了一半」
        # 一模一样（实测：静止卡 fixture 红的正是 0.42s/0.71s 两点，1.30s 绿；《曼斯菲尔德庄园》
        # 3/4）。手指若被这句派去查 --body-at，查完回来还是红的。所以这里按红点的位置分岔，
        # 并把判决权交给下面证 D 那条逐帧反解的 ŝ 轨迹——它才是量尺度的那件仪器。
        miss = "、".join(f"{t:.2f}s" for t, ok in zip(pre_times, pre_ok) if not ok)
        late_ok = [t for t, ok in zip(pre_times, pre_ok) if ok]
        early_only = bool(late_ok) and all(
            t >= pre_times[0] + (pre_times[-1] - pre_times[0]) * 0.6 for t in late_ok)
        reds.append(f"平台期 {len(pre_times)} 点里有 {len(pre_ok) - good_a} 点不是这张封面"
                    f"（{miss}）：这一层要么只在部分时刻出现，要么正文起点取错了帧"
                    f"（门会按「成片帧数 − 正文背景帧数」退回按帧换算，见上面的 ⚠️ 行）"
                    + ("；不过对上的恰好是平台期后半那几点（"
                       + "、".join(f"{t:.2f}s" for t in late_ok)
                       + "）——淡入那几点 lib 预测的是更小的缩放，静止卡或上一版 ramp 正是只在"
                         "淡入里对不上。这不是起点问题，读下面证 D 的逐帧 ŝ 轨迹"
                       if early_only else ""))


    # ── 证 B：该走之后必须不再是封面 ─────────────────────────────────────────
    body_end = round(final_dur - body_at - 0.4, 3)
    post_times = [round(gone_at + ABSENT_AFTER, 3)]
    if body_end > gone_at + 1.0:
        post_times += [round(gone_at + 0.9, 3), round((gone_at + body_end) / 2, 3),
                       round(body_end - 0.3, 3)]
    post_times = [t for t in post_times if t < body_end and t > gone_at]
    bad_b = []
    if post_times:
        print(f"\n[证 B] {gone_at:.2f}s 之后 {len(post_times)} 点：卡框必须不再与封面相符")
        for t in post_times:
            frame = grab_at(final, body_at + t, os.path.join(work, "q.png"), fps_final)
            fc = frame[cs:ce, cx0:cx1]
            d = core_diff(fc, card_c, exp_core)
            line = f"  {t:7.3f}s  本体RGB差={d:6.2f}"
            still = d <= cfg["max_box_rgb_diff"]
            if has_bg:
                bb = grab_at(bg, t, os.path.join(work, "qb.png"), fps_bg)[cs:ce, cx0:cx1]
                db = core_diff(fc, bb, exp_core)
                line += f"  与背景差={db:6.2f}（判红线 ≤{cfg['max_absent_box_rgb_diff']}）"
                # 两个都要成立才算「封面还赖在卡框里」：像封面 **且** 不是背景自己。
                # 只按 or 判会把「烤进 body-bg 底板的封面」也算成常驻——那种情况该由
                # 证 A 的残差 IoU 红（残差为零 = 它不是长出来的一层），不是这里。
                still = still and db > cfg["max_absent_box_rgb_diff"]
            if still:
                bad_b.append(t)
            print(line + ("　❌ 封面还在" if still else "　✅ 已离开"))
        if bad_b:
            reds.append(f"封面在 {gone_at:.2f}s 之后仍然出现在卡框里（"
                        f"{'、'.join(f'{t:.2f}s' for t in bad_b)}）：这一层只跟水波那一段，"
                        f"常驻整段会挤掉字幕取样带里的字幕、并把闪屏取证窗口变成假绿灯")
    else:
        print(f"\n[证 B] 成片正文窗不够长（{body_end:.2f}s），没有「该走之后」的取样点，不判")

    # ── 证 C：钩子段不许压着这张卡 ───────────────────────────────────────────
    if hook_dur:
        seam = float(intro.get("junction_dissolve_seconds")
                     or intro.get("hook_fade_out_seconds") or 0.0)
        h_hi = hook_dur - seam - 0.15
        h_times = [round(0.4 + (h_hi - 0.4) * k / 3, 3) for k in range(4)]
        h_times = [t for t in h_times if 0.2 < t < h_hi]
        rgb_hit = None
        if h_times:
            crops = []
            diffs = []
            for t in h_times:
                frame = grab_at(final, t, os.path.join(work, "h.png"), fps_final)
                fc = frame[cs:ce, cx0:cx1]
                crops.append(to_luma(fc))
                diffs.append(core_diff(fc, card_c, exp_core))
            std = np.asarray(crops, dtype=np.float64).std(axis=0)
            outside = float(std[~exp_core].mean())
            inside = float(std[exp_core].mean())
            rgb_hit = min(diffs) <= cfg["max_box_rgb_diff"]
            print(f"\n[证 C] 钩子窗 {len(h_times)} 帧：封面那片 std={inside:.2f} / "
                  f"其余 std={outside:.2f} 比值={inside / max(outside, 1e-6):.2f}"
                  f"　RGB 差最小={min(diffs):.2f}")
            if outside < MOTION_FLOOR:
                print("  → 无从判定（钩子画面本身没有足够位移，静止性分不开）")
            elif inside <= STATIC_RATIO * outside and rgb_hit:
                reds.append(f"钩子段（0..{hook_dur:.2f}s）画面上压着本书封面：这层属于正文段，"
                            f"钩子段要 showCover:false 并重渲 hook.mp4")
            else:
                print("  → 干净" + ("" if not rgb_hit else "（RGB 偶然接近，但静止层证据不成立）"))

    # ── 证 D：成片里烘的是**哪一条**缩放轨迹（用户那句「放大过程不流畅，抖动得厉害」问的是它）──
    # lib 的 motion() 有四道 import 期断言，守的是**配置**（装置／缓动／终态／速度下限）；
    # `test-cover-motion-smoothness.py` 守的是 lib 自己画出来的帧。两者都答不了「这一版成片里
    # 烘的是哪条轨迹」。证 A 也不行：它四个采样点比的是卡框本体 RGB，而缩放前后叠的是同一张
    # 封面图——一条抖的轨迹、一条不抖的轨迹、甚至一张静止的卡，在那四点上的 RGB 都能差得很小
    #（内容一致，只是尺度差几个像素）。「lib 改了 ≠ 像素改了」这一类在本仓库付过多少次账，
    # 这里就必须量多少次。
    #
    # 仪器是**逐帧反解缩放系数 ŝ**（`fit_cover_scale()`）：卡芯是 alpha=1 的不透明区，成片在那儿
    # 的像素就是封面本身，缩放错一点，封面上的字与图纹就对不上。原来那条「量残差墨迹两条沿」的
    # 尺在这次的实测里被证明既判不成抖、也判不成不抖（它的噪声底子在静止卡上就有 1px 级，和被
    # 否决的整数取整装置同量级），所以沿留作报告、判决交给 ŝ。理由与数字全写在常量区。
    #
    # 只在 alpha=1 的平台期量（淡入段残差被 alpha 衰减），而缩放行程恰好贯穿整个平台期
    # （zoom_at 到 fade_out_at 才收口），所以这一段是动效最密的那一段。
    d_lo, d_hi = fade_in + EDGE_IN, exit_at - EDGE_OUT
    if d_hi <= d_lo + 0.15:
        print(f"\n[证 D] 平台期只有 {d_hi - d_lo:.2f}s，逐帧样本不足，动效轨迹**不判**"
              "（不是通过——重渲即恢复证人）")
    else:
        card_pil = B.overlay(source, cfg, (W, H)).convert("RGBA")
        core_w = float(bx1 - bx0)
        # 残差沿的取样窗必须比**包络**宽：包络是 lib 的 zoom 两端对 slot 取的并集，是这一层
        # 允许占到的最大横向矩形（`motion_envelope` 用 slot 而不是真卡，保守方向）；窗口若等于
        # 包络，卡体一旦比包络宽（＝正是要抓的那种缺陷），两条沿会被裁在窗口边上，量出来是
        # 「回退 0、外扩 0」。MEASURE_PAD 的来历写在常量区。
        zs = [float((cfg.get("motion") or {}).get("zoom_from", 1.0)),
              float((cfg.get("motion") or {}).get("zoom_to", 1.0))]
        env_x0 = min(CX + z * (sx0 - CX) for z in zs)
        env_x1 = max(CX + z * (sx1 - CX) for z in zs)
        env_y0, env_y1 = B.motion_envelope(cfg, (W, H))
        mx0 = max(0, int(env_x0) - MEASURE_PAD)
        mx1 = min(W, int(env_x1) + MEASURE_PAD)
        my0 = max(0, int(env_y0) - 4)
        my1 = min(H, int(env_y1) + 4)
        ds, es = int(round(d_lo * fps_final)), int(round(d_hi * fps_final))
        shat, pred, errs, spreads, ts = [], [], [], [], []
        lefts, rights, clipped = [], [], 0
        for n in range(ds, es + 1):
            t = n / float(fps_final)
            fr = grab_at(final, body_at + t, os.path.join(work, "d.png"), fps_final)
            s_fit, err, spread = fit_cover_scale(fr, card_pil, box)
            if s_fit is not None:
                shat.append(s_fit)
                pred.append(B.zoom_at(t, COVER_T, cfg))
                errs.append(err)
                spreads.append(spread)
                ts.append(t)
            if has_bg:
                bb = grab_at(bg, t, os.path.join(work, "db.png"), fps_bg)
                res = np.abs(fr[my0:my1, mx0:mx1].astype(np.float64)
                             - bb[my0:my1, mx0:mx1].astype(np.float64)).max(axis=2)
                e = card_edges_from_residual(res)
                if e is not None:
                    # 沿贴在窗边＝卡体摸出了取样窗，这两帧的沿数字是假的，不进序列
                    if e[0] <= 0.5 or e[1] >= (mx1 - mx0) - 0.5:
                        clipped += 1
                    else:
                        lefts.append(e[0] + mx0)
                        rights.append(e[1] + mx0)
        med_err = float(np.median(errs)) if errs else 0.0
        med_spread = float(np.median(spreads)) if spreads else 0.0
        dev_px = [abs(a - b) * core_w for a, b in zip(shat, pred)]
        # ŝ 顶在搜索网格端点 ＝ 最优值落在区间外，而区间 [0.60, 1.40] 把 lib 现在的 0.82→1.00、
        # 被否决的 1.00→1.12、以及静止卡的 1.00 直线全包住了，健康的成片永远不会顶（普查 20 期
        # 无一顶格）。会顶格的只有两种：卡芯**没有可对齐的纹理**（纯色／极简装帧——实测一张纯色
        # 装帧在完全健康的轨迹上读出 ŝ 恒 0.600、谷高 0.00、偏差 101px，因为整块芯在每个 s 都
        # 一模一样），和那块像素根本不是这张封面（三个跨书负对照里有两个顶格）。两种都是仪器
        # 失盲，不是缺陷：把「量不到」写成一个 101px 的红，送人去查一张没有纹理的封面，是这一
        # 章最贵的那类空转。尺度这一维在这种封面上交回证 A 的残差形状＋证 0b 的叠层字节。
        pinned = bool(shat) and (min(shat) <= FIT_S_LO + 1e-9 or max(shat) >= FIT_S_HI - 1e-9)
        # 卡芯足迹按 ŝ 的两端点反解（ŝ 随时间单调，所以端点**就是**足迹的上下沿）。这里不用
        # lib 的 motion_envelope：那条按配置算，而这一条按成片算——两者不符正是这一条的红。
        intr_mark = intr_sweep = 0.0
        rect_top = rect_bot = None
        if shat:
            for z, tag in ((min(shat), "lo"), (max(shat), "hi")):
                _x0r, _y0r, _x1r, _y1r = core_rect_at(z, box)
                if "book_mark_layer" in _bands:
                    intr_mark = max(intr_mark, float(_bands["book_mark_layer"]["y1"]) - _y0r)
                if "caption_sweep" in _bands:
                    intr_sweep = max(intr_sweep, _y1r - float(_bands["caption_sweep"]["y0"]))
                if tag == "lo":
                    rect_top = _y0r
                else:
                    rect_bot = _y1r
        print(f"\n[证 D] 平台期 {es - ds + 1} 帧，逐帧反解到 {len(shat)} 帧的缩放系数 ŝ"
              f"（谷高（最优拟合的平均 RGB 差）中位 {med_err:.2f}，不判线 {FIT_MAX_ERR}；"
              f"谷深（最优到最差差多少）中位 {med_spread:.2f}）")
        if len(shat) < FIT_MIN_FRAMES:
            # 判不了：卡芯那块像素反解不出任何缩放下的这张封面（压得太狠、封面没上屏、或者
            # 背景与成片不同代）。这里必须说「无从判定」而不是「平滑」——把量不到写成一个绿，
            # 是这一整章门禁反复付账的那类空转。
            print(f"  只反解出 {len(shat)} 帧：样本不足，动效轨迹**不判**（不是通过）")
        elif med_err > FIT_MAX_ERR:
            print(f"  卡芯按最优 ŝ 仍与成片差 {med_err:.1f} > {FIT_MAX_ERR}：那块像素不是这张封面"
                  f"（被别的层盖住／整体压暗／body-bg 与成片不是同一代／盘上 book-cover.jpg 换过而"
                  f"叠层字节没重记——后者证 0b 会单独判红），ŝ 是噪声不是轨迹，**不判**（不是通过）。"
                  f"这一条不是放行：轨迹这一维只能由本期的重渲恢复证人")
        elif pinned:
            print(f"  ŝ 顶在搜索网格端点（×{min(shat):.3f} / ×{max(shat):.3f}，区间 "
                  f"[{FIT_S_LO}, {FIT_S_HI}]）：卡芯里没有可对齐的纹理（纯色／极简装帧），或那块像素"
                  f"根本不是这张封面，真尺度落在区间外——动效轨迹**不判**（不是通过）。尺度这一维"
                  f"交回证 A 的残差形状与证 0b 的叠层字节，它们不看纹理")
        else:
            travel_meas = (shat[-1] - shat[0]) * core_w
            travel_pred = abs(pred[-1] - pred[0]) * core_w
            fix_cmd = (f' `bash scripts/render-hook-batch.sh "{args.episode}"`'
                       if args.episode else "（render-hook-batch 重渲本期）")
            print(f"  ŝ {shat[0]:.3f}→{shat[-1]:.3f}（卡芯宽 {shat[0] * core_w:.1f}px→"
                  f"{shat[-1] * core_w:.1f}px，行程 {travel_meas:.1f}px）　lib 预测 "
                  f"{pred[0]:.3f}→{pred[-1]:.3f}（{pred[0] * core_w:.1f}→{pred[-1] * core_w:.1f}px，"
                  f"预测行程 {travel_pred:.1f}px）")
            print(f"  |ŝ−预测| 折像素：均 {np.mean(dev_px):.2f}（判红线 {FIT_MEAN_TOL_PX}）/ "
                  f"峰 {max(dev_px):.2f}（判红线 {FIT_PEAK_TOL_PX}）　行程下限 "
                  f"{FIT_TRAVEL_MIN:.0%}×预测　卡芯足迹顶沿 y{rect_top:.1f} / 底沿 y{rect_bot:.1f}")
            if max(dev_px) > FIT_PEAK_TOL_PX or np.mean(dev_px) > FIT_MEAN_TOL_PX:
                # 失真形状说的话不一样，别把它们混成一句「轨迹不符」：恒定要说「没有缩放」，
                # 走了一段但走错端点要说「另一条 ramp」——人听到的修法不同（前者查渲染器有没有
                # 真的合成 cover-seq，后者只是没重渲）。顶在网格边界的走不到这里，上面已不判。
                if abs(shat[-1] - shat[0]) * core_w < 1.0:
                    shape = (f"成片这 {len(shat)} 帧的 ŝ 恒定在 ×{shat[0]:.3f}，也就是**这一层"
                             f"根本没有缩放**（`cover-seq` 那组帧没被合成，或被一张静态 PNG 顶掉）")
                else:
                    shape = (f"成片从 ×{shat[0]:.3f} 走到 ×{shat[-1]:.3f}，是**另一条 ramp**")
                reds.append(
                    f"本书封面的动效轨迹与 lib 不符：平台期 {len(shat)} 帧逐帧反解的缩放系数 ŝ "
                    f"相对 `lib/book_cover.zoom_at` 平均差 {np.mean(dev_px):.2f}px、最坏单帧差 "
                    f"{max(dev_px):.2f}px（判红线 均 {FIT_MEAN_TOL_PX} / 峰 {FIT_PEAK_TOL_PX}）。"
                    f"{shape}，而 lib 现在要的是 ×{pred[0]:.3f}→×{pred[-1]:.3f}"
                    + ("；证 0b 的叠层字节也不符，先查是不是换过 book-cover.jpg 没重渲"
                       if sha_ok is False else
                       "；证 0b 的叠层字节逐字节相同，所以这一版烘的是**上一版动效形状**——"
                       "`scripts/lib/book-cover.json` 的 motion 改过而像素没重渲。这就是「放大过程"
                       f"不流畅」在成片里的机器写法，唯一的修法是本期的那一条命令{fix_cmd}，"
                       "不是调这几条判线（2026-10-08 二十期普查的三族实测：健康的逐帧浮点仿射 "
                       "3 期 均 1.31..1.34 / 峰 2.87px，没有动效的静止卡 11 期 均 17.33..18.18 / "
                       "峰 39.94..41.89px，被否决的整数取整 1.00→1.12 共 4 期 均 39.2..40.7 / "
                       "峰 48.27..52.97px）"))
            if travel_pred >= 2.0 and travel_meas < FIT_TRAVEL_MIN * travel_pred:
                reds.append(
                    f"封面动效**没在成片里长开**：ŝ 首末帧之差折算的行程只有 {travel_meas:.1f}px，"
                    f"而 lib 的 zoom_at 在 {ts[0]:.2f}..{ts[-1]:.2f}s 上预测 {travel_pred:.1f}px"
                    f"（×{pred[0]:.3f}→×{pred[-1]:.3f}），低于预测的 {FIT_TRAVEL_MIN:.0%}。"
                    f"静态叠层字节对账（证 0b）对这条是瞎的——`overlay()` 那张图不随动效变；"
                    f"剩下的解释是这一层按**没有缩放**的轨迹合成（`cover-seq` 被静态 PNG 顶掉，"
                    f"或渲染器读不到 motion）：重渲本期" + fix_cmd)
            worst_intr = max(intr_mark, intr_sweep)
            if worst_intr > FIT_BAND_TOL_PX:
                if intr_mark >= intr_sweep:
                    where = (f"顶进《书名》＋作者层（其下沿 y"
                             f"{int(_bands['book_mark_layer']['y1'])}）")
                else:
                    where = (f"底压字幕取样带（其上沿 y"
                             f"{int(_bands['caption_sweep']['y0'])}）")
                reds.append(
                    f"成片里的卡芯**越出了 slot 允许的那条带**：按 ŝ 两端点反解的卡芯矩形{where}，"
                    f"越界 {worst_intr:.1f}px（判线 {FIT_BAND_TOL_PX}px；顶沿 y{rect_top:.1f}、"
                    f"底沿 y{rect_bot:.1f}）。`lib/book_cover._assert_clear_of_reserved_bands()` "
                    f"量的是**静态 slot**，所以 1.00→1.12 那一版把卡芯顶沿推到 y206、压进书名层 "
                    f"19px 时全库十四道门都是绿的——这条按成片反解的真实足迹补那个盲区。修法是"
                    f"把 motion.zoom_to 按回 1.0（lib 的 motion() 本来就该拒绝别的值）再重渲本期"
                    + fix_cmd)
            else:
                print("  → 轨迹与 lib 相符、行程长开、足迹留在 slot 允许的那条带里")
        if has_bg and len(lefts) >= FIT_MIN_FRAMES:
            print(f"  残差沿（只报告，不判决）：左 {min(lefts):.1f}..{max(lefts):.1f} / "
                  f"右 {min(rights):.1f}..{max(rights):.1f}　越 slot 左 {sx0 - min(lefts):.1f}px / "
                  f"右 {max(rights) - sx1:.1f}px　回退 {reversals(lefts, EDGE_REV_FLOOR_PX)}/"
                  f"{reversals(rights, EDGE_REV_FLOOR_PX)} 处（{EDGE_REV_FLOOR_PX}px 阈）"
                  + (f"　另有 {clipped} 帧的卡体贴到取样窗边" if clipped else ""))
            print("    这一列为何不判决：墨迹剖面那条沿的噪声底子在**静止卡**上就有 1px 级"
                  "（实测《围城》4/1、《呐喊》0/6、《乞力马扎罗的雪》4/2 处回退），和被否决的"
                  "整数取整装置同量级——用它判「抖」会把静止卡判成抖、把真抖判成干净。亚像素"
                  "流畅度由 `scripts/tests/test-cover-motion-smoothness.py` + lib 的 device 断言"
                  "持有，成片这一侧判的是上面那条 ŝ 轨迹；沿留着是因为投影裙边的越界是 ŝ 反解"
                  "不出来的东西。")
        elif has_bg:
            print(f"  残差沿：{len(lefts)} 帧量不到卡体，连报告都不出（它不持有判决）")
        else:
            print("  残差沿：无同期 body-bg.mp4，这一列无从算起（ŝ 轨迹不依赖它，上面已判）")

    if reds:
        print(f"\n❌ 本书封面门禁：{len(reds)} 项判红")
        for r in reds:
            print(f"   · {r}")
        sys.exit(1)
    print("\n✅ 本书封面门禁：全部通过")

if __name__ == "__main__":
    main()
