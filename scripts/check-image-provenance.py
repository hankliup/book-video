#!/usr/bin/env python3
"""背景图溯源与可用性门禁：把「背景图来自 Pexels」这条规则变成可执行检查。

规则本体见 AGENTS.md「Visual And Audio Rules」与 docs/book-video-playbook.md 步骤 2/5。
本脚本只读，不改任何文件；改图的是 fetch-pexels-by-id.py / grade-atmosphere-images.py /
fix-bright-regions.py。

为什么需要：图片从 AI 出图换成 Pexels 实拍之后，成片质量取决于三件容易忘的事——
  1. 每张上屏的图都能追到一张 Pexels 照片（photo id，最好带摄影师与链接），
     否则日后无法原样复现、无法换图（《霍比特人》图片被误删后只能靠 prompts.csv 的 ID 恢复）；
  2. 图必须是 3:4 且不低于 720x960。用 Pexels 预置的 portrait 只有 810px 宽，
     或裁成 2:3 直接进 720x960 画幅，慢推全程都在放大，成片会发糊或变形；
  3. 图必须过一遍统一调色（_raw/ 备份 + 书名区/字幕区亮度达标），
     否则切镜跳色，白字被亮图吃掉。
这三件事靠人记一定会漏，所以做成门禁。

严格度分三档，历史剧集不会把 npm run check 卡死：
  - pexels：brief.json 里 image_pipeline == "pexels-1024"（本期起的新剧都必须是这档）。
    全量检查：溯源 photo id + 链接/摄影师 + 3:4 且 ≥1024 宽 + _raw/ 调色备份 + 书名/字幕区亮度。
  - pexels-legacy：prompts.csv 半数以上条目引用了 Pexels photo id，但没声明新管线。
    只检查溯源与图层结构；画幅不合规只告警（这批图是 720x960/1080x1440 的旧管线）。
  - compat：更早的 AI 出图剧集，只检查 scene-map 图层能否落到真实文件。

用法:
  python3 scripts/check-image-provenance.py <episode-dir-or-name>
  python3 scripts/check-image-provenance.py --all
"""
import csv
import glob
import hashlib
import json
import os
import re
import sys

try:
    from PIL import Image
except ImportError:  # Pillow 不在时不阻塞 npm run check，只报告
    print("⚠️ 未安装 Pillow，跳过背景图门禁（结构检查仍会跑）")
    Image = None

import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

# 与调色脚本共用同一套阈值/命名口径，避免门禁和实际调色各说各话
_spec = importlib.util.spec_from_file_location("grade_mod", os.path.join(HERE, "grade-atmosphere-images.py"))
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

# 「这张 Pexels 照片在别的期是不是已经上过屏」只有全仓库索引才看得见，
# 而这份索引挑图脚本、锁脚本、门禁必须共用同一份（口径抄第二遍就一定漂移）。
_ispec = importlib.util.spec_from_file_location("pexels_index", os.path.join(HERE, "lib", "pexels_index.py"))
PIX = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(PIX)

CANVAS_W, CANVAS_H = 720, 960      # 渲染帧：低于这个尺寸就是上采样
TARGET_W, TARGET_H = 1024, 1365    # 新管线落盘尺寸（fetch-pexels-by-id.py）
PEXELS_ID = re.compile(r"Pexels\s*(?:photo)?\s*#?\s*(\d{4,})", re.I)
PEXELS_URL = re.compile(r"pexels\.com/", re.I)
NEW_PIPELINE = "pexels-1024"


def episode_dir_of(target):
    return target if os.path.isdir(target) else os.path.join(ROOT, "episodes", target)


def shipped_images(images_dir):
    if not os.path.isdir(images_dir):
        return []
    return sorted(
        (f for f in os.listdir(images_dir)
         if G._is_atmosphere(f) and os.path.isfile(os.path.join(images_dir, f))),
        key=G._img_key,
    )


def read_prompts(ep_dir):
    """prompts.csv 的列名历史上不统一：filename / file / role 都出现过，逐个兜底。

    返回 (by_file, by_rel)。by_file 按文件名兜底（历史剧集只有 atmosphere-N.png）；
    by_rel 按 CSV 里写的相对路径建索引（images/atmosphere-1.png、images-hook/atmosphere-1.png）。
    三段结构下钩子段和正文段的文件名会撞（两段都从 atmosphere-1 起编号），
    只按文件名索引会让钩子段的溯源被正文段的同名列吃掉——门禁于是对钩子段永远绿灯。
    """
    path = os.path.join(ep_dir, "prompts.csv")
    if not os.path.exists(path):
        return {}, {}
    by_file, by_rel = {}, {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig", newline="")):
        name = (r.get("filename") or r.get("file") or "").strip()
        if not name and r.get("role"):
            name = f"{r['role'].strip()}.png"
        if not name:
            continue
        norm = name.replace("\\", "/").lstrip("./")
        by_file.setdefault(os.path.basename(norm), r)
        if "/" in norm:
            by_rel.setdefault(norm, r)
    return by_file, by_rel


def prompt_row(by_file, by_rel, subdir, fname):
    """先按「目录/文件名」精确取溯源行，取不到再退回文件名（历史剧集没有目录前缀）。"""
    return by_rel.get(f"{subdir}/{fname}") or by_file.get(fname)


def script_max_order(ep_dir):
    # 三段结构下正文真源是 script-body.csv；历史单段期只有 script-body.csv，逐个兜底，
    # 否则 limit 拿到 None，下面的「scene-map 是否覆盖全部行」会静默不判。
    orders = []
    for name in ("script-body.csv",):
        path = os.path.join(ep_dir, name)
        if not os.path.exists(path):
            continue
        for row in csv.DictReader(open(path, encoding="utf-8-sig")):
            try:
                orders.append(int(row["order"]))
            except (KeyError, TypeError, ValueError):
                continue
        if orders:
            break
    return max(orders) if orders else None


def check_structure(ep_dir, images, sink, warnings):
    """scene-map 图层必须都能落到真实文件，且行号覆盖 script-body.csv 全部行。

    sink 由调用方决定：新管线剧集传 errors（红灯），历史/在制的剧集传 warnings，
    免得别的会话刚建好 scene-map、图还没落盘时把整个仓库的 npm run check 卡住。
    """
    sm_path = os.path.join(ep_dir, "scene-map.json")
    if not os.path.exists(sm_path):
        warnings.append("没有 scene-map.json（旧版渲染路径），跳过图层结构检查")
        return
    layers = (json.load(open(sm_path, encoding="utf-8")).get("layers")) or []
    names = {f"{c['name']}.png" for c in layers}
    for c in layers:
        if f"{c['name']}.png" not in images:
            sink.append(f"scene-map 图层 {c['name']} 在 images/ 里没有对应文件")
    for i in range(1, len(layers)):
        if layers[i]["name"] == layers[i - 1]["name"]:
            sink.append(f"scene-map 相邻两层同图：{layers[i]['name']}（切点会没有变化）")
    limit = script_max_order(ep_dir)
    if limit:
        covered = {o for c in layers for o in range(int(c["order_start"]), int(c["order_end"]) + 1)}
        missing = sorted(set(range(1, limit + 1)) - covered)
        if missing:
            sink.append(f"scene-map 未覆盖 script-body.csv 行：{missing[:8]}{'...' if len(missing) > 8 else ''}")
    unused = [f for f in images if f not in names]
    if unused:
        warnings.append(f"images/ 里有未被 scene-map 引用的图：{', '.join(unused[:5])}")


def geometry_issue(path):
    """返回 (问题, 仅告警)。

    渲染器按 cover 缩放 + 居中裁切（render_scene_map_template_fallback.py 的
    covers[name] = max(W/iw, H/ih)），所以比例偏离 3:4 只是裁掉内容，不是硬伤；
    真正会毁掉成片的是**放大**：宽或高低于 720x960 画布时，慢推全程都在上采样，成片发糊。
    """
    with Image.open(path) as img:
        w, h = img.size
    if w < CANVAS_W or h < CANVAS_H:
        return f"是 {w}x{h}，低于 720x960 画布，渲染时在上采样（成片会发糊）", False
    if abs(w / h - CANVAS_W / CANVAS_H) / (CANVAS_W / CANVAS_H) > 0.05:
        return f"是 {w}x{h}，偏离 3:4 画幅，cover 裁切会丢掉约 {abs(1 - (w/h)/(CANVAS_W/CANVAS_H))*100:.0f}% 高度", True
    if w < TARGET_W:
        return f"是 {w}x{h}，低于新管线的 {TARGET_W}x{TARGET_H}", True
    return None, False


def check_pixels(ep_dir, images, errors, warnings, subdir="images"):
    raw_dir = os.path.join(ep_dir, subdir, "_raw")
    for f in images:
        path = os.path.join(ep_dir, subdir, f)
        with Image.open(path) as pil:
            w, h = pil.size
        if (w, h) != (TARGET_W, TARGET_H):
            errors.append(f"{f} 是 {w}x{h}，新管线应为 {TARGET_W}x{TARGET_H}")
        if not os.path.exists(os.path.join(raw_dir, f)):
            errors.append(f"{f} 没有 _raw/ 备份，说明没跑过 grade-atmosphere-images.py")
            continue
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            title = G.luminance(rgb.crop((0, 0, w, int(h * 0.20))))
            cap = G.luminance(rgb.crop((0, int(h * 0.70), w, int(h * 0.92))))
        if title > G.REGION_WARN:
            errors.append(f"{f} 书名区亮度 {title:.1f} > {G.REGION_WARN}，白字会被吃掉（跑 fix-bright-regions.py）")
        if cap > G.REGION_WARN:
            errors.append(f"{f} 字幕区亮度 {cap:.1f} > {G.REGION_WARN}，白字会被吃掉（跑 fix-bright-regions.py）")


def body_images_subdir(ep_dir):
    """正文段氛围图目录：三段结构改名后叫 images-body/，历史剧集只有 images/。

    两个名字都认，优先新名。写死一个就会在改名当天出现两种坏结果之一：
    门禁对着不存在的 images/ 得到「本期没有图」而静默跳过，或把整期图判成脱管。
    """
    return "images-body" if os.path.isdir(os.path.join(ep_dir, "images-body")) else "images"


PROV_NAME = "_provenance.json"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_certification(ep_dir, want_dir, name, item, errors, warnings, mode):
    """盘上这张文件到底是不是清单声称的那张 Pexels 照片。

    上面那一圈查的是「清单 ↔ prompts.csv ↔ 文件存在」，三样都对得上也可能一张都不是那张照片：
    《偏跟山过不去》2026-10-03 把钩子第 1 拍从跨期撞车的 Pexels #17941302 换成 #38552827，
    清单换了、prompts.csv 换了、`fetch-pexels-by-id.py` 打「完成 1/1」，而盘上的
    atmosphere-17.png 一个字节都没动——旧脚本把「同名文件已存在」当成取到了（`ok += 1; continue`），
    换图从未发生。把成片抽帧和磁盘图对比也查不出来：帧确实等于磁盘图，磁盘图确实是旧照片，
    九道门禁里没有任何一道看过「文件的出身」。
    感知哈希能不能顶上？实测不能。dHash 在「同一张照片调色前/调色后」之间最大 31 bit，
    而「两张不同照片」之间最小 12 bit；换用模糊+二值、模糊+相关两版描述子，两个分布完全重叠。
    调色本身就是一次非线性像素运算，所以身份只能记在取图当时：fetch 写 _provenance.json
    （photo_id + 那次写进 _raw/ 的未调色底图 sha256），这里回读核对。调色从不写 _raw/，
    因此 sha256 相符 = 底图还是当年按这个 id 拉回来的那一张。
    只对有标记文件的目录生效：历史剧集（100+ 期）从没写过标记，一道把考古工程变成红的门禁
    不会被任何人执行；新取图必然写标记，所以此后每一次换图都自动落到这道门里。
    """
    prov_file = os.path.join(ep_dir, want_dir, PROV_NAME)
    if not os.path.exists(prov_file):
        if mode == "pexels" and want_dir == "images-hook":
            # 钩子段是新回补的，正常路径必然经过 fetch，没有标记就意味着图是拷来的或手工放的。
            warnings.append(
                f"{want_dir}/{name} 没有 {PROV_NAME}：盘上这张文件是不是 Pexels #{item['photo_id']} 无从证明")
        return
    try:
        prov = json.load(open(prov_file, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 坏标记等于没有出身，新管线直接判红
        errors.append(f"{want_dir}/{PROV_NAME} 读不出来（{exc}）")
        return
    stem = name[:-4] if name.endswith(".png") else name
    ent = (prov.get("entries") or {}).get(stem)
    if not ent:
        errors.append(f"{want_dir}/{name} 在这个目录的 {PROV_NAME} 里没有条目："
                      f"清单说它是 Pexels #{item['photo_id']}，但取图脚本从未认证过这个文件")
        return
    if int(ent.get("photo_id") or -1) != int(item["photo_id"]):
        errors.append(
            f"{want_dir}/{name} 盘上文件是按 Pexels #{ent['photo_id']} 取的，"
            f"清单/prompts 却声称 #{item['photo_id']}——换图没真的落盘（fetch 的同名跳过旧 bug），"
            "重跑 fetch-pexels-by-id.py 或加 FORCE=1")
        return
    raw_file = os.path.join(ep_dir, want_dir, "_raw", name)
    if not os.path.exists(raw_file):
        errors.append(f"{want_dir}/{name} 有 {PROV_NAME} 但 _raw/ 底图不见了，无法核对出身")
        return
    if ent.get("raw_sha256") and _sha256(raw_file) != ent["raw_sha256"]:
        errors.append(
            f"{want_dir}/_raw/{name} 与 {PROV_NAME} 记的 sha256 不符："
            f"底图被别的取图或手改换过（grade-atmosphere-images.py --restore 会把这张换进来的图"
            "直接拷回成片底图），重跑 fetch-pexels-by-id.py")


def check_episode(target):
    ep_dir = episode_dir_of(target)
    if not os.path.isdir(ep_dir):
        return [f"episode 目录不存在：{ep_dir}"], [], "compat"
    errors, warnings = [], []
    body_sub = body_images_subdir(ep_dir)
    images = shipped_images(os.path.join(ep_dir, body_sub))
    # 三段结构：钩子段自带氛围图（images-hook/），它们同样上屏，同样要有 Pexels 溯源、
    # 同样不能低于画幅、同样得过调色。只查正文段会让钩子段整段游离在门禁之外。
    hook_images = shipped_images(os.path.join(ep_dir, "images-hook"))
    if not images and not hook_images:
        return errors, [f"没有 {body_sub}/ 氛围图（本期尚未出图），跳过"], "compat"

    by_file, by_rel = read_prompts(ep_dir)
    shipped = [(body_sub, f) for f in images] + [("images-hook", f) for f in hook_images]
    pexels_rows = sum(
        1 for sub, f in shipped
        if PEXELS_ID.search(
            f"{(prompt_row(by_file, by_rel, sub, f) or {}).get('source') or ''} "
            f"{(prompt_row(by_file, by_rel, sub, f) or {}).get('tool') or ''}")
    )
    brief_path = os.path.join(ep_dir, "brief.json")
    brief = json.load(open(brief_path, encoding="utf-8")) if os.path.exists(brief_path) else {}
    mode = ("pexels" if brief.get("image_pipeline") == NEW_PIPELINE
            else "pexels-legacy" if pexels_rows >= max(1, len(shipped) // 2)
            else "compat")

    # scene-map 描述的是正文段的图层，钩子段不进图层检查。
    check_structure(ep_dir, images, errors if mode == "pexels" else warnings, warnings)

    if Image is None:
        warnings.append("Pillow 缺失：画幅/亮度检查未执行")
    elif mode == "pexels":
        check_pixels(ep_dir, images, errors, warnings, subdir=body_sub)
        check_pixels(ep_dir, hook_images, errors, warnings, subdir="images-hook")
    else:
        # 历史剧集的图有 720x960 / 1600x2400 等旧口径，只告警不回改
        for subdir in (body_sub, "images-hook"):
            for f in shipped_images(os.path.join(ep_dir, subdir)):
                issue, soft = geometry_issue(os.path.join(ep_dir, subdir, f))
                if not issue:
                    continue
                warnings.append(f"{f} {issue}")

    if mode in ("pexels", "pexels-legacy"):
        # 只有声明了新管线的剧集才红灯；历史剧集缺溯源只告警，避免把 npm run check 变成考古工程
        sink = errors if mode == "pexels" else warnings
        for sub, f in shipped:
            row = prompt_row(by_file, by_rel, sub, f)
            if row is None:
                sink.append(f"{sub}/{f} 在 prompts.csv 里没有溯源行")
                continue
            blob = f"{row.get('source') or ''} {row.get('prompt') or ''} {row.get('tool') or ''}"
            if not PEXELS_ID.search(blob):
                sink.append(f"{sub}/{f} 的 prompts.csv 溯源没有 Pexels photo id")
            elif mode == "pexels" and not (PEXELS_URL.search(blob) or re.search(r"#\d+\s*\(([^)]+)\)", blob)
                                           or re.search(r"#\d+\s*·\s*([^·]+)", blob)):
                errors.append(f"{sub}/{f} 有 photo id 但既没有 pexels.com 链接也没有摄影师名")
        manifest = os.path.join(ROOT, "data", f"{os.path.basename(ep_dir)}_pexels_ids.json")
        if os.path.exists(manifest):
            shipped_set = set(shipped)
            for item in json.load(open(manifest, encoding="utf-8")):
                name = f"{item['name']}.png"
                # 清单用 dir 指明所属段（钩子段的图在 images-hook/）；segment=hook 是同义写法。
                # 两段都从 atmosphere-1 起编号，不指明目录就会拿正文段的溯源去对钩子段的 id。
                want_dir = item.get("dir") or (
                    "images-hook" if item.get("segment") == "hook" else body_sub)
                if (want_dir, name) not in shipped_set:
                    # 清单写了、盘上没有 = 取图那一步被跳过了。这条以前只告警，于是整个
                    # 「清单 ↔ 盘」方向是空跑的：2026-10-03 把《海鸥》images-hook/
                    # atmosphere-16.png 临时改名，门禁仍然打 ✅ 只留一行 ⚠️——钩子段少一张图
                    # 会直接掉一拍，而钩子只有两拍。新管线剧集判红；历史剧集（pexels-legacy）
                    # 的清单本来就不全，继续只告警，免得把门禁变成考古工程。
                    (errors if mode == "pexels" else warnings).append(
                        f"清单里的 {want_dir}/{name} 没有落盘（fetch-pexels-by-id.py 没跑或被删）")
                    continue
                found = PEXELS_ID.search(
                    (prompt_row(by_file, by_rel, want_dir, name) or {}).get("source", ""))
                if found and int(found.group(1)) != int(item["photo_id"]):
                    sink.append(f"{want_dir}/{name} prompts.csv 记的是 #{found.group(1)}，"
                                f"与清单 #{item['photo_id']} 不一致")
                check_certification(ep_dir, want_dir, name, item, errors, warnings, mode)

        # 钩子段与正文段不许共用同一张照片。两段各自从 atmosphere-1 起编号、各自有一份溯源行，
        # 所以「每张图都有溯源、每张图都达标」全都查不出这种重复。《秋园》2026-10-02 回补钩子段时，
        # 第 1 拍锁到的 #34938048 与正文 atmosphere-3 是同一张照片（_original 字节级相同），
        # 是在全尺寸复核里撞见的：一条 7 秒的钩子把四分钟片子里已经出现过的一帧又放了一遍，
        # 观众读到的是剪辑失误。真正要呼应得走 scene-map 的 reuse_of——那是正文层之间的显式引用，
        # 而且回扣的是刚出现过的那一帧，不是随手撞上的另一段。
        ids_by_seg = {}
        for sub, f in shipped:
            row = prompt_row(by_file, by_rel, sub, f) or {}
            found = PEXELS_ID.search(row.get("source") or "")
            if found:
                key = "hook" if sub == "images-hook" else "body"
                ids_by_seg.setdefault(key, {}).setdefault(int(found.group(1)), []).append(f)
        shared = sorted(set(ids_by_seg.get("body", {})) & set(ids_by_seg.get("hook", {})))
        for pid in shared:
            errors.append(
                f"钩子段与正文段用了同一张 Pexels 照片 #{pid}："
                f"正文 {'/'.join(ids_by_seg['body'][pid])} ↔ 钩子 {'/'.join(ids_by_seg['hook'][pid])}"
                "（钩子段换一张，别复用正文已经出现过的那帧）")

        # 同一件事的跨期版本：钩子段复用了**另一期**正文早就放过的那一帧。
        # 上面那条查不到它，因为另一期的记录不在本期目录里；AGENTS.md 也因此把它划在单期门禁之外，
        # 让挑图脚本自己在锁之前 diff 全仓库清单。那条 diff 只活在脚本里（tmp/hook-b4/write-picks.py
        # 靠它拒过三张），交付侧一直没有同一份口径：2026-10-03 回补 17 期钩子段那批里就有 10 期
        # 11 张钩子图是别的期正文上过的照片，八道门全绿，直到把 111 份 prompts.csv 连成
        # scripts/lib/pexels_index.py 才第一次被查出来。观众换期看到的是同一个「新地点」，
        # 一帧旧照片放在钩子里，读起来就是本期在放上一期的片尾彩蛋。
        own = os.path.basename(ep_dir)
        index = PIX.build(ROOT)
        for pid in sorted(ids_by_seg.get("hook", {})):
            hits = PIX.foreign_uses(index, pid, own)
            if hits:
                errors.append(
                    f"钩子段 {ids_by_seg['hook'][pid][0]} 的 Pexels #{pid} 已经上过别的期："
                    f"{PIX.describe(hits)}（钩子段换一张本期与别期都没用过的照片）")
    else:
        warnings.append(f"兼容模式：{pexels_rows}/{len(shipped)} 张图有 Pexels 溯源（历史 AI 出图剧集）")
    if mode == "pexels-legacy":
        warnings.append(f"pexels-legacy：brief.json 未声明 image_pipeline={NEW_PIPELINE}，只查溯源与结构")
    return errors, warnings, mode


def main():
    args = [a for a in sys.argv[1:] if a.startswith("-")]
    targets = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--all" in args:
        targets = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "episodes", "*")) if os.path.isdir(p))
    if not targets:
        sys.exit(__doc__)
    failed = 0
    for t in targets:
        errors, warnings, mode = check_episode(t)
        print(f"== {t} [{mode}] {'✅' if not errors else '❌'}")
        for e in errors:
            print(f"   ❌ {e}")
        for w in warnings:
            print(f"   ⚠️ {w}")
        failed += bool(errors)
    if failed:
        print(f"\n{failed} 期未通过背景图门禁")
        sys.exit(1)
    print("\n背景图溯源门禁：全部通过")


if __name__ == "__main__":
    main()
