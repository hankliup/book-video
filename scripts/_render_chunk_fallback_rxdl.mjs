#!/usr/bin/env node
/**
 * 单段分段渲染兜底：把 body 按固定 15s 窗口切成 N 段，逐段起独立 Chrome 会话渲染。
 * 复用已有 tmp/chunks-book-<slug>/cXX 目录（首次运行需要 render-body-chunked-xw 已建好 c01 目录，
 * 否则直接建 c0X 并从 bodyDir 复制）。跳过已存在且时长足够的段。
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";
import { resolveScriptVersion } from "./lib/script-version.mjs";

const ROOT = process.cwd();
const LOCAL_HYPERFRAMES_CLI = "/Users/apple/.workbuddy/binaries/node/workspace/node_modules/hyperframes/dist/cli.js";
const CHUNK_SECONDS = Number(process.env.CHUNK_SECONDS || 15);

const episodeName = process.argv[2];
if (!episodeName) { console.error("Usage: node scripts/_render_chunk_fallback_rxdl.mjs <episode>"); process.exit(1); }

const slug = slugifyEpisodeName(episodeName);
const episodeDir = path.join(ROOT, "episodes", episodeName);
const scriptVersion = resolveScriptVersion(episodeDir, undefined);
const timingsPath = path.join(episodeDir, "audio", "body-timings.json");
const bodyDir = path.join(ROOT, "tmp", `preview-${slug}`, "body");
const chunkWorkRoot = path.join(ROOT, "tmp", `chunks-${slug}`);

function probeDuration(p) {
  const r = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", p], { encoding: "utf8" });
  const d = Number(r.stdout?.trim());
  return r.status === 0 && Number.isFinite(d) ? d : 0;
}

const timings = JSON.parse(fs.readFileSync(timingsPath, "utf8"));
const bodyDuration = Number(timings.duration);
if (!Number.isFinite(bodyDuration) || bodyDuration <= 0) throw new Error("Invalid body duration");

// 固定 15s 窗口切分（最后一段允许 < 15s）
const windows = [];
let t0 = 0;
while (t0 < bodyDuration - 0.05) {
  const t1 = Math.min(t0 + CHUNK_SECONDS, bodyDuration);
  windows.push([Number(t0.toFixed(3)), Number(t1.toFixed(3))]);
  t0 = t1;
}
console.log(`分段：${windows.length} 段，每段≈${CHUNK_SECONDS}s，body=${bodyDuration.toFixed(2)}s`);

// 逐段渲染（跳过已完成的；缺失目录则从 bodyDir 复制）
const chunkVideos = [];
for (let i = 0; i < windows.length; i += 1) {
  const [wt0, wt1] = windows[i];
  const chunkLen = Number((wt1 - wt0).toFixed(3));
  const cdir = path.join(chunkWorkRoot, `c${String(i + 1).padStart(2, "0")}`);
  const out = path.join(cdir, "renders", `body-c${String(i + 1).padStart(2, "0")}.mp4`);

  // 确保段目录存在
  if (!fs.existsSync(cdir) || !fs.existsSync(path.join(cdir, "index.html"))) {
    fs.rmSync(cdir, { recursive: true, force: true });
    fs.mkdirSync(path.join(cdir, "renders"), { recursive: true });
    fs.cpSync(bodyDir, cdir, { recursive: true });
    const cHtmlPath = path.join(cdir, "index.html");
    let cHtml = fs.readFileSync(cHtmlPath, "utf8");
    cHtml = cHtml.replace(/data-duration="[\d.]+"/, `data-duration="${chunkLen}"`);
    cHtml = cHtml.replace(/<script src="https:\/\/cdn\.jsdelivr\.net[^"]*"><\/script>/, '<script src="gsap.min.js"></script>');
    cHtml = cHtml.replace(
      'window.__timelines["main"] = tl;',
      `window.__timelines["main"] = tl;\n      tl.shiftChildren(${(-wt0).toFixed(3)}, false);`,
    );
    fs.writeFileSync(cHtmlPath, cHtml);
    console.log(`  · 段 ${i + 1} 目录已重建`);
  }

  if (fs.existsSync(out) && probeDuration(out) > chunkLen * 0.5) {
    console.log(`✓ 段 ${i + 1} 已存在，跳过 (${probeDuration(out).toFixed(2)}s)`);
    chunkVideos.push(out);
    continue;
  }
  let ok = false;
  for (let attempt = 1; attempt <= 4 && !ok; attempt += 1) {
    console.log(`\n▶ 段 ${i + 1}/${windows.length} [${wt0.toFixed(1)}–${wt1.toFixed(1)}s] 尝试 ${attempt}`);
    const r = spawnSync("node", [LOCAL_HYPERFRAMES_CLI, "render", "--low-memory-mode", "--quality", "standard", "--protocol-timeout", "1800000", "--workers", "1", "--output", "renders/body-c" + String(i + 1).padStart(2, "0") + ".mp4"], { cwd: cdir, stdio: "inherit" });
    if (r.status === 0 && fs.existsSync(out) && probeDuration(out) > chunkLen * 0.5) {
      ok = true;
    } else {
      console.warn(`  段 ${i + 1} 渲染失败/过短，重试…`);
    }
  }
  if (!ok) { console.error(`✗ 段 ${i + 1} 渲染失败`); process.exit(1); }
  chunkVideos.push(out);
  console.log(`  ✓ 段 ${i + 1} 完成 (${probeDuration(out).toFixed(2)}s)`);
}

// concat 重组 body
const rendersDir = path.join(episodeDir, "renders");
fs.mkdirSync(rendersDir, { recursive: true });
const bodyOut = path.join(rendersDir, "body-xwz.mp4");
const listPath = path.join(chunkWorkRoot, "list.txt");
fs.writeFileSync(listPath, chunkVideos.map((v) => `file '${v}'`).join("\n"));
spawnSync("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", bodyOut], { stdio: "inherit" });
console.log(`\n✓ body 重组：${bodyOut} (${probeDuration(bodyOut).toFixed(2)}s)`);
