#!/usr/bin/env node
// Re-runnable fallback for uneven caption windows from `create-body-timings.mjs`.
// Run from the repo root (process.cwd() is used as ROOT).
//
// Symptom: after `node scripts/create-body-timings.mjs '<book>' <ver> --skip-leading 0`,
// the generated `body-timings.json` caption windows are wildly uneven for long (20+ line)
// narration — e.g. line 1 = 0.0–0.9s while line 6 = 0.86s — because silencedetect slices
// the long audio into too few segments and buildCaptionTimings / coalesceSpeechSegments
// forces them onto the row count, distorting per-line durations.
//
// Fix: distribute each script row proportional to its character count across the REAL speech
// (everything after the leading silent gap), trimming the head silence. Uses the repo's own
// buildEstimatedCaptionTimings — the same path create-body-timings.mjs falls back to when
// pause segmentation is insufficient — so script-body.csv stays the subtitle text truth.

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  parseSilenceEvents,
  buildSpeechSegments,
  buildEstimatedCaptionTimings,
} from "../scripts/lib/body-timings.mjs";

const ROOT = process.cwd();
const ep = process.argv[2];
const version = process.argv[3] || "A";
if (!ep) {
  console.error("Usage: node references/longform-caption-timing-fallback.mjs <book> [version]");
  process.exit(1);
}

const episodeDir = path.join(ROOT, "episodes", ep);
const scriptPath = path.join(episodeDir, "script-body.csv");
const bodyVoice = path.join(episodeDir, "audio", "body-voiceover.mp3");
const asrBase = path.join(episodeDir, "audio", "asr", "body");

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"' && quoted && line[i + 1] === '"') { current += '"'; i += 1; }
    else if (ch === '"') quoted = !quoted;
    else if (ch === "," && !quoted) { values.push(current); current = ""; }
    else current += ch;
  }
  values.push(current);
  return values;
}

const lines = fs.readFileSync(scriptPath, "utf8").trim().split(/\r?\n/u);
const headers = parseCsvLine(lines.shift() || "");
const rows = lines
  .filter(Boolean)
  .map((l) => Object.fromEntries(headers.map((h, i) => [h, parseCsvLine(l)[i] || ""])))
  .filter((r) => r.version === version)
  .sort((a, b) => Number(a.order) - Number(b.order));

const duration = Number(
  spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", bodyVoice], { encoding: "utf8" }).stdout.trim(),
);

const sr = spawnSync("ffmpeg", ["-hide_banner", "-i", bodyVoice, "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"], { encoding: "utf8" });
const events = parseSilenceEvents(`${sr.stdout}\n${sr.stderr}`);
let segs = buildSpeechSegments(duration, events);
// Drop a leading silent gap (0–~0.9s) so it isn't counted as the first spoken line.
if (segs.length && segs[0].start === 0 && segs[0].end < 1.5) segs = segs.slice(1);

const captions = buildEstimatedCaptionTimings(rows, segs, duration, 0);

const out = {
  scriptVersion: version,
  duration: Number(duration.toFixed(2)),
  source: "script-body.csv subtitle truth with speech-duration-estimate",
  audio: path.relative(ROOT, bodyVoice),
  asr: fs.existsSync(`${asrBase}.json`) ? path.relative(ROOT, `${asrBase}.json`) : null,
  skipLeadingSegments: 0,
  silence: { noise: "-35dB", duration: 0.18 },
  alignment: {
    method: "speech-duration-estimate",
    reason: "silence-segment slice produced uneven windows; using char-weighted proportional fallback across real speech",
    speechSegments: segs.length,
    requiresAgentReview: false,
  },
  captions,
};
fs.writeFileSync(path.join(episodeDir, "audio", "body-timings.json"), `${JSON.stringify(out, null, 2)}\n`);
console.log(`wrote body-timings.json: ${captions.length} captions, ${captions[0].start}s → ${captions[captions.length - 1].end}s`);
