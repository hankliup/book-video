#!/usr/bin/env python3
"""按 create-episode-preview.mjs 的均分切点规则，从 body-timings.json 反算 scene-map 的行区间。

预览器把正文时长按 atmosphere 图数均分（sceneStarts[k] = safeDur * k / sceneCount），
scene-map.json 的 order_start/order_end 必须与这套切点落在同一批行上，否则场景与成片不一致。
layer 0 固定为 static result-bridge（片头结果页定格图，与正文首帧同图）。
"""
import json, os, sys

EP = sys.argv[1] if len(sys.argv) > 1 else "异乡人"
SEG = sys.argv[2] if len(sys.argv) > 2 else "body"
root = os.getcwd()
epdir = os.path.join(root, "episodes", EP)
sub = "images-body" if SEG == "body" else "images-hook"
imgdir = os.path.join(epdir, sub)

files = sorted(
    [f for f in os.listdir(imgdir) if f.startswith("atmosphere-") and f.endswith(".png")],
    key=lambda n: int(n.split("-")[1].split(".")[0]),
)
count = len(files)
if count == 0:
    raise SystemExit(f"no atmosphere-*.png in {imgdir}")

tim = json.load(open(os.path.join(epdir, "audio", f"{SEG}-timings.json")))
# 兼容 byOrder / captions 两种结构
items = tim.get("captions") or tim.get("items")
if items is None:
    byOrder = tim.get("byOrder")
    items = [{"order": int(k), "start": v["start"], "end": v["end"]} for k, v in byOrder.items()] if byOrder else []
items = sorted(items, key=lambda r: r["order"])
if not items:
    raise SystemExit("no caption rows in timings")
duration = tim.get("duration") or items[-1]["end"]
max_order = items[-1]["order"]

# 预览器：useBridge 时首段从 0.32 起，末段到 safeDur
use_bridge = SEG == "body"
start0 = 0.32 if use_bridge else 0.0
span = duration - start0

def row_at(t):
    # 找到包含时刻 t 的行号；t 落在行间空隙时取下一行
    for r in items:
        if r["start"] <= t < r["end"]:
            return r["order"]
    for r in items:
        if t < r["start"]:
            return r["order"]
    return max_order

layers = []
# layer 0：result-bridge 覆盖第 1 行（片头结果页定格，与正文首帧同图）
layers.append({
    "index": 0, "name": "result-bridge", "order_start": 1, "order_end": 1,
    "theme": "定格页：被太阳晒白的灰泥墙与地中海光", "static": True,
})

bounds = [start0 + span * k / count for k in range(1, count)]
# 每个图层的行区间：从它的切点前一行开始，到下一个切点所在行结束
starts = [start0] + bounds
ends = bounds + [duration]
for i, fname in enumerate(files):
    o_start = row_at(starts[i])
    o_end = row_at(ends[i] - 0.01)
    if o_end < o_start:
        o_end = o_start
    layers.append({
        "index": i + 1, "name": fname[:-4], "order_start": o_start, "order_end": o_end,
        "theme": "（待人工填写）",
    })

# 保证连续覆盖 rows 1..max_order（预览器按 order 取图层，缝隙会露白）
for i in range(1, len(layers)):
    if layers[i]["order_start"] != layers[i - 1]["order_end"] + 1:
        layers[i]["order_start"] = layers[i - 1]["order_end"] + 1
layers[-1]["order_end"] = max_order

scene = {
    "episode": EP,
    "note": "层号即渲染顺序。create-episode-preview.mjs 按 atmosphere-*.png 张数在正文时长上均分切点，本文件的 order_start/order_end 由 body-timings.json 按同一规则反算；改图数或改轴之后必须重算，否则 scene-map 与成片不一致。",
    "fps": 30,
    "motion": {"zoom_start": 1.05, "zoom_per_10s": 0.045, "zoom_travel_min": 0.09, "zoom_travel_max": 0.15, "drift_y_px": -10},
    "layers": layers,
}
out = os.path.join(epdir, "scene-map.json" if SEG == "body" else "scene-map-hook.json")
json.dump(scene, open(out, "w"), ensure_ascii=False, indent=2)
print(f"wrote {out}: {count} atmosphere + result-bridge, rows 1..{max_order}")
for L in layers:
    print(f'  L{L["index"]:>2} {L["name"]:<16} {L["order_start"]:>3}-{L["order_end"]:<3}')