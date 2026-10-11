#!/usr/bin/env python3
"""按 Pexels 照片 ID 精确取回氛围图（复刻已采纳画面，不做重新检索）。

为什么需要：fetch-pexels-hd.py 按关键词检索，同一关键词每次返回的照片可能不同。
当剧集 prompts.csv 已经记录了「已采纳」的具体照片 ID（例如《霍比特人》图片被误删后
需要原样恢复）时，用本脚本按 ID 直接拉原图，保证与既往选型、ts-image-map.json 的
场景描述完全一致。

沿用 fetch-pexels-hd.py 的高清取法（photo.src.original 绕开 portrait/large2x 的限宽），
但按 **宽度** MIN_SRC_W 请求：竖构图照片常见 1:2 以上的超长比例，按高度 2000 请求时
宽度只有 ~920px，居中裁 3:4 后反而要放大到 1024 会发虚；按宽度请求可保证裁切后仍有余量。

用法:
  python3 scripts/fetch-pexels-by-id.py <episode-dir-or-name> <ids.json>

ids.json:
  [{"name": "atmosphere-1", "photo_id": 3408552}, ...]

依赖: Pillow；PEXELS_API_KEY 只从仓库 .env 读取，既不进了本脚本的命令行参数，
也不进 curl 的 argv（鉴权头走 stdin 配置，见 scripts/lib/pexels_curl.py）。
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from episode_paths import body_images_dir, segment_subdir  # noqa: E402
from pexels_crop import TARGET_H, TARGET_W, crop_3x4  # noqa: E402
import pexels_curl  # noqa: E402

MIN_SRC_W = 1600  # 按宽度取图：超长竖图按高度请求会被压到 ~920px 宽，裁完还要放大
FORCE = bool(os.environ.get("FORCE"))

# 每个图片目录一份 _provenance.json：记下这张文件是哪次按哪个 photo_id 取回来的，
# 以及它 _raw/ 未调色底图的 sha256。为什么必须有：本脚本以前把「文件已存在」直接当成
# 取到了（见下面 skip 分支），于是**换图**这件事在盘上根本没发生——
# 《偏跟山过不去》2026-10-03：清单和 prompts.csv 都已改成 Pexels #38552827（背包与野花），
# fetch 打「完成 1/1」，atmosphere-17.png 却还是上一期正文用过的 #17941302（木屋窗台），
# 八道门禁全绿，因为溯源门禁对的是「清单 ↔ prompts.csv ↔ 文件存在」，从不看文件是谁。
# 感知哈希能不能顶上？实测不能：dHash 在同图调色后 max 31、不同图之间 min 12，
# 模糊二值与模糊相关两版描述子的两个分布完全重叠，所以「这张图是不是这张照片」
# 只能靠取图当时记下的凭据，不能靠事后比像素。
PROV_NAME = "_provenance.json"
PROV_NOTE = ("每张已采纳图的出身：fetch-pexels-by-id.py 每成功写下一张就记 photo_id 与 _raw/ 底图 sha256。"
             "它把「盘上的文件」和「清单声称的照片」连起来——同名跳过、手工拷图、旧 _raw 经 --restore 复活，"
             "都会在这里露出来。调色只重写 atmosphere-N.png，从不写 _raw/，所以 sha256 对得上就说明"
             "底图还是当年按这个 id 取回的那一张。")

repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# --segment=hook|body：只取清单里属于这一段的那些条。
# 为什么要有这个开关：一份 data/<书名>_pexels_ids.json 记的是**整期**的选型（钩子 2 张 + 正文 15 张），
# 而钩子段出片前要 `FORCE=1` 重取——正文条目一旦跟着被重取，盘上已经调好色的 atmosphere-N.png
# 就被 CDN 的原始亮度盖回去了，而后面那条 `IMAGES_SUBDIR=images-hook grade` 只调钩子目录，
# 正文集就带着未调色的像素进渲染（调色是 in-place 的，覆写不可逆）。2026-10-06《猎人笔记》
# 跑 prepare-hook-segment.sh 就是这个形状：16 张正文图被逐张重下，还先落进了 images-hook/。
# 目录归属由条目自己的 dir/segment 决定（见 dirs_for），这里只解决「要不要动这一条」。
ONLY_SEGMENT = None
for a in sys.argv[1:]:
    if a.startswith("--segment="):
        ONLY_SEGMENT = a.split("=", 1)[1].strip()
        if ONLY_SEGMENT not in ("hook", "body"):
            sys.exit(f"--segment 只能是 hook 或 body，收到 {ONLY_SEGMENT!r}")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) != 2:
    sys.exit(__doc__)

ep_arg, manifest_file = args
ep_dir = ep_arg if os.path.isdir(ep_arg) else os.path.join(repo, "episodes", ep_arg)
if not os.path.isdir(ep_dir):
    sys.exit(f"episode 目录不存在: {ep_dir}")

key = None
for line in open(os.path.join(repo, ".env"), encoding="utf-8"):
    m = re.match(r"^PEXELS_API_KEY=(.*)$", line.strip())
    if m:
        key = m.group(1).strip().strip('"').strip("'")
if not key:
    sys.exit("Missing PEXELS_API_KEY in .env")

images_dir = body_images_dir(ep_dir)
manifest = json.load(open(manifest_file, encoding="utf-8"))
if ONLY_SEGMENT:
    before = len(manifest)
    manifest = [m for m in manifest
                if ("hook" if (m.get("dir") == "images-hook" or m.get("segment") == "hook")
                    else "body") == ONLY_SEGMENT]
    print(f"按 --segment={ONLY_SEGMENT} 取 {len(manifest)}/{before} 条选型"
          f"（其余 {before - len(manifest)} 条属于另一段，本轮不动）")
    if not manifest:
        sys.exit(f"--segment={ONLY_SEGMENT}：清单里没有这一段的选型，检查 {manifest_file}")


def dirs_for(item):
    """按清单条目决定落哪个段：钩子段 images-hook/，正文段 images-body/（历史期 images/）。

    以前这里无条件用 body_images_dir，于是拿 `data/<书名>_pexels_ids.json` 里
    `segment: "hook"` 的那份清单去取图，两张钩子图会静默落进正文目录
    （2026-10-02《秋园》实测：atmosphere-24/25 进了 images/，正文集被污染，
    而溯源门禁按 dir 去 images-hook/ 找，找不到就报缺图）。`dir` 是
    lock-pexels-selection.py 已经写进清单的那一列，这里必须真的读它。
    """
    sub = item.get("dir") or ("images-hook" if item.get("segment") == "hook" else None)
    if sub is None:
        # 条目没写段归属（早期 lock-pexels-candidates.py 只给钩子写 dir）。以前这里直接
        # body_images_dir(ep_dir)，而那个函数读 IMAGES_SUBDIR —— 于是「这次要处理钩子段」
        # 的开关顺手把 16 张正文图的路由也改成了 images-hook（《猎人笔记》2026-10-06，
        # 见 lib/episode_paths.py::segment_subdir 的注释）。正文条目钉死正文目录，
        # 段归属只由条目自己决定。
        sub = segment_subdir(ep_dir, item)
    target_dir = os.path.join(ep_dir, sub)
    raw = os.path.join(target_dir, "_raw")
    orig = os.path.join(target_dir, "_original")
    for d in (target_dir, raw, orig):
        os.makedirs(d, exist_ok=True)
    return target_dir, raw, orig


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prov_path(images_dir):
    return os.path.join(images_dir, PROV_NAME)


def load_prov(images_dir):
    """读溯源标记；没有就返回空表（历史剧集从没写过这份文件）。"""
    p = prov_path(images_dir)
    if not os.path.exists(p):
        return {"note": PROV_NOTE, "version": 1, "entries": {}}, False
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 坏标记等于没有凭据，按缺标记处理但不能静默
        print(f"  ⚠️ {os.path.relpath(p, repo)} 读不出来（{exc}）——按无标记处理")
        return {"note": PROV_NOTE, "version": 1, "entries": {}}, False
    d.setdefault("entries", {})
    return d, True


def certify(images_dir, name, pid, raw_file, source_url, source_bytes=None, crop_box=None,
            source_width=None):
    """取图成功后写下这张文件的出身；check-image-provenance.py 消费的就是这一行。"""
    prov, _ = load_prov(images_dir)
    prov["entries"][name] = {
        "photo_id": int(pid),
        "crop_box": norm_box(crop_box),
        "raw_sha256": sha256_file(raw_file),
        "shipped_sha256_at_fetch": sha256_file(os.path.join(images_dir, f"{name}.png")),
        # 源字节也留一份哈希：Pexels 的 CDN 直链是按 id 确定的，重下一次能逐字节复现，
        # 于是「盘上这张图 = Pexels #id」不止有自述，还有一次可重放的验证（见 verify 模式）。
        "source_sha256": hashlib.sha256(source_bytes).hexdigest() if source_bytes else "",
        "source_url": source_url.split("?")[0],
        # 取图当时真正落地的源图像素宽也必须记下（不是请求的档位：请求 1600 而主图只有 1280 时，
        # CDN 回的是 1280）。CDN 的 1600 渲染与 1280 渲染是同一张主图的**两种缩放**，裁到
        # 1024x1365 后逐像素并不相等。《猎人笔记》2026-10-06 atmosphere-5 (#32176786) 因 curl 18
        # 降到 1280 档，verify-pexels-fetch.py 仍按 1600 重放，与盘上 _raw 平均差 5.0 峰值 71
        # 报成「不符」，而按 1280 重放是 0.000/0——同照片的不同渲染被当成另一张照片。
        # 重放要回答的是「这张图是不是那个 id」，所以取图用的那份缩放是取证条件的一部分。
        "source_width": int(source_width) if source_width else None,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    tmp = prov_path(images_dir) + ".tmp"
    json.dump(prov, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, prov_path(images_dir))
    return prov["entries"][name]


def norm_box(box):
    """把清单里的裁切窗写成一个可比较的形状：None（居中裁）或四个浮点数。

    为什么要写进标记：裁切窗和 photo_id 一样决定「成片上到底是哪一格画面」。《曼斯菲尔德庄园》
    2026-10-05 只改了 atmosphere-10 的 crop_box（把四周死黑裁掉），photo_id 没动，于是取图脚本
    按标记判定「盘上已是这张照片」直接跳过——标记证明的是「同一张照片」，不是「同一次裁切」。
    """
    if not box:
        return None
    return [round(float(v), 6) for v in box]


def certified_existing(prov_entries, name, pid, raw_file, box=None):
    """「文件已存在」什么时候可以真的跳过：必须是这张文件由本脚本按同一个 photo_id、
    同一个裁切窗写下、且 _raw/ 底图仍然是当时那份。只按文件名认，换图就永远不发生。"""
    ent = prov_entries.get(name)
    if not ent or int(ent.get("photo_id") or -1) != int(pid):
        return False, ("无溯源标记" if not ent else f"标记记的是 #{ent.get('photo_id')}")
    want, have = norm_box(box), norm_box(ent.get("crop_box"))
    if want != have:
        return False, f"标记记的裁切窗是 {have if have else '居中裁'}"
    if not os.path.exists(raw_file):
        return False, "_raw/ 底图不见了"
    if sha256_file(raw_file) != ent.get("raw_sha256"):
        return False, "_raw/ 底图已被别的取图或手改换过"
    return True, "已认证"


def curl(url, binary=False, http11=False):
    # 密钥不进 argv：-K - 让 curl 从 stdin 读 header（见 scripts/lib/pexels_curl.py 的
    # 两条理由——ps 全局可读，以及任何把整条命令打出来的异常都会带走密钥）。
    opts = ["-sSL", "--max-time", "180"]
    if http11:
        opts.insert(0, "--http1.1")
    r = pexels_curl.run(opts, url, key)
    # 这里仍不用 check=True：CalledProcessError 会把整条命令写进异常，
    # 一次 curl 抖动（exit 18 partial file 很常见）就能把报错打进终端和会话记录。
    if r.returncode != 0:
        raise SystemExit(_redact(f"curl {r.returncode} 失败: {url.split('?')[0]}: {r.stderr.decode('utf-8', 'replace')}"))
    return r.stdout if binary else json.loads(r.stdout)


def curl_cdn(url, name):
    """CDN 拉原图：先重试，别急着回落 API。

    同一时刻常有几条会话在拉图，exit 16（HTTP2 framing）和 18（partial file）是
    最常见的抖动；回落一次就多烧一次共享 key 的限流额度，而限流是按小时的，一次
    抖动就可能把整期取图卡住几十分钟。第二轮起强制 --http1.1 绕开 HTTP2 帧错误，
    并且把字节真解码一遍——截断的 JPEG 能 open 但 load 会报，只 open 会把半张图
    当成成功交付。
    """
    last = "未知错误"
    for attempt in range(3):
        try:
            raw = curl(url, binary=True, http11=attempt > 0)
        except SystemExit as exc:
            last = str(exc)
        else:
            try:
                probe = Image.open(io.BytesIO(raw))
                probe.load()
            except Exception as exc:  # noqa: BLE001 - 半张图与网络错误同等对待
                last = f"下载内容解码失败（{len(raw)} bytes, {exc}）"
            else:
                return raw
        time.sleep(2 * (attempt + 1))
    raise SystemExit(_redact(f"{name} CDN 三次重试仍失败: {last}"))


def _redact(msg):
    return msg.replace(key, "***") if key else msg


def curl_cdn_widths(base_url, name):
    """按宽度阶梯取原图：CDN 对大图常「transfer closed」（curl 18），小图一次就过。

    《德米安》2026-10-01：#31837414 在 w=1600 三次重试全被截断，w=800 一次成功。
    只降一次宽度到仍然不放大（≥ TARGET_W）的档位，既保住画质又不再卡住整期取图。

    返回值是 (字节, 落地的档位)：降过档就必须让调用方把档位记进溯源标记，
    否则 verify-pexels-fetch.py 只能按 1600 重放，会把「同一张照片的另一档渲染」
    判成另一张照片（《猎人笔记》atmosphere-5 2026-10-06，实测平均差 5.0 峰值 71）。
    """
    head = base_url.split("?")[0]
    widths = [MIN_SRC_W] + [w for w in (1280, 1024) if w < MIN_SRC_W]
    last = "未知错误"
    for w in widths:
        url = f"{head}?auto=compress&cs=tinysrgb&w={w}"
        try:
            raw = curl_cdn(url, name)
        except SystemExit as exc:
            last = str(exc)
            print(f"  {name} — w={w} 取回失败，降一档宽度重试")
            continue
        probe = Image.open(io.BytesIO(raw))
        if probe.width < TARGET_W:
            last = f"w={w} 取回只有 {probe.width}px 宽，裁切后需放大"
            print(f"  {name} — {last}，继续试更宽的档位")
            continue
        return raw, w
    raise SystemExit(_redact(last))


# 裁切本体在 scripts/lib/pexels_crop.py：verify-pexels-fetch.py 要用同一份实现按 id 重放一次，
# 两边各写一遍的话，「盘上图 = 清单那张照片」这条证据就只是近似。

ok = 0
landed = {}
written = []
skipped_certified = []
unproven = []
for item in manifest:
    name, pid = item["name"], int(item["photo_id"])
    images_dir, raw_dir, original_dir = dirs_for(item)
    rel_sub = os.path.relpath(images_dir, ep_dir)
    landed[rel_sub] = landed.get(rel_sub, 0) + 1
    target = os.path.join(images_dir, f"{name}.png")
    raw_file = os.path.join(raw_dir, f"{name}.png")
    if os.path.exists(target) and not (FORCE or item.get("force")):
        prov, has_prov = load_prov(images_dir)
        if not has_prov:
            # 历史目录从没被新取图脚本写过标记：这里仍然跳过（共享 key 按小时限流，
            # 重跑一期正文 28 张全量重取会把整批卡死），但必须在汇总里点名「未认证」，
            # 让「盘上有文件」不再被当成「文件就是那张照片」。
            print(f"  {name} — 已存在，跳过（无 {PROV_NAME}，无法证明它就是 Pexels #{pid}）")
            ok += 1
            unproven.append(f"{rel_sub}/{name}.png")
            continue
        good, why = certified_existing(prov["entries"], name, pid, raw_file, item.get("crop_box"))
        if good:
            print(f"  {name} — 盘上已是 Pexels #{pid}（溯源标记相符），跳过")
            ok += 1
            skipped_certified.append(f"{rel_sub}/{name}.png")
            continue
        # 盘上有同名文件，但标记证明不了它是清单要的这张照片：换图必须真的落盘。
        # 以前这一步是 `ok += 1; continue`，于是《偏跟山过不去》换了 id、写了 prompts.csv、
        # 过了八道门禁，而 atmosphere-17.png 的字节还是上一期放过的那张。
        action = "覆盖重取裁切窗" if "裁切窗" in why else "覆盖重取"
        print(f"  {name} — ⚠️ 盘上同名文件{why}，清单要 Pexels #{pid}"
              + (f"（裁切窗 {item.get('crop_box')}）" if "裁切窗" in why else "")
              + f" —— {action}")
    # 锁定文件里已有 src.original（lock-pexels-selection.py 落的 image_url），
    # 直接按宽度参数打 CDN 就行，不必再按 id 查一次 API。共享 key 限流时，
    # 28 张图 = 28 次 API 调用全在退避重试，选型早就做完了却卡在取图这一步。
    # 只有历史锁定文件没有 image_url 时才回落到按 id 补查（顺带校验 id 真实存在）。
    direct = (item.get("image_url") or "").split("?")[0]
    credit = f"Pexels #{pid} · {item.get('photographer') or '未知摄影师'}"
    if direct and f"/{pid}/" in direct:
        src = direct + f"?auto=compress&cs=tinysrgb&w={MIN_SRC_W}"
        try:
            raw, src_w = curl_cdn_widths(src, name)
        except SystemExit as exc:
            print(f"  {name} — CDN 取回失败，回落 API 重试: {exc}")
            # 限流时 API 回的是 429 的 JSON 错误体而不是图片元数据，json.loads 会直接
            # 抛异常把整期取图打断（已经取回的那几张白取）。这里只跳过这一张，
            # 让剩下的图先落地，稍后重跑本脚本补上缺的那张。
            try:
                photo = curl(f"https://api.pexels.com/v1/photos/{pid}")
            # curl() 用 SystemExit 报错（为了把 Authorization 头脱敏），而 SystemExit
            # 不是 Exception 的子类：隧道回 "curl: (16) HTTP2 framing" 时它会直接穿到顶层，
            # 整期取图当场退出（《德米安》2026-10-01 第三次实测）。
            except (Exception, SystemExit) as api_exc:  # noqa: BLE001 - 抖动/限流都只影响这一张
                print(f"  {name} — API 补查也失败（{api_exc}），稍后重跑补齐")
                continue
            if not isinstance(photo, dict) or photo.get("id") != pid:
                print(f"  {name} — ID {pid} 取回失败")
                continue
            src = photo["src"]["original"]
            # API 回落到手的那一路以前没包 try：一张 CDN 抽风的图会把 SystemExit 抛到
            # 顶层，整期取图当场退出，后面的图全得重来（《德米安》2026-10-01 实测两次）。
            try:
                raw, src_w = curl_cdn_widths(src, name)
            except SystemExit as cdn_exc:
                print(f"  {name} — API 回落仍取不到图（{cdn_exc}），稍后重跑补齐")
                continue
    else:
        # 历史锁定文件没有 image_url 才走这里：按 id 补查一次 API。限流时同样只跳过
        # 这一张，不打断整期取图。
        try:
            photo = curl(f"https://api.pexels.com/v1/photos/{pid}")
        except (Exception, SystemExit) as api_exc:  # noqa: BLE001 - 同上，SystemExit 也要接住
            print(f"  {name} — API 补查失败（{api_exc}），稍后重跑补齐")
            continue
        if not isinstance(photo, dict) or photo.get("id") != pid:
            print(f"  {name} — ID {pid} 取回失败: {photo}")
            continue
        credit = f"pexels #{pid} ({photo['photographer']})"
        src = photo["src"]["original"]
        try:
            raw, src_w = curl_cdn_widths(src, name)
        except SystemExit as cdn_exc:
            print(f"  {name} — 取不到图（{cdn_exc}），稍后重跑补齐")
            continue
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w0, h0 = img.size
    box = item.get("crop_box")
    crop = crop_3x4(img, box)
    crop.save(target)
    # _raw/ 必须是**同画幅的未调色裁切图**：grade-atmosphere-images.py --restore 会把它直接
    # 拷回 images/，如果这里存的是未裁切原图（1600x2400），一次 --restore 就把 3:4 的成片底图
    # 换成 2:3，渲染时整期变形。未裁切原图另存 _original/，只用于换裁切框时复损。
    crop.save(os.path.join(raw_dir, f"{name}.png"))
    img.save(os.path.join(original_dir, f"{name}.png"))
    ent = certify(images_dir, name, pid, os.path.join(raw_dir, f"{name}.png"), src, raw, box,
                  source_width=w0)
    written.append(f"{rel_sub}/{name}.png")
    # 裁切后的可用宽度不足目标宽时是放大，成片会发虚——必须显式提示而不是静默交付
    bw, bh = (w0 * (float(box[2]) - float(box[0])), h0 * (float(box[3]) - float(box[1]))) if box else (w0, h0)
    crop_w = min(bw, int(bh * TARGET_W / TARGET_H))
    flag = "  ⚠️ 裁切后需放大，可能发虚" if crop_w < TARGET_W else ""
    box_note = f" 窗口{box}" if box else ""
    # 档位打出来：降过档就必须看得见，否则 verify-pexels-fetch.py 按 1600 重放判红时，
    # 排查的人只会去看「照片对不对」，看不到「两边取的根本不是同一次缩放」。
    rung_note = f" 档位 w={src_w}" if src_w != MIN_SRC_W else ""
    print(f"  {name} ← {credit} 取回 {w0}x{h0} "
          f"裁切宽 {int(crop_w)}px{flag}{box_note}{rung_note} | {(item.get('alt') or '')[:50]}")
    print(f"      已认证落盘 _raw sha256 {ent['raw_sha256'][:16]}…")
    ok += 1

prov_dirs = sorted({os.path.dirname(w) for w in written})
if prov_dirs:
    print(f"  本轮覆盖 {len(prov_dirs)} 个图片目录的 {PROV_NAME}：" + "、".join(prov_dirs))
print(f"\n完成 {ok}/{len(manifest)} → "
      + "、".join(f"{n} 拍/{sub}" for sub, n in landed.items())
      + f"（{ep_dir}）")
print(f"  本轮真正写盘 {len(written)} 张｜标记相符跳过 {len(skipped_certified)} 张"
      + (f"｜⚠️ 无标记跳过 {len(unproven)} 张（盘上是不是这张照片无从证明，重取请加 FORCE=1）"
         if unproven else ""))
if unproven:
    for u in unproven[:6]:
        print(f"     · {u}")
sys.exit(0 if ok == len(manifest) else 1)
