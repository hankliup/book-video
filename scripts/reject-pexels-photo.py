#!/usr/bin/env python3
"""把目检否决的照片写进跨批次持久否决名单 `data/pexels-rejected.json`。

用法：
  python3 scripts/reject-pexels-photo.py <id> [<id> ...] \
      --reason "<为什么对任何一期都不成立>" --episode "<在哪个候选批次看出来的>" [--date YYYY-MM-DD]

例子（本轮真实否决）：
  python3 scripts/reject-pexels-photo.py 39521102 39521088 \
      --reason "大理稻田亲子旅拍，红衣女子人脸" --episode 东京八平米

只写名单，不碰任何期目录。名单的唯一读入口是 `scripts/lib/pexels_reject.py`：
`pexels_candidates.py` 检索时默认丢掉名单里的 id，`lock-pexels-candidates.py` /
`lock-pexels-selection.py` 锁定时拒绝（确实要故意回收，就带 `--allow-rejected` 并在 picks 里写 reason）。

三条断言，都是为了让这个名单能长期用下去：
1. **理由必须落在「对任何一期都不成立」的三类**：出现人脸、可辨认文字招牌、生活方式/道具摆拍。
   「这期的地方不对」不收——那张图对另一期可能就是对的，拉黑只会让检索池越用越小。
2. **拒绝把已经上过屏的 id 拉黑**：那等于宣布某期成片用了废图，而重跑那一期的成本远高于换检索词。
   真要撤一张已投放的图，走重选+重渲，不要动名单。
3. **幂等合并**：同一 id 重复记录只更新它自己那条，绝不清空别人写过的条目。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from lib import pexels_index, pexels_reject  # noqa: E402

# 「对任何一期都不成立」的三类硬淘汰，词表只用来提醒，不用来放行——写理由的人必须自己负责。
GLOBAL_DEFECTS = ("人脸", "多人", "游客", "行人", "招牌", "文字", "标识", "标牌", "指示牌",
                  "摆拍", "道具", "棚拍", "静物", "时尚", "LED", "监控", "摄像头",
                  "标签", "乱标签", "塑钢", "现代")


def parse_ids(raw_ids):
    out = []
    for raw in raw_ids:
        token = str(raw).strip().lstrip("#")
        if not token.isdigit():
            raise SystemExit(f"❌ photo id 必须是纯数字，收到「{raw}」")
        out.append(int(token))
    if not out:
        raise SystemExit("❌ 至少给一个 photo id")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+", help="photo id（纯数字，可带 #）")
    ap.add_argument("--reason", required=True, help="为什么对任何一期都不成立")
    ap.add_argument("--episode", default="", help="在哪个候选批次看出来的")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--allow-shipped", action="store_true",
                    help="明知这张已投放仍要记录（默认拒绝）")
    args = ap.parse_args()

    ids = parse_ids(args.ids)
    reason = args.reason.strip()
    if len(reason) < 6:
        raise SystemExit("❌ --reason 太短：名单是给下一批人看的，「不合适」没有信息量")
    if not any(token in reason for token in GLOBAL_DEFECTS):
        raise SystemExit(
            "❌ 理由里没有「人脸 / 招牌 / 文字 / 指示牌 / 摆拍 / 道具 / 静物 / 监控」这类"
            "**对任何一期都不成立**的字样。\n"
            "   持久名单只收全局废图；如果只是这一期的地方或年代不对，那是本期换检索词的事，"
            "不要把它拉黑（拉黑了下一批就没得选）。")

    index = pexels_index.build(ROOT)
    path = pexels_reject.REJECT_FILE
    # 新建时把说明写回去：这个文件是要长期人工看的，光有一串 id 没人记得为什么。
    data = {"_说明": "跨批次持久否决名单，只收「对任何一期都不成立」的三类（人脸／可辨认文字招牌／"
                     "生活方式与道具摆拍）。唯一读入口 scripts/lib/pexels_reject.py，"
                     "写入用 scripts/reject-pexels-photo.py。",
            "ids": {}}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    data.setdefault("ids", {})

    added, updated, refused, same = [], [], [], []
    for pid in ids:
        uses = index.get(pid) or []
        if uses and not args.allow_shipped:
            refused.append((pid, pexels_index.describe(uses)))
            continue
        rec = {"reason": reason, "episode": args.episode, "rejected_at": args.date}
        key = str(pid)
        if key not in data["ids"]:
            data["ids"][key] = rec
            added.append(pid)
        elif data["ids"][key] == rec:
            same.append(pid)
        else:
            data["ids"][key] = rec
            updated.append(pid)

    # 没改动就不落盘：全被拒/全重复时重写文件只会让「谁改了名单」这件事更难追溯。
    if added or updated:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    print(f"持久否决名单 {os.path.relpath(path, ROOT)}："
          f"新增 {len(added)}，更新 {len(updated)}，原样 {len(same)}，"
          f"已投放被拒 {len(refused)}，名单共 {len(data['ids'])} 条"
          f"{'' if (added or updated) else '（本次未写盘）'}")
    for pid in added + updated:
        print(f"  ✅ #{pid}  {reason[:60]}")
    for pid, where in refused:
        print(f"  ⛔ #{pid} 已经上过屏（{where}）——撤图走重选+重渲，不要动名单")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
