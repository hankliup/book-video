#!/usr/bin/env python3
"""段级脚本版本的跨语言对照：Node 建轴与 Python 补coverage 必须取到同一个版本。

缺陷（2026-10-07 第五批《围城》）：整期版本 `brief.json.scriptVersion` 说的是**正文**（B 版），
而三段结构的钩子文案在 `script-hook.csv` 里、只有 A 版两行。`create-body-timings-precise.mjs
--segment=hook` 拿 B 去过滤它，抛 `No script rows found for version B`；就算它过了，
下游 `snap-body-timings-vad.py` 会用同一个 B 过滤出 0 行，在 `matched/total` 上除零。

解析逻辑有两份实现，必须逐字一致：JS 侧 `scripts/lib/script-version.mjs`（建轴用它，
因为建轴是 Node）和 Python 侧 `scripts/lib/script_version.py`（coverage/审计用它，
因为那些脚本是 Python）。两份漂一分，轴就一半新一半旧。

断言两边都有：
- 健康形状：正文 CSV 用整期版本，钩子 CSV 用它自己的唯一版本；
- 反向对照：**旧口径**（直接拿整期版本过滤本段 CSV）在钩子那份文件上得到 0 行，
  否则这条测试可能悄悄变成永真；
- 显式入参最大、多版本不猜、缺文件不崩；
- 跨语言：同一批夹具喂给 `scripts/lib/script-version.mjs`，四个 case 的答案逐字相同。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from lib.script_version import (csv_versions, episode_version,  # noqa: E402
                                resolve_segment_version)

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}\n       得到 {got!r}\n       期望 {want!r}")
        FAILS.append(name)


def make_episode(tmp: str, *, brief_version: str, body_versions=("B",),
                 hook_versions=("A",), name: str = "测试期") -> str:
    ep = os.path.join(tmp, name)
    os.makedirs(ep, exist_ok=True)
    with open(os.path.join(ep, "brief.json"), "w", encoding="utf-8") as fh:
        json.dump({"display_title": name, "scriptVersion": brief_version}, fh)
    for fname, versions in (("script-body.csv", body_versions), ("script-hook.csv", hook_versions)):
        lines = ["version,order,text"]
        for ver in versions:
            lines.append(f'{ver},1,这是 {ver} 版第一行')
            lines.append(f'{ver},2,这是 {ver} 版第二行')
        with open(os.path.join(ep, fname), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return ep


def rows_for(csv_path: str, ver: str) -> int:
    if not os.path.exists(csv_path):
        return 0
    with open(csv_path, encoding="utf-8-sig") as fh:
        return sum(1 for line in fh.read().splitlines()[1:]
                   if line.split(",", 1)[0].strip('"') == ver)


def node_answers(tmp: str, cases: list[tuple[str, str, str]]) -> list[str]:
    """把同一批夹具交给 mjs，返回它给每个 (episodeDir, scriptPath, requested) 的版本。"""
    script = """
import { resolveSegmentVersion } from %s;
const cases = JSON.parse(process.argv[2]);
for (const [ep, csvPath, req] of cases) {
  process.stdout.write(resolveSegmentVersion(ep, csvPath, req).version + '\\n');
}
""" % json.dumps(os.path.join(ROOT, "scripts", "lib", "script-version.mjs"))
    probe = os.path.join(tmp, "probe.mjs")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(script)
    out = subprocess.run(["node", probe, json.dumps(cases)], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"node 探针失败：{out.stderr[-400:]}")
    return out.stdout.strip().splitlines()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        print("① 正文 B / 钩子 A：各段取自己 CSV 里有的那一版")
        ep = make_episode(tmp, brief_version="B")
        body_csv = os.path.join(ep, "script-body.csv")
        hook_csv = os.path.join(ep, "script-hook.csv")
        check("整期版本仍是正文的 B", episode_version(ep), "B")
        check("正文段 = B", resolve_segment_version(ep, body_csv)[:2], ("B", False))
        check("钩子段 = A 且标记为改判",
              resolve_segment_version(ep, hook_csv), ("A", True, "B"))
        # 反向对照：旧口径（不复核）在钩子 CSV 上就是 0 行——测试不能是永真的
        check("旧口径在钩子 CSV 上取到 0 行（对照）", rows_for(hook_csv, "B"), 0)
        check("新口径在钩子 CSV 上取到 2 行", rows_for(hook_csv, "A"), 2)

        print("② 显式入参最大：调用方点名 C 就交给它，哪怕没有行")
        check("requested=C", resolve_segment_version(ep, hook_csv, "C")[:2], ("C", False))
        check("C 在钩子 CSV 上确实 0 行（所以报错仍会说话）", rows_for(hook_csv, "C"), 0)

        print("③ 本段 CSV 有两个版本且不等于整期版本：不替人选")
        ep2 = make_episode(tmp, brief_version="D", hook_versions=("A", "C"), name="两版期")
        hook2 = os.path.join(ep2, "script-hook.csv")
        check("保持整期版本 D", resolve_segment_version(ep2, hook2)[:2], ("D", False))
        check("D 在钩子 CSV 上 0 行（对照）", rows_for(hook2, "D"), 0)

        print("④ 缺文件不崩")
        check("不存在的 CSV → 空版本表", csv_versions(os.path.join(tmp, "nope.csv")), [])
        ep3 = os.path.join(tmp, "空期")
        os.makedirs(ep3, exist_ok=True)
        check("无 brief 无 script-body.csv → A_reference_like", episode_version(ep3), "A_reference_like")

        print("⑤ 跨语言对拍：mjs 与 py 对同一批夹具给同一个版本")
        cases = [[ep, body_csv, ""], [ep, hook_csv, ""], [ep, hook_csv, "C"],
                 [ep2, hook2, ""]]
        py = [resolve_segment_version(e, c, r)[0] for e, c, r in cases]
        try:
            nd = node_answers(tmp, cases)
        except SystemExit as exc:
            nd = [str(exc)]
        check("四个 case 逐字相同", nd, py)
        check("且第二个 case 正是 A（钩子段）", py[1], "A")

    if FAILS:
        print(f"\n❌ {len(FAILS)} 项不符：{'、'.join(FAILS)}")
        return 1
    print("\n✅ 段级版本：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
