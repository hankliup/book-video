#!/usr/bin/env node
/**
 * Pexels 取图兜底：search 走 API（小响应，稳定），下载走 curl（--retry + -C - 断点续传）。
 *
 * 为什么不用 fetch-pexels-images.mjs 重试到底：那条路径用 Node fetch 直接拉 src.original，
 * 几 MB 的原图在代理链路上会 "terminated"，三次重试常常全灭，一轮下来只落地两三张。
 * curl 的 --retry-all-errors + -C - 能把断在半路的字节续下去，这是这里唯一能稳定拿到整图的办法。
 *
 * 用法:
 *   HTTP_PROXY=http://127.0.0.1:1082 NODE_USE_ENV_PROXY=1 \
 *   IMAGES_SUBDIR=images-body TARGET_SIZE=1024x1365 \
 *   PROVENANCE_OUT=episodes/<书名>/pexels-fetch-log.jsonl \
 *   node scripts/fetch-pexels-images-curl.mjs <书名> <queries.json>
 *
 * 已存在的文件跳过，与 fetch-pexels-images.mjs 行为一致，可反复运行补齐缺口。
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { spawnCurl } from "./lib/pexels-auth.mjs";

const ROOT = process.cwd();
const [, , episodeName, queriesFile] = process.argv;
if (!episodeName || !queriesFile) {
  console.error("Usage: node scripts/fetch-pexels-images-curl.mjs <episode> <queries.json>");
  process.exit(1);
}

function loadEnv() {
  const envPath = path.join(ROOT, ".env");
  if (!fs.existsSync(envPath)) throw new Error("Missing .env");
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/u)) {
    const match = /^([A-Z0-9_]+)=(.*)$/u.exec(line.trim());
    if (match && !process.env[match[1]]) process.env[match[1]] = match[2];
  }
}
loadEnv();
const API_KEY = process.env.PEXELS_API_KEY;
if (!API_KEY) throw new Error("Missing PEXELS_API_KEY in .env");

const episodeDir = path.join(ROOT, "episodes", episodeName);
const imagesDir = path.join(episodeDir, process.env.IMAGES_SUBDIR || "images");
fs.mkdirSync(imagesDir, { recursive: true });

const queries = JSON.parse(fs.readFileSync(path.resolve(ROOT, queriesFile), "utf8"));
const startIndex = Number(process.env.START_INDEX || 1);
const [TARGET_W, TARGET_H] = (process.env.TARGET_SIZE || "1024x1365").split("x").map(Number);
if (!(TARGET_W > 0 && TARGET_H > 0)) throw new Error(`Bad TARGET_SIZE: ${process.env.TARGET_SIZE}`);

const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || "";
const curlProxyArgs = proxy ? ["-x", proxy] : [];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function record(entry) {
  const out = process.env.PROVENANCE_OUT
    ? path.resolve(ROOT, process.env.PROVENANCE_OUT)
    : null;
  if (!out) return;
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.appendFileSync(out, `${JSON.stringify(entry)}\n`);
}

async function search(query) {
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    // 鉴权头不进 curl 的 argv：`-K -` 让它从 stdin 读 header（scripts/lib/pexels-auth.mjs）。
    // argv 同机任何进程 ps 都读得到，而这台机器常年并行跑多个会话与后台渲染。
    const args = [
      "-sS", "--fail", "-m", "30", "-G", "https://api.pexels.com/v1/search",
      "--data-urlencode", `query=${query}`,
      "--data-urlencode", "orientation=portrait",
      "--data-urlencode", "per_page=8",
      ...curlProxyArgs,
    ];
    const res = spawnCurl(args, API_KEY, { encoding: "utf8" });
    if (res.status === 0 && res.stdout) {
      try { return JSON.parse(res.stdout).photos || []; } catch (e) { /* retry */ }
    }
    if (attempt < 4) await sleep(1500 * attempt);
  }
  return [];
}

/** curl 断点续传下载。返回 true 表示拿到完整文件。 */
function download(url, dest) {
  for (let attempt = 1; attempt <= 6; attempt += 1) {
    const res = spawnSync("curl", [
      "-sS", "--fail",
      "--max-time", "300",
      "--connect-timeout", "30",
      "--retry", "5",
      "--retry-delay", "2",
      "--retry-all-errors",
      "-C", "-",                    // 续传：上次传了多少就接着传
      ...curlProxyArgs,
      "-o", dest,
      url,
    ], { encoding: "utf8" });
    if (res.status === 0) {
      const size = fs.existsSync(dest) ? fs.statSync(dest).size : 0;
      if (size > 50_000) return true;
    }
    if (attempt < 6) sleep(2000 * attempt);
  }
  return false;
}

const usedIds = new Set();
let ok = 0, failed = 0;

for (let index = 0; index < queries.length; index += 1) {
  const { query, label, index: explicitIndexRaw } = queries[index];
  const explicitIndex = Number.isFinite(Number(explicitIndexRaw)) ? Number(explicitIndexRaw) : null;
  const indexNum = explicitIndex !== null ? explicitIndex : (index + startIndex);
  const targetName = typeof explicitIndexRaw === "string" ? explicitIndexRaw : `atmosphere-${indexNum}`;
  const target = path.join(imagesDir, `${targetName}.png`);
  if (fs.existsSync(target)) {
    console.log(`  [${index + 1}] ${label} — 已存在，跳过`);
    record({ index: index + 1, label, file: targetName, query, path: target, skipped: true });
    continue;
  }

  let photos = [];
  try {
    photos = await search(query);
  } catch (error) {
    console.warn(`  [${index + 1}] ${label} 搜索失败: ${error.message}`);
    record({ index: index + 1, label, file: targetName, query, error: error.message });
    failed += 1;
    continue;
  }
  const fresh = photos.filter((p) => !usedIds.has(p.id));
  const pool = fresh.length ? fresh : photos;
  if (!pool.length) {
    console.warn(`  [${index + 1}] ${label} 无可用结果`);
    record({ index: index + 1, label, file: targetName, query, error: "no results" });
    failed += 1;
    continue;
  }
  const photo = pool.reduce((best, p) => (p.width * p.height > best.width * best.height ? p : best));
  usedIds.add(photo.id);

  const sourceUrl = photo.src.original || photo.src.large2x || photo.src.portrait || photo.src.large;
  const tmpRaw = path.join(os.tmpdir(), `pexels-${process.pid}-${indexNum}.jpg`);
  if (!download(sourceUrl, tmpRaw)) {
    fs.rmSync(tmpRaw, { force: true });
    console.warn(`  [${index + 1}] ${label} 下载失败: ${sourceUrl}`);
    record({ index: index + 1, label, file: targetName, query, pexelsId: photo.id, error: "download failed" });
    failed += 1;
    continue;
  }
  const crop = spawnSync("ffmpeg", [
    "-y", "-hide_banner", "-loglevel", "error",
    "-i", tmpRaw,
    "-vf", `scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase:flags=lanczos,crop=${TARGET_W}:${TARGET_H}`,
    "-frames:v", "1", target,
  ], { encoding: "utf8" });
  fs.rmSync(tmpRaw, { force: true });
  if (crop.status !== 0) {
    console.warn(`  [${index + 1}] ${label} 裁切失败: ${crop.stderr}`);
    record({ index: index + 1, label, file: targetName, query, pexelsId: photo.id, error: "crop failed" });
    failed += 1;
    continue;
  }
  ok += 1;
  console.log(`  [${index + 1}] ${label} ← ${query} (pexels #${photo.id}, ${photo.photographer})`);
  record({
    index: index + 1, label, file: targetName, query, path: target,
    pexelsId: photo.id, photographer: photo.photographer, url: sourceUrl,
    crop: `center ${TARGET_W}x${TARGET_H} from src.original`, tool: "pexels-curl",
  });
}

console.log(`\n完成 ${ok}/${queries.length}，失败 ${failed}`);
if (failed) process.exitCode = 1;