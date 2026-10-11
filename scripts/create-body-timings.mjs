#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  buildCaptionTimings,
  buildEstimatedCaptionTimings,
  buildSpeechSegments,
  coalesceSpeechSegments,
  parseSilenceEvents,
} from "./lib/body-timings.mjs";
import { resolveScriptVersion } from "./lib/script-version.mjs";
import { validateBodyScript } from "./lib/script-policy.mjs";

const ROOT = process.cwd();
const MODEL_PATH = path.join(ROOT, "assets", "models", "whisper", "ggml-base.bin");
const [, , episodeName, requestedVersion, ...rest] = process.argv;

function readOptions(values) {
  const positional = [];
  const options = { skipLeading: 1, noise: "-35dB", silenceDuration: "0.18" };
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === "--skip-leading" || value === "--noise" || value === "--silence-duration") {
      options[{ "--skip-leading": "skipLeading", "--noise": "noise", "--silence-duration": "silenceDuration" }[value]] = values[++index];
    } else if (value.startsWith("--skip-leading=")) options.skipLeading = value.split("=", 2)[1];
    else if (value.startsWith("--noise=")) options.noise = value.split("=", 2)[1];
    else if (value.startsWith("--silence-duration=")) options.silenceDuration = value.split("=", 2)[1];
    else positional.push(value);
  }
  return { positional, options };
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"' && quoted && line[index + 1] === '"') { current += '"'; index += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === "," && !quoted) { values.push(current); current = ""; }
    else current += char;
  }
  values.push(current);
  return values;
}

function readScriptRows(filePath, version) {
  const lines = fs.readFileSync(filePath, "utf8").trim().split(/\r?\n/u);
  const headers = parseCsvLine(lines.shift() || "");
  return lines
    .filter(Boolean)
    .map((line) => Object.fromEntries(headers.map((header, index) => [header, parseCsvLine(line)[index] || ""])))
    .filter((row) => row.version === version)
    .sort((a, b) => Number(a.order) - Number(b.order));
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: ROOT, encoding: "utf8", shell: false, ...options });
  if (result.status !== 0) {
    const detail = result.error?.message || result.stderr || result.stdout || `status=${result.status}, signal=${result.signal || "none"}`;
    throw new Error(`${command} failed: ${detail.trim()}`);
  }
  return result;
}

function usage() {
  console.error("Usage: node scripts/create-body-timings.mjs <episode-name> [script-version] [voiceover-path] [options]");
  console.error("Options: --skip-leading 1 --noise -35dB --silence-duration 0.18");
}

if (!episodeName) {
  usage();
  process.exit(1);
}

const { positional, options } = readOptions(rest);
const episodeDir = path.join(ROOT, "episodes", episodeName);
const scriptVersion = resolveScriptVersion(episodeDir, requestedVersion);
const audioDir = path.join(episodeDir, "audio");
const scriptPath = path.join(episodeDir, "script-body.csv");
const voicePath = path.resolve(ROOT, positional[0] || path.join("episodes", episodeName, "audio", "body-voiceover.mp3"));
const asrDir = path.join(audioDir, "asr");
const asrBase = path.join(asrDir, "body");
const timingsPath = path.join(audioDir, "body-timings.json");

if (!fs.existsSync(episodeDir)) throw new Error(`Episode not found: ${episodeDir}`);
if (!fs.existsSync(scriptPath)) throw new Error(`Missing script-body.csv: ${scriptPath}`);
if (!fs.existsSync(voicePath)) throw new Error(`Voiceover not found: ${voicePath}`);
// 时间轴 ASR 必须与最终旁白同源：渲染管线用 story 预设处理后的配音成片，
// 因此优先对 body-voiceover-story.mp3 做识别与静音检测；尚不存在时回退原始口播。
const storyVoicePath = path.join(audioDir, "body-voiceover-story.mp3");
const asrSourcePath = fs.existsSync(storyVoicePath) ? storyVoicePath : voicePath;

const rows = readScriptRows(scriptPath, scriptVersion);
if (!rows.length) throw new Error(`No script rows found for version ${scriptVersion}`);
const scriptValidation = validateBodyScript(rows);
if (scriptValidation.errors.length && !process.env.ALLOW_LONG_SCRIPT) throw new Error(scriptValidation.errors.join("；"));

fs.mkdirSync(asrDir, { recursive: true });
// 仅用书名作轻量 prompt 锚点：完整文案作 prompt 会让 Whisper 把整段文本当初始上下文，
// 在静音/停顿处反复「回放」导致重复幻觉（实测 42s 片段被幻觉成 60s+ 的循环）。
// 短 prompt + --no-fallback（禁用温度回退）可彻底消除该循环。
const whisperPrompt = `${episodeName}。`;
let whisperFailure = null;
if (fs.existsSync(MODEL_PATH)) {
  try {
    const whisperArgs = ["-ng", "-m", MODEL_PATH, "-l", "zh", "-ojf", "-otxt", "--prompt", whisperPrompt, "-of", asrBase, asrSourcePath, "--no-fallback"];
    if (process.env.WHISPER_DTW) whisperArgs.push("--dtw", MODEL_PATH); // opt-in DTW for precise token timestamps
    run("whisper-cli", whisperArgs, { stdio: "inherit" });
  } catch (error) {
    whisperFailure = error;
    console.warn(`Whisper unavailable; continuing without ASR text alignment: ${error.message}`);
  }
} else {
  whisperFailure = new Error(`Missing Whisper model: ${MODEL_PATH}`);
  console.warn(`${whisperFailure.message}; continuing without ASR text alignment`);
}

const durationResult = run("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", asrSourcePath]);
const duration = Number(durationResult.stdout.trim());
if (!Number.isFinite(duration) || duration <= 0) throw new Error(`Invalid voiceover duration: ${duration}`);

let speechSegments;
let silenceFailure = null;
try {
  const silenceResult = run(
    "ffmpeg",
    ["-hide_banner", "-i", asrSourcePath, "-af", `silencedetect=noise=${options.noise}:d=${options.silenceDuration}`, "-f", "null", "-"],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  const events = parseSilenceEvents(`${silenceResult.stdout}\n${silenceResult.stderr}`);
  speechSegments = buildSpeechSegments(duration, events);
} catch (error) {
  silenceFailure = error;
  speechSegments = [{ start: 0, end: duration }];
  console.warn(`Silence detection unavailable; continuing with full audio duration: ${error.message}`);
}
const skipLeading = Number(options.skipLeading) || 0;
let asr = { transcription: [] };
if (!whisperFailure) {
  try {
    asr = JSON.parse(fs.readFileSync(`${asrBase}.json`, "utf8"));
  } catch (error) {
    whisperFailure = error;
    console.warn(`Whisper output could not be read; continuing without ASR text alignment: ${error.message}`);
  }
}
// 书名跳过必须 token 感知：若被跳过的首段实际是「书名 + 正文首句」的合并段，
// 整段丢弃会让第一行字幕时间整体错位。检测到合并时回退为不跳过，交由
// 段级 DP / 词级 LCS 在完整流上对齐。
let effectiveSkipLeading = skipLeading;
if (effectiveSkipLeading > 0 && (asr.transcription || []).length >= effectiveSkipLeading) {
  const skippedText = (asr.transcription || [])
    .slice(0, effectiveSkipLeading)
    .map((segment) => String(segment.text || "").replace(/\s+/gu, ""))
    .join("");
  const titleClean = String(episodeName).replace(/[《》\s]/gu, "");
  if (skippedText && skippedText.includes(titleClean) && skippedText.length > titleClean.length + 2) {
    console.warn(
      `Leading ASR segment merges the title with the first spoken line; keeping it for alignment `
        + `(skip-leading ${effectiveSkipLeading} -> 0): "${skippedText.slice(0, 40)}…"`,
    );
    effectiveSkipLeading = 0;
  }
}
let captions;
let alignment = {
  method: "silence-segments",
  speechSegments: speechSegments.length,
  requiresAgentReview: false,
};
try {
  const normalizedSegments = coalesceSpeechSegments(speechSegments, rows.length + effectiveSkipLeading);
  captions = buildCaptionTimings(rows.map((row) => row.order), normalizedSegments, effectiveSkipLeading);
} catch (error) {
  captions = buildEstimatedCaptionTimings(rows, speechSegments, duration, effectiveSkipLeading);
  alignment = {
    method: "speech-duration-estimate",
    reason: error.message,
    speechSegments: speechSegments.length,
    silenceDetectionAvailable: !silenceFailure,
    requiresAgentReview: true,
  };
  console.warn(`Speech pauses were insufficient; continuing with duration estimate: ${error.message}`);
}
alignment.asrAvailable = !whisperFailure;
alignment.asrText = (asr.transcription || []).map((segment) => segment.text).join("");
fs.writeFileSync(
  timingsPath,
  `${JSON.stringify({
    scriptVersion,
    duration: Number(duration.toFixed(2)),
    source: `script-body.csv subtitle truth with ${alignment.method}`,
    audio: path.relative(ROOT, asrSourcePath),
    asr: fs.existsSync(`${asrBase}.json`) ? path.relative(ROOT, `${asrBase}.json`) : null,
    skipLeadingSegments: effectiveSkipLeading,
    silence: { noise: options.noise, duration: Number(options.silenceDuration) },
    alignment,
    captions,
  }, null, 2)}\n`,
);

console.log(`ASR JSON: ${path.relative(ROOT, `${asrBase}.json`)}`);
console.log(`Body timings: ${path.relative(ROOT, timingsPath)}`);
