#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 TypeSafe Jev (System One Choice) 为《霍比特人》95 句字幕稿选 4 张背景图。
产物：episodes/霍比特人/ts-image-map.json
依赖：TYPESAFE_API_KEY（.env 或环境变量，绝不写盘）"""
import json, os, csv, sys, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes", "霍比特人")
OUT = os.path.join(EP, "ts-image-map.json")
API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

IMG_DESC = {
    "result-bridge":  "孤山晨雾中的金色山谷，落日暖光，电影感写实油画，余韵与归家感。固定用于标题卡与结尾。",
    "atmosphere-1":   "晨雾森林与远山，暖绿晨雾与冷金微光交织，中洲地底圆门小屋的宁静清晨，留白与希望感。适合：安稳舒适的生活、希望感、结尾余韵；不适合黑暗地底。",
    "atmosphere-2":   "幽暗地底隧道，烛光微光，冷蓝色调，孤独与未知感。适合：黑暗隧道、地底洞穴、面对未知与孤独、真正的勇气。",
    "atmosphere-3":   "金色山峦日出，孤山宝藏之境的史诗感，暖金与暗调交织。适合：巨龙、宝藏、财富与命运秘密、史诗感。",
}

# 95 句字幕稿（order 1 = 标题卡，固定 result-bridge）分段
SEGMENTS = [
    (1, 1, "标题卡：《霍比特人》J.R.R.托尔金"),
    (2, 9,  "比尔博·巴金斯：袋底洞里的安稳生活，爱蛋糕、扶手椅与壁炉"),
    (10, 19, "甘道夫带十三名矮人到来，邀请比尔博前往孤山夺宝；他本不想拒绝，却出发了"),
    (20, 31, "旅途初行：食人妖、森林、矮人伙伴，比尔博第一次离开舒适区"),
    (32, 45, "地底洞穴：与咕噜对弈，摸到冰冷的魔戒，智慧逃脱"),
    (46, 58, "巨龙史矛革的宝藏与战争的秘密：比尔博独面恶龙，用一个空口袋和一句话救了队伍"),
    (59, 72, "托尔金真正讲的问题：一个普通人能走多远；孤独走在黑暗隧道里的那场战斗"),
    (73, 84, "财富与贪婪的主题：孤山宝藏引来争抢，钱、地位、名声若成唯一目标会反过来控制人"),
    (85, 95, "回到袋底洞：那个害怕冒险的自己回不去了；成长是先出发，而不是成为与众不同的人"),
]
FIXED = {1: "result-bridge"}

def load_text():
    d = {}
    with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d[int(row["order"])] = row["text"].strip()
    return d

def call_typesafe(state, criteria):
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        env = os.path.join(ROOT, ".env")
        if os.path.exists(env):
            for line in open(env, encoding="utf-8"):
                if line.startswith("TYPESAFE_API_KEY="):
                    key = line.strip().split("=", 1)[1]
                    break
    if not key:
        raise SystemExit("缺少 TYPESAFE_API_KEY（.env 或环境变量），无法调用 TypeSafe。")
    body = {
        "state": state,
        "model": MODEL,
        "questions": {"pick_image": {
            "type": "choice",
            "instructions": ("这是一段读书短视频的旁白文案。请从候选背景配图里，"
                             "选出最能贴合这段文案情绪与主题的一张作为该段背景。\n"
                             "要求：情绪主题优先于画面细节；若 criteria 里某张图标注了"
                             "「上一段刚用过」，请避免连续重复，保持视觉变化。"),
            "criteria": criteria,
        }},
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.loads(r.read().decode("utf-8"))
    ans = resp["answers"]["pick_image"]
    return ans["choice"], ans.get("probabilities", {}), ans.get("confidence")

def main():
    def criteria_for(prev):
        c = dict(IMG_DESC)
        if prev:
            c[prev] = c[prev] + " 上一段刚用过这张图，请勿重复选用。"
        return c

    text = load_text()
    segs, order_map, prev = [], {}, "result-bridge"
    for a, b, theme in SEGMENTS:
        if a in FIXED:
            for o in range(a, b + 1):
                order_map[str(o)] = FIXED[a]
            segs.append({"order_start": a, "order_end": b, "theme": theme,
                         "chosen": FIXED[a], "fixed": True,
                         "probabilities": {FIXED[a]: 1.0}})
            print(f"段 {a}-{b} [固定] -> {FIXED[a]}")
            prev = FIXED[a]
            continue
        seg_text = "\n".join(text[o] for o in range(a, min(b, max(text)) + 1) if o in text)
        state = f"【剧情段主题】{theme}\n\n【旁白文案】\n{seg_text}"
        chosen, probs, conf = call_typesafe(state, criteria_for(prev))
        segs.append({"order_start": a, "order_end": b, "theme": theme,
                     "chosen": chosen, "probabilities": probs, "confidence": round(conf, 3)})
        for o in range(a, b + 1):
            order_map[str(o)] = chosen
        print(f"段 {a}-{b} -> {chosen} (conf={conf:.2f})")
        prev = chosen
    out = {"source": "typesafe-jev", "segments": segs, "order_map": order_map}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 映射已写入 {OUT}")

if __name__ == "__main__":
    main()
