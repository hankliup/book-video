#!/usr/bin/env node
/**
 * 长片 body 分块渲染（本仓三段结构 / scene-map 兜底渲染器专用）。
 *
 * 为什么需要：4.6 分钟的 body 组合（8226 帧、14 个 will-change:transform 场景）在单个
 * Chrome 会话里跑到 ~30-60% 时渲染页必然崩（"Target closed" / Page.captureScreenshot）。
 * 换 --low-memory-mode、加大 protocol timeout 都没用——崩的是渲染页本身，不是超时。
 * 每次渲染只截时间轴上的一段、每段一个全新 Chrome，就绕开了会话内的累积。
 *
 * 做法：body/index.html 里所有时间字面量（revealCaption 的 start、safeDur、sceneStarts）
 * 都在同一个 IIFE 内、共用同一个时间原点。所以给这一段注入
 *   CHUNK_START / CHUNK_LEN
 * 把 safeDur 换成局部时长、把 revealCaption 换成偏移后的包装，时间轴就被整体平移，
 * 整条 timeline 造得和整片渲染时一模一样，只有 [CHUNK_START, CHUNK_START+CHUNK_LEN)
 * 这一段落在正时间内。逐块渲染后 concat 即得整段。
 *
 * 用法：node scripts/render-body-chunked-slice.mjs <episode> [chunkSeconds] [previewDir]
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import os from "node:os";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const ROOT = process.cwd();
const [episodeName, chunkArg, previewArg] = process.argv.slice(2);
if (!episodeName) {
  console.error("Usage: node scripts/render-body-chunked-slice.mjs <episode> [chunkSeconds] [previewDir]");
  process.exit(1);
}
const CHUNK = Number(chunkArg || process.env.CHUNK_SECONDS || 30);
const PREVIEW = path.resolve(
  previewArg || path.join(ROOT, "tmp", `preview-${slugifyEpisodeName(episodeName)}`),
);
const BODY = path.join(PREVIEW, "body");
const BG_ONLY = process.env.BG_ONLY === "1";
// 出字版与纯背景版落在不同目录，避免纯背景那次覆盖掉已经渲好的带字幕版本。
const OUT = path.join(BODY, process.env.OUT_SUBDIR || "renders-chunks");
const WORK = path.join(BODY, "chunks");

const src = fs.readFileSync(path.join(BODY, "index.html"), "utf8");

// 整段时长：main 上的 data-duration 就是 safeDuration
const durMatch = src.match(/data-duration="([0-9.]+)"/u);
if (!durMatch) throw new Error("找不到 data-duration（组合结构变了？）");
const TOTAL = Number(durMatch[1]);

// 每块标题的 reveal 时间，用来把块边界挪到字幕切换的间隙里（避免切在字幕中间）
const reveals = [...src.matchAll(/revealCaption\("([^"]+)",\s*([0-9.]+),\s*([0-9.]+)\);/gu)]
  .map((m) => ({ selector: m[1], start: Number(m[2]), hold: Number(m[3]) }));
if (!reveals.length) throw new Error("找不到 revealCaption 调用（组合结构变了？）");

// 块边界：从理想等分点向后找一个「没有字幕在屏上」的时刻
function boundaryNear(target) {
  let best = target;
  for (let d = 0; d <= 6; d += 0.1) {
    for (const t of [target + d, target - d]) {
      if (t <= 0.5 || t >= TOTAL - 0.5) continue;
      const onScreen = reveals.some((r) => r.start <= t && t < r.start + r.hold);
      if (!onScreen) return Number(t.toFixed(2));
    }
  }
  return Number(target.toFixed(2));
}

const bounds = [0];
for (let t = CHUNK; t < TOTAL - 0.5; t += CHUNK) bounds.push(boundaryNear(t));
bounds.push(TOTAL);

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(WORK, { recursive: true });

function ff(args) {
  const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], {
    cwd: ROOT, encoding: "utf8",
  });
  if (r.status !== 0) throw new Error(`ffmpeg failed: ${r.stderr || r.status}`);
}

const parts = [];
for (let i = 0; i < bounds.length - 1; i += 1) {
  const t0 = bounds[i];
  const len = Number((bounds[i + 1] - t0).toFixed(3));
  const dir = path.join(WORK, `chunk-${String(i).padStart(2, "0")}`);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  for (const entry of fs.readdirSync(BODY)) {
    if (entry === "index.html" || entry === "chunks" || entry === "renders" || entry.startsWith("renders-chunks")) continue;
    fs.cpSync(path.join(BODY, entry), path.join(dir, entry), { recursive: true });
  }

  const timeFn = reveals
    .map((r) => `        case ${JSON.stringify(r.selector)}: return ${r.start} - CHUNK_START;`)
    .join("\n");
  let html = src
    // CDN 版 GSAP 是这条链上唯一的网络依赖，而它会在渲染中途 ERR_TUNNEL_CONNECTION_FAILED：
    // chunk 4 就是这么崩的（`Cannot read properties of null (reading 'timeline')`），
    // 而前 4 块恰好网络通就过了——同一份 HTML 有的块成有的块败，正是「有时依赖外网」的形状。
    // 本地 gsap.min.js 项目里本来就有，直接用本地的，把外网从渲染路径上摘掉。
    .replace(
      /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/gsap@[^"]*"><\/script>/u,
      '<script src="gsap.min.js"></script>',
    )
    .replace(
      /var safeDur = [0-9.]+;/u,
      `var CHUNK_START = ${t0};\n      var CHUNK_LEN = ${len};\n      var safeDur = CHUNK_LEN;`,
    )
    .replace(
      /function revealCaption\(selector, start, hold\) \{/u,
      // 把 revealCaption 的**时间参数**换成块内时间，函数体原样保留。
      // 这里必须整体替换签名 + 第一行，不能再多插一个同名函数：先前那版写成了
      // 「__revealCaption 返回块内时间 + revealCaption 转调 + __unusedRevealCaption 保留原体」，
      // 结果真正的 tween 落进了那个 __unused 函数里，字幕一条都没上屏——
      // 而渲染照样「成功」，只有抽帧看得见。所以下面还要断言函数体还在。
      `function revealCaption(selector, start, hold) {
        var __start = __chunkTime(selector);
        tl.fromTo(selector, { opacity: 0, y: 12, scaleX: 0.92, scaleY: 0.99 }, { opacity: 1, y: 0, scaleX: 1, scaleY: 1, duration: 0.16, ease: "power3.out" }, __start);
        tl.set(selector, { opacity: 0, y: -10 }, __start + hold);
      }
      function __chunkTime(selector) {
        switch (selector) {
${timeFn}
        default: return 0;
        }
      }
      function __originalRevealCaption(selector, start, hold) {`,
    )
    .replace(/data-duration="[0-9.]+"/u, `data-duration="${len}"`);
  if (!html.includes("__chunkTime(selector)")) throw new Error("revealCaption 时间注入失败");
  if (!html.includes("tl.fromTo(selector, { opacity: 0, y: 12")) throw new Error("revealCaption 函数体丢失");
  // 字幕层的 will-change 是这条链上唯一压垮渲染页的东西：148 个字幕 div 一旦真的被
  // revealCaption 动起来，每个 div 都被提升成独立合成层并长期驻留，Chrome 的 GPU 合成器
  // 撑不住，渲染页在 25% 处直接 "Target closed"。原生成片（如《老妇还乡》）能过是因为
  // 那条路径帧数/分辨率/worker 组合不同；分块渲染下这一条必须先摘掉。
  // 视觉上无影响：opacity/transform 本来就走合成，GSAP 在 tween 期间自会加 will-change。
  html = html.replace(".caption { position: absolute;", ".caption { position: absolute;")
    .replace("; will-change: transform, opacity; text-shadow:", "; text-shadow:");
  if (html.includes(".caption { position: absolute") && html.includes("will-change: transform, opacity; text-shadow:")) {
    throw new Error("caption will-change 移除失败");
  }
  // BG_ONLY=1：只出背景层（场景图 + 慢推近 + 交叉淡入 + 水波涟漪），隐藏常驻书名与字幕。
  // 兜底渲染器 render_scene_map_template_fallback.py 自己画书名层与字幕层，再把水波声压在 30fps
  // 的正文段上；所以给它的 body-bg.mp4 必须是**没有字**的那一层，否则会叠出两层字。
  if (BG_ONLY) {
    html = html.replace(
      "</style>",
      "\n      .book-mark, .caption { display: none !important; }\n    </style>",
    );
    if (!html.includes(".caption { display: none")) throw new Error("BG_ONLY 注入失败");
  }
  if (!html.includes("var CHUNK_START")) throw new Error("safeDur 注入失败");
  if (!html.includes("__chunkTime(selector)")) throw new Error("revealCaption 时间注入失败");
  if (!html.includes("tl.fromTo(selector, { opacity: 0, y: 12")) throw new Error("revealCaption 函数体丢失");
  if (!html.includes('src="gsap.min.js"')) throw new Error("本地 GSAP 注入失败");
  fs.writeFileSync(path.join(dir, "index.html"), html);

  const out = path.join(OUT, `chunk-${String(i).padStart(2, "0")}.mp4`);
  // SKIP_RENDER=1 时只重建注入后的 HTML，让块可以单独重渲（调试用）
  if (process.env.SKIP_RENDER === "1") { parts.push(out); continue; }
  console.log(`\n=== chunk ${i}: ${t0.toFixed(2)}s → ${(t0 + len).toFixed(2)}s (${len}s) ===`);
  // 这台机器上常常还有别的渲染会话在跑，"Target closed" 是渲染页在内存/CPU 压力下崩掉，
  // 与组合本身无关（用未注入的原版 HTML 同样复现）。所以按 load 退避重试，而不是判死刑。
  const attempts = Number(process.env.CHUNK_RETRIES || "5");
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const load = os.loadavg()[0];
    if (load > 18 && attempt < attempts) {
      const wait = Math.min(180, 30 * attempt);
      console.log(`   [retry] load ${load.toFixed(1)} 偏高，等 ${wait}s 再渲（第 ${attempt}/${attempts} 次）`);
      await sleep(wait * 1000);
    }
    const r = spawnSync(
      "npx",
      ["--yes", "hyperframes@0.7.33", "render", "--quality", "standard",
       "--workers", process.env.HF_WORKERS || "1",
       "--browser-timeout", "180",
       "--protocol-timeout", "900000",
       "--output", out],
      { cwd: dir, stdio: "inherit", shell: false },
    );
    if (r.status === 0 && fs.existsSync(out)) break;
    if (attempt === attempts) throw new Error(`chunk ${i} render failed after ${attempts} attempts`);
    console.log(`   [retry] 第 ${attempt} 次失败，30s 后重试`);
    await sleep(30000);
  }
  parts.push(out);
}

// concat：重编码一次保证帧边界整齐
const listFile = path.join(WORK, "concat.txt");
fs.writeFileSync(listFile, parts.map((p) => `file '${p}'`).join("\n"));
const finalOut = path.join(OUT, BG_ONLY ? "body-bg-full.mp4" : "body-full.mp4");
ff(["-f", "concat", "-safe", "0", "-i", listFile, "-c", "copy", finalOut]);

const probe = JSON.parse(
  spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "json", finalOut],
    { encoding: "utf8" }).stdout,
);
console.log(`\n✅ ${finalOut} · ${parts.length} 块 · ${probe.format.duration}s（目标 ${TOTAL}s）`);
