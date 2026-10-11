#!/usr/bin/env node
/**
 * 长片 body 分段渲染（通用 xw 工作流），支持按 ts-image-map.json 覆盖场景时间轴。
 *
 * 与 render-body-chunked-xw.mjs 的差异：
 *  - 读 ts-image-map.json（{order_map:{order:image}, segments:[...]}），把 body HTML 的
 *    sceneStarts 改为按段落边界对齐，每个场景（同一张图）覆盖一段连续 order 区间；
 *  - 场景切换处做 0.72s 交叉淡入淡出（与模板原有逻辑一致）。
 *
 * 用法：node scripts/render-body-chunked-ts.mjs <episode> [version] [bgm-name] [chunkSeconds]
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";
import { resolveScriptVersion } from "./lib/script-version.mjs";

const ROOT = process.cwd();
const LOCAL_HYPERFRAMES_CLI = "/Users/apple/.workbuddy/binaries/node/workspace/node_modules/hyperframes/dist/cli.js";
const INTRO_TRIM_SECONDS = Number(process.env.INTRO_TRIM_SECONDS || "1.83");

const [episodeName, requestedVersion, bgmInput, chunkSecondsArg] = process.argv.slice(2);
if (!episodeName) { console.error("Usage: node scripts/render-body-chunked-ts.mjs <episode> [version] [bgm] [chunkSeconds]"); process.exit(1); }

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
  if (f.length === 0) throw new Error(`No BGM in ${d}`);
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

// ---- 0. 渲染片头 intro（短 composition，单次会话即可）----
if (!fs.existsSync(introVideo)) {
  console.log("▶ 渲染片头 intro…");
  const introHfArgs = ["render", "--quality", "standard", "--protocol-timeout", "1800000", "--workers", "1", "--output", "renders/intro.mp4"];
  const r = spawnSync("node", [LOCAL_HYPERFRAMES_CLI, ...introHfArgs], { cwd: introDir, stdio: "inherit" });
  if (r.status !== 0 || !fs.existsSync(introVideo)) {
    console.error("片头渲染失败；尝试 npx 通道…");
    run("npx", ["--yes", "hyperframes@0.7.33", ...introHfArgs], { cwd: introDir });
  }
  console.log(`✓ 片头完成 (${probeDuration(introVideo).toFixed(2)}s)`);
} else {
  console.log(`片头已存在：${introVideo} (${probeDuration(introVideo).toFixed(2)}s)`);
}

// ---- ts-image-map.json：段落 → 图片 映射 ----
const tsMapPath = path.join(episodeDir, "ts-image-map.json");
let tsMap = null;
if (fs.existsSync(tsMapPath)) {
  try {
    tsMap = JSON.parse(fs.readFileSync(tsMapPath, "utf8"));
    console.log(`[image-map] using ts-image-map.json (source=${tsMap.source}, segments=${tsMap.segments?.length || 0})`);
  } catch (e) {
    console.warn(`[image-map] ts-image-map.json 读取失败，回退等分场景: ${e.message}`);
    tsMap = null;
  }
}

const timings = JSON.parse(fs.readFileSync(timingsPath, "utf8"));
const bodyDuration = Number(timings.duration);
if (!Number.isFinite(bodyDuration) || bodyDuration <= 0) throw new Error("Invalid body duration in timings");

// 计算段落时间边界（body 内部时间轴 = 0 起）
// 若 tsMap 存在，segments[i] = {order_start, order_end, chosen}，用 captions 的 end/start 对齐
let sceneStarts = null; // 场景切换时刻（body 时间轴）
let sceneImages = null;  // 场景 i (1-based) 的图片文件名（无扩展名）
if (tsMap?.segments?.length) {
  const caps = (timings.captions || []).slice().sort((a, b) => Number(a.order) - Number(b.order));
  const capByOrder = new Map(caps.map((c) => [Number(c.order), c]));
  // 场景按 order 区间聚合：相同 chosen 的相邻段合并（防御）
  const runs = [];
  for (const seg of tsMap.segments) {
    const last = runs[runs.length - 1];
    if (last && last.image === seg.chosen) {
      last.endOrder = seg.order_end;
    } else {
      runs.push({ image: seg.chosen, startOrder: seg.order_start, endOrder: seg.order_end });
    }
  }
  sceneImages = runs.map((r) => r.image);
  sceneStarts = [0];
  for (let i = 1; i < runs.length; i += 1) {
    const capStart = capByOrder.get(runs[i].startOrder);
    sceneStarts.push(capStart ? Number(capStart.start) : (i / runs.length) * bodyDuration);
  }
  console.log(`[image-map] ${runs.length} scenes: ${runs.map((r, i) => `${i + 1}:${r.image}@${sceneStarts[i]?.toFixed(1)}s`).join("  ")}`);
} else {
  // 回退：与模板一致，等分场景（sceneCount = atmosphere 张数）
  const sceneCount = (sceneImages = null, 6);
  sceneStarts = [0.32];
  for (let k = 1; k < sceneCount; k += 1) sceneStarts.push(Number((bodyDuration * k / sceneCount).toFixed(2)));
}

// 改 body HTML：场景切换时刻 + 各场景图片
const htmlPath = path.join(bodyDir, "index.html");
let html = fs.readFileSync(htmlPath, "utf8");
// 替换固定的 sceneStarts 生成逻辑为显式数组
html = html
  .replace(
    /var sceneStarts = \[0\.32\];\s*for \(var k = 1; k < sceneCount; k\+\+\) sceneStarts\.push\(Number\(\(safeDur \* k \/ sceneCount\)\.toFixed\(2\)\)\);/,
    `var sceneStarts = [${sceneStarts.join(", ")}];`,
  );
// 每场景图片：原 CSS 按 atmosphere 顺序映射 s1..sN，需把 s{i} 映射到 tsMap 的 image
if (tsMap?.segments?.length && sceneImages) {
  const mediaDir = path.join(bodyDir, "media");
  sceneImages.forEach((img, i) => {
    const src = path.join(episodeDir, "images", `${img}.png`);
    const dst = path.join(mediaDir, `${String(i + 1).padStart(2, "0")}-atmosphere.png`);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, dst);
    } else {
      console.warn(`[image-map] missing ${src}`);
    }
  });
  // sceneCount 保持与原 atmosphere 张数一致（7 张：bridge + 6）→ 不动
}

// ---- 分段渲染：按 caption 边界把 body 切成 ~CHUNK_SECONDS 段 ----
const caps = (timings.captions || []).slice().sort((a, b) => Number(a.order) - Number(b.order));
const windows = [];
let t0 = 0;
for (const cap of caps) {
  const cEnd = Number(cap.end);
  if (cEnd <= t0) continue;
  if (cEnd - t0 >= CHUNK_SECONDS) {
    windows.push([t0, cEnd]);
    t0 = cEnd;
  }
}
if (t0 < bodyDuration - 0.05) windows.push([t0, bodyDuration]);
if (windows.length === 0) windows.push([0, bodyDuration]);
console.log(`分段：${windows.length} 段，每段≈${CHUNK_SECONDS}s，body=${bodyDuration.toFixed(2)}s`);

fs.mkdirSync(chunkWorkRoot, { recursive: true });
fs.mkdirSync(rendersDir, { recursive: true });
const chunkVideos = [];
const CHUNK_SPLIT_OVERRIDE = "少年维特的烦恼" === episodeName ? [
  ["c05", ["body-c05a.mp4", "body-c05b.mp4"]],
  ["c10", ["body-c10a.mp4", "body-c10b.mp4"]],
] : null;
for (let i = 0; i < windows.length; i += 1) {
  const [wt0, wt1] = windows[i];
  const chunkLen = Number((wt1 - wt0).toFixed(3));
  const cdirName = `c${String(i + 1).padStart(2, "0")}`;
  const cdir = path.join(chunkWorkRoot, cdirName);
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

  const out = path.join(cdir, "renders", `body-c${String(i + 1).padStart(2, "0")}.mp4`);
  const hfArgs = ["render", "--low-memory-mode", "--quality", "standard", "--protocol-timeout", "1800000", "--workers", "1", "--output", "renders/body-c" + String(i + 1).padStart(2, "0") + ".mp4"];
  if (fs.existsSync(out) && probeDuration(out) > chunkLen * 0.5) {
    console.log(`✓ 段 ${i + 1} 已存在，跳过 (${probeDuration(out).toFixed(2)}s)`);
    chunkVideos.push(out);
    continue;
  }
  let ok = false;
  for (let attempt = 1; attempt <= 6 && !ok; attempt += 1) {
    console.log(`\n▶ 段 ${i + 1}/${windows.length} [${wt0.toFixed(1)}–${wt1.toFixed(1)}s] 尝试 ${attempt}`);
    const r = spawnSync("node", [LOCAL_HYPERFRAMES_CLI, ...hfArgs], { cwd: cdir, stdio: "inherit" });
    if (r.status === 0 && fs.existsSync(out) && probeDuration(out) > chunkLen * 0.5) {
      ok = true;
    } else {
      console.warn(`  段 ${i + 1} 渲染失败/过短，重试…`);
    }
  }
  if (!ok) {
    const splitDir = CHUNK_SPLIT_OVERRIDE?.find(([c]) => c === cdirName)?.[1];
    if (splitDir && splitDir.every((n) => fs.existsSync(path.join(cdir, "renders", n)))) {
      for (const n of splitDir) chunkVideos.push(path.join(cdir, "renders", n));
      console.log(`  ✓ 段 ${i + 1} 完成（拆分：${splitDir.join(" + ")}）`);
      continue;
    }
    console.error(`✗ 段 ${i + 1} 渲染失败`); process.exit(1);
  }
  chunkVideos.push(out);
  console.log(`  ✓ 段 ${i + 1} 完成 (${probeDuration(out).toFixed(2)}s)`);
}

// concat 重组
const bodyOut = path.join(rendersDir, "body-ts.mp4");
const listPath = path.join(chunkWorkRoot, "list.txt");
fs.writeFileSync(listPath, chunkVideos.map((v) => `file '${v}'`).join("\n"));
run("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", bodyOut]);
console.log(`✓ body 重组：${bodyOut} (${probeDuration(bodyOut).toFixed(2)}s)`);

// ---- 处理配音（story 预设）----
const introVoice = path.join(ROOT, "assets", "template-audio", "intro-voiceover.mp3");
const bodyVoice = path.join(audioDir, "body-voiceover.mp3");
const introStory = path.join(chunkWorkRoot, "intro-voiceover-story.mp3");
const bodyStory = path.join(audioDir, "body-voiceover-story.mp3");
run("node", ["scripts/process-voiceover.mjs", introVoice, introStory, "story"]);
run("node", ["scripts/process-voiceover.mjs", bodyVoice, bodyStory, "story"]);

// ---- 手 mux ----
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
const FINAL_BGM_VOLUME = Number((0.32 * Math.pow(10, Number(process.env.FINAL_BGM_GAIN_DB || 0) / 20)).toFixed(4));
const outputPath = path.join(rendersDir, `${slug}-final-${bgmSlug}-story-voice-ts-map.mp4`);

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
    `[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_VOICE_OFFSET_MS}|${INTRO_VOICE_OFFSET_MS},volume=1.0[bodya]`,
    `[4:a]atrim=0:${finalDuration},asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_VOICE_OFFSET_MS}|${INTRO_VOICE_OFFSET_MS},volume=${FINAL_BGM_VOLUME},afade=t=in:st=0:d=0.5[bgm]`,
    `[5:a]atrim=0:${scrollDur},asetpts=PTS-STARTPTS,aresample=48000,volume=${SCROLL_VOL},afade=t=in:st=0:d=0.01,afade=t=out:st=${scrollFadeStart}:d=${SCROLL_FADE},adelay=${scrollDelay}|${scrollDelay}[scrollsfx]`,
    `[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,adelay=${Math.round(WATER_START * 1000)}|${Math.round(WATER_START * 1000)}[watersfx]`,
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
  ].join(";"),
  "-map", "[v]", "-map", "[a]",
  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", outputPath,
]);
const dur = probeDuration(outputPath);
const probe = spawnSync("ffprobe", ["-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", outputPath], { encoding: "utf8" });
const pj = JSON.parse(probe.stdout || "{}");
const v = pj.streams?.find((s) => s.codec_type === "video");
if (!v || v.width !== 720 || v.height !== 960) throw new Error(`Invalid dimensions ${v?.width}x${v?.height}`);
if (dur > 60.05 && process.env.ALLOW_OVER_60_SECONDS !== "1") throw new Error(`Final ${dur.toFixed(2)}s > 60s (set ALLOW_OVER_60_SECONDS=1)`);
console.log(`\n✅ 成片：${outputPath}`);
console.log(`   时长 ${dur.toFixed(2)}s（intro ${INTRO_TRIM_SECONDS}s + body ${bodyDuration.toFixed(2)}s），分段 ${windows.length}，BGM ${path.basename(bgmPath)}，image-map=${tsMap ? "ts" : "equal-split"}`);
