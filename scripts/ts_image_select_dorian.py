#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 TypeSafe Jev (System One Choice) 为《道林·格雷的画像》85 句字幕稿选 4 张背景图。
产物：episodes/道林·格雷的画像/ts-image-map.json
依赖：TYPESAFE_API_KEY（.env 或环境变量，绝不写盘）"""
import json, os, csv, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes", "道林·格雷的画像")
OUT = os.path.join(EP, "ts-image-map.json")
API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

IMG_DESC = {
    "result-bridge":  "黑场中一支将熄的白烛升起一缕青烟，极简暗调，宿命与余韵。固定用于标题卡与结尾定格。",
    "atmosphere-1":   "镀金画框与旧肖像倚着熄灭的烛台，沙龙暗调暖光，青春、美与旧时光。适合：上流沙龙、肖像画、青春美貌、体面人设；不适合腐烂与黑暗主题。",
    "atmosphere-2":   "斑驳开裂霉变的墙皮，腐绿与灰褐交织，黑暗压迫感。适合：灵魂腐烂、罪恶痕迹、画像变质、堕落与隐藏；不适合明亮与希望感。",
    "atmosphere-3":   "烛光下的白色康乃馨与朦胧镜光，暖金与幽绿交织，唯美主义享乐氛围。适合：欲望、享乐、沉溺、美成为唯一标准、空虚的刺激。",
}

# 85 句字幕稿（order 1 = 标题卡，固定 result-bridge）分段
SEGMENTS = [
    (1, 1,   "标题卡：《道林·格雷的画像》奥斯卡·王尔德"),
    (2, 5,   "钩子：如果有一幅画像替你衰老、替你承担罪恶，你永远年轻，你愿意吗"),
    (6, 15,  "故事开端：英俊少年道林·格雷，画家巴兹尔为他画肖像，亨利勋爵诱发他对青春的执念与可怕愿望"),
    (16, 26, "奇迹应验：脸永远年轻漂亮，岁月痕迹与灵魂全部转移到画像上，真实的自己一点点腐烂"),
    (27, 37, "真正主题：哥特小说外壳下讲人如何在欲望中失去自己；堕落从「只想满足自己」开始"),
    (38, 48, "亨利勋爵的自由说教：追随欲望、摆脱道德束缚，结果道林越来越依赖欲望，失去生命感"),
    (49, 59, "画像=真实自我的象征：现实中我们用衣服、身份、财富、人设包装自己，但很难永远欺骗自己"),
    (60, 67, "结局：道林刺向画像，被毁掉的却是自己；要面对的不是镜子里的脸，而是脸孔背后的自己"),
    (68, 79, "现实启示：青春可消耗、形象可包装，但每天的选择会一点一点塑造并最终写进我们的生活"),
    (80, 85, "收束之问：如果没有任何人知道，你会选择成为什么样的人？无人看见时你如何对待自己"),
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
