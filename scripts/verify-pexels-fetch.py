#!/usr/bin/env python3
"""按清单里的 Pexels photo id 重放一次取图，逐像素核对磁盘上的 _raw/ 底图。

要回答的问题只有一个：**盘上这张图，是不是清单声称的那张照片？**

这条以前查不到。check-image-provenance.py 查的是「清单 ↔ prompts.csv ↔ 文件存在」，
三份记录可以一起指着一张错的照片：《偏跟山过不去》2026-10-03 把钩子第 1 拍换成 Pexels
#38552827（德国农舍木墙长凳上的背包），清单换了、prompts.csv 换了、取图脚本打「完成 1/1」，
而 atmosphere-17.png 一个字节都没动——旧取图脚本把「同名文件已存在」当成取到了（`ok += 1; continue`）。
成片抽帧对磁盘图也查不出这件事（帧 = 磁盘图 = 那张旧照片），八道门禁全绿。
把图从目录里改名挪走，门禁也只打一行 ⚠️——那条方向 2026-10-03 才改成判红。

感知哈希能不能顶上？实测不能：dHash 在「同一张照片调色前/调色后」之间最大 31 bit，
「两张不同照片」之间最小 12 bit；换模糊+二值、模糊+相关两版描述子，两个分布完全重叠。
调色本身就是一次非线性像素运算，所以事后比像素认不出身份。

认得出的办法是**重放**：Pexels 的 CDN 直链按 id 确定、不需要 key，
`https://images.pexels.com/photos/<id>/pexels-photo-<id>.jpeg?...&w=1600` 重新下载一次，
用与取图脚本**同一份**裁切实现（scripts/lib/pexels_crop.py，连 crop_box 窗口一起照抄清单）
裁成 3:4，再和 `_raw/<name>.png` 逐像素比。调色从不写 _raw/，所以 _raw 相符就说明这张底图
确实来自那个 id；`_provenance.json` 里 fetch 当场记下的 raw_sha256 又说明 _raw 没被事后换过。
两段接起来，「盘上的文件 ↔ 清单声称的照片」这条链才闭合。

交付的 atmosphere-N.png 是调色后的有损产物，不比对它，只比对 _raw/。

用法:
  python3 scripts/verify-pexels-fetch.py <episode> [--segment hook|body|all]
需要网络（本机走代理：HTTPS_PROXY=http://127.0.0.1:1082）。缺 _raw/ 或取不到图都算未通过——
「无法证明」不能记成绿灯。
"""
import io
import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "lib"))
from pexels_crop import TARGET_H, TARGET_W, crop_3x4  # noqa: E402

MIN_SRC_W = 1600          # 与 fetch-pexels-by-id.py 同一档宽度请求
MEAN_TOL = 1.5            # 逐像素平均差：CDN 二次编码的抖动远小于 1
EDGE_TOL = 40             # 边缘振铃阈值：JPEG 重压在强对比边上会过冲
EDGE_FRAC_TOL = 0.002     # 过冲像素占比上限（0.2%）：#15245537 是「暗室 + 亮窗」，
                          # 全图平均差 0.5 却有 51 的峰值——拿峰值当判据会把一张
                          # 本来相符的图判红，而假红会把人推去换掉没有错的选型。
                          # 真的是另一张照片时，平均差是十几到几十、过冲占比是几个百分点。


def red(msg, errors):
    print(f"  ❌ {msg}")
    errors.append(msg)


def replay_width(seg_dir, name, prov_ent):
    """重放该按哪一档宽度去 CDN 取：优先取图当时写下的记录，其次 _original/ 的像素宽。

    这一档必须是「取图当时那一档」，不能固定 1600。CDN 的 w=1600 与 w=1280 是同一张主图的
    两种渲染，裁到 1024x1365 之后逐像素并不相等：《猎人笔记》2026-10-06 atmosphere-5
    (#32176786) 取图时 curl 18 降到 1280，重放按 1600 取，与盘上 _raw 平均差 5.0 峰值 71
    判成「不符」；同一张图按 1280 重放是 **平均差 0.000｜峰值 0｜过冲 0.000%**，按 1024 重放
    则是 10.80/136。三次数字放一起说明差异来自缩放档位而不是照片身份。
    假红的代价是实的：它会把人推去换掉一张没有错的选型。

    三个来源按可信度排：
      1. `_provenance.json` 的 source_width —— fetch 当场写的，是记录；
      2. 历史标记没有这一键（2026-10-06 之前取的全是这种），用 `_original/<name>.png` 的
         像素宽反推——_original 就是 fetch 落下的那份未裁切源图，宽度假设不成立时会露出来；
      3. 两者都没有 → 只能按 MIN_SRC_W 请求，并且仍然「取不到可比档位就报未能证明」。
    任何一档都必须 ≥ TARGET_W，否则裁切是放大，比出来的数字没有意义（宁可无从证明）。
    """
    w = (prov_ent or {}).get("source_width")
    if w:
        w = int(w)
        if w < TARGET_W:
            return None, f"标记记的源图宽 {w}px 小于目标宽 {TARGET_W}，裁切是放大，无从逐像素核对"
        return w, f"标记记的取图源图宽 {w}px"
    orig = os.path.join(seg_dir, "_original", f"{name}.png")
    if os.path.exists(orig):
        try:
            ow = Image.open(orig).size[0]
        except Exception:  # noqa: BLE001 - 坏文件等于没有证据，交给下面的默认档
            return MIN_SRC_W, "历史标记无宽度且 _original 读不出来，按默认档试"
        if ow >= TARGET_W:
            return ow, f"历史标记无宽度，按 _original 的 {ow}px 宽反推"
        return None, f"_original 只有 {ow}px 宽（< {TARGET_W}），裁切是放大，无从逐像素核对"
    return MIN_SRC_W, f"无宽度记录也无 _original，按默认档 w={MIN_SRC_W} 试"


def fetch_bytes(pid, name, image_url=None, want_w=MIN_SRC_W):
    """CDN 取字节：只认取图当时那一档宽度（want_w），拿不到就重试，不降档冒充。

    两个坑都实测踩过：
      1. 扩展名不能写死 .jpeg。清单里的 image_url 有 .png 的（夜晚的潜水艇 atmosphere-22 的
         #15245537 就是），拼成 .jpeg 会 404，于是重放把自己判成「取不到图」——
         门禁报假红比不报更糟，因为它会把人推去换掉本来没错的选型。取图脚本用的就是这一行
         image_url，重放必须用同一个地址。
      2. 宽度不能降档。curl 18（partial file）是这台机器的常态抖动，降到 1280/1024 之后
         裁出来的是「另一次重采样」：《偏跟山过不去》#38552827 在 w=1024 下与磁盘 _raw
         平均差 6.3、峰值 120，看着像另一张照片，其实同一张，只是 1600→1024 的差别。
         降档重试在这里是错的，但**按取图当时的档位取**不是降档——那是同一次缩放，
         档位由 replay_width() 从记录里读，不是当场试探出来的。
         所以这里对 want_w 档重试三次，仍拿不到就报「未能证明」，绝不报「不符」。
    """
    head = (image_url or "").split("?")[0] or f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg"
    url = f"{head}?auto=compress&cs=tinysrgb&w={want_w}"
    last = "没取过"
    for attempt in range(3):
        r = subprocess.run(["curl", "-sSL", "--retry", "2", "--max-time", "150", url],
                           capture_output=True)
        if r.returncode != 0 or not r.stdout:
            last = f"curl {r.returncode}"
            print(f"  {name} — 第 {attempt + 1} 次 w={want_w} 取回失败（{last}），重试")
            time.sleep(2 * (attempt + 1))
            continue
        try:
            img = Image.open(io.BytesIO(r.stdout))
            img.load()
        except Exception as exc:  # noqa: BLE001 - 截断的 JPEG 能 open 不能 load
            last = f"解码失败 {exc}"
            print(f"  {name} — 第 {attempt + 1} 次 w={want_w} {last}，重试")
            time.sleep(2 * (attempt + 1))
            continue
        # 必须拿到**同一份缩放**。宽了窄了都不是：CDN 忽略过参数、或主图本身就是那个宽度，
        # 两种情况下裁出来的像素与取图当时不是同一份，比出来的数字无法解释成「同一张/另一张」，
        # 所以只能报「未能证明」，不能报「不符」（假红会把人推去换掉没有错的选型）。
        if img.width != want_w:
            return None, f"CDN 这次给的是 {img.width}px 宽，取图当时那一份是 {want_w}px，不是同一次缩放"
        return img.convert("RGB"), want_w
    return None, last


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        sys.exit(__doc__)
    ep = args[0]
    seg = "all"
    if "--segment" in args:
        seg = args[args.index("--segment") + 1]
    ep_dir = ep if os.path.isdir(ep) else os.path.join(ROOT, "episodes", ep)
    if not os.path.isdir(ep_dir):
        sys.exit(f"episode 目录不存在: {ep_dir}")
    man_path = os.path.join(ROOT, "data", f"{os.path.basename(ep_dir)}_pexels_ids.json")
    if not os.path.exists(man_path):
        sys.exit(f"没有清单 {os.path.relpath(man_path, ROOT)}：无从核对")
    body_sub = "images-body" if os.path.isdir(os.path.join(ep_dir, "images-body")) else "images"

    errors = []
    checked = 0
    prov_cache = {}
    for item in json.load(open(man_path, encoding="utf-8")):
        want_seg = item.get("segment") or ("hook" if item.get("dir") == "images-hook" else "body")
        if seg != "all" and want_seg != seg:
            continue
        name = item["name"]
        pid = int(item["photo_id"])
        sub = item.get("dir") or ("images-hook" if want_seg == "hook" else body_sub)
        seg_dir = os.path.join(ep_dir, sub)
        raw_file = os.path.join(seg_dir, "_raw", f"{name}.png")
        if not os.path.exists(raw_file):
            red(f"{sub}/_raw/{name}.png 不在盘上，无法核对 Pexels #{pid}", errors)
            continue
        if sub not in prov_cache:
            ppath = os.path.join(seg_dir, "_provenance.json")
            try:
                prov_cache[sub] = (json.load(open(ppath, encoding="utf-8")).get("entries") or {})
            except Exception:  # noqa: BLE001 - 没有标记=历史期，回落到 _original/ 反推
                prov_cache[sub] = {}
        want_w, why_w = replay_width(seg_dir, name, prov_cache[sub].get(name))
        if want_w is None:
            red(f"{sub}/{name} 未能证明：{why_w}（这不是「图错了」，是「按取图那一份缩放没法复现」）",
                errors)
            continue
        src, w = fetch_bytes(pid, name, item.get("image_url"), want_w)
        if src is None:
            red(f"Pexels #{pid} 重放取不到可比的那一档（{w}），{sub}/{name} 未能证明"
                "——这不是「图错了」，是「今天没法证明」，网络恢复后重跑", errors)
            continue
        replay = crop_3x4(src, item.get("crop_box"))
        disk = Image.open(raw_file).convert("RGB")
        if disk.size != (TARGET_W, TARGET_H) or replay.size != disk.size:
            red(f"{sub}/_raw/{name}.png 是 {disk.size}，重放是 {replay.size}，画幅对不上", errors)
            continue
        a = np.asarray(disk, dtype=np.int16)
        b = np.asarray(replay, dtype=np.int16)
        d = np.abs(a - b)
        mean, peak = float(d.mean()), int(d.max())
        frac = float((d.max(axis=2) > EDGE_TOL).mean())
        checked += 1
        if mean <= MEAN_TOL and frac <= EDGE_FRAC_TOL:
            print(f"  ✅ {sub}/{name} = Pexels #{pid}（重放 w={w}·{why_w}，平均差 {mean:.2f}"
                  f"｜峰值 {peak}｜过冲像素 {frac * 100:.2f}%）")
        else:
            red(f"{sub}/_raw/{name}.png 与 Pexels #{pid} 的重放不符（平均差 {mean:.1f} 峰值 {peak}）："
                f"盘上这张不是清单声称的照片，或清单的 id/crop_box 记错了（清单 alt：{str(item.get('alt'))[:40]}）",
                errors)

    print(f"\n{'=' * 60}\n逐张重放核对  《{os.path.basename(ep_dir)}》 段={seg}  核了 {checked} 张"
          f"\n{'-' * 60}\n" + ("  ✅ 盘上的底图与清单声称的 Pexels 照片逐张相符" if not errors
                              else f"  ❌ {len(errors)} 张对不上——换图没真的落盘，不得交付"))
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
