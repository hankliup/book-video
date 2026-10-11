#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付门禁第 13 道：正文段常驻《书名》+ 作者层的字体，问的是**上屏的那一款**。

用户 2026-10-06 提的要求是「优化正文部分视频中的书籍字体，改为微软雅黑」。这台 Mac 上没有
微软雅黑，也没有合法的分发渠道（系统字体不许打包进仓库），确认改用苹方-简 PingFang SC，
雅黑留在 `scripts/lib/title-font.json` 的优先级第一位——装了就在本机生效。

为什么"改了 lib"不等于"改了字体"，因而需要一道门：

  ① 字体名解析本身就是一次静默回退的高发地。`.ttc` 里 index 0 常常是繁体 face
     （STHeiti Medium.ttc 实测 index 0 = 黑体-繁、index 1 = 黑体-简；PingFang.ttc 里
     SC/TC/HK/MO 四种家族各占一段，SC Medium 在 index 7）——按 index 写死就是一次
     「看着没错、字形全繁体」的交付，而这条坑 AGENTS.md 在封面上已经记过一次。
     这道门不接受任何写死的 index：`title_font.resolve()` 按 (family, style) 反查，
     反查结果连同路径一起落盘成 `title-meta.json`，门比的是那一份**渲染时**的记录。
  ② 本机实际交付的像素由 Pillow 画（title.png），而预览由 Chrome 的 CSS 画。两边各写
     一次 font-family 迟早分叉成「Python 用了苹方、预览用了黑体」，所以两边的栈都由
     同一份 JSON 拼，这道门逐字符比对两条路径的 font-family 取值。
  ③「源改了、盘上还是旧像素」是本仓库反复出现的缺陷类（showBookMark 已在生成器里而
     hook.mp4 整段印着书名；composition-digest 记的生成器指纹比成片新）。字体同理：
     lib 改了而不回改已交付的成片，那份像素永远是黑体。所以门读渲染器现场写出的
     `title-meta.json`，它记的是渲那一帧时真正加载的 face。

判红的情形（`--require` 之外一律按同样口径，只是没落盘证人的存量期数报 ⚠️ 无从证明）：
  · 解析落到 fallback（两家都不在盘上）——用户点名的就是那两家；
  · 解析到的 face 家族/样式里带 TC/HK/MO/繁/港/澳 标记（繁体 face 排简体文案）；
  · 本期书名/作者文案里有任何一个字在该 face 上画不出墨（缺字形 = 屏上是空白或豆腐块）；
  · 两条渲染路径的 font-family 取值与 JSON 的 priority 顺序不一致；
  · `title-meta.json` 记的 face ≠ 当前解析结果（书名层是别款字体渲的，重渲本期才消）；
  · title.png 的 sha256 ≠ 证人里记的那份（中间产物被动过或没跟着重渲）；
  · 字幕那一路绕开 title_font 直接写死字体路径（2026-10-07 授权「重新优化视频字幕字体」后
    字幕与书名层同源解析；STHeiti 常量回流也要红）；
  · 共享片头的 .page-title/.page-author 被动过（2026-10-05 裁决「恢复原来的固定开场白
    效果」，片头的字体也算它的观感）。

最后一行是结论，`check-episode-delivery.py` 读它。
退出码：0 通过（含 ⚠️ 无从证明的存量期数）；1 有任一判红项。
跑法：python3 scripts/check-title-font.py "<书名>" [--tmp <渲染中间目录>] [--require]
"""
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))

import title_font as _tf  # noqa: E402

RENDERERS = {
    "render_scene_map_template_fallback.py": os.path.join(ROOT, "scripts", "render_scene_map_template_fallback.py"),
    "create-episode-preview.mjs": os.path.join(ROOT, "scripts", "create-episode-preview.mjs"),
}
INTRO_TEMPLATE = os.path.join(ROOT, "templates", "shared-video-template", "intro", "index.html")
PY_LIB = os.path.join(ROOT, "scripts", "lib", "title_font.py")
JS_LIB = os.path.join(ROOT, "scripts", "lib", "title-font.mjs")


def strip_js_comments(src):
    """去整行注释：守卫扫的是「代码里到底怎么写的」，把解释当成代码就变成假警报制造机。"""
    keep = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        keep.append(line)
    return "\n".join(keep)


def _shown(path):
    """证人路径怎么印：仓库内给相对路径，仓库外（/tmp、临时目录）给绝对路径。
    一长串 ../.. 是没法照着去开文件的。"""
    if not path:
        return "（找不到渲染中间目录）"
    rel = os.path.relpath(path, ROOT)
    return rel if not rel.startswith("..") else path


def strip_py_comments(src):
    keep = []
    for line in src.splitlines():
        if line.strip().startswith("#"):
            continue
        keep.append(line)
    # 三引号文档串也去掉：里面写的「STHeiti」是说明，不是口径
    return re.sub(r'"""[\s\S]*?"""', "", "\n".join(keep))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--tmp", default="")
    ap.add_argument("--require", action="store_true",
                    help="要求渲染时证人存在（新渲的期用；存量期数只报 ⚠️ 无从证明）")
    args = ap.parse_args()
    ep = os.path.join(ROOT, "episodes", args.episode)
    if not os.path.isdir(ep):
        print(f"❌ 本期目录不存在：{ep}")
        sys.exit(1)

    fails = []
    cfg = _tf.load()
    hit = _tf.resolve(cfg)

    # ---------------------------------------------------------------- ① 解析到的 face
    if hit["source"] == "fallback":
        fails.append(f"书名层落到兜底 {hit['family']}——priority 里的 "
                     f"{'、'.join(e['family'] for e in cfg['priority'])} 一个都不在盘上。"
                     f"用户点名的就是那两家，装字体或换机器，别把兜底当成通过")
    markers = [m for m in cfg["forbidden_family_markers"]
               if m in hit["family"] or m in hit["style"]]
    if markers:
        fails.append(f"解析到 {hit['family']}/{hit['style']}，含禁止标记 {''.join(markers)} —— "
                     f"繁体/港体 face 排简体文案")
    from PIL import ImageFont
    face = ImageFont.truetype(hit["path"], 82, index=hit["index"])
    got_family, got_style = (str(x) for x in face.getname())
    if (got_family, got_style) != (hit["family"], hit["style"]):
        fails.append(f"{hit['path']} index={hit['index']} 实际是 {got_family}/{got_style}，"
                     f"与解析记录 {hit['family']}/{hit['style']} 不一致 —— index 必须由 face 名反查")
    print(f"书名层字体  {hit['family']} {hit['style']}  index={hit['index']}")
    print(f"            {hit['path']}")

    # ---------------------------------------------- ② 本期文案在这款 face 上画不画得出来
    brief_path = os.path.join(ep, "brief.json")
    texts = []
    if os.path.exists(brief_path):
        brief = json.load(open(brief_path, encoding="utf-8"))
        texts = [f"《{brief.get('display_title', '')}》", f"{brief.get('author', '')} / 著"]
    blank = []
    for line in texts:
        for ch in line:
            if not ch.strip() or ch in "《》/":
                continue
            if face.getbbox(ch) is None:
                blank.append(ch)
    if blank:
        fails.append(f"{'、'.join(sorted(set(blank)))} 在 {hit['family']} {hit['style']} 上"
                     f"量不到墨迹（本期书名/作者「{' '.join(texts)}」）—— 屏上会是空白或豆腐块")
    else:
        print(f"字形覆盖  {' '.join(texts)} 全部有墨迹")

    # ------------------------------------------------------ ③ 两条渲染路径与 JSON 同源
    stack = _tf.css_family_value(cfg)
    py_src = strip_py_comments(open(RENDERERS["render_scene_map_template_fallback.py"],
                                    encoding="utf-8").read())
    js_src = strip_js_comments(open(RENDERERS["create-episode-preview.mjs"],
                                    encoding="utf-8").read())
    if "_title_font.truetype(" not in py_src:
        fails.append("render_scene_map_template_fallback.py 的书名层不再从 lib 取字体 —— "
                     "face 会变成各写一遍的第二份真源")
    for token, why in (("ImageFont.truetype(FONT, _TITLE_SIZE)", "书名用回字幕的 FONT"),
                       ("ImageFont.truetype(FONT, 34)", "作者行用回字幕的 FONT")):
        if token in py_src:
            fails.append(f"render_scene_map_template_fallback.py 里又出现 {token} —— {why}")
    if "titleFontFamilyCss()" not in js_src:
        fails.append("create-episode-preview.mjs 的 .book-mark 不再从 lib 取 font-family")
    if "titleFontWeightCss()" not in js_src:
        fails.append("create-episode-preview.mjs 的书名层没有用 lib 的字重 —— "
                     "回到 font-weight:900 就是让浏览器合成假粗，而 Pillow 侧不会（两条路径字重不同）")
    mjs_src = strip_js_comments(open(JS_LIB, encoding="utf-8").read())
    if "title-font.json" not in mjs_src or "title-font.json" not in open(PY_LIB, encoding="utf-8").read():
        fails.append("Python 侧或 JS 侧不再读同一份 title-font.json")
    # JS 侧的栈顺序：把它 import 进 node 取实际字符串（比正则扒 JSON 可靠）
    from subprocess import run as _run
    node_out = _run(["node", "--input-type=module", "-e",
                     'import(' + json.dumps(os.path.join(ROOT, "scripts", "lib", "title-font.mjs")) +
                     ').then(m=>process.stdout.write(m.titleFontFamilyCss()+"|"+m.titleFontWeightCss()))'],
                    capture_output=True, text=True)
    js_stack = ""
    js_weight = ""
    if node_out.returncode == 0 and "|" in node_out.stdout:
        js_stack, js_weight = node_out.stdout.rsplit("|", 1)
        if js_stack != stack:
            fails.append(f"两条路径的 font-family 栈不一致：\n      PIL/JSON = {stack}\n"
                         f"      Chrome   = {js_stack}")
        if js_weight != str(int(cfg["css_font_weight"])):
            fails.append(f"Chrome 侧字重 {js_weight} ≠ JSON 的 {cfg['css_font_weight']}")
    else:
        fails.append(f"取不到 JS 侧的字体栈：{node_out.stderr.strip()[:200]}")

    # ------------------------------------------------------------ ④ 范围：字幕走 lib 同源，片头仍不许动
    # 2026-10-07 用户授权「重新优化视频字幕字体，用更为适合的字体样式」：字幕并入
    # title_font 同源解析（旧 FONT = STHeiti Medium.ttc 退役——ImageFont 默认 index 0 是
    # Heiti TC，简体文案一直在拿繁体 face 排）。钉更新为：字幕那一路必须从 lib 取字体，
    # 且 STHeiti 常量不得回流；下一次换字体必须再来一次显式授权，不许顺手改。
    if "STHeiti Medium.ttc" in py_src:
        fails.append("render_scene_map_template_fallback.py 里又出现 STHeiti 字幕常量 —— "
                     "2026-10-07 授权后字幕已并入 title_font 同源解析，回退旧字体要有新的授权")
    if "_title_font.truetype(size)" not in py_src:
        fails.append("字幕那一路不再从 title_font 取字体 —— face 会变成写死路径的第二份真源")
    intro_src = open(INTRO_TEMPLATE, encoding="utf-8").read()
    # 只认家族名，不认样式名：片头 CSS 里的 `local("STHeitiSC-Medium")` 含 "Medium"，
    # 拿 style 去扫会把一条本来无关的声明读成「片头被改了」——守门人变假警报制造机，
    # 而这种假警报最后总是被「忽略这条」处理掉，真改动反而没人看。
    intro_tokens = sorted({n for e in cfg["priority"]
                           for n in [e["family"], *e["css_names"]]})
    hit_intro = [t for t in intro_tokens if t in intro_src]
    if hit_intro:
        fails.append(f"共享片头 index.html 出现了 {'、'.join(hit_intro)} —— 用户 2026-10-05 裁决"
                     f"「恢复原来的固定开场白效果」，片头的字体也算它的观感；"
                     f"要改片头需要新的授权，别顺手「统一一下」")

    # ---------------------------------------------------- ⑤ 渲染时证人：上屏的到底是哪一款
    # 候选顺序与 check-episode-delivery.py 的 find_tmp_dir() 逐字相同（$FALLBACK_TMP →
    # tmp/<书名>_tpl → /tmp/<书名>_tpl）：两道门找不同的目录，就会出现「wrapper 把渲染器
    # 真正用的目录传进来、这道门却去另一个空目录找证人」，然后打印一句无从证明的绿。
    tmp = args.tmp or os.environ.get("FALLBACK_TMP") or ""
    if not tmp or not os.path.isdir(tmp):
        for cand in (os.path.join(ROOT, "tmp", f"{args.episode}_tpl"),
                     f"/tmp/{args.episode}_tpl"):
            if os.path.isdir(cand):
                tmp = cand
                break
        else:
            tmp = ""
    meta_path = os.path.join(tmp, "title-meta.json") if tmp else ""
    title_png = os.path.join(tmp, "title.png") if tmp else ""
    shown = _shown(meta_path)
    if meta_path and os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8"))
        mf = meta.get("font", {})
        if (mf.get("family"), mf.get("style")) != (hit["family"], hit["style"]):
            fails.append(f"渲染时书名层用的是 {mf.get('family')}/{mf.get('style')}，"
                         f"而 lib 现在解析到 {hit['family']}/{hit['style']} —— "
                         f"成片里是旧字体像素，重渲本期才消（改 lib 不回改已交付的文件，"
                         f"这是本仓库「绿灯是旧的、缺陷是新的」那一类的又一次）")
        if os.path.exists(title_png):
            sha = hashlib.sha256(open(title_png, "rb").read()).hexdigest()
            if meta.get("title_png_sha256") and sha != meta["title_png_sha256"]:
                fails.append("title.png 与证人记录的 sha256 不符 —— 中间产物被人手改过或换了一代，"
                             "证人不再能证明成片")
        else:
            fails.append(f"有证人却没有 {title_png} —— 书名层的墨迹不在盘上")
        print(f"渲染证人  {mf.get('family')} {mf.get('style')} index={mf.get('index')}"
              f"  ← {shown}")
    elif args.require:
        fails.append(f"--require 但 {shown} 不存在 —— "
                     f"这一代渲染器没写书名层证人，无法证明上屏字体")
    else:
        print(f"⚠️ 无从证明：{shown} 不存在（存量期数：那一代渲染器"
              f"还没写证人）。只报不判红，同 Pexels 溯源标记的爆炸半径控制。")

    if fails:
        for f in fails:
            print(f"❌ {f}")
        print("❌ 书名字体核对：未通过")
        sys.exit(1)
    print(f"✅ 书名字体核对：正文《书名》+作者层 = {hit['family']} {hit['style']}"
          f"（{os.path.basename(hit['path'])} index={hit['index']}，按 face 名反查），"
          f"两条渲染路径的 font-family 栈同源同序；字幕与书名层同源同族（2026-10-07 授权），"
          f"共享片头字体未被波及")
    sys.exit(0)


if __name__ == "__main__":
    main()
