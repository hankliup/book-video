# Book Video 阶段执行手册

所有命令默认在**仓库根**执行。仓库脚本大量使用 `process.cwd()`，不要在子目录里裸跑。

## 期目录与产物

```text
episodes/<书名>/
  brief.json                  # 书名/作者/来源、脚本版本、封面与发布文案字段
  script-hook.csv             # 钩子真源：version,order,text
  script-body.csv             # 正文真源：version,order,text（第 1 行书名）
  prompts.csv                 # 已上屏图片的 Pexels 溯源行
  scene-map.json              # 正文行区间 → images-body/ 图层（含行锚点）
  book-cover.jpg / .json      # 本书封面图上屏层 + 版次旁证
  images-body/                # 本地忽略
  images-hook/                # 本地忽略
  audio/
    body-voiceover*.mp3       # canon 与 story 两代；走 lib/voice-audio.json 解析
    hook-voiceover*.mp3
    body-timings.json         # 字幕轴
    hook-timings.json
    asr/                      # Whisper token 与逐段结果
  renders/
    <书名>-final.mp4          # 成片
    intro-only.mp4            # 独立交付片头
    封面-<书名>.png           # 发布封面
    hook.provenance.json      # 段对账单
    intro.provenance.json
  发布文案.md                 # brief 两个发布文案字段的快照
data/<书名>_pexels_ids.json   # 已锁定图片 id（程序化生成，不手抄）
data/hook-drafts/<书名>.json  # 钩子草稿与审批状态
data/pexels-rejected.json     # 跨批次人工否决名单
```

## 0. 自检

```bash
git rev-parse --show-toplevel
node scripts/init.mjs
```

读 `.book-automation-state.json`。`weread: enabled` 时不再问 Key。缺依赖时集中报告并只征得一次安装确认。Whisper 模型看文件大小（≥100 MB），不要只看目录。

## 1. 选书与候选入库

```bash
# 候选 JSON 先落盘，再推荐
node scripts/record-book-candidates.mjs data/candidates-<slug>.json
```

候选 JSON 最少包含 `display_title`、`source_title`、`author`、`source_channel`、`source_book_id`。推荐五本并标一首选，等用户选书。

## 2. 书目、正文与钩子

建 `episodes/<书名>/brief.json`，用 `display_title` 做目录名。正文写 `script-body.csv`，钩子写 `script-hook.csv`。

```bash
node scripts/validate-script.mjs "<书名>"
```

未通过就在内部改到通过，不要先给用户确认。

钩子写入前：

```bash
python3 scripts/check-hook-openers.py --plan <书名…>
python3 scripts/promote-hook-draft.py "<书名>" --dry-run
# 用户明确批准后：
python3 scripts/promote-hook-draft.py "<书名>"
# 需要把批准落盘时，只照抄用户原话：
python3 scripts/promote-hook-draft.py <书名…> --approve="user: <用户原话>"
```

已录期的封面复述检查：

```bash
python3 scripts/check-hook-openers.py --cover-echo
```

发布文案写进 `brief.json` 后：

```bash
python3 scripts/check-publish-caption.py "<书名>"
```

交付阶段由 brief 两个字段生成 `发布文案.md`，逐字一致；不要重写一版。

## 3. 配图、调色与本书封面

### 3.1 候选与选型

```bash
# 复核尺寸候选；--exclude-used 去掉全库已上屏与人工否决
python3 scripts/pexels_candidates.py --out "tmp/<书名>/body/a1" --exclude-used "<query>"
# 或先拼联系表看构图
python3 scripts/mq-pexels-sheet.py --cache "tmp/<书名>/cache" "tmp/<书名>/sheet1.png" "<query1>" "<query2>"
```

候选只能筛构图。**定稿前逐张按原始尺寸看穿帮**：招牌、现代汽车、人脸、可辨认文字、绿色 EXIT 牌、天线。人工否决写进持久名单：

```bash
python3 scripts/reject-pexels-photo.py <id…> --reason "<对任何一期都不成立的理由>" --episode "<书名>"
```

### 3.2 锁定与取图

```bash
python3 scripts/lock-pexels-candidates.py "<书名>" picks.json --apply
python3 scripts/fetch-pexels-by-id.py "episodes/<书名>" "data/<书名>_pexels_ids.json"
```

`picks.json` 按「候选目录 + 格子编号」锁，不手抄 id。`lock-pexels-candidates.py --apply` 是**整段替换**：补锁一张时把该段已有条目一起带上，否则其余条目会被静默抹掉。钩子段条目加 `"dir": "images-hook"` 或 `"segment": "hook"`。

### 3.3 调色、补压、溯源行与门禁

```bash
IMAGES_SUBDIR=images-body python3 scripts/grade-atmosphere-images.py "episodes/<书名>"
IMAGES_SUBDIR=images-body python3 scripts/fix-bright-regions.py "episodes/<书名>"
python3 scripts/write-prompts-csv.py "<书名>" --apply
python3 scripts/check-image-provenance.py "<书名>"
```

`grade` 报偏亮是信号，不是中断；跑 `fix-bright-regions` 再复验。`source_width` 记实际像素宽度，不要填目标尺寸。

钩子段的取图、调色、配音处理与钩子轴由一条命令串起来（前提是 `data/<书名>_pexels_ids.json` 已有钩子条目）：

```bash
bash scripts/prepare-hook-segment.sh "<书名>"
```

### 3.4 本书封面图

```bash
python3 scripts/fetch-book-cover.py "<书名>"
# 多版次人工指定：
python3 scripts/fetch-book-cover.py "<书名>" --book-id <id>
# 网上找到直链 / 本地人工核验图：
python3 scripts/fetch-book-cover.py "<书名>" --url <https-url>
python3 scripts/fetch-book-cover.py "<书名>" --from <本地图片>
```

产出 `book-cover.jpg` + `book-cover.json`（sha256 旁证）成对才算数。缺图时渲染器会跳过封面层而不报错，所以渲前输入闸会拦。

### 3.5 发布封面

```bash
EPISODE="<书名>" python3 scripts/make_episode_cover.py
```

只重出封面、不重渲成片。书名一律单行，装不下缩字号；到绝对下限仍装不下才写 `cover_title`（只影响封面 PNG）。

## 4. 配音与字幕轴

正文口播用 story 预设处理（具体输入输出文件名按 `scripts/lib/voice-audio.json` 与当期已有文件）：

```bash
node scripts/process-voiceover.mjs "<输入.mp3>" "episodes/<书名>/audio/body-voiceover-story.mp3" story
```

长配音的标准五步：

```bash
node scripts/asr-segmented.mjs "<书名>" "episodes/<书名>/audio/body-voiceover-story.mp3"
python3 scripts/asr-to-simplified.py "episodes/<书名>/audio/asr/body.json"
node scripts/create-body-timings-precise.mjs "<书名>"
python3 scripts/snap-body-timings-vad.py "<书名>" --apply
python3 scripts/audit-caption-axis.py "<书名>" --apply
```

覆盖（snap）必须在能量审计（audit）之前。audit 的行首规则要跑两遍；`check-axis-before-render.py` 会按既有次序就地补修一轮。

渲前判决：

```bash
python3 scripts/check-axis-before-render.py "<书名>"
```

单行语音窗错、别的行都对时，修 ASR 段而不是整篇重跑：

```bash
python3 scripts/retime-asr-segments.py "<书名>" --rows <行号>
# 然后按 precise → snap → audit 顺序重跑并重渲
```

## 5. scene-map 与渲染

```bash
python3 scripts/build-scene-map.py "<书名>" body
# 人工把均分切点调成语义段落；改完盖行锚点
python3 scripts/stamp-scene-map-anchors.py "<书名>"
```

渲前闸：

```bash
python3 scripts/check-cover-and-caption-before-render.py "<书名>"
python3 scripts/check-axis-before-render.py "<书名>"
python3 scripts/check-scene-map-before-render.py "<书名>"
python3 scripts/check-caption-rows.py "<书名>"
```

主入口：

```bash
bash scripts/render-hook-batch.sh "<书名>"
```

它串起渲前串行化、四道输入闸、预览工程、钩子/片头渲染与伪影扫描、正文兜底渲染、交付门禁、组成摘要对账、独立片头。单期约十分钟，长批用后台执行并读日志：

```bash
tail -120 "tmp/render-b3/<书名>.log"
```

退出码不是判据。读 `[FATAL]`、门禁 `✅/❌`、`rc=`、`组成摘要对账`。同一期被 `tmp/render-b3/claim/<书名>.pid` 认领时不要并发。

## 6. 交付、独立片头与回收

```bash
python3 scripts/check-episode-delivery.py "<书名>" --tmp "tmp/<书名>_tpl" --intro-trim 3.0
python3 scripts/composition-digest.py verify "<书名>"
python3 scripts/check-publish-caption.py "<书名>"
```

独立片头：

```bash
python3 scripts/render-intro-only.py "<书名>" --intro "tmp/preview-<slug>/intro/renders/intro.mp4"
python3 scripts/census-intro-only.py
```

`<slug>` 由 `scripts/lib/episode-slug.mjs` 算出，不要猜目录名：

```bash
node -e 'import("./scripts/lib/episode-slug.mjs").then(m=>console.log(m.slugifyEpisodeName("<书名>")))'
```

全库门禁：

```bash
npm run check
```

读日志里的 `EXIT=`，不要只看工具通知。一个根因可能同时拥有多个 `throw` 点，红在上游会让下游注册的检查根本没跑；需要测隐藏红时按 AGENTS.md 的 fail-fast 探针配方生成诊断副本，不要手改生产文件。

回收：

```bash
node scripts/cleanup-tmp.mjs               # 干跑，先读“可释放 / 保留”
node scripts/cleanup-tmp.mjs --apply
node scripts/cleanup-tmp.mjs --only "<书名>" --apply   # 并行渲染时按期回收
```

交付门禁重跑会把 `_qc/` 截图写回 `tmp/`，回收后再验要再回收一次。**不要**把成片、发布文案、台账放进 `tmp/`。

## 7. 常见红灯

| 红灯 / 日志 | 先看 | 修法 |
| --- | --- | --- |
| `已被 pid … 认领` | `tmp/render-b3/claim/` | 另一会话在渲同一期，等待；不要删它的认领 |
| settle 等不到 | 另一会话是否在改共享渲染器/门禁 | 等共享真源静默；不要用 bypass 图快 |
| `输入闸没过` | `book-cover.jpg` 与 `book-cover.json` | `fetch-book-cover.py`，再重跑本期 |
| `轴闸没过` | `check-axis-before-render.py` 的三道见证 | 按 `snap --apply → audit --apply ×2` 修，仍红不渲 |
| `scene-map 闸没过` | 层区间、切点、锚点 | 改 scene-map 或重盖锚点；不要只让红话闭嘴 |
| `字幕切条闸没过` | 超行的 `script-body.csv` 行 | 改文案断句；不要缩字号 |
| `渲染器没出片` / `rc=` | `tmp/render-b3/.renderer-<书名>.out` | 修渲染错误后重跑；不要拿旧成片跑门禁 |
| 交付门禁某道 ❌ | 门禁标签与它调用的脚本 | 按该门自己的红话修；不要跳过 |
| `composition-digest verify` 红 | 共享生成器是否在渲染后又被改 | 只能重渲 |
| `npm run check` 红 | 第一处 `throw` 与 `EXIT=` | 修根因；红在上游时下游检查未执行，不能宣称那部分绿 |
| 独立片头 🔁 | 成片比片头新 | `render-intro-only.py` 补一条 |
| 独立片头 🧩 / 🧱 | 静默片头是否还在 | 🧩 只差混音；🧱 先重建预览工程与 intro 段 |
