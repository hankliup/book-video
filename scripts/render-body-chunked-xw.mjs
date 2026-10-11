#!/usr/bin/env node
/**
 * 分段渲染《小王子》类长片成片（奥德赛工作流的"长片"分支）。
 *
 * 背景：HyperFrames 单次渲染长 composition 时，Chrome 渲染会话约 35s 壁钟会崩
 * （Page.captureScreenshot: Target closed）。单段渲染壁钟上限约束的是"一次会话时长"，
 * 不是视频总时长。破解法：把 body 按 ~15s 视频切成 N 段，每段独立起一个 Chrome 会话
 * 渲染（段落壁钟 < 35s 上限），再 concat 重组，最后与 intro + 配音 + BGM + SFX 手 mux。
 *
 * 依赖：先跑 `node scripts/create-episode-preview-xw.mjs <ep> <ver>` 生成
 *       tmp/preview-<slug>/{intro,body}（body/index.html + intro/renders/intro.mp4 复用）。
 *
 * 用法：node scripts/render-body-chunked-xw.mjs <episode> [version] [bgm-name] [chunkSeconds]
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";
import { resolveScriptVersion } from "./lib/script-version.mjs";

const ROOT = process.cwd();
const LOCAL_HYPERFRAMES_CLI = "/Users/apple/.workbuddy/binaries/node/workspace/node_modules/hyperframes/dist/cli.js";
const INTRO_TRIM_SECONDS = Number(process.env.INTRO_TRIM_SECONDS || "1.83");
const INTRO_OFFSET_MS = Math.round(INTRO_TRIM_SECONDS * 1000);
const FINAL_BGM_VOLUME = 0.32;

const [episodeName, requestedVersion, bgmInput, chunkSecondsArg] = process.argv.slice(2);
if (!episodeName) { console.error("Usage: node scripts/render-body-chunked-xw.mjs <episode> [version] [bgm] [chunkSeconds]"); process.exit(1); }

const CHUNK_SECONDS = Number(chunkSecondsArg || process.env.CHUNK_SECONDS || 15);
const slug = slugifyEpisodeName(episodeName);
const episodeDir = path.join(ROOT, "episodes", episodeName);
const scriptVersion = resolveScriptVersion(episodeDir, requestedVersion);
const audioDir = path.join(episodeDir, "audio");
const rendersDir = path.join(episodeDir, "renders");
const timingsPath = path.join(audioDir, "body-timings.json");
const previewDir = path.join(ROOT, "tmp", `preview-${slug}`);
const introDir = path.join(previewDir, "intro");
const bodyDir = path.join(previewDir, "body");
const introVideo = path.join(introDir, "renders", "intro.mp4");
const chunkWorkRoot = path.join(ROOT, "tmp", `chunks-${slug}`);
const bgmArg = bgmInput || (() => {
  const d = path.join(ROOT, "assets", "bgm");
  const f = fs.existsSync(d) ? fs.readdirSync(d).filter((n) => n.toLowerCase().endsWith(".mp3")) : [];
  return f[0];
})();

function run(command, args, options = {}) {
  const r = spawnSync(command, args, { cwd: options.cwd || ROOT, stdio: "inherit", shell: false });
  if (r.status !== 0) { console.error(`✗ ${command} failed (${r.status})`); process.exit(r.status ?? 1); }
}
function slugifyBgmName(input) {
  const name = path.basename(input, path.extname(input));
  return name.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "bgm";
}
function probeDuration(p) {
  const r = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", p], { encoding: "utf8" });
  const d = Number(r.stdout?.trim());
  return r.status === 0 && Number.isFinite(d) ? d : 0;
}

if (!fs.existsSync(bodyDir) || !fs.existsSync(path.join(bodyDir, "index.html"))) {
  throw new Error(`Missing body preview at ${bodyDir}; run create-episode-preview-xw.mjs first`);
}
if (!fs.existsSync(introVideo)) {
  throw new Error(`Missing intro.mp4 at ${introVideo}; run create-episode-preview-xw.mjs first`);
}

// ---- 1. 计算分段窗口（对齐到 caption 结尾，每段 ~CHUNK_SECONDS）----
const timings = JSON.parse(fs.readFileSync(timingsPath, "utf8"));
const bodyDuration = Number(timings.duration);
if (!Number.isFinite(bodyDuration) || bodyDuration <= 0) throw new Error("Invalid body duration in timings");
const caps = (timings.captions || []).slice().sort((a, b) => Number(a.order) - Number(b.order));
const windows = [];
let t0 = 0;
for (const cap of caps) {
  const cEnd = Number(cap.end);
  if (cEnd <= t0) continue;
  if (t0 === 0) { t0 = 0; } // 首段从 0 起（含 bridge 淡入）
  if (cEnd - t0 >= CHUNK_SECONDS) {
    windows.push([t0, cEnd]);
    t0 = cEnd;
  }
}
if (t0 < bodyDuration - 0.05) windows.push([t0, bodyDuration]);
if (windows.length === 0) windows.push([0, bodyDuration]);
console.log(`分段：${windows.length} 段，每段≈${CHUNK_SECONDS}s，body=${bodyDuration.toFixed(2)}s`);

// ---- 2. 逐段渲染 ----
fs.mkdirSync(chunkWorkRoot, { recursive: true });
fs.mkdirSync(rendersDir, { recursive: true });
const chunkVideos = [];
for (let i = 0; i < windows.length; i += 1) {
  const [wt0, wt1] = windows[i];
  const chunkLen = Number((wt1 - wt0).toFixed(3));
  const cdir = path.join(chunkWorkRoot, `c${String(i + 1).padStart(2, "0")}`);
  fs.rmSync(cdir, { recursive: true, force: true });
  fs.mkdirSync(path.join(cdir, "renders"), { recursive: true });
  // 复制 body 目录（含 media + 本地 gsap）
  fs.cpSync(bodyDir, cdir, { recursive: true });
  // 改 HTML：data-duration 改窗口长 + CDN gsap 换本地 + 注入 shiftChildren(-t0)
  const htmlPath = path.join(cdir, "index.html");
  let html = fs.readFileSync(htmlPath, "utf8");
  html = html.replace(/data-duration="[\d.]+"/, `data-duration="${chunkLen}"`);
  html = html.replace(/<script src="https:\/\/cdn\.jsdelivr\.net[^"]*"><\/script>/, '<script src="gsap.min.js"></script>');
  html = html.replace(
    'window.__timelines["main"] = tl;',
    `window.__timelines["main"] = tl;\n      tl.shiftChildren(${(-wt0).toFixed(3)}, false);`,
  );
  fs.writeFileSync(htmlPath, html);

  const out = path.join(cdir, "renders", `body-c${String(i + 1).padStart(2, "0")}.mp4`);
  const hfArgs = ["render", "--low-memory-mode", "--quality", "standard", "--protocol-timeout", "1800000", "--workers", "1", "--output", "renders/body-c" + String(i + 1).padStart(2, "0") + ".mp4"];
  let ok = false;
  for (let attempt = 1; attempt <= 3 && !ok; attempt += 1) {
    console.log(`\n▶ 段 ${i + 1}/${windows.length} [${wt0.toFixed(1)}–${wt1.toFixed(1)}s] 尝试 ${attempt}`);
    const r = spawnSync("node", [LOCAL_HYPERFRAMES_CLI, ...hfArgs], { cwd: cdir, stdio: "inherit" });
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

// ---- 3. concat 重组 body 视频 ----
const bodyOut = path.join(rendersDir, "body-xwz.mp4");
const listPath = path.join(chunkWorkRoot, "list.txt");
fs.writeFileSync(listPath, chunkVideos.map((v) => `file '${v}'`).join("\n"));
run("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", bodyOut]);
console.log(`✓ body 重组：${bodyOut} (${probeDuration(bodyOut).toFixed(2)}s)`);

// ---- 4. process-voiceover（story 预设）----
const introVoice = path.join(ROOT, "assets", "template-audio", "intro-voiceover.mp3");
const bodyVoice = path.join(audioDir, "body-voiceover.mp3");
const introStory = path.join(chunkWorkRoot, "intro-voiceover-story.mp3");
const bodyStory = path.join(audioDir, "body-voiceover-story.mp3");
run("node", ["scripts/process-voiceover.mjs", introVoice, introStory, "story"]);
run("node", ["scripts/process-voiceover.mjs", bodyVoice, bodyStory, "story"]);

// ---- 5. 手 mux（抄 render-episode-final-xw.mjs 的 filter_complex）----
const bgmPath = (() => {
  const direct = path.resolve(ROOT, bgmArg);
  if (fs.existsSync(direct)) return direct;
  const f = bgmArg.endsWith(".mp3") ? bgmArg : `${bgmArg}.mp3`;
  const p = path.join(ROOT, "assets", "bgm", f);
  if (fs.existsSync(p)) return p;
  throw new Error(`BGM not found: ${bgmArg}`);
})();
const SCROLL_SFX = path.join(ROOT, "assets", "sfx", "gear-scroll.mp3");
const WATER_SFX = path.join(ROOT, "assets", "sfx", "water.mp3");
const bgmSlug = slugifyBgmName(bgmPath);
const INTRO_VOICE_DUR = probeDuration(introVoice);
const INTRO_VOICE_OFFSET_MS = Math.round(INTRO_VOICE_DUR * 1000);
const finalDuration = Number((INTRO_TRIM_SECONDS + bodyDuration).toFixed(2));
const SCROLL_START = 1.08, SCROLL_END = 2.38, SCROLL_VOL = 1.4, SCROLL_FADE = 0.2;
const scrollDur = Number((SCROLL_END - SCROLL_START).toFixed(2));
const scrollDelay = Math.round(SCROLL_START * 1000);
const scrollFadeStart = Number((scrollDur - SCROLL_FADE).toFixed(2));
const WATER_START = Number((INTRO_TRIM_SECONDS + 0.32).toFixed(2));
const outputPath = path.join(rendersDir, `${slug}-final-${bgmSlug}-story-voice-bgm-standard.mp4`);

run("ffmpeg", [
  "-y", "-i", introVideo, "-i", bodyOut,
  "-i", introStory, "-i", bodyStory,
  "-stream_loop", "-1", "-i", bgmPath,
  "-i", SCROLL_SFX, "-i", WATER_SFX,
  "-filter_complex", [
    `[0:v]trim=0:${INTRO_TRIM_SECONDS},setpts=PTS-STARTPTS[v0]`,
    `[1:v]trim=0:${bodyDuration},setpts=PTS-STARTPTS[v1]`,
    "[v0][v1]concat=n=2:v=1:a=0[v]",
    "[2:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[introa]",
    `[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_OFFSET_MS}|${INTRO_OFFSET_MS},volume=1.0[bodya]`,
    `[4:a]atrim=0:${Number(bodyDuration).toFixed(2)},asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_OFFSET_MS}|${INTRO_OFFSET_MS},volume=${FINAL_BGM_VOLUME},afade=t=in:st=0:d=0.5[bgm]`,
    `[5:a]atrim=0:${scrollDur},asetpts=PTS-STARTPTS,aresample=48000,volume=${SCROLL_VOL},afade=t=in:st=0:d=0.01,afade=t=out:st=${scrollFadeStart}:d=${SCROLL_FADE},adelay=${scrollDelay}|${scrollDelay}[scrollsfx]`,
    `[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,adelay=${Math.round(WATER_START * 1000)}|${Math.round(WATER_START * 1000)}[watersfx]`,
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
  ].join(";"),
  "-map", "[v]", "-map", "[a]",
  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", outputPath,
]);
const dur = probeDuration(outputPath);
// 校验维度 + 时长
const probe = spawnSync("ffprobe", ["-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", outputPath], { encoding: "utf8" });
const pj = JSON.parse(probe.stdout || "{}");
const v = pj.streams?.find((s) => s.codec_type === "video");
if (!v || v.width !== 720 || v.height !== 960) throw new Error(`Invalid dimensions ${v?.width}x${v?.height}`);
if (dur > 60.05 && process.env.ALLOW_OVER_60_SECONDS !== "1") throw new Error(`Final ${dur.toFixed(2)}s > 60s (set ALLOW_OVER_60_SECONDS=1)`);
console.log(`\n✅ 成片：${outputPath}`);
console.log(`   时长 ${dur.toFixed(2)}s（intro ${INTRO_TRIM_SECONDS}s + body ${bodyDuration.toFixed(2)}s），分段 ${windows.length}，BGM ${path.basename(bgmPath)}`);
