#!/usr/bin/env node

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";

const ROOT = process.cwd();
const HYPERFRAMES_VERSION = "0.7.33";
const requiredCommands = ["ffmpeg", "ffprobe", "npx"];
const scriptFiles = [
  "scripts/init.mjs",
  "scripts/download-whisper-model.mjs",
  "scripts/record-book-candidates.mjs",
  "scripts/create-body-timings.mjs",
  "scripts/create-episode-preview.mjs",
  "scripts/process-voiceover.mjs",
  "scripts/render-episode-final.mjs",
  "scripts/validate-script.mjs",
  "scripts/lib/body-timings.mjs",
  "scripts/lib/episode-slug.mjs",
  "scripts/lib/env.mjs",
  "scripts/lib/script-policy.mjs",
  "scripts/lib/script-version.mjs",
  "scripts/lib/subtitle-display.mjs",
  "scripts/lib/title-normalization.mjs",
  "scripts/lib/weread-request.mjs",
  "scripts/tests/test-body-timings.mjs",
  "scripts/tests/smoke-timing-fallback.mjs",
  "scripts/tests/test-title-normalization.mjs",
];

function commandArgs(command) {
  if (command === "ffmpeg") return ["-hide_banner", "-h"];
  if (command === "ffprobe") return ["-version"];
  return ["--version"];
}

function run(command, args, cwd = ROOT, options = {}) {
  return spawnSync(command, args, { cwd, encoding: "utf8", shell: false, ...options });
}

// 拿渲染锁再跑一条命令（2026-10-08《昆虫记》ENOTEMPTY 的另一半）。
// 这三个 `npx --yes hyperframes@…` 和段渲染用的是**同一棵** _npx 树，只在渲染侧排队等于修了一半
// ——一边排队、一边直冲，撞车照旧。锁的协议（mkdir + owner 文件 + 陈旧锁接管 + 超时文案 +
// 仓库内 npm 缓存的 export）只有一份实现，住在 scripts/render-hook-batch.sh 的 render_lock_run()
// 里，所以这里 source 它再调用，而不是在 Node 里再写一份 mkdir 锁：两份实现对「这把锁归谁」的
// 口径迟早会漂，而漂了的一切照绿。HF_LOCK_MAX_WAIT 必须在 **source 之前**给，因为驱动在
// 顶层就把它读进 LOCK_MAX_WAIT 了（写在 source 之后是一句死代码——探针跑过才知道）。
// 75 单独报：一次「别人正在渲染」不能伪装成一次「模板有问题」。
function runLocked(label, command, args, cwd = ROOT, options = {}) {
  const script = "set -u\n"
    + 'export HF_SOURCE_ONLY=1\n'
    + 'export HF_LOCK_MAX_WAIT="${HF_LOCK_MAX_WAIT:-900}"\n'
    + "source scripts/render-hook-batch.sh || exit 2\n"
    + 'render_lock_run "$1" "$2" "${@:3}"\n'
    + "exit $?\n";
  return spawnSync("bash", ["-c", script, "runLocked", label, cwd, command, ...args],
    { cwd: ROOT, encoding: "utf8", shell: false, ...options });
}

function requireCommand(command) {
  const result = run(command, commandArgs(command), ROOT, { stdio: "ignore" });
  if (result.status !== 0) throw new Error(`Missing or unusable required command: ${command}`);
}

for (const command of requiredCommands) requireCommand(command);
const missingScripts = scriptFiles.filter((file) => !fs.existsSync(path.join(ROOT, file)));
for (const file of scriptFiles) {
  if (missingScripts.includes(file)) continue;
  const result = run(process.execPath, ["--check", file]);
  if (result.status !== 0) throw new Error(`Syntax check failed: ${file}`);
}
if (missingScripts.length) {
  // 离线工作副本（Obsidian 同步目录）可能缺 scripts/tests/*.mjs：只告警，不因此跳过后面的规则门禁。
  console.warn(`Warning: skipped missing scripts: ${missingScripts.join(", ")}`);
}

const defaultIntroBooksPath = path.join(ROOT, "templates", "shared-video-template", "intro", "default-book-list.json");
const defaultIntroBooks = JSON.parse(fs.readFileSync(defaultIntroBooksPath, "utf8"));
if (
  !Array.isArray(defaultIntroBooks)
  || defaultIntroBooks.length !== 6
  || defaultIntroBooks.some((book) => !book?.title || !book?.author)
) {
  throw new Error("Default intro book list must contain exactly six books with authors");
}

// 字幕上屏标点策略：真源只有 scripts/lib/subtitle-punctuation.json 一份，
// 两条渲染路径（Pillow 兜底 / HyperFrames body）与验收脚本都必须读它，不得自带字符表。
// 规则本体见 AGENTS.md「Subtitles never display punctuation」。
const subtitlePolicyPath = path.join(ROOT, "scripts", "lib", "subtitle-punctuation.json");
const subtitlePolicy = JSON.parse(fs.readFileSync(subtitlePolicyPath, "utf8"));
const displayStrip = new Set(subtitlePolicy.displayStrip ?? []);
const displayKeep = new Set(subtitlePolicy.displayKeep ?? []);
// 2026-09-27 口径升级：句中断点号一律换成**换行**，不再换成全角空格。
// displayPause / displayPauseChar 是旧口径的两个键，谁把它们加回来，谁就是在静默恢复
// 「漂亮[全角空格]聪明[全角空格]有钱」那种屏上空洞，所以这里直接判红。
for (const retired of ["displayPause", "displayPauseChar"]) {
  if (retired in subtitlePolicy) {
    throw new Error(`subtitle-punctuation.json 仍带着已废止的 ${retired}：断点号只能换行（displayBreak/displayBreakTo）`);
  }
}
const displayBreak = new Set(subtitlePolicy.displayBreak ?? []);
if (subtitlePolicy.displayBreakRender !== "line-break") {
  throw new Error('subtitle-punctuation.json: displayBreakRender 必须是 "line-break"，空白间隙口径已作废');
}
if (subtitlePolicy.displayBreakTo !== "\n") {
  throw new Error(`subtitle-punctuation.json: displayBreakTo 必须是换行，当前 ${JSON.stringify(subtitlePolicy.displayBreakTo)}`);
}
if (!displayBreak.size) {
  throw new Error("subtitle-punctuation.json: displayBreak 为空，渲染器就没有断句依据了");
}
const maxCaptionLines = subtitlePolicy.displayMaxLines ?? 0;
if (!(maxCaptionLines >= 1 && maxCaptionLines <= 3)) {
  throw new Error(`subtitle-punctuation.json: displayMaxLines=${maxCaptionLines} 不在 1-3 之间`);
}
if (!subtitlePolicy.displayBreakVersion) {
  throw new Error("subtitle-punctuation.json: 缺 displayBreakVersion，字幕轨的 captionStyle 标记就无法判定新旧口径");
}
if (!displayStrip.size) throw new Error("subtitle-punctuation.json: displayStrip 为空，去标点规则等于没写");
const keepConflict = [...displayKeep].filter((ch) => displayStrip.has(ch) || displayBreak.has(ch));
if (keepConflict.length) {
  throw new Error(`subtitle-punctuation.json: displayKeep 与 strip/break 冲突：${keepConflict.join("")}`);
}
const breakNotStripped = [...displayBreak].filter((ch) => !displayStrip.has(ch));
if (breakNotStripped.length) {
  throw new Error(
    `subtitle-punctuation.json: displayBreak 的标点必须同时出现在 displayStrip，否则上屏验收门禁查不到它：${breakNotStripped.join("")}`,
  );
}
for (const file of [
  "scripts/render_scene_map_template_fallback.py",
  "scripts/lib/subtitle-display.mjs",
  "scripts/lib/caption_display.py",
  "scripts/check-caption-punctuation.py",
]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  if (!source.includes("subtitle-punctuation.json")) {
    throw new Error(`${file} 没有读取 subtitle-punctuation.json：上屏标点字符表只能有真源一份`);
  }
}
const previewSource = fs.readFileSync(path.join(ROOT, "scripts", "create-episode-preview.mjs"), "utf8");
if (!previewSource.includes("subtitle-display.mjs")) {
  throw new Error("scripts/create-episode-preview.mjs 未使用 lib/subtitle-display.mjs：HyperFrames body 路径会漏掉去标点");
}
for (const file of ["scripts/render_scene_map_template_fallback.py", "scripts/create-episode-preview.mjs"]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  if (/HIDE_GLYPHS\s*=/.test(source)) {
    throw new Error(`${file} 里又出现了一份硬编码的上屏标点字符表（HIDE_GLYPHS），改回读 subtitle-punctuation.json`);
  }
  // 两条渲染路径里出现字面全角空格，唯一用途就是把它画到屏上形成间隙（需要判断/清洗时写
  // \u3000 转义，或在注释里用「[全角空格]」占位）。旧口径就是这么留下空洞的，这里堵回去。
  if (/　/.test(source)) {
    throw new Error(`${file} 里出现了字面全角空格：上屏字幕零空白间隙，断点号只能换成换行`
      + `（需要这个字符时写 \\u3000 转义，别让它进画字的那条路）`);
  }
}
// 字幕轨必须落 captionStyle 标记：门禁据此区分「按换行口径渲的」与「历史轨」。
// 没标记 = 全仓库审计只能一律告警，新期数混过去的空格间隙就再也查不红（2026-09-27 的口径
// 升级靠的就是这个标记，否则 28 期历史轨会同时红灯、正在渲染的会话也被掐断）。
const trackWriter = fs.readFileSync(path.join(ROOT, "scripts", "render_scene_map_template_fallback.py"), "utf8");
if (!/captionStyle/.test(trackWriter)) {
  throw new Error("scripts/render_scene_map_template_fallback.py 不再往 caption-track.json 落 captionStyle："
    + "标点门禁无法判定这期是否按换行口径渲过，只能退化成告警");
}
if (!/MAX_CAPTION_LINES/.test(previewSource)) {
  throw new Error("scripts/create-episode-preview.mjs 没有接入 lib 的 displayMaxLines 行数预算："
    + "HyperFrames body 路径会把整行文案叠成一屏，或退回空格间隙");
}

// 水波同窗门禁：规则本体见 AGENTS.md「Visual And Audio Rules」——`assets/sfx/water.mp3`
// 在片头→正文切点后起播的那 WATER_DUR 秒，画面上必须同时出现一圈自中心外扩的涟漪。
// 「只加声音不加画面」是唯一能过完分辨率/时长/音频流/字幕/切点全部验收的缺陷：成片看着
// 完全正常，只有这道量化门禁拦得住。所以声音与视觉必须共用 lib 里同一对常数，而且门禁
// 必须卡在候选激活到 renders/ 之前——写在 move 之后就成了事后告警，等于没有门禁。
const waterLibFile = "scripts/lib/water_ripple.py";
const waterLib = fs.readFileSync(path.join(ROOT, waterLibFile), "utf8");
for (const name of ["WATER_OFFSET", "WATER_DUR"]) {
  if (!new RegExp(`^\\s*${name}\\s*=`, "m").test(waterLib)) {
    throw new Error(`${waterLibFile} 里没有定义 ${name}：水波常数真源被挪走了`);
  }
}
function listCodeFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    // archive/ 是历史留存，不是生产面：里面的文件不参与任何判定。
    // 不排除的话，2026-10-11 归档的 25 个期专用渲染器会被重新拖回门禁扫描范围——
    // 它们各自复制了一份 WATER_OFFSET，而 WATER_OFFSET 只能定义一次这条规则
    // 会当场翻红；更糟的是 water.mp3 豁免判定也会把它们算进来。
    // 「归档」必须对扫描面真正隐形，只靠挪目录位置是不够的。
    if (entry.name === "archive") continue;
    const rel = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listCodeFiles(rel));
    else if (/\.(?:py|mjs|js)$/.test(entry.name)) out.push(rel);
  }
  return out;
}
const waterDefiners = listCodeFiles("scripts").filter((file) =>
  /^\s*WATER_OFFSET\s*=/m.test(fs.readFileSync(path.join(ROOT, file), "utf8")),
);
if (waterDefiners.length !== 1 || waterDefiners[0] !== waterLibFile) {
  throw new Error(`WATER_OFFSET 只能在 ${waterLibFile} 定义一次，实际找到：${waterDefiners.join(", ")}`);
}
const fallbackRenderer = fs.readFileSync(path.join(ROOT, "scripts/render_scene_map_template_fallback.py"), "utf8");
for (const token of ["from water_ripple import", "WATER_OFFSET", "WATER_DUR", "ripple(", "water_u"]) {
  if (!fallbackRenderer.includes(token)) {
    throw new Error(`兜底渲染器缺少 ${token}：背景层必须按 lib 的常数施加涟漪，否则水声只有声音没有画面`);
  }
}
if (/adelay=\{?\s*[\d.]+/.test(fallbackRenderer)) {
  throw new Error("兜底渲染器把 adelay 写成了硬编码秒数：必须由 lib 的 WATER_OFFSET 算出 WATER_START");
}
const gateCall = fallbackRenderer.indexOf("check-water-ripple.py");
const activate = fallbackRenderer.indexOf("shutil.move(candidate");
if (gateCall < 0) {
  throw new Error("兜底渲染器没有调用 scripts/check-water-ripple.py：水波验收没接进渲染流程");
}
if (activate >= 0 && gateCall > activate) {
  throw new Error("水波门禁跑在候选激活之后（shutil.move 之后）：不合格成片会先覆盖掉已交付成片，门禁必须前置");
}
// 钩子段准备脚本的两条**内部顺序**断言。2026-10-03 第四批 10 期跑这一步时，第一期就停在
// 「溯源门禁失败」，后面 9 期连日志都没生成：① 脚本取图、调色、补压之后直接跑
// check-image-provenance.py，而钩子行的 prompts.csv 溯源只有 write-prompts-csv.py 会写——
// 那一步一直靠每批手工补，于是脚本永远过不了自己的门禁；② 失败路径写的是 `exit 1`，
// 而整块用花括号组包着，exit 结束的是脚本而不是这一期，外层 `st=$?` 的「一期失败继续下一期」
// 从来没生效过。两条都是「下一个会话只会再踩一次」的东西，所以钉在这里。
const hookPrep = fs.readFileSync(path.join(ROOT, "scripts/prepare-hook-segment.sh"), "utf8");
// 只认**调用行**，不认注释里的名字：这个脚本的开头说明和每步的 why 注释都在提
// check-image-provenance.py，用 indexOf(名字) 会先撞上文档，于是把修好的脚本判成红
// （2026-10-03 写这条断言时的负对照当场抓出来的）。
const promptsWrite = hookPrep.search(/python3 scripts\/write-prompts-csv\.py/);
const provenanceGate = hookPrep.search(/python3 scripts\/check-image-provenance\.py/);
if (promptsWrite < 0) {
  throw new Error("prepare-hook-segment.sh 不写 prompts.csv 溯源行：门禁读的就是这张表，"
    + "钩子图取完必然判「没有溯源行」，脚本过不了自己");
}
if (provenanceGate < 0 || promptsWrite > provenanceGate) {
  throw new Error("prepare-hook-segment.sh 里 write-prompts-csv.py 没有排在 check-image-provenance.py 之前："
    + "溯源行必须先生成，门禁才有东西可查");
}
if (/^\s*\{\s*$/m.test(hookPrep) || !/^\s*\(\s*$/m.test(hookPrep)) {
  throw new Error("prepare-hook-segment.sh 的每期代码块必须是子 shell 括号 `(`，不能是花括号组 `{`："
    + "块里的 `exit 1` 在花括号组里会结束整个脚本，一期失败就带走后面所有期");
}
// 规则文档语料（2026-10-11 拆分 AGENTS.md 后引入）。
// 拆分前所有 token 都在 AGENTS.md 一份文件里；现在规则按主题落在 docs/rules/*.md，
// AGENTS.md 只剩主干与索引。于是「某条规则是否被写下来了」不能再问单份文件，
// 必须问**整个规则语料**——否则拆分本身就会把 15 个门禁 token 全部变成「没写」。
//
// 为什么这个语料函数而不是逐个检查点自己拼路径：12 处检查点各写一遍
// 「AGENTS.md + docs/rules/* + playbook」，迟早有一处漏掉那一层，而漏掉的那处
// 恰好是它检查的 token 所在的那份文件——于是规则明明在、门禁却报「无法被执行」，
// 下一个人的反应是往 AGENTS.md 里补一句复制品，拆分白做。
const RULE_DOC_DIR = "docs/rules";
function ruleDocsCorpus() {
  const files = ["AGENTS.md", "docs/book-video-playbook.md"];
  if (fs.existsSync(path.join(ROOT, RULE_DOC_DIR))) {
    for (const f of fs.readdirSync(path.join(ROOT, RULE_DOC_DIR))) {
      if (f.endsWith(".md")) files.push(`${RULE_DOC_DIR}/${f}`);
    }
  }
  return files.map((f) => fs.readFileSync(path.join(ROOT, f), "utf8")).join("\n");
}
function ruleSays(token) {
  return ruleDocsCorpus().includes(token);
}
// 规则文档目录必须存在且非空：否则语料退化成只有 AGENTS.md，而那正是拆分前的样子——
// 所有 token 会「碰巧」还在，于是没人发现 docs/rules/ 整个丢了。
if (!fs.existsSync(path.join(ROOT, RULE_DOC_DIR))) {
  throw new Error(`${RULE_DOC_DIR}/ 不在了：AGENTS.md 已于 2026-10-11 拆成主干+规则索引，`
    + "规则本体现在只存在于 docs/rules/*.md。目录丢失时下面的 token 检查会「碰巧」全绿。");
}
const ruleFiles = fs.readdirSync(path.join(ROOT, RULE_DOC_DIR)).filter((f) => f.endsWith(".md"));
if (ruleFiles.length === 0) {
  throw new Error(`${RULE_DOC_DIR}/ 是空的：规则本体已经不在 AGENTS.md 里了，空目录等于全丢`);
}

for (const doc of ["AGENTS.md", "docs/book-video-playbook.md", RULE_DOC_DIR]) {
  if (!ruleSays("check-water-ripple.py")) {
    throw new Error(`规则文档（含 ${doc}）没有写出 check-water-ripple.py 这条验收命令：水波规则无法被执行`);
  }
}

// 封面同窗的取证口径（2026-10-08《荒凉山庄》候选被判「只加水声」之后立的这一套）：
// 本书封面这一层现在**整段压在涟漪中心上**，还按 lib 的 motion 逐帧放大。门量的是「径向逐帧差
// 质心向外跑」，卡片那份靠中心的向外位移不摘掉就会被读成涟漪在向内收（实测不排 −80px/ρ−0.96
// 的假红，真片 −9.1px）；而第一版那张静态并框只清空分子、没重算分母，被盖住一半的环按整环面积
// 稀释，实测吃掉 20~60px 外移量（真片 +15px/ρ0.26 仍红）。采用的形状是「逐帧真实足迹 + 每环按
// 自己可见的像素数归一」，**四个阈值一个都没动**。所以这里钉三件事：
//   ① 阈值钉在原值——「把 45 调到 20」是最省事的假修法，那条路必须判红；
//   ② 掩码只能从 lib 推（overlay/zoom_at/zoomed_overlay），不许门里再抄一份卡框坐标；
//   ③ 旧口径只活在 --legacy-static-box 这一面对照墙后面，两条生产调用点都不许碰它。
const waterGate = fs.readFileSync(path.join(ROOT, "scripts/check-water-ripple.py"), "utf8");
// 钉的是「常量名 = 那个值」这一行，行尾注释必须容许（那四个阈值各带一条 why）。
// 只做前缀匹配会退化成「只要谁还敢写 DRIFT_PX = 就绿」，所以谓词按值生成，
// 并且每条都拿一个诱饵值自测一次：改数值必须让原谓词失配、让诱饵谓词命中。
const waterThresholdRe = (name, value) => new RegExp(
  `^${name}\\s*=\\s*${value.replace(/\./g, "\\.")}(?:\\s+#.*)?$`, "m");
for (const [name, value, decoy, why] of [
  ["DRIFT_PX", "45.0", "20.0", "水波外移下限不是 45.0 了：封面同窗的缺陷要靠改取证口径修，"
    + "把下限调低等于让「只有水声」那一期重新能交付"],
  ["DRIFT_RHO", "0.60", "0.30", "质心外移的 Spearman ρ 下限不是 0.60 了（同上）"],
  ["MIN_MOTION", "0.015", "0.001", "窗内逐帧差下限不是 0.015 了（同上）"],
  ["TOL_FRAMES", "2.0", "6.0", "声画起点容差不是 2.0 帧了（同上）"],
]) {
  if (!waterThresholdRe(name, value).test(waterGate)) {
    throw new Error(`check-water-ripple.py：${why}`);
  }
  const line = waterGate.match(new RegExp(`^${name}\\s*=\\s*\\S+.*$`, "m"));
  if (!line) throw new Error(`check-water-ripple.py 里找不到 ${name} 的定义行：钉子无处可钉`);
  const mutated = line[0].replace(new RegExp(`(${name}\\s*=\\s*)\\S+`), `$1${decoy}`);
  if (waterThresholdRe(name, value).test(mutated)) {
    throw new Error(`${name} 的钉子不认数值（改成 ${decoy} 仍然命中原谓词）：这条断言是空转`);
  }
  if (!waterThresholdRe(name, decoy).test(mutated)) {
    throw new Error(`${name} 的钉子不自测：把值换成 ${decoy} 之后连诱饵谓词都不命中，`
      + "说明钉的是这行的形状而不是这个数");
  }
}
for (const token of ["def per_frame_cover_masks(", "visible_denominator",
                     "B.overlay(source=", "B.zoom_at(", "B.zoomed_overlay(",
                     "def cover_layer(", "--cover-witness"]) {
  if (!waterGate.includes(token)) {
    throw new Error(`check-water-ripple.py 少了 ${token}：封面同窗的逐帧足迹掩码被挪走或改成抄坐标了。`
      + "几何与动效只有 lib/book_cover.py 一个真源，门自己画一份就又是「渲染读一份、门禁读另一份」");
  }
}
if (/^\s*23[0-9]\s*[,:]|^\s*66[0-9]\s*[,:]/m.test(waterGate)) {
  throw new Error("check-water-ripple.py 里出现了写死的卡框 y 坐标：封面几何只能从 lib 推");
}
// 交付入口在这一段下面才读进来（`const deliveryRunner`），而 const 在 TDZ 里访问直接抛
// ReferenceError——整个 check 会挂在这条没跑过的引用上，而不是挂在我真要钉的东西上。
// 所以这里自己取一份局部副本，只服务封面同窗这几条断言。
const waterCoverCaller = fs.readFileSync(path.join(ROOT, "scripts/check-episode-delivery.py"), "utf8");
// 旧口径留在盘上是为了对照，但谁都不许在生产路径用它。
for (const [file, src] of [["scripts/render_scene_map_template_fallback.py", fallbackRenderer],
                           ["scripts/check-episode-delivery.py", waterCoverCaller]]) {
  if (src.includes("--legacy-static-box")) {
    throw new Error(`${file} 在水波门禁的交付路径上传了 --legacy-static-box：那是 A/B 对照口径，`
      + "静态并框 + 整环分母会把带着封面放大的期数判成假红");
  }
}
if (!/cover_witness|book-cover\.json/.test(waterGate)) {
  throw new Error("check-water-ripple.py 不读渲染期证人：掩码会按当下的 lib 猜一张并不存在的动效");
}
// 两处调用点都得把证人递进门：合成期就在 TMP 里写了 book-cover.json，门按期名去猜是兜底，不是本分。
if (!fallbackRenderer.includes('"--cover-witness"')) {
  throw new Error("兜底渲染器调水波门禁时没传 --cover-witness：这一版像素到底用了什么时序与动效，"
    + "只有它自己写的旁证知道");
}
if (!/_cover_motion\(/.test(fallbackRenderer) || !/"motion": _cover_motion/.test(fallbackRenderer)) {
  throw new Error("兜底渲染器的 book-cover.json 证人不再记 motion：动效改了而证人还是旧的，"
    + "门的逐帧足迹就会和像素不符（《荒凉山庄》14:19 那次就是这么被判红的）");
}
if (!waterCoverCaller.includes('"--cover-witness"')) {
  throw new Error("check-episode-delivery.py 的水波门没接证人：交付路径退化成按期名猜封面层在不在");
}

// 字幕轴门禁（2026-09-28《茶馆》）：字幕边界压测比的是「画面上显示的字幕 == 字幕轨在该
// 时刻写的那条」，它拿字幕轨自己当真值，所以一条整段漂了 16 秒的轴能拿 156/156 全绿。
// 只有两方能独立作证：ASR token 的时间戳（文本↔时间）和 silencedetect 的能量边界。
// 交付入口必须同时挂着这两道门，还要在 AGENTS.md 与手册里写出来，否则规则等于不存在。
const deliveryRunner = fs.readFileSync(path.join(ROOT, "scripts/check-episode-delivery.py"), "utf8");
for (const [token, why] of [
  ["create-body-timings-precise.mjs", "按 token 重算轴、逐行比交付轴的门禁被摘掉了：行归属漂移无人可查"],
  ["--verify", "重算门禁被改成写盘模式或去掉 --verify：交付检查会顺手覆盖时间轴"],
  ["audit-caption-axis.py", "能量边界门禁（行首落后/行内空窗/读不完/行尾拖）被摘掉了"],
]) {
  if (!deliveryRunner.includes(token)) {
    throw new Error(`scripts/check-episode-delivery.py 不再包含 ${token}：${why}`);
  }
}
for (const tool of ["scripts/audit-caption-axis.py"]) {
  if (!fs.existsSync(path.join(ROOT, tool))) {
    throw new Error(`${tool} 不存在：字幕轴验收没有可执行的工具`);
  }
}
if (!ruleSays("audit-caption-axis.py")) {
  throw new Error("规则文档没有写出 audit-caption-axis.py 这条验收命令：字幕轴规则无法被执行");
}

// 第七道门（2026-09-28《恶女的告白》）：逐行字幕窗必须盖住「这一行自己的字被念出来」的窗。
// 第五、六道比的是「轴 vs 轴」，一条 token 推出、逐行各差两三秒的轴在它们眼里合格——《恶女的告白》
// 88 行铺满 227.6s、前六道全绿，仍有 40 行「字还在念字幕已经换」。所以这道门也不能被摘。
// 修轴脚本与门禁共用 scripts/lib/caption_speech.py 的 audible_windows：一旦哪边改成自己算一套，
// 「过了门禁」就重新变成一句空话，这里静态盯着。
for (const [token, why] of [
  ["check-caption-speech-align.py", "逐行覆盖门禁被从交付入口摘掉了：行各差两三秒的轴无人可查"],
  ["--track", "覆盖门禁被去掉 --track：只判 body-timings.json 会漏掉渲染器按语速拉长行的改写"],
]) {
  if (!deliveryRunner.includes(token)) {
    throw new Error(`scripts/check-episode-delivery.py 不再包含 ${token}：${why}`);
  }
}
for (const tool of ["scripts/check-caption-speech-align.py", "scripts/snap-body-timings-vad.py",
                    "scripts/lib/caption_speech.py"]) {
  if (!fs.existsSync(path.join(ROOT, tool))) {
    throw new Error(`${tool} 不存在：逐行覆盖的门禁/修轴工具/共用真源缺失`);
  }
}
for (const [tool, why] of [["scripts/snap-body-timings-vad.py", "修轴脚本"],
                           ["scripts/check-caption-speech-align.py", "覆盖门禁"]]) {
  const src = fs.readFileSync(path.join(ROOT, tool), "utf8");
  if (!src.includes("caption_speech") || !src.includes("audible_windows")) {
    throw new Error(`${tool}（${why}）没有从 lib/caption_speech.py 取 audible_windows：${why}与门禁各算一套可听语音窗`);
  }
}
// Whisper 会把念出来的数词写成阿拉伯数字（《嫌疑人X的献身》2026-10-04 行 120「六十八岁」
// 被听成 token "68"@213.77-214.23）：一个字符都对不上 → 该行钉不出自身可听窗 → 锚点薄弱 →
// T2 退回原始密度判红，而轴上无处可挪，因为它的行首只能落在自己第一个字上，而那三个字被
// 一个数字 token 吞掉了。修法是把数字折成逐位汉字，且**建轴（Node）和门禁/修轴（Python）
// 两侧、文案侧和 ASR 侧同时折**——一边折一边不折就是《恶意》那两个音频文件那类死锁。
// 这里静态盯着这四条接线，少一条就重新变成「只有重渲之后才看得见」的红。
{
  const table = "零一二三四五六七八九零一二三四五六七八九";
  const py = fs.readFileSync(path.join(ROOT, "scripts/lib/caption_speech.py"), "utf8");
  if (!py.includes("DIGIT_CANON") || !/def clean[\s\S]{0,200}translate\(DIGIT_CANON\)/.test(py)) {
    throw new Error("scripts/lib/caption_speech.py 的 clean() 不再折数字：ASR 的数字 token "
      + "对不上文案的汉字数字，含数词的行钉不出自身可听窗，第七道门只能判它空放");
  }
  if (!py.includes(table)) {
    throw new Error("scripts/lib/caption_speech.py 的数字折算表和 Node 侧不是同一张：两套口径"
      + "迟早一个折一个不折，字序命中率和自身可听窗各算一套");
  }
  const nodeSrc = fs.readFileSync(path.join(ROOT, "scripts/create-body-timings-precise.mjs"), "utf8");
  if (!nodeSrc.includes(table)) {
    throw new Error("scripts/create-body-timings-precise.mjs 的数字折算表和 Python 侧不是同一张");
  }
  for (const [token, why] of [
    // 2026-10-08《反抗者》行 39「注意，不是我。」：token 文本里有 BPE 留在多字节词内的前导空格
    // （" 不是"），ch 取原始首字符就取到空格，这个 token 从此匹配不上文案。折数字和去空白是
    // **同一个表达式**，钉住旧的 `Array.from(canonDigits(t))` 会一边红一边把修复判成违规，所以
    // 这里跟着 current shape 走；真正的双向控制（老形状必须翻红）在 test-token-whitespace-char.py。
    ["Array.from(canonDigits(t.trim()))", "ASR token 侧不再折数字／不再去掉词间空白：数字 token 或带前导空白的词 token 首字符对不上文案"],
    ["canonDigits(String(row.text)", "文案侧不再折数字：文案写阿拉伯数字的历史期会从逐位相等变成整行失配"],
  ]) {
    if (!nodeSrc.includes(token)) {
      throw new Error(`scripts/create-body-timings-precise.mjs 少了 ${token}：${why}`);
    }
  }
}
// 修轴脚本的行首规则也必须看得见 lib 的 own span：−50 dB 证人只回答「有没有停顿」，一句话
// 连着念时它把整句并成一个语音段，D 拿到的 on 是**整段**起点而不是本行第一个字的起点
// （《李尔王》2026-10-04 行 15「她说，父王，」：sil 只到 28.56，on=28.56 ≤ 上一行行尾 28.6，
// D 的 on > prev_end+0.10 永不成立 → 行首落后 0.77s 没有任何规则看得见，只有第七道门判红）。
// 所以 fix_heads 里那条借用 own_spans 的兜底（D2）一旦被摘掉，红就重新变成只有渲染后才看得见。
{
  const src = fs.readFileSync(path.join(ROOT, "scripts/audit-caption-axis.py"), "utf8");
  const from = src.indexOf("def fix_heads");
  const until = src.indexOf("\n    fix_heads()", from);
  const heads = from < 0 ? "" : src.slice(from, until < 0 ? src.length : until);
  if (!heads.includes("own_spans") || !heads.includes("late-start-own")) {
    throw new Error("scripts/audit-caption-axis.py 的行首修补（fix_heads）不再借用 lib 的 own span（D2）："
      + "连读段里行首落后无人可修，第七道逐行覆盖门会重新变成渲染后才暴露的红");
  }
  if (!ruleSays("late-start-own")) {
    throw new Error("规则文档没写出 late-start-own（连读段行首兜底）：这条修轴规则无法被执行");
  }
}
// 第七道门 T2 的「读完所需」豁免（2026-10-04《人性的枷锁》行 72「而是：」）：
// 两个字念出来只有 0.14s，可它必须在上屏待满 0.90s 才读得完，中间那 0.67s 静音是规则 A
// 自己要求的停留，不是空放。旧口径把整段停留算成空放（26%），而审计的规则 A 又拒绝把它裁短
// （读不完），一条轴被两个规则钉死在原地——那种红修不掉，只能改量法。改量法的前提是
// **只有这一个来源**：reading_need 与规则 A 同一条公式，门禁和审计都从 lib 取，
// 且豁免只在「这一行真的钉得出自身可听窗」时生效、原始密度兜底线一律不动。
{
  const lib = fs.readFileSync(path.join(ROOT, "scripts/lib/caption_speech.py"), "utf8");
  if (!/def reading_need\(/.test(lib)) {
    throw new Error("lib/caption_speech.py 不再定义 reading_need：富余停留的口径回到各算一套");
  }
  if (!/def voiced_share\([^)]*readable=None/.test(lib)) {
    throw new Error("lib/caption_speech.py 的 voiced_share 少了 readable 形参：T2 又回到把读完所需的那截静音算成空放");
  }
  if (!/if readable and own:/.test(lib)) {
    throw new Error("lib/caption_speech.py 的富余停留豁免不再要求 own：钉不出自身可听窗的弱锚行也会被骗绿");
  }
  if (!/READ_FLOOR\s*=\s*0\.55/.test(lib) || !/READ_RATE\s*=\s*5\.2/.test(lib)) {
    throw new Error("lib/caption_speech.py 的 READ_FLOOR/READ_RATE 被改动：与 audit 规则 A「读不完」不再是同一条公式");
  }
  const gate = fs.readFileSync(path.join(ROOT, "scripts/check-caption-speech-align.py"), "utf8");
  if (!gate.includes("reading_need") || !/readable=None if a\.no_read_pad else reading_need\(/.test(gate)) {
    throw new Error("check-caption-speech-align.py 的 T2 不再传 readable=：短行被按旧口径判红，修轴的手无处可挪");
  }
  if (!gate.includes("--min-raw") || !gate.includes("raw < a.min_raw")) {
    throw new Error("check-caption-speech-align.py 丢了原始密度兜底线：一条完全停在静音里的字幕会被富余停留豁免放行");
  }
  if (!gate.includes("--no-read-pad")) {
    throw new Error("check-caption-speech-align.py 少了 --no-read-pad：口径改动无法与旧口径 A/B 对照");
  }
  const audit = fs.readFileSync(path.join(ROOT, "scripts/audit-caption-axis.py"), "utf8");
  if (!audit.includes("reading_need") || !/readable=pad/.test(audit)) {
    throw new Error("audit-caption-axis.py 的 density() 不再传 readable=：修轴的手按旧口径裁尾巴，门禁刚裁完就判红");
  }
  for (const token of ["reading_need", "富余停留"]) {
    if (!ruleSays(token)) {
      throw new Error(`规则文档没写出 ${token}：T2 的富余停留口径无法被执行`);
    }
  }
}
// whisper.cpp 的控制 token 与「计数相等空档按位对位」（2026-10-07《无名的裘德》行 103/129）。
// 两条都在「这一行自己被念出来的窗」这条唯一真源上，所以一侧漏接就重新变成「门禁和修轴各算
// 一套」——而且第二侧的漏法是**静默**的：旧正则 `^\[[_A-Z]+\d*$` 少一个闭合 ]，以 [ 开头的
// token 一个都没被剔掉，clean() 把 [_TT_628] 变成 _TT_六二八 七个幽灵字符（数字还折成汉字，
// 于是能和文案里真念出来的数字抢一次命中，命中的时刻是段界而不是说话点）。
// 弱锚行从 79 降到 7（136 期实测），全库门禁 7 的判红集合按行数只差 ±4~1，但有三部已交付片
// 由绿转红（乱世佳人/秘密花园/荆棘鸟）——那是口径第一次量到它们，不是新缺陷。
{
  const libPath = "scripts/lib/caption_speech.py";
  const lib = fs.readFileSync(path.join(ROOT, libPath), "utf8");
  const nodePath = "scripts/create-body-timings-precise.mjs";
  const nodeSrc = fs.readFileSync(path.join(ROOT, nodePath), "utf8");
  // ① 形状必须要求闭合的 ]，而且**两侧是同一个形状**（建轴的一侧不剔，幽灵字符就只污染
  //    门禁那侧的窗）。逐字面量写死太脆，所以把两边的 pattern 文本抠出来直接比。
  const pyShape = lib.match(/CONTROL_TOKEN = re\.compile\(r"([^"]+)"\)/);
  const jsShape = nodeSrc.match(/const CONTROL_TOKEN = \/(.+?)\/[a-z]*;/);
  if (!pyShape) {
    throw new Error(`${libPath} 的 CONTROL_TOKEN 不再是 re.compile(r"…") 这个形状：`
      + "判据必须写成「整对括号、括号内无右括号」，写死前缀表就是下一次静默失效");
  }
  if (!jsShape) {
    throw new Error(`${nodePath} 的 CONTROL_TOKEN 不再是 /…/ 字面量：建轴一侧认不出这条形状`);
  }
  if (pyShape[1] !== jsShape[1]) {
    throw new Error(`控制 token 的正则两侧不是同一个形状：Python ${pyShape[1]} vs Node ${jsShape[1]}。`
      + "建轴和门禁听同一份音频、同一族 token，一边剔一边不剔就是两套字序");
  }
  // 形状光看「含不含方括号」是判不出旧 bug 的：旧正则 `^\[[_A-Z]+\d*$` 里 ] 出现在字符类里，
  // 字面上「两个括号都有」，但它**匹配不上** [_TT_628]（结尾多一个 ]），于是以 [ 开头的
  // token 一个都没被剔掉。所以这里把 pattern 拿去真的匹配一遍——必须命中全部实测形态，
  // 必须不命中普通字和一个没闭合的左括号。
  const rx = new RegExp(pyShape[1], "u");
  const MUST = ["[_TT_628]", "[_TT_1376]", "[_BEG_]", "[_EOT_]", "[_EP_12]", "[zh]", " [zh] "];
  const MUST_NOT = ["他", "x", "[x", "你好", "[]x"];
  for (const tok of MUST) {
    if (!rx.test(tok)) {
      throw new Error(`CONTROL_TOKEN 的形状 ${pyShape[1]} 匹配不上实测控制 token ${tok}：`
        + "这就是旧正则缺闭合 ] 的静默失效——clean() 把 [_TT_628] 变成 _TT_六二八 七个幽灵字符，"
        + "数字还折成汉字，于是它能和文案里真念出来的数字抢一次命中，而那个时刻是段界不是说话点");
    }
  }
  for (const tok of MUST_NOT) {
    if (rx.test(tok)) {
      throw new Error(`CONTROL_TOKEN 的形状 ${pyShape[1]} 把 ${tok} 当成控制 token 剔掉了：`
        + "剔过头等于把真说话的字从字符流里删掉，行首锚点会整行往后错");
    }
  }
  if (!lib.includes("if not txt or CONTROL_TOKEN.match(txt):")) {
    throw new Error(`${libPath} 的 asr_char_stream() 不再用 CONTROL_TOKEN 过滤字符流`);
  }
  if (!nodeSrc.includes("CONTROL_TOKEN.test(t)")) {
    throw new Error(`${nodePath} 的 buildAsrTokens 没有过滤 CONTROL_TOKEN：轴仍然带着段界假 token`);
  }
  // ② 按位对位只在计数相等时发生，且键从真命中块**之后**起算。
  if (!lib.includes("if gs > 0 and gs == ga:")) {
    throw new Error(`${libPath} 不再要求 gs == ga：数量不等（漏听/多听/吞字）也猜时刻，`
      + "门禁会信一个编出来的窗——宁可内插不可编时刻");
  }
  if (!lib.includes("pos[b0.a + b0.size + off] = b0.b + b0.size + off")) {
    throw new Error(`${libPath} 的按位对位键起点不是 b0.a + b0.size：写成 b0.a 会把时刻盖到`
      + "上一个真命中块自己的字符上（第一版就是这么错的，全库 A/B 因此报出 46 行假收益）");
  }
  // ②b 同一条规则必须管**首尾两档空档**（2026-10-10《挪威的森林》钩子行 2「一响照旧」
  // 被听成「一想招救」）：`zip(blocks, blocks[1:])` 结构上看不见第一块之前和最后一块之后，
  // 那两档于是落到 align() 的两条兜底分支——而它们不是内插，是**编时刻**（行首整段贴到
  // 第一个锚点自己的 from/to，行尾凭空 te0+0.20）。编出来的 8.18s 收尾比真实语音短 0.47s，
  // snap ① 的天花板只有 8.78s，末条 8.78÷9.12=0.963<0.98 → 「时间轴塌陷，拒绝写入」，
  // 整期卡在轴上。写在这里而不是写在注释里，是因为这条被删掉时全库不会有任何门变红。
  if (!lib.includes("pos[off] = off")) {
    throw new Error(`${libPath} 的行首档不再按位对位：第一块之前的悬空字符会被贴到第一个锚点`
      + " 自己的时刻（整段共用一个 from/to），那是编时刻而不是内插");
  }
  const tailPair = lib.match(/pos\[last\.a \+ last\.size \+ off\] = (last\.b \+ last\.size \+ off)/);
  if (!tailPair) {
    throw new Error(`${libPath} 的行尾档不再按位对位：最后一块之后的悬空字符会拿到 te0+0.20 `
      + "这个编出来的收尾（《挪威的森林》钩子行 2 就是这么把 8.65s 的语音截成 8.18s 的）");
  }
  // 首尾两档的对位判据与内部空档**同一条**（gs == ga > 0），且各自数到正确的索引空间：
  // 首档 ASR 侧从 0 数到 first.b，尾档数到 len(asr_concat)。写成 first.b + off 会把悬空
  // 字符盖到第一个**已命中**块上——上一版探针（tmp/ab-align-tails.py）正是这样，它报的
  // 「46 行获得锚点」是被污染的对照，已撤回。
  for (const [probe, why] of [
    ["gs, ga = first.a, first.b", "行首档的两侧计数不是 first.a/first.b：索引空间写错就不等量"],
    ["ga = len(asr_concat) - (last.b + last.size)", "行尾档的 ASR 侧没数到 len(asr_concat)：尾档永远凑不出 ga"],
  ]) {
    if (!lib.includes(probe)) {
      throw new Error(`${libPath} ${why}（首尾两档必须和内部空档用同一条 gs == ga 判据）`);
    }
  }
  // ②c 这条回归的产出是**测试**，不是代码注释：并行编辑覆盖过一次「测试写进去了、lib 后来
  // 又被改回去」的事故（见上面 Speech bursts 那段），所以四个名字必须还在这个测试里点名。
  // WITHDRAWN_TAIL_END / WITHDRAWN_HEAD 是被撤形状的两个编时刻常量，删掉它们就等于删掉了
  // 「改动前那个形状确实会做错」的对照——断言会空转，而 check 仍然是绿的。
  const anchorTest = fs.readFileSync(path.join(ROOT, "scripts/tests/test-positional-anchor.py"), "utf8");
  for (const token of ["WITHDRAWN_TAIL_END", "WITHDRAWN_HEAD", "pos[off]=off", "挪威的森林"]) {
    if (!anchorTest.includes(token)) {
      throw new Error(`test-positional-anchor.py 不再点名 ${token}：首尾两档的按位对位失去对照，`
        + "被撤形状（行尾编 0.20s / 行首全体贴同一锚点 / 键写成 first.b+off）回来时没人判红");
    }
  }
  // ③ 近音锚点算 flags 但不灌 matched，否则日志里的「命中 X/Y」和历史不可比。
  if (!lib.includes("return times, etimes, flags, sum(b.size for b in blocks)")) {
    throw new Error(`${libPath} 的 matched 不再是纯字面命中数：近音对位灌进命中数，`
      + "「ASR 字序命中 X / Y」这条日志就再也无法和历史对照");
  }
  // ④ 门禁仍然从 lib 取窗和突发，不自己算一套。
  const gate = fs.readFileSync(path.join(ROOT, "scripts/check-caption-speech-align.py"), "utf8");
  if (!gate.includes("audible_windows") || !gate.includes("speech_bursts")) {
    throw new Error("check-caption-speech-align.py 不再从 lib 取 audible_windows/speech_bursts："
      + "逐行覆盖的门禁自己算一套窗");
  }
  if (!gate.includes("原始密度") || !gate.includes("raw < a.min_raw")) {
    throw new Error("check-caption-speech-align.py 的 T2 不再只报被跨过的那一条判据："
      + "「窗内语音 100% <45%」这类自相矛盾的文案会把人推去追一条并没有红的线");
  }
  for (const token of ["控制 token", "按位对位", "编时刻"]) {
    if (!ruleSays(token)) {
      throw new Error(`规则文档没写出 ${token}：自身可听窗的这条口径无法被执行`);
    }
  }
  // ⑤ 第三条独立证人：固定 −35 dBFS 的**绝对电平**静音段（2026-10-10《美丽新世界》
  // 行 52「所以，」/行 86 两种形状）。这条口径有两半，每一半都能被**单独**删掉而不让任何
  // 一道门禁变红，所以两个操作数都得钉住：
  //   · 兜底线是两条证人的**合取**。只撤第二条（above_noise_share）就回到「整窗听不见人声」
  //     由本期录音有多响决定：`speech_bursts` 的阈值按本期 p25/p90 自适应（实测 −16~−21 dBFS），
  //     同一声轻声字在响的期算语音、在轻的期算静音，于是一条听得到的两字字幕被判红，
  //     而它在轴上无处可挪（念 0.34 秒，必须待 0.55 秒才读得完）。
  //   · 推窗交叉丢弃的锚点按「是否过半落在一截真停顿里」才恢复。只撤这一半，卡在两个突发
  //     之间的那一整个自身窗就没了，T2 只能退回那条会漂的密度——两半是互相成就的。
  // 反向（恢复无条件 + 兜底恒不触发）会同时洗白压在纯静音上的字幕、并凭空造出一条 T1
  // 「提前消失」红（《汤姆叔叔的小屋》行 34 的假锚点），所以这里要的是「两个都在」。
  const acousticLib = [
    [/^def silence_runs\(/m, "绝对电平静音证人本身（ffmpeg silencedetect 的那一处解析）"],
    [/^def silence_share\(/m, "静音占比的并集与钳窗算术"],
    [/^def above_noise_share\(/m, "它的补集：窗内抬在噪声底之上的占比"],
    [/SILENCE_NOISE_DB\s*=\s*-35\.0/, "固定噪声底那一个数（不随本期响度漂）"],
    [/ANCHOR_PAUSE_SHARE\s*=\s*0\.50/, "「锚点过半落在一截真停顿里」的分界值"],
    [/elif sil is not None and pinned\[1\] > pinned\[0\][\s\S]{0,120}?< ANCHOR_PAUSE_SHARE:/,
      "恢复只对「不是过半静音」那一类开（未接 sil 的调用方逐位不变）"],
  ];
  for (const [re, why] of acousticLib) {
    if (!re.test(lib)) {
      throw new Error(`${libPath} 失去 ${why}：「整窗听不见人声」重新由本期录音的响度决定`);
    }
  }
  for (const [tok, why] of [
    ["silence_runs", "门禁不再向 lib 要第三条证人"],
    ["above_noise_share(sil, cs, ce) < a.min_raw", "兜底线的合取只剩一条证人（复用了 --min-raw，没新增阈值）"],
    ["--no-acoustic-floor", "旧口径对照开关没了：这套口径没法 A/B"],
    ["SILENCE_NOISE_DB", "打印的噪声底不是从 lib 取的：lib 改了而报告还在写旧数"],
    ["sil=sil", "门禁把证人交给建窗那一步的动作没了：恢复判据无从执行"],
  ]) {
    if (!gate.includes(tok)) {
      throw new Error(`check-caption-speech-align.py 不再出现 "${tok}"：${why}`);
    }
  }
  // 噪声底只有一个真源。文档里的「−35 dBFS」是 U+2212（不是这个谓词的操作数），
  // 代码里手抄的负号一定是 ASCII，所以这条只抓真正会漂的那一处。
  if (/-35(\.0)?\s*dBFS/.test(gate)) {
    throw new Error("check-caption-speech-align.py 里出现手抄的 -35 dBFS：噪声底必须走 SILENCE_NOISE_DB")
  }
  // 修轴的手必须和门禁听同一份证人：任何一侧没接上，就是「修的人按一套口径吸、
  // 验的人按另一套口径判，永远修不到绿」——lib 文件头那句话的字面再现。
  for (const [file, tok, why] of [
    ["scripts/snap-body-timings-vad.py", "sil=sil", "覆盖手没接证人：它按旧口径吸窗，门禁按新口径判"],
    ["scripts/audit-caption-axis.py", "sil=noise_floor", "能量审计的 density() 没接证人：裁尾巴的手与门禁分家"],
    ["scripts/trim-t2-leadout.py", "sil=silence_runs(", "裁 T2 空放尾巴的手没接证人：它会裁掉一条本来听得到的字幕"],
  ]) {
    if (!fs.readFileSync(path.join(ROOT, file), "utf8").includes(tok)) {
      throw new Error(`${file} 不再出现 "${tok}"：${why}`);
    }
  }
  const retimeSrc = fs.readFileSync(path.join(ROOT, "scripts/retime-asr-segments.py"), "utf8");
  if (!retimeSrc.includes("silence_runs")) {
    throw new Error("retime-asr-segments.py 的 silences() 不再委托 lib.silence_runs："
      + "绝对电平静音段的解析重新有了第二份（两份会各自漂）");
  }
  // 这条回归的产出是**测试**，不是注释：并行编辑覆盖过一次「测试写进去了、lib 又被改回去」。
  const apTestFile = "scripts/tests/test-acoustic-presence.py";
  const apTestPath = path.join(ROOT, apTestFile);
  if (!fs.existsSync(apTestPath)) {
    throw new Error(`${apTestFile} 不存在：绝对电平证人只有当期那一次实测，没有可复现的对照`);
  }
  const apTest = fs.readFileSync(apTestPath, "utf8");
  for (const [tok, why] of [
    ["run_gate_patched", "口径的两半不再被**各自**按掉：三臂对照说不清是恢复还是合取救下的轻声行"],
    ["ANCHOR_PAUSE_SHARE", "恢复判据的分界值不再被断言：把它写成 0 等于删掉恢复，而没人翻红"],
    ["above_noise_share", "兜底线的第二条证人不再被断言：同上，方向相反"],
    ["--no-read-pad", "富余停留不再被排除出归因：没人能证明那条绿不是上一次口径改动给的"],
    ["夹具失效", "波形与时间轴不再自证：断言会在一副错夹具上「通过」"],
    ["字序命中", "弃权防线消失：门禁对一条本就没有自身窗的轴只标记不判红，而测试以为在判它"],
  ]) {
    if (!apTest.includes(tok)) {
      throw new Error(`${apTestFile} 少了 ${tok}：${why}`);
    }
  }
  // 自证：把三个被撤形状重新注回各自的谓词，谓词必须翻（否则上面那些「必须还包含 X」是永真式）。
  for (const [mutated, re, name] of [
    [gate.replace(/[ \t]*if quiet and sil is not None:\n[ \t]*quiet = above_noise_share\(sil, cs, ce\) < a\.min_raw\n/, ""),
     /above_noise_share\(sil, cs, ce\) < a\.min_raw/, "撤掉合取的第二半（回到单证人兜底）"],
    [lib.replace(/[ \t]*elif sil is not None[\s\S]{0,120}?ANCHOR_PAUSE_SHARE:\n[ \t]*out\[order\] = \(round\(pinned\[0\], 3\), round\(pinned\[1\], 3\)\)\n/, ""),
     /elif sil is not None[\s\S]{0,120}?< ANCHOR_PAUSE_SHARE:/, "撤掉整条恢复分支（交叉即弃的旧口径）"],
    [lib.replace(/silence_share\(sil, \*pinned\) < ANCHOR_PAUSE_SHARE/, "True"),
     /silence_share\(sil, \*pinned\) < ANCHOR_PAUSE_SHARE/, "把恢复做成无条件（不分过半静音）"],
    [gate.replace(/SILENCE_NOISE_DB/g, "-35.0"), /SILENCE_NOISE_DB/, "把 lib 的数换成手抄的 −35"],
  ]) {
    if (re.test(mutated)) {
      throw new Error(`绝对电平证人的静态判据推不动（${name}）：形状改掉了而 check 仍然绿，那条断言是空转`)
    }
  }
  // 反向：手抄噪声底的谓词也必须在合成样本上命中，否则它在真文件上「不命中」只是巧合。
  if (!/-35(\.0)?\s*dBFS/.test('print(f"噪声底 -35 dBFS → 静音段 N 个")')) {
    throw new Error("手抄 -35 dBFS 的判据永不命中：那条断言测不到任何东西")
  }

  // 富余停留那条回归测试必须复现**整条** T2 判据，不是它的一半。2026-10-11 发现它只写了
  // `v < FLOOR or r < RAW_FLOOR`：10-10 兜底线改成两条证人的合取之后，这条测试在替一个已经
  // 退休的形状当证人，而它自己跑得很绿。后果当场露出来——旧②拿 193.00–193.90 当「整窗压在
  // 真空静上」，实测这段窗原始密度 0%、抬在噪声底之上 100%，shipped 口径下它是绿的。
  // 所以钉三样：合取的两行、被换到实测静音跑上的②、以及把旧窗钉成「必须是绿」的 ②b 退休对照；
  // 再加⑦⑧那两组**真实数据**交互臂（按掉豁免 / 按掉电平各掉一行），它们是「豁免与证人相互
  // 独立」这件事唯一的实物证据——合成轴造不出「只需要一半」和「两半都要」这两种形状并存。
  const vrTestFile = "scripts/tests/test-voiced-share-readable.py";
  const vrTestPath = path.join(ROOT, vrTestFile);
  if (!fs.existsSync(vrTestPath)) {
    throw new Error(`${vrTestFile} 不存在：富余停留豁免只有当期那一次实测，没有可复现的对照`);
  }
  const vrTest = fs.readFileSync(vrTestPath, "utf8");
  const vrGuards = [
    [/an = None if sil is None else above_noise_share\(sil, cs, ce\)/, "t2() 不再向绝对电平证人要第二个操作数：兜底线重新只由本期响度决定"],
    [/if quiet and sil is not None:\n\s*quiet = an < RAW_FLOOR/, "合取的第二半被删掉：测试回到 10-10 之前的半条口径"],
    [/PARK_WIN = \(196\.95, 197\.65\)/, "②不再站在实测静音跑上：那条「整窗无声」的对照会是永真的"],
    [/\(193\.00, 193\.90\)[\s\S]{0,400}?不再是绿/, "②b 退休对照没了：没人拦住把②挪回那个 shipped 口径判绿的窗"],
    [/SIL_52/, "美丽新世界 行52（两半都要）这组真实夹具被删：⑦变成合成轴上的空话"],
    [/SIL_86/, "同一期 行86（只要电平这一半）被删：⑦⑧ 的互斥对照做不成，读不出是同一手在救"],
  ];
  for (const [re, why] of vrGuards) {
    if (!re.test(vrTest)) {
      throw new Error(`${vrTestFile} 失去 ${why}：门禁改口径而它的回归还在替旧形状作证`);
    }
  }
  // 噪声底只有一个真源，测试也不例外：它用 above_noise_share，不需要知道那个数。
  if (/-35(\.0)?\s*dBFS/.test(vrTest)) {
    throw new Error(`${vrTestFile} 里出现手抄的 -35 dBFS：噪声底必须留在 lib 那一侧`)
  }
  // 自证：把被撤的半条口径重新注回上面每一条谓词，谓词必须翻（否则「必须还包含 X」是空转）。
  for (const [mutated, re, name] of [
    [vrTest.replace(/[ \t]*if quiet and sil is not None:\n[ \t]*quiet = an < RAW_FLOOR\n/, ""),
     /if quiet and sil is not None:/, "撤掉合取的第二半（回到只看自适应密度的兜底）"],
    [vrTest.replace(/else above_noise_share\(sil, cs, ce\)/, "else burst_share(bursts, cs, ce)"),
     /else above_noise_share\(sil, cs, ce\)/, "把电平证人换成密度证人自己：合取退化成同一条线量两遍"],
    [vrTest.replace(/PARK_WIN = \(196\.95, 197\.65\)/, "PARK_WIN = (193.00, 193.90)"),
     /PARK_WIN = \(196\.95, 197\.65\)/, "②挪回那截其实有声的窗"],
    [vrTest.replace(/[ \t]*# ②b 旧②用的[\s\S]*?当可用的控制"\)\n/, ""),
     /\(193\.00, 193\.90\)[\s\S]{0,400}?不再是绿/, "删掉 ②b 整段退休对照"],
    [vrTest.replace(/SIL_52/g, "BN_52"), /SIL_52/, "行52 的电平夹具改名成密度夹具（等于没有第二条证人）"],
    [vrTest.replace(/SIL_86/g, "BN_86"), /SIL_86/, "同上，方向在行86"],
  ]) {
    if (re.test(mutated)) {
      throw new Error(`富余停留回归的静态判据推不动（${name}）：形状改掉了而 check 仍然绿，那条断言是空转`)
    }
  }
}
for (const token of ["snap-body-timings-vad.py", "check-caption-speech-align.py"]) {
  if (!ruleSays(token)) {
    throw new Error(`规则文档没有写出 ${token}：逐行覆盖规则无法被执行`);
  }
}
// 建轴链里覆盖必须排在能量审计之前：审计的 C 规则裁的正是覆盖加的尾部停留，反过来会被撑开。
{
  const text = ruleDocsCorpus();
  const chain = text.split("\n").find((ln) => ln.includes("create-body-timings-precise.mjs")
                                        && ln.includes("audit-caption-axis.py"));
  if (!chain || chain.indexOf("snap-body-timings-vad.py") < 0
      || chain.indexOf("snap-body-timings-vad.py") > chain.indexOf("audit-caption-axis.py")) {
    throw new Error(`规则文档的建轴顺序不再是「token 轴 → 覆盖 → 能量审计」：先审计会把覆盖裁掉`);
  }
}

// 正式混音路径（JS）同样不许自带秒数：它只排音频，但一旦偏移被写回字面量，声音就会
// 与 Python 侧逐帧合成的涟漪错窗——门禁量的是成片，错窗的成片只会「看着没波」。
// 真源是 scripts/lib/water-ripple.json，Python 与 Node 各有一个薄壳读它。
const jsFinal = fs.readFileSync(path.join(ROOT, "scripts/render-episode-final.mjs"), "utf8");
for (const token of ['from "./lib/water-ripple.mjs"', "waterStartSeconds(INTRO_TRIM_SECONDS)",
                     "atrim=0:${WATER_DUR_SECONDS}"]) {
  if (!jsFinal.includes(token)) {
    throw new Error(`render-episode-final.mjs 缺少 ${token}：水波音效必须从 lib 的常数推起点`);
  }
}
for (const [pat, why] of [[/atrim=0:1\.06/, "水波 atrim 写成了硬编码秒数"],
                          [/INTRO_TRIM_SECONDS \+ 0\.32/, "水波起点写成了硬编码偏移"]]) {
  if (pat.test(jsFinal)) {
    throw new Error(`render-episode-final.mjs：${why}：改读 scripts/lib/water-ripple.mjs`);
  }
}

// 水波同窗对所有渲染路径一律硬失败（2026-09-27 起，此前只 console.warn）。
// 为什么必须硬：《白鲸》就是从这里漏过去的——它用的是从《道林》派生的期专用兜底渲染器，
// 混了 water.mp3 却没有涟漪，分辨率/时长/音频流/字幕/切点全部照常通过，警告又被淹没在
// 29 行历史名单里，交付时才靠人眼发现「只有水声没有水波」。
// 判定：凡引用 assets/sfx/water.mp3 的渲染脚本，必须 ① 从 lib 拿常数 ② 背景层真的调 ripple()
// ③ 把 check-water-ripple.py 卡在成片激活之前。例外只能是早于本规则的历史脚本，
// 且必须显式写进 WATER_LEGACY_BASELINE —— 新增期专用渲染器不再允许走「警告」这条路。
//
// 2026-10-11 归档：这份名单原本 26 条里有 18 条指向「期专用兜底渲染器」——每期一份、
// 硬编码书名、复制同一套渲染逻辑。它们在 2026-10-08 10:46 被 render_scene_map_template_fallback.py
// （EPISODE 环境变量驱动、读 scene-map.json）整体取代，之后再没被生产入口调用过。
// 那 18 条连同文件一起移入 scripts/archive/legacy-renderers/，豁免随之撤销：
// 归档目录不在 listCodeFiles("scripts") 的扫描面内，所以它们既不参与判定也不需要豁免。
// 留下的 8 条是**仍在生产面**的早期路径（分块渲染器、早期拼装器、render_witte.py），
// 它们的豁免仍然有效——这些路径确实还在被 check-render-artifacts.py 一类调用。
// 若哪天其中某条也被取代，删掉它的豁免即可；删不掉也不该新增，新脚本一律走硬门禁。
const WATER_LEGACY_BASELINE = new Set([
  "scripts/_final_mux_rxdl.mjs",
  "scripts/_manual-final-mux.mjs",
  "scripts/render-body-chunked-ts.mjs",
  "scripts/render-body-chunked-xw.mjs",
  "scripts/render-dorian-chunked.mjs",
  "scripts/render-episode-final-xw.mjs",
  "scripts/render-whale-chunked.mjs",
  "scripts/render_witte.py",
]);
// 名单里每一条都必须真的还在生产面。留一个指向归档目录或不存在的路径，等于给
// 「这台机器现在渲染的是什么」再添一条无人维护的说法——而这类说法的失效方式恰恰是
// 全绿：豁免名单读不到任何东西时不会报错，只会让一条真规则静默失效。
for (const file of WATER_LEGACY_BASELINE) {
  if (!fs.existsSync(path.join(ROOT, file))) {
    throw new Error(`WATER_LEGACY_BASELINE 里的 ${file} 不在了：要么它已归档（连同豁免一起删掉），`
      + "要么路径被改过。豁免名单不能留悬空条目——它失效时不报错，只会让水波硬门禁静默失守。");
  }
}
const waterMixers = listCodeFiles("scripts").filter((file) => {
  // lib 自身、门禁自身、测试不参与「渲染脚本必须施加涟漪」的判定：
  // check-water-ripple.py 当然要引用 water.mp3（它拿音效做互相关），但它不画水波。
  if (/^scripts\/(?:lib|tests)\//.test(file)) return false;
  if (file === "scripts/check-water-ripple.py") return false;
  return /water\.mp3/.test(fs.readFileSync(path.join(ROOT, file), "utf8"));
});
for (const file of waterMixers) {
  if (WATER_LEGACY_BASELINE.has(file)) continue;
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  if (!/water[-_]ripple/.test(source)) {
    throw new Error(`${file} 混了 water.mp3 却没读 scripts/lib/water-ripple：`
      + "只有水声没有涟漪的成片能过完其余全部验收，必须从 lib 拿 WATER_OFFSET/WATER_DUR 并在背景层调 ripple()");
  }
  // 只 import 不算接上：涟漪必须真的作用到画面上。合法的接法只有两种——
  //   ① 自己在背景层逐帧调 ripple()/water_u（正片渲染器必须走这条）；
  //   ② 把成品交给 scripts/add-water-ripple.py（独立片头 intro-only.mp4 走这条，
  //      AGENTS.md 规定该脚本只用于渲染器覆盖不到的素材）。
  // 带字幕层的渲染器不许走 ②：涟漪会把已经烧进去的字幕抹糊。
  if (file.endsWith(".py")) {
    for (const token of ["from water_ripple import", "WATER_OFFSET", "WATER_DUR"]) {
      if (!source.includes(token)) {
        throw new Error(`${file} 缺少 ${token}：背景层必须按 lib 的常数施加涟漪，否则水声只有声音没有画面`);
      }
    }
    const appliesInline = source.includes("ripple(") || source.includes("water_u");
    const delegates = source.includes("add-water-ripple.py");
    if (!appliesInline && !delegates) {
      throw new Error(`${file} 只 import 了 lib 常数，没把涟漪接到画面上：`
        + "背景层要么调 ripple()/water_u，要么调用 scripts/add-water-ripple.py，否则就是换了个隐蔽写法的「只有水声」");
    }
    if (delegates && !appliesInline && /cap-|caption/i.test(source)) {
      throw new Error(`${file} 用 add-water-ripple.py 给带字幕层的画面贴片：`
        + "涟漪会把烧进去的字幕抹糊，正片必须在背景层调 ripple() 后合字幕");
    }
    if (/adelay=\{?\s*[\d.]+/.test(source)) {
      throw new Error(`${file} 把 adelay 写成了硬编码秒数：必须由 lib 的 WATER_OFFSET 算出 WATER_START`);
    }
  }
  // 用 Node 侧薄壳（render-episode-final.mjs）的路径只排音频，画面由 HyperFrames 合成，
  // 不要求它 shutil.move；Python 逐帧合成器才检查「门禁前置于激活」。
  if (file.endsWith(".py") && /shutil\.move\(/.test(source)) {
    const gateCall = source.indexOf("check-water-ripple.py");
    const activate = source.indexOf("shutil.move(");
    if (gateCall < 0) {
      throw new Error(`${file} 没有调用 scripts/check-water-ripple.py：水波验收没接进渲染流程`);
    }
    if (gateCall > activate) {
      throw new Error(`${file} 的水波门禁跑在激活（shutil.move）之后：不合格成片会先覆盖已交付成片，门禁必须前置`);
    }
  }
}
const stale = [...WATER_LEGACY_BASELINE].filter((f) => {
  if (!fs.existsSync(path.join(ROOT, f))) return true;
  const source = fs.readFileSync(path.join(ROOT, f), "utf8");
  return !/water\.mp3/.test(source) || /water[-_]ripple/.test(source);
});
if (stale.length) {
  console.warn(`Warning: WATER_LEGACY_BASELINE 里有 ${stale.length} 项已不再混水声或已接上 lib，可清理：`
    + stale.join(", "));
}

// 上屏零标点、零空白间隙：字符表真源只有一份 scripts/lib/subtitle-punctuation.json，期专用
// 兜底渲染器也不许自己抄。《白鲸》2026-09-27 就是这么漏过去的——它的 `caption_lines` 只对空格切出来
// 的片段做 `rstrip(TRAILING_PUNCT)`，行中间的「、」原样画上屏（order 53「庞杂、缓慢」），
// 而分辨率/时长/字幕对齐/响度全部照常通过。这跟「只有水声没有水波」是同一类缺陷：成片看着
// 完全正常，只有量化门禁拦得住。规则本体见 AGENTS.md「Visual And Audio Rules」。
// 三道静态检查：① 凡定义 `caption_lines` 的 render_*.py 必须读 lib；② 凡写 caption-track.json
// 的必须同时落 `display` 字段——没有它，check-caption-punctuation.py 只能报「老渲染器需重渲」，
// 上屏残留根本查不出来（本期第一次跑就是这样：79/79 条无 display）；③ 通用渲染器必须落
// `captionStyle` 标记（上面单独盯）——2026-09-27 把断点号从「换成全角空格」改成「换成换行」，
// 靠这个标记才能区分「按新口径渲的（判红）」和「历史轨（进待重渲清单）」，否则规则落地那一刻
// 28 期一起红灯、正在渲的会话被掐断。期专用老渲染器不强制补标记：它们的轨本来就属于旧口径，
// 交付时 `check-episode-delivery.py` 一律带 --strict，想蒙混过去只会立刻判红。
const punctLibRe = /caption_display|subtitle_display|subtitle-punctuation\.json/;
const captionDrawers = listCodeFiles("scripts").filter((file) =>
  /(^|\/)render_.*\.py$/.test(file)
  && /def caption_lines/.test(fs.readFileSync(path.join(ROOT, file), "utf8")));
// 2026-10-11 归档：这里原本列了 9 个期专用兜底渲染器，全部已于同日移入
// scripts/archive/legacy-renderers/（被 render_scene_map_template_fallback.py 整体取代）。
// 保留名单不是为了放过它们，是为了「已接上 lib 或不再画字幕」时能主动提示可清理——
// 所以清空它，而不是把归档路径写进来。归档目录不在 listCodeFiles 的扫描面内，
// 不需要豁免；写进来反而会让下面的 stalePunct 天天报「可清理」。
const PUNCT_LEGACY_BASELINE = new Set([]);
for (const file of captionDrawers) {
  if (PUNCT_LEGACY_BASELINE.has(file)) continue;
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  if (!punctLibRe.test(source)) {
    throw new Error(`${file} 自己抄了一份上屏标点表：改读 scripts/lib/caption_display.py`
      + `（真源 subtitle-punctuation.json），否则句中 ，、；： 会原样画上屏`);
  }
  if (/caption-track\.json/.test(source) && !/"display"/.test(source)) {
    throw new Error(`${file} 写的 caption-track.json 没有 display 字段：`
      + "标点门禁查不到上屏残留，只会退化成「老渲染器需重渲」");
  }
}
const stalePunct = [...PUNCT_LEGACY_BASELINE].filter((file) => {
  if (!fs.existsSync(path.join(ROOT, file))) return true;
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  return punctLibRe.test(source) || !/def caption_lines/.test(source);
});
if (stalePunct.length) {
  console.warn(`Warning: PUNCT_LEGACY_BASELINE 里有 ${stalePunct.length} 项已接上 lib 或不再画字幕，可清理：`
    + stalePunct.join(", "));
}

// 正文段那一行常驻声明「内容为书评与感悟[全角空格]素材来源于网络」（2026-10-05 用户要求：
// 「底部居中、深灰、小字」，且只属于正文段）。规则本体见 AGENTS.md「Visual And Audio Rules」，
// 文案与几何的唯一真源是 scripts/lib/disclaimer.json（Python 侧 lib/disclaimer.py、
// JS 侧 lib/disclaimer.mjs 都读它）。这里守四个前提，每一个都对应一种「看着正常、
// 只有门拦住」的失真：
// ① 两条渲染路径各自 import lib，不许抄文案或几何——一条 26px 的灰字被手抄进第二个地方，
//    第一处改了以后没人知道屏幕上到底是哪一版（同 subtitle-punctuation.json 的理由）。
// ② 钩子那个调用点必须 showDisclaimer:false。声明属于正文段：钩子那 5 秒还没把「这是谁的书」
//    交给观众，先压一行来源说明等于自降搬运（同《书名》层不进钩子，而那条 flag 也没拦住
//    《恶意》的旧像素，所以交付侧还有 check-disclaimer-overlay.py 量成片）。
// ③ lib 里记的两块保留带坐标必须还是那两个消费者**现在**用的常量。谁挪了门禁自己的取样区
//    而没挪 lib，lib 的「不许相交」断言就在量一片早就不是那两块的地盘——绿得假，
//    而且假得难查：那行灰字会被边界压测当成字幕墨迹、被判据⑥当成画面墨迹。
// ④ 交付包装脚本必须真的在跑这道门（第十二道），否则规则只剩文档。
const disclaimerLibFile = path.join(ROOT, "scripts", "lib", "disclaimer.json");
if (!fs.existsSync(disclaimerLibFile)) {
  throw new Error("scripts/lib/disclaimer.json 不见了：常驻声明的文案与几何又变成各渲染器手抄");
}
const disclaimerLib = JSON.parse(fs.readFileSync(disclaimerLibFile, "utf8"));
const disclaimerText = disclaimerLib.parts.join("");
const fallbackRendererFile = "scripts/render_scene_map_template_fallback.py";
// 源本体已由上面水波那处读进 fallbackRenderer（同一个文件，不重复读第二份）
if (!/import disclaimer as _disclaimer|from disclaimer import|lib\/disclaimer\.py/.test(fallbackRenderer)
  || !/_disclaimer\.overlay\(/.test(fallbackRenderer)) {
  throw new Error(`${fallbackRendererFile} 不再从 scripts/lib/disclaimer.py 画常驻声明：`
    + "本机实际交付路径自己画一份，文案和几何就会跟 lib 分叉");
}
if (!/disclaimer_png/.test(fallbackRenderer)
  || !/-i", disclaimer_png|disclaimer_png,/.test(fallbackRenderer)) {
  throw new Error(`${fallbackRendererFile} 生成了 disclaimer.png 却没把它 overlay 进正文段：`
    + "叠层只落在磁盘上，成片像素里一行都没有");
}
for (const token of ["disclaimerCss", "disclaimerHtml", "./lib/disclaimer.mjs"]) {
  if (!previewSource.includes(token)) {
    throw new Error(`scripts/create-episode-preview.mjs 不再引用 ${token}：`
      + "HyperFrames 那条路径的常驻声明退回手写 CSS，两条渲染路径两种样子");
  }
}
for (const token of ["showDisclaimer ? disclaimerCss()", "showDisclaimer ? disclaimerHtml("]) {
  if (!previewSource.includes(token)) {
    throw new Error(`scripts/create-episode-preview.mjs 里 ${token} 不见了：`
      + "声明层不再由 flag 控制，钩子段就会跟着带上它");
  }
}
if (!/showDisclaimer:\s*false/.test(previewSource)) {
  throw new Error("scripts/create-episode-preview.mjs 的钩子调用点没有 showDisclaimer: false："
    + "常驻声明会压在钩子段上（第十二道门 check-disclaimer-overlay.py 对成片判红）");
}
// 文案只在 lib 里出现一次。任何渲染器自己写一遍这行字，就是一份会漂移的副本
// （验收门禁的 docstring 里点名文案是允许的——它判的是 lib 现算的墨迹，不是这串字）。
for (const file of [fallbackRendererFile, "scripts/create-episode-preview.mjs",
  "scripts/render-episode-final.mjs"]) {
  const p = path.join(ROOT, file);
  if (!fs.existsSync(p)) continue;
  if (fs.readFileSync(p, "utf8").includes(disclaimerText)) {
    throw new Error(`${file} 里硬编码了常驻声明文案：改 scripts/lib/disclaimer.json`);
  }
}
// ③ 保留带坐标对账：lib 记的是别人的常量，那常量得还是那个值。
const reservedBands = disclaimerLib.reserved_bands || {};
const capReserved = reservedBands.caption_sweep;
if (!capReserved) {
  throw new Error("disclaimer.json 缺 reserved_bands.caption_sweep：常驻声明的带位不再是"
    + "「避开字幕取样区」推导出来的");
}
const captionSweepSrc = fs.readFileSync(path.join(ROOT, "scripts/qc-caption-alignment.py"), "utf8");
// 只钉 y：x 那两个位置写的是 W（`BAND = (0, 680, W, 850)`），别把它当成数字去匹配。
const bandMatch = captionSweepSrc.match(
  /^BAND\s*=\s*\(\s*[^,]+,\s*(\d+)\s*,\s*[^,]+,\s*(\d+)\s*\)/m);
if (!bandMatch) {
  throw new Error("qc-caption-alignment.py 的 BAND 常量形状变了：lib/disclaimer.json 里记的 "
    + "caption_sweep 保留带无从对账，先把它改成现在的取样区");
}
if (Number(bandMatch[1]) !== Number(capReserved.y0) || Number(bandMatch[2]) !== Number(capReserved.y1)) {
  throw new Error(`字幕边界压测的取样带现在是 y ${bandMatch[1]}..${bandMatch[2]}，`
    + `而 disclaimer.json 记的是 y ${capReserved.y0}..${capReserved.y1}：两边必须一起改，`
    + "否则 lib 的「不许相交」断言量的是过期的地盘，那行灰字会被当成字幕墨迹");
}
const flickReserved = reservedBands.flicker_evidence;
if (flickReserved) {
  const flickerSrcForBand = fs.readFileSync(
    path.join(ROOT, "scripts/check-luminance-flicker.py"), "utf8");
  const lumaRows = flickerSrcForBand.match(/LUMA_BAND_ROWS\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)/);
  if (!lumaRows) {
    throw new Error("check-luminance-flicker.py 的 LUMA_BAND_ROWS 形状变了："
      + "disclaimer.json 里记的取证窗口无从对账");
  }
  if (Number(lumaRows[2]) !== Number(flickReserved.rows[1])) {
    throw new Error(`判据⑥的取证窗口现在是探针第 ${lumaRows[1]}..${lumaRows[2]} 行，`
      + `而 disclaimer.json 记的是 ${flickReserved.rows.join("..")}：两边必须一起改，`
      + "否则「声明带落在取证窗口之外」这句前提已经过期");
  }
}
// ④ 交付包装脚本：GATE_NAMES 里有点名，且真的 run_gate 调了这个脚本。
const deliveryWrapper = fs.readFileSync(path.join(ROOT, "scripts/check-episode-delivery.py"), "utf8");
if (!deliveryWrapper.includes("check-disclaimer-overlay.py")) {
  throw new Error("check-episode-delivery.py 不再调用 check-disclaimer-overlay.py："
    + "常驻声明只剩文档和渲染器里的代码，交付时没人问它进没进像素");
}
if (!/常驻声明上屏/.test(deliveryWrapper)) {
  throw new Error("check-episode-delivery.py 的 GATE_NAMES 少了「常驻声明上屏」："
    + "汇总行会少一道，而打印的「N 道交付门禁全部通过」就成了错数");
}
// 这道门的绿灯只可能来自交付像素，所以它自己必须是可翻面的：合成几种形状（有层 / 没层 /
// 只在一半时间有层 / 白字 / 钩子带层 / 存量无叠层），要求判决跟着形状翻面。
// 少了这条，「lib 里有文案 + 渲染器里有 CSS」就能把整条规则当成已经落地。
const disclaimerTestFile = "scripts/tests/test-disclaimer-overlay.py";
if (!fs.existsSync(path.join(ROOT, disclaimerTestFile))) {
  throw new Error(`${disclaimerTestFile} 不见了 —— 常驻声明门的正反对照是它唯一的形式化`);
}
{
  const src = fs.readFileSync(path.join(ROOT, disclaimerTestFile), "utf8");
  for (const token of ["check-disclaimer-overlay.py", "disclaimer.mjs",
    "disclaimer.png", "body-bg.mp4", "hook-dirty", "证人 B"]) {
    if (!src.includes(token)) {
      throw new Error(`${disclaimerTestFile} 不再引用 ${token}：`
        + "门禁/叠层/背景中间产物换了地方，这条回归就成了空断言");
    }
  }
}
const disclaimerTest = run("python3", [disclaimerTestFile]);
if (disclaimerTest.status !== 0) {
  throw new Error(`常驻声明门对照未通过：\n${disclaimerTest.stdout || ""}${disclaimerTest.stderr || ""}`);
}

// ── 正文书名层字体（2026-10-06 用户要求 2「优化正文部分视频中的书籍字体」）──────────────
// 真源只有一份 scripts/lib/title-font.json，两条渲染路径各有一层薄壳读它。这里守的是**接线**，
// 因为这条规则的两个失败面都不在 JSON 里：
//   ① 上屏的是哪个 face 由 .ttc 的 index 决定，而 index 顺序是打包者定的（实测
//      STHeiti Medium.ttc index 0 = 黑体-繁、PingFang.ttc 的 SC Medium 在 index 7）。任何一处把
//      font-family 或 index 手写回渲染器，就退化成「看着没报错、字形全繁体」——AGENTS.md 在封面上
//      已经记过同一个坑。
//   ② lib 改了 ≠ 像素改了，这是本仓库反复出现的缺陷类（showBookMark 早就在生成器里，而 hook.mp4
//      整段印着书名）。字体一模一样：已交付成片的书名层还是黑体，除非渲染器在**渲那一帧的现场**
//      写出 title-meta.json，让第 13 道门能比对「当时真正加载的 face + 那份像素的 sha」。
// 判决本体在 scripts/check-title-font.py（交付门禁第 13 道），这里只保证它不会被悄悄拆线。
for (const rel of ["scripts/lib/title-font.json", "scripts/lib/title_font.py",
  "scripts/lib/title-font.mjs", "scripts/check-title-font.py"]) {
  if (!fs.existsSync(path.join(ROOT, rel))) {
    throw new Error(`${rel} 不见了 —— 书名字体规则就只剩文档里的一句话`);
  }
}
{
  // 一律扫**去注释之后**的源码：这些守卫问的是「代码里到底怎么写的」，把说明文字当成代码
  // 就变成假警报制造机（本仓库的注释里到处写着「不再有 X」「STHeiti 是退役家族」）。
  const pyRenderer = codeOnly("scripts/render_scene_map_template_fallback.py");
  const jsRenderer = codeOnly("scripts/create-episode-preview.mjs");
  if (!pyRenderer.includes("_title_font.truetype(")) {
    throw new Error("render_scene_map_template_fallback.py 的书名层不再从 lib 取 face："
      + "face 会变成各写一遍的第二份真源，而 PIL 画的才是本机交付的像素");
  }
  for (const token of ["ImageFont.truetype(FONT, _TITLE_SIZE)", "ImageFont.truetype(FONT, 34)"]) {
    if (pyRenderer.includes(token)) {
      throw new Error(`render_scene_map_template_fallback.py 又出现 ${token}：`
        + "书名/作者层用回了字幕的 FONT 常量——用户的授权范围是「书籍字体」，"
        + "把书名换成字幕那款黑体正是本次要改掉的东西");
    }
  }
  // 证人必须同时记 face **和**像素：只记 face 名的话，一张被手改过的 title.png 也能配着
  // 「PingFang SC」的证人过关，而那道门就退化成在比对 lib 和 lib。
  if (!/title-meta\.json/.test(pyRenderer)) {
    throw new Error("渲染器不再写 title-meta.json：第 13 道门无从证明上屏字体，"
      + "「改了 lib」和「改了像素」之间的缺口回来了（重渲已交付的期不会有任何迹象）");
  }
  if (!/title_png_sha256/.test(pyRenderer)) {
    throw new Error("title-meta.json 里少了 title_png_sha256："
      + "证人只证明 face 名而不证明那批像素，手改的 title.png 依然能配着正确的 face 记录过关");
  }
  for (const token of ["titleFontFamilyCss()", "titleFontWeightCss()"]) {
    if (!jsRenderer.includes(token)) {
      throw new Error(`create-episode-preview.mjs 的书名层不再调用 ${token}：`
        + "预览侧的 font-family/字重会和 PIL 侧分叉（900 让浏览器合成假粗而 Pillow 不会）");
    }
  }
  if (/\.book-(?:title|author)\s*\{[^}]*font-weight\s*:\s*900/.test(jsRenderer)) {
    throw new Error("create-episode-preview.mjs 的 .book-title/.book-author 回到 font-weight:900："
      + "face 本身是 Medium，浏览器会合成粗体而 Pillow 不会，两条渲染路径字重不同");
  }
  // 字幕的字体必须**走 title_font 同源解析**：2026-10-07 用户授权「重新优化视频字幕字体」
  // 后，字幕与书名层共用 title-font.json 的 face（旧的 STHeiti Medium.ttc 常量退役——
  // ImageFont 默认 index 0 是 Heiti TC，简体文案一直在拿繁体 face 排）。钉死的是这条
  // 归属，不是某个路径：回退 STHeiti 或绕开 lib 都要红，下一次换字体需要新的显式授权。
  if (pyRenderer.includes("STHeiti Medium.ttc")) {
    throw new Error("render_scene_map_template_fallback.py 里又出现 STHeiti 字幕常量："
      + "2026-10-07 授权后字幕已并入 title_font 同源解析，回退旧字体要有新的授权");
  }
  if (!pyRenderer.includes("font = _title_font.truetype(size)")) {
    throw new Error("render_scene_map_template_fallback.py 的字幕那一路不再从 title_font 取字体："
      + "face 会变成写死路径的第二份真源");
  }
  const watchedBlock = codeOnly("scripts/composition-digest.py")
    .match(/watched\s*=\s*\[([\s\S]*?)\]/);
  if (!watchedBlock || !watchedBlock[1].includes("title-font.json")) {
    throw new Error("composition-digest.py 的 watched 名单不含 scripts/lib/title-font.json："
      + "改字体不动生成器指纹，「成片带的是上一代书名层」这条竞态守卫就看不见它");
  }
  const wrapperCode = codeOnly("scripts/check-episode-delivery.py");
  if (!wrapperCode.includes("check-title-font.py")) {
    throw new Error("check-episode-delivery.py 不再调用 check-title-font.py："
      + "书名字体只剩 lib 和文档，交付时没人问上屏的是哪一款");
  }
  if (!/书名字体核对/.test(wrapperCode)) {
    throw new Error("check-episode-delivery.py 的 GATE_NAMES 少了「书名字体核对」："
      + "汇总行会少一道，而打印的「N 道交付门禁全部通过」就成了错数");
  }
  // 门禁要有可执行的真源，也要有说清「为什么有这道门」的文档：两者缺一个，下一个人要么
  // 把它当多余的红灯摘掉，要么根本不知道该跑它（沿用本仓库每道门都写进两份文档的口径）。
  // 2026-10-11 起判据落在**规则语料**上而非单份文件：书名字体规则已随封面层拆到
  // docs/rules/book-cover-overlay.md，只查 AGENTS.md 会在拆分当天报「无法被执行」，
  // 而下一个人对这类红的反应是往 AGENTS.md 补一句复制品——拆分就白做了。
  if (!ruleSays("check-title-font.py")) {
    throw new Error("规则文档没有写出 check-title-font.py 这条验收命令：书名字体规则无法被执行");
  }
  if (!ruleSays("title-font.json")) {
    throw new Error("规则文档没有点名 scripts/lib/title-font.json：字体真源在文档里失踪，"
      + "下一个人只会去渲染器里改 font-family");
  }
  // 片头的字体不在本次授权范围内（改的是正文那两层）。片头观感本身归 10-05/10-06 那几轮
  // 裁决管，这里只钉一件事：书名层的字体改动不许顺手蔓延到片头。
  const introHtmlForFont = fs.readFileSync(
    path.join(ROOT, "templates", "shared-video-template", "intro", "index.html"), "utf8")
    .replace(/<!--[\s\S]*?-->/g, "");
  for (const marker of ["PingFang", "苹方", "YaHei", "雅黑"]) {
    if (introHtmlForFont.includes(marker)) {
      throw new Error(`共享片头 index.html 出现 ${marker}：书名层的字体授权不覆盖片头，`
        + "片头观感是另一件事（另一次裁决），别顺手「统一一下」");
    }
  }
}

// ── 正文起幅的本书封面层（2026-10-07 用户「增加本书的封面图……跟水波效果一同出现」）────────
// 判决本体在 scripts/check-book-cover-overlay.py（交付门禁第 14 道），这里守的是**接线**和
// 那条由用户选定的停留时长，因为两个失败面都不在 JSON 里：
//   ① 「lib 里有层 ≠ 成片里有层」是本仓库反复出现的缺陷类（第八/十二/十三道门各治过一次）。
//      渲染器必须 import lib/book_cover、真的把 TMP/book-cover.png 叠进正文段，并在渲那一帧的
//      现场写下 book-cover.json 证人；复用 body.mp4 那条捷径必须认**两份**凭证（声明 + 封面），
//      否则 REUSE_BODY 命中一个「这层还不存在时」渲出来的 mp4，第 14 道门量到的是上一代像素。
//   ② 停留多久不是布局偏好：用户在「只跟水波那 1.4 秒 / 跟着桥接卡 / 常驻整段」里选了第一个。
//      跟着桥接卡 = 每期一个时长（桥接卡多长是本期切镜表的事）并向常驻漂移；常驻整段 = 把一张
//      300px 竖卡摆进字幕取样带（y 680..850）。两种都在 JSON 的**键值**里，所以这里钉键值，
//      而不是只钉文件存在——文件在、值被改回被否决的那一版，是本节唯一有意义的形状。
for (const rel of ["scripts/lib/book-cover.json", "scripts/lib/book_cover.py",
  "scripts/check-book-cover-overlay.py", "scripts/fetch-book-cover.py",
  "scripts/tests/test-book-cover-overlay.py"]) {
  if (!fs.existsSync(path.join(ROOT, rel))) {
    throw new Error(`${rel} 不见了 —— 本书封面层就只剩文档里的一句话`);
  }
}
{
  const coverLib = JSON.parse(
    fs.readFileSync(path.join(ROOT, "scripts", "lib", "book-cover.json"), "utf8"));
  const ct = coverLib.timing || {};
  for (const [key, want] of [["fade_in_from", "water_offset"],
    ["fade_out_from", "ripple_end"], ["fade_out_span", "scene_xfade"]]) {
    if (ct[key] !== want) {
      throw new Error(`book-cover.json 的 timing.${key} 现在是 ${JSON.stringify(ct[key])}，应为 "${want}"：`
        + "封面的停留时长是用户 2026-10-07 在三个选项里选定的（「只跟水波那 1.4 秒」），"
        + "改它需要新的授权，而不是把某个秒数写死回渲染器");
    }
  }
  // 被否决的那一版必须不在**取值**里（写在注释里不算：注释说的是历史，取值才是判决）。
  if (Object.values(ct).some((v) => typeof v === "string" && v.includes("bridge_exit"))) {
    throw new Error("book-cover.json 的时序取值又指向桥接卡退场（bridge_exit）：那是用户在三个选项里"
      + "否决掉的那一版——桥接卡长度是本期切镜表的事，跟着它就等于向「常驻整段」漂移");
  }
  if (coverLib.segment !== "body") {
    throw new Error(`book-cover.json 的 segment 是 ${JSON.stringify(coverLib.segment)}，只能是 "body"：`
      + "封面属于正文段起幅，钩子段带它就是把书目卡片搬进第一屏（同 check-hook-no-bookmark 的理由）");
  }
  // 带位对账：lib 记的「卡框不许进入」是**别人**的常量，不是这里的偏好。字幕取样带那两个
  // 数字已经被上面常驻声明那一段从 qc-caption-alignment.py 里解析出来（同一个常量，两处都要对上）。
  const capBand = (coverLib.reserved_bands || {}).caption_sweep;
  if (!capBand) {
    throw new Error("book-cover.json 缺 reserved_bands.caption_sweep：封面卡框的位置不再是"
      + "「避开字幕取样区」推导出来的");
  }
  if (Number(bandMatch[1]) !== Number(capBand.y0) || Number(bandMatch[2]) !== Number(capBand.y1)) {
    throw new Error(`字幕边界压测的取样带现在是 y ${bandMatch[1]}..${bandMatch[2]}，`
      + `而 book-cover.json 记的是 y ${capBand.y0}..${capBand.y1}：两边必须一起改，`
      + "否则 load() 里那条「卡框不许进入保留带」的断言量的是过期的地盘");
  }
  const slot = coverLib.slot || {};
  if (slot.y1_px > capBand.y0 && slot.y0_px < capBand.y1) {
    throw new Error(`封面卡框 y ${slot.y0_px}..${slot.y1_px} 与字幕取样带 y ${capBand.y0}..${capBand.y1} 相交：`
      + "lib 的 load() 会在渲染现场当场抛错，这一期渲不出来——先把卡位挪开再跑");
  }
  // ── 出场动效（2026-10-08 用户两次要求：先「增加放大的动态效果」，同日再「放大过程不流畅，
  //    抖动得厉害，可以参考氛围图的动态效果，通过增强本书封面图的动态节奏速度来达到效果」）。
  //    四条各对应一个**被实测否决过的形状**，数值全在 motion._comment 里：
  //      · 整数取整装置（resize 到 int(round(W*s)) + 整数偏移贴回）＝ 41 步里 24 步方向回退，
  //        净位移 17.4px 而路径 30.2px —— 那就是用户报的「抖」；
  //      · zoom_to=1.12 ＝ 卡体顶沿推到 y206（书名层下沿 225）、底沿 y686（字幕带上沿 680）。
  //        旧结论「卡框与三条带互不相交」是 zoom=1.0 的算术，静态 slot 检查（上面那段）看不见
  //        缩放后的足迹，所以这里必须钉住终态倍率；
  //      · 1.00→1.12 只有 12.96px/s，比本期氛围镜头最快那一镜（14.25px/s）还慢 —— 用户要的
  //        「增强节奏速度」不是一个形容词，是一个下限；
  //      · ease 必须与氛围镜头同款（cosine），线性那版在 30fps 短窗里读作机械推。
  //    lib 的 motion() 会在渲染现场拒掉这四条，这里守的是「没人跑渲染也要红」。
  const covMotion = coverLib.motion || {};
  const motionChecks = [["device", "float_affine_per_frame"], ["ease", "cosine"]];
  for (const [key, want] of motionChecks) {
    if (covMotion[key] !== want) {
      throw new Error(`book-cover.json 的 motion.${key} 现在是 ${JSON.stringify(covMotion[key])}，`
        + `应为 "${want}"：这是 2026-10-08「放大过程抖动得厉害」那次定的装置——`
        + "整数取整把卡片中心量化到整像素（实测 24/41 步回退），逐帧浮点仿射才是正文氛围镜头"
        + "一直在用的那条矩阵。要换装置得连 scripts/lib/book_cover.py 的 zoomed_overlay 一起换");
    }
  }
  if (Number(covMotion.zoom_to) !== 1.0) {
    throw new Error(`book-cover.json 的 motion.zoom_to 现在是 ${covMotion.zoom_to}，只能是 1.0：`
      + "缩放终态就是被三条保留带校过的 slot。1.12 那一版把卡体推进书名层 19px、字幕取样带 6px，"
      + "而上面的静态 slot 检查和 lib 当年的断言都只量静态 slot——要放大请把起幅做小（0.82→1.00 "
      + "长进标定卡框），别从标定卡框再往外放大");
  }
  if (!(Number(covMotion.zoom_from) >= 0.7) || !(Number(covMotion.zoom_to) > Number(covMotion.zoom_from))) {
    throw new Error(`book-cover.json 的 motion 行程现在是 ${covMotion.zoom_from}→${covMotion.zoom_to}：`
      + "起幅下限 0.7（再小就是甩镜头不是卡片长到卡框），且必须真的在放大");
  }
  if (!(Number(covMotion.pace_min_px_per_second) > 0)) {
    throw new Error("book-cover.json 的 motion.pace_min_px_per_second 缺失或为 0："
      + "「比身后正在推的氛围图还慢」就读成「一张静止的卡片在抖」，这条下限是用户"
      + "「增强动态节奏速度」那句要求的机器写法，把它清掉等于把要求收回）");
  }
  {
    const covLibPy = codeOnly("scripts/lib/book_cover.py");
    if (!covLibPy.includes("Image.AFFINE") || !covLibPy.includes("Image.BICUBIC")) {
      throw new Error("lib/book_cover.py 的 zoomed_overlay 不再是 AFFINE+BICUBIC："
        + "回到整数 resize/贴回就是回到实测 24/41 步回退的那一版（用户 2026-10-08 明确否掉）");
    }
    if (/^\s*bw, bh = max\(1, int\(round/m.test(covLibPy)) {
      throw new Error("lib/book_cover.py 里又出现了整数尺寸 + 整数偏移贴回的缩放："
        + "封面放大必须由逐帧浮点仿射画，整数量化就是「抖动得厉害」的那条来源"
        + "（只有测试的对照组允许画它，而测试在 scripts/tests/ 不在 lib）");
    }
    if (!covLibPy.includes("motion_envelope(cfg, (w, h))")) {
      throw new Error("lib/book_cover.py 的保留带断言不再量 motion_envelope()："
        + "退回只量静态 slot，zoom_to 被改回 1.12 时 import 期就看不见越带（19px/6px 那次的成因）");
    }
    for (const token of ["_ramp_end_seconds", "pace_min_px_per_second"]) {
      if (!covLibPy.includes(token)) {
        throw new Error(`lib/book_cover.py 不再引用 ${token}：动效期长度与速度下限就变成`
          + "第二份手写的秒数（时序与节奏都必须由水波常量推导，同 timing 的理由）");
      }
    }
    const covSeq = covLibPy.includes("def cover_frame_sequence");
    if (!covSeq) {
      throw new Error("lib/book_cover.py 里 cover_frame_sequence 不见了：ffmpeg 的 scale 滤镜在 "
        + "init eval_mode 下拒绝 t/n 表达式，逐帧序列是「封面随时间放大」唯一的落屏路径");
    }
    const covWaterGate = codeOnly("scripts/check-water-ripple.py");
    if (!(covWaterGate.includes("zoom_at") && covWaterGate.includes("zoomed_overlay"))) {
      throw new Error("check-water-ripple.py 不再按 lib 的 zoom_at/zoomed_overlay 逐帧排除封面足迹："
        + "换成静态框就会重新把「封面的逐帧差」当成涟漪来量（质心被拽回去那道红的成因）");
    }
  }
  const covRenderer = codeOnly("scripts/render_scene_map_template_fallback.py");
  for (const token of ["_cover_overlay(", "_cover_timing(", "_cover_card_box("]) {
    if (!covRenderer.includes(token)) {
      throw new Error(`render_scene_map_template_fallback.py 不再调用 ${token}：`
        + "卡框/投影/时序会变成渲染器自己算的第二份真源，而门禁调的是 lib 那一份"
        + "（两份各算一次，改了 lib 的卡位就只会在一边生效）");
    }
  }
  // 守的是「这条流被加进来了、而且真的叠进了正文段」这件事，不是某一个字符串。
  // 输入的**形状** 2026-10-08 换过一次：静态单张 PNG（`"-i", cover_png`）→ 逐帧 PNG 序列，
  // 因为 ffmpeg 的 scale 滤镜在 init eval_mode 下拒绝 t/n 表达式，「随时间放大」只能由 lib
  // 逐帧画好再当流喂进来（只认旧字符串 = 下一次合法的装置改动变成一条红；
  // 只认「出现过 cover_png」= 等于不守，一层盘上的图照样能骗过第 14 道门的残差）。
  if (!/_layers\s*\+=\s*\[[^\]]*cover-seq[^\]]*\]/.test(covRenderer)) {
    throw new Error("渲染器画出了 book-cover.png 却没把逐帧封面序列作为输入加进正文段："
      + "第 14 道门的「残差墨迹」无从长出来，这一层就只是 tmp 里一堆 PNG。"
      + "合法形状是 _layers += [... \"-i\", TMP/cover-seq/f_%04d.png]（配合 lib 的 cover_frame_sequence）；"
      + "退回单张 PNG + fade 滤镜等于把「出现时放大」整个删掉");
  }
  if (!/\[cov\]overlay/.test(covRenderer)) {
    throw new Error("渲染器把封面序列喂了进来却没有 [cov]overlay 这一环："
      + "输入没被映射到输出链上，成片像素里就没有这层，而 body.mp4.book-cover-sha 照样会盖上"
      + "本次算出的 sha —— 凭证自述的是一个「打算叠」的动作，只有 filter 链能证明它发生了");
  }
  if (!covRenderer.includes('os.path.join(TMP, "book-cover.json")')) {
    throw new Error("渲染器不再写 book-cover.json 证人：门禁第 14 道只能拿**当前** lib 重算时刻，"
      + "「lib 改了而成片还是旧形状」这个缺口就回来了（同 title-meta.json 的理由）");
  }
  // 两份凭证都要「读」和「写」都出现：只写不读等于每次重渲都盖个新戳，复用条件形同虚设。
  for (const cred of [".disclaimer-sha", ".book-cover-sha"]) {
    if (covRenderer.split(cred).length - 1 < 2) {
      throw new Error(`body.mp4 的叠层凭证 ${cred} 在渲染器里出现不足两次：`
        + "写凭证而不读它，REUSE_BODY 就会复用一个没有这一层（或上一版图）的正文段");
    }
  }
  // 封面那份凭证比的必须是**含动效**的合成指纹 `_cover_cred`，不能退回静态叠层字节 `_cover_sha`。
  // 2026-10-08 二次要求「放大过程不流畅，抖动得厉害」改的就是逐帧缩放轨迹（整数取整 → 浮点仿射、
  // 1.00→1.12 → 0.82→1.00），而 `overlay()` 那张静态图一个字节都没变：只比 `_cover_sha` 的话
  // REUSE_BODY 会把上一版抖动的像素判成「层对得上」复用回来，一路绿到门禁 14 才红，
  // 而那已经是「重渲一次」的成本。算／读／写三头缺一头，这条守卫就形同虚设。
  const credWiring = [
    { re: /_cov_prev\s*!=\s*_cover_cred/, which: "读（复用前的比对）",
      revert: (s) => s.replace(/_cov_prev\s*!=\s*_cover_cred/, "_cov_prev != _cover_sha") },
    { re: /_f\.write\(_cover_cred\)/, which: "写（合成后盖戳）",
      revert: (s) => s.replace(/_f\.write\(_cover_cred\)/, "_f.write(_cover_sha)") },
    { re: /_cover_cred\s*=\s*hashlib\.sha256/, which: "算（叠层字节+动效+时刻）",
      revert: (s) => s.replace(/_cover_cred\s*=\s*hashlib\.sha256/, "_cover_cred_v = 1 + hashlib.sha256") },
  ];
  for (const { re, which } of credWiring) {
    if (!re.test(covRenderer)) {
      throw new Error(`渲染器的封面复用凭证少了${which}这一步（正则 ${re} 不再命中）：`
        + "`_cover_sha` 是静态叠层字节的 sha，动效改动对它不可见，REUSE_BODY 就会复用上一版"
        + "抖动的像素（2026-10-08 二次要求「放大过程不流畅」改的正是逐帧缩放轨迹，静态叠层"
        + "一个字节都没变）。算／读／写三头必须都走 `_cover_cred`");
    }
  }
  // 自测：一次只退回**一个**环节，断言恰好那一头的正则失配、其余两头仍然命中。
  // 三头的谓词都含 `_cover_cred` 字面量，所以「全局改名」必然三头皆红——那是同义反复，
  // 量不出任何东西。逐环节退回才证明每条谓词钉在自己的位置上，而不是蹭到别处的文本。
  for (const { re, which, revert } of credWiring) {
    const broken = revert(covRenderer);
    if (re.test(broken)) {
      throw new Error(`封面凭证守卫空转：把${which}退回静态叠层字节后，它的谓词仍然命中，`
        + "说明这条断言钉的不是这个环节");
    }
    for (const { re: otherRe, which: otherWhich } of credWiring) {
      if (otherRe.source === re.source) continue;   // 按**图案**跳过自己，不是按对象身份
      if (!otherRe.test(broken)) {
        throw new Error(`封面凭证守卫互相纠缠：只退回${which}之后，${otherWhich}那条也失配了 —— `
          + "三头里有一条其实没钉在自己的位置上，报错会把手指出向错误的一边");
      }
    }
  }
  {
    const digestPy = codeOnly("scripts/composition-digest.py");
    for (const rel of ["scripts/lib/book-cover.json", "scripts/lib/book_cover.py"]) {
      if (!digestPy.includes(`"${rel}"`)) {
        throw new Error(`composition-digest.py 的生成器指纹不再盯 ${rel}：`
          + "账本会对着改过动效的 lib 说「生成器一致」，而盘上还是上一版的缩放轨迹"
          + "（同 title-font.json 进 watched 的理由——lib 改了 ≠ 像素改了）");
      }
    }
  }
  const covGate = codeOnly("scripts/check-book-cover-overlay.py");
  if (!covGate.includes("overlay_sha256")) {
    throw new Error("check-book-cover-overlay.py 不再核对证人的 overlay_sha256："
      + "叠层的 alpha 形状对任何一张封面都成立，IoU 满分也认不出「换了一本书的封面」");
  }
  if (!/reds\.append\(\s*f?"封面图溯源不通过/.test(covGate)) {
    throw new Error("check-book-cover-overlay.py 的溯源不合格不再进 reds："
      + "手工把 jpg 拖进期目录、换图不重写旁证的成片，像素照样能对账成功，那时唯一能说话的只有旁证");
  }
  if (!covGate.includes("B.timing(")) {
    throw new Error("check-book-cover-overlay.py 不再用 lib 推导时刻："
      + "门禁自己抄一份秒数，就和渲染器那份在水波偏移改动后分叉（同 water_ripple 的理由）");
  }
  // 证 D（2026-10-08 用户第二次要求「放大过程不流畅，抖动得厉害」的成片侧判决）。
  // 这条仪器量的是**逐帧反解的缩放系数 ŝ**：lib 的 motion() 断言守配置、
  // test-cover-motion-smoothness.py 守 lib 自己画的帧，两者都答不了「这一版成片烘的是哪条轨迹」，
  // 而「lib 改了 ≠ 像素改了」正是本仓库付账最多的那一类。这里守的是这把尺本身不许被换掉、
  // 也不许被调松。
  for (const [sym, why] of [
    ["def fit_cover_scale(", "反解 ŝ 的那把尺整段消失，证 D 就退回「看不见轨迹" ],
    ["def cover_patch(", "预测裁片消失＝拟合没有可比对象，ŝ 成了自说自话" ],
    ["def core_rect_at(", "按 ŝ 反解卡芯矩形消失＝越带那一判退回静态 slot（1.00→1.12 顶进书名层 19px 时全库全绿的那条盲区）" ],
    ["fit_cover_scale(fr, card_pil, box)", "证 D 不再逐帧调用拟合（只在几离散点拟合＝抽不到回退）" ],
    ["本书封面的动效轨迹与 lib 不符", "轨迹不符不再判红（用户那句「抖动得厉害」在成片里没人持有）" ],
    ["没在成片里长开", "「静止卡冒充动效」不再判红" ],
    ["越出了 slot 允许的那条带", "成片真实足迹压进保留带不再判红" ],
    ["残差沿（只报告，不判决）", "墨迹沿那一列被重新扶成判决——它的噪声底子在静止卡上就有 1px 级，判「抖」和判「不抖」都会错" ],
    ["顶在搜索网格端点", "失盲（纯色装帧／那块像素不是这张封面）不再打印「无从判定」，而是拿一个假 ŝ 印出假红或假绿" ],
    // 「形状对得上、内容对不上」有两种相反的修法，红话必须分家；「4 点里有 2 点红」也有两种
    // 相反的落点（砍掉后半 vs 淡入缩放不对）。两处分岔都靠 fit_cover_scale 这一个读数，
    // 话术一旦被删，手指就指向错误的那一边——重渲只会把同一张错图再搬上来一次。
    ["最优仍能贴到平均差", "证 A 不再用同一把尺分岔「上一版动效形状」与「叠错了图／换了代」："
      + "换图书被派去重渲，而重渲恰恰修不了它" ],
    ["读下面证 D 的逐帧 ŝ 轨迹", "平台期「一半对上一半对不上」不再按红点落在哪一半分岔："
      + "静止卡（红在淡入）与砍掉后半（红在末尾）会说成同一句话" ],
  ]) {
    if (!covGate.includes(sym)) {
      throw new Error(`check-book-cover-overlay.py 的证 D 少了「${sym}」：${why}）`);
    }
  }
  // 被否决的旧仪器不许以任何形式回来：它靠「两条沿的回退处数」判抖。
  if (/SMOOTH_REVERSAL_MAX|SMOOTH_FLOOR_PX|OVER_ENV_TOL|rev_l/.test(covGate)) {
    throw new Error("check-book-cover-overlay.py 里又出现了墨迹沿判据的常数（SMOOTH_*/OVER_ENV_TOL/rev_l）："
      + "那把尺实测在**静止卡**上就有 4/1、0/6、4/2 处「回退」（《围城》《呐喊》《乞力马扎罗的雪》），"
      + "和被否决的整数取整装置同量级；亚像素流畅度归 lib 的 device 断言 + "
      + "test-cover-motion-smoothness.py，成片这一侧只认 ŝ 轨迹");
  }
  // 三条判线是按二十期普查定的，所以钉住数值；更要紧的是**自测提取器**——
  // 一个永远提取不到东西的正则，钉不住任何东西，只是把「有钉线」这件事写在屏幕上。
  const fitPins = {
    FIT_MEAN_TOL_PX: 6.0,    // 健康 1.31..1.34 ／静止 17.33..18.18 ／整数取整 39.2..40.7
    FIT_PEAK_TOL_PX: 12.0,   // 健康最坏一帧 2.87（细网格 5×／8× 不降）／静止 39.94..41.89
    FIT_MAX_ERR: 40.0,       // 能反解的成片谷高 ≤14.25／跨书负对照 81.6..109.7
    FIT_TRAVEL_MIN: 0.5,     // 健康 99.5%／静止 0%／整数取整 48.9..72.4%
    FIT_BAND_TOL_PX: 3.0,    // 健康足迹留在 slot 内；1.00→1.12 顶进书名层 15.7..19.4px
  };
  const readPin = (src, name) => {
    const m = src.match(new RegExp(`^${name} = ([0-9.]+)`, "m"));
    return m ? parseFloat(m[1]) : NaN;
  };
  for (const [name, val] of Object.entries(fitPins)) {
    if (!(readPin(covGate, name) === val)) {
      throw new Error(`check-book-cover-overlay.py 的 ${name} 不再是 ${val}（现在是 `
        + `${readPin(covGate, name)}）：这几条线是 2026-10-08 二十期普查量出来的，两头各留 2.6–4.5 倍余量；`
        + "放宽它就是「靠调判线过门禁」，唯一的修法是按红话重渲本期");
    }
  }
  {
    const decoy = covGate.replace(/^FIT_MEAN_TOL_PX = 6.0/m, "FIT_MEAN_TOL_PX = 0.5");
    if (!(readPin(decoy, "FIT_MEAN_TOL_PX") === 0.5)) {
      throw new Error("钉 fit_cover_scale 判线的正则抓不到东西：把 6.0 换成 0.5 它照样不报红，"
        + "说明这条钉线是 theatre，不是控制");
    }
  }
  if (!/本书封面上屏/.test(deliveryWrapper)) {
    throw new Error("check-episode-delivery.py 的 GATE_NAMES 少了「本书封面上屏」："
      + "汇总行会少一道，而打印的「N 道交付门禁全部通过」就成了错数");
  }
  if (!/"--body-at", str\(pre_body\)/.test(deliveryWrapper)) {
    throw new Error("check-episode-delivery.py 不再把正文起点喂给第 14 道门："
      + "封面时刻是相对「固定开场白结束」算的，取样点整体偏移就会把桥接卡读成正文");
  }
  // 门禁要有可执行的真源，也要有说清「为什么有这道门」的文档（沿用每道门写进两份文档的口径）。
  if (!ruleSays("check-book-cover-overlay.py")) {
    throw new Error("规则文档没有写出 check-book-cover-overlay.py 这条验收命令：封面层规则无法被执行");
  }
  if (!ruleSays("book-cover.json")) {
    throw new Error("规则文档没有点名 scripts/lib/book-cover.json：卡框/投影/时序的真源在文档里失踪，"
      + "下一个人只会去渲染器里改坐标或秒数");
  }
}

// 全仓字幕轨审计：每一期的上屏文本都要真的被零标点规则查过一遍。历史两期先豁免——
// 《一个陌生女人的来信》字幕轨是老渲染器出的（无 display），《围城》是 /tmp/围城_tpl 里
// 的陈旧 cap-*.png 计数不符（画面无违规）。**新增期一律判红**，别再让它们靠"没人跑审计"过关。
const PUNCT_EPISODE_BASELINE = new Set(["一个陌生女人的来信", "围城"]);
const punctGate = run("python3", ["scripts/check-caption-punctuation.py", "--all"]);
if (punctGate.status !== 0) {
  const lines = `${punctGate.stdout || ""}${punctGate.stderr || ""}`.split("\n");
  const offenders = lines.filter((l) => /^\s*❌/.test(l))
    .map((l) => l.replace(/^\s*❌\s*/, "").trim().split(/\s+/)[0])
    .filter((name) => name && !PUNCT_EPISODE_BASELINE.has(name));
  if (offenders.length) {
    throw new Error(`上屏标点审计未通过：${offenders.join(", ")}`
      + `（历史豁免名单：${[...PUNCT_EPISODE_BASELINE].join("、")}）\n`
      + lines.filter((l) => /^\s*(❌|·|\[audit\])/.test(l)).join("\n"));
  }
}

// 背景图溯源门禁：规则本体见 AGENTS.md「Visual And Audio Rules」——背景图来自 Pexels 实拍，
// 每张上屏的图都要能追到 photo id，且不得低分辨率上采样、不得跳过统一调色。
// 历史剧集只告警（旧口径的图不回改），声明 image_pipeline=pexels-1024 的新剧集才红灯。
// 全库这一遍判的对象是**已经上屏的帧**。另一会话正在跑的在制品（目录建了、图取到一半、
// 还没调色、prompts.csv 还没写）会把整库检查判红，而它此刻没有任何交付帧可判——它自己的
// 单期门禁在锁定和交付时各跑一次，那两道一道都不会少（2026-10-05 实测：《十日谈》13:42 建目录
// 13:44 取完图，本仓库检查当场红在「没有 _raw/ 备份」上，与当时正在落的改动毫无关系）。
// 所以红的时候按「有没有 renders/<书名>-final.mp4」逐期重跑一遍：**已交付那批仍然必须全绿**，
// 少一期绿都不放行；没交付的列成 ⚠️ 在建，看得见、不算过。
const imageGate = run("python3", ["scripts/check-image-provenance.py", "--all"]);
if (imageGate.status !== 0) {
  const episodesDir = path.join(ROOT, "episodes");
  const delivered = fs.readdirSync(episodesDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .filter((entry) => {
      const renders = path.join(episodesDir, entry.name, "renders");
      return fs.existsSync(renders)
        && fs.readdirSync(renders).some((f) => f.endsWith("-final.mp4"));
    })
    .map((entry) => entry.name);
  const deliveredGate = delivered.length
    ? run("python3", ["scripts/check-image-provenance.py", ...delivered])
    : { status: 0, stdout: "", stderr: "" };
  if (deliveredGate.status !== 0) {
    throw new Error(`背景图门禁未通过（已交付的 ${delivered.length} 期）：\n`
      + `${deliveredGate.stdout || ""}${deliveredGate.stderr || ""}`);
  }
  const inflight = (imageGate.stdout || "").split("\n").filter((line) => /❌/.test(line));
  console.log(`⚠️ 背景图溯源：全库审计红在在制品上，已交付的 ${delivered.length} 期逐期复查全部绿。`
    + `未交付的期没有上屏帧，锁定与交付时各还有一道单期门禁。红行：\n  ${inflight.join("\n  ")}`);
}

// 钩子图跨期去重：一条规则在 2026-10-03 之前是「写在 AGENTS.md 里、靠一个批次脚本自觉执行」——
// 跨**期**重复被明确划在单期门禁之外，因为一张照片在 A 期的正文和 B 期的钩子里各出现一次，
// 任何只看单期的检查都看不见。113 期回补把这件事变成了常态（每期两个钩子拍 × 一百多期共用
// 同一个素材库），11 张钩子图撞了别期正文，八道门禁全绿。现在判据只有一个真源
// scripts/lib/pexels_index.py（photo id → 期·段·文件），检索、锁定、交付门禁三处共用。
// 这里静态盯的是「三处还都接着」：任何一处改回自己算一份，跨期红线就又只剩文档了。
const pexelsIndexFile = "scripts/lib/pexels_index.py";
if (!fs.existsSync(path.join(ROOT, pexelsIndexFile))) {
  throw new Error(`${pexelsIndexFile} 不存在：钩子图跨期去重的唯一判据被删掉了`);
}
const pexelsIndexSource = fs.readFileSync(path.join(ROOT, pexelsIndexFile), "utf8");
for (const name of ["build", "foreign_uses", "hook_collisions"]) {
  if (!new RegExp(`^def ${name}\\(`, "m").test(pexelsIndexSource)) {
    throw new Error(`${pexelsIndexFile} 不再有 ${name}()：跨期去重判据被挪走，调用方会静默失去保护`);
  }
}
const indexDefiners = listCodeFiles("scripts").filter((file) =>
  /^\s*def foreign_uses\(/m.test(fs.readFileSync(path.join(ROOT, file), "utf8")),
);
if (indexDefiners.length !== 1 || indexDefiners[0] !== pexelsIndexFile) {
  throw new Error(`foreign_uses() 只能在 ${pexelsIndexFile} 定义一次，实际找到：${indexDefiners.join(", ")}`
    + "（两份判据一漂移，检索说能用、门禁说不能用，等于没有判据）");
}
// 检索阶段丢掉已用 id（省成本）、两个 lock 脚本拒绝锁（还没取图就拦下）、交付门禁红灯
// （已经上屏才拦下）——三道防线缺一道，就退回到「靠人记得」。
for (const [file, tokens] of [
  ["scripts/pexels_candidates.py", ["pexels_index", "--exclude-used", "_pix.build("]],
  ["scripts/lock-pexels-candidates.py", ["pexels_index", "foreign_uses",
    // 正文选型也要查跨期，但只查「别期的钩子」这一侧：红线钉在钩子段（观众换期该看见一个新地方），
    // 所以入侵方向是正文→别期钩子。《猎人笔记》2026-10-06 锁了《女儿红》钩子用过的 #13438765 进正文，
    // 本期十二道门全绿，红的是 npm run check 扫到《女儿红》那一行——已交付的期被新的一期判红。
    'if x[1] == "hook"']],
  ["scripts/lock-pexels-selection.py", ["pexels_index", "foreign_uses"]],
  ["scripts/check-image-provenance.py", ["pexels_index", "foreign_uses"]],
]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const token of tokens) {
    if (!source.includes(token)) {
      throw new Error(`${file} 不再引用 ${token}：钩子图跨期去重的一道防线被静默拆掉`
        + "（同一张照片在别的期已经上过屏，观众换期时看到的「新地点」其实是旧帧）");
    }
  }
}
if (!ruleSays("pexels_index.py")) {
    throw new Error("规则文档没有写出 pexels_index.py：跨期去重规则无法被执行，只能退回「靠批次脚本自觉」");
  }

// 持久否决名单（data/pexels-rejected.json）：`--exclude-used` 只认「已经上过屏」，
// 而目检否决的图恰恰从未上过屏，于是同一批废图每一批都会重新端上来——2026-10-03 第四批里
// 大理稻田亲子旅拍那 6 帧出现在 15 个候选批次、中式村委长廊 #39854273 出现在 18 个批次，
// Pexels 的 alt 是 "animals, Beautiful scenery, beautiful woman" 这种乱标签，任何氛围词都能召回。
// 人工否决不落文件就只是上一个会话的记忆，所以这里静态盯四件事。
const pexelsRejectFile = "scripts/lib/pexels_reject.py";
if (!fs.existsSync(path.join(ROOT, pexelsRejectFile))) {
  throw new Error(`${pexelsRejectFile} 不存在：目检否决的名单又变回会话记忆`);
}
const pexelsRejectSource = fs.readFileSync(path.join(ROOT, pexelsRejectFile), "utf8");
for (const token of ["def rejected_ids(", "def describe(", "REJECT_FILE", '"..", ".."']) {
  if (!pexelsRejectSource.includes(token)) {
    throw new Error(`${pexelsRejectFile} 不再包含 ${token}：名单的唯一读入口被改坏。`
      + "退一级 ROOT 会算出 scripts/data/pexels-rejected.json——读的时候名单看着是空的，"
      + "写的时候在 scripts/ 下凭空长出第二份名单，两份名单比没有名单更糟");
  }
}
const rejectDefiners = listCodeFiles("scripts").filter((file) =>
  /^\s*def rejected_ids\(/m.test(fs.readFileSync(path.join(ROOT, file), "utf8")));
if (rejectDefiners.length !== 1 || rejectDefiners[0] !== pexelsRejectFile) {
  throw new Error(`rejected_ids() 只能在 ${pexelsRejectFile} 定义一次，实际找到：${rejectDefiners.join(", ")}`);
}
// 检索默认丢掉、两个 lock 脚本拒绝锁、写入侧拒绝把已投放的图拉黑——三道防线缺一道就退回靠人记得。
for (const [file, tokens] of [
  ["scripts/pexels_candidates.py", ["pexels_reject", "rejected_ids()", "--ignore-rejected", "net_fails", "query_failed"]],
  ["scripts/lock-pexels-candidates.py", ["pexels_reject", "reject_list()", "--allow-rejected", "not in by_id"]],
  ["scripts/lock-pexels-selection.py", ["pexels_reject", "reject_list()", "already_locked()"]],
  ["scripts/reject-pexels-photo.py", ["pexels_index", "--allow-shipped", "GLOBAL_DEFECTS"]],
]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const token of tokens) {
    if (!source.includes(token)) {
      throw new Error(`${file} 不再引用 ${token}：持久否决名单的一道防线被静默拆掉`
        + "（下一批又要在全尺寸目检里把同一张人脸照片再看一遍）");
    }
  }
}
// 名单本身也要能被证伪：只收「对任何一期都不成立」的三类，且绝不能收到已经上过屏的 id。
if (fs.existsSync(path.join(ROOT, "data", "pexels-rejected.json"))) {
  const rejected = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "pexels-rejected.json"), "utf8"));
  const defectWords = ["人脸", "多人", "游客", "行人", "招牌", "文字", "标识", "标牌", "指示牌",
    "摆拍", "道具", "棚拍", "静物", "时尚", "LED", "监控", "摄像头", "标签", "乱标签", "塑钢", "现代"];
  const shippedOut = run("python3", ["-c",
    "import sys;sys.path.insert(0,'scripts');from lib import pexels_index as pi;"
    + "print('\\n'.join(str(k) for k in pi.build('.')))"], ROOT);
  const shippedIds = new Set(String(shippedOut.stdout || "").split("\n").filter(Boolean));
  for (const [pid, rec] of Object.entries(rejected.ids || {})) {
    const reason = typeof rec === "string" ? rec : (rec && rec.reason) || "";
    if (!defectWords.some((w) => reason.includes(w))) {
      throw new Error(`data/pexels-rejected.json #${pid} 的理由不像「对任何一期都不成立」：${reason.slice(0, 40)}`
        + "（某一期的地方/年代不对不该拉黑，那会让检索池越用越小）");
    }
    if (shippedIds.has(pid)) {
      throw new Error(`data/pexels-rejected.json #${pid} 已经上过屏却被拉黑：`
        + "撤一张已投放的图走重选+重渲，动名单等于宣布某期成片用了废图而无人能修");
    }
  }
}
if (!ruleSays("pexels-rejected.json")) {
    throw new Error("规则文档没有写出 pexels-rejected.json：持久否决规则只活在脚本注释里，改管线的人看不见它");
  }

// 背景图的「身份」链：清单声称的 Pexels 照片 → 取图当时写下的 _provenance.json
// → 磁盘上的 _raw/ 底图 → 调色交付的 atmosphere-N.png → 成片钩子窗里的那一帧。
// 静态盯三段，因为每一段都曾经单独绿过而整体是错的：
// ① 取图脚本把「同名文件已存在」当成取到了（ok += 1; continue），于是换图从未落盘；
// ② 门禁只比「清单 ↔ prompts.csv ↔ 文件存在」，三份记录一起指着一张错照片也照样通过
//   （《偏跟山过不去》2026-10-03：清单与 prompts 已换成 #38552827，盘上还是 #17941302，八门全绿）；
// ③ 钩子段渲染失败时磁盘上留的是上一版成片，门禁量的是「新图 + 旧片」（同一期，11:51 的成片）。
const cropLib = "scripts/lib/pexels_crop.py";
if (!fs.existsSync(path.join(ROOT, cropLib))) {
  throw new Error(`${cropLib} 不存在：3:4 裁切又会长回各写一遍，重放核对会与取图脚本比出假红`);
}
const cropDefiners = listCodeFiles("scripts").filter((file) =>
  /def crop_3x4\(/.test(fs.readFileSync(path.join(ROOT, file), "utf8")));
if (!cropDefiners.includes(cropLib)) {
  throw new Error(`${cropLib} 里找不到 crop_3x4()：裁切本体被搬走，verify-pexels-fetch.py 的重放就不是同一次裁切`);
}
for (const file of ["scripts/fetch-pexels-by-id.py", "scripts/verify-pexels-fetch.py"]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  if (!source.includes("pexels_crop")) {
    throw new Error(`${file} 不再 import pexels_crop：它自己那份裁切与另一份一漂移，`
      + "「盘上图 = 清单那张照片」就只剩近似说法");
  }
}
// 标记必须一边写、一边读，且读的那一侧要核 sha256，否则等于没有
for (const [file, tokens] of [
  ["scripts/fetch-pexels-by-id.py", ["_provenance.json", "def certify(", "raw_sha256", "certified_existing("]],
  ["scripts/check-image-provenance.py", ["_provenance.json", "def check_certification(", "raw_sha256"]],
  ["scripts/lock-pexels-candidates.py", ["replaces_photo_id"]],
  ["scripts/check-episode-delivery.py", ["check-hook-frame-match.py", "钩子图上屏核对"]],
]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const token of tokens) {
    if (!source.includes(token)) {
      throw new Error(`${file} 不再引用 ${token}：背景图身份链上的一道防线被静默拆掉`
        + "（换图可以不出现在盘上，旧成片可以顶着八个绿灯交付）");
    }
  }
}
// 「文件已存在」不得再无条件当成取到：跳过分支必须先过 certified_existing。
// 判据用 indexOf + 定长窗口，不用正则在分支尾巴上找下一个注释——那种写法把「跳过分支后面
// 恰好有一行特定注释」当成了结构约束， rearrange 一下代码就误报，而误报会让人以为防线还在。
{
  const source = fs.readFileSync(path.join(ROOT, "scripts/fetch-pexels-by-id.py"), "utf8");
  const at = source.indexOf("if os.path.exists(target)");
  const body = at < 0 ? "" : source.slice(at, at + 1400);
  if (!body.includes("certified_existing")) {
    throw new Error("fetch-pexels-by-id.py 的同名跳过分支不再走 certified_existing()："
      + "这就是《偏跟山过不去》那次「清单换了、盘上没换、门禁全绿」的入口");
  }
}
// Pexels 鉴权头的传法（2026-10-06）：密钥只许走 stdin 的 curl 配置（`-K -`），绝不进 argv。
// 两条独立的泄漏面，各自都够判红：① argv 全局可读——这台机器常年并行跑七八个会话和一堆后台
// 渲染，`ps -eww -o command` 同机任何进程都能读出「curl -H Authorization: <key> …」整条命令行；
// ② 报错会把 argv 再抄一份——`check=True` 的 CalledProcessError 带着 cmd 列表，任何打印整条命令
// 的日志/会话记录都带走一次密钥，而这条链路上 curl 抖动（exit 16/18）是常态。
// 改之前有七个形状是这个样子（六个 Python + Node 的 fetch-pexels-images-curl.mjs），其中
// lock-pexels-selection.py 的 docstring 还写着「密钥绝不进命令行参数」——注释与实现相反，
// 拿注释当防线就是这个仓库一路在防的那类空转。
// 真源：scripts/lib/pexels_curl.py（Python）、scripts/lib/pexels-auth.mjs（Node）；
// 行为与双向对照在 scripts/tests/test-pexels-key-argv.py（本地 HTTP 服务亲眼收到那个头，
// 再把改前的七个形状逐个塞进同一条度量证明度量不是空的）。
// 扫**去注释后**的源码：解释「以前这里是错的」的注释与 docstring 里引用旧形状是允许的，
// 拿原文扫就是把说明当缺陷，得到一个只能被删掉的守卫（与 retiredShapes 同一条理由）。
// scripts/tests/ 是唯一的豁免目录：那里的旧形状是**对照夹具**而不是会执行的调用，
// 而「测试目录不许出现密钥字样」只会把人推向把夹具改写得不像事故本身——所以这里改成
// 要求那份夹具表还在，而不是要求它不在。
const keyInArgv = /-H["']\s*,\s*f?["'`]Authorization:/;
const argvLeakers = listCodeFiles("scripts")
  .filter((file) => !file.startsWith(`scripts${path.sep}tests${path.sep}`))
  .filter((file) => keyInArgv.test(codeOnly(file)));
if (argvLeakers.length) {
  throw new Error(`这些脚本还把密钥拼进 curl 的 argv：${argvLeakers.join(", ")}\n`
    + "  → 改用 scripts/lib/pexels_curl.py（Python）或 scripts/lib/pexels-auth.mjs（Node）：\n"
    + "     `-K -` 让 curl 从 stdin 读 header，argv 里只剩选项和 URL。密钥同机 `ps -eww` 读得到");
}
// 守卫自证：被禁止的形状必须真能被这条正则抓住，否则上面的扫描是在测空气；
// 而两份 lib 的干净写法必须抓不住，否则豁免名单会一路长下去。
// 夹具是**拼**出来的而不是抄一份字面量：字面量会让 check.mjs 自己成为扫描的命中项
// （第一次跑就是这样红了），也让人分不清正则是认形状还是认那段复制粘贴。拼出来之后
// 源码里没有 `"-H",` 紧跟 `Authorization:` 的字面串，正则仍然命中组装出的形状——
// 这一次命中是真的命中，不是巧合。
{
  const q = '"';
  const withdrawnShape = `  cmd = ["curl", "--max-time", "120", ${q}-H${q}, f${q}Authorization: {key}${q}, url]`;
  if (!keyInArgv.test(withdrawnShape)) {
    throw new Error("密钥进 argv 的守卫对回滚前的形状不再命中：这道检查已经空转，"
      + "下一次「顺手把 key 拼回命令行」会照常通过 npm run check");
  }
  if (keyInArgv.test(codeOnly("scripts/check.mjs"))) {
    throw new Error("check.mjs 里出现了密钥进 argv 的字面形状：守卫的定义文件自己就是命中项时，"
      + "下一步一定是把这道扫描加豁免，而豁免一旦开了口子就再关不上");
  }
  for (const clean of [
    '  r = pexels_curl.run(["-sSL", "--max-time", "180"], url, key)',
    '  return spawnSync("curl", ["-K", "-", ...args], { ...options, input: authConfig(key) })',
    '  cmd += ["-H", f"{k}: {v}"]',
  ]) {
    if (keyInArgv.test(clean)) {
      throw new Error(`密钥进 argv 的守卫误伤了干净写法：${clean} —— 会被它判红的形状一旦多起来，`
        + "真正的泄漏就会混在豁免名单里");
    }
  }
}
for (const [file, tokens] of [
  ["scripts/lib/pexels_curl.py", ["-K", "def auth_config(", "def build(", "def run(", "def get_json("]],
  ["scripts/lib/pexels-auth.mjs", ["-K", "function authConfig(", "function spawnCurl("]],
  ["scripts/tests/test-pexels-key-argv.py", ["leaky_shapes", "received.get(\"authorization\")",
    "反向对照度量对干净 argv 判为不泄漏"]],
  ["scripts/fetch-pexels-by-id.py", ["import pexels_curl", "pexels_curl.run("]],
  ["scripts/fetch-pexels-images-curl.mjs", ["from \"./lib/pexels-auth.mjs\"", "spawnCurl("]],
]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const token of tokens) {
    if (!source.includes(token)) {
      throw new Error(`${file} 不再引用 ${token}：鉴权头传法的这道链被拆开，`
        + "密钥就有回去的路（同机 ps 读得到 curl 的 argv）");
    }
  }
}
// 钩子图上屏核对必须带推近阶梯：参考侧不做 scale 补偿，就等于拿未缩放的参考图去比
// 「中心被放大后的一块」，自我匹配随推近一路变差、与对手图的 MAD 差被压平，
// 换过图且已重渲的新成片会被这道门判成「没重渲」。《偏跟山过不去》2026-10-03 第一次跑
// 就是这样红的：3.6s 那帧原始差 4.5（近 tie 丢弃），带阶梯差 10.8（认出来了）。
{
  const source = fs.readFileSync(path.join(ROOT, "scripts/check-hook-frame-match.py"), "utf8");
  for (const token of ["REF_ZOOMS", "def band_variants(", "def best_mad(",
                       "band_variants(load_gray", "best_mad(fb, variants)"]) {
    if (!source.includes(token)) {
      throw new Error(`check-hook-frame-match.py 不再引用 ${token}：第 9 道门退回单张未缩放参考图，`
        + "推近动画会让它把新渲的成片误判成旧像素（《偏跟山过不去》2026-10-03 那次误红）");
    }
  }
}
if (!ruleSays("_provenance.json")) {
    throw new Error("规则文档没有写出 _provenance.json：背景图身份链只活在脚本里，改管线的人看不见它");
  }

// 封面折行回归：折行是排版规则不是某一期封面，只有跑遍全部书名才看得见回归。
// 规则本体在 make_episode_cover.wrap_title_lines()（中文禁则 + 标点锚点 + 出框），
// 这里静态盯两件事：① 那个唯一入口还在——被重新摊回画图代码，回归脚本就会去测一份
// 复制品，两份判据一漂移，绿灯就又是假的；② 全仓书名折行零违规。
const coverWrapFile = "scripts/check-cover-wrapping.py";
if (!fs.existsSync(path.join(ROOT, coverWrapFile))) {
  throw new Error(`${coverWrapFile} 不存在：封面折行的全量回归被删掉了`);
}
const coverSource = fs.readFileSync(path.join(ROOT, "scripts/make_episode_cover.py"), "utf8");
if (!coverSource.includes("def wrap_title_lines(")) {
  throw new Error("make_episode_cover.py 不再有 wrap_title_lines()：折行阶梯被摊回画图代码，"
    + "check-cover-wrapping.py 就在测一份会漂移的复制品");
}
const coverWrapGate = run("python3", [coverWrapFile]);
if (coverWrapGate.status !== 0) {
  throw new Error(`封面折行回归未通过：\n${coverWrapGate.stdout || ""}${coverWrapGate.stderr || ""}`);
}

// 封面书名这条裁决换了三次形状，两份文档必须把**三次**都写下来，否则下一个改封面的人
// 只能看到最终那两个常量，读成「不知道为什么」的阶梯，然后：
//   · 2026-10-07「书名不要有换行」被写成「单行优先」，留了一条 ONE_LINE_MIN 装不下就折行的
//     第②支 —— 把它「简化」回一到 132px 就折行，就是回到这一版；
//   · 2026-10-08「不要有换行」（绝对措辞）退役了那一支，装不下改成报错、让用户写 cover_title；
//   · 2026-10-09「根据合适排版进行缩小字号」否掉的正是 cover_title 这条路：封面印的必须是
//     正式书名，装不下就缩字号，96px 以下照出但必须留痕。
// 把 TITLE_LEGIBLE_MIN 滑到 60 让日志闭嘴，会把 2026-10-01 用户否掉的《恶意》96px
// 「字体模糊不清晰」重新装回来；把 TITLE_ABSOLUTE_MIN 往下调，书名就会比钩子行还小。
for (const token of ["ONE_LINE_MIN", "check-cover-wrapping.py",
                       // 2026-10-09 用户第三次就同一件事说：「根据合适排版进行缩小字号」。
                       // 原话进文档，下一个人才不会把它读成「改回折行」或「改封面用名」。
                       "根据合适排版进行缩小字号",
                       "TITLE_LEGIBLE_MIN", "TITLE_ABSOLUTE_MIN"]) {
  if (!ruleSays(token)) {
    throw new Error(`规则文档没有写出 ${token}：封面书名「一律单行 + 缩小字号」这条裁决无法被执行`);
  }
}

// 缩小字号合法化之后，这两个数就是整条裁决的全部重量：
//   TITLE_LEGIBLE_MIN 96 —— 2026-10-01《恶意》「字体模糊不清晰」那一版的实测字号，现在是
//     「必须逐期留痕的告警线」；把它滑到 60 = 让警告消失，等于把用户点名要的代价声明撤回。
//   TITLE_ABSOLUTE_MIN 60 —— 拒绝线，等于 HOOK_MIN：书名再小也不许比钩子行还小。
// 钉的是**值**，不是「这个常量还在」；而且每条钉子先证明自己能被被撤的形状推开，
// 否则又是一个永远为真的正则。
const legiblePin = /COVER_TITLE_LEGIBLE_MIN",\s*"96"/;
const absolutePin = /COVER_TITLE_ABSOLUTE_MIN",\s*"60"/;
for (const [pin, label, withdrawnPattern, replacement] of [
  [legiblePin, "TITLE_LEGIBLE_MIN=96", /COVER_TITLE_LEGIBLE_MIN",\s*"96"/, 'COVER_TITLE_LEGIBLE_MIN", "60"'],
  [absolutePin, "TITLE_ABSOLUTE_MIN=60", /COVER_TITLE_ABSOLUTE_MIN",\s*"60"/, 'COVER_TITLE_ABSOLUTE_MIN", "40"'],
]) {
  if (!pin.test(coverSource)) {
    throw new Error(`make_episode_cover.py 的 ${label} 钉子不见了：把清晰下限或绝对下限往下调，`
      + `就是在撤回 2026-10-09 裁决「根据合适排版进行缩小字号」里「不许折行、缩到能装下为止」那一半`);
  }
  const injected = coverSource.replace(withdrawnPattern, replacement);
  if (injected === coverSource) {
    throw new Error(`${label} 的被撤形状注入没落地：这条钉子没法被证伪，是空转`);
  }
  if (pin.test(injected)) {
    throw new Error(`${label} 在被撤形状下仍然匹配：这条钉子是空转`);
  }
}

// 交付门禁总入口：规则本体见 AGENTS.md「Production Gates」第 5 条。
// 水波同窗、字幕零标点、背景图 Pexels 溯源这三条各自都有门禁，但《围城》2026-09-26 首版
// 仍然三条全违地交付了——不是规则没写，是交付时没人强制挨个跑。收成一个入口
// scripts/check-episode-delivery.py 之后，这里静态盯三件事：① 入口还在；② 它真的调用了
// 那三道门（少一道就是给自己留后门）；③ AGENTS.md 与手册都写了这条命令，否则下一个人
// 依然不知道该跑什么。
const deliveryGateFile = "scripts/check-episode-delivery.py";
if (!fs.existsSync(path.join(ROOT, deliveryGateFile))) {
  throw new Error(`${deliveryGateFile} 不存在：交付门禁总入口被删掉了`);
}
const deliveryGateSource = fs.readFileSync(path.join(ROOT, deliveryGateFile), "utf8");
for (const gate of ["check-image-provenance.py", "check-caption-punctuation.py",
                    "check-water-ripple.py", "qc-caption-alignment.py"]) {
  if (!deliveryGateSource.includes(gate)) {
    throw new Error(`${deliveryGateFile} 不再调用 ${gate}：交付门禁被静默拆掉了一道`);
  }
}
if (!ruleSays(deliveryGateFile)) {
  throw new Error(`规则文档没有写出 ${deliveryGateFile} 这条交付命令：门禁无法被执行`);
}

// 钩子文案的两条「只有连着发才看得见」的门禁：第二行开头装置族过载、封面钩子与第二行
// 逐字复述。规则本体在 scripts/check-hook-openers.py，写盘入口是 promote-hook-draft.py。
// 静态盯三件事：① 那个唯一入口还在；② 提升脚本真的调它而不是内联一份（内联的那份会漂
// 移，而漂移的表现恰好是「看起来在检查」）；③ 已出货语料里不能再出现「未录可改」的复述——
// 音频已录的期是历史，冻结不判红，所以这里跑 --cover-echo 而不是 --plan。
const hookOpenerFile = "scripts/check-hook-openers.py";
if (!fs.existsSync(path.join(ROOT, hookOpenerFile))) {
  throw new Error(`${hookOpenerFile} 不存在：钩子第二行开头的语料级回归被删掉了`);
}
const promoteSource = fs.readFileSync(path.join(ROOT, "scripts/promote-hook-draft.py"), "utf8");
if (!promoteSource.includes("plan_problems(")) {
  throw new Error("promote-hook-draft.py 不再调用 check-hook-openers.plan_problems()："
    + "开头过载与封面复述会在写盘之后才被发现，或者根本没人发现");
}
if (!/opener_problems[\s\S]{0,400}sys\.exit\(1\)/.test(promoteSource)) {
  throw new Error("promote-hook-draft.py 的开头门禁不再拦写盘（找不到撞车→exit 1 那段）："
    + "门禁退化成打印");
}
const hookOpenerGate = run("python3", [hookOpenerFile, "--cover-echo"]);
if (hookOpenerGate.status !== 0) {
  throw new Error(`钩子文案回归未通过：\n${hookOpenerGate.stdout || ""}${hookOpenerGate.stderr || ""}`);
}

for (const [label, testFile] of [
  ["Title normalization", "scripts/tests/test-title-normalization.mjs"],
  ["Body timing", "scripts/tests/test-body-timings.mjs"],
  ["Environment parsing", "scripts/tests/test-env.mjs"],
  ["Subtitle punctuation", "scripts/tests/test-subtitle-punctuation.mjs"],
  ["Intro page pairing", "scripts/tests/test-intro-page-pairing.mjs"],
]) {
  if (!fs.existsSync(path.join(ROOT, testFile))) {
    console.warn(`Warning: skipped missing test ${testFile}`);
    continue;
  }
  const testResult = run(process.execPath, [testFile]);
  if (testResult.status !== 0) throw new Error(testResult.stderr || `${label} test failed`);
}

// 突发阈值的整数帧口径（2026-10-04《一个陌生女人的来信》行 28「不断等待。」）：
// lib 先换秒再比秒时，恰好 0.15s 的真语音会下溢成 0.14999999999999858 被 MIN_BURST 删掉，
// 该行窗内密度掉到 31%，门禁 7 在一条无处可挪的轴上判红。修复被并行编辑覆盖过一次
// （测试 12:12 写入、lib 12:47 又被改回浮点算术，任务却已标完成），所以这条回归必须进 check。
// 同一条口径链上的第二条回归：T2 的「读完所需」豁免（见上面 reading_need 那段静态断言）。
// 它和被摘掉的整数帧修复是同一类事故——测试写进去了、lib 后来又被并行编辑改回去，
// 任务却已标完成，所以两条都必须跑。
for (const [label, testFile] of [
  ["Speech bursts", "scripts/tests/test-speech-bursts.py"],
  ["Voiced share read pad", "scripts/tests/test-voiced-share-readable.py"],
  // 第三条独立证人：固定 −35 dBFS 噪声底（2026-10-10《美丽新世界》行 52「所以，」/行 86）。
  // 以前「整窗听不见人声」实际上是由**本期录音有多响**决定的：speech_bursts 的阈值按本期
  // p25/p90 自适应（实测 −16~−21 dBFS），同一声轻声字在响的期算语音、在轻的期算静音。
  // 两个方向相反的误杀都在这条口径上：兜底线把听得到的字幕判成空放（轴上无处可挪，2 字念
  // 0.34s 却要待 0.55s），以及推窗交叉把卡在两个突发之间的那一整个自身窗丢掉。
  // 六臂对照，且口径的**两半各自单独按掉都能把行 2 翻回红**（措辞各报被跨过的那一条，
  // 两条措辞必须不同），而「证人恒说没有静音」那一版必须同时洗白纯静音行并冒出假 T1 红。
  ["Acoustic presence", "scripts/tests/test-acoustic-presence.py"],
  // 图片段归属路由（2026-10-06《猎人笔记》）：整份清单交给 fetch 时，钩子段那个
  // IMAGES_SUBDIR 开关曾把 16 张**已调色**的正文图重下并写进 images-hook/，盖掉两张真钩子图。
  // 门禁看不见它——目录里有文件、尺寸对、溯源行也在，只是钩子拍换了内容。
  ["Segment routing", "scripts/tests/test-segment-routing.py"],
  // 重放核对的缩放档位（2026-10-06《猎人笔记》atmosphere-5）：verify-pexels-fetch.py 按固定
  // 1600 重放，而取图那一张落在 1280，同一张照片的两种渲染被当成「不符」，平均差 5.0 峰值 71。
  // 假红会把人推去换掉一张没有错的选型，所以档位必须由取图当场写下、由重放按记录去问。
  ["Replay width", "scripts/tests/test-replay-width.py"],
  // Pexels 鉴权头的传法（2026-10-06）：密钥从 curl 的 argv 挪进 stdin 配置。
  // argv 同机任何进程 `ps -eww` 都读得到，而打印整条命令的报错又会抄一次；
  // 这条测试两头都判——头确实送达本地 HTTP 服务，且改前的七个形状逐个仍被判为泄漏。
  ["Pexels key argv", "scripts/tests/test-pexels-key-argv.py"],
  // 第 11 道门全库普查的分类器（2026-10-06）：门的汇总末行恒含「无从证明」四个字，
  // 第一版 tmp 驱动按整段找词分类，把 118 部 ✅ 全报成 ⚠️，也就是把「全库红名单为空」
  // 伪装成好消息。普查的产出就是那份名单，名单错等于没有门。
  ["Intro census classify", "scripts/tests/test-intro-census-classify.py"],
  // 边界压测的取帧口径（2026-10-07《阿特拉斯耸耸肩》）：门禁按 f"{t:.4f}" seek，而 n/fps 在
  // 30fps 下多半是无限循环小数，进位后就读到隔壁那一帧；包装器又把正文起点按两位小数秒相加
  // （钩子配音 7.18 + 模板 3.0 = 10.18s，装配实际是 305 帧 = 10.1667s）。两个半帧的坐标错叠起来，
  // 把行尾 ±0.06s 的采样推进两行之间的真空，10 个「画面上没有字幕」全是假红——逐帧核对证明
  // 成片第 1636~1701 帧一直是行 19 的墨迹。这条双向都判：健康夹具必须逐点比中、老口径必须
  // 真的错进真空、声明比画面多 3 帧必须还红。
  ["Caption frame sampling", "scripts/tests/test-caption-frame-sampling.py"],
  // 控制 token 剔除 + 计数相等空档按位对位（2026-10-07《无名的裘德》行 103「郁郁地病着」
  // 被听成「浴浴的病者」、行 129「钟声很响」被听成「终身很想」）：这两行的字面命中只有
  // 1/5 和 1/4，低于 WEAK_ANCHOR → 门禁 7 弃权 → 字幕比配音晚 0.59s 无人可判。
  // 六组断言里每组都带「改动前那个形状确实会做错」的对照，包括真片那一组。
  ["Positional anchor", "scripts/tests/test-positional-anchor.py"],
  // token 的 ch 只能取「被念出来的第一个字符」（2026-10-08《反抗者》行 39「注意，不是我。」）：
  // whisper.cpp 把 BPE 的词边界空格留在 token 文本里（" 不是"），而 Node 侧 ch=原始文本首字符，
  // 于是这个 token 永远匹配不上（文案侧的 STRIP 已删空白），行首只能落到「我」@81.989，
  // 比交付轴 80.449（「注意」自己的开口）晚 1.541s，第 5 道上限 1.5s 差点判红，而两个能量
  // 证人都判 0 行。Python 侧本来就对（clean() 的 PUNCT 含 \s），这条把 Node 拉回同一字母表。
  // 全库 old-vs-new（146 期有正文 ASR）：命中字数 69 期上升、其余不动，判红 2 期→0 期
  // （反抗者 1.541→1.18、我们八月见 1.957→0.777），**没有一期从绿翻红**。
  // 两头都判：生产 --verify 必须绿，把那两处 trim 回放进同一份轴必须红且点名行 39。
  ["Token whitespace char", "scripts/tests/test-token-whitespace-char.py"],
  // 行首回收（2026-10-10《高老头》行 117「叉匙声、笑声」被听成「差史生孝生」）。渲前轴闸把
  // 这期拦在渲染之前，可两道能量证人都是 0 行红，只有 token 派生报「行首 -1.59s」；量到行
  // 自己身上：交付轴行首 252.733 正好是这一行自己的开口（lib/caption_speech.audible_windows）。
  // 错的是**证人**：这一行开头的字被误听，参考轴的行首就只能停在下一个直接命中字（样@254.323）
  // 上，于是「本行归属正确」被当成「本行归属漂了」。Python 侧 align() 早就有这条规则（任务 #56
  // 的按位对位：两侧悬空**字数相等**才敢一一对上），Node 侧缺的是它的**行首镜像**——AGENTS.md
  // 当时把这条范围写成「Node 侧没有要修的插值分支」，那说的是行尾（误听的尾巴停在最后一个命中字，
  // 不编时刻），行首是同一枚硬币的另一面，它会把正确的轴判成假红。
  // 阈值一个没动（1.5 / 0.35 / 1.0 / 3.0），改的是证人取行首的形状。全库 old-vs-new（157 期）：
  // 旧红 13 → 新红 12，红→绿只有本期，**绿→红 0 期**，原本绿的期点名行集合一字未变。
  // 六个对照两头都判，且 ② 的读数是数值证人（+1.59 = 254.323−252.733）而不是「该行被豁免」。
  ["Verify head reclaim", "scripts/tests/test-verify-head-dangling.py"],
  // 封面书名的三连裁决（2026-10-07「书名不要有换行的现象」→ 2026-10-08「不要有换行，
  // 并沉淀这个规则」→ 2026-10-09「根据合适排版进行缩小字号」）。第一版只做到「优先单行」，
  // 留了一条 ONE_LINE_MIN 装不下就折行的第②支；第二版把它退役，改成装不下就**报错并让用户
  // 写 cover_title**；第三版否掉的正是改名这条路——封面印的必须是正式书名。
  // 于是今天的形状是：一律单行，装不下就从 168px 往下缩到装得下为止（下限 60px＝钩子行字号），
  // 掉到 96px（2026-10-01《恶意》「字体模糊不清晰」那一版的实测字号）以下照出但必须逐期留痕。
  // 双向都判：本期单行 / 老阶梯回放必须报红 / 阶梯把下限写回 96 必须报红 / 低于 96 却不写
  // note 必须报红 / 合成 16 字书名必须报错指名 cover_title / 全库单行 100% 且 13 期小字全留痕。
  ["Cover one-line title", "scripts/tests/test-cover-one-line-title.py"],
  // 段级脚本版本（2026-10-07 第五批《围城》）：brief.json.scriptVersion 说的是正文（B 版），
  // 而 script-hook.csv 只有 A 版两行，--segment=hook 拿 B 去过滤就抛
  // `No script rows found for version B`；就算过了，下游 snap 也会过滤出 0 行再除零。
  // 这条把「本段取自己 CSV 里唯一的那一版」同时钉在 Node（建轴）和 Python（补 coverage）
  // 两侧，并把同一批夹具喂给 mjs 做跨语言对拍——两套解析漂一分，轴就会一半新一半旧。
  ["Segment script version", "scripts/tests/test-segment-script-version.py"],
  // 水波门的封面取证口径（2026-10-08《荒凉山庄》候选被「逐帧差质心向内收」判成只加水声）：
  // 本书封面整段压在涟漪中心上并按 lib 的 motion 逐帧放大，第一版用一张静态并框排它，只清空
  // 分子、没重算分母，被盖掉一半的环按整环面积稀释，实测吃掉 20~60px 外移量。这条在合成的
  // 真像素上跑三张片（涟漪+封面 / 只有封面 / 只有涟漪）与四套口径，双向都判：新口径必须绿、
  // 不排必须假红、静态并框必须比新口径少吃一大截、只有封面的片子排掉之后必须仍然红（门不能
  // 因为「排掉就没东西了」而放行），并且四个阈值一个都没动。
  ["Water cover exclusion", "scripts/tests/test-water-cover-exclusion.py"],
  // 本书封面的**版次**打分（2026-10-10《小王子》）：旧写法把书名、作者、名次三个维度**相加**
  // 成一个分数，于是作者档能买通书名档——「小王子（英文版）」(70+30+7=107) 压过中文版
  // 「小王子」(100+0+6=106)，差 1 分，简体书评视频那 1.4 秒上屏的是《The Little Prince》。
  // 病根不是权重不准，是一个维度可以买通另一个维度；译名用字（絮/苏）本来就不可比。
  // 改成逐档比较后，七组断言每组都带「改动前那套加法在同一份夹具上确实选错」的对照，
  // 包括 107 对 106 这个边界本身（夹具漂了就必须红），以及「降级不是否决」「作者字段留空
  // 不能靠 `"" in x` 拿 2 档」两条反向。
  ["Book cover edition", "scripts/tests/test-book-cover-edition.py"],
]) {
  if (!fs.existsSync(path.join(ROOT, testFile))) {
    console.warn(`Warning: skipped missing test ${testFile}`);
    continue;
  }
  const testResult = run("python3", [testFile]);
  if (testResult.status !== 0) {
    throw new Error(`${label} test failed:\n${testResult.stdout || ""}${testResult.stderr || ""}`);
  }
}

// 上面那条循环在文件缺失时只 warn（历史约定），所以「把封面口径的测试删掉」本身必须是红的。
// 四个判词各钉一条：它们分别是「无证据时退回全画面判决」「按渲染期证人的 motion 推足迹」
// 「证人写的动效 lib 不认」「证人指向一张不存在的封面图」这四条退路的输出。少一条就等于
// 那一类缺陷重新只能在人眼面前翻红——而 2026-10-08 那次翻红的正是人眼看不出的那种。
const waterCoverTestFile = "scripts/tests/test-water-cover-exclusion.py";
const waterCoverTest = path.join(ROOT, waterCoverTestFile);
if (!fs.existsSync(waterCoverTest)) {
  throw new Error(`${waterCoverTestFile} 不存在：封面同窗的水波取证口径没有回归，只有当期那一次实测`);
}
const waterCoverTestSrc = fs.readFileSync(waterCoverTest, "utf8");
for (const [token, why] of [
  ["未检出封面层", "「无证据→全画面判决」这条退路不再被断言：门可以悄悄排掉一张并不存在的封面层"],
  ["旁证 motion", "「按渲染期证人的 motion 推足迹」不再被断言：动效改了而掩码还按旧动效排，像素与证据脱钩"],
  ["找不到封面图", "「证人指向缺失的封面图」不再被断言：证据链断了却没人打印，读者以为排过了"],
  ["未通过 lib 校验", "「旁证动效被 lib 拒绝→退回 lib 口径并大声 warn」不再被断言：回退会重新变成静默"],
  ["--legacy-static-box", "静态并框的对照墙被拆了：新旧口径的差异没有可复现的量在盘上"],
  ["visible_denominator", "可见像素分母这条口径不再被直接断言：整环分母的稀释会重新变成看不见的"],
]) {
  if (!waterCoverTestSrc.includes(token)) {
    throw new Error(`${waterCoverTestFile} 少了 ${token}：${why}`);
  }
}

// 本书封面的版次打分：三个静态判据，各钉一次真实事故。
// 「逐档」这件事只有两种塌法——塌回加法（作者档重新买通书名档），或加法没塌但过滤掉了
// 0 分档（同作者的不同书重新变成一张能上屏的错封面）。两条都必须在源码层面被看见，
// 因为 2026-10-10《小王子》那一次，八道门禁全绿。
const editionScriptFile = "scripts/fetch-book-cover.py";
const editionScript = codeOnly(editionScriptFile);
for (const [token, why] of [
  ["def title_tier", "书名档不再是独立函数：档位比较退回内联分数就是加法回来的一步"],
  ["def author_tier", "作者档不再是独立函数：同上"],
  ["def foreign_edition", "外文字样版次的判定消失：版次降级（不是否决）这条结构没有实现处"],
  ["FOREIGN_EDITION_MARKERS", "标记表消失：降级只能靠人记住哪些字样算外文版次"],
  ["s[0] > 0", "书名档为 0 的候选不再被滤掉：同作者的不同书会重新变成一张能上屏的错封面"],
  ["key=lambda s: s[:4]", "比较不再是逐档元组：维度重新被加成一个数，作者档就能买通书名档"],
  ["if na and ba and (na in ba", "作者字段的空值守卫消失：`\"\" in x` 为真，一条没填作者的书目拿到 2 档"],
]) {
  if (!editionScript.includes(token)) {
    throw new Error(`${editionScriptFile} 丢了 ${token}：${why}`);
  }
}
// 被撤的加法形状不许出现在取图脚本里（测试文件里出现是**对照**，那边必须有）。
// 反面形状要能自己判红：把被撤写法塞进同一个谓词，必须命中，否则这条缺席断言是空转。
const withdrawnAdditive = [
  "100 if bt == nt else 70 if bt.startswith(nt)",
  "scored.sort(key=lambda x: (-x[0], x[1]))",
];
for (const shape of withdrawnAdditive) {
  if (editionScript.includes(shape)) {
    throw new Error(`${editionScriptFile} 里出现了被撤的加法形状 ${shape}：`
      + "一个维度可以买通另一个维度，《小王子》那 1.4 秒就会再次上屏英文版次");
  }
}
{
  // 逐条把被撤形状塞进一份副本，谓词必须命中——这一步是让「缺席断言」自己跑一遍。
  // 只注入一条再用 `some()` 判，等于允许其中一条永远扫不到东西。
  for (const shape of withdrawnAdditive) {
    const decoy = `${editionScript}\ndef _withdrawn(bt, nt):\n    return ${shape}\n`;
    if (!decoy.includes(shape)) {
      throw new Error(`加法形状的缺席断言是空转：注入 ${shape} 后谓词没有命中`);
    }
  }
}
const editionTestFile = "scripts/tests/test-book-cover-edition.py";
if (!fs.existsSync(path.join(ROOT, editionTestFile))) {
  throw new Error(`${editionTestFile} 不存在：版次打分只有当期那一次实测，没有可复现的对照`);
}
const editionTestSrc = fs.readFileSync(path.join(ROOT, editionTestFile), "utf8");
for (const [token, why] of [
  ["old_additive", "被撤的加法打分不再被重放：新写法「选对了」可能只是夹具变了"],
  ["22720170", "现抓夹具里英文版那一条的 bookId 消失：107 对 106 这个 1 分边界失去锚点"],
  ["只能命中带外文字样标记的版次", "「降级不是否决」那条 ⚠️ 不再被断言：只剩外文版次时会悄悄变成没有封面"],
  ["def old_score", "旧分数没有单独取值的对照：断言只比书名，比不过「差 1 分」这个具体边界"],
  ["title_tier", "书名档位不再被直接断言：并档（把 startswith 与 contains 合成一档）看不见"],
  ["author_tier", "作者档位不再被直接断言：空作者拿 2 档那类缺陷看不见"],
]) {
  if (!editionTestSrc.includes(token)) {
    throw new Error(`${editionTestFile} 少了 ${token}：${why}`);
  }
}

// ---------------------------------------------------------------------------
// 闪屏守卫（2026-10-04 用户报「片头钩子视频出现闪屏现象」→ 2026-10-05 用户裁决回滚）// 裁决原文：「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」，并在追问里选了
// 「三处全回滚（视觉 100% 回到从前）」。所以这段守的不再是「片头不闪」，而是三件事：
//   ① 钩子段确实带了段尾淡出——它是回滚之后**唯一**保留的抗闪屏装置；
//   ② 两条渲染路径都不许再对共享片头动手（trim 起点必须是 0，无垫帧、无溶解）；
//   ③ 10-04/10-05 加在片头侧的那套装置不许被「顺手修一下闪屏」凭记忆补回来。
// 第③条只钉**代码形状**（赋值、ffmpeg 字符串、文件存在性），不钉注释里的词——回滚说明
// 必须点名被删掉的键，用 token 缺席去扫会把说明本身判红。
const introTimingFile = "scripts/lib/intro-template.json";
const introTiming = JSON.parse(fs.readFileSync(path.join(ROOT, introTimingFile), "utf8"));
const hookFadeSeconds = Number(introTiming.hook_fade_out_seconds);
if (!(hookFadeSeconds > 0 && hookFadeSeconds < Number(introTiming.trim_seconds))) {
  throw new Error(`${introTimingFile} 的 hook_fade_out_seconds 必须是 (0, trim_seconds) 内的正数，`
    + `实际 ${introTiming.hook_fade_out_seconds}：设成 0 等于把「亮照片硬切全黑起幅」那次眨眼放回去`
    + "（《嫌疑人X的献身》第 8.27s 实测单帧 42.4→0.0）");
}
// 抗闪屏装置只落在钩子这一路，所以两条渲染路径都必须真的读同一个数、真的画淡出。
// 漏一条＝换条路径出片就闪（和水波、字幕标点同一类事故）。
const hookFadeWiring = {
  "scripts/render_scene_map_template_fallback.py": ["hook_fade_out_seconds", "HOOK_FADE", "fade=t=out:st="],
  "scripts/render-episode-final.mjs": ["hook_fade_out_seconds", "hookFade", "fade=t=out:st="],
};
for (const [file, tokens] of Object.entries(hookFadeWiring)) {
  const src = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const token of tokens) {
    if (!src.includes(token)) {
      throw new Error(`${file} 丢了钩子段尾淡出的 ${token}：淡出长度必须从 ${introTimingFile} 拿，`
        + "两条渲染路径都要做，否则换一条路径出片就闪");
    }
  }
}
// 片头那一路必须是「原样搬用」：从第 0 秒裁到 trim_seconds。起点一旦被挪（10-04 那版
// trim={HOLD}:{INTRO_TRIM}），结果页定格、水波窗口、PRE_BODY、正文起幅倍率就全错位了。
// 扫之前先去注释（codeOnly）：两条路径的回滚说明里都写着「片头仍然是 trim=0:INTRO_TRIM」
// 这类句子，拿原文扫的话，注释在、代码没了也能过——那是给正例造假证据。
// 口径与 scripts/tests/test-hook-seam-fade.py 的 strip_comments() 一致（两边同一份形状表）。
function codeOnly(file) {
  return fs.readFileSync(path.join(ROOT, file), "utf8").split("\n")
    .filter((l) => !/^\s*(#|\/\/|\/\*|\*)/.test(l)).join("\n");
}
const introTrimShapes = [
  ["scripts/render_scene_map_template_fallback.py", /trim=0:\{INTRO_TRIM\}/,
    "片头那一路不再从第 0 秒裁（应为 trim={0}:{INTRO_TRIM}）"],
  ["scripts/render-episode-final.mjs", /trim=0:\$\{INTRO_TRIM_SECONDS\}/,
    "片头那一路不再从第 0 秒裁（应为 trim=0:${INTRO_TRIM_SECONDS}）"],
];
for (const [file, shape, why] of introTrimShapes) {
  if (!shape.test(codeOnly(file))) {
    throw new Error(`${file}：${why}——共享片头按原样交付是这次裁决的本体`);
  }
}
// 回滚掉的三件片头侧装置，形状不许复活：钩子末帧垫帧 + 等长溶解、逐帧量跑道、底图归一亮带。
// 它们各有一份回归测试/脚本，所以这里同时查文件存在性——「脚本还在但没人调」和「脚本没了」
// 是两种不同的回滚，前者说明装配层随时可能被改回去。
// 反面形状**必须**在去注释之后的源码上扫：兜底渲染器那段 Pillow 交叉溶解的注释写着
// 「等价 xfade=transition=fade」，Node 路径的回滚说明写着「不再有 tpad 克隆末帧 + 等长
// xfade」——两条都是**解释**，拿原文扫就是把回滚的说明文字当成回滚被撤销，一个永远红、
// 只能被删掉的守卫。
const retiredShapes = [
  ["scripts/render_scene_map_template_fallback.py", /\bJUNCTION_(D|HOLD)\s*=/,
    "钩子→片头又开始了垫帧/等长溶解（junction_dissolve_seconds 已随 2026-10-05 回滚删除）"],
  ["scripts/render_scene_map_template_fallback.py", /xfade=transition=fade/,
    "兜底渲染器又对片头做了交叉溶解"],
  ["scripts/render-episode-final.mjs", /\bjunction(D|Hold)\b\s*=/,
    "钩子→片头又开始了垫帧/等长溶解"],
  ["scripts/render-episode-final.mjs", /tpad=stop_mode=clone/,
    "Node 路径又开始克隆钩子末帧当溶解底板"],
];
for (const [file, shape, why] of retiredShapes) {
  if (shape.test(codeOnly(file))) {
    throw new Error(`${file}：${why}——用户已明确拒绝那版开场白，修复只许落在钩子段`);
  }
}
for (const gone of ["scripts/lib/intro_runway.py", "scripts/grade-template-pages.py",
  "scripts/tests/test-junction-runway.py", "scripts/tests/check-intro-label-wrap.mjs"]) {
  if (fs.existsSync(path.join(ROOT, gone))) {
    throw new Error(`${gone} 还在：它是 2026-10-04 为「把片头亮度事件摊平」加的装置，`
      + "随 2026-10-05 回滚整份退役（旧件在 tmp/retired-20261005/）。留着它就等于留着改回去的路");
  }
}
// 光扫主文件不够：scripts/ 下还留着两份 9 月按书名分叉出来的生成器
// （create-episode-preview-xglc.mjs / -xw.mjs，各自的 render-episode-final-xw.mjs 还在），
// 它们当初是整份拷贝，旧形状在拷贝里活着——和水波、字幕标点那条「手抄一份就会漂」的
// 事故同一形状。所以这里按模式扫**所有**生成器，不分主副：任何一份再写反向双淡出即红。
for (const name of fs.readdirSync(path.join(ROOT, "scripts")).filter((n) => /^create-episode-preview.*\.mjs$/.test(n))) {
  const src = fs.readFileSync(path.join(ROOT, "scripts", name), "utf8");
  for (const [shape, why] of [
    [/tl\.to\("\.s" \+ \(k - 1\), \{ opacity: 0/, "换页时把退场层一起淡出"],
    [/tl\.to\("\.bridge", \{ opacity: 0/, "封面桥接层退场时把它淡出"],
  ]) {
    if (shape.test(src)) {
      throw new Error(`${name} ${why}：黑底上两层同时反向淡入淡出，溶解中点比凸组合少 25%·下层，`
        + "判据④把这一拍读成「去了又回」（见 scripts/tests/test-dissolve-dip.py）");
    }
  }
  // 另一半修法同样会被悄悄退回：只「让退场层不淡出」会把下探变成**一帧硬切**（判据③ 20.8>17.7）。
  // .photo 是 z-index:1，而 .scene 不写 z-index 时只有 opacity<1 才生成层叠上下文，所以永不淡出的
  // 退场层会把自己的图压在整张进场层上面直到 opacity 恰好为 1。凹陷和硬切是同一个 bug 的两张脸。
  if (!/\.scene\s*\{[^}]*z-index\s*:\s*(?!auto\b)[\w-]+/.test(src)) {
    throw new Error(`${name} 的 .scene 规则里没有显式 z-index：退场层不淡出之后，`
      + "换页会退化成「保持一段再单帧跳」，判据③红——写 auto 或不写都算没修");
  }
}

// 取帧必须单 Chrome worker（2026-10-07）。用户四次报的「钩子视频闪现」量到的是**渲这一份本身**
// 的脏帧：headless Chrome 多 worker 的栅格化竞态偶尔让某一帧只画了一半全屏 promoted `.photo` 层，
// 底带（y 834..960）从照片塌成一片平场再立刻回升。判据在 scripts/check-render-artifacts.py，
// 这里守的是它的**概率**。同一份《人类群星闪耀时》组成各渲 6 次的对照：默认 --workers=auto
// 脏帧计数 0,1,2,0,1,1（6 次里 4 次脏），--workers=1 是 0,0,0,0,0,0；A-4 与 B-4 全帧 MAD 均值
// 0.00、单帧峰值 0.0、帧数 160/160 一致——所以这个旗子只改并发，不改画面，不碰 2026-10-05
// 冻结的片头观感（它改的是 package.json 里那行命令，不是七张底板、版式、配色或时刻表）。
// 为什么四个落点都要钉：`hyperframes render` 的默认值是 --workers=auto，**不写就等于多 worker**；
// 片头段的 package.json 又是整份 copyFile 自共享模板（create-episode-preview.mjs 的 createIntro），
// 装配脚本还自带 --workers 参数——少守一处就还留着一条能渲出脏帧的路。
{
  const dirtyRenderLines = (text) => text.split("\n").filter((line) =>
    /hyperframes@[\w.]+(?:\$\{[^}]*\})?\s+render\b/.test(line) && !/--workers["' ]+1\b/.test(line));
  for (const name of fs.readdirSync(path.join(ROOT, "scripts"))
    .filter((n) => /^create-episode-preview.*\.mjs$/.test(n))) {
    const rel = `scripts/${name}`;
    const offenders = dirtyRenderLines(codeOnly(rel));
    if (offenders.length) {
      throw new Error(`${rel} 有 ${offenders.length} 行 hyperframes render 没写 --workers 1：`
        + offenders[0].trim().slice(0, 120)
        + "\n  多 worker 取帧会渲出单帧半栅格的合成伪影（用户报的钩子闪现），而默认值 auto ＝多 worker");
    }
  }
  const introPkg = JSON.parse(fs.readFileSync(
    path.join(ROOT, "templates/shared-video-template/intro/package.json"), "utf8"));
  if (!/--workers[ ]+1\b/.test(introPkg.scripts.render || "")) {
    throw new Error("templates/shared-video-template/intro/package.json 的 render 没写 --workers 1："
      + "它被 createIntro 整份 copyFile 进每期 intro 工程，那里就是片头段的取帧并发来源");
  }
  for (const rel of ["scripts/render-episode-final.mjs", "scripts/render-episode-final-xw.mjs"]) {
    const src = codeOnly(rel);
    if (!/process\.env\.HF_WORKERS\s*\|\|\s*"1"/.test(src)) {
      throw new Error(`${rel} 的 HF_WORKERS 默认值不是 "1"：环境变量留给对照实验，默认必须是单 worker`);
    }
    const hard = [...src.matchAll(/"--workers",\s*"(\d+)"/g)].map((m) => m[1]);
    if (hard.some((w) => w !== "1")) {
      throw new Error(`${rel} 里写死了 --workers ${hard.filter((w) => w !== "1").join("/")}：`
        + "任何一段用多 worker 取帧都可能渲出单帧合成伪影");
    }
  }
  // 守卫自己不能是摆设：把撤回的形状（不写 --workers）灌回同一条判据，必须判红。
  const withdrawn = "render: `npx --yes hyperframes@0.7.33 render --quality standard --output renders/body.mp4`";
  if (dirtyRenderLines(withdrawn).length !== 1) {
    throw new Error("单 worker 守卫判不出「没写 --workers」那行（自检命中 "
      + dirtyRenderLines(withdrawn).length + " 条）：这条断言是 theatre");
  }
  // 伪影明细与 baseline 必须按**期**命名（2026-10-07 量这次竞态时才发现的第二个洞）：两者都叫
  // .artifact-<段>*，一批里下一期会把上一期的证据盖掉（我一度把《无名的裘德》跑完后盖上的明细
  // 当成《人类群星闪耀时》自己的），baseline 更糟——上一期渲坏的 mp4 会被当成这一期的「上一次渲染」，
  // 于是把一次偶发的半张瓦片读成「同一帧再现＝设计内」，正是给用户闪现买单条豁免的那类误判。
  const batchSrc = fs.readFileSync(path.join(ROOT, "scripts/render-hook-batch.sh"), "utf8");
  if (/\.artifact-\$seg\b|\.artifact-baseline-\$seg/.test(batchSrc)) {
    throw new Error("render-hook-batch.sh 的伪影明细/baseline 又回到只按段名落盘："
      + "同批下一期会覆盖上一期的证据，baseline 还会跨期把偶发脏帧洗成「设计内」");
  }
  if (!/tag="\$LOGDIR\/\.artifact-\$ep-\$seg"/.test(batchSrc)) {
    throw new Error("render-hook-batch.sh 里那份按期限名的 tag 变量不见了——明细和 baseline 又会变成共享路径");
  }
}

// 系统 bash（本机 3.2.57）在 UTF-8 locale 下会把**紧跟变量名的多字节首字节算进变量名**，
// 于是 `set -u` 查的是 `before\xef` 这个不存在的变量。《无名的裘德》2026-10-08 11:13 那期就死在
// 驱动第 109 行那句 echo 上：hook.mp4 渲完 45 秒、门禁一道没跑、成片没装，日志末行是
// 「line 109: before？: unbound variable」。同一行在当天另外五期里都正常打印了「组成指纹 sha256:…」
// —— 差别只在启动它的那个 shell 的 locale：本机实测 LC_ALL=C 解析成 `before`，而 en_US.UTF-8 /
// zh_CN.UTF-8 两种都解析成 `before\xef`。所以「这行昨天还好好的」在这类语句上不是证据，
// 它只是昨天那个 shell 的 locale 恰好是 C。修法只有花括号：`${before}，` 在两种 locale 下同解。
{
  const bareVarBeforeCjk = (text) => text.split("\n")
    .map((line, i) => ({ line, n: i + 1 }))
    .filter(({ line }) => !line.trim().startsWith("#"))
    .flatMap(({ line, n }) => {
      const bare = line.replace(/\$\{[^}]*\}/g, "").replace(/\$\([^)]*\)/g, "");
      return [...bare.matchAll(/\$([A-Za-z_]\w*)([^\x00-\x7f])/g)].map((m) => `${n}:$${m[1]}${m[2]}`);
    });
  for (const name of fs.readdirSync(path.join(ROOT, "scripts")).filter((n) => n.endsWith(".sh"))) {
    const rel = `scripts/${name}`;
    const hits = bareVarBeforeCjk(fs.readFileSync(path.join(ROOT, rel), "utf8"));
    if (hits.length) {
      throw new Error(`${rel} 有 ${hits.length} 处裸 $变量 紧跟全角标点，第一处 ${hits[0]}：`
        + "\n  macOS 系统 bash 在 UTF-8 locale 下会把多字节首字节读进变量名，set -u 于是报 unbound variable"
        + " 并中途退出整期渲染（《无名的裘德》2026-10-08 11:13）——写成 ${var} 两种 locale 都对");
    }
  }
  // 守卫自己不能是摆设：把被撤回的形状（不带花括号）灌回同一条判据，必须恰好命中一处。
  const withdrawnLine = '      echo "[render] $seg OK（第 $attempt 次，组成指纹 $before，单帧伪影扫描干净）"';
  if (bareVarBeforeCjk(withdrawnLine).length !== 1) {
    throw new Error("全角标点前的裸 $变量 守卫判不出被撤回的那行（自检命中 "
      + bareVarBeforeCjk(withdrawnLine).length + " 处）：这条断言是 theatre");
  }
  // 三处已知落点还要按原文钉住：通用扫描退回时只说「有 1 处」，点名才说得出退的是哪一次事故。
  const batchFixed = fs.readFileSync(path.join(ROOT, "scripts/render-hook-batch.sh"), "utf8");
  for (const [needle, why] of [
    ["组成指纹 ${before}，", "render_segment 成功那行（原 109 行，2026-10-08 就是它炸掉一整期渲染）"],
    ["rc=${rc_render}（", "兜底渲染器失败那行的 FATAL（原 180 行：炸在报错语句上等于把诊断一起吞掉）"],
    ["持有者已不在（${owner}），${RENDER_LOCK_DIR}", "接管陈旧锁那行（2026-10-09 由 test-render-serialization.py "
      + "第一次跑满炸出来的：它坐在**抢锁失败之后的恢复路径**上，退回去等于「上一期被陈旧锁卡住、"
      + "这一期在报告接管的那句话上把整批带走」——报错语句自己报错，是本文件第三条同类事故）"],
  ]) {
    if (!batchFixed.includes(needle)) {
      throw new Error(`render-hook-batch.sh 里找不到 ${needle} —— ${why}`);
    }
  }
}

// 驱动「渲几段」看 script-hook.csv 在不在（2026-10-07 重渲两段片《荆棘鸟》）。它原先无条件
// 先渲 hook，两段片于是报「工程目录不存在：tmp/preview-book-…/hook」+「hook 渲之前算不出工程
// 指纹 —— 本期不进门禁」：一句读起来像**素材缺失**的话，实际是驱动假设了每部片都有钩子段。
// 生成器（create-episode-preview.mjs 不给两段片造 hook 工程）和兜底渲染器（HAS_HOOK→hook_dur=0，
// PRE_BODY 退回 INTRO_TRIM）早就按结构分过支，第 11 道门对它们也是 ⚠️ 无从证明，所以缺那一段
// 不是缺陷而是本期结构。这条钉两点：判据确实取自 script-hook.csv，且 hook 段会被它短路。
{
  const batch = fs.readFileSync(path.join(ROOT, "scripts/render-hook-batch.sh"), "utf8");
  if (!/episodes\/\$EPISODE\/script-hook\.csv/.test(batch)) {
    throw new Error("render-hook-batch.sh 不再按 script-hook.csv 决定渲几段：两段片会卡在"
      + "「hook 渲之前算不出工程指纹」，而那句报错读起来像素材缺失，把人指向录音而不是驱动");
  }
  if (!/= no \] \\\s*\|\| render_segment hook/.test(batch)) {
    throw new Error("render-hook-batch.sh 的 hook 段不再被 HAS_HOOK 短路（那个 `||` 分支没了）："
      + "两段片依旧渲不出片，交付门禁只能对着上一代像素打分");
  }
}

// 第 10 道门必须**无条件**跑：两段结构的成片没有钩子，片头在 t=0 一样闪（《小王子》《教父》
 // 《家》各 7 帧），挂在 if has_hook 里就等于给一半的片子开了免检。
const deliveryGateSrc = fs.readFileSync(path.join(ROOT, "scripts/check-episode-delivery.py"), "utf8");
// 两种写法都算「无条件第 10 道」：直接 append，或者先存进变量再 append（闪屏红时要触发归因
// 诊断，就得拿到那个返回值）。真正 forbidden 的是把它塞进 if / 挂上 has_hook ——缩进一变就说明
// 它被挪进了某个分支，等于给两段结构的成片开了免检。
if (!/^ {4}(?:(\w+) = )?(?:results\.append\()?run_gate\(\n {8}"luminance-flicker"/m.test(deliveryGateSrc)) {
  throw new Error("check-episode-delivery.py 的亮度闪屏门不再是无条件第 10 道（缩进变了＝被挪进了某个 if 分支）");
}
// 存进变量再 append 也可以（触发归因诊断要拿到那个返回值），但变量必须真的进汇总——
// 「跑了一道没记账的门」和「把门删了」在最后一行列上长得一模一样。
{
  const wiring = deliveryGateSrc.match(
    /^ {4}(?:(\w+) = )?(?:results\.append\()?run_gate\(\n {8}"luminance-flicker"/m);
  if (wiring && wiring[1] && !deliveryGateSrc.includes(`results.append(${wiring[1]})`)) {
    throw new Error(`check-episode-delivery.py 把闪屏门的结果存进 ${wiring[1]} 却没有 append 进汇总：`
      + "这道门跑了但不计入门禁计数，汇总会永远少印一道");
  }
}
// 判据①~⑤ 报的是整片的数，修法却分属三层（共享模板 / 本期素材 / 装配层），只报「红」会让人
// 去压暗底图或改阈值。所以门禁红时必须自动跑一次归因诊断——这条接线掉了，归因脚本就成了没人调的摆设。
if (!(/check-flicker-attribution\.py/.test(deliveryGateSrc) && /if not flicker\[0\]/.test(deliveryGateSrc))) {
  throw new Error("check-episode-delivery.py 不再在闪屏红时调用 check-flicker-attribution.py —— "
    + "只报红不报owner，下一步就会变成「把片头调暗」这种被明令禁止的修法");
}
if (!deliveryGateSrc.includes("整片无亮度闪屏")) {
  throw new Error("check-episode-delivery.py 的 GATE_NAMES 里少了「整片无亮度闪屏」——汇总会少印一道门");
}
// 2026-10-05 用户裁决「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」之后，判据②~⑥
// 不再判共享片头窗——于是**十道门里没有任何一道看得见交付像素里的片头是回滚前还是回滚后**：
// 那 6 部（10-04 14:27 ~ 10-05 10:58 渲的）底板被归一亮带、书单被改成卡片网格、接缝被垫帧溶解、
// 砸黑被改成 0.50s 溶解，十道门照样全绿；组成指纹也绿，因为源此刻确实和记录一致。
// 第十一道门补的就是这个洞，所以它的接线一旦掉了，裁决就重新变成一句口头约定。
{
  const introGate = "scripts/check-intro-is-original.py";
  if (!fs.existsSync(path.join(ROOT, introGate))) {
    throw new Error(`${introGate} 不见了 —— 交付像素里的片头原样从此无从核对`);
  }
  if (!/results\.append\(run_gate\(\n {8}"intro-original",\n {8}\[PY, "scripts\/check-intro-is-original\.py"/.test(deliveryGateSrc)) {
    throw new Error("check-episode-delivery.py 不再把「片头原样核对」append 进 results —— "
      + "这道门要么没跑，要么跑了却不计入门禁计数（汇总少印一道 = 裁决失去机械凭据）");
  }
  if (!deliveryGateSrc.includes("片头原样核对") || !/names\.append\(GATE_NAMES\[10\]\)/.test(deliveryGateSrc)) {
    throw new Error("check-episode-delivery.py 的 GATE_NAMES/names 少了第 11 道「片头原样核对」——"
      + "结果数组与名字数组错位，汇总会把别的门安到这道门头上");
  }
  const introGateSrc = fs.readFileSync(path.join(ROOT, introGate), "utf8");
  for (const [token, why] of [
    ["check-luminance-flicker.py", "必须复用门禁自己的探针与 held_levels()：第二个亮度口径"
      + "和第二个电平切法会和判据⑥量出两个答案"],
    ["media", "底图平台必须从 templates/…/media/_raw/pages 现场量，抄一份数字表就把裁决"
      + "变成了「和一张可能过期的表比」——底板逐字节守卫也随之失效"],
    ["intro-template.json", "接缝/滚动/砸黑三个窗口全部由同一份时刻表推出：写死秒数就是"
      + "AGENTS.md 禁止的「hand-write a runway length」"],
    ["_raw", "基准必须是**未调色的原图**；拿 media/ 下的成品当基准，调色版会自证清白"],
  ]) {
    if (!introGateSrc.includes(token)) {
      throw new Error(`${introGate} 不再引用 ${token} —— ${why}`);
    }
  }
}
const flickerGateSrc = fs.readFileSync(path.join(ROOT, "scripts/check-luminance-flicker.py"), "utf8");
for (const [token, why] of [
  ["DEFAULT_MAX_DELTA = 40.0", "判据①闸刀线口径（正文 p99≈2.4，缺陷 42~93）；它是**全片**判据，"
    + "豁免窗只管②~⑥，所以片头再怎么按原样交付，一帧砸进黑起幅照样红"],
  ["0.9 * expected", "反空转：解不满整片就判红，不许「没看到画面」当成「没有缺陷」"],
  ["OPENING_LUMA_FLOOR = 18.0", "判据②眨眼：钩子段里下探全黑就是缺陷，不许被「合法短黑场」放回去"],
  ["def rate_allowance(", "判据③必须是**本片正文**的相对量；退化成手写绝对数＝第二个天花板（见用例 I）"],
  ["RATE_MULTIPLE = 4.0", "判据③的倍数：开场 14.3 / 正文 5.0 实测落在 4× 以内，写死 2× 就把修法本身判红"],
  ["def flickers(", "判据④「去了又回」：翻页来回与图层连乘凹陷只有这一条看得见（用例 C/D 实测旧⑤ 0 帧）"],
  ["FLICKER_LEG_MAX_S = 0.35", "判据④的时间尺度：两个合法大跨度（钩子→片头、书单→结果页）腿都 >0.35s，"
    + "取消这个条件就把一次溶解判成闪"],
  // 判据⑤（2026-10-05）：①~④ 的输入量里根本没有「这一帧换掉了多少像素」——《李尔王》成片
  // 的钩子段整幅换页时均亮度只跳 13.7，MAD 却到 20.8（正文峰值 4.7）。它必须是**同一次解码**
  // 的第二个量，否则同一处画面会拿到两份互相矛盾的证词；允许线也必须跟着本片正文走，
  // 写成绝对数就是规则禁止的第二个天花板。
  ["def content_allowance(", "判据⑤必须是本片正文 MAD 的相对量（和判据③同一形状）"],
  ["CONTENT_MULTIPLE = 4.0", "判据⑤的倍数：写死更小就把现修法（钩子段 0.72s 溶解换页）判红"],
  ["CONTENT_FLOOR = 16.0", "判据⑤的下限：正文极平滑的片子允许线不能无限低（合法那一拍实测 13.7）"],
  ["luma, mad, band = frame_curves(", "判据⑤⑥和①~④必须读**同一次解码**的三条逐帧量（分两次解码＝两份真源）"],
  ["measure(luma, mad, band)", "②~⑥ 全部走同一个片段聚合器，不许有的判整窗、有的判片段（两份口径）"],
  // 判据⑥（2026-10-05）：判据①~⑤ 看的都是「这一帧比上一帧变了多少」，所以一次**慢慢漏进黑底**
  // 的溶解（《李尔王》交付片 t=3.80s 五帧下探 5.6 级、单帧峰值只有 4.1）五条全绿，而那就是
  // 用户第四次报的「还是闪」。它必须量「离两侧电平夹出的带有多远」，且深度地板必须带一个
  // 比例项——只有绝对容差的话，把两张图一起调暗就能洗绿（depth/带宽 在同比压暗下不变）。
  ["def held_levels(", "判据⑥先要认出「镜头电平」：整段极差够小**且**够长，否则溶解谷底会被登记成电平"],
  ["def dissolve_excursions(", "判据⑥的越带判据本体"],
  ["dissolve_excursions(curves[s:e]", "⑥ 和②~⑤ 判在同一批**被判片段**上，不是整窗（豁免范围只有一份口径）"],
  ["floor = max(tol, rel * (hi - lo))", "⑥ 的深度地板含比例项：压暗洗不掉它，也不需要在它之外再加绝对天花板"],
  ["UNDERSHOOT_REL = 0.08", "⑥ 的比例地板：B=0.5A 的双层淡出理论最深 12.5%·带宽，0.08 在它下面、在编码噪声上面"],
  ["LEVEL_MIN_FRAMES = 15", "⑥：0.5s 才登记一个电平——溶解谷底那段平底不算，否则一次回头被拆成两条单调腿"],
  ["LUMA_BAND_ROWS = (0, 35)", "⑥ 的取证窗口不许含字幕行：墨迹是独立相加的一层 gain，含进来就会在"
    + "《丧钟为谁而鸣》《一个叫欧维的男人决定去死》两处误判（修法改口径，不是把 UNDERSHOOT_TOL 抬到 5）"],
  ["EXCURSION_MAX = 0", "⑥ 的上限：一处越带就红。写成 ≥1 就是把「双层同时反向淡出」这个真缺陷摊平掉"],
  // 管辖范围（2026-10-05 用户裁决「只改动钩子视频的闪屏问题」）：豁免的是**范围**，不是阈值。
  // 这三条掉任何一个，都会把回滚后的固定开场白（黑场起幅 + 书单砸黑）重新判红，
  // 而那时最省事的修法又变成「改片头」——正是被明令禁止的方向。
  ["def judged_segments(", "②~⑥ 的判决范围必须是一段函数式划分（钩子段本体 + 正文起幅），不是把窗口切回整块"],
  ["segments, exempt = judged_segments(", "main() 真的在用它，而不是只定义了一份没人读的口径"],
  ['float(intro_lib.get("hook_fade_out_seconds"', "钩子段尾淡出的长度只有 lib 一份真源，门禁跟着它划豁免窗"],
  ["按同一套判据本会翻", "豁免窗必须把数**印出来**（只报告不判红）——不印就是暗账，红了多少没人知道"],
  ["if not intro:", "认不出结构时**不豁免**（宁可多判不可漏判），退成整窗照判"],
]) {
  if (!flickerGateSrc.includes(token)) {
    throw new Error(`check-luminance-flicker.py 丢了 ${token} —— ${why}`);
  }
}
// 判据的形状靠合成序列钉（真片只有一份产物，它绿了可能只是判据没醒）。这条必须跑：
// 2026-10-04 那条退役的旧判据⑤就是「只在真片上验过」的判据——两类真实缺陷全盲、
// 报红的理由又站不住，最省事的修法永远是压暗画面。
if (run("python3", ["scripts/tests/test-flicker-rules.py"], ROOT, { stdio: "inherit" }).status !== 0) {
  throw new Error("亮度闪屏判据回归（test-flicker-rules.py）未通过——判据形状退了，先别改产物");
}
// 剩下的合成夹具回归（各自只判形状与接线，不靠真片那份唯一产物）：
//   ① test-hook-seam-fade.py —— 回滚之后钩子段尾淡出是唯一保留的抗闪屏装置：它必须真的从
//     lib 拿长度、两条渲染路径都要画它，而且把门禁自己的 flickers() 灌进合成序列证明
//     「淡出到黑」不是判据④的「去了又回」，而同一处去掉淡出的硬切形状确实被它判红（非空转）。
//   ② test-dissolve-dip.py —— 共享构图里换页的进场层与退场层不许再同时反向淡入淡出：
//     黑底上双层 alpha 的溶解中点比凸组合少 25%·下层（《人间草木》实测 59.0/60.2 → 45.1，
//     模型 44.8）。这条是**钩子侧**设备（.scene 图层），与片头回滚无关，所以它留下。
//   ③ test-dissolve-undershoot.py —— 判据⑥本体。它抓的是「两帧之间没跳、却慢慢漏进黑底」，
//     ①~⑤ 对这一形状天生全盲（《李尔王》交付片实测：单帧峰值 4.1、来回 0 处、MAD 也在允许线
//     内，而五帧下探 5.6 级），所以它必须由 ffmpeg 真的画出两种溶解再灌进门禁 main()：
//     凸组合绿、双层反向淡出红、**同比压暗之后照样红**（否则比例地板形同虚设）、
//     字幕行含进来才红（证明取证窗口的选择是判红的一部分而不是运气）。
//   ④ test-composition-digest.py —— 渲染竞态：一次 render 跑几十秒，同期另一个会话把同一份
//     工程目录重写掉，产物就是「一半旧一半新」的像素，而十道门禁每一条问的都是「画面对不对」，
//     没有一条问「这份像素是谁渲出来的」。《李尔王》第四次报闪屏的真源正是这个（index.html
//     改于 10:10:42、渲染 10:10:58 收尾、交付片带的是 `.scene` 还没有 z-index 那版形状）。
//     正反对照都在里面：动 renders/ 里的产物不许动指纹（产物不许给自己的来源作证），
//     动 index.html 一个字节必须动指纹。
//   ⑤ test-flicker-attribution.py —— 归因表本身。红片下一步改哪儿全靠它，而它算错段落边界或
//     读错允许线**照样会输出一张看起来很专业的表**，只是把修法指错地方。这次加判据⑥时它正是
//     盲区：《李尔王》门禁报「❌ 漏底 5.6 级」而归因回「无红段」，全库 32 部红里 27 部没有归属。
//     正反对照：健康片两段 0 处越带；同一形状摆进钩子段只翻 ⑥（①~⑤ 一条都不许跟着红）并把
//     归属报成「共享生成器的淡出形状 · 重渲即消」，摆进共享片头段必须**只印不判红**
//     （豁免的是范围，不是阈值），且判决范围必须继续等于门禁 judged_segments() 的两段。
//   ⑥ test-intro-is-original.py —— 片头原样门。它的绿灯是「交付像素里的片头是裁决那一版」的
//     **唯一**凭据（十道门按裁决都不看那段），所以它自己必须是可翻面的：健康形状绿、
//     10-04 那版三个证人一起红、每个证人单独被拆掉时**只**红它那一项，
//     外加一条压暗对照（把底板整体调离原图平台必须红——裁决禁止的正是拿亮度换绿灯）。
//   ⑦ test-title-font.py —— 书名层字体（2026-10-06 用户要求 2）。它守的两件事都没法靠读代码
//     确认：face 由 .ttc 的 index 决定而 index 顺序是打包者定的（写死 index 会拿到黑体-繁），
//     以及「lib 改了而盘上还是旧像素」。夹具把这两款都做成可翻面的对照——请求繁体 face 必须被拒、
//     两家都装不到必须标成兜底、证人记回 STHeiti 或 title.png 被改一个字节必须判红。
//   ⑧ test-render-artifacts.py —— 渲后单帧合成伪影（用户 10-06 第①条规则）。它防的是第三种
//     「绿灯是旧的」：《一颗简单的心》2026-10-07 那一记底带 38.3→27.5→38.3 在第 10 道门里读成
//     峰值 2.4／跳变 3.1／越带 0 处，十三道全绿，而它就是用户报的闪现。判决必须来自**本期工程
//     自己声明的单帧刀口**，两种看起来合理的替代都被真像素证伪过：拿另一次渲染当豁免（同一份钩子
//     连渲三次是「有／干净／有」，而脏的那两次落在同一帧同号），和把 GSAP 的时刻按窗长展开当豁免
//     （字幕出场是 0.16s 渐变，单调 ramp 给不出回头，却把 r3 的 t=5.03s 塌陷买成了设计内）。
//     正反对照七个方向：四种渐变必须红、三种刀口必须绿；外加不传组成时一律严格。
//   ⑨ test-book-cover-overlay.py —— 本书封面门（2026-10-07 用户要求）。这一层的两条红线是
//     「它得真的在像素里」和「它得按时走」，而两者都没法靠读代码确认：叠层是渲染器现场合成的，
//     时刻是水波偏移 + 交叉溶解推导出来的。夹具把六种形状都做成可翻面的对照——没叠这层红、
//     叠了另一本书的封面红（且红话必须说「不是这本书的封面」而不是「没上屏」，两者修法相反）、
//     烤进 body-bg 底板红、常驻不走红、钩子段带它红而干净绿、改 lib 没重渲红——外加证人链三红
//     （没证人 / 桥接卡被重切 / 证人记的是被否决的旧时序）与存量期 ⚠️ 不炸。
//     2026-10-09 起这一套夹具还持有**动效形状**的四条对照（证 D 在交付像素上逐帧反解 ŝ）：
//     健康轨迹必须自己报「轨迹与 lib 相符」（绿要来自量到东西，不是来自没醒）；被否决的整数
//     取整 1.00→1.12 必须同时红「轨迹」与「越带」两条，而它的叠层字节与 lib 逐字节相同——
//     这一条才是「lib 改了 ≠ 像素改了」在动效维度上的正面防线；有叠层但静止的卡必须红「没在
//     成片里长开」而足迹那条不红；纯色装帧（卡芯没有可对齐的纹理，ŝ 只能顶在搜索网格左端点）
//     必须打印「不判（不是通过）」而整片仍绿——仪器的失盲写成红，送人去查一张没有缺陷的封面。
//     越带那一条必须用 1:2 的高装帧造：3:4 的卡芯在同一比例下只越 1px，低于那条 3.0px 的线，
//     用它测越带等于没测。
//   ⑩ test-cover-motion-smoothness.py —— 封面放大动效的「流畅 / 不越带 / 够快」（2026-10-08
//     用户二次要求）。上面那道门证的是「这一层在不在、走没走」，动效只是被证 A 顺带覆盖：
//     2026-10-08 复核时 `grep zoom|motion` 在 test-book-cover-overlay.py 里为空、`grep zoom_at|
//     cover_frame_sequence|--exclude-cover` 在 check.mjs 里为空，也就是把渲染器退回「静态 PNG +
//     fade」当时不会有任何一道门红。这条回归补的就是那三个面：包络算术必须看得见缩放（1.12 那版
//     给出 y205.8..686.8 并点名两条带）、motion() 的四道拒绝各配一个只违反一条的配置、
//     真像素上现形状逐帧回退 0 处而**被否决的整数装置回退 ≥8 处**（少了后者，0 就只是「量不到
//     东西」的同义反复），外加每帧净位移必须 ≥ 旧形状的 1.3 倍——「节奏更快」也要落在像素上。
// 真片只有一份产物，它绿了可能只是判据没醒；这些不许只在真片上跑。
for (const [label, testFile] of [
  ["Hook seam fade", "scripts/tests/test-hook-seam-fade.py"],
  ["Scene dissolve dip", "scripts/tests/test-dissolve-dip.py"],
  ["Dissolve undershoot", "scripts/tests/test-dissolve-undershoot.py"],
  ["Composition digest", "scripts/tests/test-composition-digest.py"],
  ["Flicker attribution", "scripts/tests/test-flicker-attribution.py"],
  ["Intro original look", "scripts/tests/test-intro-is-original.py"],
  ["Title font", "scripts/tests/test-title-font.py"],
  ["Render artifacts", "scripts/tests/test-render-artifacts.py"],
  ["Book cover overlay", "scripts/tests/test-book-cover-overlay.py"],
  ["Cover motion smoothness", "scripts/tests/test-cover-motion-smoothness.py"],
  // 渲前轴闸（2026-10-08 第五批）：夜莺与玫瑰 / 呐喊 / 动物农场 各渲了约十分钟，才在第七道门
  // 「逐行盖住自身语音」翻红，红的是**轴**——那三期的 body-timings.json 没跑过 snap/audit
  // （audit dry-run 报 changes 34、late 2→0），而渲染链上没有任何一步在渲染之前问过轴一句。
  // 这道闸把判定提前到十秒内，红就按 AGENTS.md 次序修一轮再量；三头都必须跑：健康放行、
  // 人为推迟行首必须拦、修轴臂必须把它修绿。
  ["Axis guard", "scripts/tests/test-axis-guard.py"],
  // 渲前串行化判据（2026-10-08 任务 #43）：连着两次事故都不是「渲出来不合格」，而是
  // 「渲的时候机器不对」——13:13《昆虫记》六次 attempt 全 ENOTEMPTY（两个会话抢同一棵
  // /private/tmp 的 _npx 安装树，谁都不报错，只是永远渲不出文件）；2026-10-05《李尔王》在
  // 另一个会话改写共享真源时开渲，成片是「一半旧一半新」的像素而十道门禁全绿。两类都不产生
  // 不合格产物，所以断言只能钉在「什么时候允许开渲」这个判据上。判据读的是进程表、mtime、
  // 锁目录三种量，三种都能被写坏且写坏之后一切照绿，因此每条都带「写坏了会怎样」的对照；
  // 而它读的是**环境**（ps/pgrep、文件时间），所以测试必须自己控制这两种输入（PATH 里塞
  // pgrep 桩、给夹具 mtime 打标），否则这条回归只在别的会话恰好空闲时才绿——靠运气绿的回归
  // 会被下一个人删掉。跑的就是生产脚本本体（HF_SOURCE_ONLY=1 source 进来逐条调），不是副本。
  ["Render serialization", "scripts/tests/test-render-serialization.py"],
]) {
  if (!fs.existsSync(path.join(ROOT, testFile))) {
    throw new Error(`${testFile} 不见了 —— ${label} 回归是上面那处修法的唯一机器可读形式`);
  }
  if (run("python3", [testFile], ROOT, { stdio: "inherit" }).status !== 0) {
    throw new Error(`${label} 回归未通过——合成形状退了，先别改产物（压暗画面不算修）`);
  }
}
// 这些回归必须判在**真东西**上：用例得把亮度序列灌进门禁自己的 flickers()/main()（阈值当天被改过
// 两轮，测试里抄一份比较就会跟着漂移），并静态扫 scripts/create-episode-preview.mjs 本体而不是
// 某个已经落盘的 index.html 副本——共享层改了而生成器没改，下一期照样烧回旧像素。
for (const [testFile, tokens] of [
  ["scripts/tests/test-dissolve-dip.py", ["check-luminance-flicker.py", "create-episode-preview.mjs"]],
  ["scripts/tests/test-hook-seam-fade.py", ["check-luminance-flicker.py", "intro-template.json",
    "render_scene_map_template_fallback.py", "render-episode-final.mjs"]],
  ["scripts/tests/test-dissolve-undershoot.py", ["check-luminance-flicker.py",
    "--band-rows", "UNDERSHOOT_REL", "held_levels"]],
  // 竞态守卫本体在**驱动脚本**里，所以这条回归必须引用 render-hook-batch.sh：它一旦只测自己的
  // 那个 CLI，驱动被改回「渲完就用」也没人知道。
  ["scripts/tests/test-composition-digest.py", ["composition-digest.py", "render-hook-batch.sh"]],
  // 归因表一旦不再调门禁自己的 judged_segments()/dissolve_excursions()，它就变成「自己抄一份
  // 阈值」——判据⑥那次就是这么瞎的：门禁判越带、归因只量 ①~⑤，27 部红没有归属。
  ["scripts/tests/test-flicker-attribution.py", ["check-luminance-flicker.py",
    "intro-template.json", "check-episode-delivery.py", "judged_segments",
    "dissolve_excursions", "frame_curves"]],
  // 片头原样门的对照必须调门禁自己的 check_one()/raw_page_levels()：合成板子的电平一旦
  // 由测试自己算，门禁换了基准（比如改用别的底图目录）测试就成了自证。
  ["scripts/tests/test-intro-is-original.py", ["check-intro-is-original.py",
    "check_one", "raw_page_levels", "hook-voiceover-story.mp3", "color_range"]],
  // 字体门的两端都必须被夹具点到：face 解析（title_font.py 的 index 反查）和渲染时证人
  // （title-meta.json）。少了后者，测试就只在比对 lib 和 lib，而这条规则要防的恰恰是
  // 「lib 改了、成片还是旧字体」；少了 forbidden_family_markers，繁体 face 的对照就没人要求了。
  ["scripts/tests/test-title-font.py", ["check-title-font.py", "title-font.json",
    "title-meta.json", "forbidden_family_markers", "render_scene_map_template_fallback.py",
    "create-episode-preview.mjs", "STHeiti"]],
  // 伪影这条规则曾经「只写在 playbook 里，没有任何调用点」。所以这条回归必须同时点名扫描器、
  // 驱动、驱动传给扫描器的那份**本期 index.html**（不是某个死表），以及豁免的唯一形状来源
  // design_events()/刀口认定；少了 composition-digest.py 这个参照，它就不再看「扫描必须发生在
  // 落账本之前」，而带伪影的产物留下 provenance 等于给脏像素作过证。
  ["scripts/tests/test-render-artifacts.py", ["check-render-artifacts.py", "render-hook-batch.sh",
    "--composition", "$dir/index.html", "design_events", "KNIFE_SPAN_MAX",
    "composition-digest.py"]],
  // 封面这条链的失败面全在「像素与 lib/证人之间」，所以夹具必须同时点名三端：门禁本体、
  // lib 的 overlay/时序（core_mask 与 timing 各是被否决过一次的形状）、渲染器写下的那份证人
  // 字段（bridge_exit_seconds 是「本期桥接卡多长」的钉子，overlay_sha256 是「换书不换形状」的
  // 唯一解法），以及那三种红话各自的名字——红话一合并，手指就指向错误的那一边。
  ["scripts/tests/test-book-cover-overlay.py", ["check-book-cover-overlay.py", "book_cover",
    "overlay_sha256", "bridge_exit_seconds", "timing", "card_box", "provenance_ok",
    "烤进 body-bg", "不是这本书的封面", "溯源不通过", "hook-dirty", "涟漪收口",
    // 2026-10-09：动效形状现在由这一套夹具持有（证 D 在成片像素上反解 ŝ）。缺了下面这几个名字，
    // 「轨迹」那一半就可以整段删掉而这里照绿——而 test-cover-motion-smoothness.py 守的是 lib
    // 画出来的帧，答不了「这一版成片烘的是哪条 ramp」。
    "compose_ramp", "integer_overlay", "zoom_at", "动效轨迹与 lib 不符", "上一版动效形状",
    "越出了 slot 允许的那条带", "没在成片里长开", "根本没有缩放", "顶在搜索网格端点"]],
  // 动效这条链的三端都必须被夹具点到：lib 的两个函数（zoom_at 定节奏、zoomed_overlay 定装置）、
  // 被否决的整数形状（它得留在测试里当反面，否则「回退 0 处」就退化成「量不到东西」）、以及
  // 包络/下限各自的红话名字。少点一个，比如哪天有人把对照组成整数装置删了图省事，
  // 这条回归就只剩「新形状自己和自己一致」，而它守的恰恰是两种形状之间的差别。
  ["scripts/tests/test-cover-motion-smoothness.py", ["book_cover", "zoom_at", "zoomed_overlay",
    "motion_envelope", "integer_overlay", "pace_min_px_per_second", "_assert_clear_of_reserved_bands",
    "回退", "check-water-ripple.py", "check-book-cover-overlay.py",
    "render_scene_map_template_fallback.py"]],
  // 片头书名↔封面配对判据只有一个真源（scripts/lib/intro-page-pairing.mjs）：测试必须 import
  // 它而不是抄一份正则（抄来的那份测的是副本），而且 13:13 那次的真实页序要一直留在测试里
  // 当反例，否则这条断言会随时间退化成「只测健康」。
  ["scripts/tests/test-intro-page-pairing.mjs", ["intro-page-pairing",
    "checkPairing", "default-book-list.json", "result.png", "04/05/02/06/01/03"]],
  // 段归属这条链上四个环节都必须被测试点名：lib 的 resolver、锁清单的脚本、按条目路由的 fetch、
  // 以及那个把整份清单交给 fetch 的钩子准备脚本。少点一个，比如哪天 prepare-hook-segment.sh
  // 去掉 --segment=hook，测试照样全绿而事故会照原样复发。
  ["scripts/tests/test-segment-routing.py", ["episode_paths", "segment_subdir", "dirs_for",
    "lock-pexels-candidates.py", "fetch-pexels-by-id.py", "prepare-hook-segment.sh",
    "--segment=hook", "ONLY_SEGMENT", "body_images_subdir"]],
  // 重放档位这条链的两个环节都必须被测试点名：fetch 当场写下源图宽、重放按记录去问。
  // 少点一个（比如哪天 certify 又不再传 source_width），测试照样全绿而假红会照原样复发。
  ["scripts/tests/test-replay-width.py", ["replay_width", "source_width", "fetch_bytes",
    "crop_3x4", "verify-pexels-fetch.py", "fetch-pexels-by-id.py", "不是同一次缩放"]],
  // 取帧口径这条链也是两环：门禁自己按帧号取、包装器把成片与正文背景的帧数差喂进去。
  // 少点一个（比如哪天 --pre-body-frames 又不传了，或 seek 退回 .4f），测试就只在测自己的夹具。
  ["scripts/tests/test-caption-frame-sampling.py", ["qc-caption-alignment.py",
    "check-episode-delivery.py", "seek_time", "round(t * fps)", "--pre-body-frames",
    "ffprobe_frames", "stream=nb_frames"]],
  // 封面「一律单行 + 缩小字号」这条链有三环都必须被测试点名：出图口的阶梯（wrap_title_lines
  // 只有单行那一支）、全量回归里的四条判红（折行 / 下限返回值 / 留痕 / 绝对下限）、以及
  // 那个回放老阶梯的对照。少点一个——比如哪天留痕那条红被删掉而测试还在只测折行——测试就
  // 只在测自己的夹具。判红的**措辞**也在这里钉：改了措辞不改这条，钉子就在钉一个不存在的字符串
  // （2026-10-09 就是因为「单行装得下却折了行」改成了「书名折了行」而钉子滞后一天，
  // 又被 npm run check 的 fail-fast 挡在上面看不见）。
  ["scripts/tests/test-cover-one-line-title.py", ["make_episode_cover.py",
    "check-cover-wrapping.py", "ONE_LINE_MIN", "wrap_title_lines", "old_ladder",
    "书名折了行", "TITLE_ABSOLUTE_MIN", "TITLE_LEGIBLE_MIN", "阶梯交回的下限",
    "低于清晰下限却没有留痕警告", "字号掉到绝对下限以下", "改名不是出路",
    "resolve_cover_title"]],
  // 段级版本这条链有四个环节都必须被测试点名：两份解析（mjs/py）、两个调用点（建轴/补 coverage）。
  // 少点一个——比如哪天 snap 又不调 resolve_segment_version 而退回 brief.scriptVersion——测试就只在
  // 测两份 lib 互相抄，而真正的失败面是钩子轴整段建不出来（《围城》2026-10-07）。
  ["scripts/tests/test-segment-script-version.py", ["script-version.mjs", "script_version.py",
    "resolveSegmentVersion", "resolve_segment_version", "create-body-timings-precise.mjs",
    "snap-body-timings-vad.py", "A_reference_like", "scriptVersion"]],
  // 渲前轴闸这条链有三个环节，少点一个测试就只在测自己的夹具：
  //   ① 批渲染脚本必须真的调这道闸（不调 = 第五批那三期照原样花十分钟再翻红）；
  //   ② 入口必须**复用**三道既有对账而不是自己重述一遍阈值（重述 = 第四个问「轴对不对」
  //      却和第七道门口径不同的地方，本仓库为这类分叉翻过无数次车），并点名修轴次序；
  //   ③ 测试自己必须跑 --no-repair 的红绿两头、脏化和硬链夹具。
  ["scripts/render-hook-batch.sh", ["check-axis-before-render.py", "axis_guard"]],
  ["scripts/check-axis-before-render.py", ["create-body-timings-precise.mjs", "--verify",
    "check-caption-speech-align.py", "audit-caption-axis.py", "snap-body-timings-vad.py",
    "行归属没有漂移", "判红", "residual", "snap --apply → audit --apply ×2"]],
  ["scripts/tests/test-axis-guard.py", ["check-axis-before-render.py", "--no-repair",
    "doctor", "cp -al", "token 派生应保持绿", "body-timings.json"]],
  // 渲前串行化这条链的判据全部住在**驱动脚本**里（忙判据、静默窗、锁、缓存目录、旁路理由），
  // 所以测试必须点名被 source 的那个文件本身：它一旦只测自己 clone 的一份副本，驱动被改回
  // 「上来就 npx」也不会有人知道——那正是 13:13《昆虫记》烧掉六次 attempt 的形状。
  // 后半截是五条退路各钉一条：它们分别是「没有证据时不许悄悄放行」「锁被活人占着要报出 pid」
  // 「陈旧锁要能被接管」「真源缺席要拦住」「封层三方不一致要拦住」这五种红的输出名，外加
  // 两个**环境控制装置**的名字（pgrep 桩 + mtime 打标）。少了装置，这条回归就退回「只在机器
  // 恰好空闲时才绿」，而靠运气绿的回归会被下一个人当噪音删掉。
  ["scripts/tests/test-render-serialization.py", ["render-hook-batch.sh", "settle_before_render",
    "others_rendering", "cover_layer_ready", "render_lock_acquire", "render_lock_run",
    "npm_config_cache", "ZQ_PGREP_TABLE", 'errors="replace"', "stamp_quiet", "HF_SETTLE_BYPASS",
    "接管陈旧锁", "没拿到渲染锁", "有他期渲染在跑", "共享真源缺席", "封层真源", "锁被占",
    "ps -o args", "HF_SOURCE_ONLY"]],
  // 驱动这一侧：五种量必须还在同一个文件里被读。判据没有第二个真源，也就不许有第二个地方
  // 用别的措辞问「现在能不能开渲」——旁路的门槛（写理由）也一起钉住，漏字等于把例外搬回对话里。
  // 最后一组 `render_lock_run` / `check.mjs`：锁的覆盖面不止 `npm run check` 里那句 npx——
  // check 侧那三个 `npx --yes hyperframes@…` 碰的是**同一棵** _npx 树，只在渲染侧排队等于修了
  // 一半（一边排队、一边直冲，13:13《昆虫记》照原样撞车）。
  ["scripts/render-hook-batch.sh", ["settle_before_render", "others_rendering",
    "cover_layer_ready", "render_lock_acquire", "render_lock_release", "render_lock_run",
    "npm_config_cache", "ps -o comm=", "HF_SETTLE_BYPASS", "BYPASS_MIN_REASON_CHARS"]],
  ["scripts/check.mjs", ["runLocked", "render_lock_run", "HF_LOCK_MAX_WAIT"]],
  // token ch 这条链的四个环节都必须被测试点名：Node 侧那两处 trim（取字符 / 跳过纯空白）、
  // 它对齐到的 Python 侧入口，以及两侧共同依据的控制 token 形状。少点一个——比如哪天
  // trim 挪进了别处、或 Python 侧的 clean() 不再删空白——测试就只在测自己的字符串，
  // 而失败面还是一个正确的轴被第 5 道判成行归属漂移。
  ["scripts/tests/test-token-whitespace-char.py", ["create-body-timings-precise.mjs",
    "canonDigits(t.trim())", "!t.trim()", "asr_char_stream", "CONTROL_TOKEN",
    "交付轴与 token 重算轴一致"]],
  // 行首回收这一支的五个环节都必须还被点名：不猜条件（ga === gs，抄自 Python 侧 align()）、
  // 「无人争抢」前提（上一行末字自己就是直接命中）、上一行的字符区间、悬空 token 的计数口径、
  // 以及真正被采用的那个候选 token。少点一个，比如有人把 `ga === gs` 当「多余的保险」删掉，
  // 测试就只在测自己的字符串，而失败面是两面的：本期照样红 + 16 期已交付轴被新判红。
  // 测试那一侧额外钉 shutil.copytree / cp -al：这次事故不是判据漂移，是**夹具写穿了供体**，
  // 而供体是下一期建轴要读的取证文件。
  // 操作数顺序以生产源码为准（2026-10-10：源码是 ga === gs，本钉子曾写成 gs === ga，
  // 于是整套 npm run check 死在这条空断言上，后面Registered loop 与 hyperframes 三件套
  // 因 fail-fast 全部没跑到；下面那条守卫自证用的本来就是 ga === gs，两处必须一致）。
  ["scripts/create-body-timings-precise.mjs", ["ga === gs", "direct[lastCharOfPrev]",
    "scriptCum[index - 1]", "firstDirectTok - prevTokIdx - 1", "asr.tokens[prevTokIdx + 1]"]],
  ["scripts/tests/test-verify-head-dangling.py", ["create-body-timings-precise.mjs",
    "shutil.copytree(src / \"audio\" / \"asr\"", "cp -al", "drop_asr_tokens",
    "donor_untouched", "OWN_ONSET", "PARKED_ONSET", "PARKED_DELTA"]],
]) {
  const dipSrc = fs.readFileSync(path.join(ROOT, testFile), "utf8");
  for (const token of tokens) {
    if (!dipSrc.includes(token)) {
      throw new Error(`${testFile} 不再引用 ${token} —— 判据/生成器换了地方，这条回归就成了空断言`);
    }
  }
}
// ── 行首回收的守卫自证（2026-10-10）──────────────────────────────────────
// 上面那条只是「文本里还有这几个字」；这一条要回答的是「这几个字还在**做判断**」。
// 回收块有两个被否决的近邻形状，都是这几天真跑过的：
//   · 无条件回收（第一版探针）：只看「上一行末字是直接命中 + 中间有悬空 token」就把本行行首
//     挪过去。157 期 A/B 里 16 期已交付轴被点红（女儿红 max 0.65→4.61），因为文案与录音不同字
//     的长串悬空 token 被划给了下一行——不猜条件 gs === ga 就是为它加的。
//   · 读交付轴：回收的目标若从「本行自己的开口」换成「交付轴写的那个数」，判据就用自己的答案
//     出题，永远绿。
// 于是自测两头：把不猜条件退掉，谓词必须松手；把整块删掉，谓词也必须松手。
{
  const witnessFile = "scripts/create-body-timings-precise.mjs";
  const code = codeOnly(witnessFile);
  const hasReclaim = (text) => text.includes("const ga = firstDirectTok - prevTokIdx - 1;")
    && text.includes("if (gs > 0 && ga === gs &&")
    && text.includes("firstIdx = prevTokIdx + 1;");
  if (!hasReclaim(code)) {
    throw new Error(`${witnessFile} 里那段「行首按位对位回收」不再是这个形状：行首被误听的行会`
      + "把正确的交付轴判成行归属漂移（《高老头》行 117 实测 -1.59s），而渲前轴闸会在渲染之前"
      + "整整把它拦下来——两个能量证人都报 0 行红，没有人会去问证人自己");
  }
  const noGuard = code.replace("if (gs > 0 && ga === gs &&", "if (gs > 0 &&");
  if (noGuard === code) {
    throw new Error("行首回收的自测无法构造「无条件回收」诱饵：生产源码里那句不猜条件换了写法，"
      + "下面这条断言就成了空转（换写法可以，但必须同时把这条自测改到新形状上）");
  }
  if (hasReclaim(noGuard)) {
    throw new Error("行首回收的守卫认不出「无条件回收」：把 gs === ga 删掉仍然算命中，"
      + "这道守卫拦不住 2026-10-10 第一版那个把 16 期已交付轴判红的形状");
  }
  const withoutBlock = code.replace("firstIdx = prevTokIdx + 1;", "firstIdx = firstDirectTok;");
  if (hasReclaim(withoutBlock)) {
    throw new Error("行首回收的守卫认不出「回收被改成直接采信悬空段末」：删掉真正被采用的那行仍然"
      + "算命中，说明三个 token 里有一个人只写在注释里");
  }
}
// 两份解析必须各自被调用点接上，且「整期版本」这个歧义源只能出现在被复核的那一步里。
// 光有 lib 不算修好：《围城》那次是 build 用 brief 的 B 去过滤 script-hook.csv 的 A 行，
// 抛 `No script rows found for version B`；就算 build 过了，snap 也会过滤出 0 行再在
// matched/total 上除零。所以两侧都必须走 resolve_segment_version。
// 2026-10-08 11:25 同一个缺陷从第三个调用点冒出来：create-episode-preview.mjs 的钩子段
// 也按整期版本过滤 script-hook.csv，围城因此 rc=1 死在「预览目录没生成：''」，报错只说
// 找不到行，不说版本串了段。渲染器是这三条里最后一条，少接线就还留着一条能整期失败的路。
for (const [file, tokens] of [
  ["scripts/create-body-timings-precise.mjs", ["resolveSegmentVersion", "script-hook.csv"]],
  ["scripts/snap-body-timings-vad.py", ["resolve_segment_version", "script-hook.csv"]],
  ["scripts/create-episode-preview.mjs", ["resolveSegmentVersion", "script-hook.csv", "hookVersion"]],
  ["scripts/lib/script-version.mjs", ["resolveSegmentVersion", "A_reference_like", "scriptVersion"]],
  ["scripts/lib/script_version.py", ["resolve_segment_version", "A_reference_like", "scriptVersion"]],
]) {
  const source = fs.readFileSync(path.join(ROOT, file), "utf8");
  for (const token of tokens) {
    if (!source.includes(token)) {
      throw new Error(`${file} 不再引用 ${token}：段级版本的一道防线被静默拆掉`
        + "（正文是 B 版的期，钩子轴会整段建不出来，而报错说的是「找不到行」不是「版本串了段」）");
    }
  }
}
// 回滚之后共享片头的**素材**也是产物的一部分：七张底图必须是未调色的原图。
// 这条是实测不是 grep——10-04 那版把底图归一到 155~185 亮带，靠 grade-template-pages.py
// 原地改写 media/ 下的 jpg，一次「顺手重跑调色脚本」就会把用户拒绝过的那种片头亮度带回来，
// 而所有 grep 型守卫照样全绿。判据：与 _raw/ 逐字节相同（_raw 是调色脚本自己的备份，
// 也是这次还原的来源）。result.png 不在这里比——它每期由本期 result-bridge 覆盖生成。
const templateMediaDir = path.join(ROOT, "templates", "shared-video-template", "intro", "media");
const md5 = (p) => crypto.createHash("md5").update(fs.readFileSync(p)).digest("hex");
for (const rel of ["intro-background.jpg", "pages/scroll-01.jpg", "pages/scroll-02.jpg",
  "pages/scroll-03.jpg", "pages/scroll-04.jpg", "pages/scroll-05.jpg", "pages/scroll-06.jpg"]) {
  const shipped = path.join(templateMediaDir, rel);
  const raw = path.join(templateMediaDir, "_raw", rel);
  if (!fs.existsSync(shipped) || !fs.existsSync(raw)) {
    throw new Error(`共享片头底图缺一半：${rel}（shipped 或 _raw 不在）——`
      + "素材是否原样就无从对账，这条守卫会退化成空断言");
  }
  if (md5(shipped) !== md5(raw)) {
    throw new Error(`共享片头底图 ${rel} 不再是原图（与 _raw/ 不同）：这是 2026-10-04 那版亮带归一。`
      + "用户 2026-10-05 裁决恢复原效果——跑 python3 - <<'PY' 之类的手把手式还原不如直接"
      + " cp templates/shared-video-template/intro/media/_raw/" + rel
      + " templates/shared-video-template/intro/media/" + rel);
  }
}
// ── 共享片头模板本体：必须就是 2026-10-03 交付时那一份 ──────────────────────────
// 2026-10-05 用户裁决「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」，在追问里选了
// 「三处全回滚（视觉 100% 回到从前）」。片头侧的三处改动（底图归一到 155~185 亮带、书单→结果页
// 摊成 0.50s 溶解、六张书封改 3x2 累积卡片）全部撤销。这里钉的是**回滚之后的形状**，因为
// 「顺手再把闪屏修一下」的诱惑恰恰就是这版被否掉的理由——被否的不是观感算术，是取舍：
//   · .page 是整幅图层（inset:0 / z-index:4）：一页盖一页就是原效果；
//   · .template-blackout 仍被时间轴驱动：书单→结果页那一拍的下探全黑是设计（实测 86→0→44），
//     第 10 道门对片头窗只报告不判红（见 check-luminance-flicker.py 的豁免窗）；
//   · 亮度只由 CSS 承担（.image-piece 的 brightness(1.08) + ::before 的 screen 0.23），
//     素材必须与 _raw/ 逐字节相同——上面那条实测守的就是这件事。
// 卡片版式那套（格子尺寸、前一页不退场、fitLabel 排版阶梯、page_canvas/--measure 可见窗口径、
// HANDOFF 跑道、grade-template-pages.py）随这次回滚整份退役，旧件在 tmp/retired-20261005/。
const introHtmlSrc = fs.readFileSync(
  path.join(ROOT, "templates", "shared-video-template", "intro", "index.html"), "utf8");
const pageRule = introHtmlSrc.match(/\n\s*\.page\s*\{([^}]*)\}/);
if (!pageRule || !/inset\s*:\s*0/.test(pageRule[1])) {
  throw new Error("index.html 的 .page 不再是整幅图层（inset:0）：片头版式被动过，"
    + "而用户 2026-10-05 要的正是回滚前那版整幅书封");
}
if (/\.page\.candidate\s*\{/.test(introHtmlSrc)) {
  throw new Error("index.html 里又出现了 .page.candidate 卡片规则：3x2 累积网格是 2026-10-05 被"
    + "撤回的那版开场白，它把 MAD 判据喂绿了，代价是观众再也不认识的那个片头");
}
// 页序＝书单序，第 n 页的书名/作者必须压在**那一本书自己的封面**上。
// 六张底图不是氛围板：scroll-04 上印着「悲惨世界 [法]雨果」，scroll-05 印着昆德拉
// 「不能承受的生命之轻」，而每页顶上的 .page-title 来自 default-book-list.json 的第 n 条。
// 2026-10-05 13:13 这里为「可见窗均亮度单调下降」把底板重排成 04/05/02/06/01/03，亮度门全绿，
// 观众看到的是「《奥德赛》」压在昆德拉封面上——四期成片（13:19 人间草木 / 13:32 解忧杂货店 /
// 13:37 幽谷百合 / 13:42 曼斯菲尔德庄园）的第一页全错。第 11 道门量的是亮度电平，看不见配对；
// 配对只能在这里钉死：DOM 第 n 格必须引用 scroll-0n。
const bookList = JSON.parse(fs.readFileSync(
  path.join(ROOT, "templates/shared-video-template/intro/default-book-list.json"), "utf8"));
// 判据本体住在 scripts/lib/intro-page-pairing.mjs：那份回归测试
// （scripts/tests/test-intro-page-pairing.mjs）要能当场验「对调两格必须翻红」，而 check.mjs
// 在 2026-10-05 下午会先在「grade-template-pages.py 还在」那条 throw（并行会话把它加回来了），
// 后面的守卫一条都跑不到——判据只有抽成入口，才测得到它自己在跑。
const { checkPairing } = await import("./lib/intro-page-pairing.mjs");
const pairing = checkPairing(introHtmlSrc, bookList);
for (const problem of pairing.problems) {
  throw new Error(`片头书名↔封面配对：${problem}`);
}
// 交付路径也不许把判据内联回来：测试测的是 lib 那一份，这里改成手写正则的话，
// 绿灯测的是一份、红的判的是另一份，而 13:13 那种错配恰恰只在其中一份里存在。
if (!fs.readFileSync(path.join(ROOT, "scripts/check.mjs"), "utf8")
  .includes('await import("./lib/intro-page-pairing.mjs")')) {
  throw new Error("check.mjs 不再从 scripts/lib/intro-page-pairing.mjs 读片头配对判据：判据有了第二份，"
    + "test-intro-page-pairing.mjs 测的就不是交付路径实际在用的那一条");
}
// 那一次「故意保留的短黑场」是原效果的一部分。停掉它＝把用户否掉的版本改回来，
// 所以这里反过来要求时间轴**必须**还在驱动 .template-blackout。
if (!/tl\.[a-zA-Z]+\(\s*["']\.template-blackout["']/.test(introHtmlSrc)) {
  throw new Error("index.html 的时间轴不再驱动 .template-blackout：书单→结果页那一拍的眨眼"
    + "是 2026-10-03 交付效果的一部分，抹掉它就违反了 2026-10-05 的回滚裁决");
}
// 原版高光/亮度全在 CSS 上。这两处一丢就说明有人把亮度从 CSS 搬回了底图（=归一亮带那版）。
for (const [shape, why] of [
  [/brightness\(\s*1\.08\s*\)/, ".image-piece 的 brightness(1.08)"],
  [/opacity:\s*0\.23/, ".image-piece::before 那层 screen 高光的 opacity 0.23"],
]) {
  if (!shape.test(introHtmlSrc)) {
    throw new Error(`index.html 少了 ${why}：片头亮度只有一个真源（这层 CSS + 未调色底图），`
      + "改成给底图调亮度就是被撤回的那版亮带归一");
  }
}
// 全幅底图的退场动画**必须还在**：2026-10-04 那次「修闪屏」把 .hero-full / .piece-stage 的
// opacity→0 摘掉过（理由：书页是 z4 全幅图层，一亮起就把这两层整张盖住，退场在画面上一帧
// 收益都没有，只剩三层连乘的凹陷 189→159→170）。2026-10-05 用户裁决「三处全回滚（视觉 100%
// 回到从前）」后退场动画保留、退场**时刻**也回到原值（.hero-full 1.08 / .piece-stage 1.02）。
// 2026-10-06 15:55 有会话以「用户裁决『重新落上修法』」为注释把 .hero-full 挪到 1.17；那句话在
// 两个会话日志根（~/.qoder 当日 460 条 user 消息、~/.qoder-cn）里都查不到任何用户原话，只有一份
// assistant 的交付汇报提过它，而用户 15:24 已再次确认维持 10-05 裁决——所以这里钉的是原值，
// 这段注释里也不许再写「10-06 授权」。
// 这条反向守卫防的是「把退场动画整个摘掉」，同时把**时刻**钉死：摘掉会红，往后挪 0.01s 也会红。
for (const [layer, at] of [[".hero-full", 1.08], [".piece-stage", 1.02]]) {
  const sel = layer.replace(".", "\\.");
  const off = new RegExp(`tl\\.[a-zA-Z]+\\(\\s*["']${sel}["']\\s*,\\s*\\{[^}]*opacity:\\s*0[^}]*\\}\\s*,\\s*${String(at).replace(".", "\\.")}`);
  const hit = introHtmlSrc.match(off);
  if (!hit) {
    throw new Error(`index.html 不再在 ${at}s 把全幅底图 ${layer} 淡到 0：这是 2026-10-03 原效果的退场动画，`
      + "摘掉它就是 2026-10-05 被撤回的那版片头改动。凹陷要治只许治在钩子那一路（段尾淡出 / "
      + "create-episode-preview.mjs 的溶解形状），片头窗现在是「只报告不判红」");
  }
  // 反向守卫（「必须存在」）最容易悄悄变成永真，所以这里当场把三种「顺手把凹陷修一下」的改法
  // 灌进同一条正则判一遍：任何一次仍然命中，就说明这条断言什么都没测。放在守卫内部而不是
  // 另写一份测试，是为了让形状只有一份真源——复制一份正则去测它，测的就是那个副本。
  const atStr = String(at).replace(".", "\\.");
  const nudged = (at + 0.01).toFixed(2);
  const doctored = [
    ["退场改成不透明（opacity: 1）", introHtmlSrc.replace(hit[0], hit[0].replace(/opacity:\s*0/, "opacity: 1"))],
    ["整条 tween 删掉（2026-10-04 那版的形状）", introHtmlSrc.replace(hit[0], "")],
    [`起点时刻挪开（${at}s → ${nudged}s）`,
      introHtmlSrc.replace(hit[0], hit[0].replace(new RegExp(`,\\s*${atStr}$`), `, ${nudged}`))],
  ];
  for (const [name, text] of doctored) {
    if (off.test(text)) {
      throw new Error(`${layer}@${at} 的退场守卫在「${name}」这种改法上仍然命中：反向守卫是空的，`
        + "它拦不住下一次「顺手修凹陷」");
    }
  }
}
// 六页滚动的**换页形状**同样是 2026-10-03 交付效果的一部分，而它此前一条守卫都没有：2026-10-06
// 15:55 那次把后五页的入场从「0.09s 三帧急换」摊成 PAGE_STEP(0.175s) 匀速溶解、把前一页的退场从
// 「后一页亮起 +0.09s」推到「+PAGE_STEP」、把书名/作者落点各后移 0.06s，`npm run check` 一声不响
// 就放过去了——因为它改的正好是第 10 道门**豁免**的那扇窗，而豁免是用户 10-05 裁决要的取舍，
// 不是给片头改动开的后门。豁免的是判决范围，不是形状本身。这里把四个数按死，并且当场把
// 「15:55 那一版」灌回同一批正则验一遍非空转（反向守卫写成人人永真太容易了）。
const flipShapes = [
  [/duration:\s*0\.09,\s*ease:\s*"power2\.out"/,
    "每页入场 duration 0.09 / power2.out（三帧急换）",
    'duration: index === 0 ? 0.09 : PAGE_STEP, ease: index === 0 ? "power2.out" : "none"'],
  [/var outAt = hasNext \? 1\.08 \+ \(index \+ 1\) \* PAGE_STEP \+ 0\.09\b/,
    "前一页在后一页亮起 +0.09s 之后才退",
    "var outAt = hasNext ? 1.08 + (index + 1) * PAGE_STEP + PAGE_STEP"],
  [/start \+ 0\.025/, "书名落点 start + 0.025", "start + 0.085"],
  [/start \+ 0\.075/, "作者落点 start + 0.075", "start + 0.13"],
];
for (const [re, why] of flipShapes) {
  if (!re.test(introHtmlSrc)) {
    throw new Error(`index.html 的滚动书单少了「${why}」：整幅换页那一拍是 2026-10-03 的效果，`
      + "摊成溶解就是 2026-10-05 被用户撤回、2026-10-06 又以一条查无实据的「用户裁决」重新落上的那版。"
      + "抗闪屏只许落在钩子段与装配层的钩子侧（见 AGENTS.md 的 10-05 裁决条）");
  }
}
for (const [re, why, withdrawnShape] of flipShapes) {
  if (withdrawnShape && re.test(introHtmlSrc.replace(re, withdrawnShape))) {
    throw new Error(`换页形状守卫对「${why}」仍然命中被撤回的写法（${withdrawnShape}）：`
      + "这条断言是空的，拦不住下一次「重新落上修法」");
  }
}
// 画布尺寸仍是水波/涟漪/裁切共用的 720x960 基准（page_canvas 这个键随归一一起退役了，
// 但画布本身仍是所有下游窗口的口径，所以这里继续和 index.html 对账）。
const canvasAttr = introHtmlSrc.match(/<main[^>]*data-width="(\d+)"[^>]*data-height="(\d+)"/);
if (!canvasAttr || Number(canvasAttr[1]) !== 720 || Number(canvasAttr[2]) !== 960) {
  throw new Error(`index.html 的画布不再是 720x960（实测 ${canvasAttr ? `${canvasAttr[1]}×${canvasAttr[2]}` : "无"}）：`
    + "水波窗口、涟漪圆心、氛围图裁切都按这块画布算，改它必须一起改那些消费者");
}
// 结果页推镜与兜底渲染器的桥接静止倍率必须同源：index.html 把 .target .page-bg 在
// result_page_at 起用 1.87s 推到 1.035，而 render_scene_map_template_fallback.py 的
// BRIDGE_ZOOM 取的是**同一时刻**的倍率，好让片头末帧与正文首帧是同一张结果桥接图。
// 改了模板忘了改渲染器，片头→正文那道缝就重新跳（阈值 12；《偏跟山过不去》硬切那次 26）。
const zoomM = introHtmlSrc.match(
  /\.target \.page-bg["']\s*,\s*\{\s*scale:\s*([\d.]+)\s*,\s*duration:\s*([\d.]+)[^}]*\}\s*,\s*([\d.]+)/);
if (!zoomM) {
  throw new Error("index.html 里 .target .page-bg 的推镜不再是「{scale, duration} , 起点」这一条："
    + "BRIDGE_ZOOM 无从对账，片头→正文接缝的静止倍率会各算各的");
}
if (Math.abs(Number(zoomM[3]) - Number(introTiming.result_page_at)) > 1e-6) {
  throw new Error(`index.html 结果页推镜起点 ${zoomM[3]}s 与 ${introTimingFile} 的 `
    + `result_page_at ${introTiming.result_page_at}s 不一致：trim 点、水波窗口、字幕取点都读 lib 那个数`);
}
const bridgeSrc = fs.readFileSync(
  path.join(ROOT, "scripts/render_scene_map_template_fallback.py"), "utf8");
const bridgeM = bridgeSrc.match(
  /_intro_u\s*=\s*[^/\n]*\(\s*INTRO_TRIM\s*-\s*([\d.]+)\s*\)\s*\/\s*([\d.]+)/);
if (!bridgeM) {
  throw new Error("兜底渲染器不再按 (INTRO_TRIM − 结果页起点)/推镜时长 取桥接倍率："
    + "它与 index.html 的推镜是同一个数的两份写法，必须一起改、一起核");
}
if (Math.abs(Number(bridgeM[1]) - Number(zoomM[3])) > 1e-6
    || Math.abs(Number(bridgeM[2]) - Number(zoomM[2])) > 1e-6) {
  throw new Error(`BRIDGE_ZOOM 的时刻 ${(bridgeM[1])}s/${(bridgeM[2])}s 与 index.html 推镜 `
    + `${zoomM[3]}s/${zoomM[2]}s 对不上：片头定格那一帧的倍率算错，接缝就会重新跳`);
}
const ampM = bridgeSrc.match(/BRIDGE_ZOOM\s*=\s*1\.0\s*\+\s*([\d.]+)\s*\*\s*ease_in_out/);
if (!ampM || Math.abs(Number(ampM[1]) - (Number(zoomM[1]) - 1.0)) > 1e-6) {
  throw new Error(`BRIDGE_ZOOM 的推镜幅度 ${ampM ? ampM[1] : "读不到"} 与 index.html 的 `
    + `scale ${zoomM[1]} 对不上：同上，两条路径必须量同一个动作`);
}

const templateSourceDir = path.join(ROOT, "templates", "shared-video-template", "intro");
const sharedTemplateDir = path.join(ROOT, "templates", "shared-video-template");
const templateDir = fs.mkdtempSync(path.join(os.tmpdir(), "book-video-hyperframes-check-"));
fs.cpSync(templateSourceDir, templateDir, { recursive: true });
// intro/index.html 引用 ./gsap.min.js；临时校验目录需同时提供该文件。
fs.copyFileSync(path.join(sharedTemplateDir, "gsap.min.js"), path.join(templateDir, "gsap.min.js"));
const resultPlaceholder = path.join(templateDir, "media", "pages", "result.png");
const resultSource = path.join(templateDir, "media", "intro-background.jpg");
const resultConversion = run("ffmpeg", [
  "-hide_banner", "-loglevel", "error", "-y", "-i", resultSource, "-frames:v", "1", resultPlaceholder,
]);
if (resultConversion.status !== 0) {
  fs.rmSync(templateDir, { recursive: true, force: true });
  throw new Error("Could not create temporary HyperFrames result placeholder");
}

try {
  for (const command of ["lint", "validate", "inspect"]) {
    const args = ["--yes", `hyperframes@${HYPERFRAMES_VERSION}`, command, "--json"];
    if (command === "validate") args.push("--no-contrast");
    if (command === "inspect") args.push("--at", "0.2,0.75,1.2,1.7,2.08,2.25,2.55,3.2,3.8,4.15");
    // 这三个 npx 和段渲染用的是**同一棵** `_npx` 安装树：不排队就会重现 13:13《昆虫记》那六次
    // ENOTEMPTY（两个会话各自不报错，只是永远渲不出文件）。锁的协议只有一份，见 runLocked()。
    const result = runLocked(`npm run check: hyperframes ${command}`, "npx", args, templateDir,
      { stdio: "inherit" });
    if (result.status === 75) {
      throw new Error(`HyperFrames ${command} 没拿到渲染锁（900s 内另有一个 npx hyperframes 在跑）—— `
        + "这不是模板有问题，是同一棵 _npx 树正被别的会话写着；等它结束再跑 check，"
        + "别把锁的等待时间调小来「过」这一步");
    }
    if (result.status !== 0) throw new Error(`HyperFrames ${command} failed`);
  }
} finally {
  fs.rmSync(templateDir, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// 渲染中间产物的回收（2026-10-11 新增）
// 这道门守的不是「画面对不对」，而是「磁盘有没有被暂存区吃光」。
//
// 一次正文渲染往 tmp/<书名>_tpl/ 落下 body.mp4 / body-bg.mp4 / sub.mov / cover-seq/ 逐帧 PNG
// 和每行一张的 cap-*.png，单期 180-585 MB。全部可由 episodes/<书名>/ 重建（该目录自己的
// docstring 就写着「渲染中间目录」）。实测 2026-10-11：31 期已交付的剧集压着 10.8 GB，
// render-hook-batch.sh 里唯一的 rm -rf 指向渲染锁，AGENTS.md 搜「清理」零命中，
// Delivery 阶段只要求把成片贴进对话——所以它从来没被回收过。
//
// 门禁查三件���，缺一不可：
//   ① 清理脚本存在且可执行语法检查，防止它在不在这个检查里退化掉；
//   ② 保留清单**必须**含字幕轴与 -sha 指纹。字幕轴是 check-episode-delivery.py 的
//      find_tmp_dir() 回落目标，删了它，下次重验交付时那几道字幕门禁会报
//      「找不到字幕轨」而不是报真问题。指纹是 -sha 不是 .sha（body.mp4.book-cover-sha
//      的真实后缀带连字符）——写成 .sha 会一个都匹配不到，幂等快路径被当中间件清掉；
//   ③ 保留清单**不得**含媒体中间件，否则这个脚本自己就变成了它要清理的东西。
const cleanupFile = "scripts/cleanup-tmp.mjs";
if (!fs.existsSync(path.join(ROOT, cleanupFile))) {
  throw new Error(`${cleanupFile} 不存在：渲染中间产物没有回收路径，tmp/*_tpl/ 会按单期 180-585 MB `
    + "持续堆积（2026-10-11 实测 31 期已积 10.8 GB）。见 AGENTS.md 的 Cleanup 阶段");
}
// 导入脚本里**真正跑的**保留判定，而不是在源码里 grep 字符串：grep 只能证明
// 「这段文字出现过」，证明不了「cover-seq/f_0000.png 会被删」。两边共用一个实现，
// 判定口径才不会各写一份然后漂（和 check-image-provenance.py 共用 crop_3x4() 同理）。
const cleanup = await import("./lib/cleanup-keep.mjs");
// 用真实的判定跑一遍这几类文件，证明「保的留下、该删的删掉」，而不只是证明代码存在。
for (const [rel, shouldKeep, why] of [
  ["caption-track.json", true, "字幕轴是交付门禁 find_tmp_dir() 的回落目标，删了重验交付会报「找不到字幕轨」"],
  ["body.mp4.book-cover-sha", true, "重渲幂等指纹；后缀是 -sha 不是 .sha，写成 .sha 一个都匹配不到"],
  ["body.mp4.book-cover-sha.txt", false, "指纹文件不带 .txt 后缀"],
  ["body.mp4", false, "正文中间件，可由 caption-track.json + images-body/ 重建"],
  ["body-bg.mp4", false, "背景中间件，可重建"],
  ["sub.mov", false, "字幕轨中间件，可重建"],
  ["cover-seq/f_0000.png", false, "逐帧 PNG 序列——31 期里最没必要保留的部分，必须递归删掉"],
  ["cover-seq/_blank.png", false, "同上，帧序列目录内的一切"],
  ["cap-000-1.png", false, "字幕位图，可重建"],
]) {
  const got = cleanup.isKeepTarget(rel);
  if (got !== shouldKeep) {
    throw new Error(`${cleanupFile} 的保留判定对 ${rel} 返回 ${got}，应为 ${shouldKeep}——${why}`);
  }
}
// 保留清单不得含媒体后缀，否则这个脚本自己就变成了它要清理的东西。
for (const name of cleanup.KEEP_EXACT) {
  if (/\.(mp4|mov|png|m4a|mp3)$/i.test(name)) {
    throw new Error(`${cleanupFile} 的 KEEP_EXACT 含媒体文件 ${name}：保留清单只放字幕轴、指纹和配置快照`);
  }
}
for (const suffix of cleanup.KEEP_SUFFIX) {
  if (/\.(mp4|mov|png|m4a|mp3)$/i.test(suffix)) {
    throw new Error(`${cleanupFile} 的 KEEP_SUFFIX 含媒体后缀 ${suffix}：保留清单只放字幕轴、指纹和配置快照`);
  }
}
// _raw 是 Pexels 原图：grading --restore 靠它复原，清掉等于不可逆地毁掉溯源链的一环。
if (!cleanup.PROTECTED.has("_raw")) {
  throw new Error(`${cleanupFile} 的 PROTECTED 丢了 _raw：它是 Pexels 原图，grade-atmosphere-images.py --restore 靠它复原`);
}

const modelPath = path.join(ROOT, "assets", "models", "whisper", "ggml-base.bin");
if (!fs.existsSync(modelPath)) console.warn("Warning: Whisper model is not installed. Run node scripts/download-whisper-model.mjs before timing voiceover.");
if (run("whisper-cli", ["--version"], ROOT, { stdio: "ignore" }).status !== 0) {
  console.warn("Warning: whisper-cli is not installed. Voiceover timing will not be available until it is installed.");
}
console.log("book-video checks: ok");
