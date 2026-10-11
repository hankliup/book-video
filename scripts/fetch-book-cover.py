#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取「本书封面图」并落旁证：微信读书优先，取不到再走显式 URL / 本地文件。

用法：
  python3 scripts/fetch-book-cover.py "<书名>"                 # 按 brief.json 的 display_title+author 搜
  python3 scripts/fetch-book-cover.py "<书名>" --list 8         # 只看候选，不写文件
  python3 scripts/fetch-book-cover.py "<书名>" --url <https>    # 网上找到的直链
  python3 scripts/fetch-book-cover.py "<书名>" --from <本地图片> # 人工核验过的图
  python3 scripts/fetch-book-cover.py "<书名>" --book-id <id>   # 指定微信读书书目（多版次时人工选定）

产出两件，成对才算数（lib/book_cover.py 的 provenance_ok() 会核 sha256）：
  episodes/<书名>/book-cover.jpg      上屏的那张图（统一 1080 宽渐进式 JPEG）
  episodes/<书名>/book-cover.json     来源旁证：source / url / 书目 id / 命中标题作者 / 字节 sha256

为什么要旁证：AGENTS.md 对整个仓库的要求是「每一张上屏的图都能溯源」。Pexels 那套有
manifest + _provenance.json + sha256 标记链，本层是同一件事的最小版——换图不重写旁证 = 红，
「图上屏了但没人知道它是哪一版」= 红。

微信读书接口（无需鉴权，实测同 ~/.agents/skills/weread-cover-fetch）：
  GET https://weread.qq.com/web/search/global?keyword=<title>&maxIdx=0&fragmentSize=0&count=8
  返回 books[].bookInfo.{title,author,cover}；cover 是 70×101 缩略图，必须换前缀升级：
  t9_?imageMogr2/thumbnail/1080x → o_ → t9_ → t8_ → s_，取第一个宽度 ≥1080 的。
  关键词一律只传书名：拼上作者名时《史记》一类只会返回研究/解读类衍生书。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import book_cover as bc  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HDR = {"User-Agent": UA, "Referer": "https://weread.qq.com/"}
TARGET_W = int(os.environ.get("BOOK_COVER_TARGET_W", "1080"))
CFG = bc.load()


def get(url, timeout=30, proxy=None):
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
        with opener.open(urllib.request.Request(url, headers=HDR), timeout=timeout) as r:
            return r.read()
    with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=timeout) as r:
        return r.read()


def norm(s):
    return re.sub(r"[\s，,：:·\.\-—【】\(\)（）\[\]《》]", "", s or "")


def search(title, proxy=None):
    q = urllib.parse.urlencode({"keyword": title, "maxIdx": 0, "fragmentSize": 0, "count": 8})
    data = json.loads(get("https://weread.qq.com/web/search/global?" + q, proxy=proxy))
    return [b["bookInfo"] for b in data.get("books", [])]


# 外文字样版次标记：**同档降级，不是否决**。视频口播是简体中文，观众要在 1.4 秒里认出的
# 是中文版次那张书衣；但若整批候选只剩外文版次，仍然要出图（「本书长什么样」优先于
# 「哪个版次最好看」），只是把 ⚠️ 打在日志里，让人知道该用 --book-id 复核。
#
# 只收**正文是英文**的那一类。2026-10-10 全库对照里《化身博士》交付的是「化身博士：英汉对照」，
# 目检过：境内出版社的双语读本，封面主标题就是中文，它不是《小王子》那种英文版次。
# 所以「对照」「双语」一类词**不进**这张表——把它们当外文版次，会让下一次批量重取
# 把一张已经目检合格的中文书衣换成没看过的。
FOREIGN_EDITION_MARKERS = ("英文版", "英文原版", "全英文", "英文注释")


def foreign_edition(raw_title):
    return any(m in (raw_title or "") for m in FOREIGN_EDITION_MARKERS)


def title_tier(bt, nt):
    """书名匹配档位：3 完全等值 / 2 以书名开头 / 1 包含书名 / 0 不相关。"""
    if not nt:
        return 2
    if bt == nt:
        return 3
    if bt.startswith(nt):
        return 2
    if nt in bt:
        return 1
    return 0


def author_tier(ba, na, author):
    """作者匹配档位：2 互相包含 / 1 名字里任一片段命中 / 0 无关。

    档位而不是分数：「圣埃克絮佩里」与「圣埃克苏佩里」是同一人的两种译名用字，
    字符串不等，所以《小王子》中文版这一档是 0——旧写法把它算成「少 30 分」，
    于是那 30 分被外文版次拿去买通了书名档。
    `ba` 也要非空：Python 里 `"" in "圣埃克絮佩里"` 为真，微信读书偶有作者字段留空的书目，
    少了这道守卫，一条空作者记录能拿到 2 档，压过真正写对了的作者。
    """
    if na and ba and (na in ba or ba in na):
        return 2
    for part in re.split(r"\s+|·", author or ""):
        if len(norm(part)) >= 2 and norm(part) in ba:
            return 1
    return 0


def pick(books, title, author):
    """按 (书名档, 无外文字样标记, 作者档, 搜索名次) **逐档比较**，不做加法。

    2026-10-10 实测缺陷：旧写法把三个维度加成一个分数，于是作者档正好能买通书名档。
    《小王子》的搜索返回里，「小王子（英文版）」(startswith 70 + 作者全等 30 + 名次 7 = 107)
    压过了「小王子」(完全等值 100 + 作者译名用字不同 0 + 名次 6 = 106)，差 1 分——
    结果中文读书视频上屏那 1.4 秒的书衣是《The Little Prince》的英文版次。
    病根不是权重调得不准，是**一个维度可以买通另一个维度**：调权重要求每次重算边界，
    而逐档比较把「书名优先」写成了结构。名次同理，它只是同一档里的先后，不能跨档。
    """
    nt, na = norm(title), norm(author)
    scored = []
    for i, b in enumerate(books):
        bt, ba = norm(b.get("title")), norm(b.get("author"))
        scored.append((title_tier(bt, nt), 0 if foreign_edition(b.get("title")) else 1,
                       author_tier(ba, na, author), -i, i, b))
    usable = [s for s in scored if s[0] > 0]
    if not usable:
        # 旧写法会在这里返回一个书名完全不相关的命中（分数只靠作者堆起来）。
        # 现在宁可让 main() 报「没有命中」退回 --list / --book-id / --url：
        # 「本书长什么样」是一张错图最贵的代价，而它恰好是门禁量不出来的那种错。
        return None
    best = max(usable, key=lambda s: s[:4])
    if best[1] == 0:
        print(f"[weread] ⚠️ 只能命中带外文字样标记的版次：{best[5].get('title')}"
              f"（同批无中文版次可用；要换版次先 --list 看候选，再 --book-id 指定）")
    return best[5]


def hd_variants(cover):
    def swap(p):
        return re.sub(r"/s_([^/]+)$", "/" + p + r"\1", cover)
    t9 = swap("t9_")
    out = [t9 + f"?imageMogr2/thumbnail/{TARGET_W}x", swap("o_"), t9, swap("t8_"), cover]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def download_best(cover, proxy=None):
    """按清晰度候选下载，返回 (url, bytes, width)；宽度到 TARGET_W 即停。"""
    from PIL import Image
    best = None
    for u in hd_variants(cover):
        try:
            b = get(u, proxy=proxy)
        except Exception:
            continue
        if len(b) < 8000:
            continue
        try:
            im = Image.open(io.BytesIO(b))
            im.load()
        except Exception:
            continue
        if best is None or im.size[0] > best[2]:
            best = (u, b, im.size[0])
        if im.size[0] >= TARGET_W:
            break
    return best


def save_normalized(data, dest):
    """统一落地为 TARGET_W 宽的渐进式 JPEG（扩展名与真格式必须一致）。"""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    im = im.convert("RGB")
    if im.size[0] < TARGET_W:
        h = round(im.size[1] * TARGET_W / im.size[0])
        im = im.resize((TARGET_W, h), Image.LANCZOS)
    im.save(dest, "JPEG", quality=92, optimize=True, progressive=True)
    return im.size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--url", default="", help="非微信读书来源的直链（人工核验过是这本书的封面）")
    ap.add_argument("--from", dest="local", default="", help="本地图片路径")
    ap.add_argument("--book-id", default="", help="指定微信读书 bookid，跳过打分")
    ap.add_argument("--list", type=int, default=0, metavar="N", help="只打印前 N 个候选")
    ap.add_argument("--proxy", default=os.environ.get("BOOK_COVER_PROXY", ""),
                    help="走代理时用（微信读书是境内服务，默认直连）")
    ap.add_argument("--force", action="store_true", help="已有图也覆盖重取")
    args = ap.parse_args()

    ep_dir = os.path.join(ROOT, "episodes", args.episode)
    if not os.path.isdir(ep_dir):
        sys.exit(f"episode 目录不存在：{ep_dir}")
    brief = {}
    bp = os.path.join(ep_dir, "brief.json")
    if os.path.exists(bp):
        brief = json.load(open(bp, encoding="utf-8"))
    title = args.title or brief.get("display_title") or args.episode
    author = args.author or brief.get("author") or ""
    dest = os.path.join(ep_dir, os.path.basename(CFG["asset"]["filename"]))
    if os.path.exists(dest) and not args.force:
        sys.exit(f"{os.path.relpath(dest, ROOT)} 已存在（要覆盖加 --force）。"
                 f"换图必须同时重写旁证，否则 provenance_ok() 会按 sha256 判红")

    prov = {"episode": args.episode, "query_title": title, "query_author": author,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "target_width": TARGET_W, "card_box_px": None}

    if args.local:
        data = open(args.local, "rb").read()
        prov.update(source="local", url=os.path.abspath(args.local), note="--from 人工提供")
    elif args.url:
        data = get(args.url, proxy=args.proxy)
        prov.update(source="web", url=args.url)
    else:
        books = search(title, proxy=args.proxy)
        if args.list:
            for i, b in enumerate(books[:args.list], 1):
                print(f"[{i}] {b.get('title')} / {b.get('author')}  bookid={b.get('bookId')}"
                      f"  cover={b.get('cover')}")
            return
        if args.book_id:
            hit = next((b for b in books if str(b.get("bookId")) == str(args.book_id)), None)
            if not hit:
                sys.exit(f"搜索结果里没有 bookid={args.book_id}（--list 看候选）")
        else:
            hit = pick(books, title, author)
        if not hit or not hit.get("cover"):
            sys.exit(f"微信读书没有命中《{title}》的封面（--list 5 看候选，或 --url 指定）")
        got = download_best(hit["cover"], proxy=args.proxy)
        if not got:
            sys.exit(f"命中了但高清图取不下来：{hit.get('cover')}")
        url, data, width = got
        prov.update(source="weread", url=url, weread_book_id=str(hit.get("bookId") or ""),
                    hit_title=hit.get("title"), hit_author=hit.get("author"),
                    thumbnail_url=hit.get("cover"), source_width=width)
        print(f"[weread] {hit.get('title')} / {hit.get('author')}  {width}px  <- {url}")

    size = save_normalized(data, dest)
    prov.update(width=size[0], height=size[1],
                sha256=hashlib.sha256(open(dest, "rb").read()).hexdigest())
    from PIL import Image
    with Image.open(dest) as im:
        prov["card_box_px"] = list(bc.card_box(dest, CFG))
    with open(bc.provenance_path(ep_dir, CFG), "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)
    ok, note = bc.provenance_ok(ep_dir, CFG)
    print(f"[cover] {os.path.relpath(dest, ROOT)} {size[0]}x{size[1]} → 卡框 "
          f"{prov['card_box_px']}  旁证核对：{'✅' if ok else '❌'} {note}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
