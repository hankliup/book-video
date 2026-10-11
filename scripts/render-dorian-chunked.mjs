#!/usr/bin/env node
/**
 * 道林·格雷的画像 —— 分段渲染正文，规避 HyperFrames 单次长会话
 * "Protocol error (Page.captureScreenshot): Target closed" 崩溃。
 * 复用 render-whale-chunked.mjs 的思路，但严格对齐 render-episode-final.mjs
 * 的共享模板样式、混音参数与响度标准。
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = process.cwd();
const LOCAL_HF = "/Users/apple/.workbuddy/binaries/node/workspace/node_modules/hyperframes/dist/cli.js";

const EPISODE_NAME = "道林·格雷的画像";
const PREVIEW_DIR = path.join(ROOT, "tmp", "preview-book-b53f635c07");
const episodeDir = path.join(ROOT, "episodes", EPISODE_NAME);
const timingsPath = path.join(episodeDir, "audio", "body-timings.json");
const rendersDir = path.join(episodeDir, "renders");

// —— 与 render-episode-final.mjs 保持一致的混音常量 ——
const INTRO_TRIM_SECONDS = 1.83;
const INTRO_OFFSET_MS = Math.round(INTRO_TRIM_SECONDS * 1000);
const FINAL_BGM_VOLUME = 0.32;
const INTRO_SCROLL_SFX_START_SECONDS = 1.08;
const INTRO_SCROLL_SFX_END_SECONDS = 2.38;
const INTRO_SCROLL_SFX_FADE_OUT_SECONDS = 0.2;
const INTRO_SCROLL_SFX_VOLUME = 1.4;
const INTRO_WATER_SFX_START_SECONDS = Number((INTRO_TRIM_SECONDS + 0.32).toFixed(2));
const INTRO_WATER_SFX_VOLUME = 1.0;
const SCROLL_SFX = path.join(ROOT, "assets", "sfx", "gear-scroll.mp3");
const WATER_SFX = path.join(ROOT, "assets", "sfx", "water.mp3");
const bgmPath = path.join(ROOT, "assets", "bgm", "Where are you（low）.mp3");
const introVideo = path.join(PREVIEW_DIR, "intro", "renders", "intro.mp4");
const introStory = path.join(PREVIEW_DIR, "audio", "intro-voiceover-story.mp3");
const bodyStory = path.join(episodeDir, "audio", "body-voiceover-story.mp3");

const MAX_WINDOW = 10;

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { cwd: opts.cwd || ROOT, stdio: "inherit", shell: false });
}

function probeDur(p) {
  const r = spawnSync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", p,
  ], { encoding: "utf8" });
  return Number(r.stdout?.trim());
}

const timings = JSON.parse(fs.readFileSync(timingsPath, "utf8"));
const bodyDur = Number(timings.duration);
const caps = (timings.captions || []).slice().sort((a, b) => Number(a.order) - Number(b.order));
console.log(`Body duration: ${bodyDur.toFixed(2)}s, ${caps.length} captions`);

// —— 按字幕边界切窗，单窗不超过 MAX_WINDOW，末尾余段继续细分 ——
const windows = [];
let t0 = 0;
let idx = 0;
while (t0 < bodyDur - 0.05) {
  while (idx < caps.length && Number(caps[idx].end) <= t0 + 0.001) idx += 1;
  let end = t0;
  while (idx < caps.length) {
    const cEnd = Number(caps[idx].end);
    if (cEnd - t0 <= MAX_WINDOW && cEnd > end) {
      end = cEnd;
      idx += 1;
    } else {
      break;
    }
  }
  if (end <= t0) end = Math.min(bodyDur, t0 + MAX_WINDOW);
  windows.push([t0, Number(end.toFixed(3))]);
  t0 = end;
}
console.log(`Created ${windows.length} windows (<=${MAX_WINDOW}s each)`);

const workRoot = path.join(ROOT, "tmp", "dorian-chunks");
fs.rmSync(workRoot, { recursive: true, force: true });
fs.mkdirSync(workRoot, { recursive: true });
fs.mkdirSync(rendersDir, { recursive: true });

const bodySrc = path.join(PREVIEW_DIR, "body");
if (!fs.existsSync(bodySrc)) throw new Error(`缺少正文合成目录: ${bodySrc}`);

const chunkVideos = [];
for (let wi = 0; wi < windows.length; wi++) {
  const [ws, we] = windows[wi];
  const wLen = Number((we - ws).toFixed(3));
  const tag = String(wi + 1).padStart(3, "0");
  const chunkDir = path.join(workRoot, `c${tag}`);
  fs.rmSync(chunkDir, { recursive: true, force: true });
  fs.cpSync(bodySrc, chunkDir, { recursive: true });
  fs.mkdirSync(path.join(chunkDir, "renders"), { recursive: true });

  const htmlPath = path.join(chunkDir, "index.html");
  let html = fs.readFileSync(htmlPath, "utf8");
  html = html.replace(/data-duration="[\d.]+"/, `data-duration="${wLen.toFixed(2)}"`);
  html = html.replace(/<script src="https:\/\/cdn\.jsdelivr\.net[^"]*"><\/script>/, '<script src="gsap.min.js"></script>');
  html = html.replace(
    'window.__timelines["main"] = tl;',
    `window.__timelines["main"] = tl;\n      tl.shiftChildren(${(-ws).toFixed(3)}, false);`,
  );
  fs.writeFileSync(htmlPath, html);

  const outFile = path.join(chunkDir, "renders", `chunk-${tag}.mp4`);
  let ok = false;
  for (let attempt = 1; attempt <= 3 && !ok; attempt++) {
    console.log(`\n▶ Chunk ${wi + 1}/${windows.length} [${ws.toFixed(1)}–${we.toFixed(1)}s] attempt ${attempt}`);
    const r = run("node", [
      LOCAL_HF, "render",
      "--quality", "standard",
      "--fps", "30",
      "--protocol-timeout", "900000",
      "--workers", "1",
      "--browser-timeout", "120",
      "--output", `renders/chunk-${tag}.mp4`,
    ], { cwd: chunkDir });
    if (r.status === 0 && fs.existsSync(outFile) && probeDur(outFile) > wLen * 0.7) ok = true;
    else console.warn("  渲染失败或时长不足，重试…");
  }
  if (!ok) {
    console.error(`✗ Chunk ${wi + 1} 连续 3 次失败`);
    process.exit(1);
  }
  chunkVideos.push(outFile);
  console.log(`  ✓ Chunk ${wi + 1} (${probeDur(outFile).toFixed(2)}s)`);
  fs.rmSync(chunkDir, { recursive: true, force: true });
}

console.log("\n=== 拼接正文 ===");
const bodyOut = path.join(workRoot, "body.mp4");
fs.writeFileSync(path.join(workRoot, "list.txt"), chunkVideos.map(v => `file '${v}'`).join("\n"));
run("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
  "-i", path.join(workRoot, "list.txt"), "-c", "copy", bodyOut]);
console.log(`✓ 正文 ${probeDur(bodyOut).toFixed(2)}s`);

console.log("\n=== 混音合成成片 ===");
const finalDuration = Number((INTRO_TRIM_SECONDS + bodyDur).toFixed(2));
const bgmTail = Number((finalDuration - INTRO_TRIM_SECONDS).toFixed(2));
const scrollDur = Number((INTRO_SCROLL_SFX_END_SECONDS - INTRO_SCROLL_SFX_START_SECONDS).toFixed(2));
const scrollFade = Number((scrollDur - INTRO_SCROLL_SFX_FADE_OUT_SECONDS).toFixed(2));
const outputPath = path.join(rendersDir, `${EPISODE_NAME}-final.mp4`);

const fc = [
  `[0:v]trim=0:${INTRO_TRIM_SECONDS},setpts=PTS-STARTPTS[v0]`,
  `[1:v]trim=0:${bodyDur.toFixed(2)},setpts=PTS-STARTPTS[v1]`,
  "[v0][v1]concat=n=2:v=1:a=0[v]",
  "[2:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[introa]",
  `[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_OFFSET_MS}|${INTRO_OFFSET_MS},volume=1.0[bodya]`,
  `[4:a]atrim=0:${bgmTail},asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_OFFSET_MS}|${INTRO_OFFSET_MS},volume=${FINAL_BGM_VOLUME},afade=t=in:st=0:d=0.5,afade=t=out:st=${Number((bgmTail - 0.8).toFixed(2))}:d=0.8[bgm]`,
  `[5:a]atrim=0:${scrollDur},asetpts=PTS-STARTPTS,aresample=48000,volume=${INTRO_SCROLL_SFX_VOLUME},afade=t=in:st=0:d=0.01,afade=t=out:st=${scrollFade}:d=${INTRO_SCROLL_SFX_FADE_OUT_SECONDS},adelay=${Math.round(INTRO_SCROLL_SFX_START_SECONDS * 1000)}|${Math.round(INTRO_SCROLL_SFX_START_SECONDS * 1000)}[scrollsfx]`,
  `[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=${INTRO_WATER_SFX_VOLUME},adelay=${Math.round(INTRO_WATER_SFX_START_SECONDS * 1000)}|${Math.round(INTRO_WATER_SFX_START_SECONDS * 1000)}[watersfx]`,
  "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
].join(";");

const mux = run("ffmpeg", [
  "-y", "-i", introVideo, "-i", bodyOut, "-i", introStory, "-i", bodyStory,
  "-stream_loop", "-1", "-i", bgmPath, "-i", SCROLL_SFX, "-i", WATER_SFX,
  "-filter_complex", fc,
  "-map", "[v]", "-map", "[a]",
  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", outputPath,
]);
if (mux.status !== 0) process.exit(mux.status ?? 1);

console.log(`\n✅ 成片: ${outputPath}`);
console.log(`   时长 ${probeDur(outputPath).toFixed(2)}s (片头 ${INTRO_TRIM_SECONDS}s + 正文 ${bodyDur.toFixed(2)}s)`);
console.log(`   分段数 ${chunkVideos.length}`);
