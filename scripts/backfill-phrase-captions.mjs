#!/usr/bin/env node
/**
 * Phrase-mode backfill: 用 word-timings.json 每行的首字.start / 尾字.end
 * 回填 body-timings.json 的 captions[].start / end。
 * 解决长视频静音分段不足导致 create-body-timings 回退到均分估算的问题。
 *
 * 用法: node backfill-phrase-captions.mjs <episode> [version]
 * 输出: 覆写 episodes/<episode>/audio/body-timings.json
 */

import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const [, , episodeName, requestedVersion] = process.argv;

if (!episodeName) {
  console.error("Usage: node backfill-phrase-captions.mjs <episode-name> [version]");
  process.exit(1);
}

const episodeDir = path.join(ROOT, "episodes", episodeName);
const timingsPath = path.join(episodeDir, "audio", "body-timings.json");
const wtPath = path.join(episodeDir, "audio", "word-timings.json");

if (!fs.existsSync(timingsPath)) throw new Error(`Missing: ${timingsPath}`);
if (!fs.existsSync(wtPath)) throw new Error(`Missing: ${wtPath}`);

const bt = JSON.parse(fs.readFileSync(timingsPath, "utf8"));
const wt = JSON.parse(fs.readFileSync(wtPath, "utf8"));

console.log(`body-timings: ${bt.captions.length} captions, duration=${bt.duration}`);
console.log(`word-timings: ${Object.keys(wt.byOrder || {}).length} orders, matchRate=${wt.alignment?.matchRate ?? "?"}`);

// Extract per-order { start, end } from word-timings byOrder
const lineTimings = {};
for (const [orderStr, list] of Object.entries(wt.byOrder || {})) {
  const nonSpace = list.filter((x) => x.ch !== " ");
  if (!nonSpace.length) continue;
  const first = nonSpace[0].start;
  const last = nonSpace[nonSpace.length - 1].end;
  lineTimings[orderStr] = { start: Number(first), end: Number(last) };
}

// Backfill captions
let fixed = 0;
let missing = 0;
for (const cap of bt.captions) {
  const key = String(cap.order);
  if (lineTimings[key]) {
    cap.start = lineTimings[key].start;
    cap.end = lineTimings[key].end;
    fixed += 1;
  } else {
    missing += 1;
  }
}

console.log(`Backfilled: ${fixed}, missing (kept original): ${missing}`);

// --- Validation ---
const sorted = [...bt.captions].sort((a, b) => a.order - b.order);
let issues = [];
for (let i = 1; i < sorted.length; i++) {
  const prev = sorted[i - 1];
  const cur = sorted[i];
  if (cur.start < prev.start) {
    issues.push(`#${cur.order} start(${cur.start}) < #${prev.order} start(${prev.start}) [NOT MONOTONIC]`);
  }
  if (cur.start < prev.end - 0.01) {
    issues.push(`#${cur.order} start(${cur.start}) overlaps #${prev.order} end(${prev.end})`);
  }
}
const lastCap = sorted[sorted.length - 1];
if (lastCap && lastCap.end > bt.duration + 0.5) {
  issues.push(`Last caption #${lastCap.order} end(${lastCap.end}) > duration(${bt.duration})`);
}

if (issues.length) {
  console.warn("Validation warnings:");
  for (const w of issues) console.warn(`  ⚠ ${w}`);
} else {
  console.log("Validation OK: monotonic, no overlap, within duration.");
}

// Write back
fs.writeFileSync(timingsPath, `${JSON.stringify(bt, null, 2)}\n`);
console.log(`Written: ${path.relative(ROOT, timingsPath)}`);
