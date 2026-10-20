# Book Video 闸门与规则对照

这是路由表，不是判据本体。判据只在 `docs/rules/*.md` 与对应脚本里；文件名以 `ls docs/rules` 和 AGENTS.md 的 Rule Index 为准。改规则时连门禁与钉住措辞的 token 一起改。

## 六道生产闸（顺序不可交换）

| 顺序 | 阶段 | 规则入口 | 主要产物 |
| --- | --- | --- | --- |
| 1 | 文案成稿 | `docs/rules/script-length-gate.md`、`publish-caption.md` | `script-body.csv`、`script-hook.csv`、`brief.json` 的发布文案字段 |
| 2 | 文案过审后取背景图 | `docs/rules/image-sourcing-gate.md`，细则见 `image-provenance-chain.md`、`image-source-width.md`、`image-cross-episode-dedup.md`、`image-manual-rejection.md`、`image-network-retry.md` | `images-body/`、`images-hook/`、`prompts.csv`、`book-cover.jpg/.json` |
| 3 | 拿到旁白后建字幕轴 | `docs/rules/caption-axis-gate.md` | `body-timings.json`、`hook-timings.json`、`asr/` |
| 4 | 混音 | `docs/rules/mixing-gate.md` | 成片音轨、BGM/响度 |
| 5 | 替换旧成片 | `docs/rules/replace-media-gate.md`，细则见 `replace-media-coexistence.md`、`replace-media-activation.md`、`replace-media-ripple-order.md`、`replace-media-source-width.md`、`replace-media-delivered-pixels.md` | 新一代成片 + 对账单 |
| 6 | 交付 | `docs/rules/delivery-embed.md`、`intro-only-deliverable.md` | `renders/<书名>-final.mp4`、`renders/intro-only.mp4`、`发布文案.md` |

## 视觉 / 听觉规则族

| 文件 | 管什么 | 常见误判 |
| --- | --- | --- |
| `segment-structure.md` | 三段结构、共用片头 3.0s trim | 把钩子段当模板段；裁点落在滚动书单上 |
| `render-concurrency.md` | 渲染并发、机器状态、共享片头 | 两个会话抢 npx 缓存；一半旧一半新 |
| `intro-flicker-ruling.md` | 片头闪屏裁决与归因 | 把重试能压掉的一次伪影当成稳定缺陷 |
| `intro-flicker-device.md` | 片头防频闪装置、水波同窗 | 改共享片头观感；只想压低亮度 |
| `disclaimer-overlay.md` | 正文常驻来源声明 | 声明层被封面/书名层盖住 |
| `book-cover-overlay.md` | 封面/书名层版位、字号、行距 | 书名折行、缩到 96px 以下、用 AI 补图 |
| `subtitles.md` | 字符流、时间轴、显示 | 屏上残留标点/空白；按字数切字幕 |
| `source-floor.md` | 素材底线：Pexels 实拍、不用 AI、无第三方水印 | 用生成图补背景；留着水印 |
| `image-same-episode-reuse.md` | 同期复用同一张图（意象回环） | 见重复就判红；回环缺 `reuse_of` 留痕 |
| `caption-axis-gate.md` | ASR 只给时间、`script-body.csv` 是真源 | 用静音间隙顺序铺满；先审计后覆盖 |
| `publish-caption.md` | 长版发布文案、互动装置 | 一句金句 + 标签串；空壳提问 |

## 交付门族

`python3 scripts/check-episode-delivery.py "<书名>" --tmp "tmp/<书名>_tpl" --intro-trim 3.0` 一次跑完；门数会随规则增加，**不要背数字**。主要族：

| 门族 | 代表脚本 | 看什么 |
| --- | --- | --- |
| 图片溯源 | `check-image-provenance.py` | 清单 ↔ `_provenance.json` ↔ 成片；`source_width`、跨期撞图、同期回环 |
| 图片重放 | `verify-pexels-fetch.py` | 盘上 `_raw/` 是否真是清单声称的 Pexels id |
| 字幕字符 | `check-caption-punctuation.py` | 屏上零标点、零空白、行数上限 |
| 字幕边界 | `qc-caption-alignment.py` | 成片帧上的墨迹 == 字幕轨在该时刻的字 |
| 字幕轴 token | `create-body-timings-precise.mjs --verify` | 轴是否 token 派生、行归属是否漂移 |
| 字幕轴能量 | `audit-caption-axis.py --json` | 行首落后、尾部拖进静音、窗口不可读 |
| 逐行语音覆盖 | `check-caption-speech-align.py --track` | 每行字幕窗是否盖住它自己念出的语音 |
| 水波同窗 | `check-water-ripple.py` | 水声与水波是否同窗；封面放大是否破坏 |
| 钩子无书名层 | `check-hook-no-bookmark.py` | 钩子段是否压着《书名》/作者层 |
| 钩子帧匹配 | `check-hook-frame-match.py` | 每张钩子图是否真的上过屏 |
| 闪屏 | `check-luminance-flicker.py`、`check-flicker-attribution.py` | 单帧脉冲与归因 |
| 片头原样 | `check-intro-is-original.py` | 共享片头是否被改观感/归一 |
| 声明层 | `check-disclaimer-overlay.py` | 正文来源声明是否常驻且不被盖 |
| 书名字体 | `check-title-font.py` | 字体族/字重同源 |
| 本书封面层 | `check-book-cover-overlay.py` | 封面上屏几何、alpha、与水波窗不打架 |
| 发布文案 | `check-publish-caption.py --delivery` | brief 字段、长版形状、标签、具体提问 |

门禁之后另跑：

```bash
python3 scripts/composition-digest.py verify "<书名>"
```

它问的是「这些像素是不是此刻这一代生成器渲的」。红只意味着重渲，调阈值/改判据都无效。

独立片头不在交付门里，由 `census-intro-only.py` 分族盘点：✅ 合格；🔁 旧于成片；🧩 缺但静默片头在；🧱 缺且静默片头没了；🚫 有片无音轨/时长不对。🔁/🚫 判红，🧩/🧱 记 ⚠️。

## 全库检查

```bash
npm run check
```

- 读日志里的 `EXIT=`，不要只看后台包装器状态。
- 它会在第一个 `throw` 处中止。一个根因可能拥有多个 `throw` 点，红在上游时下游检查根本没执行——「我这部分绿」不能从一次半途而废的 sweep 里推出来。
- 需要测隐藏红时，按 AGENTS.md 的 fail-fast 探针配方生成诊断副本；不要手改 `scripts/check.mjs`。探针输出是“红背后藏了什么”的测量，不是绿。
