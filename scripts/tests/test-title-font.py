#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正文书名层字体（`scripts/lib/title-font.json`）的回归。

用户 2026-10-06 的要求是「优化正文部分视频中的书籍字体，改为微软雅黑」。这台 Mac 上没有
微软雅黑也没有合法取得渠道，确认改用苹方-简，雅黑留在 priority 第一位。这条改动看起来
只是「换一个字体名」，而它有三个只有字体才有的静默失败面，每一个都能让「我改了字体」
这句绿话什么都不证明：

  A. `.ttc` 的 index 排序属于打包方的实现细节。STHeiti Medium.ttc 的 **index 0 是黑体-繁**，
     index 1 才是黑体-简（AGENTS.md 在封面上记过同一个坑）；PingFang.ttc 里 SC/TC/HK/MO
     四个家族各占一段，SC Medium 落在 index 7。按 index 写死＝拿繁体 face 排简体文案，
     而门禁只看代码里有 `index=…` 是查不出来的。所以 T1 用「同一个文件里两个 index 的
     face 名确实不同」来证明 `resolve()` 是按名字找的：如果它只是回显第一个 index，
     断言会当场失败。
  B. 装不到就静默回退。priority 全落空时 `resolve()` 必须把 `source` 标成 fallback，
     而门禁把它报成 ❌ 而不是绿——T4 拿一份不存在的家族造这个形状。
  C. 「lib 改了、像素没改」。书名层的墨迹由 Pillow 画成 title.png（本机交付路径），
     Chrome 那条只是预览。所以渲染器落一份**渲染时**的证人 title-meta.json，
     T5 拿它做三组正反对照：证人正确 → 绿；证人写成改造前的黑体 → 红且说「重渲本期」；
     title.png 被动过一个字节 → 红（证人不能再替成片说话）。

另外钉住**范围**：T6 证明字幕那一路走 title_font 同源解析（2026-10-07 授权「重新优化
视频字幕字体」后与书名层同族；旧 STHeiti 常量——ImageFont 默认 index 0 是 Heiti TC——
不得回流），共享片头的字体也没被顺手换掉——片头按 2026-10-05 裁决「恢复原来的固定
开场白效果」原样交付。T3 钉两条渲染路径的 font-family
栈同序同源：两边各写一次，迟早分叉成「Python 用了苹方、预览用了黑体」。

退出码：0 全部通过；1 有任何一条失败。
跑法：python3 scripts/tests/test-title-font.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LIB = os.path.join(ROOT, "scripts", "lib")
sys.path.insert(0, LIB)

import title_font as tf                                    # noqa: E402
GATE = os.path.join(ROOT, "scripts", "check-title-font.py")
PY_RENDERER = os.path.join(ROOT, "scripts", "render_scene_map_template_fallback.py")
JS_RENDERER = os.path.join(ROOT, "scripts", "create-episode-preview.mjs")
INTRO_TEMPLATE = os.path.join(ROOT, "templates", "shared-video-template", "intro", "index.html")
EPISODE = "雪国"
STHEITI = "/System/Library/Fonts/STHeiti Medium.ttc"


def strip_comments(src):
    keep = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        keep.append(line)
    src = "\n".join(keep)
    return re.sub(r'"""[\s\S]*?"""', "", src)


def main():
    fails = []
    cfg = tf.load()

    # ------------------------------------------------- T1 index 由 face 名反查，不是回显第一个
    hit = tf.resolve(cfg)
    print(f"T1 解析  {hit['family']} {hit['style']}  index={hit['index']}  "
          f"source={hit['source']}")
    if hit["source"] != "priority":
        fails.append(f"T1 priority 里的家族都没解析出来（source={hit['source']}）—— "
                     f"本机连苹方-简都拿不到，用户要的字体根本没上屏")
    if (hit["family"], hit["style"]) != (cfg["priority"][1]["family"], cfg["priority"][1]["style"]) \
            and (hit["family"], hit["style"]) != (cfg["priority"][0]["family"],
                                                  cfg["priority"][0]["style"]):
        fails.append(f"T1 解析到 {hit['family']}/{hit['style']}，不在 priority 声明的家族+样式里")
    faces = tf.faces(hit["path"])
    names = {idx: (fam, sty) for idx, fam, sty in faces}
    if names.get(hit["index"]) != (hit["family"], hit["style"]):
        fails.append(f"T1 {hit['path']} 的 index={hit['index']} 其实是 "
                     f"{names.get(hit['index'])} —— face 名与 index 对不上")
    # 非空转：这个文件里必须**存在**别的 index 是别的 face，否则「按名字找」与「回显 0」无从区分
    distinct = {v for v in names.values() if v != (hit["family"], hit["style"])}
    if len(distinct) < 2:
        fails.append(f"T1 {os.path.basename(hit['path'])} 只量到 {len(names)} 个 face，"
                     f"这条『按名字反查』的断言是空的")
    # 黑体的 index 0 是繁体：这正是「写死 index」那条路的真实后果，拿来当反例
    st_faces = dict((i, (f, s)) for i, f, s in tf.faces(STHEITI))
    if st_faces.get(0) and any(m in st_faces[0][0] + st_faces[0][1]
                               for m in cfg["forbidden_family_markers"]):
        print(f"    对照  STHeiti Medium.ttc index 0 = {st_faces[0]}（禁止标记命中）"
              f" / index 1 = {st_faces.get(1)}")
    else:
        fails.append(f"T1 STHeiti Medium.ttc 的 index 0 = {st_faces.get(0)}，"
                     f"没有繁体标记 —— AGENTS.md 记的那条坑变了，禁标记需要重新核对")

    # ------------------------------------------- T2 繁体 face 被请求时必须拒绝而不是滑过去
    tf._face_cache.clear()
    try:
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys,os;sys.path.insert(0,os.path.join(%r,'scripts','lib'));"
             "import title_font as t;"
             "cfg=t.load();cfg['priority']=[{'family':%r,'style':%r,'css_names':['X'],"
             "'file_globs':[%r]}];print(t.resolve(cfg))" % (ROOT, st_faces[0][0], st_faces[0][1], STHEITI)],
            capture_output=True, text=True)
        rejected = proc.returncode != 0 and "禁止标记" in (proc.stderr + proc.stdout)
    except Exception as e:                                       # noqa: BLE001
        rejected = False
        print(f"    T2 子进程起不来：{e}")
    print(f"T2 请求繁体 face（{st_faces[0][0]}/{st_faces[0][1]}）被拒绝 = {rejected}")
    if not rejected:
        fails.append("T2 按名字请求到繁体 face 时 resolve() 没有拒绝 —— 简体文案会排出繁体字形，"
                     "而这条正是 T1 立起来的理由")

    # --------------------------------------------- T3 两条渲染路径的 CSS 栈同序同源
    py_stack = tf.css_family_value(cfg)
    node = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import(%s).then(m=>process.stdout.write(m.titleFontFamilyCss()))"
         % json.dumps(os.path.join(LIB, "title-font.mjs"))],
        capture_output=True, text=True)
    js_stack = node.stdout.strip()
    print(f"T3 PIL  = {py_stack}\n   Chrome= {js_stack}")
    if not js_stack:
        fails.append(f"T3 取不到 JS 侧字体栈：{node.stderr.strip()[:200]}")
    elif js_stack != py_stack:
        fails.append("T3 两条渲染路径的 font-family 栈不同 —— 同一份书名会由 Pillow 和 Chrome "
                     "画成两款字体，而本机交付的像素走 Pillow")
    for i, entry in enumerate(cfg["priority"]):
        if py_stack.index(entry["css_names"][0]) > (
                py_stack.index(cfg["priority"][i + 1]["css_names"][0])
                if i + 1 < len(cfg["priority"]) else len(py_stack)):
            fails.append(f"T3 priority[{i}] 的家族在 CSS 栈里排在后一位之后 —— 顺序就是优先级")

    # -------------------------------------------------- T4 装不到 ⇒ 明确是兜底，不是悄悄绿
    fake = json.loads(json.dumps(cfg))
    fake["priority"] = [{"family": "Nonexistent Face", "style": "Bold",
                         "css_names": ["NonexistentFace"], "file_globs": ["/nope/*.ttf"]}]
    tf._face_cache.clear()
    fb = tf.resolve(fake)
    print(f"T4 家族都不在盘上时  source={fb['source']}  family={fb['family']}")
    if fb["source"] != "fallback":
        fails.append(f"T4 请求一个不存在的家族却拿到 source={fb['source']} —— "
                     f"回退没有被标出来，门禁就分不出「拿到了」和「落回了黑体」")
    tf._face_cache.clear()

    # ------------------------------- T5 渲染时证人：门禁对「旧字体像素」和「证人被改」都翻红
    with tempfile.TemporaryDirectory(prefix="title-font-fixture-") as tmp:
        from PIL import Image, ImageDraw, ImageFont
        png = os.path.join(tmp, "title.png")
        img = Image.new("RGBA", (720, 960), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((70, 70), "《雪国》", font=ImageFont.truetype(
            hit["path"], 82, index=hit["index"]), fill=(255, 255, 255, 255))
        img.save(png)
        meta = {"font": {"family": hit["family"], "style": hit["style"], "file": hit["path"],
                         "index": hit["index"], "source": hit["source"]},
                "title": {"text": "《雪国》", "size": 82, "top": 70, "spacing_em": 0.04},
                "author": {"text": "川端康成 / 著", "size": 34, "top": 140, "spacing_em": 0.06},
                "title_png_sha256": hashlib.sha256(open(png, "rb").read()).hexdigest()}

        def run_gate():
            p = subprocess.run([sys.executable, GATE, EPISODE, "--tmp", tmp, "--require"],
                               capture_output=True, text=True)
            return p.returncode, (p.stdout + p.stderr)

        json.dump(meta, open(os.path.join(tmp, "title-meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        rc, out = run_gate()
        print(f"T5a 证人=当前解析结果        exit={rc}")
        if rc != 0:
            fails.append(f"T5a 证人完全正确却红了：\n{out}")
        # 反向①：证人写成改造前的黑体（= 成片还是旧像素），必须红
        meta["font"] = {"family": cfg["retired_family"], "style": "Medium",
                        "file": STHEITI, "index": 1, "source": "priority"}
        json.dump(meta, open(os.path.join(tmp, "title-meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        rc, out = run_gate()
        print(f"T5b 证人写成改造前的 {cfg['retired_family']}  exit={rc}"
              f"  {'✅命中' if '重渲本期' in out else '❌没命中'}「重渲本期」")
        if rc == 0:
            fails.append("T5b 书名层是改造前的黑体、门禁却绿 —— 这道门证明不了「字体进了成片」")
        if "重渲本期" not in out:
            fails.append("T5b 虽然红了，但没说「重渲本期」—— 红了却指不出修法")
        # 反向②：证人没问题，墨迹被改过一个字节 —— 证人不能再替成片说话
        meta["font"] = {"family": hit["family"], "style": hit["style"], "file": hit["path"],
                        "index": hit["index"], "source": hit["source"]}
        json.dump(meta, open(os.path.join(tmp, "title-meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        img2 = img.copy()
        img2.putpixel((300, 300), (255, 255, 255, 255))
        img2.save(png)
        rc, out = run_gate()
        print(f"T5c title.png 被动过一个像素   exit={rc}"
              f"  {'✅命中' if 'sha256' in out else '❌没命中'}「sha256 不符」")
        if rc == 0 or "sha256" not in out:
            fails.append("T5c title.png 与证人对不上却不红 —— 证人这道环节是空的")
        # 反向③：新渲的期没有证人（--require）必须红
        os.remove(os.path.join(tmp, "title-meta.json"))
        rc, out = run_gate()
        print(f"T5d --require 而证人不在      exit={rc}")
        if rc == 0:
            fails.append("T5d --require 时证人缺失却绿 —— 「无从证明」被当成通过")

    # ------------------------------- T6 范围：字幕必须走 lib 同源、片头字体不许被顺手换掉（守住授权）
    # 2026-10-07 用户授权「重新优化视频字幕字体」后，字幕并入 title_font 同源解析
    # （旧 STHeiti 常量退役——ImageFont 默认 index 0 是 Heiti TC，简体文案拿繁体 face 排）。
    # T6 钉的是新归属：字幕那一路必须 `_title_font.truetype(size)`，STHeiti 回流即红；
    # 片头仍按 2026-10-05 裁决原样交付。下一次换字体需要新的显式授权。
    py_src = strip_comments(open(PY_RENDERER, encoding="utf-8").read())
    js_src = strip_comments(open(JS_RENDERER, encoding="utf-8").read())
    intro_src = open(INTRO_TEMPLATE, encoding="utf-8").read()
    print("T6 范围  字幕走 title_font 同源且 STHeiti 已退役 = "
          f"{'_title_font.truetype(size)' in py_src and 'STHeiti Medium.ttc' not in py_src}"
          f"   片头无新家族 = {not any(n in intro_src for e in cfg['priority'] for n in [e['family'], *e['css_names']])}")
    if "_title_font.truetype(size)" not in py_src or "STHeiti Medium.ttc" in py_src:
        fails.append("T6 字幕那一路不再走 title_font 同源解析（或 STHeiti 常量回流）—— "
                     "2026-10-07 授权后字幕与书名层必须同族，换字体要有新的显式授权")
    if "_title_font.truetype(" not in py_src:
        fails.append("T6 兜底渲染器的书名层不再从 lib 取字体（代码里查不到 `_title_font.truetype(`）")
    if "titleFontFamilyCss()" not in js_src or "titleFontWeightCss()" not in js_src:
        fails.append("T6 create-episode-preview.mjs 的书名层不再从 lib 取字体栈/字重")
    if re.search(r"\.book-title[^}]*font-weight:\s*900", js_src):
        fails.append("T6 .book-title 回到 font-weight:900 —— 浏览器合成假粗而 Pillow 不会，"
                     "两条路径字重不同（title-font.json 的 _css_weight_comment）")
    for entry in cfg["priority"]:
        for name in [entry["family"], *entry["css_names"]]:
            if name in intro_src:
                fails.append(f"T6 共享片头 index.html 出现 {name} —— 2026-10-05 裁决片头原样交付，"
                             f"字体也算它的观感")

    # --------------------------------------------------------- T7 门禁自己不许是空转
    for token in ("title-meta.json", "css_family_value", "forbidden_family_markers",
                  "STHeiti Medium.ttc"):
        if token not in open(GATE, encoding="utf-8").read():
            fails.append(f"T7 门禁 scripts/check-title-font.py 里查不到 {token} —— "
                         f"这一条断言从文件里消失了")

    print()
    if fails:
        for f in fails:
            print(f"❌ {f}")
        sys.exit(1)
    print(f"✅ 书名字体回归通过：{hit['family']} {hit['style']} 的 index 由 face 名反查"
          f"（同文件另有 {len(distinct)} 款不同 face，写死 index 会拿到 "
          f"{st_faces.get(0, ('?', '?'))[0]}），请求繁体 face 被拒、装不到被标成兜底、"
          f"两条渲染路径的 font-family 栈逐字符相同，门禁对「书名层还是黑体」「墨迹与证人不符」"
          f"「新渲的期没有证人」三种形状都翻红，字幕与共享片头的字体未被波及")
    sys.exit(0)


if __name__ == "__main__":
    main()
