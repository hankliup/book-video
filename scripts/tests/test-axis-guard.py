#!/usr/bin/env python3
"""渲前轴闸的回归：证明「轴红」在渲染**之前**就能被判出来，而且修轴的那一轮真的能修好。

为什么钉这一条（2026-10-08 第五批）：夜莺与玫瑰 / 呐喊 / 动物农场 三期各花约十分钟渲完三段，
才在第七道门「逐行盖住自身语音」翻红。红的不是像素、不是装配、不是选型，是**轴**——那三期的
`audio/body-timings.json` 根本没跑过 snap/audit 两道 pass（audit dry-run 报 `changes 34`、
`before.late 2→0`；snap 之后 max |Δstart| 0.98s），而整条渲染链上没有任何一步在渲染之前问过
轴一句。夜莺与玫瑰更绕一层：轴在 11:13 补好了，成片却是 11:06 按补之前的轴渲的，交付轨
`caption-track.json` 于是和轴对不上（行63 轴 261.35 起 / 轨 262.05 起），第七道门量的是轨，
它红得完全正确，可真正的修法只是「拿现在这份轴重渲一次」——十分钟已经花掉了。

`scripts/check-axis-before-render.py` 把这件事提前：三道对账各 ~0.5–2s，红的就按 AGENTS.md 的
既有次序（snap --apply → audit --apply ×2，跑两遍是行首规则 D/D2 必须在 C/C2 收尾之后再判一次
那条）修一轮，修完仍红就不放行。这里要证明它两个方向都真的会动：

  健康轴            → 三道全绿、exit 0            （否则这道闸永远拦，等于又一个假红）
  行首推迟 0.45s    → 判红、exit 1                （否则这道闸不拦任何东西，是走过场）
  脏轴 + 修轴臂     → 修完绿、exit 0              （否则「先修再渲」是把十分钟换成一句报错）

第三组还专门记录一条**非空转**细节：纯行首推迟时 `token 派生` 那道判据是**绿**的——0.45s 落在
它的真中位容差里，合法的 snap 位移本来就有这么大（《三体1·地球往事》0.36s、《你的夏天还好吗》
0.60s）。所以红必须来自另外两道；哪天只有第一道报红，说明阈值被人改过，这里的断言会先炸。

夹具用 `cp -al` 硬链整份音频（本机 15MB 配音，复制一遍不值得），但 `body-timings.json` 一律
真复制，因为修轴臂会原地写它——硬链过去就是把《动物农场》的轴给改了。跑完再核对真目录的
sha256 一个字节没动。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ["python3", "scripts/check-axis-before-render.py"]
DONOR = "动物农场"
SCRATCH = "轴闸自检-动物农场"
SCRATCH_DIR = ROOT / "episodes" / SCRATCH

fails: list[str] = []


def note(ok: bool, msg: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + msg)
    if not ok:
        fails.append(msg)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_guard(name: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(GUARD + [name, *extra], cwd=ROOT, capture_output=True, text=True)


def build_fixture() -> Path:
    """音频硬链（本机 15MB 配音复制一遍不值得），**其余一律真复制**。

    只有 *.mp3 / asr/ 是只读的；轴 json 会被修轴臂原地改写，连 json 的 *.presnap /
    *.pre-audit 备份也一样会被重新写—— snap 落盘时先把自己的旧轴拷成 .presnap，
    那份备份要是硬链到供体上，就是在动《动物农场》的取证文件。所以只链大文件。
    """
    if SCRATCH_DIR.exists():
        shutil.rmtree(SCRATCH_DIR)
    SCRATCH_DIR.mkdir(parents=True)
    src = ROOT / "episodes" / DONOR
    (SCRATCH_DIR / "audio").mkdir()
    if (src / "audio" / "asr").exists():
        subprocess.run(["cp", "-al", str(src / "audio" / "asr"), str(SCRATCH_DIR / "audio" / "asr")],
                       check=True)
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


def doctor(axis: Path, delay: float = 0.45) -> int:
    """把若干行的行首按原样**推迟** delay 秒——正是《动物农场》行23/79 真实穿帮的形状。"""
    d = json.loads(axis.read_text(encoding="utf-8"))
    n = 0
    for i, c in enumerate(d["captions"]):
        if (i + 1) % 10 == 3 and c["end"] - c["start"] > delay + 0.9:
            c["start"] = round(c["start"] + delay, 2)
            n += 1
    axis.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def witness(out: str, label: str) -> str | None:
    for line in out.splitlines():
        if f"｜" in line and label in line:
            return line.strip()
    return None


def main() -> int:
    donor_axis = ROOT / "episodes" / DONOR / "audio" / "body-timings.json"
    if not donor_axis.exists():
        print(f"SKIP：没有 {donor_axis.relative_to(ROOT)}")
        return 0
    # 供体 audio/ 里**每一个**文件都得原样：轴 json 会被修轴臂写，*.presnap / *.pre-audit
    # 是它的取证备份，一样不许动。硬链只许给只读的 mp3 和 asr/。
    donor_shas = {f.name: sha(f) for f in sorted((donor_axis.parent).iterdir()) if f.is_file()}

    axis = build_fixture()
    try:
        print(f"[夹具] episodes/{DONOR} 的轴复制到 episodes/{SCRATCH}（音频硬链，未复制 15MB）")

        # 健康基线在**夹具**上取，不在供体上断言：供体要是哪天落了一版没跑 snap/audit 的轴，
        # 那条红恰恰是这道闸要拦的东西，不该变成回归测试自己的失败。所以夹具先量一次，
        # 不绿就在夹具上走修轴臂拿到绿，再拿这份绿当对照 1 的输入。
        base = run_guard(SCRATCH, "--no-repair")
        if base.returncode != 0:
            print("[夹具] 供体轴本身没跑过 snap/audit —— 先在夹具上走修轴臂取健康基线")
            note(run_guard(SCRATCH).returncode == 0,
                 "夹具修完应绿（供体轴脏不等于这道闸坏，但修轴臂必须真的能修）")
        p = run_guard(SCRATCH, "--no-repair")
        print("[对照 1] 健康轴 → 期望 exit 0")
        note(p.returncode == 0, f"健康轴应放行，实得 rc={p.returncode}\n{p.stdout}")
        note("✅ 轴闸通过" in p.stdout, "健康轴应打「✅ 轴闸通过」")
        note(p.stdout.count("❌") == 0, "健康轴三道判据都不该有 ❌")

        n = doctor(axis)
        print(f"[对照 2] {n} 行行首推迟 0.45s → 期望 exit 1")
        note(n >= 3, f"脏化夹具至少要有 3 行被动到，实得 {n}（供体轴的行窗口结构变了）")
        p = run_guard(SCRATCH, "--no-repair")
        note(p.returncode == 1, f"脏轴应拦下，实得 rc={p.returncode}\n{p.stdout}")
        note("❌ 轴闸不通过" in p.stdout, "脏轴应打「❌ 轴闸不通过」")
        tok = witness(p.stdout, "token 派生")
        cov = witness(p.stdout, "逐行覆盖")
        bnd = witness(p.stdout, "能量边界")
        note(tok is not None and "✅" in tok,
             f"纯行首推迟时 token 派生应保持绿（红来自另外两道）：{tok}")
        note(cov is not None and "❌" in cov, f"逐行覆盖必须看见脏轴：{cov}")
        note(bnd is not None and "❌" in bnd, f"能量边界必须看见脏轴：{bnd}")

        print("[对照 3] 脏轴 + 修轴臂 → 期望修完放行")
        p = run_guard(SCRATCH)
        note(p.returncode == 0, f"修轴后应放行，实得 rc={p.returncode}\n{p.stdout}\n{p.stderr}")
        note("snap --apply → audit --apply ×2" in p.stdout,
             "修轴次序必须走 AGENTS.md 那条（snap → audit ×2），不是另起一套")
        note("修轴后才绿" in p.stdout, "修轴后才绿要说出来——它等于「本期像素必须重渲」")

        note(sha(donor_axis) == donor_shas[donor_axis.name],
             "供体《动物农场》的轴必须一字节未动（修轴臂只写夹具那一份）")
        moved = [n for n, h in donor_shas.items()
                 if (donor_axis.parent / n).is_file() and sha(donor_axis.parent / n) != h]
        note(not moved, f"供体 audio/ 里的取证备份也不许被写花：{moved}")
    finally:
        shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
        note(not SCRATCH_DIR.exists(), f"夹具目录 episodes/{SCRATCH} 应已清走")

    print("❌ " + f"{len(fails)} 项不通过" if fails else "✅ 渲前轴闸回归全部通过")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
