#!/usr/bin/env python3
"""由 data/<书名>_pexels_ids.json + scene-map.json 生成 episodes/<书名>/prompts.csv。

为什么要有这个脚本：溯源行过去都是手写的，而 `check-image-provenance.py` 只认
「prompts.csv 里有没有 Pexels photo id / 链接 / 摄影师」，一旦手抄时把 id 少写或
错写一位（联系表上是一位一位读出来的），门禁反而查不出来——那张图确实存在、
确实来自 Pexels，只是不是选中的那一张。选型既然已经按格子编号锁进 ids.json，
溯源行就必须由同一份数据生成，不允许第二次手抄。

用法：
  python3 scripts/write-prompts-csv.py "<书名>" [--apply]
默认打印预览；--apply 写 episodes/<书名>/prompts.csv。
"""
import argparse
import csv
import io
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from episode_paths import body_images_subdir  # noqa: E402

GRADE_NOTE = ("grade (lum normalize + desaturate + vignette + top/bottom scrim) "
              "+ fix-bright-regions")
TOOL = "mq-pexels-sheet.py → lock-pexels-selection.py → fetch-pexels-by-id.py"

ap = argparse.ArgumentParser()
ap.add_argument("episode")
ap.add_argument("--manifest", help="默认 data/<书名>_pexels_ids.json")
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

ep_dir = os.path.join(ROOT, "episodes", args.episode)
manifest_path = args.manifest or os.path.join(ROOT, "data", f"{args.episode}_pexels_ids.json")
if not os.path.exists(manifest_path):
    sys.exit(f"缺少选型清单：{manifest_path}（先跑 lock-pexels-selection.py --apply）")

items = json.load(open(manifest_path, encoding="utf-8"))
brief = json.load(open(os.path.join(ep_dir, "brief.json"), encoding="utf-8"))
sm_path = os.path.join(ep_dir, "scene-map.json")
themes = {}
reuses = []
layers = []
if os.path.exists(sm_path):
    layers = json.load(open(sm_path, encoding="utf-8")).get("layers", [])
    for layer in layers:
        themes.setdefault(layer["name"], layer.get("theme", ""))
        # 回扣层（同一张照片的字节级副本）不进 lock manifest——lock 见到重复 photo id
        # 会直接退出，所以它只能靠 scene-map 的 reuse_of 指回原层。这里必须替它生成溯源行，
        # 否则 check-image-provenance.py 判「没有溯源行」，而手抄 id 又是本脚本要消灭的东西。
        if layer.get("reuse_of"):
            reuses.append((layer["name"], layer["reuse_of"]))

version = brief.get("scriptVersion", "A")
by_name = {item["name"]: item for item in items}


def source_line(item):
    # 裁切法必须写实：把画面一角的现代杂物裁在画幅外时，溯源行不能还写成居中裁切
    crop = (f"crop window x{item['crop_box'][0]:g}–{item['crop_box'][2]:g} "
            f"y{item['crop_box'][1]:g}–{item['crop_box'][3]:g} then 3:4"
            if item.get("crop_box") else "center crop")
    return (f"Pexels #{item['photo_id']} · {item.get('photographer', '')} · "
            f"{item.get('page_url') or item.get('url', '')} · "
            f"原图 {item.get('pixels', '')} → 1024x1365 (3:4) {crop}, {GRADE_NOTE}")


def mood_for(name):
    return ("竖版氛围实拍，暗调留白，承载常驻书名与白字幕" if name == "result-bridge"
            else "竖版氛围实拍，低饱和暗调、氛围优先不逐句图解，承载白字幕")


buf = io.StringIO()
w = csv.writer(buf, lineterminator="\n")
w.writerow(["version", "filename", "prompt", "tool", "source", "status"])


def rel_name(item):
    """每一行的 filename 都带上它真正所在的目录前缀（images-body/…、images-hook/…）。

    三段结构下钩子段和正文段都从 atmosphere-1 起编号，而 check-image-provenance.py 只在
    CSV 的 filename 含 "/" 时才建「目录/文件名」索引；不带前缀时它退回按 basename 匹配，
    钩子段 atmosphere-1 会被正文段同名列吃掉——门禁于是拿正文的 id 去对钩子的文件，
    对钩子段永远绿灯（《恶意》2026-10-01 之后 AGENTS.md 明写过这条，脚本一直没跟上）。
    正文段也不能再写裸名：lock-pexels-candidates.py 生成的清单里正文条目没有 dir，
    于是《嫌疑人X的献身》2026-10-04 的正文行仍然落回裸名，同名的钩子行把正文层吃掉，
    门禁报出两条假撞图。前缀由 episode_paths 的同一份口径补齐，不再依赖清单写没写 dir。
    """
    name = f"{item['name']}.png"
    sub = item.get("dir") or ("images-hook" if item.get("segment") == "hook"
                              else body_images_subdir(ep_dir))
    return f"{sub}/{name}"


for item in items:
    name = item["name"]
    # scene-map 只描述正文段的图层，而钩子段同样从 atmosphere-1 起编号。按裸文件名查 theme
    # 会把正文层的描述写进钩子行——溯源行于是描述的是另一张根本没上屏的画面，
    # 门禁只查「有没有 id / 链接 / 摄影师」，查不出描述配错了图。钩子行只认清单里的 note。
    is_hook = (item.get("dir") or ("images-hook" if item.get("segment") == "hook" else "")) == "images-hook"
    theme = (item.get("note", "") if is_hook else themes.get(name)) or item.get("note", "")
    prompt = f"{theme} / {mood_for(name)}" if theme else mood_for(name)
    w.writerow([version, rel_name(item), prompt, item.get("tool") or TOOL,
                source_line(item), "adopted"])
    print(f"  {rel_name(item):<32} Pexels #{item['photo_id']} · {item.get('photographer', '')}")

out_path = os.path.join(ep_dir, "prompts.csv")
regen_reuses = []
unresolved_reuses = []


def find_manifest_item(src_name):
    """reuse_of 允许两种写法：裸层名，或「images-body/atmosphere-2」这样的目录限定名。

    只写裸名有歧义：钩子段和正文段都从 atmosphere-1 起编号，by_name 按 name 索引时
    后一条会覆盖前一条，回扣层就可能解析到**另一段**那张完全不同的照片上
    （《乞力马扎罗的雪》2026-10-07：正文回扣 atmosphere-2 解析到了钩子的 #5994745）。
    目录限定名优先；裸名保持原语义，兼容历史 scene-map。
    """
    if "/" in src_name:
        want_dir, bare = src_name.split("/", 1)
        for item in items:
            item_dir = item.get("dir") or (
                "images-hook" if item.get("segment") == "hook" else body_images_subdir(ep_dir))
            if item["name"] == bare and item_dir == want_dir:
                return item
        return None
    return by_name.get(src_name)


for name, src_name in reuses:
    src = find_manifest_item(src_name)
    if not src:
        # 清单只覆盖一段时（回补钩子段），指向正文层的回扣层这次无法重新生成。老流程落的盘
        # 根本没有正文清单（《对我无害之人》2026-10-02：21 张正文图只活在 prompts.csv 里），
        # 在这里 exit 等于「清单不全就不许补钩子」，而 AGENTS.md 要求两段都有溯源行。
        # 所以这次跳过它，交给下面的原样保留逻辑；真的连旧行都没有时再报错。
        unresolved_reuses.append(name)
        continue
    theme = themes.get(name, "")
    prompt = f"{theme} / {mood_for(name)}（回扣 {src_name}，同一张照片的字节级副本）" if theme else mood_for(name)
    # filename 带真实目录前缀，与 rel_name 同一口径：回扣层名（atmosphere-14…）虽然目前
    # 跨段唯一，但裸名行是 rel_name 的 docstring 里写明要消灭的形状。
    reuse_rel = f"{body_images_subdir(ep_dir)}/{name}.png"
    w.writerow([version, reuse_rel, prompt, TOOL,
                source_line(src) + f" · 复用自 {src_name}.png", "adopted"])
    regen_reuses.append(name)
    print(f"  {reuse_rel:<32} Pexels #{src['photo_id']} · 复用自 {src_name}")

# 清单只覆盖一段时，绝不能把另一段的溯源行抹掉。
# 2026-10-02 回补《秋园》钩子段时踩到的形状：data/秋园_pexels_ids.json 里只有钩子那两张
# （正文 23 张是改名前的老流程落的盘，没有清单），本脚本按 `"w"` 整表重写，
# 一次 --apply 就把 23 行正文溯源抹成 2 行——check-image-provenance.py 随即判整期红，
# 而红的原因是「你刚把它的历史删了」，不是图有问题。所以：本次没有重新生成的行一律原样保留，
# 只有当它指向的文件确实不在盘上时才丢（那才是真的陈旧行）。
new_rel = {rel_name(item) for item in items} | {
    f"{body_images_subdir(ep_dir)}/{n}.png" for n in regen_reuses}
kept, dropped = [], []
if os.path.exists(out_path):
    with open(out_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("filename") or row.get("file") or "").strip()
            if not raw:
                continue
            norm = raw.replace("\\", "/").lstrip("./")
            if norm in new_rel:
                continue          # 这一行本次由清单重新生成，用新行
            exists = any(os.path.exists(os.path.join(ep_dir, sub, os.path.basename(norm)))
                         for sub in ("images", "images-body", "images-hook"))
            (kept if exists else dropped).append(row)

if unresolved_reuses:
    have = {(r.get("filename") or r.get("file") or "").strip()
            .replace("\\", "/").lstrip("./") for r in kept}
    body_sub = body_images_subdir(ep_dir)
    lost = [n for n in unresolved_reuses
            if f"{n}.png" not in have and f"{body_sub}/{n}.png" not in have]
    if lost:
        sys.exit(f"回扣层 {'、'.join(lost)} 的 reuse_of 指向清单外的层，"
                 "prompts.csv 里也没有可保留的旧行——先把正文选型锁进 "
                 "data/<书名>_pexels_ids.json 再跑")
    print(f"  （回扣层 {'、'.join(unresolved_reuses)} 沿用 prompts.csv 里的旧溯源行）")

rows_text = io.StringIO()
w2 = csv.writer(rows_text, lineterminator="\n")
w2.writerow(["version", "filename", "prompt", "tool", "source", "status"])
for row in kept:
    w2.writerow([row.get("version", version), row.get("filename") or row.get("file"),
                 row.get("prompt", ""), row.get("tool", ""), row.get("source", ""),
                 row.get("status", "")])
new_only = buf.getvalue().split("\n", 1)[1]
rows_text.write(new_only)
if kept:
    print(f"  （保留原有 {len(kept)} 行溯源：本次清单没有覆盖它们）")
if dropped:
    print(f"  （丢弃 {len(dropped)} 行陈旧溯源，指向的文件已不在盘上："
          f"{'、'.join((d.get('filename') or '') for d in dropped[:5])}）")

if args.apply:
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(rows_text.getvalue())
    print(f"\nwrote {out_path}")
else:
    print("\n[dry-run] 未写盘，加 --apply")
