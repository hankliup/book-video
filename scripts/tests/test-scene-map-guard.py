#!/usr/bin/env python3
"""渲前 scene-map 闸的回归：证明「本期渲不出正文」在花钱渲钩子**之前**就判得出来。

为什么钉这一条（2026-10-11《少年维特的烦恼》）：钩子段和片头都渲完了（约十分钟、8.8MB + 2.5MB
像素），走到正文合成那一行才 `FileNotFoundError: episodes/少年维特的烦恼/scene-map.json`。
它是全库 158 期里唯一没有这份文件的一期——正文当年由专属兜底渲染器
`scripts/archive/legacy-renderers/render_werther_template_fallback.py` 渲染，图层—行文配对写死在
那个脚本的 SCENE_RUNS 里，从来没有落成 scene-map.json。轴闸照过、钩子照渲，只在最后一步炸。

`scripts/check-scene-map-before-render.py` 把这件事提前到一次 stat + 一次 json.load。它和
`check-image-provenance.py` 的图层结构检查不是一条口径：那边对「没有 scene-map.json」写的是
⚠️ 跳过（旧版渲染路径的期数不该被卡住，别的会话刚建好 scene-map、图还没落盘时也不该红），
这边判的是「`render-hook-batch.sh` 只会调 scene-map 驱动的那一个渲染器」——缺席不是旧版，是渲不出来。
所以这里缺席必须是红。两个方向都要证：

  健康图层表          → 绿                （否则这道闸永远拦，等于又一个假红）
  删掉 scene-map.json → 红 + 给出修法     （否则它不拦任何东西，是走过场）
  覆盖留缝            → 红
  相邻两层同图        → 红
  图层没有对应文件    → 红
  行号越界            → 红
  接线                → render-hook-batch.sh 里真的调了它，且缺席的期数不再能进渲染

2026-10-14 加上第六族断言（行锚点），因为上面四条拦不住《源泉》那一族改动：文案把相邻两行
并成一行（95 行变 94 行），只要把末层 order_end 也改成 94，越界与覆盖两条照样绿，而中间每个
切点都往回错了一行——行号是整数，它对着的文字变了它不报错。锚点（每层 anchor_start/anchor_end
+ 顶层 body_rows）把「作者当时看到的句子」钉在图层表上，于是新增这几臂：

  改字没改行          → 红话给「重盖章」，不给「重排行号」
  合并行并喂绿越界    → 红 + 按锚点给出正确行号；同一份表剥掉锚点必须绿（这条闸的增量本身）
  锚点在两行都命中    → 红「无法唯一还原」
  没有锚点            → 不判，且 anchor_line 必须写「判不了＝没测」
  盖章工具            → 拒盖已经对不上的表
  版本口径            → 围城/女儿红这种多版本 CSV，锚点比的是本期渲染的那一版
  变异阶梯            → 摘掉 judge／把半盖章的覆盖数喂成「全覆盖」／盖章不写锚点／退回全表，四条都要翻红

夹具全在 `tempfile.mkdtemp` 里搭，不碰 episodes/ 真目录；真证人那两臂用《源泉》留下的
`scene-map.json.pre-merge-1014` 与 `script-body.csv.pre-merge-1014`（不在仓库里时打 🔒，
同《少年维特的烦恼》那一档）；最后另跑一遍真仓库，把「已交付的期数全绿」和「已交付的期数
全都盖了锚点」两件事钉在盘上——只在合成夹具上绿的闸门，拦不住下一次真实缺席。
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GUARD = os.path.join(ROOT, "scripts", "check-scene-map-before-render.py")
BOOK = "夹具书"

_spec = importlib.util.spec_from_file_location("scene_map_guard", GUARD)
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


def make_episode(root, *, scene_map=True, layers=None, rows=6):
    """搭一期最小可判的夹具：script-body.csv + images/ 三张图 + 可选 scene-map.json。"""
    ep = os.path.join(root, "episodes", BOOK)
    img = os.path.join(ep, "images")
    os.makedirs(img, exist_ok=True)
    with open(os.path.join(ep, "script-body.csv"), "w", encoding="utf-8") as f:
        f.write("version,order,text\n")
        for i in range(1, rows + 1):
            f.write(f"A,{i},第{i}行旁白\n")
    for name in ("result-bridge", "atmosphere-1", "atmosphere-2"):
        open(os.path.join(img, name + ".png"), "wb").close()
    if scene_map:
        layers = layers if layers is not None else [
            {"name": "result-bridge", "order_start": 1, "order_end": 1, "static": True},
            {"name": "atmosphere-1", "order_start": 2, "order_end": 3},
            {"name": "atmosphere-2", "order_start": 4, "order_end": 6},
        ]
        write_scene_map(root, layers)
    return ep


def write_scene_map(root, layers):
    ep = os.path.join(root, "episodes", BOOK)
    with open(os.path.join(ep, "scene-map.json"), "w", encoding="utf-8") as f:
        json.dump({"episode": BOOK, "layers": layers}, f, ensure_ascii=False)


def run_case(title, build, expect_red, needle=None):
    """expect_red=False 要求绿；True 要求红且（给了 needle 时）红话里含 needle。

    needle 是防空转的：只判「红不红」的话，一条永远红的断言和一条真判据长得一样。
    """
    scratch = tempfile.mkdtemp(prefix="scene-map-guard-")
    try:
        build(scratch)
        problems = G.check_episode(BOOK, root=scratch)
        if expect_red is False:
            if problems:
                raise SystemExit(f"❌ {title}：健康夹具被判红（这道闸会永远拦）\n   " + "\n   ".join(problems))
            print(f"  ✅ {title} → 绿")
            return
        if not problems:
            raise SystemExit(f"❌ {title}：被撤的形状没被判出来（这道闸不拦任何东西）")
        joined = "\n".join(problems)
        if needle and needle not in joined:
            raise SystemExit(f"❌ {title}：判红了但红话里没有「{needle}」，等于换了个理由红：{joined[:160]}")
        print(f"  ✅ {title} → 红（{problems[0][:52]}…）")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------- 行锚点那几臂（2026-10-14）

STAMP_TOOL = os.path.join(ROOT, "scripts", "stamp-scene-map-anchors.py")
ANCHOR_LIB = os.path.join(ROOT, "scripts", "lib", "scene_map_anchors.py")
YUANQUAN = os.path.join(ROOT, "episodes", "源泉")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SA = load_mod("scene_map_anchors_under_test", ANCHOR_LIB)


def ep_json(scratch):
    return os.path.join(scratch, "episodes", BOOK, "scene-map.json")


def read_map(scratch):
    return json.load(open(ep_json(scratch), encoding="utf-8"))


def write_map(scratch, scene):
    with open(ep_json(scratch), "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=2)


def write_csv(scratch, lines):
    """lines: [(version, order, text)]，整期行号由此重排。"""
    with open(os.path.join(scratch, "episodes", BOOK, "script-body.csv"), "w",
              encoding="utf-8") as f:
        f.write("version,order,text\n")
        for v, o, t in lines:
            f.write(f"{v},{o},{t}\n")


def stamp_tool(scratch, *args):
    r = subprocess.run([sys.executable, STAMP_TOOL, "--root", scratch, *args],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def fresh_stamp(scratch):
    """盖章走真工具（不是测试自己调 SA.stamp），这样接线也被判到。"""
    rc, out = stamp_tool(scratch, "--all")
    if rc != 0:
        raise SystemExit(f"❌ 夹具盖章失败 rc={rc}：{out[:200]}")
    judged, slots = SA.coverage(read_map(scratch))
    if judged != slots:
        raise SystemExit(f"❌ 盖完章锚点只写了 {judged}/{slots} 层：盖章工具与判据不是同一份口径")
    return judged, slots


def merge_shape(root):
    """《源泉》那一族改动的最小复刻：合并相邻两行 + 把末层 order_end 改小一位。

    合并之后行号全体上移一位，改末层只是把「越界」与「未覆盖」两条喂绿；中间每个切点都错一行，
    而那正是旧四条断言看不见的部分。变异阶梯与 ③⑦ 共用这一份夹具：形状只写一次。
    """
    make_episode(root)
    fresh_stamp(root)
    rows = SA.read_body_rows(os.path.join(root, "episodes", BOOK))
    merged = [("A", 1, rows[1]), ("A", 2, rows[2] + rows[3]),
              ("A", 3, rows[4]), ("A", 4, rows[5]), ("A", 5, rows[6])]
    write_csv(root, merged)
    scene = read_map(root)
    scene["layers"][-1]["order_end"] = 5
    write_map(root, scene)
    return root


def stamped_shape(root):
    """健康对照：盖完章、文案没动过。M3 要用它，否则「原本落了多少个锚点」没有基线。"""
    make_episode(root)
    fresh_stamp(root)
    return root


def half_stamp_shape(root):
    """盖了一半的形状：`has_anchors` 为真而 `coverage` 不满——M2 唯一的对照物。

    为什么 M2 不能拿全裸的图层表当对照：`anchor_line` 先看 `has_anchors`，全裸那份走的是
    「0/{slots}…判不了」分支，摘掉 `coverage` 也翻不成 ✅——那是两道独立分支挡同一个假绿，
    不是这条变异的靶子。半盖章那份才只靠 `coverage` 一行撑着：它一旦被喂成 (slots, slots)，
    「缺的那些等于没判」立刻变成「✅ 全覆盖」，也就是**没测被写成了测过**。
    """
    stamped_shape(root)
    scene = read_map(root)
    scene["layers"][1]["anchor_end"] = ""
    write_map(root, scene)
    return root


def multi_version_shape(root):
    """围城/女儿红的形状：同一份 CSV 里两版行文同号不同字，本期渲染的是 B 版。"""
    make_episode(root)
    write_csv(root, [("A", o, f"A版第{o}行") for o in range(1, 7)]
              + [("B", o, f"第{o}行旁白") for o in range(1, 7)])
    with open(os.path.join(root, "episodes", BOOK, "brief.json"), "w", encoding="utf-8") as f:
        json.dump({"scriptVersion": "B"}, f, ensure_ascii=False)
    fresh_stamp(root)
    return root


def anchor_arms():
    print("----- 行锚点：合并/删行造成的整体错位（《源泉》2026-10-14）-----")

    def a1(root):
        make_episode(root)
        fresh_stamp(root)
        if G.check_episode(BOOK, root=root):
            raise SystemExit("❌ ①健康：刚盖完章就被判红，这道闸会永远拦")
        line = G.anchor_line(BOOK, root=root)
        if "全覆盖" not in line:
            raise SystemExit(f"❌ ①健康的 anchor_line 没报全覆盖：{line}")
        print("  ✅ ①盖章后不动文案 → 绿，且行上写「全覆盖」")

    def a2(root):
        make_episode(root)
        fresh_stamp(root)
        rows = SA.read_body_rows(os.path.join(root, "episodes", BOOK))
        polish = [("A", o, ("改过了：" + t) if o == 3 else t)
                  for o, t in sorted(rows.items())]
        write_csv(root, polish)                     # 行数没变，只改了第 3 行的字
        problems = G.check_episode(BOOK, root=root)
        if not problems:
            raise SystemExit("❌ ②改了字不判红：那锚点只是装饰")
        joined = "\n".join(problems)
        if "多半只是改了字" not in joined or "重盖章" not in joined:
            raise SystemExit(f"❌ ②红话没给正确修法（改字应当重盖章，不该叫人重排行号）：{joined[:160]}")
        if "把 order" in joined:
            raise SystemExit(f"❌ ②行数没变却让人改行号，会把正常改字派去做正文重排：{joined[:160]}")
        print("  ✅ ②改字没改行 → 红话给「重盖章」，不给「重排行号」")

    def a3(root):
        merge_shape(root)
        problems = G.check_episode(BOOK, root=root)
        joined = "\n".join(problems)
        if not problems:
            raise SystemExit("❌ ③合并两行+末层改号没被判出来：锚点这一族等于没写")
        if "改成" not in joined or "指错了行" not in joined:
            raise SystemExit(f"③红话没按锚点给出正确行号：{joined[:200]}")
        stripped = read_map(root)
        SA.unstamp(stripped)
        write_map(root, stripped)
        old = G.check_episode(BOOK, root=root)
        if old:
            raise SystemExit(f"❌ ③增量证明失败：这份表在旧四条断言下本该是绿的（红来自别处，"
                             f"于是「锚点才有牙齿」这句话没被钉住）：{old[0][:120]}")
        print("  ✅ ③合并行并喂绿越界 → 锚点红且给出正确行号；同一份表剥掉锚点＝绿（增量本身）")

    def a4(root):
        make_episode(root)
        if G.check_episode(BOOK, root=root):
            raise SystemExit("❌ ④没有锚点的健康夹具被判红：存量期数会被这道闸拦住")
        line = G.anchor_line(BOOK, root=root)
        if "判不了＝没测" not in line or "✅" in line:
            raise SystemExit(f"❌ ④无锚点被写成通过或漏掉了「判不了」：{line}")
        print("  ✅ ④没有锚点 → 不判，且行上明写「判不了＝没测」")

    def a5(root):
        """同一句话在两行都出现时不许「照第一个命中改行号」——那会把切点搬到另一处同样的话上。"""
        make_episode(root)
        texts = {1: "第1行旁白", 2: "同一句话", 3: "同一句话",
                 4: "第4行旁白", 5: "第5行旁白", 6: "第6行旁白"}
        write_csv(root, [("A", o, texts[o]) for o in sorted(texts)])
        scene = read_map(root)
        for L, anc in zip(scene["layers"], ["第1行旁白", "同一句话", "同一句话"]):
            L["anchor_start"] = anc
            L["anchor_end"] = texts[L["order_end"]]
        scene["body_rows"] = 6
        write_map(root, scene)
        problems = G.check_episode(BOOK, root=root)
        hits = [p for p in problems if "无法唯一还原" in p]
        if len(hits) != 1 or len(problems) != 1:
            raise SystemExit(f"❌ ⑤同一句在两行都命中时没单独报「无法唯一还原」（或连带误红别处）："
                             + " / ".join(p[:70] for p in problems))
        if "人工确认" not in hits[0]:
            raise SystemExit(f"❌ ⑤红话没让人工确认，等于给了一个假的自动修法：{hits[0][:120]}")
        print("  ✅ ⑤锚点在两行都整行命中 → 红「无法唯一还原」，不假装能自动还原")

    def a6(root):
        """多版本 CSV：锚点比的是本期渲染的那一版，不是先出现的那一版。"""
        make_episode(root)
        write_csv(root, [("A", o, f"A版第{o}行") for o in range(1, 7)]
                  + [("B", o, f"第{o}行旁白") for o in range(1, 7)])
        with open(os.path.join(root, "episodes", BOOK, "brief.json"), "w", encoding="utf-8") as f:
            json.dump({"scriptVersion": "B"}, f, ensure_ascii=False)
        _judged, slots = fresh_stamp(root)
        problems = G.check_episode(BOOK, root=root)
        if problems:
            raise SystemExit(f"❌ ⑥围城/女儿红这种多版本期数被判红（锚点跟错了版本）：{problems[0][:120]}")
        return root

    def a7(root):
        merge_shape(root)
        rc, out = stamp_tool(root, "--all")
        if rc == 0 or "拒盖" not in out:
            raise SystemExit(f"❌ ⑦盖章工具给已经错位的表盖了章（rc={rc}）：那会把钉住错位当成修复\n{out[:200]}")
        print("  ✅ ⑦盖章工具拒盖已经对不上的图层表（不把当前行文当意图）")

    print("----- 盖章工具与判据：接线、口径、真证人 -----")
    for title, fn in [("①盖章后不动文案", a1), ("②改字没改行", a2), ("③合并行并喂绿越界", a3),
                      ("④没有锚点", a4), ("⑤锚点命中两行", a5)]:
        scratch = tempfile.mkdtemp(prefix="scene-map-anchor-")
        try:
            fn(scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    scratch = tempfile.mkdtemp(prefix="scene-map-anchor-")
    try:
        a6(scratch)
        rc, out = stamp_tool(scratch, "--check")
        if rc != 0 or "1 期已盖" not in out:
            raise SystemExit(f"❌ ⑧--check 在多版本期数上没报「已盖」：{out[:160]}")
        print("  ✅ ⑧多版本 CSV：锚点按 brief.scriptVersion 那一版比，A 版不同文不误伤")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    scratch = tempfile.mkdtemp(prefix="scene-map-anchor-")
    try:
        a7(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    gate_src = open(GUARD, encoding="utf-8").read()
    stamp_src = open(STAMP_TOOL, encoding="utf-8").read()
    builder_src = open(os.path.join(ROOT, "scripts", "build-scene-map.py"), encoding="utf-8").read()
    for src, who, tokens in [
        (gate_src, "渲前闸", ["scene_map_anchors", "SA.judge", "anchor_rows"]),
        (stamp_src, "盖章工具", ["scene_map_anchors", "SA.stamp", "SA.judge"]),
        (builder_src, "图层表生成器", ["scene_map_anchors", "SA.stamp"]),
    ]:
        missing = [t for t in tokens if t not in src]
        if missing:
            raise SystemExit(f"❌ {who} 不再引用 {missing}：锚点判据开始有第二份实现，"
                             f"三处会各自漂开")
    for forbidden in ['L.get("anchor_start") ==', "anchor_start ==", '== L["anchor']:
        if forbidden in gate_src:
            raise SystemExit(f"❌ 渲前闸里出现了自己的锚点比较「{forbidden}」：抄来的那份测的是副本")

    print("----- 真证人：《源泉》合并前后两份留档 -----")
    pre_map = os.path.join(YUANQUAN, "scene-map.json.pre-merge-1014")
    pre_csv = os.path.join(YUANQUAN, "script-body.csv.pre-merge-1014")
    if not (os.path.exists(pre_map) and os.path.exists(pre_csv)):
        print("  🔒 《源泉》的合并前后留档不在仓库（已交付的期会被搬出 episodes/），"
              "这一臂由 ①~⑧ 的合成夹具证明判据落过地")
    else:
        scratch = tempfile.mkdtemp(prefix="scene-map-yuanquan-")
        try:
            ep = os.path.join(scratch, "episodes", "源泉")
            os.makedirs(ep)
            shutil.copytree(os.path.join(YUANQUAN, "images"), os.path.join(ep, "images"))
            shutil.copy(os.path.join(YUANQUAN, "script-body.csv"),
                        os.path.join(ep, "script-body.csv"))
            scene = json.load(open(pre_map, encoding="utf-8"))
            pre_rows = {}
            for r in csv.DictReader(open(pre_csv, encoding="utf-8")):
                pre_rows[int(r["order"])] = (r.get("text") or "").strip()
            SA.stamp(scene, pre_rows)               # 锚点取自**合并前**那份文案：这是真证人，不是夹具
            merged_rows, _v, _b = SA.anchor_rows(ep)
            problems, judged = SA.judge(scene, merged_rows)
            if not problems or judged == 0:
                raise SystemExit("❌ 真证人臂空转：《源泉》合并前的表对着合并后的文案没判出任何东西")
            if not any("改成" in p for p in problems):
                raise SystemExit(f"❌ 真证人红话没给出正确行号：{problems[0][:120]}")
            write_path = os.path.join(ep, "scene-map.json")
            # 把末层 order_end 改成新行数的上限：这一步就是「把越界与覆盖喂绿」的那一手。
            scene["layers"][-1]["order_end"] = len(merged_rows)
            json.dump(scene, open(write_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            problems2, _ = SA.judge(scene, merged_rows)
            SA.unstamp(scene)
            json.dump(scene, open(write_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            old_verdict = G.check_episode("源泉", root=scratch)
            if old_verdict:
                raise SystemExit(f"❌ 真证人「剥掉锚点即绿」不成立，红来自别处：{old_verdict[0][:120]}")
            if not problems2:
                raise SystemExit("❌ 真证人：把末层改成 94 之后锚点也没判出来——这一族的形状没被钉住")
            print(f"  ✅ 真证人《源泉》：合并前的表 + 合并后的文案，锚点判出 {len(problems)} 处并给出"
                  f"正确行号；末层改成 94（旧四条全绿）仍判出 {len(problems2)} 处")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        # 修好的那一期本身必须绿：留档对齐过，现在盘上的表对得上现在盘上的文案。
        # 但这份绿必须是**测出来的**：无锚点的图层表在 check_episode 上同样返回空列表，
        # 所以这里要求锚点覆盖满——否则「修好了」只是「没判」的另一种说法。
        if G.check_episode("源泉"):
            raise SystemExit("❌ 真证人：修好的《源泉》现在还是红的（12:16 那次重排没落全）")
        yq_line = G.anchor_line("源泉")
        if "✅" not in yq_line:
            raise SystemExit(f"❌ 真证人：《源泉》的绿来自没锚点而不是对齐（{yq_line[:70]}）"
                             f"——先 python3 scripts/stamp-scene-map-anchors.py \"源泉\"")
        print(f"  ✅ 修好的《源泉》在盘上判绿，且锚点判得到（{yq_line.split('（')[0]}）")


# ---------------------------------------------------------------- 变异阶梯

MUT_LIB_SRC = open(ANCHOR_LIB, encoding="utf-8").read()


def mutated_lib(tag, anchor, replacement):
    if MUT_LIB_SRC.count(anchor) != 1:
        raise SystemExit(f"❌ 变异锚点「{anchor}」在 scene_map_anchors.py 里命中 "
                         f"{MUT_LIB_SRC.count(anchor)} 次：生产写法变了，先把它和这里的锚一起更新")
    d = tempfile.mkdtemp(prefix="scene-map-mut-")
    path = os.path.join(d, f"sa_mut_{tag}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(MUT_LIB_SRC.replace(anchor, replacement))
    return load_mod(f"sa_mut_{tag}", path), d


def with_lib(mut):
    """把变异后的判据塞进 sys.modules，再取一份**全新**的渲前闸：验的是接线后的判决会翻面。"""
    if mut is None:
        sys.modules.pop("scene_map_anchors", None)
    else:
        sys.modules["scene_map_anchors"] = mut
    return load_mod(f"guard_bound_{id(mut)}", GUARD)


def mutation_arms():
    """四条变异：每条都必须**只在被摘掉的那一处**翻面。

    翻面方向按形状自己的期望定：M1/M3 是「红→绿」「落了→没落」，M4 是「绿→红」，
    M2 是「部分测量→报成全覆盖」。拿同一个期望去套四组，就会把「判不了」和「判红」混成一种颜色。
    M2 的夹具是半盖章那份（`half_stamp_shape`），不是全裸那份——全裸那份由 `has_anchors` 单独挡着，
    摘掉 `coverage` 它仍写 ⚠️，用它当对照只会证明这条变异打不中，不证明判据有牙齿。
    """
    print("----- 变异阶梯：摘掉哪一行，这一族就失去牙齿 -----")
    muts = [
        ("M1 judge 恒绿（锚点退化成装饰）",
         "def judge(scene, rows):\n"
         '    """返回 (problems, judged)。judged=0 表示这一期判不了（无锚点），不是绿。"""',
         "def judge(scene, rows):\n    return [], 0  # 变异：判据被摘掉",
         merge_shape, "red"),
        ("M2 把没测报成测过（覆盖数喂满 → 半盖章读成全覆盖）",
         "    return judged, slots",
         "    return slots, slots  # 变异：把没测报成测过",
         half_stamp_shape, "line"),
        ("M3 盖章不落锚点（盖了等于没盖）",
         "def stamp(scene, rows, note=None):",
         "def stamp(scene, rows, note=None):\n    return 0  # 变异：不落锚点",
         stamped_shape, "stamp"),
        ("M4 锚点不比版本（多版本期数跟先出现的那一版比）",
         "    version, _changed, _episode = resolve_segment_version(ep_dir, path)",
         '    version = "A"  # 变异：不再问本期渲染的是哪一版',
         multi_version_shape, "green"),
    ]
    for tag, anchor, repl, build, kind in muts:
        scratch = tempfile.mkdtemp(prefix="scene-map-mut-")
        mut = d = None
        try:
            build(scratch)
            real_problems = G.check_episode(BOOK, root=scratch)
            real_line = G.anchor_line(BOOK, root=scratch)
            if kind == "red" and not real_problems:
                raise SystemExit(f"❌ {tag}：健康对照不红，这条变异无从翻面（夹具搭错了）")
            if kind == "green" and real_problems:
                raise SystemExit(f"❌ {tag}：健康对照被判红，这一臂先坏了：{real_problems[0][:120]}")
            if kind == "line" and "缺的那些等于没判" not in real_line:
                raise SystemExit(f"❌ {tag}：半盖章那行没写「缺的那些等于没判」：{real_line[:120]}")
            if kind == "stamp":
                scene0 = read_map(scratch)
                judged0, slots = SA.coverage(scene0)
                if not slots or judged0 != slots:
                    raise SystemExit(f"❌ M3：夹具盖章后是 {judged0}/{slots} 个槽位，没有满锚点基线")
                SA.unstamp(scene0)
                write_map(scratch, scene0)     # 回到没盖的形状，让绑了变异判据的工具重新落笔
                healthy = slots
            mut, d = mutated_lib(tag.split()[0], anchor, repl)
            sys.modules["scene_map_anchors"] = mut
            G2 = with_lib(mut)
            if kind == "line":
                line = G2.anchor_line(BOOK, root=scratch)
                if "没判" in line or "判不了" in line:
                    raise SystemExit(f"❌ {tag} 没翻面：摘掉覆盖之后仍然报「没测」：{line[:100]}")
                if "全覆盖" not in line or "✅" not in line:
                    raise SystemExit(f"❌ {tag} 翻面了但没写成「✅ 全覆盖」：{line[:80]}")
                if real_problems:
                    raise SystemExit(f"❌ {tag}：对照物本身是红的，翻面证明不了「绿来自这条测量」")
                print(f"  ✅ {tag} → 同一期从「缺的那些等于没判」变成「✅ 全覆盖」"
                      f"（假绿来自这一行，不是来自别处）")
            elif kind == "stamp":
                # 变异后的**判据**只影响本进程，工具是另一次 import：这里在同一进程里
                # 重新加载盖章工具本体，让它绑到变异判据上（subprocess 会绑回生产那份）。
                tool = load_mod(f"stamp_mut_{tag.split()[0]}", STAMP_TOOL)
                kind_code, line = tool.stamp_one(BOOK, root=scratch)
                after = SA.coverage(read_map(scratch))[0]
                if kind_code not in ("ok", "same") or after >= healthy:
                    raise SystemExit(f"❌ {tag} 没翻面：摘掉 SA.stamp 之后盘上仍有 {after}/{healthy} 个槽位"
                                     f"（stamp_one={kind_code} {line[:80]}）")
                print(f"  ✅ {tag} → 工具照样报「{kind_code}」，盘上锚点却停在 {after}/{healthy}"
                      f"（盖了等于没盖）")
            elif kind == "green":
                after = G2.check_episode(BOOK, root=scratch)
                if not after:
                    raise SystemExit(f"❌ {tag} 没翻面：不比版本之后多期数仍然判绿——"
                                     f"说明这条口径压根没被执行")
                print(f"  ✅ {tag} → 围城/女儿红那种多版本期数从绿翻成红（版本解析真的在起作用）")
            else:
                after = G2.check_episode(BOOK, root=scratch)
                if after:
                    raise SystemExit(f"❌ {tag} 没翻面：摘掉判据之后照样红，说明红来自别处")
                print(f"  ✅ {tag} → 摘掉后同一份表判绿（红确实来自这条判据）")
        finally:
            sys.modules.pop("scene_map_anchors", None)
            if d:
                shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(scratch, ignore_errors=True)


def main():
    print("----- 渲前 scene-map 闸：双向对照 -----")
    run_case("健康图层表", make_episode, expect_red=False)

    def withdraw_file(root):
        make_episode(root)
        p = os.path.join(root, "episodes", BOOK, "scene-map.json")
        assert os.path.exists(p), "夹具没建出 scene-map.json，本例空转"
        os.remove(p)
    run_case("删掉 scene-map.json（《少年维特的烦恼》的形状）", withdraw_file,
             expect_red=True, needle="缺 scene-map.json")

    def gap(root):
        make_episode(root, layers=[
            {"name": "result-bridge", "order_start": 1, "order_end": 1},
            {"name": "atmosphere-1", "order_start": 2, "order_end": 3},
            {"name": "atmosphere-2", "order_start": 5, "order_end": 6},   # 行 4 没人盖
        ])
    run_case("覆盖留缝（成片会露白）", gap, expect_red=True, needle="未覆盖")

    def adjacent_dup(root):
        make_episode(root, layers=[
            {"name": "result-bridge", "order_start": 1, "order_end": 1},
            {"name": "atmosphere-1", "order_start": 2, "order_end": 3},
            {"name": "atmosphere-1", "order_start": 4, "order_end": 6},   # 相邻两层同图
        ])
    run_case("相邻两层同图（切点没有变化）", adjacent_dup, expect_red=True, needle="相邻两层同图")

    def missing_png(root):
        make_episode(root)
        os.remove(os.path.join(root, "episodes", BOOK, "images", "atmosphere-2.png"))
    run_case("图层在 images/ 里没有对应文件", missing_png, expect_red=True, needle="没有对应文件")

    def out_of_range(root):
        make_episode(root, layers=[
            {"name": "result-bridge", "order_start": 1, "order_end": 1},
            {"name": "atmosphere-1", "order_start": 2, "order_end": 3},
            {"name": "atmosphere-2", "order_start": 4, "order_end": 99},  # 只有 6 行
        ])
    run_case("行号越界", out_of_range, expect_red=True, needle="越界")

    anchor_arms()
    mutation_arms()

    print("----- 接线：渲染链真的调了这道闸 -----")
    sh = open(os.path.join(ROOT, "scripts", "render-hook-batch.sh"), encoding="utf-8").read()
    for token, why in [
        ("scene_map_guard \"$EPISODE\"", "渲染链不再问 scene-map 在不在，缺席又回到十分钟之后才炸"),
        ("scripts/check-scene-map-before-render.py", "接线改成了别的脚本：判据不再由渲染链执行"),
    ]:
        if token not in sh:
            raise SystemExit(f"❌ render-hook-batch.sh 少了 {token}：{why}")
    print("  ✅ render-hook-batch.sh 在 axis_guard 之后调 scene_map_guard")

    print("----- 真仓库：已交付期数必须全绿 -----")
    eps = sorted(d for d in os.listdir(os.path.join(ROOT, "episodes"))
                 if os.path.isdir(os.path.join(ROOT, "episodes", d)))
    delivered = []
    for b in eps:
        if not os.path.isdir(os.path.join(ROOT, "episodes", b, "renders")):
            continue
        if not any(f.endswith("-final.mp4") for f in os.listdir(os.path.join(ROOT, "episodes", b, "renders"))):
            continue
        delivered.append(b)
    delivered_red = []
    for b in delivered:
        problems = G.check_episode(b)
        if problems:
            delivered_red.append((b, problems[0]))
    if delivered_red:
        raise SystemExit("❌ 有已交付期数过不了这道闸（重渲会在正文合成处炸）：\n   "
                         + "\n   ".join(f"{b}：{p[:80]}" for b, p in delivered_red))
    # 「扫到 0 期」不是绿，是这一臂根本没跑：判据在真仓库上一次都没落过地。
    if not delivered:
        raise SystemExit("❌ episodes/ 里一期已交付的都没有 —— 全库这一臂无从跑起，不许报绿")
    # 《少年维特的烦恼》是 2026-10-11 补的那一份：它**在仓库里**时必须单独绿，否则「全绿」
    # 可能来自判据没跑到它。它被搬出仓库之后（已交付的期会被用户整目录归档，2026-10-12 实测
    # 《少年》《少年维特的烦恼》都已不在 episodes/）改成锁形记录——修好的那一期是历史，
    # 不是一条永远还不完的债（同 AGENTS.md 里 hook 录音「冻结的 take 判 🔒/exit-0」那一档）；
    # 但绿名单非空这一条照判，所以上面那句不是装饰。
    if "少年维特的烦恼" in eps:
        if G.check_episode("少年维特的烦恼"):
            raise SystemExit("❌ 《少年维特的烦恼》红：scene-map.json 没补上或补得不对")
        print(f"  ✅ 全库 episodes/ {len(eps)} 期里 {len(delivered)} 期已交付者全绿，含《少年维特的烦恼》")
    else:
        print(f"  🔒 《少年维特的烦恼》已不在仓库（已交付的期会被搬出 episodes/），"
              f"这一臂改由 {len(delivered)} 期在库已交付者证明判据真的落过地")

    # 上面那一臂全绿只说明「四条老断言没抓到东西」，锚点缺席时它照样绿——这正是《源泉》
    # 合并行之后仍然过闸的形状。所以已交付的每一期都必须**盖得上锚点**：判定槽位齐了
    # 才算这一族断言真的在保护这一期。分母沿用上面那个 delivered 名单，因此「0 期盖过」
    # 不是绿而是一条点名盖章命令的红（同《少年》那一档：扫不到东西的臂不许报平安）。
    print("----- 真仓库：已交付期数必须都盖上锚点 -----")
    unstamped = []
    for b in delivered:
        sm = os.path.join(ROOT, "episodes", b, "scene-map.json")
        if not os.path.exists(sm):
            continue          # 缺席/半截 JSON 上面已经红过，这里不重复报案
        scene = json.load(open(sm, encoding="utf-8"))
        judged, slots = SA.coverage(scene)
        if judged < slots or scene.get(SA.BODY_ROWS_KEY) is None:
            unstamped.append((b, judged, slots))
    if unstamped:
        raise SystemExit(
            "❌ 已交付期数里 " + str(len(unstamped)) + " 期的图层表没有锚点（第六族断言对它们是「判不了」）：\n   "
            + "\n   ".join(f"{b}：锚点 {j}/{s} 层槽位" for b, j, s in unstamped[:12])
            + "\n   修法：python3 scripts/stamp-scene-map-anchors.py \"<书名>\"（只加渲染器不读的锚点键，"
              "不改图层顺序；盖章只锁未来，不证明当初对齐）")
    print(f"  ✅ {len(delivered)} 期已交付者全部盖满锚点，第六族断言在真语料上有落点"
          f"（批量：python3 scripts/stamp-scene-map-anchors.py --all）")
    print("scene-map guard: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
