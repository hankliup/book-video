#!/usr/bin/env node
// 长音频分段 ASR：解决 Whisper 在长音频（>2min）上严重漏识的问题。
//
// 背景（2026-08-28 汤姆·索亚历险记 277s 实测）：
//   whisper-cli 直接跑整段 277s 音频时，开头 0–30s 的完整内容被压缩成一句
//   「汤姆·索亚历险记。」（漏识幻觉），后续段落边界也整体漂移，
//   导致 align-word-timings 的 LCS matchRate 只有 0.756、多行字幕时间被挤成 1~2 秒。
//   把同一段音频切成 ~45s 的小段后单独识别，内容完整、段落边界正确。
//
// 策略：
//   1. ffmpeg silencedetect 找出所有 >= minPause 的静音区间；
//   2. 按 targetSegment 秒为名义目标，在每个目标点 ±window 内挑「最长静音」的中点做切点
//      （在静音中点切割不会切断任何词）；
//   3. 逐段 ffmpeg 切片 → whisper-cli -ojf 识别；
//   4. 把每段 token offsets 加上段起始偏移后合并，覆盖 audio/asr/body.json（原文件备份 .bak）。
//
// 用法：
//   node scripts/asr-segmented.mjs <episode-name> [audio-rel-path]
//   TARGET_SEGMENT=45 WINDOW=12 MIN_PAUSE=0.5 node scripts/asr-segmented.mjs "书名"
//
// 之后照常跑：node scripts/align-word-timings.mjs <episode-name> [version]

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = process.cwd();
// 默认 base；长音频/错字多时建议用 small（中文准确率明显更好）：
//   WHISPER_MODEL=assets/models/whisper/ggml-small.bin node scripts/asr-segmented.mjs "书名"
const MODEL_PATH = path.resolve(
  ROOT,
  process.env.WHISPER_MODEL || path.join("assets", "models", "whisper", "ggml-base.bin"),
);

const [episodeName, audioRel] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const asrNameArg = (process.argv.find((a) => a.startsWith("--asr=")) || "").split("=")[1];
if (!episodeName) {
  console.error("Usage: node scripts/asr-segmented.mjs <episode-name> [audio-rel-path] [--asr=hook]");
  process.exit(1);
}

const TARGET_SEGMENT = Number(process.env.TARGET_SEGMENT || "45");
const WINDOW = Number(process.env.WINDOW || "12");
const MIN_PAUSE = Number(process.env.MIN_PAUSE || "0.5");

const episodeDir = path.join(ROOT, "episodes", episodeName);
// --asr=hook 让同一套分段识别写 audio/asr/hook.json（三段结构的钩子段用它）。
// 不加时输出与历史完全一致：body-voiceover.mp3 → audio/asr/body.json。
const asrName = asrNameArg || "body";
const audioPath = path.resolve(
  ROOT,
  audioRel || path.join("episodes", episodeName, "audio", `${asrName}-voiceover.mp3`),
);
const asrDir = path.join(episodeDir, "audio", "asr");
const asrBase = path.join(asrDir, asrName);

if (!fs.existsSync(audioPath)) throw new Error(`Audio not found: ${audioPath}`);
if (!fs.existsSync(MODEL_PATH)) throw new Error(`Missing Whisper model: ${MODEL_PATH}`);

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), `asr-seg-${Date.now()}-`));

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: ROOT, encoding: "utf8", shell: false, ...options });
  if (result.status !== 0) {
    const detail = result.error?.message || result.stderr || result.stdout || `status=${result.status}`;
    throw new Error(`${command} failed: ${String(detail).trim()}`);
  }
  return result;
}

function getDuration(file) {
  const out = run("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file]);
  const value = Number(out.stdout.trim());
  if (!Number.isFinite(value) || value <= 0) throw new Error(`Invalid duration for ${file}`);
  return value;
}

// 找出所有 >= MIN_PAUSE 的静音区间
function findPauses(file) {
  const res = run("ffmpeg", [
    "-hide_banner", "-i", file,
    "-af", `silencedetect=noise=-35dB:d=${MIN_PAUSE}`,
    "-f", "null", "-",
  ], { stdio: ["ignore", "pipe", "pipe"] });
  const text = `${res.stdout || ""}\n${res.stderr || ""}`;
  const times = [...text.matchAll(/silence_(?:start|end):\s*([0-9.]+)/gu)].map((m) => Number(m[1]));
  const pauses = [];
  for (let i = 0; i < times.length - 1; i += 2) {
    const start = times[i];
    const end = times[i + 1];
    if (Number.isFinite(start) && Number.isFinite(end) && end - start >= MIN_PAUSE) {
      pauses.push({ start, end, length: end - start, mid: (start + end) / 2 });
    }
  }
  return pauses;
}

// 在每个目标点 ±WINDOW 内挑最长静音的中点做切点
function pickCutPoints(duration, pauses) {
  const cuts = [];
  const used = new Set();
  const segmentCount = Math.max(1, Math.round(duration / TARGET_SEGMENT));
  for (let i = 1; i < segmentCount; i += 1) {
    const target = (duration * i) / segmentCount;
    let best = null;
    for (const pause of pauses) {
      if (used.has(pause)) continue;
      if (Math.abs(pause.mid - target) > WINDOW) continue;
      if (!best || pause.length > best.length) best = pause;
    }
    if (best) {
      used.add(best);
      cuts.push(Number(best.mid.toFixed(3)));
    }
  }
  // 只保留递增且间隔足够（>=10s）的切点
  return cuts
    .sort((a, b) => a - b)
    .filter((value, index, arr) => index === 0 || value - arr[index - 1] >= 10);
}

const duration = getDuration(audioPath);
const pauses = findPauses(audioPath);
const cuts = pickCutPoints(duration, pauses);
const bounds = [0, ...cuts, duration];

console.log(`音频时长 ${duration.toFixed(2)}s，静音点 ${pauses.length} 个，切成 ${bounds.length - 1} 段`);
console.log(`切点(s)：${cuts.map((c) => c.toFixed(2)).join(", ") || "（无需切分）"}`);

const segments = [];
for (let i = 0; i < bounds.length - 1; i += 1) {
  const start = bounds[i];
  const end = bounds[i + 1];
  const length = end - start;
  if (length < 0.3) continue;
  const wav = path.join(tmpDir, `seg-${i}.wav`);
  const outBase = path.join(tmpDir, `out-${i}`);
  run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-i", audioPath,
    "-ss", String(start), "-t", String(length), "-ar", "16000", "-ac", "1", wav, "-y"]);
  const whisperArgs = ["-ng", "-m", MODEL_PATH, "-l", "zh", "-ojf", "--no-fallback", "-of", outBase, wav];
  // 可选：WHISPER_PROMPT 传入书名/作者/专有名词，帮助 whisper 在分段识别时正确辨认
  // 专有名词（如「多萝西娅」「利德盖特」）。注意：只传短上下文，切勿传整段文案，
  // 否则 whisper 会回放 prompt 导致幻觉循环（见项目 memory）。
  if (process.env.WHISPER_PROMPT) whisperArgs.splice(whisperArgs.indexOf(wav), 0, "--prompt", process.env.WHISPER_PROMPT);
  run("whisper-cli", whisperArgs,
    { stdio: ["ignore", "ignore", "ignore"] });
  const jsonPath = `${outBase}.json`;
  if (!fs.existsSync(jsonPath)) {
    console.warn(`  段 ${i} (${start.toFixed(2)}–${end.toFixed(2)}s) 无识别输出，跳过`);
    continue;
  }
  const data = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
  const items = data.transcription || [];
  // ⚠️ 段级 offsets 和 token 级 offsets 都必须加上段起始偏移。
  // align-word-timings.mjs 读的是 token 级时间戳（词级对齐真源），只改段级 offsets
  // 会让第 2 段起的所有 token 时间退回段内相对值 —— 表现为后半程字幕全堆在同一时刻
  // （实测 55s 处堆了 5 行），而段级时间戳看起来却完全正常，极难排查。
  const offsetMs = start * 1000;
  const shifted = items.map((item) => ({
    ...item,
    offsets: {
      from: Math.round((item.offsets?.from ?? 0) + offsetMs),
      to: Math.round((item.offsets?.to ?? 0) + offsetMs),
    },
    tokens: Array.isArray(item.tokens)
      ? item.tokens.map((token) => ({
          ...token,
          offsets: {
            from: Math.round((token.offsets?.from ?? 0) + offsetMs),
            to: Math.round((token.offsets?.to ?? 0) + offsetMs),
          },
        }))
      : item.tokens,
  }));
  segments.push(...shifted);
  const preview = items.map((s) => s.text).join("").slice(0, 32);
  console.log(`  段 ${i}  ${start.toFixed(2)}–${end.toFixed(2)}s  ${String(length.toFixed(1))}s  ${items.length} 句  ${preview}…`);
}

if (!segments.length) throw new Error("分段识别未产出任何内容");

segments.sort((a, b) => a.offsets.from - b.offsets.from);
const merged = { transcription: segments };

fs.mkdirSync(asrDir, { recursive: true });
const target = `${asrBase}.json`;
if (fs.existsSync(target)) fs.copyFileSync(target, `${target}.bak`);
fs.writeFileSync(target, `${JSON.stringify(merged, null, 2)}\n`);

const totalChars = segments.map((s) => String(s.text || "").replace(/\s+/gu, "")).join("").length;
console.log(`\n合并完成：${segments.length} 句 / ${totalChars} 字 → ${path.relative(ROOT, target)}`);
console.log(`原 ASR 已备份为 ${path.basename(target)}.bak`);
console.log(`\n下一步：node scripts/align-word-timings.mjs "${episodeName}"`);

try {
  fs.rmSync(tmpDir, { recursive: true, force: true });
} catch {
  /* best-effort cleanup */
}
