#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 TypeSafe Jev（System One Choice 原语）为《少年维特的烦恼》V-B 每个剧情段重选最贴合的背景配图。

按连续 order 区间划分剧情段（不逐句选，避免 Ken Burns run 闪烁/碎段），
每段调一次 Jev Choice 在 7 张候选图里单选一张。标题段固定 result-bridge。

产物：episodes/少年维特的烦恼/ts-image-map.json
  { "segments": [ {order_start, order_end, theme, chosen, probabilities} ],
    "order_map": { "<order>": "<image_file_no_ext>" } }

依赖：环境变量 TYPESAFE_API_KEY（Bearer token）
"""
import json, os, csv, sys, urllib.request, urllib.error, time

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "少年维特的烦恼")
OUT = os.path.join(EP, "ts-image-map.json")

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

# 候选图（来自 queries.json 的 pexels 描述 + 语义定位）
IMG_DESC = {
    "result-bridge":  "晨光中窗边摊开的旧信与羽笔（结果桥接，承接片头定格，用于标题与结尾金句）",
    "atmosphere-1":   "夏日乡野麦田与菩提树（维特来到瓦尔海姆乡间，远离城市喧嚣）",
    "atmosphere-2":   "暖光厨房白裙少女切面包分给弟妹（18世纪乡间厨房：维特初见绿蒂、绿蒂切面包分给弟妹那一秒的心动）",
    "atmosphere-3":   "烛光下伏案写信的旧书桌（维特写信给绿蒂，情感倾诉）",
    "atmosphere-4":   "贵族舞会吊灯与华丽内景（公使馆/晚宴的社交场合）",
    "atmosphere-5":   "冬夜荒原枯树与结冰湖面（精神困境、寒冷绝望）",
    "atmosphere-6":   "黎明前冷蓝天光薄雾（重新面对生活、清醒与成长）",
}

# 剧情段（连续 order 区间，V-B 共 56 行）。S1 标题卡固定 result-bridge。
SEGMENTS = [
    (1, 1,   "标题卡：书名《少年维特的烦恼》"),
    (2, 4,   "共鸣开头：现代人对「明知没有结果仍靠近、把全部情绪寄托在一个人身上」的回顾与共鸣"),
    (5, 9,   "18世纪故事：维特来到瓦尔海姆乡间远离城市，遇到温柔聪明的绿蒂，两人产生深厚情感"),
    (10, 21, "绿蒂已有未婚夫阿尔伯特；痛苦源于深爱的人本不属于他；克制越深越痛苦，陷入精神困境"),
    (22, 27, "维特的问题不仅是爱情：过于敏感投入依赖内心感受；绿蒂阿尔伯特维特各有自己的人生"),
    (28, 37, "年轻时把强烈误认为深刻；成熟后明白强烈感情不等于正确选择；失去后仍能把人生继续过下去"),
    (38, 43, "把全部意义寄托在一种情感上，情感破裂生活失去支撑；健康人生不应只有爱情；不要让一个人成为整个世界"),
    (44, 51, "歌德的提醒：可以有深刻感情但不能失去自我；如何面对得不到的东西、现实与理想的差距、保留清醒"),
    (52, 55, "真正的成长：即使依然会爱会痛会遗憾，也知道自己应继续向前走"),
    (56, 56,  "结尾金句：爱可以很深，但人生不能只有爱"),
]

FIXED = {1: "result-bridge", 56: "result-bridge"}  # 标题段与结尾金句固定


def load_text_by_order(version):
    d = {}
    with open(os.path.join(EP, "script-body.csv"), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("version") == version:
                d[int(row["order"])] = row["text"].strip()
    return d


def load_key():
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("TYPESAFE_API_KEY")


def call_typesafe(state_text, criteria):
    key = load_key()
    if not key:
        raise SystemExit("缺少 TYPESAFE_API_KEY（.env 或环境变量），无法调用 TypeSafe。")
    body = {
        "state": state_text,
        "model": MODEL,
        "questions": {
            "pick_image": {
                "type": "choice",
                "instructions": (
                    "这是一段读书短视频的旁白文案。请从候选背景配图里，"
                    "选出最能贴合这段文案情绪与主题的那一张，作为该段的背景。"
                    "候选图各自描述不同场景，请按主题区分度选择，避免连续同图。"
                ),
                "criteria": criteria,
            }
        },
    }
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(4):
        req = urllib.request.Request(
            API, data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode("utf-8"))
            ans = resp["answers"]["pick_image"]
            return ans["choice"], ans.get("probabilities", {}), ans.get("confidence")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8','replace')}"
            if e.code in (429, 529):
                time.sleep(min(2 ** attempt + 1, 16)); continue
            raise
        except Exception as e:  # noqa
            last = str(e); time.sleep(min(2 ** attempt + 1, 16))
    raise RuntimeError(f"Jev request failed after retries: {last}")


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "B"
    text = load_text_by_order(version)
    segments_out = []
    order_map = {}
    for (a, b, theme) in SEGMENTS:
        if a in FIXED:
            chosen = FIXED[a]
            for o in range(a, b + 1):
                order_map[str(o)] = chosen
            segments_out.append({"order_start": a, "order_end": b, "theme": theme,
                                "chosen": chosen, "probabilities": {chosen: 1.0}, "fixed": True})
            print(f"段 {a}-{b} [固定] -> {chosen}")
            continue
        seg_text = "\n".join(text[o] for o in range(a, b + 1))
        state = f"【剧情段主题】{theme}\n\n【旁白文案】\n{seg_text}"
        chosen, probs, conf = call_typesafe(state, IMG_DESC)
        segments_out.append({"order_start": a, "order_end": b, "theme": theme,
                             "chosen": chosen, "probabilities": probs, "confidence": conf})
        for o in range(a, b + 1):
            order_map[str(o)] = chosen
        print(f"段 {a}-{b} -> {chosen} (conf={conf:.2f})")
    out = {"source": "jev", "scriptVersion": version, "segments": segments_out, "order_map": order_map}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 映射已写入 {OUT}")


if __name__ == "__main__":
    main()
