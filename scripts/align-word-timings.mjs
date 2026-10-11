#!/usr/bin/env node
// 逐字时间轴对齐：把 script-body.csv（简体正文）的每个字，
// 用 LCS 强制对齐到 Whisper 词级 token 偏移（absolute ms）。
// 关键难点：Whisper 对中文常输出繁体（錢/會/過…）且偶有错字（刀封），
// 与脚本简体对不上。本脚本用「位置对应自动学出 繁→简 映射」：
//   先按最小长度做 1:1 位置配对，收集 (繁字→简字) 票数，取多数；
//   再用该映射做 LCS（匹配条件 = 简体字符 == map(繁字)），迭代两轮收敛。
// 这样繁简差异与少量错字都被位置配对吸收，逐字拿到真实词时间戳。
// 未匹配到的脚本字（ASR 漏识/多识）用相邻已匹配时间做线性插值兜底。
// 输出 audio/word-timings.json：{ byOrder: { "2": [{ch,start,end}, ...空格占位...], ... } }
// 空格为视觉换行占位（start/end 取前字 end），预览据此保留「用空格断句」的排版。

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { resolveScriptVersion } from "./lib/script-version.mjs";

const ROOT = process.cwd();
const [, , episodeName, requestedVersion] = process.argv;
if (!episodeName) {
  console.error("Usage: node scripts/align-word-timings.mjs <episode-name> [script-version]");
  process.exit(1);
}

const PUNCT = /[，。！？；：、""''《》（）…—·,.?!;:'"()\s]/gu;

function stripPunct(text) {
  return String(text || "").replace(PUNCT, "");
}

// 把正文按标点拆句 → 每句去标点 → 空格 join（保留「用空格断句」视觉排版）。
function wrapCaptionText(text) {
  return String(text || "")
    .split(/[，。！？；：,.!?;:]/u)
    .map((s) => s.replace(PUNCT, "").trim())
    .filter(Boolean)
    .join(" ");
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"' && quoted && line[i + 1] === '"') {
      current += '"';
      i += 1;
    } else if (ch === '"') {
      quoted = !quoted;
    } else if (ch === "," && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  values.push(current);
  return values;
}

function readScriptRows(filePath, version) {
  const text = fs.readFileSync(filePath, "utf8").trim();
  const lines = text.split(/\r?\n/u).filter(Boolean);
  const headers = parseCsvLine(lines.shift() || "");
  const rows = lines
    .map((line) => Object.fromEntries(headers.map((h, i) => [h, parseCsvLine(line)[i] || ""])))
    .filter((row) => row.version === version)
    .sort((a, b) => Number(a.order) - Number(b.order));
  return rows;
}

const episodeDir = path.join(ROOT, "episodes", episodeName);
const scriptVersion = resolveScriptVersion(episodeDir, requestedVersion);
const scriptPath = path.join(episodeDir, "script-body.csv");
const asrPath = process.env.ASR_PATH
  ? path.resolve(ROOT, process.env.ASR_PATH)
  : path.join(episodeDir, "audio", "asr", "body.json");
const timingsPath = path.join(episodeDir, "audio", "word-timings.json");
const bodyTimingsPath = path.join(episodeDir, "audio", "body-timings.json");

if (!fs.existsSync(asrPath)) throw new Error(`Missing ASR JSON: ${asrPath}`);

const asr = JSON.parse(fs.readFileSync(asrPath, "utf8"));
// 书名引导段处理（重要）：配音开头常先念一遍书名再进正文，这段不属于任何脚本行。
// 旧实现按 body-timings.json 的 skipLeadingSegments 整段丢弃前导 segment，
// 但 Whisper 常把「书名 + 正文首句」合并进同一个 segment（本项目即如此：
// ASR 把「牛虻」误识成「牛猛」，合并检测失效），整段丢弃会把正文首行一起丢掉，
// 逐字对齐因此整体前移一行、全片字幕错位。
// 改为保留完整 ASR 流 + 前置「书名锚点」：片头念出的书名由锚点吸收，
// 正文各行仍然对齐到自己的真实 token；若配音其实没念书名，锚点只是未匹配占位，
// 不参与输出，也不会抢走正文的匹配。
const transcription = asr.transcription || [];

function readAudioDurationMs() {
  try {
    const meta = JSON.parse(fs.readFileSync(bodyTimingsPath, "utf8"));
    const seconds = Number(meta.duration);
    if (Number.isFinite(seconds) && seconds > 0) return Math.round(seconds * 1000);
  } catch {
    // 回退到探测音频文件
  }
  const audioPath = path.join(episodeDir, "audio", "body-voiceover-story.mp3");
  if (!fs.existsSync(audioPath)) return 0;
  const result = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audioPath],
    { encoding: "utf8" },
  );
  const seconds = Number(result.stdout?.trim());
  return Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : 0;
}

function readLeadTitle() {
  try {
    const brief = JSON.parse(fs.readFileSync(path.join(episodeDir, "brief.json"), "utf8"));
    const raw = brief.display_title || brief.displayTitle || brief.title || "";
    const clean = String(raw).replace(/[《》\s]/gu, "");
    if (clean) return clean;
  } catch {
    // brief.json 缺失或不可解析时回退为剧集名
  }
  return String(episodeName).replace(/[《》\s]/gu, "");
}
// 扁平化 token：跨 segment 按音频顺序，记录每个字所属 token 的 from/to(ms)。
const asrTokens = [];
for (const seg of transcription) {
  for (const tok of seg.tokens || []) {
    if (!tok.text || tok.text.startsWith("[_")) continue;
    const from = Number(tok.offsets?.from ?? 0);
    const to = Number(tok.offsets?.to ?? 0);
    asrTokens.push({ text: tok.text, from, to });
  }
}
const asrChars = [];
for (let i = 0; i < asrTokens.length; i += 1) {
  for (const ch of asrTokens[i].text) {
    if (PUNCT.test(ch)) continue;
    asrChars.push({ char: ch, tokenIdx: i });
  }
}

const rows = readScriptRows(scriptPath, scriptVersion);
if (!rows.length) throw new Error(`No script rows for version ${scriptVersion}`);

// 前置的书名锚点（order 0）只用于吸收配音开头念出的书名，不进入 byOrder 输出。
const titleClean = stripPunct(readLeadTitle());
const bodyRows = rows;
const scriptChars = [];
for (const ch of titleClean) scriptChars.push({ char: ch, order: 0 });
const rowStartIdx = new Map();
const rowEndIdx = new Map();
for (const row of bodyRows) {
  const order = Number(row.order);
  rowStartIdx.set(order, scriptChars.length);
  for (const ch of stripPunct(row.text)) {
    scriptChars.push({ char: ch, order });
  }
  rowEndIdx.set(order, scriptChars.length - 1);
}
const m = asrChars.length;
const n = scriptChars.length;
if (m === 0 || n === 0) throw new Error("Empty ASR or script char stream");

// 通用 LCS，match 由谓词决定；天然容忍 ASR 的增删字（插入/删除不影响后续对齐）。
function lcsAlign(aChars, sChars, matchFn) {
  const mm = aChars.length;
  const nn = sChars.length;
  const stride = nn + 1;
  const dp = new Int32Array((mm + 1) * stride);
  for (let i = 1; i <= mm; i += 1) {
    for (let j = 1; j <= nn; j += 1) {
      if (matchFn(aChars[i - 1].char, sChars[j - 1].char)) {
        dp[i * stride + j] = dp[(i - 1) * stride + (j - 1)] + 1;
      } else {
        dp[i * stride + j] = Math.max(dp[(i - 1) * stride + j], dp[i * stride + (j - 1)]);
      }
    }
  }
  const res = new Int32Array(nn).fill(-1);
  let i = mm;
  let j = nn;
  while (i > 0 && j > 0) {
    if (matchFn(aChars[i - 1].char, sChars[j - 1].char)) {
      res[j - 1] = i - 1;
      i -= 1;
      j -= 1;
    } else if (dp[(i - 1) * stride + j] > dp[i * stride + (j - 1)]) {
      i -= 1;
    } else {
      j -= 1;
    }
  }
  return res;
}

// 从「已对齐结果」学出 繁→简 / 错字 映射：只在真正对齐(位置对应)且字符不同的地方投票。
// 这比位置 1:1 强行配对稳健——ASR 若在中段多出「今天」二字导致整体漂移，
// 位置配对会把 今→妈、妈→死 一类的错映射学进去，污染 LCS；对齐法只在真正对齐处投票，避开漂移。
function learnMapFromAlignment(aChars, sChars, res) {
  const cnt = new Map();
  for (let j = 0; j < res.length; j += 1) {
    const a = res[j];
    if (a < 0) continue;
    const ac = aChars[a].char;
    const sc = sChars[j].char;
    if (ac === sc) continue;
    if (!cnt.has(ac)) cnt.set(ac, new Map());
    cnt.get(ac).set(sc, (cnt.get(ac).get(sc) || 0) + 1);
  }
  const t2s = new Map();
  for (const [a, smap] of cnt) {
    let best = null;
    let bc = 0;
    for (const [s, c] of smap) if (c > bc) { bc = c; best = s; }
    if (best != null) t2s.set(a, best);
  }
  return t2s;
}

// 三轮：
//  ① 用「精确字符相等」做粗对齐（不依赖任何映射，天然容忍 ASR 的增删字）。
//  ② 从粗对齐学出 繁→简 / 错字 映射。
//  ③ 用映射再对齐，并从精对齐结果精炼映射后重对齐，收敛。
// （ASR 已用 opencc-js 预转简体时，第 ① 遍即能高质量对齐；预转非必须，但能显著提升第 ① 遍质量。）
const coarse = lcsAlign(asrChars, scriptChars, (a, s) => a === s);
let t2s = learnMapFromAlignment(asrChars, scriptChars, coarse);
let scriptToAsr = lcsAlign(asrChars, scriptChars, (a, s) => s === (t2s.get(a) ?? a));
{
  const t2s2 = learnMapFromAlignment(asrChars, scriptChars, scriptToAsr);
  t2s = t2s2;
  scriptToAsr = lcsAlign(asrChars, scriptChars, (a, s) => s === (t2s.get(a) ?? a));
}

const matchedCount = scriptToAsr.filter((x) => x >= 0).length;
const totalAsrFrom = asrTokens.length ? asrTokens[0].from : 0;
const totalAsrTo = asrTokens.length ? asrTokens.at(-1).to : totalAsrFrom;
const totalAsrSpan = Math.max(1, totalAsrTo - totalAsrFrom);
const avgMsPerChar = totalAsrSpan / Math.max(1, n);

// 每个脚本字赋绝对 [from,to] ms；未匹配字用相邻匹配做线性插值。
// 多字映射到同一 token 时，把该 token 的 [from,to] 在其字间均匀切分，
// 避免同一 token 的字同帧齐亮（更顺滑、更贴语音）。
const charMs = new Array(n).fill(null);
const tokCharGroups = new Map();
for (let j = 0; j < n; j += 1) {
  const a = scriptToAsr[j];
  if (a < 0) continue;
  const tIdx = asrChars[a].tokenIdx;
  if (!tokCharGroups.has(tIdx)) tokCharGroups.set(tIdx, []);
  tokCharGroups.get(tIdx).push(j);
}
for (const [tIdx, js] of tokCharGroups) {
  const tk = asrTokens[tIdx];
  const span = Math.max(1, tk.to - tk.from);
  const k = js.length;
  for (let p = 0; p < k; p += 1) {
    charMs[js[p]] = {
      from: tk.from + (p / k) * span,
      to: tk.from + ((p + 1) / k) * span,
    };
  }
}
function findPrevMatched(idx) {
  for (let k = idx; k >= 0; k -= 1) if (charMs[k]) return k;
  return -1;
}
function findNextMatched(idx) {
  for (let k = idx; k < n; k += 1) if (charMs[k]) return k;
  return n;
}
// 先填两端，再填中段连续未匹配段。
for (let j = 0; j < n; j += 1) {
  if (charMs[j]) continue;
  const prev = findPrevMatched(j);
  const next = findNextMatched(j);
  if (prev < 0 && next < n) {
    charMs[j] = { from: Math.max(0, charMs[next].from - (next - j) * avgMsPerChar), to: charMs[next].from };
  } else if (prev >= 0 && next >= n) {
    charMs[j] = { from: charMs[prev].to, to: charMs[prev].to + avgMsPerChar };
  }
}
for (let j = 0; j < n; j += 1) {
  if (charMs[j]) continue;
  const prev = findPrevMatched(j);
  const next = findNextMatched(j);
  if (prev >= 0 && next < n) {
    const a = charMs[prev].to;
    const b = charMs[next].from;
    const span = Math.max(1, b - a);
    const gap = next - prev - 1;
    charMs[j] = {
      from: a + ((j - prev) / (gap + 1)) * span,
      to: a + ((j - prev + 1) / (gap + 1)) * span,
    };
  } else {
    charMs[j] = {
      from: totalAsrFrom + j * avgMsPerChar,
      to: totalAsrFrom + (j + 1) * avgMsPerChar,
    };
  }
}
// 单调递增兜底（插入/删除可能致局部倒退）。
for (let j = 1; j < n; j += 1) {
  if (charMs[j].from < charMs[j - 1].to) charMs[j].from = charMs[j - 1].to;
  if (charMs[j].to <= charMs[j].from) charMs[j].to = charMs[j].from + 1;
}
// 收尾钳制：Whisper 末段常常给出超出音频真实长度的 token 时间（本项目尾句被推到
// 249s，而旁白只有 246.14s），不钳制会让最后一句字幕落在时间轴之外、末行时长虚高。
// 钳制是单调的，不会破坏上面的递增兜底。
const durationMs = readAudioDurationMs();
if (durationMs > 0) {
  for (let j = 0; j < n; j += 1) {
    const item = charMs[j];
    if (item.from > durationMs) item.from = durationMs;
    if (item.to > durationMs) item.to = durationMs;
    if (item.to <= item.from) item.to = Math.min(durationMs, item.from + 1);
  }
}

// 组装 byOrder：用 wrapCaptionText 保留空格断句；空格为占位，时间取前字 to。
function buildList(text, startIdx) {
  const display = wrapCaptionText(text); // 含空格、无标点
  const list = [];
  let ci = startIdx;
  let spacePrevEnd = charMs[ci] ? charMs[ci].to : totalAsrFrom;
  for (const ch of display) {
    if (ch === " ") {
      list.push({
        ch: " ",
        start: Number((spacePrevEnd / 1000).toFixed(2)),
        end: Number((spacePrevEnd / 1000).toFixed(2)),
      });
      continue;
    }
    const ms = charMs[ci];
    ci += 1;
    spacePrevEnd = ms.to;
    list.push({
      ch,
      start: Number((ms.from / 1000).toFixed(2)),
      end: Number((ms.to / 1000).toFixed(2)),
    });
  }
  return list;
}
const byOrder = {};
for (const row of bodyRows) {
  byOrder[String(row.order)] = buildList(row.text, rowStartIdx.get(Number(row.order)));
}

const allStarts = Object.values(byOrder).flatMap((l) => l.filter((x) => x.ch !== " ").map((x) => x.start));
const firstTime = allStarts.length ? Math.min(...allStarts) : 0;
const lastTime = Math.max(
  0,
  ...Object.values(byOrder).flatMap((l) => l.map((x) => x.end)),
);

const output = {
  scriptVersion,
  source: "whisper-token LCS char alignment (auto-learned trad->simp map, 2-pass, title lead anchor)",
  audio: path.relative(ROOT, path.join(episodeDir, "audio", "body-voiceover-story.mp3")),
  asr: path.relative(ROOT, asrPath),
  leadAnchor: titleClean || null,
  alignment: {
    asrChars: m,
    scriptChars: n,
    matched: matchedCount,
    matchRate: Number((matchedCount / Math.max(1, n - titleClean.length)).toFixed(3)),
    requiresAgentReview: false,
  },
  span: { fromSec: Number(firstTime.toFixed(2)), toSec: Number(lastTime.toFixed(2)) },
  byOrder,
};
fs.writeFileSync(timingsPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(`Word-level timings written to ${path.relative(ROOT, timingsPath)}`);
console.log(`  asrChars=${m} scriptChars=${n} matched=${matchedCount} matchRate=${output.alignment.matchRate}`);

// 用逐字结果回写 body-timings.json 的逐句时间轴：停顿分段法在长旁白上会把
// 多个句子并成一段、或把一句切成半秒，远不如 token 级 LCS 精准。
// 仅在版本一致且匹配率可用时回写，失败不阻断（保留原停顿时间轴）。
try {
  if (fs.existsSync(bodyTimingsPath)) {
    const meta = JSON.parse(fs.readFileSync(bodyTimingsPath, "utf8"));
    if (!meta.scriptVersion || meta.scriptVersion === scriptVersion) {
      const captions = bodyRows.map((row) => {
        const order = Number(row.order);
        const spoken = (byOrder[String(order)] || []).filter((x) => x.ch !== " ");
        const start = spoken.length ? spoken[0].start : 0;
        const end = spoken.length ? spoken[spoken.length - 1].end : start + 0.3;
        return { order, start: Number(start.toFixed(2)), end: Number(Math.max(start + 0.3, end).toFixed(2)) };
      });
      meta.captions = captions;
      meta.captionDisplay = "sentence"; // 逐行整句字幕模式（与长旁白文案配对）
      meta.alignment = {
        ...(meta.alignment || {}),
        method: "word-level-lcs",
        speechSegments: meta.alignment?.speechSegments ?? null,
        asrAvailable: true,
        requiresAgentReview: output.alignment.matchRate < 0.7,
      };
      fs.writeFileSync(bodyTimingsPath, `${JSON.stringify(meta, null, 2)}\n`);
      console.log(`  body-timings.json captions updated from word-level alignment (${captions.length} rows)`);
    }
  }
} catch (error) {
  console.warn(`Could not backfill body-timings.json captions: ${error.message}`);
}
for (const row of bodyRows) {
  const order = Number(row.order);
  const arr = byOrder[String(order)];
  const first = arr.find((x) => x.ch !== " ");
  const last = [...arr].reverse().find((x) => x.ch !== " ");
  console.log(`  #${order}  ${first?.start.toFixed(2)}s – ${last?.end.toFixed(2)}s  (${arr.filter((x) => x.ch !== " ").length} chars)`);
}
