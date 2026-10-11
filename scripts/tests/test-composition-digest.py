#!/usr/bin/env python3
"""渲染竞态守卫回归：产物必须能对上「渲它的那一版组成」，对不上就不许进门禁。

这条规则的本体是**驱动层的时序**，不是某个数：一次 hyperframes render 跑几十秒，同期另一个
会话把同一份工程目录重写掉，烧进 mp4 的就是「一半旧一半新」的像素，而十道门禁每一条问的都是
「画面对不对」，没有一条问「这份像素是谁渲出来的」。实测就是这么交付的：index.html 改于
10:10:42，渲染 10:10:58 收尾，成片带的是 `.scene` 还没有 z-index 的那版形状，判据①~⑤ 全绿。

三个用例，前两个是同一份目录的**正反对照**（少一个就是空断言）：
  1 改 renders/ 里的产物        → 指纹**不许**变。产物给自己的来源作证，就是《李尔王》那次
                                  绿灯的来源：门禁量磁盘上的 mp4，拿 mp4 反查组成永远查不出问题。
  2 改 index.html 一个字节      → 指纹必须变。这就是「页面在渲染进行中被改写」的可量形式。
  3 render-hook-batch.sh 静态形状 → 渲前渲后各算一次、两个数不同就 rm 掉产物重渲、算得出指纹
                                  才 record 成 renders/<段>.provenance.json；缺任何一条，
                                  这道守卫就退化成「打了个日志」。
  4~7 **读账本**的那一侧（verify）：账本只在渲染当时打一行日志是不够的——共享层改了而没人重渲
      的那 118 部片子，磁盘上没有一条像素会自称是旧的。所以 verify 必须
        · 生成器与记录时一致 → 绿；
        · 生成器在渲染之后被改过 → 红并点名文件（《李尔王》《恶意》那一类的可判形式）；
        · 记录的那份 mp4 字节和账本不符 → 红（手工覆盖产物）；
        · 本期根本没有账本 → 只报「无从证明」不判红（记录始于 2026-10-05，把已交付的一百多部
          一律判红是把账本的出生日当成缺陷，也会把真正的清单埋掉）。
      另外 record 必须能把同一份 payload 镜像到期数目录：预览目录的 renders/ 会被下一次
      create-episode-preview.mjs 整片 rm -rf，账本跟着它作证的那份像素一起消失。

用法：python3 scripts/tests/test-composition-digest.py
退出码：0 全部通过；1 有用例失败。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIGEST = os.path.join(ROOT, "scripts", "composition-digest.py")
DRIVER = os.path.join(ROOT, "scripts", "render-hook-batch.sh")


def digest_of(comp_dir):
    """跑**同一份 CLI**，不在这里重算一遍：算法抄一份就会跟着门禁漂移。"""
    r = subprocess.run([sys.executable, DIGEST, "digest", comp_dir],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"digest 失败：{r.stdout}{r.stderr}")
    return r.stdout.strip()


def make_comp(dirs):
    os.makedirs(os.path.join(dirs, "media"), exist_ok=True)
    os.makedirs(os.path.join(dirs, "renders"), exist_ok=True)
    with open(os.path.join(dirs, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html><body>scene A</body></html>")
    with open(os.path.join(dirs, "media", "01.png"), "wb") as f:
        f.write(b"\x89PNG fake still 01")
    with open(os.path.join(dirs, "renders", "hook.mp4"), "wb") as f:
        f.write(b"ftypmp42 old pixels")
    return dirs


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    fails = []

    def check(label, ok, detail=""):
        print(f"  {'✅' if ok else '❌'} {label}{detail}")
        if not ok:
            fails.append(label)

    with tempfile.TemporaryDirectory(prefix="comp-digest-") as tmp:
        comp = make_comp(os.path.join(tmp, "hook"))
        base = digest_of(comp)
        check("指纹格式是可比的定长串", base.startswith("sha256:") and len(base) == 71,
              f" —— {base}")

        # 1 反面对照：动**产物**不许动指纹
        write(os.path.join(comp, "renders", "hook.mp4"), "brand new pixels from another run")
        check("改 renders/ 里的产物 → 指纹不变（产物不许给自己的来源作证）",
              digest_of(comp) == base, f" —— {base[:20]}…")

        # 2 正面对照：动**组成**必须动指纹，而且只动一次字节就够
        write(os.path.join(comp, "index.html"), "<html><body>scene A + z-index:1</body></html>")
        after = digest_of(comp)
        check("改 index.html 一个字节 → 指纹必须变（这就是那次竞态的可量形式）",
              after != base, f" —— {base[:20]}… → {after[:20]}…")
        write(os.path.join(comp, "media", "02.png"), "second still")
        check("加一张素材 → 指纹再变（覆盖「只哈希了 index.html」的退化实现）",
              digest_of(comp) != after)

        # 3 record：provenance 必须同时钉住组成、成片、生成器三方
        prov = os.path.join(comp, "renders", "hook.provenance.json")
        r = subprocess.run([sys.executable, DIGEST, "record", comp,
                            "--video", os.path.join(comp, "renders", "hook.mp4"),
                            "--out", prov], capture_output=True, text=True, cwd=ROOT)
        ok = r.returncode == 0 and os.path.isfile(prov)
        check("record 写出 renders/<段>.provenance.json", ok, f" —— {r.stderr[-200:]}")
        if ok:
            doc = json.load(open(prov, encoding="utf-8"))
            check("对账单里有组成指纹 + 成片指纹 + 生成器指纹三方",
                  doc.get("composition_digest") == digest_of(comp)
                  and doc.get("video_sha256") and doc.get("generators"),
                  f" —— keys={sorted(doc)}")
            check("生成器里含 create-episode-preview.mjs（改了生成器没人重新生成工程目录，"
                  "落盘的 index.html 还是旧形状 ——《恶意》2026-10-01 那版书名层）",
                  "scripts/create-episode-preview.mjs" in (doc.get("generators") or {}))

        # 3b 持久镜像：预览目录的 renders/ 会被下一次 create-episode-preview.mjs 整片 rm -rf，
        #    账本跟着它作证的那份像素一起消失就永远读不回来 —— 所以 record 必须能同时落第二处。
        keep = os.path.join(tmp, "episodes-side", "renders", "hook.provenance.json")
        r = subprocess.run([sys.executable, DIGEST, "record", comp,
                            "--video", os.path.join(comp, "renders", "hook.mp4"),
                            "--out", prov, "--also", keep],
                           capture_output=True, text=True, cwd=ROOT)
        mirrored = (r.returncode == 0 and os.path.isfile(keep)
                    and json.load(open(keep, encoding="utf-8")) == json.load(open(prov, encoding="utf-8")))
        check("record --also 到期数目录镜像同一份对账单（两处逐字节相同）", mirrored,
              f" —— {r.stderr[-200:]}")

    # 4~7 verify：账本必须**被读**，否则它只是一份没人打开的日志。
    def verify(target):
        r = subprocess.run([sys.executable, DIGEST, "verify", target],
                           capture_output=True, text=True, cwd=ROOT)
        return r.returncode, r.stdout + r.stderr

    with tempfile.TemporaryDirectory(prefix="comp-verify-") as tmp:
        comp = make_comp(os.path.join(tmp, "hook"))
        ep = os.path.join(tmp, "episodes-side")
        subprocess.run([sys.executable, DIGEST, "record", comp,
                        "--video", os.path.join(comp, "renders", "hook.mp4"),
                        "--out", os.path.join(comp, "renders", "hook.provenance.json"),
                        "--also", os.path.join(ep, "renders", "hook.provenance.json")],
                       capture_output=True, text=True, cwd=ROOT)
        rc, out = verify(ep)
        check("verify：生成器与记录时逐字节相同 → 绿（这条绿不是空转：同一个夹具下面两条要翻红）",
              rc == 0 and "✅" in out, f" —— rc={rc} {out.strip()[-160:]}")

        pth = os.path.join(ep, "renders", "hook.provenance.json")
        doc = json.load(open(pth, encoding="utf-8"))
        doc["generators"]["scripts/create-episode-preview.mjs"] = "0" * 64
        json.dump(doc, open(pth, "w", encoding="utf-8"), ensure_ascii=False)
        rc, out = verify(ep)
        check("verify：生成器在渲染之后被改过 → 红，并点名改了哪个文件（这就是《李尔王》那一类）",
              rc == 1 and "scripts/create-episode-preview.mjs" in out and "上一代形状" in out,
              f" —— rc={rc}")

        # 重新记一遍（生成器回到真值），只把 video_sha256 改掉：模拟「记录之后有人直接换了产物」
        subprocess.run([sys.executable, DIGEST, "record", comp,
                        "--video", os.path.join(comp, "renders", "hook.mp4"),
                        "--out", os.path.join(comp, "renders", "hook.provenance.json"),
                        "--also", pth], capture_output=True, text=True, cwd=ROOT)
        doc = json.load(open(pth, encoding="utf-8"))
        doc["video_sha256"] = "f" * 64
        json.dump(doc, open(pth, "w", encoding="utf-8"), ensure_ascii=False)
        rc, out = verify(ep)
        check("verify：记录的那份 mp4 字节和账本不符 → 红（手工覆盖产物也要抓住）",
              rc == 1 and "已经不是磁盘上的产物" in out, f" —— rc={rc}")

    with tempfile.TemporaryDirectory(prefix="comp-norec-") as tmp:
        os.makedirs(os.path.join(tmp, "renders"), exist_ok=True)
        rc, out = verify(tmp)
        check("verify：本期没有账本 → 只报「无从证明」，不判红"
              "（记录始于 2026-10-05，把已交付的一百来期一律判红是把账本出生日当成缺陷）",
              rc == 0 and "无从证明" in out, f" —— rc={rc}")

    src = open(DRIVER, encoding="utf-8").read()
    for token, why in [
        ("before=$(python3 scripts/composition-digest.py digest \"$dir\")",
         "渲之前没按指纹，就没有「之前」可比"),
        ("after=$(python3 scripts/composition-digest.py digest \"$dir\")",
         "渲之后没再按一次，这条守卫就是空转"),
        ("if [ \"$after\" != \"$before\" ]; then",
         "两个数对上了却不判，等于只打了个日志"),
        ("rm -f \"$out\" \"$dir/renders/$seg.provenance.json\"",
         "判了却没删产物：那份一半旧一半新的像素还会被下一次装配拼进成片"),
        ("composition-digest.py record", "没有落盘的对账单，产物与组成之间依然没有任何对应关系"),
        ('--also "$keep"', "对账单只落在预览目录里，下一次生成预览把它连同像素一起 rm -rf，账本就永远读不回来了"),
        ('rm -f "episodes/$EPISODE/renders/hook.provenance.json"',
         "本期没渲成却留着上一代的记录，verify 会把已经不存在的像素读成「生成器一致」"),
        ('composition-digest.py verify "$EPISODE"',
         "写了没人读的对账单就是暗账：交付路径上必须真的读一遍，红了要带走本期退出码"),
    ]:
        check(f"render-hook-batch.sh 仍在校验 {token[:42]}…", token in src, f" —— {why}")

    print()
    if fails:
        print(f"❌ 渲染竞态守卫回归：{len(fails)} 项失败 —— {' / '.join(fails)}")
        sys.exit(1)
    print("✅ 渲染竞态守卫回归：产物不许给自己作证、改一字节就翻指纹、驱动仍在前后各按一次并重渲")


if __name__ == "__main__":
    main()
