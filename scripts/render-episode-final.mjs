#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";
import { resolveScriptVersion } from "./lib/script-version.mjs";
import { WATER_DUR_SECONDS, waterStartSeconds } from "./lib/water-ripple.mjs";

const ROOT = process.cwd();
const HYPERFRAMES_VERSION = "0.7.33";
const LOCAL_HYPERFRAMES_CLI = "/Users/apple/.workbuddy/binaries/node/workspace/node_modules/hyperframes/dist/cli.js";
// 裁切点取在片头「结果页定格」稳定展示后（结果页 2.38s 才出现，之后 page-bg 用 1.87s 从 scale 1
// 缓推到 1.035），让片头末帧与正文首帧同为结果桥接图，消除黑场硬切亮图的闪屏接缝。
// 默认 3.0s 而不是口播长度 1.83s：1.83 处画面还在候选书单滚动中段，切过去等于把书名列表
// 硬接到正文首帧（《白鲸》《道林·格雷的画像》《霍比特人》的 template fallback 已把这点写死为
// 3.0s，见 render_scene_map_template_fallback.py 的 BRIDGE_ZOOM 推导）。口播只有 1.83s，
// 多出的 ~1.17s 是结果页定格的静拍，不是缺音。
// 一期成片 = 钩子（本期专属，可缺省）→ 固定开场白（intro 模板裁到本常量）→ 正文（本期专属）。
// 裁切点仍是「模板段」的边界，钩子段接在它前面，所以钩子时长不进 INTRO_TRIM_SECONDS——
// 齿轮/水波的秒数全部相对成片时间轴右移 hookDuration，由 hookOffsetMs() 统一换算。
const INTRO_TRIM_SECONDS = Number(process.env.INTRO_TRIM_SECONDS || "3.0");
const INTRO_TIMING = JSON.parse(
  fs.readFileSync(path.join(ROOT, "scripts", "lib", "intro-template.json"), "utf8"),
);
// 钩子→片头那道缝：**钩子段尾淡出到黑**（hook_fade_out_seconds），三段之间仍是硬 concat。
// 2026-10-05 11:39「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」、13:30「三处全回滚
// （视觉 100% 回到从前）」、14:46「重新恢复固定开场白，保留原始效果」三次同向裁决之后，共享片头
// 重新以「0.13s 全黑起幅 + ~0.47s 飞到亮度平台」开头——那是原效果的一部分，不许在片头上动刀。
// 片头前面是本期那张均亮度 40~60 的钩子照片，硬切过去就是 2026-10-04 第一次量到的那次眨眼
// （《嫌疑人X的献身》成片第 8.27s 单帧 42.4→0.0，判据①）。让钩子自己沉到黑去接片头的黑起幅，
// 两侧同暗就没有可翻的案。13:04 那轮「只改过渡形状」的授权（亮面起幅 + 就地等长溶解）已被 14:46
// 那句收回（2026-10-06 复核时它又被一次陈旧缓冲区保存写回过一次），旧件在 tmp/retired-20261005c/。
// 长度真源：scripts/lib/intro-template.json 的 hook_fade_out_seconds（第 10 道门的豁免窗读同一个数）。
const hookFadeSeconds = Number(
  process.env.HOOK_FADE_OUT_SECONDS ?? INTRO_TIMING.hook_fade_out_seconds ?? 0,
);
// 齿轮滚动音效原本相对「模板段起点」定位（1.08s 起、2.38s 止），模板段前插钩子后整体右移。
const INTRO_SCROLL_SFX_START_SECONDS = 1.08;
const INTRO_SCROLL_SFX_END_SECONDS = 2.38;
const FINAL_BGM_BASE_VOLUME = 0.32;
const FINAL_BGM_GAIN_DB = Number(process.env.FINAL_BGM_GAIN_DB || "0");
if (!Number.isFinite(FINAL_BGM_GAIN_DB)) {
  throw new Error(`Invalid FINAL_BGM_GAIN_DB: ${process.env.FINAL_BGM_GAIN_DB}`);
}
const FINAL_BGM_VOLUME = Number((FINAL_BGM_BASE_VOLUME * Math.pow(10, FINAL_BGM_GAIN_DB / 20)).toFixed(4));
const ALLOW_OVER_60_SECONDS = process.env.ALLOW_OVER_60_SECONDS === "1";
const INTRO_SCROLL_SFX_FADE_OUT_SECONDS = 0.2;
const INTRO_SCROLL_SFX_VOLUME = 1.4;
const INTRO_SCROLL_SFX_PATH = path.join(ROOT, "assets", "sfx", "gear-scroll.mp3");
// 水波音效：与画面涟漪同源。秒数只写在 scripts/lib/water-ripple.json 里一处，
// Python 渲染器（逐帧合成涟漪）和本文件（排音频）都读它——任何一处另立常数都算违规，
// 交付前 `python3 scripts/check-water-ripple.py` 会比对声画同窗。
const INTRO_WATER_SFX_PATH = path.join(ROOT, "assets", "sfx", "water.mp3");
const INTRO_WATER_SFX_VOLUME = 1.0;

const [episodeName, requestedVersion, bgmInput] = process.argv.slice(2);

if (!episodeName) {
  console.error("Usage: node scripts/render-episode-final.mjs <episode-name> [script-version] [bgm-file-or-name]");
  process.exit(1);
}

function chooseRandomBgm() {
  const bgmDir = path.join(ROOT, "assets", "bgm");
  const available = fs.existsSync(bgmDir)
    ? fs.readdirSync(bgmDir).filter((name) => name.toLowerCase().endsWith(".mp3"))
    : [];
  if (available.length === 0) {
    throw new Error(`No shared BGM found in ${bgmDir}`);
  }
  return available[Math.floor(Math.random() * available.length)];
}

const bgmArg = bgmInput || chooseRandomBgm();

function slugifyBgmName(input) {
  const name = path.basename(input, path.extname(input));
  return name.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "bgm";
}

const slug = slugifyEpisodeName(episodeName);
const bgmSlug = slugifyBgmName(bgmArg);
const episodeDir = path.join(ROOT, "episodes", episodeName);
const scriptVersion = resolveScriptVersion(episodeDir, requestedVersion);
const audioDir = path.join(episodeDir, "audio");
const rendersDir = path.join(episodeDir, "renders");
const timingsPath = path.join(audioDir, "body-timings.json");
const previewDir = path.join(ROOT, "tmp", `preview-${slug}`);
const introDir = path.join(previewDir, "intro");
const hookDir = path.join(previewDir, "hook");
const bodyDir = path.join(previewDir, "body");
const finalCandidateDir = path.join(previewDir, "final");
const introVideo = path.join(introDir, "renders", "intro.mp4");
const hookVideo = path.join(hookDir, "renders", "hook.mp4");
const bodyVideo = path.join(bodyDir, "renders", "body.mp4");
const introVoice = path.join(ROOT, "assets", "template-audio", "intro-voiceover.mp3");
const bodyVoice = path.join(audioDir, "body-voiceover.mp3");
const hookVoice = path.join(audioDir, "hook-voiceover.mp3");
const introStoryVoice = path.join(previewDir, "audio", "intro-voiceover-story.mp3");
const bodyStoryVoice = path.join(audioDir, "body-voiceover-story.mp3");
const hookStoryVoice = path.join(audioDir, "hook-voiceover-story.mp3");
// 钩子段是可选段：script-hook.csv 存在才算三段结构，否则整期退回历史两段。
// 判定用「文案文件」而不是音频，因为 create-episode-preview.mjs 用的是同一个信号；
// 两边判据不一致会出现「有钩子画面没配音」或反过来的半截状态。
const hasHook = fs.existsSync(path.join(episodeDir, "script-hook.csv"));
const INTRO_VOICE_DURATION = probeAudioDuration(introVoice);
const INTRO_VOICE_OFFSET_MS = Math.round(INTRO_VOICE_DURATION * 1000);
const bgmMixSuffix =
  FINAL_BGM_GAIN_DB === 0
    ? "bgm-standard"
    : `bgm-mix-${FINAL_BGM_GAIN_DB > 0 ? "plus" : "minus"}${Math.abs(FINAL_BGM_GAIN_DB)}db`;
const outputPath = path.join(rendersDir, `${slug}-final-${bgmSlug}-story-voice-${bgmMixSuffix}.mp4`);
const candidateOutputPath = path.join(finalCandidateDir, path.basename(outputPath));
const introScrollSfxDuration = Number((INTRO_SCROLL_SFX_END_SECONDS - INTRO_SCROLL_SFX_START_SECONDS).toFixed(2));
const introScrollSfxFadeOutStart = Number((introScrollSfxDuration - INTRO_SCROLL_SFX_FADE_OUT_SECONDS).toFixed(2));
// 钩子段在成片时间轴上占最前面 [0, hookDuration)，模板段接在后面，正文再接模板段之后。
// 齿轮音效和水波原本相对「模板段起点」定位，插入钩子后必须整体右移，否则声画错窗。
// 这些偏移量是三段唯一的换算处，拼接和混音都从这里取值，不各自手写秒数。
const hookDuration = readHookDuration();
const hookOffsetMs = Math.round(hookDuration * 1000);
const bodyOffsetSeconds = Number((hookDuration + INTRO_TRIM_SECONDS).toFixed(2));
const bodyOffsetMs = Math.round(bodyOffsetSeconds * 1000);
const introScrollSfxDelayMs = Math.round((hookDuration + INTRO_SCROLL_SFX_START_SECONDS) * 1000);
// 水波锁在「模板段→正文」切点之后。切点 = 钩子 + INTRO_TRIM_SECONDS，起点由 lib 的
// waterStartSeconds() 从 water-ripple.json 推出模板段内的偏移，再加上钩子占掉的前置时长——
// 秒数只从 lib 取，这里不写 0.32（门禁同时盯着字面量和硬编码偏移两种写法）。
const waterSfxDelayMs = Math.round((hookDuration + waterStartSeconds(INTRO_TRIM_SECONDS)) * 1000);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || ROOT,
    stdio: "inherit",
    shell: false,
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function probeVideo(filePath) {
  const result = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", filePath],
    { cwd: ROOT, encoding: "utf8", shell: false },
  );
  if (result.status !== 0) {
    throw new Error(`ffprobe failed for ${filePath}: ${result.stderr || "unknown error"}`);
  }
  const probe = JSON.parse(result.stdout);
  const video = probe.streams?.find((stream) => stream.codec_type === "video");
  const audio = probe.streams?.find((stream) => stream.codec_type === "audio");
  const duration = Number(probe.format?.duration || 0);
  if (!video || video.width !== 720 || video.height !== 960) {
    throw new Error(`Invalid final video dimensions: ${video?.width || 0}x${video?.height || 0}`);
  }
  if (!audio) throw new Error("Final video has no audio stream");
  if (!Number.isFinite(duration) || duration <= 0) throw new Error(`Invalid final duration: ${duration}`);
  if (duration > 60.05 && !ALLOW_OVER_60_SECONDS) {
    throw new Error(`Final video is ${duration.toFixed(2)}s; maximum is 60s`);
  }
}

function activateFinalRender(candidatePath, destinationPath) {
  fs.mkdirSync(rendersDir, { recursive: true });
  fs.renameSync(candidatePath, destinationPath);
  for (const entry of fs.readdirSync(rendersDir, { withFileTypes: true })) {
    const entryPath = path.join(rendersDir, entry.name);
    if (entry.isFile() && entryPath !== destinationPath) fs.rmSync(entryPath, { force: true });
  }
}

function getBgmPath(input) {
  const direct = path.resolve(ROOT, input);
  if (fs.existsSync(direct)) return direct;
  const withExt = input.endsWith(".mp3") ? input : `${input}.mp3`;
  const fromAssets = path.join(ROOT, "assets", "bgm", withExt);
  if (fs.existsSync(fromAssets)) return fromAssets;
  throw new Error(`BGM not found: ${input}`);
}

function probeAudioDuration(filePath) {
  const result = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filePath],
    { cwd: ROOT, encoding: "utf8", shell: false },
  );
  const duration = Number(result.stdout?.trim());
  if (result.status !== 0 || !Number.isFinite(duration) || duration <= 0) {
    throw new Error(`Could not determine audio duration: ${filePath}`);
  }
  return duration;
}

// 一段的成片时长：优先量渲染出来的视频，视频不存在时退回该段配音。
// 钩子段的视频此刻还没渲染（这段代码在渲染之前跑），所以实际走的是配音时长；
// 配音是权威时钟——字幕轴和它对齐，画面按它裁。
function probeSegmentDuration(videoPath, audioPath) {
  if (fs.existsSync(videoPath)) {
    const result = spawnSync(
      "ffprobe",
      ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", videoPath],
      { cwd: ROOT, encoding: "utf8", shell: false },
    );
    const duration = Number(result.stdout?.trim());
    if (result.status === 0 && Number.isFinite(duration) && duration > 0) return duration;
  }
  return probeAudioDuration(audioPath);
}

// 一段的时间轴时长：优先用该段 timings 里的 duration（字幕轴的时间真源），
// 缺失或不匹配时退回配音时长。钩子段与正文段走同一份逻辑。
function readSegmentTimingsDuration(timingsFile, voicePath, label) {
  const fallbackDuration = probeAudioDuration(voicePath);
  if (!fs.existsSync(timingsFile)) {
    console.warn(`Missing ${label} timings; continuing with voiceover duration and script hints`);
    return fallbackDuration;
  }
  let timings;
  try {
    timings = JSON.parse(fs.readFileSync(timingsFile, "utf8"));
  } catch (error) {
    console.warn(`Could not read ${label} timings; continuing with voiceover duration: ${error.message}`);
    return fallbackDuration;
  }
  if (timings.scriptVersion && timings.scriptVersion !== scriptVersion) {
    console.warn(`${label} timings are for ${timings.scriptVersion}; continuing with script hints for ${scriptVersion}`);
    return fallbackDuration;
  }
  if (timings.alignment?.requiresAgentReview) {
    console.warn(
      `Rendering ${label} with timings marked for Agent review (${timings.alignment.method || "unknown method"}): `
      + `${timings.alignment.reason || "low-confidence ASR alignment"}`,
    );
  }
  const timingDuration = Number(timings.duration);
  return Number.isFinite(timingDuration) && timingDuration > 0 ? timingDuration : fallbackDuration;
}

function readBodyDuration() {
  return readSegmentTimingsDuration(timingsPath, bodyVoice, "body");
}

// 钩子段时长以时间轴为准（渲染前钩子视频还不存在，视频时长只能事后量），
// 字幕轴与它同源，所以两处一致。
function readHookDuration() {
  if (!hasHook) return 0;
  return readSegmentTimingsDuration(path.join(audioDir, "hook-timings.json"), hookVoice, "hook");
}

if (!fs.existsSync(episodeDir)) throw new Error(`Episode not found: ${episodeDir}`);
if (!fs.existsSync(introVoice)) {
  throw new Error(`Missing shared intro voiceover: ${introVoice}`);
}
if (!fs.existsSync(bodyVoice)) {
  throw new Error(`Missing episode body voiceover: ${bodyVoice}`);
}
// 有 script-hook.csv 就必须有三段素材齐：钩子配音、钩子氛围图目录。
// 缺任何一样都直接报错，不要静默渲染成两段——那会交付一期观众看不到钩子的片子。
if (hasHook) {
  if (!fs.existsSync(hookVoice)) {
    throw new Error(`script-hook.csv exists but hook voiceover is missing: ${hookVoice}`);
  }
  if (!fs.existsSync(path.join(episodeDir, "images-hook"))) {
    throw new Error(
      `script-hook.csv exists but images-hook/ is missing: ${path.join(episodeDir, "images-hook")} `
      + `(the hook segment needs its own Pexels atmosphere stills)`,
    );
  }
}
if (!fs.existsSync(INTRO_SCROLL_SFX_PATH)) {
  throw new Error(`Missing intro scroll SFX: ${INTRO_SCROLL_SFX_PATH}`);
}

const bgmPath = getBgmPath(bgmArg);
console.log(`Using BGM: ${path.basename(bgmPath)}`);
const bodyDuration = readBodyDuration();
// 成片总长 = 钩子 + 模板裁切段 + 正文。hookDuration 已在顶层算好。
const finalDuration = Number((hookDuration + INTRO_TRIM_SECONDS + bodyDuration).toFixed(2));
if (finalDuration > 60 && !ALLOW_OVER_60_SECONDS) {
  throw new Error(`Planned final duration is ${finalDuration.toFixed(2)}s; maximum is 60s`);
}
if (hasHook) {
  console.log(
    `Three-segment episode: hook ${hookDuration.toFixed(2)}s → template ${INTRO_TRIM_SECONDS}s → body ${bodyDuration.toFixed(2)}s`,
  );
}

fs.mkdirSync(rendersDir, { recursive: true });

run("node", ["scripts/create-episode-preview.mjs", episodeName, scriptVersion]);
fs.mkdirSync(finalCandidateDir, { recursive: true });
if (hasHook) {
  run("node", ["scripts/process-voiceover.mjs", hookVoice, hookStoryVoice, "story"]);
}
run("node", ["scripts/process-voiceover.mjs", introVoice, introStoryVoice, "story"]);
run("node", ["scripts/process-voiceover.mjs", bodyVoice, bodyStoryVoice, "story"]);
// 取帧默认单 Chrome worker：多 worker 的栅格化竞态会渲出「某一帧只画了一半合成层」的脏帧
// （用户四次报的钩子闪现，实测与理由见 scripts/create-episode-preview.mjs 写 package.json
// 处，2026-10-07）。钩子段/正文段本来就硬写 --workers 1，这里只剩片头段是并发来源；片头
// 段一旦带脏帧，第 10 道门在豁免窗内只报告不判红、第 11 道门量的是电平与黑场，两道都看不见
// 半张瓦片，所以这个旗子必须和另两段一致。旗子不改画面（A/B 全帧 MAD 0.00），也不碰
// 2026-10-05 冻结的片头观感。HF_WORKERS 保留给对照实验。
const hfWorkers = process.env.HF_WORKERS || "1";
const hfLowMemory = process.env.HF_LOW_MEMORY === "1";
const hfRenderArgs = [
  "render",
  "--quality", "standard",
  "--protocol-timeout", "1800000",
  ...(hfLowMemory ? ["--low-memory-mode"] : []),
  "--workers", hfWorkers,
  "--output", "renders/intro.mp4",
];
if (fs.existsSync(LOCAL_HYPERFRAMES_CLI)) {
  run("node", [LOCAL_HYPERFRAMES_CLI, ...hfRenderArgs], { cwd: introDir });
} else {
  run("npx", ["--yes", `hyperframes@${HYPERFRAMES_VERSION}`, ...hfRenderArgs], { cwd: introDir });
}
if (hasHook) {
  const hfRenderHookArgs = [
    "render",
    "--quality", "standard",
    "--fps", "15",
    "--protocol-timeout", "900000",
    ...(hfLowMemory ? ["--low-memory-mode"] : []),
    "--workers", "1",
    "--output", "renders/hook.mp4",
    "--browser-timeout", "120",
  ];
  if (fs.existsSync(LOCAL_HYPERFRAMES_CLI)) {
    run("node", [LOCAL_HYPERFRAMES_CLI, ...hfRenderHookArgs], { cwd: hookDir });
  } else {
    run("npx", ["--yes", `hyperframes@${HYPERFRAMES_VERSION}`, ...hfRenderHookArgs], { cwd: hookDir });
  }
}
const hfRenderBodyArgs = [
  "render",
  "--quality", "standard",
  "--fps", "15",
  "--protocol-timeout", "900000",
  ...(hfLowMemory ? ["--low-memory-mode"] : []),
  "--workers", "1",
  "--output", "renders/body.mp4",
  "--browser-timeout", "120",
];
if (fs.existsSync(LOCAL_HYPERFRAMES_CLI)) {
  run("node", [LOCAL_HYPERFRAMES_CLI, ...hfRenderBodyArgs], { cwd: bodyDir });
} else {
  run("npx", ["--yes", `hyperframes@${HYPERFRAMES_VERSION}`, ...hfRenderBodyArgs], { cwd: bodyDir });
}

// 三段拼接：钩子 → 模板裁切段 → 正文。钩子段可缺省，缺省时 inputs 从 introVideo 起、
// 音轨从 introStoryVoice 起，amix 的 inputs 数量同步降为 4——用同一段代码渲染两段结构，
// 不留两条会各自漂移的分支。
const videoInputs = hasHook ? [hookVideo, introVideo, bodyVideo] : [introVideo, bodyVideo];
const voiceInputs = hasHook
  ? [hookStoryVoice, introStoryVoice, bodyStoryVoice]
  : [introStoryVoice, bodyStoryVoice];

const ffmpegArgs = [
  "-y",
  ...videoInputs.flatMap((input) => ["-i", input]),
  ...voiceInputs.flatMap((input) => ["-i", input]),
  "-stream_loop",
  "-1",
  "-i",
  bgmPath,
  "-i",
  INTRO_SCROLL_SFX_PATH,
  "-i",
  INTRO_WATER_SFX_PATH,
];

const filterParts = [];
const mixLabels = [];
let nextInputIndex = 0;
const idx = () => nextInputIndex++;

const videoLabels = [];
// 三段拼接是**硬 concat**：共享片头按 2026-10-03 的原样整段搬用，不在它头上做溶解或垫帧。
// 抗闪屏只落在钩子这一路——段尾 fade=t=out 淡到黑，去接片头自己的黑场起幅。fade 的 st 相对
// setpts=PTS-STARTPTS 归零之后的**这一段**，所以是 hookDuration − hookFadeSeconds。
// 片头那一路必须是 trim=0:${INTRO_TRIM_SECONDS}：起点一旦挪开，bodyOffsetMs、齿轮/水波的 adelay、
// 字幕取点、水波窗口和 BRIDGE_ZOOM 全部错窗，而它们都按 hookDuration + INTRO_TRIM_SECONDS 算。
// 钩子段是 HyperFrames 渲的 15fps，片头与正文是 30fps：concat 之前先 fps=30 对齐时间基
// （只补帧，不改时长），混合帧率直接进 concat 会让整段成片的时间戳各算各的。
if (hasHook) {
  const fadeTail = hookFadeSeconds > 0
    ? `,fade=t=out:st=${Math.max(0, hookDuration - hookFadeSeconds).toFixed(2)}:d=${hookFadeSeconds}`
    : "";
  filterParts.push(
    `[${idx()}:v]trim=0:${hookDuration.toFixed(2)},setpts=PTS-STARTPTS,fps=30${fadeTail}[vh]`,
  );
  videoLabels.push("[vh]");
}
filterParts.push(
  `[${idx()}:v]trim=0:${INTRO_TRIM_SECONDS},setpts=PTS-STARTPTS[vt]`,
);
videoLabels.push("[vt]");
filterParts.push(`[${idx()}:v]trim=0:${bodyDuration.toFixed(2)},setpts=PTS-STARTPTS[vb]`);
videoLabels.push("[vb]");
filterParts.push(
  `${videoLabels.join("")}concat=n=${videoLabels.length}:v=1:a=0[v]`,
);

if (hasHook) {
  filterParts.push(
    `[${idx()}:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[hooka]`,
  );
  mixLabels.push("[hooka]");
}
filterParts.push(
  `[${idx()}:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${hookOffsetMs}|${hookOffsetMs},volume=1.0[introa]`,
);
mixLabels.push("[introa]");
filterParts.push(
  `[${idx()}:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${bodyOffsetMs}|${bodyOffsetMs},volume=1.0[bodya]`,
);
mixLabels.push("[bodya]");

// BGM 从模板段起播并铺到片尾，所以裁到「成片总长 - 模板段起点」的时长。
const bgmTailSeconds = Number((finalDuration - hookDuration).toFixed(2));
const bgmFadeOutStart = Number((bgmTailSeconds - 0.8).toFixed(2));
filterParts.push(
  `[${idx()}:a]atrim=0:${bgmTailSeconds},asetpts=PTS-STARTPTS,aresample=48000,`
  + `adelay=${hookOffsetMs}|${hookOffsetMs},volume=${FINAL_BGM_VOLUME},`
  + `afade=t=in:st=0:d=0.5,afade=t=out:st=${bgmFadeOutStart}:d=0.8[bgm]`,
);
mixLabels.push("[bgm]");

filterParts.push(
  `[${idx()}:a]atrim=0:${introScrollSfxDuration},asetpts=PTS-STARTPTS,aresample=48000,`
  + `volume=${INTRO_SCROLL_SFX_VOLUME},afade=t=in:st=0:d=0.01,`
  + `afade=t=out:st=${introScrollSfxFadeOutStart}:d=${INTRO_SCROLL_SFX_FADE_OUT_SECONDS},`
  + `adelay=${introScrollSfxDelayMs}|${introScrollSfxDelayMs}[scrollsfx]`,
);
mixLabels.push("[scrollsfx]");

// 水波与画面涟漪同窗：延迟 = 模板段起点 + 裁切点 + water-ripple.json 的 offsetSeconds。
// 秒数只从 lib 取，这里不写 0.32。
filterParts.push(
  `[${idx()}:a]atrim=0:${WATER_DUR_SECONDS},asetpts=PTS-STARTPTS,aresample=48000,`
  + `volume=${INTRO_WATER_SFX_VOLUME},adelay=${waterSfxDelayMs}|${waterSfxDelayMs}[watersfx]`,
);
mixLabels.push("[watersfx]");

filterParts.push(
  `${mixLabels.join("")}amix=inputs=${mixLabels.length}:duration=longest:dropout_transition=0:normalize=0,`
  + `alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,`
  + `aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]`,
);

run("ffmpeg", [
  ...ffmpegArgs,
  "-filter_complex",
  filterParts.join(";"),
  "-map",
  "[v]",
  "-map",
  "[a]",
  "-c:v",
  "libx264",
  "-pix_fmt",
  "yuv420p",
  "-profile:v",
  "high",
  "-level",
  "4.1",
  "-c:a",
  "aac",
  "-b:a",
  "192k",
  "-movflags",
  "+faststart",
  "-shortest",
  candidateOutputPath,
]);

probeVideo(candidateOutputPath);
activateFinalRender(candidateOutputPath, outputPath);
try {
  fs.rmSync(previewDir, { recursive: true, force: true });
} catch (e) {
  // best-effort cleanup; sandbox safe-delete guards may block bulk rm after a successful render
}
console.log(outputPath);
