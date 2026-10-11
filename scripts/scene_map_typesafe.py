#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《死屋手记》场景映射：用 TypeSafe System One 模型 Jev 做结构化 Choice 判断。

每个叙事片段（约 20s 的 render run）作为一道 Choice 问题：
  - state      : 项目背景（陀氏《死屋手记》读书视频）
  - questions  : 每片段一题，type=choice，criteria=7 张背景图的可选项描述
  - 模型返回   : 每题 choice（最优图）+ probabilities + confidence

Jev 提供"可编程的语义常识"（哪张图最贴合该段旁白的情绪与主题），代码负责执行。
API 不可达时回退到本地透明打分（local_fallback），并在产物里标注 source。

产物：episodes/死屋手记/scene-map.json  →  render 脚本据此渲染。
API key 仅从环境变量 TYPESAFE_API_KEY 读取，绝不写盘/打印。
"""
import json, os, sys, csv, time, urllib.request, urllib.error

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "死屋手记")

# ---------- 1. 背景图语义（来自 prompts.csv 的真值 + 主题释义） ----------
# 选项 key 必须与 images/ 下的文件名（去掉 .png）一致。
# 结构化 criteria：显式写出 what / not_for，把易混淆的"天空/鹰/黎明"三个选项拆开，
# 避免 Jev 把它们当成同一类而反复选同一张（首轮实测：atmosphere-6 被选 5 次）。
IMAGE_CRITERIA = {
    "atmosphere-1": {
        "what": "Wooden fortress palisade in snow at winter dusk — the Siberian penal colony, hard-labor exile, the prison walls and the outside world.",
        "not_for": "not for: close-ups of bars/chains, or for: the title card and closing line.",
    },
    "atmosphere-2": {
        "what": "Dark candlelit prison barracks interior — where prisoners actually live, sleep and coexist day to day.",
        "not_for": "not for: open landscapes, skies, or eagles.",
    },
    "atmosphere-3": {
        "what": "Rusty iron prison bars and chains, extreme close-up — literal confinement, the cage, judgment and being labeled.",
        "not_for": "not for: wide landscapes, skies, or the title card and closing line.",
    },
    "atmosphere-4": {
        "what": "Lonely single footprints trail across a snow field — solitude, the individual self, 'I am still me', walking alone.",
        "not_for": "not for: skies, eagles, or the title card and closing line.",
    },
    "atmosphere-5": {
        "what": "Winter dawn, cold blue minimal sky landscape — cold vast openness, dawn light, the outside world and hope.",
        "not_for": "not for: eagles, or for: the title card and closing line.",
    },
    "atmosphere-6": {
        "what": "Eagle silhouette soaring against a cold sky — the soul unchained, transcendence, inner freedom, the individual breaking free.",
        "not_for": "not for: the title card and closing line, and not for: landscapes without an eagle.",
    },
    "result-bridge": {
        "what": "Eagle flying free in a vast open bright sky — full freedom; reserved for the title and the closing line.",
        "not_for": "not for: any other section; only the title card and the closing line.",
    },
}

# ---------- 2. 14 个叙事片段（render run 的边界，按旁白节拍切分） ----------
# 与脚本语义对应；Jev 只决定每个片段用哪张图
SECTIONS = [
    (1, 1),    # 标题
    (2, 8),    # 假处决 / 西伯利亚 / 苦役 / 陀氏
    (9, 13),   # 极端环境中失去尊严、维护最后一点自由
    (14, 19),  # 囚犯来自不同阶层，共同生活
    (20, 26),  # 陀氏观察理解他们；杀人犯温柔/小偷自尊/凶狠的人怕孤独
    (27, 30),  # 罪犯标签 vs 看见"人"；人性非黑即白
    (31, 39),  # 最难得的是自己决定的小事；"我还是我"
    (40, 46),  # 自由变得具体：穿什么/脾气尊严习惯/心里不被控制的角落
    (47, 50),  # 外部世界压缩空间，人怎样拼命寻找尊严
    (51, 56),  # 为什么会犯罪：贫穷/环境/欲望/人性黑暗
    (57, 63),  # 走进死屋看见真实的人；他们和我们没那么远
    (64, 70),  # 失去选择会变成什么样；自由的珍贵
    (71, 80),  # 残酷而温柔；人不能被错误定义；忘记自己是谁
    (81, 82),  # 金句收尾：写的是监狱，照见整个人类
]

STATE = ("Book-video narration for Fyodor Dostoevsky's 'The House of the Dead' (死屋手记), "
         "a memoir of his Siberian hard-labor imprisonment. Assign each narration section the single "
         "background image whose mood and subject best fit it. Background options are described in each "
         "question's criteria.")


def build_request(text_by_order):
    questions = {}
    # Noul：标题与结尾金句必须路由到 result-bridge（唯一一张"鹰飞向明亮天空"的图）。
    # 单独一题，避免它污染其他 Choice 的分布。
    title_text = text_by_order[1]
    closing_text = " / ".join(text_by_order[o] for o in range(81, 83))
    questions["title_or_closing"] = {
        "type": "noul",
        "instructions": {
            "title_text": title_text,
            "closing_text": closing_text,
            "question": "Is this narration the opening title or the closing line of the video, and therefore must use the 'eagle flying free in a vast open bright sky' image?",
        },
        "criteria": {
            "true": "The opening title or the closing line of the video.",
            "false": "A body narration section, not the title or the closing line.",
        },
    }
    # Choice：其余 12 个正文片段（排除 1 和 14，已由 Noul 决定性路由）。
    for i, (a, b) in enumerate(SECTIONS, 1):
        if i in (1, 14):
            continue
        lines = [text_by_order[o] for o in range(a, b + 1)]
        section_text = " / ".join(lines)
        questions[f"section_{i:02d}_cap{a}-{b}"] = {
            "type": "choice",
            "instructions": {
                "section_text": section_text,
                "question": "Which single background image best matches the mood and subject of this narration section?",
            },
            "criteria": dict(IMAGE_CRITERIA),
        }
    return {"state": STATE, "model": "jev-latest", "questions": questions}


def call_jev(payload, api_key, retries=4):
    url = "https://api.typesafe.ai/v1/systemone"
    data = json.dumps(payload).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url, data=data,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last_err = f"HTTP {e.code}: {body}"
            # 429/529 指数退避
            if e.code in (429, 529):
                time.sleep(min(2 ** attempt + 1, 16))
                continue
            raise
        except Exception as e:  # noqa
            last_err = str(e)
            time.sleep(min(2 ** attempt + 1, 16))
    raise RuntimeError(f"Jev request failed after retries: {last_err}")


def local_fallback(text_by_order):
    """Jev 不可达时的透明本地打分：关键词/主题亲和度。仅作兜底，产物会标注 source。
    标题(1)与结尾金句(81-82)由 Noul 决定性路由到 result-bridge，正文片段禁选 result-bridge。"""
    rules = [
        ("atmosphere-1", ["西伯利亚", "刑场", "死刑", "苦役", "囚犯", "流放", "俄国"]),
        ("atmosphere-2", ["生活", "肮脏", "拥挤", "共同", "通铺", "牢房", "共处", "环境"]),
        ("atmosphere-3", ["铁栏", "脚镣", "标签", "罪犯", "非黑即白", "尊严", "禁锢", "栏"]),
        ("atmosphere-4", ["脚印", "孤独", "自己", "我还是", "自我", "一个人", "雪地"]),
        ("atmosphere-5", ["黎明", "天亮", "远走", "选择", "珍贵", "道路", "天空", "冷蓝"]),
        ("atmosphere-6", ["鹰", "飞翔", "自由", "尊严", "灵魂", "超越"]),
    ]
    out = {}
    for i, (a, b) in enumerate(SECTIONS, 1):
        if i in (1, 14):
            out[f"section_{i:02d}"] = {"choice": "result-bridge", "confidence": 1.0, "probabilities": {}}
            continue
        text = " ".join(text_by_order[o] for o in range(a, b + 1))
        best, best_score = None, -1
        for img, kws in rules:
            s = sum(kw in text for kw in kws)
            if s > best_score:
                best, best_score = img, s
        out[f"section_{i:02d}"] = {"choice": best, "confidence": 0.0, "probabilities": {}}
    return out


def main():
    text_by_order = {}
    with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            text_by_order[int(row["order"])] = row["text"].strip()

    payload = build_request(text_by_order)

    source = "jev"
    try:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise RuntimeError("TYPESAFE_API_KEY not set")
        resp = call_jev(payload, api_key)
        answers = resp.get("answers", {})
        # 归一化：section_01_... -> section_01
        picks = {}
        for qid, ans in answers.items():
            if qid == "title_or_closing":
                continue
            sec = qid.split("_")[0] + "_" + qid.split("_")[1]
            picks[sec] = {
                "choice": ans.get("choice"),
                "confidence": ans.get("confidence"),
                "probabilities": ans.get("probabilities", {}),
            }
    except Exception as e:  # noqa
        print(f"[warn] live Jev unavailable ({e}); using local fallback", file=sys.stderr)
        source = "local_fallback"
        picks = local_fallback(text_by_order)

    # 组装 per-caption 映射
    caption_image = {}
    sections_out = []
    # Noul 决定性路由：标题(1)与结尾金句(81-82)必须用 result-bridge
    noul_ans = answers.get("title_or_closing", {})
    noul_val = float(noul_ans.get("noul", 0.0))
    for i in (1, 14):
        a, b = SECTIONS[i - 1]
        for o in range(a, b + 1):
            caption_image[str(o)] = "result-bridge"
        sections_out.append({
            "id": i, "orders": [a, b], "image": "result-bridge",
            "confidence": noul_val, "probabilities": {},
            "judgment": "noul_title_or_closing",
        })
    # 其余 12 个正文片段走 Choice
    for i, (a, b) in enumerate(SECTIONS, 1):
        if i in (1, 14):
            continue
        sec = f"section_{i:02d}"
        choice = picks.get(sec, {}).get("choice") or "atmosphere-1"
        if choice not in IMAGE_CRITERIA:
            choice = "atmosphere-1"
        for o in range(a, b + 1):
            caption_image[str(o)] = choice
        sections_out.append({
            "id": i, "orders": [a, b], "image": choice,
            "confidence": picks.get(sec, {}).get("confidence"),
            "probabilities": picks.get(sec, {}).get("probabilities"),
        })

    out = {"source": source, "model": payload["model"] if source == "jev" else None,
           "caption_image": caption_image, "sections": sections_out}
    with open(os.path.join(EP, "scene-map.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"source={source}")
    for s in sections_out:
        print(f"  S{s['id']:02d} cap{s['orders'][0]}-{s['orders'][1]} -> {s['image']} "
              f"(conf={s['confidence']})")
    print(f"wrote episodes/死屋手记/scene-map.json")


if __name__ == "__main__":
    main()
