#!/usr/bin/env python3
"""token 证人的「行首被误听」回收：证人的参考轴必须落在这一行**自己的开口**上。

为什么钉这一条（2026-10-10《高老头》）。渲前轴闸把这一期拦在渲染之前（rc=1、
「❌ 轴闸不通过（修完仍红）」），而修轴臂跑完之后三道对账里有两道已经绿：逐行覆盖 0 行红、
能量边界 0 行红，只有 `token 派生` 报「行 117 交付轴行首 -1.59s 于 token 重算轴」，
越过最大 1.5s 那条线。量到行 117 自己身上：

    文案  叉匙声、笑声，样样不少。      → clean 后 9 字，前 5 字 叉匙声笑声
    Whisper 差 史 生 | 孝 生 | 样 样 不 少   （差 252.733 起，样 254.323 起）
    上一行 十五位房客谈天论地， 八个字**全部直接命中**，最后一个字 地 收到 252.403
    这一行自己的语音窗（lib/caption_speech.audible_windows）= (252.733, 254.92)
    交付轴行首 = 252.733 —— 正好落在自己开口的那一帧上

也就是说轴是对的，**证人**是错的：它把行首钉在下一个直接命中字（样@254.323）上，于是把
「这一行开头的字被听岔了」当成「这一行归属错了」。Python 侧早就有这条规则（任务 #56 落地的
`align()` 按位对位：两侧悬空**字数相等**才敢一一对上，不相等宁可内插），Node 侧的证人却没有
它的行首镜像 —— AGENTS.md 当时把这条范围写成「Node 侧没有要修的插值分支」，那说的是**行尾**
（误听的尾巴停在最后一个命中字，不编时刻）；行首是另一面，它会把正确的轴判成假红。

同一个形状此前已经误伤过两次，两次都是改证人而不是放宽判据：《反抗者》行 39（token 首字符
带 BPE 空格 → 1.541s，改成与 Python 同字母表后 1.18s）、《雨季不再来》行 16（gapOnset 量错
基准 → 2.15s，改 end-to-onset 后 1.32s）。这条是第三种形状。

六个对照，三个方向都必须跑：
  ① 健康轴跑绿——回收不许把好轴判红；
  ② **停在下一个命中字**这一被否决的形状必须照样跑红，且报出的差恰是 +1.59s。这一条同时是
     数值证人：回收后的参考行首真的落在 252.73（254.32−252.73=1.59），不是把该行豁免掉了；
  ③ 多秒级整段漂移（《茶馆》那种静音铺满轴的 −16s 级）照样跑红——证人的牙没被磨平；
  ④「无人争抢」前提：删掉上一行末字 地 的 token，那段悬空 token 就可能属于上一行 → 不许回收，
     行 117 重新被点名（−1.59s，正是回收前那份判决）；
  ⑤ 不猜条件 gs==ga：删掉一个悬空 token 让两侧字数不等 → 不许回收，同一名点；
  ⑥ 供体一期（真目录）一字节未动。

夹具音频走 `cp -al` 硬链（15MB 配音不值得复制），asr/ 与轴 json **真复制**，因为 ④⑤ 要改 ASR、
②③ 要改轴；改的都只是夹具。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WITNESS = ["node", "scripts/create-body-timings-precise.mjs"]
DONOR = "高老头"
SCRATCH = "行首误听回收自检-高老头"
SCRATCH_DIR = ROOT / "episodes" / SCRATCH

# 行 117 的三个时刻，全部来自上面那段实测（不是调出来的数）
ROW = 117
OWN_ONSET = 252.733        # 这一行自己的开口（差@252.733）
PARKED_ONSET = 254.323     # 被否决的形状：停在下一个直接命中字（样@254.323）
PARKED_DELTA = 1.59        # 两者的差，也就是回收前那条红的读数

fails: list[str] = []


def note(ok: bool, msg: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + msg)
    if not ok:
        fails.append(msg)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_witness(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(WITNESS + [SCRATCH, "--verify", *extra],
                          cwd=ROOT, capture_output=True, text=True)


def build_fixture() -> Path:
    src = ROOT / "episodes" / DONOR
    if SCRATCH_DIR.exists():
        shutil.rmtree(SCRATCH_DIR)
    (SCRATCH_DIR / "audio").mkdir(parents=True)
    # asr/ 必须**真复制**：④⑤ 会删 token，硬链等于直接改供体的取证文件。
    # （2026-10-10 就踩过：这一行写的是 cp -al，对照 4 把《高老头》真的 body.json 删掉了
    #  地@252.103 那个 token，直到对照 6 之前先炸在 SameFileError 上。）
    shutil.copytree(src / "audio" / "asr", SCRATCH_DIR / "audio" / "asr")
    for f in sorted((src / "audio").iterdir()):
        if f.is_dir():
            continue
        if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac"):
            subprocess.run(["cp", "-al", str(f), str(SCRATCH_DIR / "audio" / f.name)], check=True)
        else:
            shutil.copy2(f, SCRATCH_DIR / "audio" / f.name)
    for csv in ("script-body.csv", "script-hook.csv"):
        if (src / csv).exists():
            shutil.copy(src / csv, SCRATCH_DIR / csv)
    if (src / "brief.json").exists():
        shutil.copy(src / "brief.json", SCRATCH_DIR / "brief.json")
    return SCRATCH_DIR / "audio" / "body-timings.json"


def drop_asr_tokens(wanted) -> int:
    """按 (text, from_ms) 精确删夹具 ASR 里的 token；只动 asr/body.json 这一份副本。"""
    p = SCRATCH_DIR / "audio" / "asr" / "body.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for seg in raw["transcription"]:
        keep = []
        for t in seg.get("tokens", []):
            if wanted(t):
                n += 1
                continue
            keep.append(t)
        seg["tokens"] = keep
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return n


def set_axis_row(order: int, start: float) -> None:
    p = SCRATCH_DIR / "audio" / "body-timings.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for c in d["captions"]:
        if int(c["order"]) == order:
            c["start"] = start
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def shift_axis_rows_from(first_order: int, delay: float) -> int:
    p = SCRATCH_DIR / "audio" / "body-timings.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for c in d["captions"]:
        if int(c["order"]) >= first_order:
            c["start"] = round(float(c["start"]) + delay, 3)
            n += 1
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def named_rows(out: str, label: str = "行首") -> dict[int, float]:
    import re
    pat = re.compile(rf"^\s*行 (\d+) 交付轴{label} ([+\-][\d.]+)s", re.M)
    return {int(m.group(1)): float(m.group(2)) for m in pat.finditer(out)}


def donor_untouched(donor_audio: Path, donor_shas: dict[str, str], donor_asr_sha: str,
                    stage: str) -> None:
    """每次动过夹具就当场回查供体，别等到最后一道对照——写花供体的那一步正是炸点。"""
    moved = [f.name for f in sorted(donor_audio.iterdir())
             if f.is_file() and sha(f) != donor_shas.get(f.name)]
    note(not moved, f"[{stage}] 供体 audio/ 被写花了：{moved}")
    note(sha(donor_audio / "asr" / "body.json") == donor_asr_sha,
         f"[{stage}] 供体 asr/body.json 必须一字节未动（删 token 只发生在夹具里）")


def main() -> int:
    donor_audio = ROOT / "episodes" / DONOR / "audio"
    if not (donor_audio / "asr" / "body.json").exists() or not (donor_audio / "body-timings.json").exists():
        print(f"SKIP：没有 {DONOR} 的 ASR 或轴")
        return 0
    donor_shas = {f.name: sha(f) for f in sorted(donor_audio.iterdir()) if f.is_file()}
    donor_asr_sha = sha(donor_audio / "asr" / "body.json")

    axis = build_fixture()
    try:
        print(f"[夹具] {DONOR} → episodes/{SCRATCH}（音频硬链，asr/ 与轴真复制）")
        donor_untouched(donor_audio, donor_shas, donor_asr_sha, "夹具建好")

        print("[对照 1] 健康轴 → 期望 exit 0，且行 117 不被点名")
        p = run_witness()
        note(p.returncode == 0, f"健康轴应绿，实得 rc={p.returncode}\n{p.stdout}\n{p.stderr}")
        note(ROW not in named_rows(p.stdout), f"行 {ROW} 不该被点名：{named_rows(p.stdout)}")

        set_axis_row(ROW, PARKED_ONSET)
        print(f"[对照 2] 行 {ROW} 行首停在下一个命中字 {PARKED_ONSET} → 期望 exit 1 且 Δ=+{PARKED_DELTA}s")
        p = run_witness()
        rows = named_rows(p.stdout)
        note(p.returncode == 1, f"被否决的形状应拦下，实得 rc={p.returncode}\n{p.stdout}")
        note(ROW in rows, f"行 {ROW} 必须被点名（回收不是豁免）：{rows}")
        note(ROW in rows and abs(rows[ROW] - PARKED_DELTA) < 0.02,
             f"点名读数应约为 +{PARKED_DELTA}s（证明参考行首落在 {OWN_ONSET}），实得 {rows.get(ROW)}")
        set_axis_row(ROW, OWN_ONSET)

        n = shift_axis_rows_from(100, 3.0)
        print(f"[对照 3] 行 100 起整段行首推迟 3.0s（{n} 行，《茶馆》那种漂移） → 期望 exit 1")
        note(n >= 20, f"脏化至少要动 20 行，实得 {n}")
        p = run_witness()
        note(p.returncode == 1, f"多秒级漂移应照样看得见，实得 rc={p.returncode}\n{p.stdout}")
        note("行归属没有漂移" not in p.stdout, "漂移时不许打「行归属没有漂移」")
        shutil.copy2(donor_audio / "body-timings.json", axis)

        print("[对照 4] 删掉上一行末字「地」的 token（无人争抢前提不成立） → 期望不许回收")
        k = drop_asr_tokens(lambda t: t.get("text") == "地"
                            and str(t["timestamps"]["from"]).endswith("12,103"))
        note(k == 1, f"应恰好删到 1 个 地@252.103 token，实得 {k}（供体 ASR 结构变了）")
        donor_untouched(donor_audio, donor_shas, donor_asr_sha, "对照 4 删 token 之后")
        p = run_witness()
        rows = named_rows(p.stdout)
        note(p.returncode == 1 and rows.get(ROW) is not None and rows[ROW] < 0,
             f"这段悬空 token 有了争抢者就不许回收，行 {ROW} 应重新以 ≈−{PARKED_DELTA}s 被点名，"
             f"实得 rc={p.returncode} 行{ROW}={rows.get(ROW)}")
        shutil.copy2(donor_audio / "asr" / "body.json", SCRATCH_DIR / "audio" / "asr" / "body.json")

        print("[对照 5] 删掉一个悬空 token 让 gs≠ga（宁可内插不可编时刻） → 期望不许回收")
        k = drop_asr_tokens(lambda t: t.get("text") == "史"
                            and str(t["timestamps"]["from"]).endswith("12,803"))
        note(k == 1, f"应恰好删到 1 个 史@252.803 token，实得 {k}")
        p = run_witness()
        rows = named_rows(p.stdout)
        note(p.returncode == 1 and rows.get(ROW) is not None and rows[ROW] < 0,
             f"两侧悬空字数不等就不许一一对上，行 {ROW} 应重新被点名，实得 rc={p.returncode} "
             f"行{ROW}={rows.get(ROW)}")

        print("[对照 6] 供体一字节未动")
        moved = [f.name for f in sorted(donor_audio.iterdir())
                 if f.is_file() and sha(f) != donor_shas.get(f.name)]
        note(not moved, f"供体 audio/ 被写花了：{moved}")
        note(sha(donor_audio / "asr" / "body.json") == donor_asr_sha,
             "供体 asr/body.json 必须一字节未动（删 token 只发生在夹具里）")
    finally:
        shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
        note(not SCRATCH_DIR.exists(), f"夹具目录 episodes/{SCRATCH} 应已清走")

    print(("❌ " + f"{len(fails)} 项不通过") if fails else "✅ 行首误听回收回归全部通过")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
