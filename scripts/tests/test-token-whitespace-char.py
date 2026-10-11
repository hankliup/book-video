#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASR token 的 ch 必须取「第一个被念出来的字符」，不能取原始文本的第一个字符。

为什么钉这条（2026-10-08《反抗者》行 39「注意，不是我。」）：
  whisper.cpp 的 BPE 把**词边界空格留在 token 文本里**（" 不是"），而
  create-body-timings-precise.mjs 的匹配是「文案单字 == token 的 ch」，ch 取
  `Array.from(t)[0]` —— 对 " 不是" 就是空格。文案侧的 STRIP 早把空白删干净了，
  所以没有任何文案字符能等于它：**这个 token 永远匹配不上**。
  本期 token 流是 注意@80.449-81.089 / " 不是"@81.089-81.989 / 我@81.989-82.449。
  「注意」是 gapOnset（Pause 1 不许落），「不」又匹配不到带空格的 token，行首只能
  落到「我」@81.989 —— 比交付轴的 80.449（正是「注意」自己的开口）晚 **1.541s**，
  第 5 道（token 派生）上限 1.5s 差点判红，而两个能量证人（逐行覆盖、-50dB 边界）
  都判 0 行。假红的代价不是小数：按 AGENTS.md 的口径，轴绿而证人红＝去修证人，
  但只有先证明「这一行自己的字确实匹配不上」才谈得上是证人错。
  Python 侧本来就是对的：asr_char_stream() 把整个 token 文本交给 clean()，
  而 PUNCT 含 \\s，空格进不了字符流。这条把 Node 侧拉回同一个字母表——和
  「数字逐位折汉字」「控制 token 整族剔除」是同一件事的第三种形状。

四组断言，②③互为对照（健康必须跑绿、被撤回的形状必须跑红）：
  ① 形状：全库确实存在带前导空格的 token（否则这条是空转），且撤回口径下它的首字符
     是空格、生产口径下是它自己的字；生产源码里两处 trim 都还在。
  ② 生产 --verify《反抗者》：exit 0 且打「✅ 交付轴与 token 重算轴一致」。
  ③ 回放被撤回的形状（同一份轴、同一份 ASR，只差那两处 trim）：exit 1，
     并且点名的行首最差行里必须有行 39 —— 这才证明判红来自 trim，不是来自别的。
  ④ 两个臂只能差那两处替换，夹具漂了就报错，别让「测了自己的副本」冒充回归。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PROD = os.path.join(ROOT, "scripts", "create-body-timings-precise.mjs")
BOOK = "反抗者"

TRIM_CHARS = "const tchars = Array.from(canonDigits(t.trim()));"
RAW_CHARS = "const tchars = Array.from(canonDigits(t));"
TRIM_SKIP = "if (!t.trim() || CONTROL_TOKEN.test(t)) continue;"
RAW_SKIP = "if (!t || CONTROL_TOKEN.test(t)) continue;"

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# ─────────────────────────────── ① 形状 ───────────────────────────────
prod_src = open(PROD, encoding="utf-8").read()
check(TRIM_CHARS in prod_src,
      "① 生产脚本里 `canonDigits(t.trim())` 不见了——ch 又退回取原始文本首字符（被撤回的形状）")
check(TRIM_SKIP in prod_src,
      "① 生产脚本里 `!t.trim()` 不见了——纯空白 token 又留在流里，会改写 gapOnset")
check(RAW_CHARS not in prod_src and RAW_SKIP not in prod_src,
      "① 被撤回的两处形状之一又回到了生产脚本里")

# 全库扫一遍：带前导空格的 token 必须真实存在，否则这条回归只会测自己的夹具。
ws_tokens = []
for entry in sorted(os.listdir(os.path.join(ROOT, "episodes"))):
    path = os.path.join(ROOT, "episodes", entry, "audio", "asr", "body.json")
    if not os.path.isfile(path):
        continue
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    for seg in raw.get("transcription", []):
        for tok in seg.get("tokens") or []:
            text = str(tok.get("text") or "")
            if text and text[0].isspace() and text.strip():
                ws_tokens.append((entry, text))
check(len(ws_tokens) > 0,
      "① 全库没有一个带前导空格的 token：要么 ASR 形状变了，要么这条断言已经空转，请重写")

sample = next(((e, t) for e, t in ws_tokens if t.strip() == "不是"), ws_tokens[0])
raw_first = sample[1][0]
trim_first = sample[1].strip()[0]
check(raw_first.isspace(),
      f"① 对照前提漂了：{sample[1]!r} 的首字符不是空格（{raw_first!r}）")
check(not trim_first.isspace(),
      f"① trim 以后首字符仍是空白：{sample[1]!r} → {trim_first!r}")

STRIP = re.compile(r"[，。？！、；：…—·“”‘’\"'《》（）()\[\]\s,.?!;:'\"\\-]+")
check(STRIP.match(raw_first),
      "① 文案侧的 STRIP 竟然不删空白——那两侧又回到同一个字母表了，本条前提要重写")

# ────────────────────────── ②③ 两个臂对拍 ──────────────────────────
withdrawn_src = prod_src.replace(TRIM_CHARS, RAW_CHARS).replace(TRIM_SKIP, RAW_SKIP)
check(withdrawn_src != prod_src, "③ 回放臂没有产生任何改动：两处替换有一处已经不对，控制是空转")
diff_count = sum(1 for a, b in zip(prod_src.splitlines(), withdrawn_src.splitlines()) if a != b)
check(diff_count == 2,
      f"④ 两个臂只能差那两行，实际差 {diff_count} 行——夹具漂了，③ 的红不再是「撤回形状」的红")

tmpdir = tempfile.mkdtemp(prefix="token-ws-")
withdrawn_path = os.path.join(tmpdir, "withdrawn-precise.mjs")
# 相对 import 在 tmp/ 解析不到，指向绝对路径；这一步对两个臂完全一致，不引入第二个差异。
withdrawn_src = withdrawn_src.replace(
    'from "./lib/script-version.mjs"',
    f'from "{os.path.join(ROOT, "scripts", "lib", "script-version.mjs")}"')
open(withdrawn_path, "w", encoding="utf-8").write(withdrawn_src)


def run(script):
    return subprocess.run(["node", script, BOOK, "--verify"], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)


green = run(PROD)
check(green.returncode == 0,
      "② 生产 --verify《反抗者》判红（期望绿）：\n" + (green.stdout + green.stderr)[-1200:])
check("✅ 交付轴与 token 重算轴一致" in green.stdout,
      "② 生产臂没打出绿的那句话：\n" + green.stdout[-800:])

red = run(withdrawn_path)
check(red.returncode == 1,
      "③ 被撤回的形状竟然也判绿——这条断言是空转（trim 已经不是行 39 晚点的原因了，请重新定位）")
worst = re.findall(r"行 (\d+) 交付轴行首 (-?[\d.]+)s 于 token 重算轴", red.stdout)
check(any(order == "39" for order, _ in worst),
      "③ 撤回形状的红没有点名行 39，说明它测的是别的东西：\n" + red.stdout[-800:])
delta39 = next((float(d) for order, d in worst if order == "39"), None)
check(delta39 is not None and delta39 <= -1.5,
      f"③ 撤回形状下行 39 的行首差应当 ≥1.5s（本期实测 -1.541s），实际 {delta39}")

print(f"全库带前导空格的 token：{len(ws_tokens)} 个（样本 {sample[0]}: {sample[1]!r}）")
print(f"生产臂 max 行首差那一行的读数：行 39 = {delta39}s（撤回形状）/ 绿（生产）")
if fails:
    print("\n".join("❌ " + f for f in fails))
    sys.exit(1)
print("✅ token ch 取被念出来的第一个字符：生产绿 / 撤回形状红，两侧同字母表")
