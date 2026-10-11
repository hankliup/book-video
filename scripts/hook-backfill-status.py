#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钩子回补的单一进度口径：从磁盘现算，不手维护表格。

113 期回补是跨天、跨会话的工程。进度一旦写成手工维护的 CSV，就会出现「表里写着已批、
盘上其实没有」「上一期会话标了完成、这一期会话又重做一遍」——所以状态全部从文件现推，
每条判据都是可复核的存在性/时长检查，不是回忆。

每期按六道状态推进（顺序即依赖顺序）：
  1 文案  draft   : data/hook-drafts/<书名>.json 存在
  2 审批  approved: 该草稿 status == "approved"（逐期审批的落点）
  3 配音  voice   : episodes/<书名>/script-hook.csv 已提升，且钩子配音在盘
                    （钩子.mp3 / audio/hook-voiceover.mp3 / audio/hook-voiceover-story.mp3 任一）
  4 图    images  : images-hook/ 下 >= HOOK_MIN_IMAGES 张 png/jpg
  5 封面  cover   : brief.json 的 cover_hook 非空，且 renders/封面-<书名>.png 比
                    script-hook.csv 新（旧封面没有钩子行，不能算这一步已过）
  6 成片  film    : 成片时长 ≈ 钩子配音 + 3.0s 模板 + 正文配音（±1.2s），
                    即钩子段真的在片里；只看 script-hook.csv 在不在是不够的——
                    《恶意》2026-10-01 就是文案改了、拼进去的还是旧 hook.mp4。

用法：
  python3 scripts/hook-backfill-status.py                 # 打印汇总 + 写 data/钩子回补进度.csv
  python3 scripts/hook-backfill-status.py --stage voice    # 只列「卡在配音这一步」的书名
  python3 scripts/hook-backfill-status.py --json
"""
import csv
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes")
DRAFTS = os.path.join(ROOT, "data", "hook-drafts")
OUT_CSV = os.path.join(ROOT, "data", "钩子回补进度.csv")

INTRO_TRIM = 3.0
HOOK_MIN_IMAGES = 2
FILM_TOLERANCE = 1.2

# 回补范围必须钉死，不能每次从「还没有 script-hook.csv」现推：
# 提升文案恰恰是写 script-hook.csv 这一步，现推的名单会在文案提升的瞬间把这期忘掉，
# 于是第 3~6 步（配音/图/封面/成片）永远没有归属，113 期的进度看不全。
ROSTER = os.path.join(ROOT, "data", "钩子回补范围.json")

STAGES = ("draft", "approved", "voice", "images", "cover", "film")
STAGE_CN = {"draft": "文案草稿", "approved": "文案已批", "voice": "配音+提升",
            "images": "钩子氛围图", "cover": "封面", "film": "成片含钩子段"}

# 封面这一步不能只看「封面-<书名>.png 在不在」。回补提升（promote-hook-draft.py）会同时
# 写 script-hook.csv 和 brief.json 的 cover_hook，而目录里那张封面是当初交付时出的、上面
# 根本没有钩子行——只判存在就把 20 期标成已完成，等下次出片时才发现封面是旧的。
# 所以封面的判据是「比本期钩子文案更新」：mtime 早于 script-hook.csv 的封面一律算未完成。
# 《恶意》2026-10-01 那条「钩子改了没落盘」的事故就是这个形状，只是从封面这侧露出来。
def probe_dur(path):
    if not path or not os.path.exists(path):
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 2)
    except ValueError:
        return None


def first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def episode_state(name):
    d = os.path.join(EP, name)
    hook_csv_p = os.path.join(d, "script-hook.csv")
    hook_csv = os.path.exists(hook_csv_p)
    draft_p = os.path.join(DRAFTS, f"{name}.json")
    draft = None
    if os.path.exists(draft_p):
        try:
            draft = json.load(open(draft_p, encoding="utf-8"))
        except json.JSONDecodeError as exc:
            draft = {"_error": f"草稿 JSON 解析失败：{exc}"}
    hook_voice = first_existing([
        os.path.join(d, "audio", "hook-voiceover-story.mp3"),
        os.path.join(d, "audio", "hook-voiceover.mp3"),
        os.path.join(d, "钩子.mp3"),
        os.path.join(d, "钩子配音.mp3"),
    ])
    body_voice = first_existing([
        os.path.join(d, "audio", "body-voiceover.mp3"),
        os.path.join(d, "audio", "body-voiceover-story.mp3"),
        os.path.join(d, "正文配音.mp3"),
        os.path.join(d, "配音.mp3"),
    ])
    hook_imgs = glob.glob(os.path.join(d, "images-hook", "*.png")) + \
        glob.glob(os.path.join(d, "images-hook", "*.jpg"))
    cover = first_existing(glob.glob(os.path.join(d, "renders", f"封面-{name}.png")))
    final = first_existing(glob.glob(os.path.join(d, "renders", "*-final.mp4")))

    brief = {}
    bp = os.path.join(d, "brief.json")
    if os.path.exists(bp):
        try:
            brief = json.load(open(bp, encoding="utf-8"))
        except json.JSONDecodeError:
            brief = {}

    # 封面必须晚于本期钩子文案：早于它的那张是交付时出的旧图，上面没有钩子行。
    cover_fresh = bool(cover) and (
        not hook_csv or
        os.path.getmtime(cover) >= os.path.getmtime(hook_csv_p) - 1)
    st = {
        "draft": draft is not None and "_error" not in draft,
        "approved": bool(draft and draft.get("status") == "approved"),
        "voice": hook_csv and hook_voice is not None,
        "images": len(hook_imgs) >= HOOK_MIN_IMAGES,
        "cover": bool(brief.get("cover_hook")) and cover_fresh,
    }
    # 成片：钩子 + 模板 + 正文三段之和能不能对上
    fd, hd, bd = probe_dur(final), probe_dur(hook_voice), probe_dur(body_voice)
    expect = None
    if None not in (hd, bd):
        expect = round(hd + INTRO_TRIM + bd, 2)
    st["film"] = bool(fd and expect and abs(fd - expect) <= FILM_TOLERANCE)
    st["_dur"] = {"final": fd, "hook": hd, "body": bd, "expect": expect}
    st["_counts"] = {"hook_imgs": len(hook_imgs), "hook_csv": hook_csv}
    st["_draft_status"] = (draft or {}).get("status", "-")
    notes = [(draft or {}).get("_error", "")]
    if cover and not cover_fresh:
        notes.append("封面早于本期钩子文案，重出（旧图上没有钩子行）")
    st["_block"] = "；".join(n for n in notes if n)
    return st


def current_hookless():
    out = []
    for name in sorted(os.listdir(EP)):
        d = os.path.join(EP, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        if os.path.exists(os.path.join(d, "script-body.csv")) and not \
                os.path.exists(os.path.join(d, "script-hook.csv")):
            out.append(name)
    return out


def roster_or_detect():
    """有钉死的名单就用名单；没有就把当前「无钩子」的期钉成名单并落盘。"""
    if os.path.exists(ROSTER):
        books = json.load(open(ROSTER, encoding="utf-8"))["books"]
        extra = [b for b in current_hookless() if b not in books]
        if extra:
            print(f"⚠️ 有 {len(extra)} 期不在钉死名单里（新出的两段结构期？）：{'、'.join(extra[:5])}"
                  f"{'…' if len(extra) > 5 else ''}\n   跑 --pin 把它们并入名单")
        missing = [b for b in books if not os.path.isdir(os.path.join(EP, b))]
        if missing:
            print(f"⚠️ 名单里有 {len(missing)} 期目录不存在：{'、'.join(missing[:5])}")
        return books
    books = current_hookless()
    return books


def pin():
    books = sorted(set(current_hookless()) | set(
        json.load(open(ROSTER, encoding="utf-8"))["books"] if os.path.exists(ROSTER) else []))
    json.dump({"note": "钩子回补工程的范围名单；进度六道全在这份名单上算，不随 script-hook.csv 出现而缩小。",
               "pinned_at": "2026-10-02",
               "total": len(books), "books": books},
              open(ROSTER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已钉死回补范围：{len(books)} 期 → {os.path.relpath(ROSTER, ROOT)}")


def next_stage(st):
    """六道按依赖顺序，第一个没过的就是这期的下一步。"""
    for s in STAGES:
        if not st[s]:
            return s
    return "done"


def main():
    args = sys.argv[1:]
    if "--pin" in args:
        pin()
        return
    as_json = "--json" in args
    only = None
    if "--stage" in args:
        only = args[args.index("--stage") + 1]

    rows = []
    for name in roster_or_detect():
        st = episode_state(name)
        rows.append({"book": name, "next": next_stage(st),
                     **{s: st[s] for s in STAGES},
                     "hook_imgs": st["_counts"]["hook_imgs"],
                     "draft_status": st["_draft_status"],
                     "final": st["_dur"]["final"], "expect": st["_dur"]["expect"],
                     "block": st["_block"]})

    if only:
        for r in rows:
            if r["next"] == only:
                print(r["book"])
        return

    if not as_json:
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["书名", "下一步"] + [STAGE_CN[s] for s in STAGES]
                       + ["钩子图数", "草稿状态", "成片时长", "期望时长", "阻塞"])
            for r in rows:
                w.writerow([r["book"], STAGE_CN.get(r["next"], "已完成")]
                           + ["Y" if r[s] else "-" for s in STAGES]
                           + [r["hook_imgs"], r["draft_status"],
                              r["final"] or "", r["expect"] or "", r["block"]])
        print(f"已写 {os.path.relpath(OUT_CSV, ROOT)}（{len(rows)} 期）\n")
        print("卡点分布（下一步该做什么）")
        order = list(STAGES) + ["done"]
        for s in order:
            n = sum(1 for r in rows if r["next"] == s)
            if n:
                print(f"  {STAGE_CN.get(s, '三段齐全'):12} {n:3} 期")
        print(f"\n六道全过：{sum(1 for r in rows if r['next'] == 'done')} / {len(rows)}")
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
