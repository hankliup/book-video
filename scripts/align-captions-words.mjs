#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { resolveScriptVersion } from "./lib/script-version.mjs";

const ROOT = process.cwd();
const [, , episodeName, requestedVersion] = process.argv;
if (!episodeName) {
  console.error("Usage: node scripts/align-captions-words.mjs <episode-name> [script-version]");
  process.exit(1);
}

const PUNCT = /[，。！？；：、""''《》（）…—·,.?!;:'"()\s]/gu;

function stripPunct(text) {
  return String(text || "").replace(PUNCT, "");
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
  return lines
    .map((line) => Object.fromEntries(headers.map((h, i) => [h, parseCsvLine(line)[i] || ""])))
    .filter((row) => row.version === version)
    .sort((a, b) => Number(a.order) - Number(b.order));
}

function readDuration(filePath) {
  const result = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filePath],
    { cwd: ROOT, encoding: "utf8", shell: false },
  );
  const d = Number(result.stdout?.trim());
  return result.status === 0 && Number.isFinite(d) && d > 0 ? d : 0;
}

const episodeDir = path.join(ROOT, "episodes", episodeName);
const scriptVersion = resolveScriptVersion(episodeDir, requestedVersion);
const scriptPath = path.join(episodeDir, "script-body.csv");
const asrPath = path.join(episodeDir, "audio", "asr", "body.json");
const voicePath = path.join(episodeDir, "audio", "body-voiceover.mp3");
const timingsPath = path.join(episodeDir, "audio", "body-timings.json");

if (!fs.existsSync(asrPath)) throw new Error(`Missing ASR JSON: ${asrPath}`);

const asr = JSON.parse(fs.readFileSync(asrPath, "utf8"));
const tokens = [];
for (const seg of asr.transcription || []) {
  for (const tok of seg.tokens || []) {
    if (!tok.text || tok.text.startsWith("[_")) continue;
    const from = tok.offsets?.from ?? 0;
    const to = tok.offsets?.to ?? 0;
    tokens.push({ text: tok.text, from: Number(from), to: Number(to) });
  }
}

const asrChars = [];
for (let i = 0; i < tokens.length; i += 1) {
  for (const ch of tokens[i].text) {
    if (PUNCT.test(ch)) continue;
    asrChars.push({ char: ch, tokenIdx: i });
  }
}

const rows = readScriptRows(scriptPath, scriptVersion);
if (!rows.length) throw new Error(`No script rows for version ${scriptVersion}`);

const scriptChars = [];
const lineStartIdx = new Map();
const lineEndIdx = new Map();
for (const row of rows) {
  const order = Number(row.order);
  lineStartIdx.set(order, scriptChars.length);
  for (const ch of row.text) {
    if (PUNCT.test(ch)) continue;
    scriptChars.push({ char: ch, order });
  }
  lineEndIdx.set(order, scriptChars.length - 1);
}

const m = asrChars.length;
const n = scriptChars.length;
const stride = n + 1;
const dp = new Int32Array((m + 1) * stride);
for (let i = 1; i <= m; i += 1) {
  for (let j = 1; j <= n; j += 1) {
    if (asrChars[i - 1].char === scriptChars[j - 1].char) {
      dp[i * stride + j] = dp[(i - 1) * stride + (j - 1)] + 1;
    } else {
      dp[i * stride + j] = Math.max(dp[(i - 1) * stride + j], dp[i * stride + (j - 1)]);
    }
  }
}

const scriptToAsr = new Int32Array(n).fill(-1);
let i = m;
let j = n;
while (i > 0 && j > 0) {
  if (asrChars[i - 1].char === scriptChars[j - 1].char) {
    scriptToAsr[j - 1] = i - 1;
    i -= 1;
    j -= 1;
  } else if (dp[(i - 1) * stride + j] >= dp[i * stride + (j - 1)]) {
    i -= 1;
  } else {
    j -= 1;
  }
}

const totalAsrFrom = tokens.length ? tokens[0].from : 0;
const totalAsrTo = tokens.length ? tokens.at(-1).to : totalAsrFrom;
const totalAsrSpan = Math.max(1, totalAsrTo - totalAsrFrom);
const avgMsPerScriptChar = totalAsrSpan / Math.max(1, n);

const captions = [];
let previousEndMs = totalAsrFrom;
for (const row of rows) {
  const order = Number(row.order);
  const startIdx = lineStartIdx.get(order);
  const endIdx = lineEndIdx.get(order);
  const matchedAsrIndices = [];
  for (let k = startIdx; k <= endIdx; k += 1) {
    const aIdx = scriptToAsr[k];
    if (aIdx >= 0) matchedAsrIndices.push(aIdx);
  }

  let startMs;
  let endMs;
  if (matchedAsrIndices.length >= 2) {
    const minA = Math.min(...matchedAsrIndices);
    const maxA = Math.max(...matchedAsrIndices);
    startMs = tokens[asrChars[minA].tokenIdx].from;
    endMs = tokens[asrChars[maxA].tokenIdx].to;
  } else if (matchedAsrIndices.length === 1) {
    const aIdx = matchedAsrIndices[0];
    startMs = tokens[asrChars[aIdx].tokenIdx].from;
    endMs = tokens[asrChars[aIdx].tokenIdx].to;
  } else {
    const lineCharStart = startIdx;
    const lineCharEnd = endIdx + 1;
    startMs = totalAsrFrom + lineCharStart * avgMsPerScriptChar;
    endMs = totalAsrFrom + lineCharEnd * avgMsPerScriptChar;
  }

  // If the line has leading/trailing unmatched chars (e.g. mis-ASR names),
  // stretch to cover the estimated time for those chars as well.
  const lineChars = endIdx - startIdx + 1;
  const matchedChars = matchedAsrIndices.length;
  if (matchedChars > 0 && matchedChars < lineChars) {
    const extraBefore = scriptToAsr[startIdx] < 0 ? (startIdx - findPrevMatched(startIdx) - 1) : 0;
    const extraAfter = scriptToAsr[endIdx] < 0 ? (findNextMatched(endIdx) - endIdx - 1) : 0;
    if (extraBefore > 0) startMs = Math.max(0, startMs - extraBefore * avgMsPerScriptChar);
    if (extraAfter > 0) endMs = endMs + extraAfter * avgMsPerScriptChar;
  }

  startMs = Math.max(0, Number(startMs));
  endMs = Math.max(startMs + 300, Number(endMs));
  if (endMs < previousEndMs + 200) endMs = previousEndMs + 200;
  previousEndMs = endMs;

  if (row.text.trim()) {
    captions.push({
      order,
      start: Number((startMs / 1000).toFixed(2)),
      end: Number((endMs / 1000).toFixed(2)),
    });
  }
}

function findPrevMatched(idx) {
  for (let k = idx; k >= 0; k -= 1) if (scriptToAsr[k] >= 0) return k;
  return -1;
}
function findNextMatched(idx) {
  for (let k = idx; k < n; k += 1) if (scriptToAsr[k] >= 0) return k;
  return n;
}

const duration = readDuration(voicePath) || captions.at(-1)?.end || 0;
const lastCaptionEnd = captions.reduce((max, c) => Math.max(max, c.end), 0);
const safeDuration = Number(Math.max(duration, lastCaptionEnd + 0.4).toFixed(2));

const existing = fs.existsSync(timingsPath)
  ? JSON.parse(fs.readFileSync(timingsPath, "utf8"))
  : {};

const output = {
  scriptVersion,
  duration: safeDuration,
  source: "script-body.csv subtitle truth with whisper word-level forced alignment",
  audio: path.relative(ROOT, voicePath),
  asr: path.relative(ROOT, asrPath),
  skipLeadingSegments: 0,
  alignment: {
    method: "whisper-word-lcs",
    speechSegments: tokens.length,
    asrAvailable: true,
    requiresAgentReview: false,
    asrText: (asr.transcription || []).map((s) => s.text).join(""),
  },
  captions,
};

fs.writeFileSync(timingsPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(`Word-aligned timings written to ${path.relative(ROOT, timingsPath)}`);
for (const c of captions) {
  console.log(`  #${c.order}  ${c.start.toFixed(2)}s – ${c.end.toFixed(2)}s`);
}
