#!/usr/bin/env python3
"""交付门禁第 10 道：整片逐帧亮度闪屏核对。

判据：把成片整片解成 36x48 灰度，逐帧取均亮度**和相邻帧的像素平均差（MAD）**，看六件事——
  ① 闸刀（全片，只让开片头窗内部）：任何相邻两帧 |Δ| > DEFAULT_MAX_DELTA。正文每个场景切换
     都是 0.64s 交叉溶解（单帧只摊 2~3 级），所以这部片子里本来不存在「一个硬切」这种语言。
  ② 眨眼（被判片段）：任何一帧均亮度低于 OPENING_LUMA_FLOOR（下探全黑）。
  ③ 摊不开（被判片段）：每帧峰值跳变不许超过**本片自己正文峰值**的 RATE_MULTIPLE 倍
     （下限 RATE_FLOOR）。参照物是这部片子的正文，不是一个手写的绝对数。
  ④ 来回（被判片段）：把亮度切成「同向腿」，数方向相反、都够深（FLICKER_LEG_MIN）且都够短
     （FLICKER_LEG_MAX_S）的相邻腿——亮度掉下去又爬回来（或反过来），才是观众嘴里那个「闪」。
  ⑤ 换页（被判片段）：每帧**内容**跳变（MAD）不许超过本片正文 MAD 峰值的 CONTENT_MULTIPLE 倍
     （下限 CONTENT_FLOOR）。①~④ 量的都是「每帧一个均亮度」，
     对「整幅换了画、均值没变」那一拍天生全盲——见下面 2026-10-05 的补充。
  ⑥ 漏底（被判片段）：一次交叉溶解是凸组合 f·上层+(1−f)·下层，**越不出两侧电平夹出的带**；
     曲线在两个镜头电平之间出现越带游程，就说明黑底从两层之间漏了进来（形如
     f·上层+(1−f)²·下层）。这里没有绝对亮度项，所以「挑暗一点的照片去过门」行不通。

「被判片段」＝钩子段本体（去掉段尾淡出）+ 正文起幅 OPENING_TAIL 秒。共享片头窗
[hook_dur, hook_dur+trim_seconds) **只报告不判红**——2026-10-05 用户裁决，见下面那节。

为什么需要它（2026-10-04《嫌疑人X的献身》）：用户报「片头钩子出现闪屏」。整片 8405 帧
实测 |Δ|>40 的只有 8 帧，全部落在成片 8.27~10.43s 那段共享片头里，最大 93.4；
同一批成片的正文段 p99 只有 2.7。也就是说闪屏 100% 来自片头，而前九道门里
唯一看画面的三道（水波、字幕边界压测、钩子图上屏）量的都是**局部区域对不对**，
没有一道看**相邻帧之间跳了多少**——亮度从 98 一帧砸到 5.4 它们全绿。

★ 六个判据的**形状**是 2026-10-04~05 在共享片头和钩子段上量出来的，每条都对应一类真实缺陷，
  一条都不许退回去（形状与范围是两件事，见下一节）：
    · ②眨眼：那一版片头在「书单→结果页」那一拍**故意**下探全黑（`.template-blackout`，
      86→0→44 / 233ms），观众嘴里「片头闪屏」报的就是它。最初的结论「阈值要给这一拍留空间」
      被推翻了：砸黑就是砸黑，跟边缘平滑与否无关。
    · ③摊不开：末页交给结果页那一拍跨度 122 级、摊在 0.26s（8 帧）上单帧 27.7，是正文峰值 5.0
      的 5.5 倍；摊到 0.50s 就是 14.3。同形状、同跨度，差别只在摊开的时间——所以这条只能是
      相对量（本片正文当参照物），写死的绝对数既会误伤亮的健康片也守不住暗的缺陷片。
    · ④来回：三张全幅图层同时淡出、唯一一层淡入，合成出 189→159→170 的**连乘**凹陷
      （腿长 0.07~0.17s）；以及底图未归一时的整幅翻页 172.7/187.2/166.0/184.1/183.9/171.5
      （单帧 13~15 级、上下来回、连续五页）。两类都够不上①、也不眨眼，而旧判据⑤
      「开场里 |Δ|>18 的帧数 >3」对它们**实测 0 帧**——既对真缺陷全盲，又只在同一个位置留下
      第二个魔法数，所以退役，只保留在 --legacy-strobe 做 old-vs-new 对照。
    · ⑤换页：完全修好那一版《李尔王》①~④ 全绿（开场峰值 14.3、最暗 31.1、来回 0 处）而观众
      仍报闪，因为滚动书单每 0.175s 换掉**整幅**画面：开场 MAD 峰值 34.3 / p99 28.5，对照同一片
      正文 MAD 峰值 5.9 / p99 2.6。①~④ 只看均亮度，对这一拍天生全盲。
    · ⑥漏底：钩子两张照片之间用「进入层淡入 + 退出层淡出」的同速 CSS 溶解，合成是
      f·上层+(1−f)²·下层而不是凸组合。《人间草木》两幅均值 59.0/60.2 的照片，谷底实测 45.1
      （模型预测 44.5）；只淡入上一层、退出层保持 opacity:1 之后凹陷消失。这条也抓
      「摘掉退出层的淡出」引出的第二个面：没有 z-index 时它会变成一帧硬切（《丧钟为谁而鸣》
      实测 60.7 平 0.72s → 39.9），凹陷和硬切是同一个 bug 的两张脸。

★ 2026-10-05 用户裁决：「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题。」
  上面那四类片头侧缺陷当时全部修在**共享片头**（底图按 page_luma_band 归一、书单→结果页改成
  等长交叉溶解并把跑道拉到 0.50s、撤掉三层连乘淡出、六页改成 3×2 累积网格），装配层另外加了
  「钩子末帧原地保持 opening_runway_seconds + junction_dissolve_seconds 等长溶解」。用户否掉的
  是这套**改动本身**：片头按 2026-10-03 的观感原样交付——整幅换页、原始底图亮度
  （95/208/101/230/221/108/210）、CSS brightness(1.08)+screen 0.23、那一拍 .template-blackout
  砸黑、以及它自己的全黑起幅。三处（模板底图 / 模板时间轴 / 装配层）一起回滚，撤掉的文件落在
  tmp/retired-20261005/，禁止它们复活的静态断言在 scripts/check.mjs。
  于是这道门的**判决范围**跟着改：共享片头窗 [hook_dur, hook_dur+trim_seconds) 与钩子段尾那段
  淡出（[hook_dur−hook_fade, hook_dur)）从判决里摘出去，只印数不判红（judged_segments）；
  被判的是钩子段本体 + 正文起幅 OPENING_TAIL 秒。判据①只让开**两端都在片头窗内**的帧对，
  跨边界那两对（淡出末帧→片头第一帧、片头末帧→正文第一帧）照判。
  豁免的是**范围**，不是阈值：六个判据的形状、倍数、参照物一个字都没改。同一个砸黑形状摆在
  钩子段照样红——scripts/tests/test-flicker-rules.py 的 R1（红在②④⑥）与 R2（同一个序列搬进
  片头窗，只报告）就是把它摆在两个位置上跑的。豁免窗那一行还把「按同一套判据本会翻几项红」
  印出来，所以不存在「红但没人知道」的暗账。
  回滚之后**唯一保留**的抗闪屏装置在钩子这一路：段尾淡出到黑 hook_fade_out_seconds
  （scripts/lib/intro-template.json 是唯一真源，两条渲染路径都从它取），让片头的黑起幅接在
  一个已经在变暗的画面上；scripts/tests/test-hook-seam-fade.py 钉住它的长度、单调性、
  接缝对落在闸刀线以下，以及两条渲染路径确实画了 fade=t=out。

回滚之后的正反对照（都是**量出来的**，不是推算）：
  正例（豁免范围之外必须绿、豁免之内必须只报告）：
    《解忧杂货店》成片 8938 帧、整片 |Δ| p99 2.9 / max 13.6 → 判据①违规 0 帧；被判 228 帧
      ＝钩子 0.00~6.61s + 正文起幅 10.01~11.01s，均亮度 34.4~65.7、峰值 3.2（正文 4.5 →
      ③允许 17.8）、来回 0 处、MAD 峰值 3.6（正文 5.8 → ⑤允许 23.3）、越带 0 处 → 全绿。
    《秋园》成片 10023 帧、整片 |Δ| max **93.3**（全在片头窗内部）而判据①违规 **0 帧**：
      这就是「只让开片头内部的对、跨边界那两对照判」的实际效果——段尾淡出画对了，
      接缝那一对是 0→0；把它摘掉，同一处立刻是 42~90 级一帧红（R8 与 T3 各证一半）。
  反例（本期素材自己的缺陷，必须红，红点都不在片头里）：
    《秋园》被判段里 ④ 1 处 + ⑥ 2 处：t=4.23s 腿长 0.30s/0.33s 的 −15→+16 来回，以及
      两侧电平 71.2/70.3 却读到 53.1 的下探 17.2 级（连续 15 帧）——那是它自己钩子段的
      两层溶解凹陷，跟共享片头无关，重渲本期就消。
    《嫌疑人X的献身》被判段 ④ 1 处 + ⑥ 1 处（t≈4.7s）；豁免窗同时印出片头内部
      峰值 23.6、来回 3 处、MAD 35.3「本会翻 3 项红」——红账在纸上，不在判决里。
    《一生》被判段 ⑤ 换页 1 帧（开场 MAD 峰值 41.9 / 正文 5.2 → 允许 20.6）@t≈4.50s。
    合成用例（test-flicker-rules.py 的 R1/R3/R4/R6/R8）把砸黑、整幅换页、跨度摊不开、
      正文硬切、去掉段尾淡出五种形状**摆在钩子段**，各自只翻该翻的那条。
  ★ 判据④不许退化成「只要下探就红」：一次单调的大跨度（哪怕 122 级）不红，
    两条腿都 ≤0.35s 的来回才红——钩子段尾淡出正是靠这条时间尺度被读成「换气」。
  ★ 判据③⑤不许退化成绝对阈值：正文参照物是这部片子自己的正文，
    换一期正文切点更跳的片子，允许线自己抬高（R5 用同一份开场配两种正文证明判决会翻面）。

全库积压（**新判决范围**，2026-10-05 12:59 逐部量 episodes/*/renders/*-final.mp4，
共 125 部；脚本 tmp/census-flicker-scope-1005.sh，逐部完整输出在
tmp/flicker-census-scope-1005/，摘要一行一部（片名|成片 mtime|判决）在
tmp/flicker-census-scope-1005.txt——摘要带 mtime 是因为同期有别的会话在重渲，
判决必须说得出它量的是哪一代像素）：
绿 93、红 32。红的按判据分：⑥漏底 24 部（绝大多数是「下探 1x.x 级 / 1 处」，
即 9 月那批 create-episode-preview 还在淡出退场层留下的凸组合凹陷）、④来回 4 部、
⑤换页 3 部、②眨眼 2 部（夜晚的潜水艇 95 帧、面纱 30 帧——都是**本期素材自己暗**，
不是片头起幅）。
对照 2026-10-04 那次同目录普查（旧范围：整个开场窗都判，含共享片头）：绿 6、红 113。
换范围不是放宽标准：113 部里那 105 部「峰值 92~105 / 最暗 0.0」的红，红的全是
**共享片头自己那一拍砸黑和整幅翻页**，而用户 2026-10-05 明确要那一拍原样交付；
把它们继续算成本期缺陷，只会把每一次交付都推回「去改片头」——那正是被否掉的修法。
剩下的 32 部红是本期素材的账，重渲本期（钩子段 + 装配）就能消。

红点归属有三家，这道门只负责「看得见 + 说得清是谁的账」，改都在别处：
  ① **本期素材**（钩子段/正文起幅）：④⑤⑥ 的红绝大多数在这里。修在 episodes/<书名>/ 的
     图上（换更同亮度的照片、缩小换页面积）或 create-episode-preview.mjs 的溶解形状
     （只淡入上一层 + .scene 显式 z-index），重渲本期才消。
  ② **装配层**：片头窗与钩子段尾淡出**之外**的接缝。判据①跨边界那两对归它——段尾淡出
     长度只许从 scripts/lib/intro-template.json 的 hook_fade_out_seconds 取（回滚撤掉的
     junction_dissolve_seconds / opening_runway_seconds / intro_runway.py 不许复活，
     已冻结在 tmp/retired-20261005/，由 scripts/check.mjs 静态守着）。
  ③ **共享片头模板**：现在的处置是**不判、只报告**。它内部的砸黑起幅与那一拍下探全黑
     是 2026-10-03 的设计，用户裁决原样交付；豁免窗那一行把同一套判据的「本会翻几项红」
     印出来，就是为了让「这里为什么不算红」可查，而不是留暗账。
  注意成片里烧的是**渲染那天**的模板与装配层像素：改了共享层不回改已经交付的文件，
  所以 --opening-only 的积压表读的一定是成片，不是模板。

反空转设计：解码帧数与 ffprobe 时长×帧率对账，少于 90% 直接判红——「没解出来」和
「没看到缺陷」不是一回事，一条看不见画面的门比没有门更危险。判据的**形状**与**范围**
另有四套合成回归，全部接在 npm run check 里（只靠真片跑绿会掩盖判据失效）：
  · scripts/tests/test-flicker-rules.py R0~R8——同一份序列摆在钩子段（红）与摆进片头窗
    （只报告）两个位置上跑，证明豁免的是范围不是阈值；再证明①~⑥各自「抓到该抓的、
    放过该放的」，包括 R5（同一开场配两种正文参照，判决翻面）与 R8（摘掉段尾淡出→①红）。
  · scripts/tests/test-hook-seam-fade.py T1~T4——回滚之后唯一保留的抗闪屏装置。
  · scripts/tests/test-dissolve-dip.py / test-dissolve-undershoot.py——钩子两层溶解的
    凸组合形状与 ⑥ 的越带取证，用例都摆在**钩子**该在的位置上。
  · scripts/check-flicker-attribution.py 的红点归因表（check-episode-delivery.py 在①~⑥
    任一条红时自动跑），把同一套判据的分段结果摊成「本期素材 / 装配层 / 共享片头模板」
    三家，它只诊断、不改判决。

用法：
  python3 scripts/check-luminance-flicker.py episodes/<书名>/renders/<书名>-final.mp4
  python3 scripts/check-luminance-flicker.py <成片> --max-delta 40   # 调阈值做对照实验
  python3 scripts/check-luminance-flicker.py <成片> --opening-only   # 只核对开场窗口，供批量积压表
  python3 scripts/check-luminance-flicker.py <成片> --legacy-strobe  # 复算旧判据⑤，做 old-vs-new
退出码：0 无违规帧；1 有违规帧或解码不完整。最后一行是结论，交付汇总读它。
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

PROBE_W, PROBE_H = 36, 48      # 逐帧亮度用的极小采样画幅：280s 全片只有 14MB
DEFAULT_MAX_DELTA = 40.0       # 判据①闸刀线：一次单帧亮度阶跃。正文 p99=2.7，缺陷 42~93
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 判据②眨眼。黑场在这套度量里读 0~5；被判片段（钩子段本体 + 正文起幅）里健康的本期氛围图
# 最暗实测 25.5~34.4（《解忧杂货店》34.4），红的是**本期素材自己暗**（夜晚的潜水艇 11.1、
# 面纱 3.6）。共享片头内部那一下下探全黑按 2026-10-05 裁决只报告、不判红。
OPENING_LUMA_FLOOR = 18.0
# 判据③摊不开：参照物是**本片正文**的每帧峰值，不是手写绝对数。
# 回滚前在片头上量的那一对（《李尔王》正文 5.0 → 允许 20.0；同一跨度摊在 0.26s 上 26.9 红、
# 摊在 0.50s 上 14.3 绿）说明的是「跨度必须用时间摊」这个形状本身。现在被判的是钩子段：
# 《解忧杂货店》钩子峰值 3.2 / 正文 4.5 → 允许 max(16, 4×4.5)=17.8。RATE_FLOOR 兜住正文异常平的片子。
RATE_MULTIPLE = 4.0
RATE_FLOOR = 16.0
BODY_REF_SECONDS = 12.0        # --opening-only 不解全片，正文参照只解开场后面这 12 秒
# 判据④来回：把亮度序列切成「同向腿」，两条**方向相反**的相邻腿都够深（FICKER_LEG_MIN）
# 且都够短（FICKER_LEG_MAX_S）就是一次「去了又回」。健康钩子段实测 0 处，红的是本期自己的
# 两层溶解凹陷（《秋园》t=4.23s 的 −15→+16，腿长 0.30s/0.33s）。
# 时间尺度是这条判据的实质：这套语言里的合法大跨度——正文每个切点 0.64s、钩子段尾淡出
# hook_fade_out_seconds 0.4s——腿长都在 0.35s 以上，一次都不算；
# 0.1~0.2s 内去了又回的才是观众嘴里的「闪」。这也是 T2 那三条对照的唯一依据。
FLICKER_LEG_MIN = 8.0
FLICKER_LEG_MAX_S = 0.35
FLICKER_LEG_EPS = 0.4          # 小于这个量的单帧变化是编码噪声，不切断腿
FLICKER_MAX = 0                # 健康成片实测 0 处，所以一处就是红
FADE_IN_GRACE = 0.75           # 两段结构（无钩子）时片头从全黑起幅，这是设计不是闪
OPENING_TAIL = 1.0             # 窗口要多带正文起幅 1 秒：片头→正文那个接缝也算开场
# 旧判据⑤的形状：开场里 |Δ|>18 的**帧数**。2026-10-04 退役（见 docstring：它既抓不住
# 翻页来回/图层连乘，又要在亮带健康成片上判红），只保留给 --legacy-strobe 做 old-vs-new A/B。
STROBE_DELTA = 18.0
STROBE_MAX_FRAMES = 3
# 判据⑤换页（2026-10-05 新增，量的不是亮度而是**内容**）：相邻两帧的像素平均差（MAD）。
# 前四条全都建立在「每帧一个均亮度」上，于是它们对**整幅换了画、均值没变**那一拍天然全盲：
# 《李尔王》那次是①~④ 全绿（峰值 14.3 / 最暗 31.1 / 来回 0 处）而观众仍报闪，因为整幅翻页时
# 均亮度只跳十几级，屏幕上却是 82.9% 的像素在一帧里换了内容（开场 MAD 峰值 34.3 / p99 28.5）。
# 参照物仍然取**本片正文**（正文每个切点 0.64s 交叉溶解，实测帧间 MAD p99 2.6 / max 5.9），
# 不写绝对天花板。
# 为什么必须缩「换的面积」而不能只放慢：两幅整幅画的 MAD 差本身就有 ~150，摊到 6 帧还剩
# 25，而滚动的整段窗口只有 1.30s（scroll_start→result_page_at，被 trim/水波/BRIDGE_ZOOM 钉死），
# 六页每页最多摊 6 帧——**整幅换页在这个窗口里永远到不了正文量级**。共享片头按 2026-10-05
# 裁决保持整幅换页、只报告不判红；这条判据因此是**钩子段**的约束：一个钩子 beat 换成另一张
# 全幅照片时，要么让翻页摊到正文量级，要么缩小换的面积（分屏/局部），没有第三种。
CONTENT_MULTIPLE = 4.0
CONTENT_FLOOR = 16.0
# 判据⑥漏底（2026-10-05 新增）：一次交叉溶解在数学上是**凸组合**
#   v(f) = f·上层 + (1−f)·下层 , f∈[0,1]
# 凸组合不可能低于两端里较暗的那端，也不可能高于较亮的那端。所以只要开场亮度曲线在两个
# 镜头电平之间出现一段「越带」的游程（下探到较暗电平以下，或上冲到较亮电平以上），合成
# 就不是凸组合——黑底从两层之间漏了进来（形如 f·上层 + (1−f)²·下层），观众看到的就是
# 「闪一下」。这条判据里没有绝对亮度项：两侧电平一起变暗时谷底跟着变暗，判决不翻面，
# 所以「挑暗一点的照片去过门」在这里行不通（这正是判据④洗不掉的那类修法）。
#   · 镜头用电平表达：够长（LEVEL_MIN_FRAMES）且**整体够平**（整段极差 ≤ LEVEL_RANGE_TOL）
#     的一段才算一个镜头。一次 0.72s 溶解底部的二次型平底（实测合成对照 0.30s）不是镜头，
#     把它登记成电平等于把一次「回头」拆成两条单调腿，判据当场失明。
#   · 极差而不是逐帧容差：一次 8 帧、每帧 0.9 级的回升按逐帧容差是「平」的，合起来跨了
#     7.5 级，而那正是溶解谷底（实测《李尔王》交付钩子）。
#   · 取证窗口只量**字幕以上**那块画面（LUMA_BAND_ROWS）：字幕墨迹是独立相加的一层 gain，
#     不是两层照片的凸组合，它中途入场可以合法地越出两张底图夹出的带。实测整幅口径会把
#     《丧钟为谁而鸣》字幕入场那一帧报成「上冲 3.2 级」、《一个叫欧维的男人决定去死》报成
#     「下探 3.9 级」，两处都是假阳；换成字幕以上窗口后两部转绿，而《李尔王》的真缺陷
#     仍然是 🔴 5.6 级——判据的取证口径要改，不是把 UNDERSHOOT_TOL 从 3 抬到 5 把两边一起抹掉。
LEVEL_RANGE_TOL = 3.0          # 一个电平整段允许的极差（级）
LEVEL_MIN_FRAMES = 15          # ≥0.50s @30fps 才算一个镜头
UNDERSHOOT_TOL = 3.0           # 编码噪声 + 慢平移的量级，低于它不算越界（**地板**）
UNDERSHOOT_REL = 0.08          # 越带深度的相对地板：占两电平带宽的比例
EXCURSION_MIN_FRAMES = 2       # 单帧毛刺不算缺陷
EXCURSION_MAX = 0              # 凸组合一次都不越带，所以一处就是红（同判据④的形状）
LUMA_BAND_ROWS = (0, 35)       # 36x48 探针里字幕以上的行（48 行 = 960px，字幕占到行 35~45）


def probe(path, key):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", f"stream={key}", "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    return r.stdout.strip()


def frame_curves(path, limit=None, band_rows=LUMA_BAND_ROWS):
    """**一次解码**拿到三个逐帧量：整幅均亮度、相邻帧像素平均差 MAD、取证窗口均亮度。

    三个量必须出自同一次解码、同一个 36x48 口径：判据⑤要和①~④读同一批帧，判据⑥要和它们
    读同一批帧，否则同一处画面会拿到两份互相矛盾的证词（而「哪份算」又是一次手写阈值的争论）。
    MAD 的下标和 np.diff 一致：mad[i] 是第 i 帧与第 i+1 帧之间的差。
    灰度栈先转 int16 再相减——uint8 直接 diff 会回绕（255−0 变 255、0−255 变 1）。
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if limit:
        cmd += ["-t", f"{limit:.3f}"]      # 放在 -i 之前 = 只解这一段，批量积压表靠它压到秒级
    cmd += ["-y", "-i", path,
            "-vf", f"scale={PROBE_W}:{PROBE_H},format=gray", "-f", "rawvideo", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"解码失败：{r.stderr.decode()[-300:]}")
    n = len(r.stdout) // (PROBE_W * PROBE_H)
    if n == 0:
        z = np.zeros(0, dtype=float)
        return z, z.copy(), z.copy()
    stack = np.frombuffer(r.stdout[: n * PROBE_W * PROBE_H],
                          dtype=np.uint8).reshape(n, PROBE_H, PROBE_W).astype(np.int16)
    means = stack.mean(axis=(1, 2))
    mad = np.abs(np.diff(stack, axis=0)).mean(axis=(1, 2)) if n > 1 else np.zeros(0, dtype=float)
    band = stack[:, band_rows[0]:band_rows[1], :].mean(axis=(1, 2))
    return means, mad, band


def frame_probe(path, limit=None):
    """整片（或前 limit 秒）的 (每帧均亮度, 相邻帧像素平均差 MAD)。

    frame_curves() 的 2 元投影，只留给 frame_luma() 和**不量判据⑥**的回归测试桩。归因脚本
    check-flicker-attribution.py 2026-10-05 起改用 frame_curves()：⑥ 必须在第三条曲线（取证窗口
    =字幕以上）上量，用这个 2 元投影就会把字幕入场那一帧读成越带（两处实测假阳）。
    """
    means, mad, _ = frame_curves(path, limit)
    return means, mad


def frame_luma(path, limit=None):
    """整片（或前 limit 秒）逐帧均亮度（36x48 灰度）。"""
    return frame_probe(path, limit)[0]


def media_duration(path):
    txt = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(txt)
    except ValueError:
        return 0.0


def episode_dir_of(path):
    """从成片路径回推本期目录（…/episodes/<书名>/renders/…）；推不出返回 None。

    判据②③④ 要知道开场有多长，而长度只有本期自己知道（钩子配音几秒是每期不同的）。
    批量积压表跑的是别的目录里的文件，推不出就退到保守口径（见 opening_window）。
    """
    parts = os.path.abspath(path).split(os.sep)
    if "episodes" not in parts:
        return None
    i = len(parts) - 1 - parts[::-1].index("episodes")
    if i + 2 >= len(parts) or parts[i + 2] != "renders":
        return None
    cand = os.path.join(ROOT, "episodes", parts[i + 1])
    return cand if os.path.isdir(cand) else None


def opening_window(path, trim_seconds, hook_fade=0.0):
    """开场窗口 = 钩子段 + 固定开场白（裁到 trim_seconds）+ 正文起幅 1.0s。

    三段结构里钩子那张照片就是成片的第一帧，所以片头自带的 0.13s 全黑起幅**必须**被装配层
    的钩子段尾淡出接住（接不住就是真源②复发，判据①的闸刀线抓它）；两段结构（没有
    script-hook.csv）时片头确实是视频的第一个镜头，从全黑淡入是对的，于是前 FADE_IN_GRACE
    秒不判下限。认不出本期结构（批量跑别的目录）时保守处理：窗口取 13s、下限从前
    FADE_IN_GRACE 秒起判。
    `hook_fade` 取自 scripts/lib/intro-template.json 的 hook_fade_out_seconds（**唯一真源**，
    由调用方读进来，这里不重复开文件）：钩子段被判的部分因此到 hook_dur−hook_fade 为止，
    见 judged_segments。默认 0 表示没有淡出段——调用方（归因脚本）自己有段表，不受影响。
    """
    ep = episode_dir_of(path)
    hook_dur = 0.0
    if ep and os.path.exists(os.path.join(ep, "script-hook.csv")):
        hook_dur = round(media_duration(
            os.path.join(ep, "audio", "hook-voiceover-story.mp3")), 2)
    end = (hook_dur + trim_seconds + OPENING_TAIL) if ep else 13.0
    grace = 0.0 if (ep and hook_dur > 0) else FADE_IN_GRACE
    has_hook = bool(ep) and hook_dur > 0
    return dict(end=round(end, 2), floor_start=round(grace, 2), hook_dur=hook_dur,
                resolved=bool(ep), episode=ep or "-",
                # 共享片头窗与钩子段尾淡出：只在 resolved 时给，认不出结构就不豁免（见 judged_segments）
                intro=(round(hook_dur, 2), round(hook_dur + trim_seconds, 2)) if ep else None,
                hook_sink=(round(max(grace, hook_dur - max(0.0, hook_fade)), 2)
                           if has_hook else None))


def judged_segments(win, fps, n_frames):
    """开场窗口里被**判**的区间，与只报告不判红的豁免窗（都是帧号半开区间）。

    2026-10-05 用户裁决「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」：共享片头按
    2026-10-03 原样交付，它内部的 0.13s 全黑起幅与「书单→结果页」那一拍的下探全黑都是**设计**。
    所以片头窗 [hook_dur, hook_dur+trim_seconds) 从判决里排除、只把数印出来；被判的是钩子段
    [floor_start, hook_dur − 段尾淡出) 与正文起幅 [hook_dur+trim_seconds, win.end)。
    钩子段尾那 `hook_sink` 秒（装配层 `hook_fade_out_seconds` 的淡出到黑）跟着一起豁免：它
    就是那段全黑起幅的钩子一侧——两边合起来才是「换气」，只把片头摘出去、把淡出的末帧留在
    判决里，等于要求修法自己制造一次判据②的眨眼（《雪国》12:27 实测：最暗 0.0@t=7.53s、
    越带下探 50.6 级@t=7.50s 全部落在淡出与片头之内）。归因表把同一处摆在同名段里
    （check-flicker-attribution.py 的 hook_fade 段），两份口径必须一致。
    豁免的是**范围**，不是阈值：判据②~⑥的形状、允许线、参照物（本片自己的正文）一个字不改，
    同一种形状摆在钩子段里照样红——scripts/tests/test-dissolve-undershoot.py 的六个用例就是
    把它摆在钩子该在的位置上跑的，而判据①（全片闸刀线）仍然覆盖接缝：段尾没有淡出、亮照片
    一帧砸进黑起幅，照样是 |Δ|>40。
    `win["intro"]` 缺失（认不出本期结构：批量跑别的目录、合成对照片段）时**不豁免**，整窗照判
    ——宁可多判不可漏判，因为没有 hook_dur 就划不出片头窗到底在哪儿。
    """
    i0 = min(n_frames - 1, max(0, int(round(win["floor_start"] * fps))))
    i1 = min(n_frames, max(2, int(round(win["end"] * fps)) + 1))
    intro = win.get("intro")
    if not intro:
        return [(i0, i1)], None
    sink = win.get("hook_sink")
    a_end = max(i0, min(i1, int(round((intro[0] if sink is None else sink) * fps))))
    ib = max(a_end, min(i1, int(round(intro[1] * fps)) + 1))
    segs = [(s, e) for (s, e) in ((i0, a_end), (ib, i1)) if e > s]
    return (segs or [(i0, i1)]), ((a_end, ib) if ib > a_end else None)


def flickers(seg, fps, leg_min=FLICKER_LEG_MIN, leg_max_s=FLICKER_LEG_MAX_S,
             eps=FLICKER_LEG_EPS):
    """判据④「去了又回」：把亮度序列切成同向腿，再数方向相反、都够深且都够短的相邻腿。

    切腿而不是逐帧比差分：砸到全黑再爬回来的那一拍中间有一段亮度为 0 的平台，
    逐帧差分在那里是 −x,0,0,+y，「符号翻转 + 腿长」看不见它；腿是能被观众指认的单位。
    跨度**带符号**，够短是实质：正文那两个合法大跨度（钩子→片头 0.70s、书单→结果页 0.50s）
    腿长都在 0.35s 以上，一次都不算；0.1~0.2s 内掉下去又爬回来才是观众说的「闪」。
    这条判据同时看「跳了多大」和「用了多久」，只看其中一个的判据已经在 2026-10-04 被
    证明两头都错：只看跨度（闸刀线 40）漏掉 0.26s 跑道和图层连乘，只看帧数
    （旧判据⑤ |Δ|>18 计帧）既漏掉同样两处、又要误伤健康的亮带成片。
    返回 [(帧号, 跨度1, 跨度2, 时长1, 时长2)]。
    """
    d = np.diff(np.asarray(seg, dtype=float))
    runs = []
    i, n = 0, len(d)
    while i < n:
        if abs(d[i]) < eps:
            i += 1
            continue
        sign = 1 if d[i] > 0 else -1
        j, acc = i, 0.0
        while j < n and (1 if d[j] > 0 else -1) == sign and abs(d[j]) >= 0.05:
            acc += d[j]
            j += 1
            if abs(d[j - 1]) < eps:
                break
        runs.append((i, acc, (j - i) / float(fps)))
        i = j
    events = []
    for k in range(1, len(runs)):
        _, a0, t0 = runs[k - 1]
        start, a1, t1 = runs[k]
        if a0 * a1 >= 0:
            continue
        if abs(a0) >= leg_min and abs(a1) >= leg_min and max(t0, t1) <= leg_max_s:
            events.append((start, a0, a1, t0, t1))
    return events


def rate_allowance(body_peak, multiple=RATE_MULTIPLE, floor=RATE_FLOOR):
    """判据③的允许线：开场每帧峰值可以比正文快 multiple 倍，但不低于 floor。"""
    return max(floor, multiple * float(body_peak))


def content_allowance(body_mad_peak, multiple=CONTENT_MULTIPLE, floor=CONTENT_FLOOR):
    """判据⑤的允许线：开场每帧**内容**跳变不许超过本片正文 MAD 峰值的 multiple 倍（下限 floor）。

    形状和判据③一致（参照物是这部片子自己的正文，floor 只兜正文异常平的片子），量的却是
    另一个东西：③ 看「均值挪了多少」，⑤ 看「多少像素换了内容」。《李尔王》正是 ③ 绿（14.3
    ≤ 20.2）而 ⑤ 红（34.3 > 23.6）——这两条码必须能各自翻面，否则 ⑤ 只是 ③ 的重复。
    """
    return max(floor, multiple * float(body_mad_peak))


def held_levels(curve, range_tol=LEVEL_RANGE_TOL, min_frames=LEVEL_MIN_FRAMES):
    """判据⑥的第一步：把亮度曲线切成「镜头」——够长**且整体够平**的一段才算一个电平。

    判据必须是「整段的极差 ≤ range_tol」，不能只看相邻帧之差：一次 8 帧、每帧 0.9 级的回升
    按逐帧容差是「平」的，合起来却跨了 7.5 级——那正是溶解谷底，把它登记成一个电平，就等于
    把一次「回头」拆成两条单调腿，判据⑥当场失效（实测 2026-10-05《李尔王》hook.mp4：谷底
    34.1 到平台 39.8 的回升被逐帧容差吞掉，整段成为「电平 40」，下探看不见）。
    返回 [(起帧, 止帧, 电平中位亮度)]，闭区间。
    """
    curve = np.asarray(curve, dtype=float)
    out = []
    n = len(curve)
    i = 0
    while i < n:
        j = i
        hi = lo = curve[i]
        while j + 1 < n:
            v = curve[j + 1]
            if max(hi, v) - min(lo, v) > range_tol:
                break
            hi, lo = max(hi, v), min(lo, v)
            j += 1
        if j - i + 1 >= min_frames:
            out.append((i, j, float(np.median(curve[i:j + 1]))))
            i = j + 1
        else:
            i += 1
    return out


def _runs(flags):
    """布尔序列里的游程：返回 [(起点, 终点, 是否贴窗口边缘)]，闭区间。"""
    out = []
    i, n = 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        out.append((i, j, i == 0 or j == n - 1))
        i = j + 1
    return out


def dissolve_excursions(curve, fps, tol=UNDERSHOOT_TOL,
                        min_frames=EXCURSION_MIN_FRAMES,
                        range_tol=LEVEL_RANGE_TOL, level_frames=LEVEL_MIN_FRAMES,
                        rel=UNDERSHOOT_REL):
    """判据⑥「漏底」：相邻两个镜头电平之间那些帧，有没有越出两电平夹出的亮度带。

    凸组合越不出这条带，所以一次「离开带 → 回到带」只能是黑底（或白底）从两层之间漏进来。
    电平本身把游程的两端钉住，所以「出去又回来」是结构自带的，不需要再额外要求闭合。

    越带的深度地板取 `max(tol, rel × 带宽)`，**不是**一个手写的绝对数：把双层淡出写成
    v = f·B + (1−f)²·A，它越出带宽的最深点是 depth = B²/(4A)，而带宽是 A−B，两个量在同比例
    压暗下都乘 k，所以 depth/带宽 是**不变量**（B=0.5A 时恒等于 12.5%）。绝对容差留得住编码
    噪声，比例项堵住「把两张图一起调暗到谷底只剩 2 级」这条唯一还能走通的洗绿路。
    返回 [{"t","kind","depth","band","floor","level","frames","span_s"}]。
    """
    curve = np.asarray(curve, dtype=float)
    levels = held_levels(curve, range_tol, level_frames)
    out = []
    for (_, b, va), (c, _, vc) in zip(levels, levels[1:]):
        if c <= b + 1:
            continue
        between = curve[b + 1:c]
        lo, hi = min(va, vc), max(va, vc)
        floor = max(tol, rel * (hi - lo))
        in_band = (between >= lo - floor) & (between <= hi + floor)
        for s, e, _touch in _runs(~in_band):
            if e - s + 1 < min_frames:
                continue                      # 单帧毛刺不算缺陷
            seg = between[s:e + 1]
            below, above = lo - float(seg.min()), float(seg.max()) - hi
            if below >= above:
                kind, depth, pick = "下探", below, int(np.argmin(seg))
            else:
                kind, depth, pick = "上冲", above, int(np.argmax(seg))
            if depth <= floor:
                continue
            out.append({"t": (b + 1 + s + pick) / fps, "kind": kind, "depth": depth,
                        "band": (va, vc), "floor": floor, "level": float(seg[pick]),
                        "frames": e - s + 1, "span_s": (c - b - 1) / fps})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("final")
    ap.add_argument("--max-delta", type=float, default=DEFAULT_MAX_DELTA)
    ap.add_argument("--luma-floor", type=float, default=OPENING_LUMA_FLOOR)
    ap.add_argument("--rate-multiple", type=float, default=RATE_MULTIPLE)
    ap.add_argument("--rate-floor", type=float, default=RATE_FLOOR)
    ap.add_argument("--flicker-leg-min", type=float, default=FLICKER_LEG_MIN)
    ap.add_argument("--flicker-leg-max-s", type=float, default=FLICKER_LEG_MAX_S)
    ap.add_argument("--flicker-max", type=int, default=FLICKER_MAX)
    ap.add_argument("--content-multiple", type=float, default=CONTENT_MULTIPLE)
    ap.add_argument("--content-floor", type=float, default=CONTENT_FLOOR)
    ap.add_argument("--undershoot-tol", type=float, default=UNDERSHOOT_TOL,
                    help="判据⑥的越带深度绝对地板（噪声量级，不是亮度天花板）")
    ap.add_argument("--undershoot-rel", type=float, default=UNDERSHOOT_REL,
                    help="判据⑥的越带深度相对地板（占两电平带宽的比例）；depth/带宽 在同比压暗下不变")
    ap.add_argument("--excursion-min-frames", type=int, default=EXCURSION_MIN_FRAMES,
                    help="判据⑥：连续几帧越带才算缺陷（单帧毛刺不算）")
    ap.add_argument("--level-range-tol", type=float, default=LEVEL_RANGE_TOL,
                    help="判据⑥：一个镜头电平整段允许的极差")
    ap.add_argument("--level-min-frames", type=int, default=LEVEL_MIN_FRAMES,
                    help="判据⑥：够长才算一个镜头（溶解谷底那段平底不许登记成电平）")
    ap.add_argument("--band-rows", default=f"{LUMA_BAND_ROWS[0]}:{LUMA_BAND_ROWS[1]}",
                    help="判据⑥的取证窗口（36x48 探针的行，半开区间）；整幅口径写 0:48 做 A/B")
    ap.add_argument("--opening-only", action="store_true",
                    help="只核对开场窗口（批量积压表用，正文参照另解 12s）")
    ap.add_argument("--legacy-strobe", action="store_true",
                    help="复算退役的旧判据⑤（|Δ|>18 的帧数），做 old-vs-new 对照")
    args = ap.parse_args()

    if not os.path.exists(args.final):
        sys.exit(f"找不到成片：{args.final}")
    fps_txt = probe(args.final, "r_frame_rate")
    try:
        num, den = fps_txt.split("/")
        fps = float(num) / float(den)
    except Exception:
        fps = 30.0
    duration = media_duration(args.final)

    with open(os.path.join(ROOT, "scripts", "lib", "intro-template.json"),
              encoding="utf-8") as f:
        intro_lib = json.load(f)
    trim_seconds = float(intro_lib["trim_seconds"])
    # 钩子段尾淡出的长度只有这一份真源（两条渲染路径读的也是它），门禁据它划豁免窗
    hook_fade = float(intro_lib.get("hook_fade_out_seconds", 0.0))
    win = opening_window(args.final, trim_seconds, hook_fade)
    # --opening-only 也要有正文参照物（判据③是相对量），所以多解开场后 12 秒而不是全片。
    decode_limit = (win["end"] + BODY_REF_SECONDS) if args.opening_only else None

    try:
        r_lo, r_hi = (int(x) for x in str(args.band_rows).split(":"))
        if not (0 <= r_lo < r_hi <= PROBE_H):
            raise ValueError
    except ValueError:
        sys.exit(f"--band-rows 必须是 0<=a<b<={PROBE_H} 的 a:b（当前 {args.band_rows}）")

    luma, mad, band = frame_curves(args.final, decode_limit, (r_lo, r_hi))
    if len(luma) == 0:
        print("❌ 亮度闪屏门禁：一帧都没解出来，这道门没看到任何画面")
        sys.exit(1)
    decoded_seconds = len(luma) / fps
    # 对账拿**要求解的那段**当分母，不是「实际解出来的」——否则一条截断的解码永远自证清白。
    requested = min(duration, win["end"] + (BODY_REF_SECONDS if args.opening_only else 0.0))
    expected = requested * fps
    if expected and len(luma) < 0.9 * expected:
        print(f"❌ 亮度闪屏门禁：只解出 {len(luma)} 帧，按 {requested:.2f}s×"
              f"{fps:g}fps 应有 {expected:.0f} 帧 —— 判据覆盖不到该片段，不算通过")
        sys.exit(1)
    if decoded_seconds < requested - 0.05:
        print(f"⚠️ 解码 {decoded_seconds:.2f}s 短于核对窗口 {requested:.2f}s（文件本身就这么长），"
              f"正文参照退到判据③的绝对下限 {RATE_FLOOR:g}")

    i1 = min(len(luma), max(2, int(round(win["end"] * fps)) + 1))
    body = luma[i1:]
    delta = np.abs(np.diff(luma))
    # 判据① 是全片闸刀线，它只让开**两端都落在共享片头窗内**的那些帧对：那几对就是固定开场白
    # 自己的换页与那一拍砸黑（2026-10-05 用户裁决按 10-03 原样交付，实测单帧 42~107 级）。
    # 跨界的两对（钩子末帧→片头首帧、片头末帧→正文首帧）**照判**——接缝上不许有硬切，那是本期
    # 装配层与钩子段的责任，也正是「只改钩子侧闪屏」这句话里唯一还留在判决里的片头相邻量。
    intro_pair = np.zeros(max(0, len(luma) - 1), bool)
    if win.get("intro"):
        _ia = int(round(win["intro"][0] * fps))
        _ib = int(round(win["intro"][1] * fps))
        intro_pair[max(0, _ia):max(_ia, _ib - 1)] = True
    bad = (np.zeros(0, int) if args.opening_only
           else np.where((delta > args.max_delta) & ~intro_pair)[0])

    print(f"{'开场' if args.opening_only else '整片'}亮度闪屏核对  "
          f"{os.path.basename(args.final)}")
    if not args.opening_only:
        print(f"  {len(luma)} 帧 / {duration:.2f}s / {fps:g}fps   "
              f"均亮度 {luma.min():.1f}~{luma.max():.1f}   "
              f"单帧 |Δ| p50={np.percentile(delta, 50):.1f} p99={np.percentile(delta, 99):.1f} "
              f"max={delta.max():.1f}")
        print(f"  阈值 |Δ|>{args.max_delta:g}   违规 {len(bad)} 帧")
        for i in bad[:20]:
            t = (i + 1) / fps
            print(f"    ❌ t={t:7.2f}s  均亮度 {luma[i]:6.1f} → {luma[i + 1]:6.1f}   "
                  f"跳变 {luma[i + 1] - luma[i]:+.1f}")
        if len(bad) > 20:
            print(f"    …… 另有 {len(bad) - 20} 帧")

    # ------------------------------------------------ 开场窗口五条判据（②③④⑤⑥）
    # ②~⑥ 只在 judged_segments() 给出的**被判片段**上量（钩子段本体 + 正文起幅）；
    # 共享片头窗与钩子段尾淡出只把数印出来，不参与判决（lib 的 _flicker_scope_comment）。
    # 片段的**选择**是范围问题，由用户裁决；判据的**形状**一个都没动。
    segments, exempt = judged_segments(win, fps, len(luma))
    body_peak = float(np.abs(np.diff(body)).max()) if len(body) > 1 else 0.0
    allowance = rate_allowance(body_peak, args.rate_multiple, args.rate_floor)
    # 判据⑤：mad[j] 是第 j→j+1 帧的内容差，所以片段内的转移是 mad[s:e-1]，
    # 正文参照是 mad[i1:]。正文自己不当被 judged 的对象——它就是参照物（同判据③）。
    body_mad = mad[i1:]
    body_mad_peak = float(body_mad.max()) if len(body_mad) else 0.0
    allowance_content = content_allowance(body_mad_peak, args.content_multiple,
                                          args.content_floor)

    def measure(idx, ids, curves):
        """在给定片段集合上把 ②③④⑤⑥ 各量一遍，返回聚合结果（帧号一律用**绝对**帧）。"""
        res = dict(n_frames=0, lo=None, hi=None, dark=[], peak=0.0, events=[],
                   mad_peak=0.0, over=[], exc=[])
        for (s, e) in segments:
            piece = idx[s:e]
            res["n_frames"] += len(piece)
            if len(piece):
                res["lo"] = piece.min() if res["lo"] is None else min(res["lo"], piece.min())
                res["hi"] = piece.max() if res["hi"] is None else max(res["hi"], piece.max())
            d = np.abs(np.diff(piece))
            if len(d):
                res["peak"] = max(res["peak"], float(d.max()))
            res["dark"].extend(s + int(k) for k in np.where(piece < args.luma_floor)[0])
            # 判据④按片段各跑一遍：跨豁免窗的"腿"根本不相邻，把它们接起来就是凭空造事件
            for k, a0, a1, t0, t1 in flickers(piece, fps, args.flicker_leg_min,
                                              args.flicker_leg_max_s):
                res["events"].append((s + k, a0, a1, t0, t1))
            pmad = ids[s:max(s, e - 1)]
            if len(pmad):
                res["mad_peak"] = max(res["mad_peak"], float(pmad.max()))
            res["over"].extend((s + int(k) + 1, float(pmad[k]))
                               for k in np.where(pmad > allowance_content)[0])
            for item in dissolve_excursions(curves[s:e], fps, args.undershoot_tol,
                                            args.excursion_min_frames, args.level_range_tol,
                                            args.level_min_frames, args.undershoot_rel):
                res["exc"].append(dict(item, t=s / fps + item["t"]))
        res["exc"].sort(key=lambda x: -x["depth"])
        res["lo"] = float(res["lo"]) if res["lo"] is not None else 0.0
        res["hi"] = float(res["hi"]) if res["hi"] is not None else 0.0
        res["seg_delta"] = np.concatenate(
            [np.abs(np.diff(idx[s:e])) for (s, e) in segments if e - s > 1]) \
            if sum(e - s for s, e in segments) > 1 else np.zeros(0)
        return res

    judged = measure(luma, mad, band)
    seg = np.concatenate([luma[s:e] for (s, e) in segments]) if segments else np.zeros(0)
    seg_delta = judged["seg_delta"]
    dark = np.asarray(judged["dark"], dtype=int)
    opening_peak = judged["peak"]
    events = judged["events"]
    opening_mad_peak = judged["mad_peak"]
    over = judged["over"]
    exc = judged["exc"]
    ref_scope = (f"正文参照 {BODY_REF_SECONDS:g}s 切片" if args.opening_only
                 else "正文参照全片")
    print(f"  开场窗口 {win['floor_start']:.2f}~{win['end']:.2f}s"
          f"（钩子 {win['hook_dur']:.2f}s + 片头 {trim_seconds:g}s + 正文起幅 "
          f"{OPENING_TAIL:g}s；结构{'已识别 ' + os.path.basename(win['episode']) if win['resolved'] else '未识别，按 13s 保守窗口'}）"
          f"  被判 {judged['n_frames']} 帧"
          + (f"＝钩子 {win['floor_start']:.2f}~{win['hook_sink']:.2f}s + 正文起幅 "
             f"{win['intro'][1]:.2f}~{win['end']:.2f}s"
             if exempt and win.get("intro") and win.get("hook_sink") is not None
             else (f"＝正文起幅 {win['intro'][1]:.2f}~{win['end']:.2f}s（两段结构，"
                   f"片头就是第一个镜头）" if exempt and win.get("intro") else ""))
          + f"  均亮度 {judged['lo']:.1f}~{judged['hi']:.1f}")
    print(f"    峰值每帧跳变：开场 {opening_peak:.1f} / 正文 {body_peak:.1f}"
          f"（{ref_scope}）→ 判据③允许 {allowance:.1f}"
          f"＝max({args.rate_floor:g}, {args.rate_multiple:g}×正文)")
    print(f"    判据④来回：{len(events)} 处（上限 {args.flicker_max}；"
          f"相邻两条腿都 ≥{args.flicker_leg_min:g} 级且都 ≤{args.flicker_leg_max_s:g}s）"
          + ("；" + " ".join(f"t={k / fps:.2f}s({a0:+.0f}→{a1:+.0f},"
                             f"{t0:.2f}s/{t1:.2f}s)" for k, a0, a1, t0, t1 in events[:4])
             if events else ""))
    print(f"    判据⑤换页：内容跳变峰值 开场 {opening_mad_peak:.1f} / 正文 {body_mad_peak:.1f}"
          f"（{ref_scope}）→ 允许 {allowance_content:.1f}"
          f"＝max({args.content_floor:g}, {args.content_multiple:g}×正文)"
          f"   超过 {len(over)} 帧"
          + ("；" + " ".join(f"t={k / fps:.2f}s(MAD {v:.1f})"
                             for k, v in over[:5]) if len(over) else ""))
    print(f"    判据⑥漏底：越带 {len(exc)} 处（上限 {EXCURSION_MAX}；取证窗口行 "
          f"{r_lo}~{r_hi - 1}，电平≥{args.level_min_frames}帧且整段极差≤{args.level_range_tol:g}级，"
          f"两电平之间连续≥{args.excursion_min_frames}帧越出亮度带 >"
          f"max({args.undershoot_tol:g}, {args.undershoot_rel:g}×带宽)级）"
          + ("；" + " ".join(f"{d['kind']} {d['depth']:.1f}级"
                             f"@t={d['t']:.2f}s"
                             f"[带 {d['band'][0]:.1f}/{d['band'][1]:.1f}→{d['level']:.1f},"
                             f"地板{d['floor']:.1f},{d['frames']}帧]" for d in exc[:4])
             if exc else ""))
    if args.legacy_strobe:
        print(f"    旧频闪判据对照：|Δ|>{STROBE_DELTA:g} 共 "
              f"{int((seg_delta > STROBE_DELTA).sum())} 帧（上限 {STROBE_MAX_FRAMES}）")
    # 豁免窗（钩子段尾淡出 + 共享片头）：**只报告**。固定开场白按 10-03 原样交付，
    # 它内部的黑场起幅与书单→结果页那一拍砸黑都是设计；把数印出来是为了让「这里为什么不算红」
    # 可查，而不是留一条「红但没人知道」的暗账。判据① 只让开片头**内部**的帧对，
    # 片头两侧的接缝（钩子末帧→片头首帧、片头末帧→正文首帧）照常判。
    if exempt:
        ex_lo, ex_hi = exempt
        ex_seg = luma[ex_lo:ex_hi]
        ex_delta = np.abs(np.diff(ex_seg))
        ex_mad = mad[ex_lo:max(ex_lo, ex_hi - 1)]
        ex_events = flickers(ex_seg, fps, args.flicker_leg_min, args.flicker_leg_max_s)
        ex_exc = dissolve_excursions(band[ex_lo:ex_hi], fps, args.undershoot_tol,
                                     args.excursion_min_frames, args.level_range_tol,
                                     args.level_min_frames, args.undershoot_rel)
        would = (int((ex_seg < args.luma_floor).sum() > 0)
                 + int(len(ex_delta) and ex_delta.max() > allowance)
                 + int(len(ex_events) > args.flicker_max)
                 + int(len(ex_mad) and ex_mad.max() > allowance_content)
                 + int(len(ex_exc) > EXCURSION_MAX)
                 + int(len(ex_delta) and (ex_delta > args.max_delta).sum() > 0))
        print(f"    豁免窗（"
              + (f"钩子段尾淡出 {hook_fade:g}s + " if win.get("hook_sink") is not None else "")
              + f"共享片头 {trim_seconds:g}s，"
              f"用户 2026-10-05 裁决原样交付）{ex_lo / fps:.2f}~{ex_hi / fps:.2f}s"
              f"  {len(ex_seg)} 帧：峰值 {float(ex_delta.max()) if len(ex_delta) else 0.0:.1f}"
              f"、最暗 {float(ex_seg.min()) if len(ex_seg) else 0.0:.1f}"
              f"、来回 {len(ex_events)} 处、MAD 峰值 "
              f"{float(ex_mad.max()) if len(ex_mad) else 0.0:.1f}、越带 {len(ex_exc)} 处"
              f"——按同一套判据本会翻 {would} 项红，这里只报告不判红；"
              f"闸刀线只让开了片头内部 "
              f"{int(intro_pair[ex_lo:ex_hi].sum())} 对，接缝两对（"
              f"{(ex_hi - 1) / fps:.2f}s→{ex_hi / fps:.2f}s 跳 "
              f"{float(delta[ex_hi - 1]) if ex_hi - 1 < len(delta) else 0.0:.1f}）照判")

    reds = []
    if len(dark):
        worst = int(dark[np.argmin(luma[dark])])
        reds.append(f"眨眼：{len(dark)} 帧低于 {args.luma_floor:g}"
                    f"（最暗 {judged['lo']:.1f} @ t={(worst + 1) / fps:.2f}s）")
    if opening_peak > allowance:
        reds.append(f"摊不开：被判片段每帧峰值 {opening_peak:.1f} 超过允许 {allowance:.1f}"
                    f"（正文峰值 {body_peak:.1f}；跨度要摊在**时间**上——把这一刻的换页提前/延长，"
                    f"或让两侧的图本来就更接近，不许靠压暗来过门）")
    if len(events) > args.flicker_max:
        reds.append(f"来回：{len(events)} 处去了又回（相邻两条腿都 ≥{args.flicker_leg_min:g} 级、"
                    f"都 ≤{args.flicker_leg_max_s:g}s —— 够不上闸刀线、也没眨眼，"
                    f"但亮度在 0.2s 里来回跑，就是观众说的「闪」）")
    if len(over):
        reds.append(f"换页：{len(over)} 帧内容跳变超过允许 {allowance_content:.1f}"
                    f"（开场 MAD 峰值 {opening_mad_peak:.1f}，正文 MAD 峰值 {body_mad_peak:.1f} —— "
                    f"①~④ 量的都是每帧一个均亮度，整幅换了画而均值没变时它们四条全绿；"
                    f"修法只有让已出现的画面不消失或缩小换的面积，把翻页摊长在这个窗口里"
                    f"到不了正文量级，见本文件判据⑤的算账）")
    if exc:
        d0 = exc[0]
        reds.append(f"漏底：{len(exc)} 处越带（最深 {d0['kind']} {d0['depth']:.1f} 级"
                    f"@t={d0['t']:.2f}s，两侧电平 "
                    f"{d0['band'][0]:.1f}/{d0['band'][1]:.1f} 却读到 {d0['level']:.1f}，"
                    f"连续 {d0['frames']} 帧，地板 {d0['floor']:.1f} 级 —— "
                    f"交叉溶解是凸组合 f·上层+(1−f)·下层，**越不出两侧电平夹出的带**；"
                    f"越带说明黑底从两层之间漏了进来（形如 f·上层+(1−f)²·下层，"
                    f"即退场层被淡了第二次）。压暗洗不掉这条：越带级数和带宽在同比压暗下"
                    f"一起缩小，depth/带宽 是不变量，所以判据里没有也不需要绝对天花板。"
                    f"能改的只有合成形状——只淡入上一层、退场层保持 opacity:1，"
                    f"见 create-episode-preview.mjs 与 .scene 的 z-index）")
    if args.legacy_strobe:
        legacy = int((seg_delta > STROBE_DELTA).sum())
        if legacy > STROBE_MAX_FRAMES:
            reds.append(f"旧频闪判据：|Δ|>{STROBE_DELTA:g} 共 {legacy} 帧")
    for line in reds:
        print(f"    ❌ {line}")

    if len(bad):
        print(f"❌ 亮度闪屏：{len(bad)} 帧单帧亮度跳变超过 {args.max_delta:g}"
              f"（最大 {delta.max():.1f}，正文正常量级 p99≈3）")
        sys.exit(1)
    if reds:
        print(f"❌ 开场亮度闪屏：{len(reds)} 项判据红 —— " + "；".join(reds))
        sys.exit(1)
    scope = "开场窗口" if args.opening_only else f"全片 {len(luma)} 帧 + 开场窗口"
    print(f"✅ 亮度闪屏：{scope}无异常（开场峰值 {opening_peak:.1f}≤{allowance:.1f}、"
          f"去了又回 {len(events)} 处、开场内容跳变 {opening_mad_peak:.1f}≤{allowance_content:.1f}、"
          f"越带 {len(exc)} 处、最暗 {judged['lo']:.1f}、最亮 {judged['hi']:.1f}）")
    sys.exit(0)


if __name__ == "__main__":
    main()
