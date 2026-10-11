#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一份**已批准**的钩子草稿提升到剧集目录（回补管线第 3 步的唯一入口）。

为什么要有这一步，而不是直接在 chat 里写完了手改文件：113 期、跨天、可能换会话。
手改会出现三种事故——草稿改了没落盘、brief.json 少了 cover_hook（下一次渲染封面又
退回没有钩子行的旧版，正是《恶意》2026-10-01 踩过的那条）、钩子行里带了书名号或破折号
（正文规则禁止，但钩子文件过去没有门禁）。这里把「审批通过」变成一次幂等写入，
并且先把 AGENTS.md 的钩子约束断言一遍，不合格直接红灯不写。

校验项（全部来自 AGENTS.md / playbook，不在这里另立口径）：
  · status 必须是 approved（逐期审批的落点；--force 也不能越过，只允许 --stage 干跑）
  · 钩子 2~3 行，每行 <= 24 个非空白字符（一行一个完整口语单位，供 ASR 对齐）
  · 钩子行内不得有装饰符号：《》“” ‘’ 「」 『』 ——（）() 
  · cover_hook <= 22 字（与 make_episode_cover.py 的 HOOK_CHARS 同一上限，不重写数值）
  · 钩子首句不得与正文口播首句（order 2）撞车；钩子整体不得复用正文前 5 行原句

用法：
  python3 scripts/promote-hook-draft.py "<书名>"            # 校验 + 写入
  python3 scripts/promote-hook-draft.py "<书名>" --dry-run  # 只校验，不写
  python3 scripts/promote-hook-draft.py A B C --check       # 审批前自检（不看 status，多期还查同批撞车）
  python3 scripts/promote-hook-draft.py A B C --approve="user: 全过（第三批 10 期）"
        # 把「逐期审批通过」这一步落盘（status/approved_at/approved_by），再走提升。
        # 审批口径由用户在对话里给出，Agent 只照抄，不代替用户判合格；
        # 前三批是手改 JSON 落的盘，113 期里第三步每次都要手写一遍就是事故面。
"""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes")
DRAFTS = os.path.join(ROOT, "data", "hook-drafts")

# 与 scripts/make_episode_cover.py 的 HOOK_CHARS 对齐：上限只在封面脚本里定义一次，
# 这里读它，避免两处各自写死 22 而后漂移。
_cover_src = open(os.path.join(ROOT, "scripts", "make_episode_cover.py"),
                  encoding="utf-8").read()
_m = re.search(r"^HOOK_CHARS\s*=\s*(\d+)", _cover_src, re.M)
COVER_HOOK_MAX = int(_m.group(1)) if _m else 22

# 正文行内禁止的装饰符号（钩子同规则）：书名号、各种引号、破折号、括号
FORBIDDEN = re.compile(r"[《》“”‘’「」『』〈〉（）()〔〕]|——|…{2,}")
MAX_LINE_CHARS = 24


def load_draft(book):
    p = os.path.join(DRAFTS, f"{book}.json")
    if not os.path.exists(p):
        raise SystemExit(f"没有草稿：{p}")
    return json.load(open(p, encoding="utf-8")), p


def body_rows(book):
    d = os.path.join(EP, book)
    for fname in ("script-body.csv",):
        p = os.path.join(d, fname)
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig", newline="") as fh:
                return [(int(r["order"]), (r["text"] or "").strip())
                        for r in csv.DictReader(fh) if (r.get("text") or "").strip()]
    raise SystemExit(f"{book}: 找不到 script-body.csv")


def check(book, draft, pre=False):
    problems = []
    if draft.get("status") != "approved":
        # --check 是审批**前**的自检：草稿本来就还没批，这一条不该混进来把其他判据淹掉。
        # 只有真正要写盘（不带 --check）时，未批准才是红灯。
        if not pre:
            problems.append(f"草稿 status={draft.get('status')!r}，未批准不得提升"
                            f"（本轮回补按逐期审批，请先在会话里点头）")
    lines = [l.strip() for l in draft.get("hook_lines") or [] if l.strip()]
    if not 2 <= len(lines) <= 3:
        problems.append(f"钩子 {len(lines)} 行，要求 2~3 行")
    for i, l in enumerate(lines, 1):
        n = len(re.sub(r"\s", "", l))
        if n > MAX_LINE_CHARS:
            problems.append(f"钩子第 {i} 行 {n} 字 > {MAX_LINE_CHARS}，不是一个完整口语单位：{l}")
        if FORBIDDEN.search(l):
            problems.append(f"钩子第 {i} 行含装饰符号（《》/引号/破折号/括号）：{l}")
    cover_hook = (draft.get("cover_hook") or "").strip()
    if not cover_hook:
        problems.append("缺 cover_hook：封面会退回没有钩子行的旧版")
    elif len(cover_hook) > COVER_HOOK_MAX:
        problems.append(f"cover_hook {len(cover_hook)} 字 > {COVER_HOOK_MAX}，"
                        f"make_episode_cover.py 会直接拒绝")
    golden = (draft.get("golden_line") or "").strip()
    if cover_hook and golden and cover_hook == golden:
        problems.append("封面钩子印了金句本身：封面只有看没有听，金句是答案，印上去等于泄底")
    rows = body_rows(book)
    spoken = [t for o, t in rows if o >= 2]
    if spoken and lines and lines[0] == spoken[0]:
        problems.append(f"钩子首句与正文口播首句撞车：{spoken[0]}")
    head5 = set(spoken[:5])
    dup = [l for l in lines if l in head5]
    if dup:
        problems.append("钩子复用了正文前 5 行原句，片头会和正文首句撞车：" + " / ".join(dup))
    if not (draft.get("hook_design") or "").strip():
        problems.append("缺 hook_design：5 问自检口径无处可查")
    return problems, lines, cover_hook


def promote(book, draft, lines):
    d = os.path.join(EP, book)
    version = draft.get("version") or (body_rows(book)[0][0] and "A")
    # 1) script-hook.csv —— 钩子文案真源落盘（渲染路径以它的存在与否切换三段结构）
    hp = os.path.join(d, "script-hook.csv")
    with open(hp, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["version", "order", "text"])
        for i, l in enumerate(lines, 1):
            w.writerow([version, i, l])
    # 2) 钩子源文案 钩子.md（与 5 期在制剧集同一形态，便于你直接改）
    with open(os.path.join(d, "钩子.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    # 3) brief.json 记录封面钩子与设计口径：渲染命令行传参是临时的，下次就丢
    bp = os.path.join(d, "brief.json")
    brief = json.load(open(bp, encoding="utf-8")) if os.path.exists(bp) else {}
    brief["cover_hook"] = draft["cover_hook"].strip()
    if draft.get("cover_tag"):
        brief["cover_tag"] = draft["cover_tag"].strip()
    elif "cover_tag" not in brief:
        author = brief.get("author") or ""
        brief["cover_tag"] = f"{author} / 著" if author else brief.get("display_title", book)
    brief["hook_design"] = draft["hook_design"].strip()
    brief["hook_status"] = "approved"
    tmp = bp + ".tmp"
    json.dump(brief, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, bp)
    return hp


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    pre = "--check" in sys.argv
    approve = next((a.split("=", 1)[1] for a in sys.argv[1:]
                    if a.startswith("--approve=")), None)
    if not args:
        raise SystemExit(__doc__)
    if approve is not None and (dry or pre):
        # 审批与干跑互斥：--approve 是要写盘的，配 --dry-run 只会写出一个
        # 「已批准但其实没落盘」的中间态，比没批更难查。
        raise SystemExit("--approve 不能和 --dry-run / --check 同时用：审批本身要写盘")
    if approve is not None:
        today = __import__("datetime").date.today().isoformat()
        for book in args:
            draft, dp = load_draft(book)
            draft["status"] = "approved"
            draft["approved_at"] = today
            draft["approved_by"] = approve.strip()
            tmp = dp + ".tmp"
            json.dump(draft, open(tmp, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            os.replace(tmp, dp)
        print(f"已记录审批：{len(args)} 期 → {approve}（{today}）")
    # 开头重复：写盘**之前**判红。以前这段逻辑在循环之后打印 rc=2，文件早就写完了，
    # 而且只比同批彼此（前缀完全相同才算），从不看已出货语料——所以三批各写三遍
    # 「我合上书」，一次都没拦下来，最后靠人耳在 30 期里听出来。
    # 口径不在这里重写，交给 check-hook-openers.plan_problems 一处定义。
    # 那个脚本的文件名带连字符，不能 import，所以按路径加载——加载失败就当门禁不成立
    # 会被静默跳过，所以直接报错退出：门禁看不见缺陷比误报更糟。
    import importlib.util
    _opener_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "check-hook-openers.py")
    _spec = importlib.util.spec_from_file_location("check_hook_openers", _opener_path)
    if _spec is None or _spec.loader is None:
        raise SystemExit(f"加载不了钩子开头门禁：{_opener_path}")
    openers = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(openers)
    pairs = []
    covers = openers.shipped_covers()
    for book in args:
        d = load_draft(book)[0]
        lines = [l.strip() for l in d.get("hook_lines") or [] if l.strip()]
        if len(lines) > 1:
            pairs.append((book, lines[1]))
            # 草稿里的 cover_hook 才是这一期的封面；盘上的 brief.json 是上一次的，
            # 用盘上的查就等于放行本轮、下一轮才红。
            if (d.get("cover_hook") or "").strip():
                covers[book] = d["cover_hook"].strip()
    opener_problems = openers.plan_problems(pairs, openers.shipped_corpus(), covers=covers)
    if opener_problems and "--allow-overloaded-opener" not in sys.argv:
        print("❌ 钩子第二行开头与语料撞车，未提升（改开头，别改书名）：")
        for p in opener_problems:
            print(f"   · {p}")
        print("   确实要用这个开头：加 --allow-overloaded-opener，"
              "并在草稿里留 opener_override_note 把理由写下来，别让绕过只存在于口头。")
        sys.exit(1)
    if opener_problems:
        print("⚠️ 已用 --allow-overloaded-opener 放行 "
              f"{len(opener_problems)} 条开头撞车，理由需在草稿里可查")
    exit_code = 0
    for book in args:
        draft, dp = load_draft(book)
        problems, lines, cover_hook = check(book, draft, pre=pre)
        # 同批开头撞车已由上面的 plan_problems 在写盘前拦下，这里不再重复统计一遍：
        # 两处口径早晚漂移成「一个拦一个只告警」，那正是这次要修的东西。
        if problems:
            exit_code = 1
            print(f"❌ 《{book}》草稿不合格，未提升：")
            for p in problems:
                print(f"   · {p}")
            continue
        if dry or pre:
            print(f"✅ 《{book}》草稿校验通过（{'--check 审批前自检' if pre else '--dry-run'}，未写入）")
            continue
        hp = promote(book, draft, lines)
        print(f"✅ 《{book}》钩子已提升：{os.path.relpath(hp, ROOT)} + brief.json"
              f"（cover_hook「{cover_hook}」）")
        print(f"   下一步：把钩子配音放 episodes/{book}/钩子.mp3，"
              f"然后取 images-hook/ 氛围图、出封面、重渲")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
