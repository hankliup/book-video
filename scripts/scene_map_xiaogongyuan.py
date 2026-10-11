#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《小公务员之死》氛围图重选：TypeSafe Jev（System One Choice 原语）
从 30 张 Pexels 候选中，按 10 个剧情段各选 1 张最贴合的图。

为什么按段选而非逐句：渲染时同段连续行共用同一张背景，段级选图保证视觉连贯。
API 不可达时回退到本地透明打分，并在产物里标注 source。

产物：episodes/小公务员之死/ts-image-map.json
"""
import json, os, sys, urllib.request, urllib.error

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "小公务员之死")
OUT = os.path.join(EP, "ts-image-map.json")

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

# ---------- 候选图（Pexels 竖版，key = px-{id}） ----------
options = json.load(open("/tmp/pexels-final.json"))
IMAGE_DESC = {o["key"]: f"[{o['query']}] {o.get('alt', o['id'])}" for o in options}

# ---------- 剧情段定义（order 区间 + 主题摘要） ----------
SEGMENTS = [
    (1, 10, "开头钩子：人真正害怕的不是灾难贫穷死亡，而是别人怎么看自己"),
    (11, 20, "切尔维亚科夫去剧院看戏，打喷嚏喷到前排将军身上，将军说没关系"),
    (21, 30, "他开始不断回想自己是否冒犯了将军，一次又一次去道歉"),
    (31, 40, "将军彻底失去耐心对他发火，第二天又去道歉被赶出门，回家躺下死了"),
    (41, 50, "契诃夫最厉害的地方：长期生活在等级权力恐惧中，人会自己成为自己的审判者"),
    (51, 60, "在他的世界里上级永远是对的，他必须不断确认有没有犯错，道歉变成无法停止的自我折磨"),
    (61, 70, "外部压迫被内化后即使没人逼迫也会主动服从；契诃夫冷静告诉你看看这个人是怎么把自己逼上绝路的"),
    (71, 80, "现实中的类似循环：说错话不断复盘，领导皱眉就胡思乱想，别人没回消息就怀疑得罪了对方"),
    (81, 90, "我们越来越在意别人评价、害怕冲突、习惯讨好；忘记别人不高兴不等于你做错了"),
    (91, 100, "真正的悲剧不是死去的小公务员，而是活着却失去独立判断能力；真正困住我们的是住进心里的别人"),
]


def local_fallback(seg, seg_idx):
    """本地透明打分：按关键词命中排序，保证 API 挂了也能出结果。"""
    scored = []
    for key, desc in IMAGE_DESC.items():
        score = 0
        dl = desc.lower()
        if "theatre" in dl or "theater" in dl: score += 2
        if "man" in dl or "suit" in dl: score += 1
        if "lonely" in dl or "alone" in dl: score += 2
        if "dark" in dl or "night" in dl: score += 1
        if "office" in dl or "corridor" in dl or "staircase" in dl: score += 1
        if "bed" in dl or "candle" in dl: score += 1
        if "window" in dl or "silhouette" in dl: score += 1
        if "fog" in dl or "street" in dl: score += 1
        if "face" in dl or "portrait" in dl or "fear" in dl: score += 2
        scored.append((key, score, desc))
    scored.sort(key=lambda x: -x[1])
    top = scored[:3]
    return {
        "chosen": top[0][0],
        "probabilities": {t[0]: round(0.5 - i * 0.15, 2) for i, t in enumerate(top)},
        "confidence": 0.4,
        "source": "local_fallback",
        "reason": "keyword score: " + "; ".join(f"{k}={s}" for k, s, _ in top),
    }


def call_typesafe(state_text, criteria):
    """按 ts_image_select.py 的已验证请求格式调用 TypeSafe。"""
    key = os.environ.get("TYPE_SAFE_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("missing TYPE_SAFE_API_KEY / TYPESAFE_API_KEY")
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
    segments_out = []
    order_map = {}
    source = "typesafe-jev"
    api_ok = True

    for i, (a, b, theme) in enumerate(SEGMENTS, 1):
        seg_text_lines = []
        # Load actual script text for this range
        try:
            import csv
            with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    o = int(row.get("order", 0))
                    if a <= o <= b:
                        seg_text_lines.append(row["text"].strip())
        except Exception:
            pass
        state = f"【剧情段主题】{theme}\n\n【旁白文案】\n" + "\n".join(seg_text_lines)

        try:
            chosen, probs, conf = call_typesafe(state, IMAGE_DESC)
            segments_out.append({
                "order_start": a, "order_end": b, "theme": theme,
                "chosen": chosen, "probabilities": probs, "confidence": conf,
            })
            for o in range(a, b + 1):
                order_map[str(o)] = chosen
            print(f"段{i} ({a}-{b}) -> {chosen} (conf={conf:.2f})")
        except Exception as e:
            if api_ok:
                print(f"API error on segment {i}: {e}; falling back to local scoring for all")
                api_ok = False
            r = local_fallback((a, b, theme), i)
            segments_out.append({
                "order_start": a, "order_end": b, "theme": theme,
                "chosen": r["chosen"], "probabilities": r["probabilities"],
                "confidence": r["confidence"], "source": r["source"],
            })
            for o in range(a, b + 1):
                order_map[str(o)] = r["chosen"]
            print(f"段{i} ({a}-{b}) -> {r['chosen']} [local_fallback]")

    out = {
        "source": "typesafe-jev" if api_ok else "local_fallback",
        "segments": segments_out,
        "order_map": order_map,
        "candidate_count": len(IMAGE_DESC),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {OUT} (source={'typesafe-jev' if api_ok else 'local_fallback'}, {len(segments_out)} segments)")


if __name__ == "__main__":
    main()
