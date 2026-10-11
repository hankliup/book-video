#!/usr/bin/env python3
"""把 Whisper 输出的 ASR JSON 统一转成简体中文。

背景（2026-08-28 汤姆·索亚历险记实测）：
  whisper-cli 对长音频分段识别时，不同段会输出不同字形——首段简体、后续段繁体
  （這本書寫於 / 馬克圖文 / 親身經歷）。align-word-timings.mjs 的繁→简映射是靠
  「位置对应投票」学出来的，当繁体集中在后段、前段样本不足时映射学不全，
  LCS 在繁体段起点处整体失配：后半程所有字幕被挤在同一时刻（实测 55s 处堆了 5 行）。

  在对齐之前把 ASR 全量转简体，可让 LCS 只处理同音错字、不再受字形差异干扰。

要点：
  zhconv 是严格的一字对一字转换（這→这），字符数不变，因此不影响 token 与字符的
  位置对应关系，也不影响任何时间偏移。

  ⚠️ 必须同时转换 transcription[].text **和** transcription[].tokens[].text。
  align-word-timings.mjs 读的是 token 级文本（词级时间戳挂在 token 上），
  只转段级 text 对对齐毫无影响——实测 matchRate 一动不动（0.641 → 0.641）。

用法：
  python3 scripts/asr-to-simplified.py "episodes/<书名>/audio/asr/body.json"

需要：pip install zhconv
"""
import json
import shutil
import sys

try:
    from zhconv import convert
except ImportError:
    sys.exit("缺少 zhconv，请先安装：pip install zhconv")

if len(sys.argv) < 2:
    sys.exit(__doc__)

target = sys.argv[1]

try:
    with open(target, encoding="utf-8") as f:
        data = json.load(f)
except FileNotFoundError:
    sys.exit(f"文件不存在: {target}")

def to_simplified(text):
    """保留 token 前导空格（Whisper 的 token 常带空格前缀）。"""
    if not text:
        return text
    stripped = text.lstrip(" ")
    prefix = text[: len(text) - len(stripped)]
    return prefix + convert(stripped, "zh-cn")


total = 0
changed = 0
token_total = 0
token_changed = 0

for segment in data.get("transcription", []):
    original = segment.get("text", "")
    simplified = convert(original, "zh-cn")
    if simplified != original:
        changed += 1
    segment["text"] = simplified
    total += 1

    # 关键：token 级文本才是 align-word-timings 的对齐真源
    for token in segment.get("tokens", []) or []:
        original = token.get("text", "")
        simplified = to_simplified(original)
        if simplified != original:
            token_changed += 1
        token["text"] = simplified
        token_total += 1

shutil.copyfile(target, f"{target}.bak")
with open(target, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"段级：{total} 句，{changed} 句含繁体")
print(f"token：{token_total} 个，{token_changed} 个含繁体 → {target}")
print(f"原文件备份为 {target}.bak")
print("\n下一步：node scripts/align-word-timings.mjs \"<书名>\"")
