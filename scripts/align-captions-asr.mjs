// Precise caption alignment using whisper ASR segment-level timestamps.
// Reads audio/asr/body.json (transcription offsets in ms), maps segments to
// script-body.csv rows in order, and rewrites body-timings.json captions.
// Handles the one merge case where a single script line was spoken as two
// ASR segments (detected by count mismatch + greedy length-based grouping).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const episode = process.argv[2] || "百年孤独";
const epDir = path.join(repoRoot, "episodes", episode);

const asr = JSON.parse(fs.readFileSync(path.join(epDir, "audio", "asr", "body.json"), "utf8"));
const timingsPath = path.join(epDir, "audio", "body-timings.json");
const timings = JSON.parse(fs.readFileSync(timingsPath, "utf8"));

// Parse script-body.csv (version,order,text) -> ordered rows
const csvRaw = fs.readFileSync(path.join(epDir, "script-body.csv"), "utf8").trim();
const csvLines = csvRaw.split(/\r?\n/);
const header = csvLines[0].split(",");
const iText = header.indexOf("text");
const iOrder = header.indexOf("order");
const rows = csvLines.slice(1).map((ln) => {
  // naive CSV: text has no commas here, but guard anyway by splitting first N-1
  const parts = ln.split(",");
  const order = Number(parts[iOrder]);
  const text = parts.slice(iText).join(",");
  return { order, text };
});

// ASR segments with ms offsets
const segs = (asr.transcription || []).map((s) => ({
  from: s.offsets.from,
  to: s.offsets.to,
  text: (s.text || "").trim(),
}));

const skip = Number(timings.skipLeadingSegments || 0);
const eff = segs.slice(skip); // drop leading title segment(s)

console.log(`script rows: ${rows.length} | asr eff segments: ${eff.length} (skipped ${skip})`);

// Char-count DP grouping. ASR may mis-transcribe characters, but in Chinese
// each character is one syllable, so a segment's character count closely
// matches its script line's character count even with wrong characters. We
// partition the ordered ASR segments into rows.length contiguous groups that
// minimize the total |scriptLineChars - groupChars| difference. This is far
// more robust than silence-gap heuristics (排比句 connect with tiny pauses).
if (eff.length < rows.length) {
  throw new Error(`Fewer ASR segments (${eff.length}) than script rows (${rows.length}); cannot align by grouping.`);
}
const norm = (s) => s.replace(/[，。、！？,.!?\s]/g, "");
const cc = (s) => norm(s).length; // char count (punctuation stripped)
const rowLens = rows.map((r) => cc(r.text));
const segLens = eff.map((s) => cc(s.text));
// prefix sums of segment char counts
const pre = [0];
for (const l of segLens) pre.push(pre[pre.length - 1] + l);
const R = rows.length, S = eff.length;
const INF = Infinity;
// dp[i][j] = min cost assigning first i rows to first j segments
const dp = Array.from({ length: R + 1 }, () => new Array(S + 1).fill(INF));
const back = Array.from({ length: R + 1 }, () => new Array(S + 1).fill(-1));
dp[0][0] = 0;
for (let i = 1; i <= R; i++) {
  // row i (1-based) must take >=1 segment; leave enough for remaining rows
  for (let j = i; j <= S - (R - i); j++) {
    for (let k = i - 1; k < j; k++) { // previous boundary at k segments
      if (dp[i - 1][k] === INF) continue;
      const groupChars = pre[j] - pre[k];
      const cost = dp[i - 1][k] + Math.abs(rowLens[i - 1] - groupChars);
      if (cost < dp[i][j]) { dp[i][j] = cost; back[i][j] = k; }
    }
  }
}
// reconstruct boundaries
const bounds = new Array(R + 1);
bounds[R] = S;
for (let i = R; i >= 1; i--) bounds[i - 1] = back[i][bounds[i]];
console.log(`DP total char-diff cost: ${dp[R][S]}`);

const captions = [];
for (let ri = 0; ri < rows.length; ri++) {
  const a = bounds[ri], b = bounds[ri + 1]; // segments [a, b)
  const first = eff[a];
  const last = eff[b - 1];
  captions.push({
    order: rows[ri].order,
    start: +(first.from / 1000).toFixed(3),
    end: +(last.to / 1000).toFixed(3),
    _script: rows[ri].text,
    _asr: eff.slice(a, b).map((e) => e.text).join(" / "),
    _len: `${rowLens[ri]}vs${pre[b] - pre[a]}`,
  });
}

// Print alignment table for verification
console.log("\n=== ALIGNMENT (order | start->end | script ↔ asr) ===");
for (const c of captions) {
  console.log(
    `${String(c.order).padStart(2)} | ${c.start.toFixed(2)}->${c.end.toFixed(2)} (${(c.end - c.start).toFixed(2)}s) [${c._len}] | ${c._script}  ↔  ${c._asr}`
  );
}

// Write back clean captions (strip debug fields)
timings.captions = captions.map(({ order, start, end }) => ({ order, start, end }));
timings.alignment = {
  ...(timings.alignment || {}),
  method: "asr-segments",
  requiresAgentReview: false,
  asrAligned: true,
};
timings.source = "script-body.csv subtitle truth with asr-segments (word/segment precise)";
fs.writeFileSync(timingsPath, JSON.stringify(timings, null, 2));
console.log(`\nWrote ${captions.length} captions to ${path.relative(repoRoot, timingsPath)}`);
