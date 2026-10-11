#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { resolveScriptVersion } from "./lib/script-version.mjs";
import { validateBodyScript, DEFAULT_SCRIPT_PRESET, NARRATION_RATE_CPS } from "./lib/script-policy.mjs";

const ROOT = process.cwd();
const argv = process.argv.slice(2);
const allowDecorativeSymbols = argv.includes("--allow-decorative-symbols");
// 默认按长版口径校验（4-5 分钟）。短版是例外，需显式 --short，否则「下限当目标」的坑会复发。
const expectedPreset = argv.includes("--short") ? "short" : DEFAULT_SCRIPT_PRESET;
const [episodeName, requestedVersion] = argv.filter((arg) => !arg.startsWith("--"));

if (!episodeName) {
  console.error("Usage: node scripts/validate-script.mjs <episode-name> [script-version] [--short] [--allow-decorative-symbols]");
  process.exit(1);
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

const episodeDir = path.join(ROOT, "episodes", episodeName);
// 三段结构的正文文案是 script-body.csv（钩子 script-hook.csv），历史单段期只有 script-body.csv。
// 两者都找不到才报错，script-body.csv 优先。
const scriptPath = ["script-body.csv"]
  .map((name) => path.join(episodeDir, name))
  .find((p) => fs.existsSync(p));
if (!scriptPath) {
  throw new Error(`Missing script-body.csv: ${episodeDir}`);
}
const version = resolveScriptVersion(episodeDir, requestedVersion);
const lines = fs.readFileSync(scriptPath, "utf8").trim().split(/\r?\n/u);
const headers = parseCsvLine(lines.shift() || "");
const rows = lines
  .filter(Boolean)
  .map((line) => {
    const values = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
  })
  .filter((row) => row.version === version)
  .sort((a, b) => Number(a.order) - Number(b.order));
if (!rows.length) throw new Error(`No script rows found for version ${version}`);

const result = { episode: episodeName, scriptVersion: version, narrationRateCps: NARRATION_RATE_CPS, ...validateBodyScript(rows, { allowDecorativeSymbols, expectedPreset }) };
// 写稿门禁：字幕正文的特殊符号属于必须交付前修掉的问题，不作为可选告警放行。
if (!allowDecorativeSymbols) result.errors.push(...result.warnings);
console.log(JSON.stringify(result, null, 2));
if (result.notes?.length) for (const note of result.notes) console.error(`note: ${note}`);
if (result.errors.length) {
  console.error(result.errors.join("；"));
  process.exit(1);
}
