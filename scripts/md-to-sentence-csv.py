#!/usr/bin/env python3
"""从源 .md 文案生成 sentence 模式的 script-body.csv（词边界安全版）。

与仓库内 md-to-sentence-script.mjs 的区别：
  mjs 版对超长子句按「字符数」硬切 + 一张手写双字词表（FORBIDDEN2）兜底，
  词表覆盖不到的地方就会把词劈开 —— 实测出现「苹/果」「小/孩」「马/克吐温」
  「每/一个」「那/个」这类断句，观感很差。词表不可能穷举中文，这条路走不通。

本脚本改用 jieba 中文分词：切点必定落在词边界或标点上，从原理上杜绝劈词。

规则：
  - 首段自动作为书名标题锚点行（旁白开头念书名时对齐必需）
  - 先按标点切成子句，再 jieba 分词，greedy 组合成 ≤MAX_LINE 字的视觉行
  - 每个 caption 最多 MAX_LINES 个视觉行，视觉行之间用空格分隔
    （渲染器按空格切块同屏；空格即换行，与 phrase 模式一致）
  - 去掉引号/书名号，避免渲染时占位

用法：
  python3 scripts/md-to-sentence-csv.py "episodes/<书名>/文案.md" "episodes/<书名>/script-body.csv" \
      "马克吐温,本罗杰斯,波莉姨妈,印第安乔,汤姆索亚,贝基,哈克,密西西比"

  第三个参数（可选）是逗号分隔的专名词表，防止人名/地名/书名被从中间劈开。
  书名会自动从 .md 首段提取并加入词典，无需重复写在参数里。

需要：pip install jieba
"""
import csv
import math
import re
import sys

try:
    import jieba
except ImportError:
    sys.exit("缺少 jieba，请先安装：pip install jieba")

MAX_LINE = 12          # 单个视觉行最大字数（容器 ~652px / 字号 50px → 12 字不折行）
MAX_LINES = 2          # 每个 caption 最多 2 个视觉行
PUNCT_CHARS = "，。！？；：、…,.!?;:"
STRIP = "“”‘’\"'《》（）()·— 　"


def strip_symbols(text):
    """去掉引号、书名号等，这些符号在字幕里占位且无朗读价值。"""
    for ch in STRIP:
        text = text.replace(ch, "")
    return text.strip()


SENT_END = "。！？；…"


def split_clauses(para, by_sentence=False):
    """按标点切成子句，标点保留在子句末尾（仅用于判断句读，最终会被去掉）。

    by_sentence=True：只在句末标点（。！？；…）处切，逗号/顿号留在句内。
    用于「短子句过多」的稿子——默认逐逗号切会让「也不写圣徒，」「十九岁。」
    这类 5-8 字子句各占一个视觉行、进而独占一屏，成片切换过碎（实测
    《少年》一稿切成 93 屏、平均仅 13.7 字/屏）。按句切分后交给 DP 重新断行，
    每屏稳定 2 个视觉行（~20 字），切换节奏正常，切点仍由 DP 保证落在词边界。
    """
    pattern = r"([，。！？；：、…])" if not by_sentence else r"([。！？；…])"
    parts = re.split(pattern, para)
    clauses = []
    buf = ""
    for i in range(0, len(parts), 2):
        body = parts[i]
        tail = parts[i + 1] if i + 1 < len(parts) else ""
        buf += body + tail
        if tail:
            if buf.strip():
                clauses.append(buf.strip())
            buf = ""
    if buf.strip():
        clauses.append(buf.strip())
    # 纯标点子句合并到上一个子句：句末标点连续出现时（如"……"被切成"…"+"…"、
    # "！！"被切成"！"+"！"）会产生只含一个标点的子句，进而生成只有一个省略号的
    # caption —— 成片是一屏空白加一个点，观感很差（实测《在人间》出现 "A,15,…"）。
    merged = []
    for clause in clauses:
        if merged and is_punct(clause):
            merged[-1] += clause
        else:
            merged.append(clause)
    return merged


def is_punct(token):
    return bool(token) and all(ch in PUNCT_CHARS for ch in token)


def visual_len(text):
    """视觉宽度：汉字 1、标点 0.5（中文标点窄，可悬挂在行尾）。"""
    return sum(0.5 if ch in PUNCT_CHARS else 1 for ch in text)


# 12 个汉字 = 600px，容器 ~652px，余量约 1 个字符宽，正好容纳一个悬挂标点
LINE_LIMIT = MAX_LINE + 0.5

# 断行观感惩罚：行尾悬单字 / 行首孤单字，各 +6（见 clause_to_lines 说明）
ORPHAN_W = 6.0
START_W = 6.0


def tokenize(text):
    """jieba 分词；标点并入前一个词，避免出现「只有一个逗号的视觉行」。"""
    tokens = []
    for word in jieba.cut(text):
        word = strip_symbols(word)
        if not word:
            continue
        if is_punct(word):
            if tokens:
                tokens[-1] += word
            continue
        tokens.append(word)
    return tokens


def clause_to_lines(clause):
    """把一个子句切成 ≤MAX_LINE 字的视觉行，切点必在词边界。

    为什么不用贪心「塞满即断」：那会留下行尾单字，实测出现过
    「今天想跟你聊一本很多人听 / 过」「但不一定真正走进过的一 / 本书」
    「社交场合露出得体 / 的微笑」，观感很差。

    改成 DP：
      1. 行数取理论最少 L = ceil(总宽 / LINE_LIMIT)，放不下才 +1
      2. 各行宽度尽量均衡（对偏离均值取平方惩罚）
      3. 切点处罚：行尾是单字（orphan）、行首是单字（start），各 +6
    """
    text = strip_symbols(clause)
    if not text:
        return []
    if visual_len(text) <= LINE_LIMIT:
        return [text]

    tokens = tokenize(text)
    if not tokens:
        return []
    n = len(tokens)
    widths = [visual_len(t) for t in tokens]
    pre = [0.0] * (n + 1)
    for i, w in enumerate(widths):
        pre[i + 1] = pre[i] + w
    total = pre[n]

    def fits(i, j):
        """tokens[i:j] 能否作为一行。"""
        if pre[j] - pre[i] <= LINE_LIMIT + 0.01:
            return True
        # 单个词本身就超长（长专名）：允许独占一行，不再切碎
        return j == i + 1 and len(tokens[i]) > MAX_LINE

    def break_penalty(i):
        """在 token i 处断行（上一行以 i-1 结尾、本行以 i 开头）的观感惩罚。"""
        p = 0.0
        if widths[i - 1] <= 1:
            p += ORPHAN_W      # 行尾悬一个单字，如「…人听 / 过」
        if widths[i] <= 1:
            p += START_W       # 行首孤零零一个单字，如「很多 / 人听过」
        return p

    def solve(L):
        avg = total / L
        INF = float("inf")
        dp = [[INF] * (n + 1) for _ in range(L + 1)]
        back = [[-1] * (n + 1) for _ in range(L + 1)]
        dp[0][0] = 0.0
        for k in range(1, L + 1):
            for j in range(k, n + 1):
                best_c, best_i = INF, -1
                for i in range(k - 1, j):
                    if dp[k - 1][i] == INF or not fits(i, j):
                        continue
                    c = dp[k - 1][i] + (pre[j] - pre[i] - avg) ** 2
                    if k > 1:
                        c += break_penalty(i)
                    if c < best_c:
                        best_c, best_i = c, i
                dp[k][j] = best_c
                back[k][j] = best_i
        if dp[L][n] == INF:
            return None
        lines, j, k = [], n, L
        while k > 0:
            i = back[k][j]
            lines.append("".join(tokens[i:j]))
            j, k = i, k - 1
        return list(reversed(lines))

    L = max(1, math.ceil(total / LINE_LIMIT))
    while L <= n:
        got = solve(L)
        if got:
            return got
        L += 1

    # 兜底：贪心
    lines, cur = [], ""
    for t in tokens:
        if cur and visual_len(cur + t) <= LINE_LIMIT:
            cur += t
        else:
            if cur:
                lines.append(cur)
            cur = t
    if cur:
        lines.append(cur)
    return lines


def pack(clauses):
    """以「子句」为单位打包 caption，保证 caption 边界 = 语义边界。

    不能简单地把视觉行两两打包：那样会把「露出得体 / 的微笑」「愿你心里，/ 永远住着」
    这类同一句话劈到两个 caption 里，观感断裂。
    规则：子句优先整体放进当前 caption；放不下就先收口上一个 caption。
    只有当单个子句本身就超过 MAX_LINES 行时才在子句内部再切。
    """
    captions = []
    pending = []
    for clause in clauses:
        lines = clause_to_lines(clause)
        if not lines:
            continue
        if pending and len(pending) + len(lines) <= MAX_LINES:
            pending.extend(lines)
        else:
            if pending:
                captions.append(" ".join(pending))
            pending = list(lines)
        # 子句本身超过 MAX_LINES 行：在子句内部按 MAX_LINES 切出
        while len(pending) > MAX_LINES:
            captions.append(" ".join(pending[:MAX_LINES]))
            pending = pending[MAX_LINES:]
    if pending:
        captions.append(" ".join(pending))
    return captions


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, out = sys.argv[1], sys.argv[2]

    # 第三个参数：逗号分隔的专名词表（人名/地名/书名），避免被 jieba 从中间劈开。
    # 例：--dict "马克吐温,本罗杰斯,波莉姨妈,印第安乔,汤姆索亚,贝基,哈克,密西西比"
    proper_nouns = []
    by_sentence = False
    for arg in sys.argv[3:]:
        if arg == "--by-sentence":
            by_sentence = True
            continue
        proper_nouns.extend(w.strip() for w in arg.split(",") if w.strip())

    with open(src, encoding="utf-8") as f:
        raw = f.read()

    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paras:
        sys.exit(f"源文案为空: {src}")

    # 书名整词入词典（首段），否则「汤姆索亚历险记」会被切成「汤姆 / 索亚历险记」
    book_title = strip_symbols(paras[0])
    for word in [book_title, *proper_nouns]:
        if word:
            jieba.add_word(word, freq=100000)

    rows = []
    order = 1

    # 首段 = 书名标题锚点行
    title = strip_symbols(paras[0])
    rows.append({"version": "A", "order": str(order), "text": title})
    order += 1

    # 逐段落打包：caption 不允许跨段落边界。
    # 早期版本把所有段落的子句拼成一个列表再打包，会把上一段的末句和下一段的
    # 首句塞进同一个 caption（如「到死都不相信她真的爱他。/ 一百多年前，」），
    # 语义断裂。段落是天然的场景/语气切换点，必须收口。
    for para in paras[1:]:
        if strip_symbols(para) == title:
            continue
        for caption in pack(split_clauses(para, by_sentence)):
            rows.append({"version": "A", "order": str(order), "text": caption})
            order += 1

    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["version", "order", "text"])
        writer.writeheader()
        writer.writerows(rows)

    # 自检：视觉行宽度（标点半宽）、caption 行数、空视觉行
    over_line = over_cap = empty_line = 0
    for row in rows[1:]:
        visual = row["text"].split(" ")
        if len(visual) > MAX_LINES:
            over_cap += 1
            print(f"  ⚠️ 超 {MAX_LINES} 行 caption: {row['text'][:40]}")
        for vl in visual:
            if not vl:
                empty_line += 1
                print(f"  ⚠️ 空视觉行: {row['text'][:40]!r}")
                continue
            if visual_len(vl) > LINE_LIMIT + 0.01:
                over_line += 1
                print(f"  ⚠️ 超宽视觉行（{visual_len(vl):.1f}）: {vl}")

    total_chars = sum(len(r["text"].replace(" ", "")) for r in rows[1:])
    print(f"标题行: {title}")
    print(f"共 {len(rows) - 1} 个 caption / {total_chars} 字 → {out}")
    print(f"自检：超长视觉行 {over_line}，超行数 caption {over_cap}")
    print("\n--- 前 10 行 ---")
    for row in rows[1:11]:
        print(f"  {row['order']}: {row['text']}")
    print("--- 后 5 行 ---")
    for row in rows[-5:]:
        print(f"  {row['order']}: {row['text']}")


if __name__ == "__main__":
    main()
