#!/usr/bin/env python3
"""跨批次持久否决名单：唯一读 `data/pexels-rejected.json` 的地方。

为什么需要这一层（2026-10-03，第四批钩子图检索）：`--exclude-used` 只排除**已经上过屏**的
photo id（判据 `scripts/lib/pexels_index.py`）。它拦不住「目检否决但从来没投放过」的图，
于是同一张照片每一批都会被重新端上来一次：大理稻田亲子旅拍那一组（#39521085/87/91/92/102/108）
本轮出现在 15 个候选批次里，中式村委长廊 #39854273 出现在 18 个批次里——它们的 Pexels `alt`
是「animals, Beautiful scenery, beautiful woman」这种乱标签，任何氛围词都能把它们召回。
人工否决是一次性的会话记忆，不是管线状态；不落成文件，下一批就得把同样的图再看一遍。

名单里只放**对任何一期都不成立**的三类（和 house style 的三条硬淘汰一一对应）：
出现人脸、有可辨认的文字招牌、生活方式/道具摆拍。某一期的地方不对（比如江南白墙青瓦用在
西北村庄）不算——那张图对另一期可能就是对的，把它拉黑等于让检索池越用越小。

写入用 `scripts/reject-pexels-photo.py`，不要手改 JSON（脚本会断言 id 是数字、理由非空、
并拒绝把已经投放过的 id 拉黑——那会让溯源门禁去红一个已成片的期）。
"""
from __future__ import annotations

import json
import os
from typing import Dict, Set

# 与 scripts/lib/pexels_index.py 同一算法：这一层是 scripts/lib，仓库根要退两级。
# 退一级会算出 scripts/data/pexels-rejected.json，读的时候文件"不存在"（名单看起来是空的），
# 写的时候在 scripts/ 下凭空多长出一份第二名单——两份名单比没有名单更糟。
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
REJECT_FILE = os.path.join(ROOT, "data", "pexels-rejected.json")


def load(path: str = REJECT_FILE) -> Dict[str, dict]:
    """返回 {photo_id(str): {reason, episode, rejected_at}}；文件不存在就是空名单。"""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    ids = data.get("ids") or {}
    out: Dict[str, dict] = {}
    for key, val in ids.items():
        if not str(key).isdigit():
            continue
        out[str(key)] = val if isinstance(val, dict) else {"reason": str(val)}
    return out


def rejected_ids(path: str = REJECT_FILE) -> Set[int]:
    return {int(k) for k in load(path)}


def describe(pid: int, path: str = REJECT_FILE) -> str:
    rec = load(path).get(str(pid)) or {}
    why = rec.get("reason") or "（名单里没写理由）"
    where = rec.get("episode") or "?"
    when = rec.get("rejected_at") or "?"
    return f"#{pid} 已被持久否决（{where} / {when}）：{why}"


if __name__ == "__main__":
    ids = load()
    print(f"持久否决 {len(ids)} 张：")
    for k in sorted(ids, key=int):
        print(f"  #{k}  {ids[k].get('reason', '')[:70]}")
