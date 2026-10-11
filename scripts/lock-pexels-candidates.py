#!/usr/bin/env python3
"""把「全尺寸候选复核」的结果锁进 data/<书名>_pexels_ids.json。

为什么需要：lock-pexels-selection.py 按联系表格子编号取图，而联系表每格只有 200x267，
穿帮物恰恰是在那个尺寸上看不见的（《达洛维夫人》2026-10-01：按表锁定的 18 张里 13 张
在全尺寸复核时被判不合格）。pexels_candidates.py 把候选按复核尺寸落到本地、逐张看过之后，
选型就该按「哪个候选目录的第几格」锁，id 由脚本从 cand.json 里程序化取出，
仍然不允许任何人手抄 photo id。

用法：
  python3 scripts/lock-pexels-candidates.py "<书名>" picks.json [--apply]
picks.json（两种写法都接受，可以混用）：
  [{"name": "atmosphere-3", "beat": "tmp/dlrw-cand/a3", "cell": 4, "note": "..."},
   {"name": "atmosphere-1", "photo_id": 7837822, "note": "..."}]     沿用已锁定的旧图
  钩子段的图加 "dir": "images-hook"（或 "segment": "hook"），与 lock-pexels-selection.py 同义。

钩子段选型在锁之前会查一条跨期重复：Pexels photo id 只要在全仓库任何一期的画面上屏过，
就不允许再当钩子图用（观众换期要看见一个新地方，钩子放一帧旧照片读起来像上一期的片尾彩蛋）。
口径与 check-image-provenance.py 共用 scripts/lib/pexels_index.py。检索阶段先用
pexels_candidates.py --exclude-used 把这些 id 直接丢掉，省得复核完才发现不能用。
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

import importlib.util

# 跨期撞图的口径只写一份：门禁、挑图脚本、这里共用同一份全仓库索引
_pspec = importlib.util.spec_from_file_location(
    "pexels_index", os.path.join(ROOT, "scripts", "lib", "pexels_index.py"))
PIX = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(PIX)
# 目检否决但从未投放的图，索引看不见（它只认「已上过屏」），所以另有这一份名单。
_rspec = importlib.util.spec_from_file_location(
    "pexels_reject", os.path.join(ROOT, "scripts", "lib", "pexels_reject.py"))
REJECT = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(REJECT)
_repo_index = None
_rejected = None


def reject_list():
    """持久否决名单只读一次：一批 picks 循环里反复 open 同一个 JSON 没有意义。

    故意不叫 `rejected_ids`——那个名字属于 lib。这里只是缓存外壳，
    同名会让「判据只写一份」这条静态检查看起来像被抄了两遍（`npm run check` 就是查这个）。
    """
    global _rejected
    if _rejected is None:
        _rejected = REJECT.rejected_ids()
    return _rejected


def repo_index():
    """全仓库 photo id → 上屏记录；只在真的有钩子段选型要判跨期时才扫那 111 份 CSV。"""
    global _repo_index
    if _repo_index is None:
        _repo_index = PIX.build(ROOT)
    return _repo_index


ap = argparse.ArgumentParser()
ap.add_argument("episode")
ap.add_argument("picks")
ap.add_argument("--apply", action="store_true")
ap.add_argument("--allow-used-elsewhere", action="store_true",
                help="放行「钩子图在别的期已经出现过」，理由必须写在 picks.json 的 reason 里")
ap.add_argument("--allow-rejected", action="store_true",
                help="放行 data/pexels-rejected.json 里被持久否决的图，理由必须写在 picks.json 的 reason 里")
ap.add_argument("--out", help="默认 data/<书名>_pexels_ids.json")
args = ap.parse_args()

picks = json.load(open(args.picks, encoding="utf-8"))
out_path = args.out or os.path.join(ROOT, "data", f"{args.episode}_pexels_ids.json")

# 同一期里沿用旧图时，旧条目（含摄影师/链接/原图地址）从既有清单里原样搬过来，
# 不重新打 Pexels API：共享 key 限流时，一次补元数据能把整批选型卡死几十分钟。
prev = {}
if os.path.exists(out_path):
    for item in json.load(open(out_path, encoding="utf-8")):
        key = (item["name"], item.get("dir", ""))
        prev[key] = item
by_id = {int(item["photo_id"]): item for item in prev.values()}

locked = []
for pick in picks:
    name = pick["name"]
    sub = pick.get("dir") or ("images-hook" if pick.get("segment") == "hook" else "images-body")
    if "beat" in pick:
        cand_dir = pick["beat"]
        cands = json.load(open(os.path.join(cand_dir, "cand.json"), encoding="utf-8"))
        cell = int(pick["cell"])
        if not 0 <= cell < len(cands):
            sys.exit(f"{name}: 候选 #{cell} 超出 {cand_dir} 的 {len(cands)} 张")
        row = cands[cell]
        entry = {
            "name": name,
            "photo_id": row["id"],
            "photographer": row.get("photographer", ""),
            "url": row.get("url", ""),
            "page_url": row.get("url", ""),
            "image_url": row.get("image_url", ""),
            "pixels": row.get("pixels", ""),
            "avg_color": row.get("avg_color", ""),
            "alt": row.get("alt", ""),
            "query": row.get("query", ""),
            "note": pick.get("note", ""),
        }
        src = f"{os.path.basename(cand_dir.rstrip('/'))}#{cell}"
    elif "photo_id" in pick:
        pid = int(pick["photo_id"])
        base = by_id.get(pid)
        if not base:
            # 新取图路径：picks.json 自带完整溯源（fetch-pexels-images.mjs 当场写盘），
            # 只需从 Pexels API 拉一次元数据补 pixels/avg_color/alt，不再要求「已在既有清单里」。
            if pick.get("photographer") is not None and pick.get("image_url"):
                entry = {
                    "name": name,
                    "photo_id": pid,
                    "photographer": pick.get("photographer", ""),
                    "url": pick.get("url", pick.get("image_url", "")),
                    "page_url": pick.get("url", pick.get("image_url", "")),
                    "image_url": pick.get("image_url", ""),
                    "pixels": pick.get("pixels", ""),
                    "avg_color": pick.get("avg_color", ""),
                    "alt": pick.get("alt", ""),
                    "query": pick.get("query", ""),
                    "note": pick.get("note", ""),
                }
                src = "新取图 #{pid}"
            else:
                sys.exit(f"{name}: #{pid} 不在既有清单里，沿用旧图必须已经锁过")
        else:
            entry = dict(base)
            entry["name"] = name
            if pick.get("note"):
                entry["note"] = pick["note"]
            src = f"沿用 #{pid}"
    else:
        sys.exit(f"{name}: 既没有 beat 也没有 photo_id")
    # 段归属必须在下面那条跨期检查**之前**落到 entry 上。以前这两行活在检查后面，
    # 于是 beat/cell 这条主路径（也就是所有新选型）走到 `entry.get("segment")` 时永远是 None，
    # 钩子跨期那一张网只在「沿用旧图」分支才生效——2026-10-03 用一张已上屏的 #18247562
    # 当钩子图 dry-run，脚本照常打印选型并 exit 0。检索阶段的 --exclude-used 是同一件事的
    # 另一道网，但它只拦「这一轮检索」，拦不住手抄 cell 或复用旧候选目录。
    #
    # 这里**无条件**写，不留 `if sub`：正文条目缺 dir 不是「没写目录」，而是「把目录交出去让人猜」。
    # fetch-pexels-by-id.py 过去就靠 body_images_dir() 兜这个底，而那个函数认 IMAGES_SUBDIR 环境变量
    # ——2026-10-06《猎人笔记》跑 prepare-hook-segment.sh，钩子段那一步是
    # `IMAGES_SUBDIR=images-hook FORCE=1 fetch <整份 18 条清单>`，16 张正文图于是被逐张重下并
    # 写进 images-hook/，把真正的两张钩子图盖掉了。选型清单是一段图片的**归属记录**，
    # 归属必须由锁这一步写死，不能等到取图时再按环境变量反推。
    entry["dir"] = sub
    entry["segment"] = "hook" if sub == "images-hook" else "body"
    # 持久否决名单在**锁这一步**也要拦一次，不能只靠检索那一步：picks.json 里的 cell 是
    # 对着某一轮 cand.json 的下人手抄的，而那一轮可能是 --ignore-rejected 捞回来的、
    # 也可能是补检索时同目录被覆盖过的。锁口不查，否决就等于只存在于上一个会话的聊天记录里。
    # 名单只收「对任何一期都不成立」的三类（人脸／可辨认招牌／摆拍），所以正文段一样查。
    #
    # 但**本期清单里本来就有的那张不拦**：重锁一段是为了换掉某一张，其余条目原样搬回来。
    # 要是名单后来收了其中一张（比如正文图事后才被认出带人脸），拦在这里等于把整段重锁打死，
    # 而撤一张已投放的图本来就不该动名单（reject-pexels-photo.py 的第 2 条断言）。
    # 这条豁免是门禁的正/负对照：拦新图必须为真，拦「沿用本期已锁」必须为假。
    if int(entry["photo_id"]) in reject_list() and int(entry["photo_id"]) not in by_id:
        if not args.allow_rejected:
            sys.exit(f"{name}: {REJECT.describe(int(entry['photo_id']))}\n"
                     "   这张已经在全局目检里被否决过，不该再进任何一期。"
                     "确实要故意回收就加 --allow-rejected 并在这一条写 reason")
        if not pick.get("reason"):
            sys.exit(f"{name}: --allow-rejected 必须配一条 reason，说清这次回收凭什么")
        print(f"  ⚠️ {name}: 已放行被持久否决的 Pexels #{entry['photo_id']}：{pick['reason']}")
    # 钩子段先查跨期：这张照片要是别的期正文早就放过，锁下来就是一条「本期的钩子在放上一期的片尾」。
    # 口径与交付门禁共用 scripts/lib/pexels_index.py——AGENTS.md 把跨期重复划在单期门禁之外，
    # 却只让挑图脚本自己 diff（那条 diff 活在 tmp/hook-b4/write-picks.py），所以 2026-10-03 那批
    # 17 期钩子里有 10 期 11 张撞了别的期正文还八门全绿。锁的这一步拒绝，比在成片上拒绝便宜得多。
    if entry.get("segment") == "hook":
        idx = repo_index()
        hits = PIX.foreign_uses(idx, entry["photo_id"], args.episode)
        # 同一期的另一段也不能撞：门禁查这一条，但那要等 prompts.csv 写完；
        # 这里顺手一起查，钩子段一次锁两拍，撞了就在取图之前换。
        own = [x for x in idx.get(int(entry["photo_id"]), [])
               if x[0] == args.episode and x[1] != "hook"]
        if hits or own:
            if not args.allow_used_elsewhere:
                where = "、".join(filter(None, [
                    PIX.describe(hits),
                    PIX.describe(own) + "（本期正文）" if own else ""]))
                sys.exit(f"{name}: Pexels #{entry['photo_id']} 已经上过屏（{where}）"
                         "——钩子段换一张本期与别期都没用过的照片，"
                         "确实要故意回扣就加 --allow-used-elsewhere 并在这一条写 reason")
            if not pick.get("reason"):
                sys.exit(f"{name}: --allow-used-elsewhere 放行的是有意的回扣，"
                         "这一条必须有 reason 说明扣的是哪一期、为什么")
            print(f"  ⚠️ {name}: 已放行跨期/跨段回扣 Pexels #{entry['photo_id']}：{pick['reason']}")
    else:
        # 正文段也查跨期，但只查一条：这张照片是不是**别的期的钩子**已经放过。
        # 钩子是「观众换期时该看见一个新地方」那一拍；正文把它当过场重放，等于把别人片头
        # 的彩蛋搬进自己的中段。判红本来就存在（check-image-provenance.py 按钩子那一期报），
        # 只是锁这一步以前只查钩子选型，于是《猎人笔记》2026-10-06 把《女儿红》钩子用过的
        # #13438765 锁进了正文 atmosphere-4：本期十二道门全绿，红的是 `npm run check`
        # 扫到《女儿红》那一行——**一个已交付的期被新的一期判红**，而唯一的修法是新的那期重做选型。
        # 正文与正文之间不拦：同一张空房间在全库十个期的正文里各出现一次是既有事实（#34938048），
        # 拦它只会一批一批抽干素材池，而观众对一帧过场并没有「这是哪儿」的期待。
        idx = repo_index()
        hits = [x for x in PIX.foreign_uses(idx, entry["photo_id"], args.episode)
                if x[1] == "hook"]
        if hits:
            if not args.allow_used_elsewhere:
                sys.exit(f"{name}: Pexels #{entry['photo_id']} 已经上过 {PIX.describe(hits)} 的钩子段"
                         "——正文换一张没被任何一期钩子用过的照片，"
                         "确实要故意回扣就加 --allow-used-elsewhere 并在这一条写 reason")
            if not pick.get("reason"):
                sys.exit(f"{name}: --allow-used-elsewhere 放行的是有意的回扣，"
                         "这一条必须有 reason 说明扣的是哪一期的钩子、为什么")
            print(f"  ⚠️ {name}: 已放行「正文重放别期钩子图」Pexels #{entry['photo_id']}：{pick['reason']}")
    if pick.get("crop_box"):
        # 裁切窗口跟着选型一起锁：fetch-pexels-by-id.py 按它把画面一角的现代杂物
        # （游客、路牌、双黄线）排除在 3:4 画幅之外，而 prompts.csv 的溯源行必须写出
        # 真实裁切法，不能继续声称是一次 center crop。
        entry["crop_box"] = pick["crop_box"]
    # 换图要留下「换掉了谁」这一笔：fetch-pexels-by-id.py 靠它判断盘上的同名文件是上一版的照片，
    # 必须覆盖重取。2026-10-03《偏跟山过不去》换了 id、写了 prompts.csv、八门全绿，
    # 而 fetch 把「同名文件已存在」当成取到了，atmosphere-17.png 一个字节都没变。
    old = prev.get((name, sub or "")) or next(
        (v for (n, d), v in prev.items() if n == name
         and (v.get("segment") or ("hook" if v.get("dir") == "images-hook" else "body"))
         == ("hook" if sub == "images-hook" else "body")), None)
    if old and int(old["photo_id"]) != int(entry["photo_id"]):
        entry["replaces_photo_id"] = int(old["photo_id"])
        print(f"  ⚠️ {name}: 本条把盘上的 Pexels #{old['photo_id']} 换成 #{entry['photo_id']}，"
              "取图时必须覆盖重取（fetch 已按 replaces_photo_id 自动处理）")
    locked.append(entry)
    print(f"  {name:<16} {src:<14} → Pexels #{entry['photo_id']} · {entry.get('photographer','')} "
          f"· {entry.get('pixels','')} · sat/亮 {(entry.get('avg_color') or '')} · {str(entry.get('alt',''))[:30]}")

ids = [e["photo_id"] for e in locked]
if len(set(ids)) != len(ids):
    dup = sorted({i for i in ids if ids.count(i) > 1})
    sys.exit(f"同一张 Pexels 照片被两个层引用了：{dup}（要复用请走 scene-map 的 reuse_of）")


def _seg_of(item):
    return item.get("segment") or ("hook" if item.get("dir") == "images-hook" else "body")


# 一份 manifest 记的是这一期**全部**已锁选型（钩子段 + 正文段，靠 segment/dir 分辨），所以重锁
# 一段只能替换那一段的条目。以前这里是整份覆盖写：只锁 2 张钩子图就会把 23 行正文选型一次抹掉
# （2026-10-02《秋园》，lock-pexels-selection.py 已按段合并修过，这个候选版脚本漏了同一刀——
# 而跨期重选钩子图恰好是「只锁钩子段」的调用，覆盖写当天就会把正文记录清零）。
segs_now = {_seg_of(e) for e in locked}
kept = [item for item in prev.values() if _seg_of(item) not in segs_now]
merged = kept + locked
mids = [e["photo_id"] for e in merged]
if len(set(mids)) != len(mids):
    dup = sorted({i for i in mids if mids.count(i) > 1})
    print(f"⚠️ 合并后 Pexels {dup} 被本期两个层引用（钩子段与正文段共用同一帧是门禁红线）")

print(f"\n{len(locked)} 条 → {out_path}（另有 {len(kept)} 条其他段选型原样保留）")
if args.apply:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(merged, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写盘")
else:
    print("[dry-run] 未写盘，加 --apply")
