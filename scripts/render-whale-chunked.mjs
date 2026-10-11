#!/usr/bin/env node
/**
 * 白鲸 episode 分段渲染脚本
 * 将 body 拆成 ~10s 片段，每段独立 Chrome 会话，避免单次会话崩溃
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = process.cwd();
const LOCAL_HF = "/Users/apple/.workbuddy/binaries/node/workspace/node_modules/hyperframes/dist/cli.js";

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { cwd: opts.cwd || ROOT, stdio: "inherit", shell: false });
  return r;
}

function probeDur(p) {
  const r = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", p], { encoding: "utf8" });
  return Number(r.stdout?.trim());
}

// ---- Config ----
const episodeDir = path.join(ROOT, "episodes", "白鲸");
const timingsPath = path.join(episodeDir, "audio", "body-timings.json");
const timings = JSON.parse(fs.readFileSync(timingsPath, "utf8"));
const bodyDur = Number(timings.duration);
const caps = (timings.captions || []).sort((a, b) => Number(a.order) - Number(b.order));
console.log(`Body duration: ${bodyDur.toFixed(2)}s, ${caps.length} captions`);

// Build windows: align to caption boundaries, max 10s each
const windows = [];
let t0 = 0;
for (const cap of caps) {
  const cStart = Number(cap.start);
  const cEnd = Number(cap.end);
  if (cStart <= t0) continue;
  if (t0 === 0) {
    // First window starts at 0
    if (cEnd <= 10) {
      windows.push([0, cEnd]);
      t0 = cEnd;
    } else {
      windows.push([0, 10]);
      t0 = 10;
    }
  } else {
    if (cStart >= t0) {
      const wEnd = Math.min(cEnd, t0 + 10);
      if (wEnd > t0) {
        windows.push([t0, wEnd]);
        t0 = wEnd;
      }
    }
  }
}
if (t0 < bodyDur - 0.1) {
  // 末尾余段可能很长（最后一条字幕后到旁白结束），必须再按 MAX_WINDOW 细分，
  // 否则单个超长窗口会再次触发 Chrome "Target closed" 崩溃。
  const MAX_WINDOW = 10;
  let seg = t0;
  while (bodyDur - seg > 0.05) {
    const segEnd = Math.min(bodyDur, seg + MAX_WINDOW);
    windows.push([seg, segEnd]);
    seg = segEnd;
  }
}
console.log(`Created ${windows.length} windows`);
for (let i = 0; i < windows.length; i++) {
  const [s, e] = windows[i];
  console.log(`  Window ${i + 1}: ${s.toFixed(2)}–${e.toFixed(2)}s (${(e - s).toFixed(2)}s)`);
}

// ---- Setup dirs ----
const workRoot = path.join(ROOT, "tmp", "whale-chunks");
const rendersDir = path.join(episodeDir, "renders");
fs.rmSync(workRoot, { recursive: true, force: true });
fs.mkdirSync(path.join(workRoot, "renders"), { recursive: true });
fs.mkdirSync(rendersDir, { recursive: true });

// Copy body HTML template
const bodySrc = path.join(ROOT, "tmp", "preview-book-f03c46a261", "body");
fs.cpSync(bodySrc, path.join(workRoot, "body"), { recursive: true });

// Process voiceover
const introVoice = path.join(ROOT, "assets", "template-audio", "intro-voiceover.mp3");
const bodyVoice = path.join(episodeDir, "audio", "body-voiceover.mp3");
const introStory = path.join(workRoot, "intro-voiceover-story.mp3");
const bodyStory = path.join(episodeDir, "audio", "body-voiceover-story.mp3");
run("node", ["scripts/process-voiceover.mjs", introVoice, introStory, "story"]);
run("node", ["scripts/process-voiceover.mjs", bodyVoice, bodyStory, "story"]);

// Render each window
const chunkVideos = [];
for (let wi = 0; wi < windows.length; wi++) {
  const [ws, we] = windows[wi];
  const wLen = we - ws;
  const chunkDir = path.join(workRoot, `c${String(wi + 1).padStart(3, "0")}`);
  fs.rmSync(chunkDir, { recursive: true, force: true });
  fs.mkdirSync(path.join(chunkDir, "renders"), { recursive: true });
  fs.cpSync(path.join(workRoot, "body"), chunkDir, { recursive: true });

  // Modify HTML for this window
  const htmlPath = path.join(chunkDir, "index.html");
  let html = fs.readFileSync(htmlPath, "utf8", "utf8");
  html = html.replace(/data-duration="[\d.]+"/, `data-duration="${wLen.toFixed(2)}"`);
  html = html.replace(/<script src="https:\/\/cdn\.jsdelivr\.net[^"]*"><\/script>/, '<script src="gsap.min.js"></script>');
  html = html.replace(
    'window.__timelines["main"] = tl;',
    `window.__timelines["main"] = tl;\n      tl.shiftChildren(${(-ws).toFixed(3)}, false);`
  );
  fs.writeFileSync(htmlPath, html);

  const outFile = path.join(chunkDir, "renders", `chunk-${String(wi + 1).padStart(3, "0")}.mp4`);
  const hfArgs = ["render", "--quality", "standard", "--protocol-timeout", "1800000", "--workers", "1", "--output", "renders/chunk-" + String(wi + 1).padStart(3, "0") + ".mp4"];

  let ok = false;
  for (let attempt = 1; attempt <= 3 && !ok; attempt++) {
    console.log(`\n▶ Chunk ${wi + 1}/${windows.length} [${ws.toFixed(1)}–${we.toFixed(1)}s] attempt ${attempt}`);
    const r = spawnSync("node", [LOCAL_HF, ...hfArgs], { cwd: chunkDir, stdio: "inherit" });
    if (r.status === 0 && fs.existsSync(outFile) && probeDur(outFile) > wLen * 0.3) {
      ok = true;
    } else {
      console.warn(`  Failed/short, retrying…`);
    }
  }
  if (!ok) {
    console.error(`✗ Chunk ${wi + 1} FAILED after 3 attempts`);
    process.exit(1);
  }
  const dur = probeDur(outFile);
  chunkVideos.push(outFile);
  console.log(`  ✓ Chunk ${wi + 1} done (${dur.toFixed(2)}s)`);
}

// Concatenate chunks
console.log("\n=== Concatenating chunks ===");
const bodyOut = path.join(rendersDir, "body-whale.mp4");
const listPath = path.join(workRoot, "list.txt");
fs.writeFileSync(listPath, chunkVideos.map(v => `file '${v}'`).join("\n"));
run("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", bodyOut]);
console.log(`✓ Body assembled: ${bodyOut} (${probeDur(bodyOut).toFixed(2)}s)`);

// Final mux
console.log("\n=== Final mux ===");
const introVideo = path.join(ROOT, "tmp", "preview-book-f03c46a261", "intro", "renders", "intro.mp4");
const bgmPath = path.join(ROOT, "assets", "bgm", "Where are you（low）.mp3");
const SCROLL_SFX = path.join(ROOT, "assets", "sfx", "gear-scroll.mp3");
const WATER_SFX = path.join(ROOT, "assets", "sfx", "water.mp3");
const INTRO_TRIM = 1.83;
const FINAL_BGM_VOLUME = 0.32;
const bgmSlug = "where-are-you-low";

const outputPath = path.join(rendersDir, `whale-final-${bgmSlug}-story-voice-bgm-standard.mp4`);

run("ffmpeg", [
  "-y", "-i", introVideo, "-i", bodyOut,
  "-i", introStory, "-i", bodyStory,
  "-stream_loop", "-1", "-i", bgmPath,
  "-i", SCROLL_SFX, "-i", WATER_SFX,
  "-filter_complex", [
    `[0:v]trim=0:${INTRO_TRIM},setpts=PTS-STARTPTS[v0]`,
    `[1:v]trim=0:${bodyDur.toFixed(2)},setpts=PTS-STARTPTS[v1]`,
    "[v0][v1]concat=n=2:v=1:a=0[v]",
    "[2:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[introa]",
    `[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${Math.round(INTRO_TRIM * 1000)}|${Math.round(INTRO_TRIM * 1000)},volume=1.0[bodya]`,
    `[4:a]atrim=0:${bodyDur.toFixed(2)},asetpts=PTS-STARTPTS,aresample=48000,adelay=${Math.round(INTRO_TRIM * 1000)}|${Math.round(INTRO_TRIM * 1000)},volume=${FINAL_BGM_VOLUME},afade=t=in:st=0:d=0.5[bgm]`,
    "[5:a]atrim=0:1.3,asetpts=PTS-STARTPTS,aresample=48000,volume=1.4,afade=t=in:st=0:d=0.01,afade=t=out:st=1.1:d=0.2,adelay=1080|1080[scrollsfx]",
    `[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,adelay=${Math.round((INTRO_TRIM + 0.32) * 1000)}|${Math.round((INTRO_TRIM + 0.32) * 1000)}[watersfx]`,
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
  ].join(";"),
  "-map", "[v]", "-map", "[a]",
  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", outputPath,
]);

const finalDur = probeDur(outputPath);
const probe = spawnSync("ffprobe", ["-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", outputPath], { encoding: "utf8" });
const pj = JSON.parse(probe.stdout || "{}");
const v = pj.streams?.find(s => s.codec_type === "video");
console.log(`\n✅ Final video: ${outputPath}`);
console.log(`   Duration: ${finalDur.toFixed(2)}s (intro ${INTRO_TRIM}s + body ${bodyDur.toFixed(2)}s)`);
console.log(`   Dimensions: ${v?.width}x${v?.height}`);
console.log(`   Chunks rendered: ${chunkVideos.length}`);
