#!/usr/bin/env python3
"""渲前闸：本期要走共享兜底渲染器，scene-map.json 必须在，且必须过渲染器自己那几条前置断言。

为什么需要（2026-10-11《少年维特的烦恼》）：钩子段和片头都渲完了（约十分钟、8.8MB + 2.5MB 像素），
走到正文合成那一行才 `FileNotFoundError: episodes/少年维特的烦恼/scene-map.json`。
它是全库 158 期里唯一没有这份文件的一期——正文当年由
`scripts/archive/legacy-renderers/render_werther_template_fallback.py` 渲染，图层—行文配对写死在
那个脚本的 SCENE_RUNS 里，从来没有落成 scene-map.json。共享链接上之后它照旧能过轴闸、照旧能渲
钩子，只在最后一步炸。十分钟的机器时间和一次「本期到底停在哪」的误判，本来可以用一次 stat 换掉。

它和 `check-image-provenance.py` 的图层结构检查不是同一件事：那边对「没有 scene-map.json」写的是
⚠️ 跳过（旧版渲染路径的期数不该被卡住），判据是「有文件才检查文件」；这里判的是「这条链已经没有
旧版渲染路径了」——`render-hook-batch.sh` 只会调 `render_scene_map_template_fallback.py`，
没有 scene-map.json 的期数不是「旧版」，是渲不出来。所以缺席在这里是红，不是警告。

断言逐条对齐渲染器（scripts/render_scene_map_template_fallback.py 读 scene-map 那一段），
不在这里发明第四条口径：
  1. 文件在，layers 非空
  2. 每层 order_start/order_end 落在 1..script-body.csv 行数之间
  3. 行号并集 == 1..N（缝隙会在成片里露白）
  4. 相邻两层不得同图（切点会没有变化）
  5. 每层都能落到 images-body/（历史期数为 images/）里的真实文件
  6. 图层写了锚点（anchor_start/anchor_end + 顶层 body_rows）的话，锚点必须还等于它指的那一行
     —— 2026-10-14《源泉》：文案把第 78/79 行并成一行，末层 order_end 从 95 改成 94 之后 1..5
     全绿，中间每个切点都往回错了一行。行号是整数，它对着的文字变了它不报错，只有钉住当时
     那段文字才判得出来。判据本体在 scripts/lib/scene_map_anchors.py（盖章工具与 build-scene-map
     共用同一份），这里只接线；**没有锚点的期数不判**（判不了＝没测，不是绿），逐期打印锚点覆盖。
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from episode_paths import body_images_dir  # noqa: E402
import scene_map_anchors as SA  # noqa: E402


def check_episode(ep_name, root=ROOT):
    """返回本期所有问题（空列表 = 可以渲）。"""
    ep = os.path.join(root, "episodes", ep_name)
    problems = []
    body_csv = os.path.join(ep, "script-body.csv")
    if not os.path.exists(body_csv):
        return [f"没有 script-body.csv，行号没有对照物（{ep_name}）"]
    rows = SA.read_body_rows(ep)
    orders = sorted(rows)
    if not orders:
        return [f"script-body.csv 是空的（{ep_name}）"]
    max_order = max(orders)

    sm_path = os.path.join(ep, "scene-map.json")
    if not os.path.exists(sm_path):
        return [
            f"缺 scene-map.json：正文合成会在渲完钩子和片头之后才炸（《少年维特的烦恼》2026-10-11）。"
            f"修法二选一——① 本期正文当年走的是专属兜底渲染器：把它的 SCENE_RUNS 逐行转成 "
            f"episodes/{ep_name}/scene-map.json 的 layers（配对别重排，否则已交付的正文会改样）；"
            f"② 没有专属脚本时跑 python3 scripts/build-scene-map.py \"{ep_name}\" 再人工填 theme。"
        ]
    try:
        scene = json.load(open(sm_path, encoding="utf-8"))
    except Exception as exc:  # 半截 JSON 在这里和缺席同罪
        return [f"scene-map.json 读不出来：{exc}"]
    layers = scene.get("layers") or []
    if not layers:
        return ["scene-map.json 里 layers 是空的"]

    names = [L.get("name") for L in layers]
    for L in layers:
        try:
            a, b = int(L["order_start"]), int(L["order_end"])
        except Exception:
            problems.append(f"图层 {L.get('name')} 没有可用的 order_start/order_end")
            continue
        if not (1 <= a <= b <= max_order):
            problems.append(
                f"scene-map 行号越界：{L.get('name')} {a}-{b}（script-body.csv 共 {max_order} 行）")
    covered = set()
    for L in layers:
        try:
            covered |= set(range(int(L["order_start"]), int(L["order_end"]) + 1))
        except Exception:
            pass
    missing = sorted(set(range(1, max_order + 1)) - covered)
    if missing:
        problems.append(f"scene-map 未覆盖 script-body.csv 行：{missing[:8]}"
                        f"{'...' if len(missing) > 8 else ''}")
    for i in range(1, len(layers)):
        if names[i] == names[i - 1]:
            problems.append(f"scene-map 相邻两层同图：{names[i]}（切点会没有变化）")
    img_dir = body_images_dir(ep)
    for n in dict.fromkeys(names):
        if not os.path.exists(os.path.join(img_dir, f"{n}.png")):
            problems.append(f"scene-map 图层 {n} 在 {os.path.relpath(img_dir, root)} 里没有对应文件")
    anchor_problems, _ = SA.judge(scene, SA.anchor_rows(ep)[0])
    problems.extend(anchor_problems)
    return problems


def anchor_line(ep_name, root=ROOT):
    """本期锚点覆盖的一行话。判不了必须说出来，不许和「锚点全对」共用一个 ✅。"""
    sm_path = os.path.join(root, "episodes", ep_name, "scene-map.json")
    if not os.path.exists(sm_path):
        return "锚点：无从判（没有 scene-map.json）"
    try:
        scene = json.load(open(sm_path, encoding="utf-8"))
    except Exception:
        return "锚点：无从判（scene-map.json 读不出来）"
    judged, slots = SA.coverage(scene)
    version = SA.anchor_rows(os.path.join(root, "episodes", ep_name))[1]
    if not SA.has_anchors(scene):
        return (f"⚠️ 锚点 0/{slots} 层（对照版 {version}）：这一期的图层行号**没有行文对照物**，"
                f"合并/删行造成的整体错位判不了（判不了＝没测，不是绿）")
    if judged < slots:
        return (f"⚠️ 锚点 {judged}/{slots} 层（对照版 {version}）：只有盖了锚点的切点判得到，"
                f"缺的那些等于没判")
    return f"✅ 锚点 {judged}/{slots} 层全覆盖（对照版 {version}）"


def main(argv):
    books = [a for a in argv[1:] if not a.startswith("--")]
    if "--all" in argv:
        ep_root = os.path.join(ROOT, "episodes")
        books = sorted(d for d in os.listdir(ep_root)
                       if os.path.exists(os.path.join(ep_root, d, "scene-map.json")))
    if not books:
        print(__doc__)
        return 2
    bad = 0
    unanchored = []
    for b in books:
        problems = check_episode(b)
        line = anchor_line(b)
        if "⚠️ 锚点 0/" in line:
            unanchored.append(b)
        if problems:
            bad += 1
            print(f"[scene-map 闸] ❌ {b}")
            for p in problems:
                print(f"    · {p}")
        else:
            print(f"[scene-map 闸] ✅ {b} ｜ {line}")
    if "--all" in argv:
        n = len(books)
        print(f"[scene-map 闸] 锚点覆盖：{n - len(unanchored)}/{n} 期有锚点可判；"
              f"无锚点的 {len(unanchored)} 期判不了（判不了＝没测，不是绿）"
              + (f"：{'、'.join(unanchored[:6])}{'…' if len(unanchored) > 6 else ''}"
                 if unanchored else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
