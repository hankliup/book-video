#!/usr/bin/env node
// 重建本地候选池 data/book-pipeline.csv。
// 来历：2026-10-01 一次并行迁移把 episodes/待上传 整目录挪到了读书视频/待上传/<分类>，
// 同一天 data/book-pipeline.csv 在两次读写之间消失，record-book-candidates.mjs 见文件不在
// 就退回 book-pipeline.example.csv 重写，143KB 的历史候选被截成 10 行种子数据。
// 池子本身没有备份（无 Time Machine、无 APFS 快照、回收站为空），唯一完整的真源是每期 brief.json：
// 已交付的 115 期都带 display_title / author / source_book_id / source_channel / 评分 / 出版社 / ISBN / emotion。
// 所以重建口径是「从每期 brief.json 反推 selected 行」，而不是恢复那张搜索结果表。
//
// 用法：
//   node scripts/rebuild-book-pipeline.mjs            # 只报告将要写入什么
//   node scripts/rebuild-book-pipeline.mjs --apply    # 写出 candidates JSON 并调用写入器合并
//
// 本脚本只增不改：已存在的行由 record-book-candidates.mjs 按键合并，绝不整表重写。

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = process.cwd();
const APPLY = process.argv.includes("--apply");

function repoRoot() {
  const top = spawnSync("git", ["rev-parse", "--show-toplevel"], { cwd: ROOT, encoding: "utf8" });
  if (top.status === 0 && top.stdout.trim()) return top.stdout.trim();
  return ROOT;
}

const REPO = repoRoot();

// 交付集的新家在一个分类共享目录里（读书视频/待上传/<分类>），旧的期内位置保留兼容。
function episodeRoots() {
  const roots = [path.join(REPO, "episodes")];
  const category = path.basename(path.dirname(REPO));
  const shared = path.join(REPO, "..", "..", "待上传", category);
  roots.push(shared);
  roots.push(path.join(REPO, "..", "..", "待上传"));
  return roots.filter((r) => fs.existsSync(r));
}

function walkBriefs(dir, acc, depth = 0) {
  if (depth > 3) return acc;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    const briefPath = path.join(full, "brief.json");
    if (fs.existsSync(briefPath)) acc.push({ episode: entry.name, briefPath });
    else walkBriefs(full, acc, depth + 1);
  }
  return acc;
}

function readBrief(briefPath) {
  try {
    return JSON.parse(fs.readFileSync(briefPath, "utf8"));
  } catch {
    return null;
  }
}

function toCandidate(brief, episode) {
  const displayTitle = String(brief.display_title || brief.title || episode).trim();
  if (!displayTitle) return null;
  const ratingCount = brief.newRatingCount ?? brief.rating_count ?? "";
  return {
    status: "selected",
    priority: "high",
    display_title: displayTitle,
    source_title: String(brief.source_title || displayTitle).trim(),
    author: String(brief.author || "").trim(),
    source_channel: String(brief.source_channel || "weread"),
    source_book_id: String(brief.source_book_id ?? ""),
    category: String(brief.category || ""),
    rating: brief.newRating ?? brief.rating ?? "",
    rating_count: ratingCount,
    reading_count: brief.readingCount ?? brief.reading_count ?? "",
    highlight_top_count: brief.highlightTopCount ?? "",
    reviews_count: brief.deepVUniqueCount ?? brief.reviewsCount ?? "",
    resonance_score: String(brief.resonance_score || "high"),
    emotion_theme: Array.isArray(brief.emotion)
      ? brief.emotion.join(";")
      : String(brief.emotion || brief.emotion_theme || ""),
    selection_reason: `由 ${episode}/brief.json 重建（候选池在 2026-10-01 的一次并行迁移中被截断，无备份可还原，本行取本期成稿使用的书目溯源字段）`,
    video_project: episode,
    notes: [brief.translator && `译者${brief.translator}`, brief.publisher, brief.publishTime, brief.isbn]
      .filter(Boolean)
      .join("；"),
  };
}

const found = [];
const seen = new Set();
for (const root of episodeRoots()) {
  for (const { episode, briefPath } of walkBriefs(root, [])) {
    const brief = readBrief(briefPath);
    if (!brief) continue;
    const candidate = toCandidate(brief, episode);
    if (!candidate) continue;
    // 同一本书在旧位置和新位置各有一份时，保留第一次读到的（更靠近本期成稿）。
    const key = `${candidate.source_channel}:${candidate.source_book_id || candidate.display_title}`;
    if (seen.has(key)) continue;
    seen.add(key);
    found.push(candidate);
  }
}

const pipelinePath = path.join(REPO, "data", "book-pipeline.csv");
const existingRows = fs.existsSync(pipelinePath)
  ? fs.readFileSync(pipelinePath, "utf8").trim().split(/\r?\n/u).length - 1
  : 0;

const report = {
  episodeRoots: episodeRoots().map((r) => path.relative(REPO, r) || "."),
  rebuiltFromBriefs: found.length,
  pipelineRowsBefore: existingRows,
  pipelineExists: fs.existsSync(pipelinePath),
  apply: APPLY,
};

if (!APPLY) {
  report.next = `node scripts/rebuild-book-pipeline.mjs --apply`;
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

const outPath = path.join(REPO, "data", ".internal", "rebuild-book-pipeline.candidates.json");
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, `${JSON.stringify({ candidates: found }, null, 2)}\n`, { mode: 0o600 });
const merge = spawnSync("node", ["scripts/record-book-candidates.mjs", path.relative(REPO, outPath)], {
  cwd: REPO,
  encoding: "utf8",
});
process.stdout.write(merge.stdout || "");
process.stderr.write(merge.stderr || "");
console.log(JSON.stringify({ ...report, candidatesFile: path.relative(REPO, outPath) }, null, 2));
process.exit(merge.status ?? 1);
