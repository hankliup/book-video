#!/usr/bin/env node
/**
 * 从 Pexels 获取氛围图并按剧集顺序落盘为 atmosphere-N.png（720x960 竖版）。
 *
 * 用法: node scripts/fetch-pexels-images.mjs <episode> <queries.json>
 * queries.json 形如:
 *   [ { "query": "vintage french village dusk", "label": "小镇黄昏" }, ... ]
 *
 * 行为:
 *   - 读取 .env 中的 PEXELS_API_KEY（不通过命令行传密钥）
 *   - 优先竖版原图；搜索时 orientation=portrait
 *   - 用 ffmpeg 居中裁切并缩放到 720x960（9:16），保证成片不变形
 *   - 已存在同名文件时跳过，便于增量补图
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = process.cwd();
const [, , episodeName, queriesFile] = process.argv;
if (!episodeName || !queriesFile) {
  console.error("Usage: node scripts/fetch-pexels-images.mjs <episode> <queries.json>");
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
// 三段结构：钩子段的图落 images-hook/，正文段落 images/。
// 用 IMAGES_SUBDIR 切换，两段共用这一个取图脚本，不复制第二份。
const imagesDir = path.join(episodeDir, process.env.IMAGES_SUBDIR || "images");
fs.mkdirSync(imagesDir, { recursive: true });

const queries = JSON.parse(fs.readFileSync(path.resolve(ROOT, queriesFile), "utf8"));
const startIndex = Number(process.env.START_INDEX || 1);
// 默认沿用 720x960（=画幅）。源图明显大于画幅时，渲染器的浮点慢推全程都在降采样，不会上采样发糊。
const [TARGET_W, TARGET_H] = (process.env.TARGET_SIZE || "720x960").split("x").map(Number);
if (!(TARGET_W > 0 && TARGET_H > 0)) throw new Error(`Bad TARGET_SIZE: ${process.env.TARGET_SIZE}`);

function run(command, args) {
  const result = spawnSync(command, args, { cwd: ROOT, encoding: "utf8", shell: false });
  if (result.status !== 0) {
    throw new Error(`${command} failed: ${result.stderr || result.stdout || result.status}`);
  }
  return result;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function search(query, perPage = 8) {
  const url = new URL("https://api.pexels.com/v1/search");
  url.searchParams.set("query", query);
  url.searchParams.set("orientation", "portrait");
  url.searchParams.set("per_page", String(perPage));
  const response = await fetch(url, { headers: { Authorization: API_KEY } });
  if (!response.ok) {
    throw new Error(`Pexels ${response.status} for "${query}": ${(await response.text()).slice(0, 200)}`);
  }
  return (await response.json()).photos || [];
}

function pickPhoto(photos, usedIds) {
  // 优先竖版且未被用过；同等条件下取分辨率最高的一张，保证慢推时仍是降采样
  const fresh = photos.filter((photo) => !usedIds.has(photo.id));
  if (!fresh.length) return null;
  return fresh.reduce((best, photo) =>
    photo.width * photo.height > best.width * best.height ? photo : best);
}

const usedIds = new Set();
const results = [];
// 溯源不能只留在终端输出里：日志一滚就没，prompts.csv 便无 photo id 可写，
// 交付门禁只能靠人回忆。设 PROVENANCE_OUT 时把每条结果实时追加成 JSONL。
const provenanceOut = process.env.PROVENANCE_OUT
  ? path.resolve(ROOT, process.env.PROVENANCE_OUT)
  : null;
function record(entry) {
  results.push(entry);
  if (!provenanceOut) return;
  fs.mkdirSync(path.dirname(provenanceOut), { recursive: true });
  fs.appendFileSync(provenanceOut, `${JSON.stringify(entry)}\n`);
}
for (let index = 0; index < queries.length; index += 1) {
  const { query, label, index: explicitIndexRaw } = queries[index];
  const explicitIndex = Number.isFinite(Number(explicitIndexRaw)) ? Number(explicitIndexRaw) : null;
  const indexNum = explicitIndex !== null ? explicitIndex : (index + startIndex);
  // 非数字 index（如 "result-bridge"）直接用作文件名；数字 index 走 atmosphere-N
  const targetName = typeof explicitIndexRaw === "string" ? explicitIndexRaw : `atmosphere-${indexNum}`;
  const target = path.join(imagesDir, `${targetName}.png`);
  if (fs.existsSync(target)) {
    console.log(`  [${index + 1}] ${label} — 已存在，跳过`);
    record({ index: index + 1, label, file: targetName, path: target, skipped: true });
    continue;
  }
  let photos = [];
  try {
    photos = await search(query);
  } catch (error) {
    console.warn(`  [${index + 1}] ${label} 搜索失败: ${error.message}`);
    record({ index: index + 1, label, file: targetName, query, error: error.message });
    continue;
  }
  const photo = pickPhoto(photos, usedIds);
  if (!photo) {
    console.warn(`  [${index + 1}] ${label} 无可用结果`);
    record({ index: index + 1, label, file: targetName, query, error: "no results" });
    continue;
  }
  usedIds.add(photo.id);
  // 取尽可能高的原图，再按目标画幅居中裁切；不要用 Pexels 预置的 portrait（只有 810x1080）
  const sourceUrl = photo.src.original || photo.src.large2x || photo.src.portrait || photo.src.large;
  const tmpRaw = path.join(imagesDir, `.raw-${index + 1}.jpg`);
  // 一张原图几 MB，链路抖一下就 ECONNRESET / "other side closed"，未捕获时整个进程会当场退出，
  // 后面的图全得重来。所以下载自己重试三次，失败只记这一条，不影响其余条目。
  let downloaded = false;
  for (let attempt = 1; attempt <= 3 && !downloaded; attempt += 1) {
    try {
      const response = await fetch(sourceUrl);
      if (!response.ok) {
        console.warn(`  [${index + 1}] ${label} 下载失败 ${response.status}`);
        break;
      }
      fs.writeFileSync(tmpRaw, Buffer.from(await response.arrayBuffer()));
      downloaded = true;
    } catch (error) {
      console.warn(`  [${index + 1}] ${label} 下载中断（第 ${attempt} 次）: ${error.message}`);
      await sleep(900);
    }
  }
  if (!downloaded) {
    fs.rmSync(tmpRaw, { force: true });
    record({ index: index + 1, label, file: targetName, query, pexelsId: photo.id, error: "download failed" });
    continue;
  }
  // 居中裁切到目标画幅， lanczos 降采样，避免拉伸变形
  run("ffmpeg", [
    "-y", "-hide_banner", "-loglevel", "error",
    "-i", tmpRaw,
    "-vf", `scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase:flags=lanczos,crop=${TARGET_W}:${TARGET_H}`,
    "-frames:v", "1",
    target,
  ]);
  fs.rmSync(tmpRaw, { force: true });
  console.log(`  [${index + 1}] ${label} ← ${query} (pexels #${photo.id}, ${photo.photographer}, ${sourceUrl})`);
  record({ index: index + 1, label, file: targetName, query, path: target, pexelsId: photo.id, photographer: photo.photographer, url: sourceUrl });
}

const failed = results.filter((item) => item.error);
console.log(`\n完成 ${results.length - failed.length}/${queries.length}，失败 ${failed.length}`);
if (failed.length) process.exitCode = 1;
