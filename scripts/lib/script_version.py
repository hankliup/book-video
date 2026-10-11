#!/usr/bin/env python3
"""段级脚本版本：与 `scripts/lib/script-version.mjs` 的 `resolveSegmentVersion` 同一条规则。

为什么两份语言要有同一个判据：建轴链一半在 Node（`create-body-timings-precise.mjs`），
一半在 Python（`snap-body-timings-vad.py`），而两边都要把「整期版本」用到**本段自己的 CSV** 上。
整期版本（`brief.json.scriptVersion`，或 `script-body.csv` 的唯一版本）说的是正文；三段结构的钩子文案
在 `script-hook.csv` 里，两份 CSV 的版本号没有义务相同——《围城》正文是 B 版、钩子只有 A 版两行，
2026-10-07 第五批就撞在这里：Node 侧抛 `No script rows found for version B`，
Python 侧会拿到 0 行、在 `matched/total` 上除零。

规则（两边逐字一致，`scripts/tests/test-segment-script-version.py` 跑同一批夹具对拍）：
1. 显式入参最大；
2. 整期版本在本段 CSV 里有行 → 用它；
3. 整期版本在本段 CSV 里没有行、而本段 CSV 只有一个版本 → 用那一个；
4. 本段 CSV 里有两个以上版本且都不等于整期版本 → 原样交回整期版本，让「没有行」的报错继续说话
   （那是真需要人来选的情况，不是脚本能替人决定的）。
"""
from __future__ import annotations

import csv
import json
import os


def csv_versions(csv_path: str) -> list[str]:
    """读一份文案 CSV 的 version 列（去重、保序）。文件不存在就是空表。"""
    if not csv_path or not os.path.exists(csv_path):
        return []
    out: list[str] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            ver = (row.get("version") or "").strip()
            if ver and ver not in out:
                out.append(ver)
    return out


def episode_version(episode_dir: str) -> str:
    """整期版本：brief.json.scriptVersion > script-body.csv 唯一版本 > "A_reference_like"。

    最后那个兜底串与 `script-version.mjs` 逐字相同——两边不一致的话，
    「同一期在 Node 侧和 Python 侧取到不同版本」会变成下一个静默缺陷。
    """
    brief = os.path.join(episode_dir, "brief.json")
    if os.path.exists(brief):
        try:
            with open(brief, encoding="utf-8") as fh:
                ver = json.load(fh).get("scriptVersion")
            if ver:
                return str(ver)
        except Exception:  # noqa: BLE001 - 坏 brief 不该让建轴链崩在这里
            pass
    present = csv_versions(os.path.join(episode_dir, "script-body.csv"))
    if len(present) == 1:
        return present[0]
    return "A_reference_like"


def resolve_segment_version(episode_dir: str, script_path: str,
                            requested: str | None = None) -> tuple[str, bool, str]:
    """返回 (版本, 是否改了整期版本, 整期版本)。"""
    episode = (requested or "").strip() or episode_version(episode_dir)
    if requested:
        return episode, False, episode
    present = csv_versions(script_path)
    if present and episode not in present and len(present) == 1:
        return present[0], True, episode
    return episode, False, episode
