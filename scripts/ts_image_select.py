#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 TypeSafe Jev（System One Choice 原语）为《荆棘鸟》每个剧情段重选最贴合的背景配图。

为什么用 Choice 而非逐句选：
- 渲染时连续同图的 caption 会合并成一段 Ken Burns run，若逐句选图会出现闪烁/碎段；
- 按剧情段（连续 order 区间）让 Jev 在 7 张候选图里做单选，既语义对齐又保持视觉连贯。

产物：episodes/荆棘鸟/ts-image-map.json
  { "segments": [ {order_start, order_end, theme, chosen, probabilities} ],
    "order_map": { "<order>": "<image_file_no_ext>" } }

依赖：环境变量 TYPE_SAFE_API_KEY（Bearer token）
"""
import json, os, csv, sys, urllib.request

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "荆棘鸟")
IMG = os.path.join(EP, "images")
OUT = os.path.join(EP, "ts-image-map.json")

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

# 候选图（来自 prompts.csv 的 pexels 描述）
IMG_DESC = {
    "result-bridge":  "窗边摊开的书与暖光（结果桥接，承接片头定格）",
    "atmosphere-1":   "荆棘枝上的鸟与暗色天空（荆棘鸟传说：一生只唱一次歌）",
    "atmosphere-2":   "澳大利亚内陆红土地与麦田晨光（德罗海达农场）",
    "atmosphere-3":   "哥特教堂烛光剪影（拉尔夫神父与信仰）",
    "atmosphere-4":   "黄昏农舍窗前独坐身影（梅吉的漫长等待与孤独）",
    "atmosphere-5":   "暗夜风暴海浪（海与失去）",
    "atmosphere-6":   "晨雾田野金色晨光（好好生活的结尾余味）",
}

# 剧情段（连续 order 区间）。S1 标题卡固定 result-bridge，其余段交给 Jev 选。
SEGMENTS = [
    (1, 1,   "标题卡：书名《荆棘鸟》"),
    (2, 8,   "荆棘鸟传说：一生只唱一次，刺进最长最锋利的荆棘，用生命唱最凄美的歌"),
    (9, 17,  "考琳·麦卡洛的家族故事与德罗海达农场；少女梅吉爱上拉尔夫神父"),
    (18, 35, "拉尔夫的神父身份、信仰与野心让他一次次离开；梅吉漫长等待；爱情不能改变一个人的选择"),
    (36, 53, "梅吉在失去与成长中接受现实；婚姻的失望与孤独；真正决定命运的是选择怎样生活"),
    (54, 64, "爱只能存在于回忆；遗憾不是因为不够努力；真正成熟的爱是知道放手；也许可以读一读荆棘鸟"),
    (65, 81, "成长是把自己的人生还给自己；不是所有爱都需要圆满结局；经历伤痛后依然学会好好生活"),
]

FIXED = {1: "result-bridge"}  # 标题段固定


def load_text_by_order():
    d = {}
    with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d[int(row["order"])] = row["text"].strip()
    return d


def call_typesafe(state_text, criteria):
    key = os.environ.get("TYPE_SAFE_API_KEY")
    if not key:
        raise SystemExit("缺少环境变量 TYPE_SAFE_API_KEY，无法调用 TypeSafe。")
    body = {
        "state": state_text,
        "model": MODEL,
        "questions": {
            "pick_image": {
                "type": "choice",
                "instructions": (
                    "这是一段读书短视频的旁白文案。请从候选背景配图里，"
                    "选出最能贴合这段文案情绪与主题的那一张，作为该段的背景。"
                ),
                "criteria": criteria,
            }
        },
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode("utf-8"))
    ans = resp["answers"]["pick_image"]
    return ans["choice"], ans.get("probabilities", {}), ans.get("confidence")


def main():
    text = load_text_by_order()
    segments_out = []
    order_map = {}
    for (a, b, theme) in SEGMENTS:
        for o in range(a, b + 1):
            if a in FIXED:
                order_map[str(o)] = FIXED[a]
        if a in FIXED:
            segments_out.append({"order_start": a, "order_end": b, "theme": theme,
                                 "chosen": FIXED[a], "probabilities": {"result-bridge": 1.0}, "fixed": True})
            print(f"段 {a}-{b} [固定] -> {FIXED[a]}")
            continue
        seg_text = "\n".join(text[o] for o in range(a, b + 1))
        state = f"【剧情段主题】{theme}\n\n【旁白文案】\n{seg_text}"
        chosen, probs, conf = call_typesafe(state, IMG_DESC)
        segments_out.append({"order_start": a, "order_end": b, "theme": theme,
                             "chosen": chosen, "probabilities": probs, "confidence": conf})
        for o in range(a, b + 1):
            order_map[str(o)] = chosen
        print(f"段 {a}-{b} -> {chosen} (conf={conf:.2f})")
    out = {"segments": segments_out, "order_map": order_map}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 映射已写入 {OUT}")


if __name__ == "__main__":
    main()
