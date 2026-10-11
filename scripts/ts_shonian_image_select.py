#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 TypeSafe Jev（System One Choice 原语）为《少年》(version B, 113 行旁白) 每个剧情段重选最贴合的背景配图。

产物：episodes/少年/ts-image-map.json
  { "source": "jev", "model": "jev-latest",
    "segments": [ {order_start, order_end, theme, chosen, probabilities, confidence, fixed} ],
    "order_map": { "<order>": "<image_no_ext>" } }

API key 仅从 .env / 环境变量 TYPESAFE_API_KEY 读取，绝不写盘。
"""
import json, os, csv, sys, time, urllib.request, urllib.error

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "少年")
OUT = os.path.join(EP, "ts-image-map.json")

def load_key():
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("TYPESAFE_API_KEY")

api_key = load_key()
assert api_key, "TYPESAFE_API_KEY not found in .env or environment"

text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row["version"] == "B":
            text_by_order[int(row["order"])] = row["text"].strip()

# 候选图语义（来自 prompts.csv 的生成提示词）
IMG_DESC = {
    "result-bridge": "The wide Neva River in St. Petersburg before dawn, a wrought-iron bridge silhouetted against a pale cold sky, a lone small figure standing on the embankment — reserved for the opening title card and the final closing lines only.",
    "atmosphere-1": "A solitary teenage boy in a threadbare coat and scarf, hunched against the cold, walking alone under a flickering gas streetlamp on a snowy 19th-century St. Petersburg street at night, snow falling, warm amber lamp glow in cold blue shadow — the young protagonist himself, loneliness, youth, ambition in the cold.",
    "atmosphere-2": "Interior of a cold 19th-century Russian boarding school: a dim wooden corridor with frost-covered windows, pale grey winter light, worn wooden floorboards, a bare wall sconce, lonely and austere — the boarding-school humiliation, 'you are a bastard' years, being mocked for his surname.",
    "atmosphere-3": "A sumptuous 19th-century Russian aristocratic drawing room at night, crystal chandelier, warm candlelight over mahogany, a crumpled secret letter clutched in a young man's trembling hand, opulent red drapes — the upper-class world, the secret letter, inheritance intrigue, the gambling temptation's beginning.",
    "atmosphere-4": "A dim 19th-century Russian gambling hall at night, green felt card table lit by flickering candlelight, scattered banknotes and coins, a young man's tense pale face half in shadow, thin smoke — gambling, ruin, being accused of cheating, the night of total failure in the snow.",
    "atmosphere-5": "A solitary old Russian peasant pilgrim walking across a vast snowy wilderness at dusk, holding a small lantern, ragged humble clothes, peaceful weathered face with white beard, endless snowfield under a bruised winter sky — Makar the old man: lifelong deprivation, forgiveness instead of resentment, pilgrimage, inner peace.",
    "atmosphere-6": "A humble wooden cabin interior, a narrow bed where an old man lies dying peacefully, a small Orthodox icon in the corner emitting a faint holy glow, a single candle, a young man sitting silently at the bedside — Makar's deathbed: endurance, love, beauty as the most important thing, 'I take responsibility for my own life'.",
}

# 剧情段（连续 order 区间，基于 113 行旁白的叙事脉络划分；段数 = 候选图数 7）
SECTIONS = [
    (1, 1, "title card"),
    (2, 30, "the pain of not knowing who you want to be; Arkady's obsession to become Rothschild: money as the only way to buy dignity, freedom and strength"),
    (31, 50, "failure: the more he pursues independence the more his emotions drag him down; the father Versilov: a complex, admired and despised man; 'growing up is re-seeing one's parents'"),
    (51, 70, "real youth: many ideas, little experience; wealth cannot free a mind ruled by envy, vanity and dependence; value built on the outside world is always led by it"),
    (71, 89, "true growth: who I want to be matters more than what I want to get; we keep proving ourselves with money, status and better life; but who am I really?"),
    (90, 100, "it writes about everyone still searching for themselves; whether you left school long ago or already earned the money you dreamed of, if you still don't know why you live, you are still a young boy"),
    (101, 113, "final: you must make your own choices, bear your own consequences, and answer slowly who I am and what kind of life I am willing to spend for what I want; only then does the boy truly grow"),
]

FIXED = {1: "result-bridge", 113: None}  # 标题固定；结尾交给 Jev（Jev 通常会把收束段选给 result-bridge）

STATE = (
    "Narration script (version B, 113 lines) for Dostoevsky's novel 'The Adolescent' (少年, Russian: Podrostok), "
    "a 19th-century story about Arkady, a 19-year-old illegitimate nobleman who wants to become 'Rothschild' — rich, independent, free — "
    "and learns that money cannot buy dignity or selfhood. "
    "Assign each narration section the single background image whose mood and subject best fit it. "
    "The result-bridge image (dawn over the Neva river) is reserved for the opening title card and the final closing lines only. "
    "Body sections should use atmosphere-1 through atmosphere-6, and avoid repeating an image that a neighboring section already uses."
)

questions = {}
for i, (a, b, theme) in enumerate(SECTIONS, 1):
    section_text = " ".join(text_by_order[o] for o in range(a, b + 1))
    questions[f"section_{i:02d}_cap{a}-{b}"] = {
        "type": "choice",
        "instructions": {
            "section_theme": theme,
            "section_text": section_text,
            "question": "Which single background image best matches the mood and subject of this narration section?",
        },
        "criteria": dict(IMG_DESC),
    }
payload = {"state": STATE, "model": "jev-latest", "questions": questions}

url = "https://api.typesafe.ai/v1/systemone"
last_err, resp = None, None
for attempt in range(4):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
        break
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        last_err = f"HTTP {e.code}: {body}"
        if e.code in (429, 529):
            time.sleep(min(2 ** attempt + 1, 16)); continue
        break
    except Exception as e:
        last_err = str(e); time.sleep(2 ** attempt)
if resp is None:
    print(f"Jev failed: {last_err}", file=sys.stderr)
    sys.exit(1)

answers = resp.get("answers", {})
segments, order_map = [], {}
for i, (a, b, theme) in enumerate(SECTIONS, 1):
    qid = f"section_{i:02d}_cap{a}-{b}"
    ans = answers.get(qid, {})
    choice = ans.get("choice") or "atmosphere-1"
    if choice not in IMG_DESC:
        choice = "atmosphere-1"
    if a in FIXED and FIXED[a]:
        choice = FIXED[a]
    probs = {k: round(v, 4) for k, v in (ans.get("probabilities") or {}).items()}
    conf = ans.get("confidence")
    for o in range(a, b + 1):
        order_map[str(o)] = choice
    segments.append({
        "order_start": a, "order_end": b, "theme": theme,
        "chosen": choice, "probabilities": probs,
        "confidence": conf, "fixed": (a in FIXED and bool(FIXED[a])), "jev_qid": qid,
    })

out = {"source": "jev", "model": "jev-latest", "episode": "少年", "scriptVersion": "B",
       "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
       "segments": segments, "order_map": order_map}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("wrote", OUT)
for s in segments:
    print(f"  cap{s['order_start']:3d}-{s['order_end']:3d} -> {s['chosen']:14s} conf={s['confidence']} probs={s['probabilities']}")
