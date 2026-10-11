#!/usr/bin/env python3
"""第 11 道门全库普查的分类器：两个方向都钉住。

缺陷本体：门自己的汇总末行恒含「无从证明」四个字（`片头原样核对：N 通过 / M 不通过 / K 无从
证明`），所以 2026-10-06 第一版 tmp 驱动里那句 `if "无从证明" in out` 把每一部**健康**片子都
判成 ⚠️，报出「绿 0 / 无从证明 118」，而同一批片子逐行看是 118 个 ✅。普查的产出是红名单，
名单为空就等于「全库没问题」，于是一个分类 bug 会伪装成一次好消息。

对照：
  A 健康片输出 → green（若按整段找词，这里是 unknown —— 用例 F 将旧判法原样重放，证明本测试
    不是空转）；
  B 真·无从证明 → unknown；
  C 红 → red，且「红名单」段落里重复印的花名不产生第二行；
  D 混排多片计数正确；
  E tally 与行首计数不一致 → CensusError（宁可不给数字）。
"""
import importlib.util as _ilu
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_cspec = _ilu.spec_from_file_location(
    "census_intro_original",
    os.path.join(_ROOT, "scripts", "census-intro-original.py"))
cen = _ilu.module_from_spec(_cspec)
_cspec.loader.exec_module(cen)

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r} want {want!r}")
        print(f"❌ {name}")
    else:
        print(f"✅ {name}")


GREEN_OUT = """片头原样核对 ｜ 基准＝media/_raw/pages 六张**未调色原图**在 720x960 可见窗内的平台：[95.2, 97.6]
           ｜ 时刻表＝scripts/lib/intro-template.json（scroll_start=1.08 result_page_at=2.38 trim=3.0 hook_fade=0.4）
  ✅ 一生             W1 接缝黑场 ✅ 6 帧（模板 +7.08s 起）；W2 底图电平 ✅ 5 个平台全部落在原图平台上（最大偏离 4.7 级 ≤ 12）；W3 砸黑一拍 ✅ 7 帧（模板 +9.28s 起）

片头原样核对：1 通过 / 0 不通过 / 0 无从证明
"""

UNKNOWN_OUT = """片头原样核对 ｜ 基准＝…
  ⚠️ 麦琪的礼物         两段式片子：认不出本期结构或读不到帧率：无从证明

片头原样核对：0 通过 / 0 不通过 / 1 无从证明
"""

RED_OUT = """片头原样核对 ｜ 基准＝…
  ❌ 乡下人的悲歌         
      · W1 接缝不到黑：装配层又在把钩子末帧垫帧/溶解，那是 10-04 那版片头
      · W2 底图被改过：最偏离的一个离原图 48.2 级（>12）
      · W3 书单→结果页的砸黑一拍不见了

不通过的都是**回滚之前渲的片头**，压暗、放宽阈值、改装配层都不解决它——只有重渲该段：
  · 乡下人的悲歌

片头原样核对：0 通过 / 1 不通过 / 0 无从证明
"""

MIX_OUT = """片头原样核对 ｜ 基准＝…
  ✅ 一生             W1 接缝黑场 ✅ 6 帧；W2 底图电平 ✅；W3 砸黑一拍 ✅ 7 帧
  ❌ 白夜行            W2 底图电平
      · W2 底图电平 ❌ 平台偏离 42.0 级 > 12
  ⚠️ 麦琪的礼物         两段式片子：无从证明

不通过的都是**回滚之前渲的片头**，压暗、放宽阈值、改装配层都不解决它——只有重渲该段：
  · 白夜行

片头原样核对：1 通过 / 1 不通过 / 1 无从证明
"""

check("A 健康片判绿", [v for v, _, _ in cen.classify(GREEN_OUT, expect=1)], ["green"])
check("B 真无从证明", [v for v, _, _ in cen.classify(UNKNOWN_OUT, expect=1)], ["unknown"])
check("C 红名单不重复印花", [n for _, n, _ in cen.classify(RED_OUT, expect=1)], ["乡下人的悲歌"])
check("C2 红判红", [v for v, _, _ in cen.classify(RED_OUT, expect=1)], ["red"])
check("C3 分行罪证并进同一条判决",
      [len(d) > 20 for _, _, d in cen.classify(RED_OUT, expect=1)], [True])
check("C4 三条 W 都在 detail 里",
      [all(w in d for w in ("W1", "W2", "W3"))
       for _, _, d in cen.classify(RED_OUT, expect=1)], [True])
check("C5 花名列表不当判决行",
      [n for _, n, _ in cen.classify(RED_OUT, expect=1)].count("乡下人的悲歌"), 1)
check("D 混排计数",
      sorted(v for v, _, _ in cen.classify(MIX_OUT, expect=3)),
      ["green", "red", "unknown"])
check("D2 分行罪证只挂在它上面那条",
      [("偏离 42.0" in d) == (v == "red")
       for v, _n, d in cen.classify(MIX_OUT, expect=3)], [True, True, True])

# E：tally 与行首计数不一致必须炸，而不是默默给出一个数字
try:
    cen.classify(GREEN_OUT.replace("1 通过 / 0 不通过 / 0 无从证明", "0 通过 / 1 不通过 / 0 无从证明"))
    check("E tally 不一致报错", "no raise", "CensusError")
except cen.CensusError:
    check("E tally 不一致报错", "CensusError", "CensusError")

# F：证明本测试不空转 —— 旧判法（整段找词）在 A 上给出错误答案
old_judged = "unknown" if ("无从证明" in GREEN_OUT and "❌" not in GREEN_OUT) else "other"
check("F 旧判法把健康片误判为无从证明", old_judged, "unknown")

# G：普查脚本里不得留整段找词的判法。
# 扫描时先剔掉模块 docstring —— 那段散文正是在**描述**这个缺陷，留着它，这条断言会像
# check.mjs 那次一样抓到自家注释而不是抓到代码。
src = open(os.path.join(_ROOT, "scripts", "census-intro-original.py"), encoding="utf-8").read()
import ast as _ast
_mod = _ast.parse(src)
_doc = _ast.get_docstring(_mod, clean=False)
_doc_span = set()
if _doc:
    for _n in _mod.body[:1]:
        if isinstance(_n, _ast.Expr) and isinstance(_n.value, _ast.Constant) \
                and isinstance(_n.value.value, str):
            _doc_span = set(range(_n.lineno, (_n.end_lineno or _n.lineno) + 1))
code = "\n".join(l for i, l in enumerate(src.splitlines(), 1)
                 if i not in _doc_span and not re.match(r"^\s*#", l))
check("G 普查不再按整段找词分类", ('"无从证明" in out' in code) or ("'无从证明' in out" in code), False)
check("G2 docstring 确实写了这个缺陷（否则 G 是空转）",
      '"无从证明" in out' in src, True)

# H：交付成片的枚举要用真实目录，且至少能找到一批
n = len(cen.delivered_finals())
check("H 枚举到交付成片", n > 100, True)

# I：普查的两个正则都钉在门的输出字面量上。门哪天改了打印格式，行首符号就扫不到行，
# 名单会变空 —— 空名单读起来和「全库没问题」一模一样，所以这里反向钉住字面量本身。
gate_src = open(os.path.join(_ROOT, "scripts", "check-intro-is-original.py"),
                encoding="utf-8").read()
check("I1 tally 字面量仍在", "片头原样核对：" in gate_src, True)
check("I2 逐片判决行字面量仍在", "{mark} {name:14s} {detail}" in gate_src, True)
check("I3 判决符号仍由 verdict 映射", '{"green": "✅", "red": "❌", "unknown": "⚠️"}' in gate_src, True)

print("\n".join(fails) if fails else f"intro-census classify: ok（{n} 部交付成片）")
raise SystemExit(1 if fails else 0)
