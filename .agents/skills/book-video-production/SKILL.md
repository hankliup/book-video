---
name: book-video-production
description: Book Video 仓库的读书类短视频全流程编排：定位仓库并跑 init 自检、选书与候选入库、正文文案与两行钩子、抖音/视频号发布文案、Pexels 实拍配图与溯源、配音与 ASR token 字幕轴、scene-map 与三段渲染、交付门禁、独立片头、替换旧成片和 tmp 回收。用户要求做一期读书视频/图书带货视频、写钩子或发布文案、配图、对齐字幕、渲染成片、重渲或替换旧片、验收交付，或询问这个仓库的制作流程时使用；触发词：读书视频、图书视频、book video、做一期、钩子、发布文案、字幕轴、成片、重渲、交付门禁。
agent_created: true
---

# Book Video 生产编排

## 定位

本 skill 是 Book Video 仓库的**流程启动器**，不替代仓库真源。真源优先级：

1. `AGENTS.md`（在本仓库自动加载；含六道生产闸、视觉/听觉规则和事故沉淀）
2. `docs/book-video-playbook.md`（完整 SOP）
3. `docs/rules/*.md`（按主题拆分的规则本体，索引见 AGENTS.md 的 Rule Index）
4. 脚本自身的 docstring 与 `--help`
5. 本 skill 的阶段编排（只负责顺序、入口命令和检查点）

冲突时以 1–4 为准。**不要用本 skill 里看到的数字覆盖规则文件**：阈值、命令参数、门禁 token 都可能已经随事故移动。每个阶段开工前读对应规则文件，改门禁时把判定措辞与钉住它的 token 一起改。

一期成片 = **片头钩子（本期专属）→ 固定开场白（模板）→ 正文（本期专属）**，缺一不可。

## 开工前

1. 定位仓库根：`git rev-parse --show-toplevel`。失败时看当前目录是否有 `scripts/init.mjs`、`episodes/`、`docs/book-video-playbook.md`；仍找不到就问用户仓库路径。**不要硬编码既往 clone 路径。**
2. 读 `.book-automation-state.json`。若 `weread` 已是 `enabled`，**不要再问微信读书 Key**；只有初始化结果明确缺少 Key 时才按 AGENTS.md 的步骤引导用户获取并安全写入 `.env`（`0600`，不回显、不作为命令参数）。
3. 在仓库根运行 `node scripts/init.mjs`。它幂等：不重装已验证 Skill、不覆盖有效 Key、不重置用户选择。一次检查 Node.js、`npx`、FFmpeg/FFprobe、`whisper-cli`、Whisper 模型。**以退出状态和 JSON 结果为准**，不要用 `command -v` 或解析版本横幅代替。
4. 缺本机依赖时集中列出并征得**一次**确认后再安装；不要静默改系统包。Whisper 模型看 `assets/models/whisper/ggml-base.bin` 的存在与大小（有效文件至少 100 MB），不要用目录列表判断。
5. HyperFrames 走 `npx hyperframes@0.7.33`。遇到 `ENETUNREACH` / `EACCES` / `ENOTFOUND` / `ENOTCACHED` / registry 被拦，按环境/网络问题立即用执行工具的网络升级重试，不向用户二次要确认。
6. 判断任务模式，再只读该模式需要的规则。用户没有明确说“重渲/替换/只改某项”时，先判断最小改动面，不要默认整期重做。

## 任务模式

| 用户意图 | 走法 |
| --- | --- |
| 新做一期 | 选书 → `brief.json` → 正文/钩子/发布文案 → 配图与封面 → 配音与字幕轴 → scene-map → `render-hook-batch.sh` → 交付 → 回收 |
| 继续某期 / 不知道停在哪 | 先跑该期交付门禁与渲前闸看红灯，再读 `tmp/render-b3/<书名>.log`；**不要因为“文件都在”就判定完成** |
| 只改封面 | 改 `brief.json` 的 `cover_hook` 等字段 → `EPISODE=<书名> python3 scripts/make_episode_cover.py` → 封面相关门禁。**不重渲成片** |
| 只改发布文案 | 改 `brief.json` 的 `publish_caption_douyin` / `publish_caption_wechat` → `check-publish-caption.py` → 同步 `发布文案.md`。**不重渲成片** |
| 改正文/钩子文案 | 先判断该期是否已有冻结录音；有录音时改文字不等于改声音，按 AGENTS.md 的冻结录音规则处理。改完重跑 `validate-script.mjs`，再按改动范围重建轴与像素 |
| 改图片 | 先过取图与溯源门；已交付期属于替换旧成片，读 `docs/rules/replace-media-gate.md` 及其细则后再动 |
| 换配音 / 换钩子录音 | 钩子走 `prepare-hook-segment.sh`；正文走配音处理 → 五步字幕轴 → 重渲。变化的时长会牵动水波、片头裁切与装配，不能只替换音频文件 |
| 只补独立片头 | 有成片且静默片头在：`python3 scripts/render-intro-only.py "<书名>" --intro <silent>`；连静默片头都没有时先按 `census-intro-only.py` 的指示重建，不伪造 |
| 验收 / 全库盘点 | `check-episode-delivery.py`、`composition-digest.py verify`、`census-intro-only.py`、`npm run check`；按各脚本自己的红/⚠️ 口径读，不把 ⚠️ 说成绿 |

## 生产阶段

### 1. 选书

- 用户点名书：先核实书名、作者、版次，再建期。歧义版本、解读本、缩编本必须让用户确认。
- 未点名：读 `data/book-pipeline.csv`；空或不可用时只问一个问题（题材/情绪/受众）。有偏好按偏好搜，无偏好选文学/哲学、情绪共鸣强的书。
- 每次检索的完整结果**先落盘再推荐**：`node scripts/record-book-candidates.mjs <candidates.json>`（字段至少 `display_title`、`source_title`、`author`、`source_channel`、`source_book_id`；其余可选字段见脚本）。禁止只在对话里给结果。
- 从已记录行里推荐五本并标出首选，等用户选书后再写稿。
- 微信读书 Skill 可用时作为首选研究源（书籍信息、热门划线、公开点评），但只作研究信号，不整段复制。

### 2. 书目与文案

- 建 `episodes/<书名>/brief.json`。核心字段：`display_title`、`source_title`、`author`、`source_book_id`、`source_channel`、`script_version`、`script_mode`、`length_mode`、`image_pipeline`、`cover_hook`、`cover_tag`、`hook_design`、`publish_caption_douyin`、`publish_caption_wechat`、`publish_caption_mode`。用 `display_title` 做目录名和可见书名；`source_title` 保留原始检索结果，不要覆盖。
- 正文真源是 `episodes/<书名>/script-body.csv`（`version,order,text`；第 1 行书名，正文从第 2 行念）。钩子真源是 `script-hook.csv`（两行，金句 + 代价；每行一个完整口语单位）。**不要**重新引入裸 `script.csv` / `文案.md` 作为机器真源。
- 文案默认长档；短档只在用户明确要求时用。长度与语速口径只在 `scripts/lib/script-policy.mjs`，不要凭记忆写数字。
- 写完先跑 `node scripts/validate-script.mjs "<书名>"`，未通过就在内部改到通过，**不要先发给用户确认，也不要拖到渲染阶段**。
- 钩子按 5 问自检：与观众有关、替观众省掉损失、反常识、观众经历过、留下想补的缺口。开场优先“具体故事/创作年数反差 + 《书名》 + 代价落回观众”；禁用“你有没有过这样的瞬间：”式空壳提问。封面钩子 ≤ 22 字（以 `make_episode_cover.py` 的 `HOOK_CHARS` 为准），**金句不印在封面上**，封面用问句或代价句；封面与开头同主题，但不得复述第二行。
- 钩子草稿走 `data/hook-drafts/<书名>.json`，用 `python3 scripts/promote-hook-draft.py` 提升；只有用户明确批准后才写 `approved`，不得代用户批。写盘前用 `python3 scripts/check-hook-openers.py --plan <书名…>` 撞语料与封面复述；已录期用 `--cover-echo`，按 🔒/exit-0 读，不逼已冻结的录音重写。
- 发布文案在**文案确认阶段**就写进 `brief.json` 两个字段，不要只留在命令行或上传描述里；默认长版（含《书名》+ 情节 + 金句 + 具体立场二选一）。交付阶段由字段生成 `发布文案.md` 快照，两处必须逐字一致。

### 3. 配图与本书封面

- 背景图**只用 Pexels 实拍**，不用 AI 生成；正文段落 `images-body/`，钩子段落 `images-hook/`，两段同一条调色口径。取候选时带 `PROVENANCE_OUT=episodes/<书名>/pexels-fetch-log-*.jsonl`，当场记 photo id、摄影师、链接。
- 选型 → 锁定 → 按 id 取图 → 调色 → 补压亮区 → 生成 `prompts.csv` → 溯源门。锁定按“候选目录/格子编号”程序化取 id，不手抄；补锁一张要带全该段，`lock-pexels-candidates.py --apply` 是整段替换语义。
- 定稿前逐张按原始尺寸打开看穿帮（招牌、现代汽车、人脸、文字、EXIT 牌）；人工否决写进 `data/pexels-rejected.json`，不要只口头说。
- 每期必须有 `book-cover.jpg` 与 `book-cover.json` 旁证；缺它渲染器会跳过封面层而不报错。取图：`python3 scripts/fetch-book-cover.py "<书名>"`（优先微信读书版次；多版次用 `--book-id` 或人工核验 `--from`）。
- 运行 `python3 scripts/check-image-provenance.py "<书名>"`。`source_width` 必须记实际值；跨期去重与同期复用（`reuse_of`）按 `docs/rules/` 对应文件处理。

### 4. 配音与字幕轴

- 用户录的正文口播用 story 预设处理，保留仓库约定的两个文件名（canon 与 `-story`）；具体走哪一份由 `scripts/lib/voice-audio.json` 的解析表决定，不要在门禁/生成器里各写一份默认值。
- **字幕文本真源永远是 `script-body.csv`**；ASR 只提供时间。长配音的五步：`asr-segmented.mjs` → `asr-to-simplified.py` → `create-body-timings-precise.mjs` → `snap-body-timings-vad.py --apply` → `audit-caption-axis.py --apply`。覆盖必须排在能量审计之前；审计的行首规则要跑两遍。
- 渲前用 `python3 scripts/check-axis-before-render.py "<书名>"` 做判决；它复用的是既有三道见证脚本的输出，红就按它给的就地修轴顺序处理，修完再量，仍红不许渲。
- 一处语音窗错、别的行都对时改 ASR 段（`retime-asr-segments.py --rows`），不要为修一行重跑整篇 ASR，也不要把行尾顶进后面的静音。
- 录音已冻结：文字与录音不一致时，按 AGENTS.md 的规则同步文字或重录；Agent 不能替用户“假装重录”。

### 5. scene-map 与渲染

- `scene-map.json` 把正文行区间映射到 `images-body/` 的图；层区间必须覆盖 1..N、相邻层不同图、文件真实存在。先按时间轴生成再人工调语义切点，最后盖行锚点：`python3 scripts/stamp-scene-map-anchors.py "<书名>"`。改文案导致行号移动时，锚点会先红，不要只改 `order_end` 让门禁闭嘴。
- 渲前闸（批驱动会自动跑，也可手动先跑）：`check-cover-and-caption-before-render.py`、`check-axis-before-render.py`、`check-scene-map-before-render.py`、`check-caption-rows.py`。
- 主入口：`bash scripts/render-hook-batch.sh "<书名>"`。它依次做渲前串行化、输入闸、轴闸、scene-map 闸、字幕切条闸、生成预览工程、渲钩子/片头并扫伪影、兜底渲正文、跑交付门禁、组成摘要对账、混独立片头。**不要用 `npx hyperframes render` 单段拼一期来代替这条链**，除非明确在做诊断且知道四个手装坑（哈希预览目录、`ALLOW_OVER_60_SECONDS=1`、按 `script-hook.csv` 是否存在决定渲几段、钩子先归一到 30fps 再按秒 trim）。
- 每期日志在 `tmp/render-b3/<书名>.log`。**退出码不是成败判据**：读日志里的 `[FATAL]`、门禁 `✅/❌`、`rc=` 与组成摘要行。批驱动还会写 `tmp/render-b3/claim/<书名>.pid`；同一期被别的会话认领时不要并发渲。
- 渲前串行化会等共享真源静默；不要用 `HF_SETTLE_BYPASS=1` 图快，除非有可落盘的理由。另一个会话正在改共享渲染器/门禁时开渲，可能得到“一半旧一半新”的像素而门禁全绿。

### 6. 交付、验收与回收

- 交付门禁总入口：`python3 scripts/check-episode-delivery.py "<书名>" --tmp "tmp/<书名>_tpl" --intro-trim 3.0`。它一次跑完像素、字幕、轴、封面层、发布文案等全部交付门；**不要只跑其中一两道就宣布交付**。`--skip-axis` 只用于历史审计，新交付不得跳过。
- 门禁之后跑 `python3 scripts/composition-digest.py verify "<书名>"`：像素是不是这一代生成器渲的，只有重渲能修。
- 成片交付物是两条：`episodes/<书名>/renders/<书名>-final.mp4` 与 `episodes/<书名>/renders/intro-only.mp4`。缺独立片头按 `census-intro-only.py` 的五族桶处理，不要用成片冒充。
- 在对话里用**绝对路径的 Markdown 媒体引用**直接展示两条成片，并给路径、时长、规格；只给路径不算交付。
- 发布前跑 `npm run check`。它会在第一个 `throw` 处中止，**读 `EXIT=` 行与日志**，不要把“扫描到某一项”当成全绿；若它退出 0，再按 AGENTS.md 验证 Git 历史与私有素材。
- 回收只在交付判绿之后：先 `node scripts/cleanup-tmp.mjs`（干跑）读行，再 `--apply`。有并行渲染时用 `--only <书名>`，不要让回收碰正在渲染的期。回收后若又跑了一遍交付门禁，质检截图会重新写回 `tmp/`，需要再回收一次。
- **durable 产物永远不要放 `tmp/`**：`tmp/` 被外部清空过一次（6.3 GB 连带见证一起没了）。成片、发布文案、台账都在 `episodes/`。

## 改规则或门禁时

这个仓库的规则多数来自真实事故，改它们不是改文案：

1. 先读对应 `docs/rules/*.md` 与门禁脚本，找到判据真源；不要在 skill、AGENTS、脚本里各留一份阈值。
2. 改判定措辞时同步改钉住它的 token / 测试 / `check.mjs` 守卫；否则门禁会在 fail-fast 下游变成不可达的地雷。
3. 新门禁要有健康产物跑绿、不相干产物跑红、以及全库 old-vs-new A/B；防空转的红与“判不了＝没测，不是红”都要在输出里写清。
4. 不许靠压暗、降观感、删注册、放宽阈值来让红变绿；修的是判据形状或产物本身。
5. 用户没有明确裁决时，不要以“用户要求”的名义改范围。授权要在日志里找得到原话，不能只写在注释或本 skill 里。

## 资源

- 详细阶段命令、产物清单与常见红灯：[references/runbook.md](references/runbook.md)
- 生产闸 / 交付门与规则文件对照：[references/gate-map.md](references/gate-map.md)
- 完整 SOP：`docs/book-video-playbook.md`；规则索引：`AGENTS.md` 的 Rule Index。
