# -*- coding: utf-8 -*-
"""正文段常驻《书名》+ 作者层的字体解析入口（Python 侧，唯一真源是 title-font.json）。

约定和 `disclaimer.py` / `subtitle-punctuation.json` 一样：字体只写一次，谁要用谁 import。
它存在的理由不是「少写一行常量」，而是这台机器上**没有微软雅黑**——用户 2026-10-06 点名的
字体必须现场解析，而不是在代码里写死一个家族名然后悄悄落回黑体：

  · 写死家族名 ⇒ 装了就中、没装就静默回退，而两条渲染路径（Pillow 画 title.png /
    Chrome 渲 .book-mark）回退到的还未必是同一个 face；
  · 写死 .ttc 的 index ⇒ 正是 AGENTS.md 封面那条踩过的坑（STHeiti Medium.ttc 的 index 0
    是黑体-**繁**，拿简体文案排出繁体字形）。这里 index 一律由 face 名反查得到，
    不许凭记忆填，`resolve()` 会把命中的 (family, style) 连同路径一起交回去，
    门禁与回归测试据此判定「上屏的到底是不是请求的那一款」。

解析顺序 = title-font.json 的 priority（雅黑 → 苹方-简），全部落空才用 fallback，
而 fallback 命中会被 `check-title-font.py` 报成 ❌ 回退（用户要的就是那两家）。

CSS 侧的同一份读法是 `scripts/lib/title-font.mjs`：`css_family_value()` 与它的栈顺序
必须一致（回归测试按 JSON 的 priority 双向比），否则「Python 用了苹方、Chrome 用了黑体」
这种分叉又会变成「修的人说绿了、判的人说红了」。
"""
import glob
import json
import os

LIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "title-font.json")

_face_cache = {}
_resolve_cache = {}


def load(path=LIB_PATH):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    priority = cfg.get("priority")
    if not isinstance(priority, list) or not priority:
        raise ValueError("title-font.json: priority 至少要有一个个候选家族")
    for i, entry in enumerate(priority):
        for key in ("family", "style"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"title-font.json: priority[{i}].{key} 必须是非空字符串")
        for key in ("css_names", "file_globs"):
            v = entry.get(key)
            if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
                raise ValueError(f"title-font.json: priority[{i}].{key} 必须是非空字符串数组")
    fb = cfg.get("fallback") or {}
    if not fb.get("family") or not fb.get("file") or not isinstance(fb.get("index"), int):
        raise ValueError("title-font.json: fallback 必须有 family / file / index")
    if not isinstance(cfg.get("css_font_weight"), (int, float)) or cfg["css_font_weight"] <= 0:
        raise ValueError("title-font.json: css_font_weight 必须是正数（见 _css_weight_comment）")
    markers = cfg.get("forbidden_family_markers")
    if not isinstance(markers, list) or not markers:
        raise ValueError("title-font.json: forbidden_family_markers 不许为空——"
                         "拿繁体 face 排简体文案是这台机器上真实踩过的坑")
    if not cfg.get("retired_family"):
        raise ValueError("title-font.json: retired_family 是字体验收门禁的反向对照，不许缺")
    if cfg.get("scope") != "body-book-mark":
        raise ValueError('title-font.json: scope 只能是 "body-book-mark"——字幕/片头/封面的字体'
                         "各有自己的裁决，混进来就是越权改片头")
    return cfg


def candidate_files(entry, cfg=None):
    """把一个家族的 file_globs 展开成盘上真实存在的文件，顺序即优先级。

    AssetsV2 那类路径带 hash 目录，只能 glob；展开后不存在的路径直接丢掉，
    所以「装了雅黑的机器走雅黑、这台走苹方」是同一条代码路径，不是两份配置。
    """
    out = []
    for pattern in entry.get("file_globs", []):
        expanded = os.path.expanduser(pattern)
        hits = sorted(glob.glob(expanded, recursive=True)) if any(c in expanded for c in "*?[") \
            else ([expanded] if os.path.isfile(expanded) else [])
        for hit in hits:
            if hit not in out:
                out.append(hit)
    return out


def faces(path):
    """一个字体文件里的 (index, family, style) 列表，读到 PIL 报错为止。

    只信 face 名不信 index：`.ttc` 的排序属于打包方的实现细节，一次系统更新就能变。
    """
    if path in _face_cache:
        return _face_cache[path]
    from PIL import ImageFont          # 延迟 import：解析 CSS 的用例不该被 Pillow 拖住
    found = []
    idx = 0
    while idx < 64:
        try:
            f = ImageFont.truetype(path, 24, index=idx)
        except Exception:              # noqa: BLE001 —— 越界/损坏都到此为止
            break
        name = f.getname()
        found.append((idx, str(name[0]), str(name[1])))
        idx += 1
    _face_cache[path] = found
    return found


def find_face(entry, cfg=None):
    """在候选文件里按 (family, style) 找 face，返回 {path,index,family,style} 或 None。"""
    cfg = cfg or load()
    want_family = entry["family"]
    want_style = entry["style"]
    for path in candidate_files(entry, cfg):
        for idx, fam, style in faces(path):
            if fam == want_family and style == want_style:
                return {"path": path, "index": idx, "family": fam, "style": style}
    return None


def resolve(cfg=None, refresh=False):
    """按 priority 解析出本机真正会用的那一款；全落空才回退，并带 source 标出是哪一种。"""
    cfg = cfg or load()
    key = id(cfg)
    if not refresh and key in _resolve_cache:
        return _resolve_cache[key]
    markers = cfg["forbidden_family_markers"]
    for entry in cfg["priority"]:
        hit = find_face(entry, cfg)
        if not hit:
            continue
        bad = [m for m in markers if m in hit["family"] or m in hit["style"]]
        if bad:
            # 命中了家族名却带着繁体/港体标记：这是「以为拿到了」的最坏形状，直接拒绝而不是继续滑到下一个。
            raise SystemExit(f"[title-font] {hit['path']} index={hit['index']} 解析到 "
                             f"{hit['family']}/{hit['style']}，含禁止标记 {''.join(bad)} —— "
                             f"简体文案不许用繁体 face，改 title-font.json 的 style 而不是换机器")
        hit["source"] = "priority"
        hit["requested"] = f"{entry['family']}/{entry['style']}"
        _resolve_cache[key] = hit
        return hit
    fb = cfg["fallback"]
    if not os.path.isfile(fb["file"]):
        raise SystemExit(f"[title-font] priority 全落空，连兜底的 {fb['file']} 也不在盘上")
    hit = {"path": fb["file"], "index": fb["index"], "family": fb["family"],
           "style": fb.get("style", ""), "source": "fallback",
           "requested": " → ".join(f"{e['family']}/{e['style']}" for e in cfg["priority"])}
    _resolve_cache[key] = hit
    return hit


def truetype(size, cfg=None):
    """书名/作者层唯一的取字体入口：字号进、ImageFont 出，face 由 resolve() 决定。"""
    from PIL import ImageFont
    hit = resolve(cfg)
    return ImageFont.truetype(hit["path"], size, index=hit["index"])


def css_names(cfg=None):
    """CSS font-family 的取值列表，顺序 = priority 顺序 + 兜底，与 resolve() 同源。"""
    cfg = cfg or load()
    out = []
    for entry in cfg["priority"]:
        for name in entry["css_names"]:
            if name not in out:
                out.append(name)
    for name in cfg["fallback"].get("css_names", []):
        if name not in out:
            out.append(name)
    return out


def css_family_value(cfg=None):
    """拼成一条 CSS font-family 值。加引号的规则与 title-font.mjs 的 quote() 逐字相同
    （含空格 / 连字符 / 非 ASCII 就加引号）——两份拼法不一样，回归测试就会在一个本来
    等价的写法上报红，而那种红最后总是被「算了，忽略这条」处理掉。"""
    cfg = cfg or load()
    return ", ".join(_quote(name) for name in css_names(cfg)) + ", sans-serif"


def _quote(name):
    needs = any(c.isspace() for c in name) or "-" in name or any(ord(c) > 127 for c in name)
    return f'"{name}"' if needs and not name.startswith('"') else name


def css_font_weight(cfg=None):
    cfg = cfg or load()
    return int(cfg["css_font_weight"])


def describe(hit=None, cfg=None):
    hit = hit or resolve(cfg)
    tag = "兜底" if hit["source"] == "fallback" else "按优先级"
    return (f"{tag}命中 {hit['family']} {hit['style']}  index={hit['index']}  "
            f"file={hit['path']}")


if __name__ == "__main__":
    import json as _json
    cfg = load()
    hit = resolve(cfg)
    print(_json.dumps({**hit, "css_family": css_family_value(cfg),
                       "css_font_weight": css_font_weight(cfg)}, ensure_ascii=False, indent=2))
    print(describe(hit, cfg))
