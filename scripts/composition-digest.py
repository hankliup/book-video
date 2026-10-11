#!/usr/bin/env python3
"""给一份 HyperFrames 工程目录算一个**内容摘要**，并在渲完之后把它连同成片指纹落盘。

为什么需要它（2026-10-05《李尔王》第四次「片头还是闪」的真源）：交付门禁量的永远是
**磁盘上的那份 mp4**，而 mp4 是若干分钟前由某个工程目录渲出来的。那段时间里工程目录如果被
改写（同期另一个会话重跑了 `create-episode-preview.mjs`、或者共享层被改之后顺手补了别的期），
烧进像素的就是**既不是旧版也不是新版**的一份中间态——十道门禁会对它全绿，因为每一条问的都是
「这张图上屏了吗」「这段窗口对吗」，没有一条问「这份像素是不是由我此刻看到的这份组成渲出来的」。
实测：那份 index.html 改于 10:10:42，渲染 10:10:58 收尾，中间正好一次改写，交付片带的还是
`.scene` 没有 `z-index` 之前的旧像素，判据①~⑤ 对它全盲（判据⑥看得见形状，但看不见「谁渲的」）。

所以这里做两件事：
  ① `digest <dir>`：把工程目录里**除 renders/ 之外**的每个普通文件按 (相对路径, 字节 sha256)
     排序后折成一个摘要。渲染前后各跑一次，两个数不一样就说明页面在渲染进行中被改写过 ——
     这次的产物既不该进门禁，也不该留着：必须重渲。由 scripts/render-hook-batch.sh 执行。
  ② `record <dir> --video <mp4> --out <json> [--also <json2>]`：渲完之后把「摘要 + 成片指纹 +
     生成器指纹」写成 `renders/<段>.provenance.json`。它是**唯一一份能把「画面里的像素」和
     「当时的组成」对上账的东西**——本仓库没有 git，产物和源码之间没有任何别的对应关系。
     生成器指纹取 scripts/create-episode-preview.mjs 与被它整份拷进工程目录的共享层文件
     （gsap.min.js 除外，那是第三方运行时，单独按字节记）。
     `--also` 是**必须**的：`--out` 落在预览目录里，而 `create-episode-preview.mjs` 下一次生成
     预览会 `rm -rf` 掉 `tmp/preview-<slug>/{hook,intro}/renders/`（AGENTS.md 明写的坑），账本跟
     着它作证的那份像素一起消失就永远读不回来。所以同一份 payload 再落一份到期数目录
     `episodes/<书名>/renders/`——交付的成片住在那里，整目录搬去备份时也带着它。
  ③ `verify <书名>`：**读**那份账。到期数目录里找 `*.provenance.json`，把记录时的生成器指纹
     和此刻的生成器指纹比一遍。对不上就说明这份交付像素是**上一代形状**渲出来的（共享层改了
     而没人重渲，正是《李尔王》《恶意》那一类），门禁量不出它，因为它问的是画面对不对。
     没有账本的文件只报「无从证明」不报红——记录始于 2026-10-05，把 118 部已交付片子一律判红
     不是缺陷清单，而是把账本的出生日当成缺陷。

摘要不含 `renders/`：那是产物本身，含进去就等于让产物给自己的来源作证。

用法：
  python3 scripts/composition-digest.py digest <工程目录>
  python3 scripts/composition-digest.py record <工程目录> --video <段.mp4> --out <provenance.json> \
      [--also <episodes/<书名>/renders/<段>.provenance.json>]
  python3 scripts/composition-digest.py verify <书名|episodes/书名>
退出码：digest/record —— 0 正常，1 目录不存在或没有可摘要的文件；
verify —— 0 没有「记录之后生成器又被改过」的段（含无记录），1 有。
"""
import argparse
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 工程目录里这些子树是**产物**或安装物，不参与「组成」的定义
SKIP_DIRS = {"renders", "node_modules", ".cache", "tmp", "work"}
# 第三方运行时的字节不该算进「本期组成」——它变了是依赖升级，不是有人改了画面
THIRD_PARTY = {"gsap.min.js"}


def sha256_file(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def entries(comp_dir):
    """[(相对路径, 文件 sha256)]，按路径排序 —— 排序是为了让摘要与目录遍历顺序无关。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(comp_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, comp_dir)
            if os.path.basename(rel) in THIRD_PARTY:
                continue
            out.append((rel, sha256_file(full)))
    return out


def digest(comp_dir):
    es = entries(comp_dir)
    if not es:
        raise SystemExit(f"工程目录里没有任何可摘要的文件：{comp_dir}")
    h = hashlib.sha256()
    for rel, sha in es:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(sha.encode("ascii"))
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}", es


def generator_fingerprint():
    """渲这份工程的那个**生成器**，以及它读的共享层真源。

    只记本期组成是不够的：`create-episode-preview.mjs` 改了而没人重新生成工程目录，
    落盘的 index.html 还是旧形状（《恶意》2026-10-01 那版 `showBookMark:false` 已经在生成器里、
    成片里却整段印着书名，就是这一类）。指纹把这条对账做成可能。
    """
    watched = ["scripts/create-episode-preview.mjs",
               "scripts/lib/intro-template.json",
               "scripts/lib/title-font.json",
               "scripts/lib/title_font.py",
               # 封面层的动效与时刻和字体同一性质：改的是 lib，而成片里烘的是**渲染那一刻**
               # 逐帧算出的缩放轨迹。2026-10-08 用户第二次要求「放大过程不流畅，抖动得厉害」
               # 之后，整数取整装置换成浮点仿射、行程 1.00→1.12 换成 0.82→1.00，而静态叠层
               # 字节一个都没变——不把这两个文件钉进生成器指纹，账本就只能说「生成器一致」
               # 而盘上还是上一版抖动的像素（同 title-font.json 的理由）。
               "scripts/lib/book-cover.json",
               "scripts/lib/book_cover.py",
               "scripts/render_scene_map_template_fallback.py"]
    out = {}
    for rel in watched:
        full = os.path.join(ROOT, rel)
        out[rel] = sha256_file(full) if os.path.isfile(full) else None
    return out


def cmd_digest(args):
    dig, es = digest(args.comp_dir)
    print(dig)
    if args.verbose:
        for rel, sha in es:
            print(f"  {sha[:16]}  {rel}")
    return 0


def _write(payload, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")


def cmd_record(args):
    dig, es = digest(args.comp_dir)
    if not os.path.isfile(args.video):
        raise SystemExit(f"成片不在：{args.video} —— 没有产物可记账")
    payload = {
        "composition_dir": os.path.relpath(os.path.abspath(args.comp_dir), ROOT),
        "composition_digest": dig,
        "composition_files": [{"path": r, "sha256": s,
                               "bytes": os.path.getsize(os.path.join(args.comp_dir, r))}
                              for r, s in es],
        "video": os.path.relpath(os.path.abspath(args.video), ROOT),
        "video_sha256": sha256_file(args.video),
        "video_bytes": os.path.getsize(args.video),
        "video_mtime": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                     time.localtime(os.path.getmtime(args.video))),
        "generators": generator_fingerprint(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "渲染前后各跑一次 digest；两个数不同＝页面在渲染进行中被改写过，这份产物不作数。",
    }
    _write(payload, args.out)
    print(f"[provenance] {args.out}  {dig}  ← {payload['video']}")
    if args.also:
        _write(payload, args.also)
        print(f"[provenance] 同步落一份到期数目录（预览目录会被下一次生成抹掉）：{args.also}")
    return 0


def _episode_renders(target):
    """verify 的入参可以是书名，也可以是 episodes/<书名> —— 统一回成 renders/ 目录。"""
    cand = target if os.path.isdir(target) else os.path.join(ROOT, "episodes", target)
    renders = os.path.join(cand, "renders")
    if not os.path.isdir(renders):
        renders = cand if os.path.basename(cand) == "renders" else None
    return renders


def cmd_verify(args):
    """读期数目录里的 provenance，回答「这份交付像素是哪一代形状渲的」。"""
    renders = _episode_renders(args.episode)
    if not renders:
        raise SystemExit(f"找不到期数的 renders/ 目录：{args.episode}")
    books = sorted(f for f in os.listdir(renders) if f.endswith(".provenance.json"))
    now = generator_fingerprint()
    print(f"合成摘要对账  {os.path.basename(renders)}/  {len(books)} 份记录")
    if not books:
        print("  ⚠️ 无从证明：这一期没有 provenance.json（记录始于 2026-10-05 12:48）。"
              "它不判红——没有账本不等于画面是旧的；要判它只能看像素（判据⑥ + 门禁 9）。")
        return 0
    stale = []
    for name in books:
        with open(os.path.join(renders, name), encoding="utf-8") as f:
            rec = json.load(f)
        seg = rec.get("video") or name
        gen = rec.get("generators") or {}
        diffs = [rel for rel, sha in now.items() if gen.get(rel) != sha]
        # 记账那份 mp4 还在不在、还是不是那些字节：在而不同＝记录之后有人直接换了产物
        # （手工 mv、别的渲染路径覆盖），这份账就不再描述磁盘上的东西了。
        video = os.path.join(ROOT, rec.get("video", "")) if rec.get("video") else None
        drifted = bool(video) and os.path.isfile(video) and \
            sha256_file(video) != rec.get("video_sha256")
        if video and not os.path.isfile(video):
            print(f"  ℹ️ {name} 记录的那份 {seg} 已经不在了（预览目录会被下一次生成抹掉，"
                  f"这是预期的）；生成器指纹仍然比对。")
        if diffs or drifted:
            stale.append((name, seg, diffs, drifted))
            print(f"  ❌ {name}  记录于 {rec.get('recorded_at', '?')}  ← {seg}")
            for rel in diffs:
                print(f"       生成器在渲染之后又被改过：{rel}")
            if drifted:
                print("       而且那份 mp4 的字节和记录时不同了：账本描述的已经不是磁盘上的产物")
        else:
            print(f"  ✅ {name}  记录于 {rec.get('recorded_at', '?')}，"
                  f"生成器与渲染时逐字节相同（{rec['composition_digest'][:19]}…）")
    if stale:
        print(f"  → {len(stale)} 段带的是**上一代形状**的像素：共享层改了而没人重渲，"
              "十道门禁全绿也不会变。修法只有重渲那一段，改判据或压暗都不算。")
        return 1
    print("  → 每一份记录的生成器都和此刻一致：交付像素就是当前形状渲出来的。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest", help="算工程目录的内容摘要（渲染前后各一次）")
    d.add_argument("comp_dir")
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(func=cmd_digest, comp_dir=True)
    r = sub.add_parser("record", help="把摘要与成片指纹写成 provenance.json")
    r.add_argument("comp_dir")
    r.add_argument("--video", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--also", help="同一份 payload 再落一处（期数目录，预览目录会被下次生成抹掉）")
    r.set_defaults(func=cmd_record, comp_dir=True)
    v = sub.add_parser("verify", help="读那份账：交付像素是不是当前生成器渲的")
    v.add_argument("episode")
    v.set_defaults(func=cmd_verify, comp_dir=False)
    args = ap.parse_args()
    if getattr(args, "comp_dir", False) and not os.path.isdir(args.comp_dir):
        sys.exit(f"工程目录不存在：{args.comp_dir}")
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
