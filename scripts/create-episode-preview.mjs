import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { buildEstimatedCaptionTimings } from "./lib/body-timings.mjs";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";
import { resolveScriptVersion, resolveSegmentVersion } from "./lib/script-version.mjs";
import { validateBodyScript } from "./lib/script-policy.mjs";
import { SUBTITLE_PUNCTUATION, displayLines, MAX_CAPTION_LINES } from "./lib/subtitle-display.mjs";
import { disclaimerCss, disclaimerHtml } from "./lib/disclaimer.mjs";
import { titleFontFaceCss, titleFontFamilyCss, titleFontWeightCss } from "./lib/title-font.mjs";

const ROOT = process.cwd();
// 上屏标点的字符集只认 scripts/lib/subtitle-punctuation.json，这里不再各自硬编码正则。
// displayKeep（译名间隔号 ·）属于专名而不是标点：计数、断句、画字都要把它当正文留着。
const PUNCT_STRIP = new Set(SUBTITLE_PUNCTUATION.displayStrip);
const PUNCT_KEEP = new Set(SUBTITLE_PUNCTUATION.displayKeep);
// 断点号上屏一律换成换行。旧口径把它换成一个全角空格（这里刻意不写出那个字符：
// 静态守卫 `npm run check` 会盯住两条渲染路径里的字面全角空格），屏上就出现
// 「漂亮[全角空格]聪明[全角空格]有钱」式的间隙，2026-09-27 起废止。换行在这里落成独立的
// block .seg，一条字幕最多 MAX_CAPTION_LINES 行。
const BREAK_TO = SUBTITLE_PUNCTUATION.displayBreakTo;
const regEscape = (ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const isDisplayPunct = (ch) => !PUNCT_KEEP.has(ch) && (/\s/.test(ch) || PUNCT_STRIP.has(ch));
// 与上屏完全一致的正文字符序列：删标点、留 ·，断句下标必须按它算，否则断点会整体偏移。
const displayChars = (rawText) => Array.from(String(rawText ?? "")).filter((ch) => !isDisplayPunct(ch));
// 一条上屏文本 → 视觉行数组，与 Pillow 渲染器的 caption_lines 同口径：
// 断点号和空格都是硬换行点（屏上零标点、零空白间隙），行内残留空白一并去掉。
// 超过一屏行数的由 pushCaptions() 顺延成顺序字幕，这里只负责拆。
const BREAK_RUN_RE = new RegExp(
  "[\\s" + [...SUBTITLE_PUNCTUATION.displayBreak ?? []].map(regEscape).join("") + "]+",
  "g",
);
function captionLines(text) {
  return String(text ?? "")
    .split(BREAK_RUN_RE)
    .flatMap((piece) => displayLines(piece))
    .filter(Boolean);
}
const FALLBACK_CAPTION_START = 1.5;
const [episodeName, requestedVersion] = process.argv.slice(2);

if (!episodeName) {
  console.error("Usage: node scripts/create-episode-preview.mjs <episode-name> [script-version]");
  process.exit(1);
}

const episodeDir = path.join(ROOT, "episodes", episodeName);
const version = resolveScriptVersion(episodeDir, requestedVersion);
const briefPath = path.join(episodeDir, "brief.json");
// 三段结构的正文文案是 script-body.csv（钩子 script-hook.csv），历史单段期只有 script-body.csv。
const scriptPath = ["script-body.csv"]
  .map((name) => path.join(episodeDir, name))
  .find((p) => fs.existsSync(p));
if (!scriptPath) {
  console.error(`Missing script-body.csv: ${episodeDir}`);
  process.exit(1);
}
// 正文段氛围图目录：改名后叫 images-body/，历史剧集只有 images/。两个都认、优先新名，
// 和 script-body.csv 的命名保持一致，否则改名当天预览会拿到空图目录。
const imagesDir = ["images-body", "images"]
  .map((sub) => path.join(episodeDir, sub))
  .find((dir) => fs.existsSync(dir)) || path.join(episodeDir, "images");
const audioTimingsPath = path.join(episodeDir, "audio", "body-timings.json");
const bodyVoicePath = path.join(episodeDir, "audio", "body-voiceover.mp3");

const workSlug = slugifyEpisodeName(episodeName);
const workDir = path.join(ROOT, "tmp", `preview-${workSlug}`);
const introDir = path.join(workDir, "intro");
const hookDir = path.join(workDir, "hook");
const bodyDir = path.join(workDir, "body");
// 钩子段（片头钩子）是本期专属的一段：文案 script-hook.csv、配音 钩子配音.mp3、画面 Pexels 氛围图。
// 三段结构 = 钩子 + 固定开场白（模板）+ 正文，钩子段可缺省——没有 script-hook.csv 时整期退回
// 历史的两段结构，交付门禁与模板裁切点都不变。
const hookScriptPath = path.join(episodeDir, "script-hook.csv");
const hookImagesDir = path.join(episodeDir, "images-hook");
const hasHook = fs.existsSync(hookScriptPath);
const defaultIntroBooksPath = path.join(ROOT, "templates", "shared-video-template", "intro", "default-book-list.json");

function readCsv(filePath) {
  const text = fs.readFileSync(filePath, "utf8").trim();
  const lines = text.split(/\r?\n/);
  const headers = parseCsvLine(lines.shift());
  return lines.map((line) => {
    const values = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"' && quoted && line[index + 1] === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  values.push(current);
  return values;
}

function copyFile(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function cleanDir(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch (e) {
    // best-effort cleanup; sandbox safe-delete guards may block bulk rm
  }
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch (e) {
    /* dir already exists */
  }
}

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function getTitleLayout(title) {
  const wrappedTitle = `《${title}》`;
  const fontSize = Math.max(46, Math.min(82, Math.floor(660 / Math.max(1, wrappedTitle.length + 1))));
  // 作者行 = 书名顶(70) + 书名字号 + 26px 恒定行距。旧的两档阶梯（≥70px→166，否则 140）
  // 在 66~69px 书名上只有 4px 行距（《乞力马扎罗的雪》2026-10-07 用户报「书籍信息行距太紧」），
  // 而 70px 档是 26px——同一个块两种行距。恒定行距让作者行跟着书名字号走，
  // 70px 档（166）与旧值逐字节相同，已交付版式不再漂移。
  // 正文兜底渲染器的 _AUTHOR_TOP 与这里必须同式同值：两处不同步，intro→正文切点作者行会跳位。
  const authorTop = 96 + fontSize;
  return { wrappedTitle, fontSize, authorTop };
}

function getDisplayTitle(brief) {
  return brief.display_title || brief.displayTitle || brief.title;
}

function getIntroBooks() {
  if (!fs.existsSync(defaultIntroBooksPath)) {
    throw new Error(`Missing fixed intro book list: ${defaultIntroBooksPath}`);
  }
  const books = JSON.parse(fs.readFileSync(defaultIntroBooksPath, "utf8"));
  if (!Array.isArray(books) || books.length !== 6 || books.some((book) => !book?.title || !book?.author)) {
    throw new Error("Fixed intro book list must contain exactly six real books with authors");
  }
  return books.map((book) => ({ title: String(book.title).trim(), author: String(book.author).trim() }));
}

// 在整段旁白上跑 silencedetect，返回内部停顿区间 [{start,end,duration}]。
function detectSilences(filePath, minDuration = 0.18) {
  if (!fs.existsSync(filePath)) return [];
  const result = spawnSync(
    "ffmpeg",
    ["-hide_banner", "-nostats", "-i", filePath, "-af", `silencedetect=n=-30dB:d=${minDuration}`, "-f", "null", "-"],
    { cwd: ROOT, encoding: "utf8", shell: false },
  );
  const log = `${result.stdout || ""}${result.stderr || ""}`;
  const starts = [];
  const ends = [];
  let m;
  const startRe = /silence_start:\s*([0-9.]+)/g;
  while ((m = startRe.exec(log))) starts.push(Number(m[1]));
  const endRe = /silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)/g;
  while ((m = endRe.exec(log))) ends.push({ end: Number(m[1]), duration: Number(m[2]) });
  const silences = [];
  for (let i = 0; i < starts.length && i < ends.length; i += 1) {
    silences.push({ start: starts[i], end: ends[i].end, duration: ends[i].duration });
  }
  return silences;
}

// 把清洗后的字符数组按给定断点（去重已排序）用换行串起来。
function buildBrokenText(chars, breaks) {
  const sorted = [...breaks].sort((a, b) => a - b);
  const out = [];
  let prev = 0;
  for (const b of sorted) {
    if (b <= prev || b >= chars.length) continue;
    out.push(chars.slice(prev, b).join(""));
    out.push(BREAK_TO);
    prev = b;
  }
  out.push(chars.slice(prev).join(""));
  return out.join("").trim();
}

// 清洗后文本里、每个"原标点之前"的字符下标 = 在该处之后断开的断点（自然短语边界）。
// 计数时必须和上屏一致：displayKeep 的 · 算正文，任何空白算断点，否则断点会整体偏移。
function naturalBoundaries(rawText) {
  const chars = Array.from(rawText || "");
  const boundaries = [];
  let cleanedIdx = 0;
  for (const ch of chars) {
    if (isDisplayPunct(ch)) {
      boundaries.push(cleanedIdx); // 标点之前的清洗位 = 下一短语起点
      continue;
    }
    cleanedIdx += 1;
  }
  return boundaries;
}

// 按旁白音频节奏断句：
// 1) 候选断点只取"原标点处"的自然短语边界（保证词不被劈开）；
// 2) 仅保留那些位置附近真有旁白停顿（≥0.18s）的边界——即 narrator 在逗号等处真喘了口气；
// 3) 按停顿时长降序，每句最多 2–3 个断点，相邻断点至少间隔 4 字。
// 返回值里每个断点都是一个换行：调用方 captionLines() 据此拆成独立的 block .seg，
// 行间不留空白间隙（旧口径在这里插一个全角空格，屏上出现空洞）。
// 无可用时间轴 / 无节奏断点时返回 null。
function rhythmBreakText(rawText, sentenceStart, sentenceEnd, silences) {
  const chars = displayChars(rawText);
  const n = chars.length;
  if (n === 0) return null;
  if (!Number.isFinite(sentenceStart) || !Number.isFinite(sentenceEnd) || sentenceEnd <= sentenceStart) return null;
  const span = sentenceEnd - sentenceStart;
  const boundaries = naturalBoundaries(rawText).filter((b) => b > 0 && b < n);
  const rhythmic = [];
  for (const b of boundaries) {
    const t = sentenceStart + (b / n) * span;
    let best = null;
    for (const s of silences) {
      if (s.duration < 0.18) continue;
      const mid = (s.start + s.end) / 2;
      if (Math.abs(mid - t) <= 0.45) {
        if (!best || s.duration > best.duration) best = s;
      }
    }
    if (best) rhythmic.push({ idx: b, duration: best.duration });
  }
  let chosen = [];
  if (rhythmic.length > 0) {
    const sorted = rhythmic.sort((a, b) => b.duration - a.duration);
    const picked = [];
    for (const c of sorted) {
      if (picked.some((ci) => Math.abs(ci - c.idx) < 4)) continue;
      picked.push(c.idx);
      if (picked.length >= (n > 30 ? 3 : 2)) break;
    }
    chosen = picked;
  }
  if (chosen.length === 0) {
    // 标点处都没有对应停顿：吸附最长停顿到最近标点边界；整句无标点且较长时，中点断一次。
    if (boundaries.length > 0) {
      let longest = null;
      for (const s of silences) {
        if (s.duration < 0.18) continue;
        if (!longest || s.duration > longest.duration) longest = s;
      }
      if (longest) {
        const lt = (longest.start + longest.end) / 2;
        let bestB = null;
        let bestD = Infinity;
        for (const b of boundaries) {
          const bt = sentenceStart + (b / n) * span;
          const d = Math.abs(bt - lt);
          if (d < bestD) {
            bestD = d;
            bestB = b;
          }
        }
        if (bestB != null) chosen = [bestB];
      }
    } else if (n > 14) {
      chosen = [Math.round(n / 2)];
    }
  }
  if (chosen.length === 0) return null; // 交给调用方：整行按断点号换成换行后上屏（captionLines）
  return buildBrokenText(chars, new Set(chosen));
}

// rhythmBreakText 的"逐行精准对齐"版：除断句文本外，还返回每个节奏段的音频子时间轴。
// 关键：断点时间不再用"匀速字符占比"插值（那假设 narrator 语速恒定、会漂），
// 而是**锚定到本句区间内真实的旁白停顿（silencedetect）的结束时刻**——
// 上一段从本句起点显示到该停顿结束，下一段从该停顿结束开始。
// 即每行字幕恰好覆盖"它被念出来的那段音频"，并一直显示到下一行开始念，精准对齐旁白节奏。
function rhythmBreakSegments(rawText, sentenceStart, sentenceEnd, silences) {
  const chars = displayChars(rawText);
  const n = chars.length;
  if (n === 0) return null;
  if (!Number.isFinite(sentenceStart) || !Number.isFinite(sentenceEnd) || sentenceEnd <= sentenceStart) return null;
  const span = sentenceEnd - sentenceStart;
  const boundaries = naturalBoundaries(rawText).filter((b) => b > 0 && b < n);
  // 只保留落在本句区间内的真实停顿，用于把断点时间锚定到 narrator 真实的换气点。
  const inRange = (silences || []).filter(
    (s) => Number.isFinite(s.start) && Number.isFinite(s.end) && s.end > s.start
      && s.end >= sentenceStart - 0.05 && s.start <= sentenceEnd + 0.05,
  );
  const rhythmic = [];
  for (const b of boundaries) {
    const t = sentenceStart + (b / n) * span; // 仅用于就近匹配停顿，不做最终时间
    let best = null;
    for (const s of inRange) {
      if (s.duration < 0.18) continue;
      const mid = (s.start + s.end) / 2;
      if (Math.abs(mid - t) <= 0.5) {
        if (!best || s.duration > best.duration) best = s;
      }
    }
    if (best) rhythmic.push({ idx: b, silence: best });
  }
  let chosen = [];
  if (rhythmic.length > 0) {
    const sorted = rhythmic.sort((a, b) => b.silence.duration - a.silence.duration);
    const picked = [];
    for (const c of sorted) {
      if (picked.some((ci) => Math.abs(ci.idx - c.idx) < 4)) continue;
      picked.push(c);
      if (picked.length >= (n > 30 ? 3 : 2)) break;
    }
    chosen = picked;
  }
  // 去重：两个断点若锚定到同一个真实停顿对象，只保留一个，
  // 否则同一静音会被重复映射，导致 segTimes 出现相等项、相邻字幕时间重叠。
  const seenSil = new Set();
  const deduped = [];
  for (const c of chosen) {
    if (c.silence && seenSil.has(c.silence)) continue;
    if (c.silence) seenSil.add(c.silence);
    deduped.push(c);
  }
  chosen = deduped;
  if (chosen.length === 0) {
    if (boundaries.length > 0) {
      let longest = null;
      for (const s of inRange) {
        if (s.duration < 0.18) continue;
        if (!longest || s.duration > longest.duration) longest = s;
      }
      if (longest) {
        const lt = (longest.start + longest.end) / 2;
        let bestB = null, bestD = Infinity;
        for (const b of boundaries) {
          const bt = sentenceStart + (b / n) * span;
          const d = Math.abs(bt - lt);
          if (d < bestD) { bestD = d; bestB = b; }
        }
        if (bestB != null) chosen = [{ idx: bestB, silence: longest }];
      }
    } else if (n > 14) {
      chosen = [{ idx: Math.round(n / 2), silence: null }];
    }
  }
  if (chosen.length === 0) return null;
  // 必须有真实停顿才能做精准锚定；否则回退为单段（整句同屏，由调用方处理）。
  if (chosen.some((c) => !c.silence || !Number.isFinite(c.silence.end))) return null;
  const sorted = [...chosen].sort((a, b) => a.idx - b.idx);
  const cuts = [0, ...sorted.map((c) => c.idx), n];
  // 精准锚定到"停顿之间的语音段"：本句常带前导静音（标题/上句余隙），
  // 真正的第一句短语从"首个停顿结束"才开始被念。因此：
  //   · 首行起点 = 首个停顿之前的静音结束（即第一句短语被念起的时刻）
  //   · 每行 [上一段停顿结束 → 本段停顿开始] = 恰好覆盖它被念出来的那段音频（不含其后停顿）
  //   · 末行终点 = 末段停顿之后的静音起点（即下一句即将开始前的余隙）
  const sortedSil = [...inRange].sort((a, b) => a.start - b.start);
  let prevSilEnd = sentenceStart;
  for (let k = sortedSil.length - 1; k >= 0; k -= 1) {
    if (sortedSil[k].end <= sorted[0].silence.start + 0.001 && sortedSil[k].end > prevSilEnd) {
      prevSilEnd = sortedSil[k].end;
    }
  }
  const lastSil = sorted[sorted.length - 1].silence;
  let nextSilStart = sentenceEnd;
  for (const s of sortedSil) {
    if (s.start >= lastSil.end - 0.001 && s.start < nextSilStart) {
      nextSilStart = s.start;
    }
  }
  prevSilEnd = Math.min(Math.max(prevSilEnd, sentenceStart), lastSil.start);
  nextSilStart = Math.max(Math.min(nextSilStart, sentenceEnd), lastSil.end);
  // 每个短语 = 一个"语音段"：起点 = 上一个停顿结束（或本句前导静音结束），
  // 终点 = 本停顿开始（即该短语被念完、narrator 换气的那一刻）。
  // 这样每行字幕只覆盖"它被念出来的那段音频"，不含其后的停顿空档，
  // 字幕显示时长 == 真实说话时长 → 与人声语速严格一致，
  // 不再因各句后接停顿长短不一而忽快（短停顿早切）忽慢（长停顿久留）。
  // 停顿期间刻意留白（不显示任何字幕），与"人声停顿处无台词"真实对应。
  const segs = [];
  for (let k = 0; k < sorted.length + 1; k += 1) {
    const sStart = k === 0 ? prevSilEnd : sorted[k - 1].silence.end;
    const sEnd = k === sorted.length ? nextSilStart : sorted[k].silence.start;
    const text = chars.slice(cuts[k], cuts[k + 1]).join("");
    if (!text) continue;
    segs.push({ text, start: Number(sStart.toFixed(2)), end: Number(sEnd.toFixed(2)) });
  }
  if (segs.length === 0) return null;
  // 安全网：每段至少 MIN_GAP 长（过短短语仍可读），段间天然留停顿空档不重叠。
  const MIN_GAP = 0.16;
  for (const seg of segs) {
    if (!(seg.end > seg.start + MIN_GAP)) seg.end = Number((seg.start + MIN_GAP).toFixed(2));
  }
  for (let i = 1; i < segs.length; i += 1) {
    if (segs[i].start < segs[i - 1].end + 0.02) {
      const candidate = Number((segs[i - 1].end + 0.02).toFixed(2));
      if (candidate < segs[i].end) segs[i].start = candidate;
    }
  }
  if (segs[segs.length - 1].end > sentenceEnd) {
    segs[segs.length - 1].end = Number(sentenceEnd.toFixed(2));
  }
  return { segments: segs.map((s) => ({ text: s.text, start: s.start, end: s.end })) };
}

function createIntro(brief) {
  const displayTitle = getDisplayTitle(brief);
  const titleLayout = getTitleLayout(displayTitle);
  const introBooks = getIntroBooks();
  fs.mkdirSync(introDir, { recursive: true });
  fs.cpSync(
    path.join(ROOT, "templates", "shared-video-template", "intro", "media"),
    path.join(introDir, "media"),
    { recursive: true },
  );
  copyFile(path.join(ROOT, "templates", "shared-video-template", "intro", "package.json"), path.join(introDir, "package.json"));
  copyFile(path.join(ROOT, "templates", "shared-video-template", "gsap.min.js"), path.join(introDir, "gsap.min.js"));
  let html = fs.readFileSync(path.join(ROOT, "templates", "shared-video-template", "intro", "index.html"), "utf8");
  html = html
    .replaceAll('<div class="page-title">{{TARGET_TITLE}}</div>', `<div class="page-title" style="font-size: ${titleLayout.fontSize}px;">${esc(titleLayout.wrappedTitle)}</div>`)
    .replaceAll('<div class="page-author">{{TARGET_AUTHOR}}</div>', `<div class="page-author" style="top: ${titleLayout.authorTop}px;">${esc(`${brief.author} / 著`)}</div>`)
    .replaceAll("{{TARGET_TITLE}}", titleLayout.wrappedTitle)
    .replaceAll("{{TARGET_AUTHOR}}", `${brief.author} / 著`);
  introBooks.forEach((book, index) => {
    html = html
      .replaceAll(`{{LIST_TITLE_${index + 1}}}`, `《${book.title}》`)
      .replaceAll(`{{LIST_AUTHOR_${index + 1}}}`, `${book.author} / 著`);
  });
  fs.writeFileSync(path.join(introDir, "index.html"), html);
  copyFile(path.join(imagesDir, "result-bridge.png"), path.join(introDir, "media", "pages", "result.png"));
}

// 读一段的时间轴。正文读 body-timings.json、钩子读 hook-timings.json，走同一份解析与降级逻辑：
// 文件缺失 / 解析失败 / 版本不符时都退回「配音时长 + script 时长提示」，不阻塞渲染。
function readSegmentTimings(timingsFile, voiceFile, version, label) {
  const fallbackDuration = readAudioDuration(voiceFile);
  if (!fs.existsSync(timingsFile)) {
    if (fallbackDuration) console.warn(`Missing ${label} timings; using script duration hints for captions`);
    return fallbackDuration ? { duration: fallbackDuration, byOrder: new Map() } : null;
  }
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(timingsFile, "utf8"));
  } catch (error) {
    console.warn(`Could not read ${label} timings; using script duration hints: ${error.message}`);
    return fallbackDuration ? { duration: fallbackDuration, byOrder: new Map() } : null;
  }
  if (raw.scriptVersion && raw.scriptVersion !== version) {
    console.warn(`Ignoring ${label} timings for ${raw.scriptVersion}; using script duration hints for ${version}`);
    return fallbackDuration ? { duration: fallbackDuration, byOrder: new Map() } : null;
  }
  if (raw.alignment?.requiresAgentReview) {
    console.warn(
      `${label} timings need Agent review (${raw.alignment.method || "unknown method"}): `
      + `${raw.alignment.reason || "low-confidence ASR alignment"}`,
    );
  }
  const byOrder = new Map((raw.captions || []).map((item) => [Number(item.order), item]));
  return {
    duration: Number(raw.duration) || fallbackDuration,
    byOrder,
    display: raw.captionDisplay === "sentence" ? "sentence" : "phrase",
  };
}

function readOptionalBodyTimings(version) {
  return readSegmentTimings(audioTimingsPath, bodyVoicePath, version, "body");
}

function readAudioDuration(filePath) {
  if (!fs.existsSync(filePath)) return 0;
  const result = spawnSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filePath],
    { cwd: ROOT, encoding: "utf8", shell: false },
  );
  const duration = Number(result.stdout?.trim());
  return result.status === 0 && Number.isFinite(duration) && duration > 0 ? duration : 0;
}

// 一段可渲染的画面（正文段 / 钩子段）。两段共用这一个实现：字幕断句、折行、标点策略、
// 慢推镜、场景切分全部只写一份，钩子段不会长出一套和正文分叉的规则。
// segment.imagesDir = 该段的 Pexels 氛围图目录；segment.voicePath = 该段配音（断句用）；
// segment.timings = 该段时间轴；segment.coverImage = 首层 static 图（正文是结果桥接图，
// 钩子段用它自己的封面图）。
// 常驻书名层只在正文段出现：钩子段是「还没报名字的那句话」，用户 2026-10-01 明确要求去掉
// 《书名》/作者行——书名在钩子里先亮出来，钩子就退化成书目卡片，悬念被顶部的字提前泄掉。
// 正文段照旧压常驻书名层（水波→定格页→正文那一切换刚把书名给到观众，需要它一直在场）。
function createBody(brief, rows, audioTimings, segment = {}) {
  const {
    outDir = bodyDir,
    images: segmentImagesDir = imagesDir,
    voicePath = bodyVoicePath,
    coverImage = "result-bridge.png",
    coverMediaName = "00-result-bridge.png",
    packageName = "body",
    // 桥接首层：正文段用结果页定格接住片头，钩子段没有片头可接（它就是全片第一个画面），
    // 所以钩子段从第一张氛围图起幅，也不在起点画水波——水波属于「片头→正文」那一个切点。
    useBridge = true,
    // 常驻《书名》/ 作者叠层：正文 true、钩子 false（见上）。
    showBookMark = true,
    // 常驻声明「内容为书评与感悟[全角空格]素材来源于网络」：同样只在正文段出现。
    // 文案/字号/灰度/下边距的唯一真源是 scripts/lib/disclaimer.json（Python 侧同一份），
    // 这里只负责把它拼成 CSS 与 HTML——写死一个字就会和兜底渲染器、验收门禁分叉。
    showDisclaimer = true,
  } = segment;
  const displayTitle = getDisplayTitle(brief);
  const titleLayout = getTitleLayout(displayTitle);
  fs.mkdirSync(path.join(outDir, "media"), { recursive: true });
  copyFile(path.join(ROOT, "templates", "shared-video-template", "gsap.min.js"), path.join(outDir, "gsap.min.js"));
  if (useBridge) copyFile(path.join(segmentImagesDir, coverImage), path.join(outDir, "media", coverMediaName));
  // 动态检测 atmosphere-*.png（支持长视频多图），按数字排序后按序铺设场景
  const atmosphereFiles = fs
    .readdirSync(segmentImagesDir)
    .filter((name) => /^atmosphere-\d+\.png$/u.test(name))
    .sort((a, b) => Number(a.match(/atmosphere-(\d+)/u)[1]) - Number(b.match(/atmosphere-(\d+)/u)[1]));
  if (atmosphereFiles.length === 0) throw new Error(`No atmosphere-*.png found in ${segmentImagesDir}`);
  atmosphereFiles.forEach((name, index) => {
    copyFile(path.join(segmentImagesDir, name), path.join(outDir, "media", `${String(index + 1).padStart(2, "0")}-atmosphere.png`));
  });
  const sceneCount = atmosphereFiles.length;
  const sceneCss = atmosphereFiles
    .map((_, index) => `      .s${index + 1} .photo { background-image: url("media/${String(index + 1).padStart(2, "0")}-atmosphere.png"); }`)
    .join("\n");
  const sceneSections = atmosphereFiles
    .map((_, index) => `      <section class="scene s${index + 1}" data-layout-ignore><div class="photo" data-layout-ignore></div></section>`)
    .join("\n");

  const estimatedByOrder = audioTimings?.duration && audioTimings.byOrder.size === 0
    ? new Map(buildEstimatedCaptionTimings(
      rows,
      [{ start: FALLBACK_CAPTION_START, end: Math.max(FALLBACK_CAPTION_START + 0.3, audioTimings.duration - 0.2) }],
      audioTimings.duration,
      0,
    ).map((item) => [item.order, item]))
    : new Map();
  // 逐字时间轴（Whisper 词级 + LCS 字符对齐）：优先逐字亮起。
  const wordTimingsPath = path.join(episodeDir, "audio", "word-timings.json");
  let wordTimings = null;
  if (fs.existsSync(wordTimingsPath)) {
    try { wordTimings = JSON.parse(fs.readFileSync(wordTimingsPath, "utf8")); } catch (e) { wordTimings = null; }
  }
  let cursor = audioTimings?.duration ? FALLBACK_CAPTION_START : 0.72;
  const speechTimings = rows.map((row) => {
    const order = Number(row.order);
    const audioTiming = audioTimings?.byOrder.get(order) || estimatedByOrder.get(order);
    const start = Number(audioTiming?.start);
    const end = Number(audioTiming?.end);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      const safeStart = Math.max(0, start);
      const safeEnd = Math.max(safeStart + 0.3, end);
      cursor = Math.max(cursor, safeEnd + 0.12);
      return { selector: `.c${row.order}`, start: safeStart, end: safeEnd };
    }
    const duration = Number(row.duration_hint || 2);
    const item = { selector: `.c${row.order}`, start: cursor, end: cursor + Math.max(1.05, duration - 0.42) };
    cursor += duration;
    return item;
  });
  // 上屏一行真正画出来的字数：标点由 scripts/lib/subtitle-punctuation.json 统一删掉（译名间隔号 · 保留），
  // 字号档位 56px / 50px 只能按画出来的字数定，按 CSV 原文算会偏大。
  const displayLen = (t) => displayLines(String(t ?? "")).join("").length;
  // 整段旁白静音检测，用于按旁白节奏断句 + 逐行精准对齐。
  const silenceList = detectSilences(voicePath, 0.18);
  function pushEnglishCaption(row, order, rowStart, rowEnd) {
    if (!row.en) return;
    const enStart = Number(Math.max(0, rowStart).toFixed(2));
    const enEnd = Number(Math.max(enStart + 0.3, rowEnd).toFixed(2));
    captionRows.push(`      <div class="caption en cen${order}">${esc(String(row.en))}</div>`);
    revealItems.push({ selector: `.cen${order}`, start: enStart, hold: Number((enEnd - enStart).toFixed(2)) });
  }
  // 一条字幕 = 一个 .caption div，里面每个视觉行是一个 block .seg（CSS: .blk .seg{display:block}）。
  // 断点号已经换成换行，所以行与行之间不会再有空格间隙；行窗口相同就同时出现，
  // 需要逐短语揭示时由调用方给每行所属段自己的窗口。
  function pushCaptionBlock(order, small, linesWithWindow) {
    const spans = linesWithWindow
      .map((item, i) => `        <span class="seg seg${i}">${esc(item.text)}</span>`)
      .join("\n");
    captionRows.push(`      <div class="caption c${order}${small} blk">\n${spans}\n      </div>`);
    linesWithWindow.forEach((item, i) => {
      const start = Number(Math.max(0, item.start).toFixed(2));
      const end = Number(Math.max(start + 0.16, item.end).toFixed(2));
      revealItems.push({ selector: `.c${order} .seg${i}`, start, hold: Number((end - start).toFixed(2)) });
    });
  }
  // 一屏只装 MAX_CAPTION_LINES 个视觉行：装不下的行顺延成下一条字幕，而不是把 5、6 行
  // 叠在同一屏（旧口径用全角空格把它们连成一行、交给容器宽度折行，屏上既有空洞、行数也失控）。
  // 批内每行沿用所属段的真实窗口；只有各批窗口本来重叠（= 调用方要求整行同屏）时，
  // 才按字数把整行窗口比例切给各批，保证前后两条字幕不会同屏压字。
  // 单行像素宽度重排：caption 容器 652px（720 − 34×2），字号档 56/50 → 单行容量 11/13 字。
  // 一条 seg 超过 13 字（50px 档也放不下）时 DOM 会自行硬折——折点由浏览器挑，
  // 常折出「天」这类孤字行（《少年Pi的奇幻漂流》钩子第二条实测三行）。这里在
  // 进批次之前按词边界均衡拆行：断点不许落在数字串（二百二十七天）内部，
  // 也不许让下一行以虚词/闭合标点起头；子行按字数比例继承原行的窗口。
  const NUMERAL_RUN = /[一二三四五六七八九十百千万零两〇]/u;
  const BAD_WRAP_START = "」』）》。，、；：！？…的了着地得吗呢吧啊之";
  function splitLongLine(item) {
    const chars = Array.from(item.text);
    const n = chars.length;
    if (n <= 13) return [item];
    const k = Math.ceil(n / 13);
    const allowed = [];
    for (let j = 1; j < n; j += 1) {
      if (NUMERAL_RUN.test(chars[j - 1]) && NUMERAL_RUN.test(chars[j])) continue; // 数字串内部
      if (BAD_WRAP_START.includes(chars[j])) continue;                            // 行首禁则
      if ("《“『「(".includes(chars[j - 1])) continue;                             // 开引号不能收尾
      allowed.push(j);
    }
    if (allowed.length === 0) return [item];
    const target = n / k;
    const cuts = [];
    let prevCut = 0;
    for (let i = 1; i <= k; i += 1) {
      if (i === k) { cuts.push(n); break; }
      const want = Math.round(i * target);
      let best = null;
      for (const j of allowed) {
        if (j <= prevCut || j >= n - 1) continue;
        // 平局取更靠后的断点：14 字句在 7 处打平时，切 8 得「他替你在海上放了 / 二百二十七天」，
        // 数字短语独立成行；切 6 会把动词「放了」甩到下一行行首。
        if (best === null || Math.abs(j - want) <= Math.abs(best - want)) best = j;
      }
      if (best === null) return [item];
      cuts.push(best);
      prevCut = best;
    }
    const out = [];
    let prev = 0;
    cuts.forEach((cut) => {
      // 拆出来的各行**共享**原 seg 的整个窗口：它们是同一个短语的同一屏两行，
      // 在真实停顿处一起出现（2026-10-06 用户报「重新准确生成钩子字幕」——
      // 之前按字数比例切窗口，第二条字幕在语流中间弹出来，配音还没停字幕先换）。
      out.push({ text: chars.slice(prev, cut).join(""), start: item.start, end: item.end, wrap: item });
      prev = cut;
    });
    return out;
  }
  function pushCaptions(order, linesWithWindow, label) {
    // 先做单行宽度重排，再打包。打包规则：拆行产生的**多行同属一个换行组**，
    // 必须整组待在同一条字幕里（两行一组的组满一条）；单行之间仍然 2 行一条
    // 顺延，和旧行为一致。这样「你没说出口的那句话」独自一条（切换点=逗号处的
    // 真实停顿），「他替你在海上放了 / 二百二十七天」作为两行一条随后出现，
    // 不再把两个分句混进同一条、也不在语流中间换字幕。
    const units = linesWithWindow.map((item) => splitLongLine(item));
    const batches = [];
    let cur = [];
    for (const unit of units) {
      if (unit.length > MAX_CAPTION_LINES) {
        if (cur.length) { batches.push(cur); cur = []; }
        for (let i = 0; i < unit.length; i += MAX_CAPTION_LINES) {
          batches.push(unit.slice(i, i + MAX_CAPTION_LINES));
        }
        continue;
      }
      if (cur.length + unit.length > MAX_CAPTION_LINES) {
        batches.push(cur);
        cur = [];
      }
      cur.push(...unit);
      if (cur.length === MAX_CAPTION_LINES) {
        batches.push(cur);
        cur = [];
      }
    }
    if (cur.length) batches.push(cur);
    if (batches.length === 0) return;
    const overlapped = batches.some((batch, i) => {
      if (i === 0) return false;
      const prev = batches[i - 1];
      return batch[0].start < prev[prev.length - 1].end - 0.001;
    });
    if (overlapped) {
      const start = batches[0][0].start;
      const last = batches[batches.length - 1];
      const end = Math.max(last[last.length - 1].end, start + 0.3);
      const weights = batches.map((batch) => batch.reduce(
        (sum, item) => sum + Math.max(1, Array.from(item.text).length), 0));
      const totalWeight = weights.reduce((sum, w) => sum + w, 0);
      let cursor = start;
      batches.forEach((batch, i) => {
        const batchEnd = i === batches.length - 1
          ? end
          : Math.min(end, cursor + (end - start) * (weights[i] / totalWeight));
        batch.forEach((item) => { item.start = cursor; item.end = batchEnd; });
        cursor = batchEnd;
      });
      console.warn(`⚠️ order ${order} 一屏装不下（${label}）：已顺延成 ${batches.length} 条顺序字幕`);
    }
    batches.forEach((batch, i) => {
      const widest = Math.max(...batch.map((item) => Array.from(item.text).length));
      pushCaptionBlock(i === 0 ? order : `${order}b${i + 1}`, widest > 11 ? " small" : "", batch);
    });
  }
  // 为每一行字幕构建"逐短语"分段：每个分段携带自己真实的旁白子时间轴，
  // 让它恰好在对应短语被念出来的那一刻才亮起（精准对齐，而非整句在句首一次性全亮）。
  const captionRows = [];
  const revealItems = [];
  const sentenceMode = audioTimings?.display === "sentence";
  rows.forEach((row, index) => {
    const order = Number(row.order);
    const rawText = String(row.text || "");
    // 字号档位不再按整行字数定：一行现在可能顺延成多条字幕，字号由每条字幕最宽的那一行定。
    const audioTiming = audioTimings?.byOrder?.get(order);
    const hasTiming = audioTiming && Number.isFinite(audioTiming.start) && Number.isFinite(audioTiming.end);
    const rowTiming = speechTimings[index];
    const rowStart = rowTiming ? rowTiming.start : (hasTiming ? Math.max(0, audioTiming.start) : 0.72);
    const rowEnd = rowTiming ? rowTiming.end : (hasTiming ? Math.max(rowStart + 0.3, audioTiming.end) : rowStart + 2);

    // —— 整句同屏模式：整句在句首一次性亮起、句尾一次性熄灭（一句一句显示，不逐短语）——
    if (sentenceMode) {
      const sentStart = Number(Math.max(0, rowStart).toFixed(2));
      const sentEnd = Number(Math.max(sentStart + 0.3, rowEnd).toFixed(2));
      // 视觉行 = 断点号/空格硬拆出来的段（屏上零标点、零空白）；一屏装不下的行顺延成下一条字幕。
      pushCaptions(order, captionLines(rawText).map((text) => ({
        text, start: sentStart, end: sentEnd,
      })), rawText);
      pushEnglishCaption(row, order, rowStart, rowEnd);
      return;
    }

    // —— 整句精准对齐：用词级时间戳锚定整句的起止（首字念出时整句亮起、末字念完时熄灭）——
    const wordList = wordTimings?.byOrder?.[String(order)];
    if (Array.isArray(wordList) && wordList.length > 0) {
      const spoken = wordList.filter((item) => item.ch !== " ");
      if (spoken.length > 0) {
        const sentStart = Number(Math.max(0, spoken[0].start).toFixed(2));
        const sentEnd = Number(Math.max(sentStart + 0.3, spoken[spoken.length - 1].end).toFixed(2));
        // 词级时间轴只用来定整句起止；上屏版式仍走 captionLines：断点号/空格都是换行点。
        const joined = wordList.map((item) => item.ch).join("");
        let lines = captionLines(joined);
        if (lines.length === 0) lines = captionLines(rawText);
        pushCaptions(order, lines.map((text) => ({ text, start: sentStart, end: sentEnd })), joined);
        pushEnglishCaption(row, order, rowStart, rowEnd);
        return;
      }
    }

    // 优先按旁白音频节奏拆成多个短语段；无内部停顿时回退为单段（整句同屏）。
    let segs = null;
    if (hasTiming) {
      const rb = rhythmBreakSegments(rawText, audioTiming.start, audioTiming.end, silenceList);
      if (rb && rb.segments && rb.segments.length > 0) segs = rb.segments;
    }
    if (!segs || segs.length === 0) {
      const display = hasTiming
        ? (rhythmBreakText(rawText, audioTiming.start, audioTiming.end, silenceList) ?? rawText)
        : rawText;
      segs = [{ text: display, start: rowStart, end: rowEnd }];
    }
    // 每个节奏段各自的窗口；段内若还有断点（回退单段时最常见）拆成多行、共用该段窗口。
    const linesWithWindow = [];
    segs.forEach((seg) => {
      captionLines(seg.text)
        .forEach((text) => linesWithWindow.push({ text, start: seg.start, end: seg.end }));
    });
    if (linesWithWindow.length === 0) {
      console.warn(`⚠️ order ${order} 整行去标点后无正文可上屏，跳过字幕：${rawText}`);
      pushEnglishCaption(row, order, rowStart, rowEnd);
      return;
    }
    pushCaptions(order, linesWithWindow, rawText);
    pushEnglishCaption(row, order, rowStart, rowEnd);
  });
  const captionHtml = captionRows.join("\n");
  const revealJs = revealItems
    .map((item) => `      revealCaption("${item.selector}", ${item.start}, ${item.hold});`)
    .join("\n");
  const lastCaptionEnd = revealItems.reduce((max, item) => Math.max(max, item.start + item.hold), 0);
  const duration = audioTimings?.duration
    ? Number(audioTimings.duration.toFixed(2))
    : Number((cursor + 0.8).toFixed(2));
  const safeDuration = Number(Math.max(duration, lastCaptionEnd + 0.4).toFixed(2));
  const sceneTwo = Number((safeDuration * 0.34).toFixed(2));
  const sceneThree = Number((safeDuration * 0.67).toFixed(2));

  const html = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=720, height=960" />
    <link rel="icon" href="data:," />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * { box-sizing: border-box; }
      @font-face { font-family: "DeYiHei"; src: local("DeYiHei"), local("DeYi Hei"), local("德意黑"); }
      @font-face { font-family: "德意黑"; src: local("德意黑"), local("DeYiHei"), local("DeYi Hei"); }
      @font-face { font-family: "STHeiti"; src: local("STHeiti"), local("STHeitiSC-Medium"); }
      @font-face { font-family: "Hiragino Sans GB"; src: local("Hiragino Sans GB"); }
${titleFontFaceCss()}
      html, body { width: 720px; height: 960px; margin: 0; overflow: hidden; background: #000; }
      body { font-family: "DeYiHei", "德意黑", "STHeiti", "Hiragino Sans GB", sans-serif; color: #fff; }
      #root { position: relative; width: 720px; height: 960px; overflow: hidden; background: #000; }
      /* z-index 必须显式给，而且必须**不靠 opacity 来生成层叠上下文**。
         .photo 是 position:absolute + z-index:1，落在 root 的层叠上下文里；而 .scene
         若不写 z-index，就只有 opacity<1 时才是层叠上下文。于是「淡入下一层」这条路
         单独存在时是看不见的：下层（opacity<1，paint 在 z=0 组）整段被上一层的
         .photo（z=1，paint 在正 z 组）盖住，直到 opacity 正好等于 1 的那一刻它不再
         是层叠上下文、自己的 .photo 才挤进 z=1 组按 DOM 顺序压过上一层 —— 0.72s 的
         溶解在画面上变成**一帧硬切**（2026-10-04《丧钟为谁而鸣》hook.mp4 实测
         60.7→39.9 单帧掉 20.8，判据③红）。写死 z-index:1 之后每一层永远是层叠
         上下文、整层作为一个单元 paint，同值按 DOM 顺序 → 后一层永远在前一层之上，
         合成即 f·上层 + (1−f)·下层，与正文 ffmpeg xfade 同一条凸组合。
         字幕 z-index:9、书名层 z-index:8 都高于场景层，不受影响。 */
      .scene { position: absolute; inset: 0; opacity: 0; overflow: hidden; z-index: 1; }
      .scene:first-of-type { opacity: 1; }
      .photo { position: absolute; inset: -22px; z-index: 1; background-size: cover; background-position: center; background-repeat: no-repeat; transform-origin: 50% 50%; will-change: transform; }
${useBridge ? `      .bridge .photo { inset: 0; background-image: url("media/${coverMediaName}"); }` : ""}
${sceneCss}
${showBookMark ? `      .book-mark { position: absolute; inset: 0; z-index: 8; text-align: center; color: #fff; opacity: 1; transform-origin: 50% 120px; font-family: ${titleFontFamilyCss()}; font-weight: ${titleFontWeightCss()}; }
      .book-title { position: absolute; left: 28px; right: 28px; top: 70px; display: block; font-size: ${titleLayout.fontSize}px; line-height: 1; font-weight: ${titleFontWeightCss()}; letter-spacing: 0.04em; white-space: nowrap; text-shadow: 0 7px 18px rgba(0, 0, 0, 0.96); }
      .book-author { position: absolute; left: 36px; right: 36px; top: ${titleLayout.authorTop}px; display: block; font-size: 34px; line-height: 1; font-weight: ${titleFontWeightCss()}; letter-spacing: 0.06em; white-space: nowrap; text-shadow: 0 5px 14px rgba(0, 0, 0, 0.96); }` : ""}
      .caption { position: absolute; left: 34px; right: 34px; bottom: 126px; z-index: 9; color: #fff; text-align: center; font-family: ${titleFontFamilyCss()}; font-weight: ${titleFontWeightCss()}; font-size: 56px; line-height: 1.16; letter-spacing: 0; opacity: 1; transform-origin: 50% 55%; will-change: transform, opacity; text-shadow: 0 7px 18px rgba(0, 0, 0, 0.96); }
      .caption span { white-space: normal; overflow-wrap: anywhere; word-break: break-word; }
      .caption .seg { opacity: 0; display: inline; }
      .caption.blk .seg { display: block; }
      .caption.small { font-size: 50px; }
      .caption.en { bottom: 60px; font-size: 30px; line-height: 1.18; font-weight: 700; letter-spacing: 0; color: #ffe9c7; opacity: 0; }
${showDisclaimer ? disclaimerCss() : ""}
    </style>
  </head>
  <body>
    <svg style="position:absolute;width:0;height:0;overflow:hidden;" aria-hidden="true" data-layout-ignore>
      <defs>
        <filter id="water-ripple" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">
          <feTurbulence id="ripple-turb" type="fractalNoise" baseFrequency="0.012 0.018" numOctaves="2" seed="5" result="noise" />
          <feDisplacementMap id="ripple-disp" in="SourceGraphic" in2="noise" scale="0" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
    </svg>
    <main id="root" data-composition-id="main" data-start="0" data-duration="${safeDuration}" data-width="720" data-height="960">
${useBridge ? `      <section class="scene bridge" data-layout-ignore><div class="photo" data-layout-ignore></div></section>` : ""}
${sceneSections}
${showBookMark ? `      <div class="book-mark" data-layout-ignore>
        <span class="book-title">${esc(titleLayout.wrappedTitle)}</span>
        <span class="book-author">${esc(brief.author)} / 著</span>
      </div>` : ""}
${showDisclaimer ? disclaimerHtml(esc) : ""}
${captionHtml}
    </main>
    <script>
      window.__timelines = window.__timelines || {};
      var tl = gsap.timeline({ paused: true, defaults: { ease: "power3.out" } });
      var sceneCount = ${sceneCount};
      var safeDur = ${safeDuration};
      var sceneStarts = [${useBridge ? "0.32" : "0"}];
      for (var k = 1; k < sceneCount; k++) sceneStarts.push(Number((safeDur * k / sceneCount).toFixed(2)));
${useBridge ? `      tl.fromTo(".s1", { opacity: 0 }, { opacity: 1, duration: 0.64, ease: "sine.inOut" }, 0.32);
      /* 退场层不再淡出：见下面 for 循环里同一条规则的解释。 */
      tl.fromTo("#ripple-disp", { attr: { scale: 0 } }, { attr: { scale: 32 }, duration: 0.34, ease: "power2.in" }, 0.32);
      tl.to("#ripple-disp", { attr: { scale: 0 }, duration: 0.38, ease: "power2.out" }, 0.66);
      tl.fromTo("#ripple-turb", { attr: { baseFrequency: "0.012 0.018" } }, { attr: { baseFrequency: "0.006 0.009" }, duration: 0.72, ease: "sine.inOut" }, 0.32);
      tl.fromTo(".bridge .photo", { scale: 1.035, x: 0, y: 0, filter: "url(#water-ripple)" }, { scale: 1.045, x: 0, y: 0, duration: 0.9, ease: "sine.inOut" }, 0);` : ""}
      for (var k = 1; k <= sceneCount; k++) {
        var startK = sceneStarts[k - 1];
        var endK = k < sceneCount ? sceneStarts[k] : safeDur;
        var panDur = Number((endK - startK + 1.2).toFixed(2));
        if (k === 1) {
          tl.fromTo(".s1 .photo", { scale: 1.035, x: 8, y: -4${useBridge ? `, filter: "url(#water-ripple)"` : ""} }, { scale: 1.105, x: -16, y: 12, duration: panDur, ease: "sine.inOut" }, 0);
        } else {
          /* 只淡入上一层，**不**淡出下一层——这是交叉溶解的唯一正确写法。
             两层叠在黑底上、各自跑同一条 sine.inOut 时，合成结果是
               out = f·上层 + (1−f)²·下层
             而不是真溶解的 f·上层 + (1−f)·下层：f=0.5 那帧少了一层 (1−f)·下层，
             中点必然**低于两端**，一次 0.72s 的溶解在逐帧均亮度上就是一条 V。
             实测（2026-10-04《人间草木》钩子段 hook.mp4）：两张底图均亮度 59.0 / 60.2，
             44.5 = 0.5×59.0 + 0.25×60.2 与量到的最低 45.1 对得上；《一个叫欧维的男人
             决定去死》68.0 / 60 那一拍预测 49.0、实测最低 49.8。掉 12~17 级、单帧只走
             3~4 级，够不上第 10 道门的闸刀线也不是眨眼，但两条腿都在 0.35s 内 ≥8 级
             ——判据④「去了又回」抓的就是它，而观众嘴里这叫「钩子那段在闪」。
             下一层保持 opacity:1 被上层逐渐盖住，合成即 f·上层 + (1−f)·下层：真溶解、
             中点不落坑、峰值不比原来更暗（.photo 是 inset:-22px 且 scale≥1.035，
             平移 ±16px 仍在画幅内，上层盖得住）。正文每个切点在 scene-map 兜底渲染器
             里走 ffmpeg xfade——同样是 f·上层 + (1−f)·下层 的凸组合，只有 f 的推进速度
             不同（xfade 线性、这里 sine.inOut）。两条渲染路径同一溶解语言。
             回归：python3 scripts/tests/test-dissolve-dip.py（接在 npm run check 里，
             用例 G 静态守住"退场层不许再淡出"）。 */
          tl.fromTo(".s" + k, { opacity: 0 }, { opacity: 1, duration: 0.72, ease: "sine.inOut" }, startK);
          tl.fromTo(".s" + k + " .photo", { scale: 1.035, x: -10, y: 6 }, { scale: 1.095, x: 14, y: -8, duration: panDur, ease: "sine.inOut" }, startK);
        }
      }
      function revealCaption(selector, start, hold) {
        tl.fromTo(selector, { opacity: 0, y: 12, scaleX: 0.92, scaleY: 0.99 }, { opacity: 1, y: 0, scaleX: 1, scaleY: 1, duration: 0.16, ease: "power3.out" }, start);
        tl.set(selector, { opacity: 0, y: -10 }, start + hold);
      }
${revealJs}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
`;

  fs.writeFileSync(path.join(outDir, "index.html"), html);
  // 取帧必须单 Chrome worker（2026-10-07）。用户四次报的「钩子视频闪现」量到的是**渲这一份
  // 本身**的脏帧：headless Chrome 的栅格化调度偶尔让某一帧只画了一半全屏 promoted `.photo`
  // 层，底带（y 834..960）从照片塌成一片平场再立刻回升——不是组成形状，不是照片，也不是
  // 片头。判据见 scripts/check-render-artifacts.py；这里修的是它的**概率**。
  // 对照实测（《人类群星闪耀时》同一份组成，各渲 6 次，脏帧计数）：
  //   A 默认 --workers=auto（10 核机 → 多个 Chrome 进程）：0,1,2,0,1,1 → 6 次里 4 次脏
  //   B --workers=1                              ：0,0,0,0,0,0        → 6 次全干净
  //   C --low-memory-mode                        ：0,0,0（但 40s/次 vs 13s/次，不采用）
  // 像素中立性由 A-4 vs B-4 证明：全帧 MAD 均值 0.00、单帧峰值 0.0、底带 0.00、帧数 160/160
  // 一致——所以这个旗子只改并发，不改画面，也不碰 2026-10-05 冻结的片头观感。
  // 注意 hyperframes render 的默认值是 --workers=auto，不写就等于 A。
  fs.writeFileSync(
    path.join(outDir, "package.json"),
    JSON.stringify(
      {
        name: `preview-${workSlug}-${packageName}`,
        private: true,
        type: "module",
        scripts: {
          check: "npx --yes hyperframes@0.7.33 lint && npx --yes hyperframes@0.7.33 validate && npx --yes hyperframes@0.7.33 inspect --at 0.8,4,8,12,18,24,30,36,42",
          render: `npx --yes hyperframes@0.7.33 render --quality standard --workers 1 --output renders/${packageName}.mp4`,
        },
      },
      null,
      2,
    ),
  );
}

cleanDir(workDir);

const brief = JSON.parse(fs.readFileSync(briefPath, "utf8"));
const rows = readCsv(scriptPath)
  .filter((row) => row.version === version)
  .sort((a, b) => Number(a.order) - Number(b.order));

if (!rows.length) {
  console.error(`No script rows found for version ${version}`);
  process.exit(1);
}
const scriptValidation = validateBodyScript(rows);
if (scriptValidation.errors.length && !process.env.ALLOW_LONG_SCRIPT) throw new Error(scriptValidation.errors.join("；"));

createIntro(brief);

// 钩子段：本期专属，画面用 images-hook/ 下的 Pexels 氛围图，时间轴读 audio/hook-timings.json。
// 三段顺序 = hook → 固定开场白（intro 模板，裁到 INTRO_TRIM_SECONDS）→ body。
// 没有 script-hook.csv 的期完全跳过这一段，render-episode-final.mjs 退回两段结构。
if (hasHook) {
  // 钩子段用它**自己那份 CSV** 的版本，不是整期正文的版本。《围城》正文是 B 版、钩子只有 A 版两行，
  // 建轴链（create-body-timings-precise.mjs / snap-body-timings-vad.py）2026-10-07 就按
  // lib/script-version.mjs 的段级规则改过了，而这个生成器还拿整期版本去筛钩子行，于是 2026-10-08
  // 11:25 它在 readCsv 之后抛「No hook script rows found for version B」直接 exit(1)：驱动读到的
  // 预览目录是空串，整期以「预览目录没生成」跳过——轴已经建好，死在出片前最后一步。
  // 规则只有一个真源，这里必须走同一个入口，不能在两处各写一份「怎么办」。
  const hookVersionChoice = resolveSegmentVersion(episodeDir, hookScriptPath, requestedVersion);
  const hookVersion = hookVersionChoice.version;
  if (hookVersionChoice.changed) {
    console.error(`[hook] 本期正文版本 ${hookVersionChoice.from} 在 ${path.basename(hookScriptPath)} 里没有行，钩子段按本段唯一的 ${hookVersion} 版出`);
  }
  const hookRows = readCsv(hookScriptPath)
    .filter((row) => row.version === hookVersion)
    .sort((a, b) => Number(a.order) - Number(b.order));
  if (!hookRows.length) {
    console.error(`No hook script rows found for version ${hookVersion} in ${hookScriptPath}`);
    process.exit(1);
  }
  const hookVoicePath = path.join(episodeDir, "audio", "hook-voiceover.mp3");
  const hookTimings = readSegmentTimings(
    path.join(episodeDir, "audio", "hook-timings.json"),
    hookVoicePath,
    hookVersion,
    "hook",
  );
  createBody(brief, hookRows, hookTimings, {
    outDir: hookDir,
    images: hookImagesDir,
    voicePath: hookVoicePath,
    coverImage: "result-bridge.png",
    coverMediaName: "00-result-bridge.png",
    useBridge: false,
    // 钩子段不压《书名》/ 作者：书名先亮出来，钩子就变成书目卡片，悬念在顶部被提前泄掉。
    showBookMark: false,
    // 常驻声明同一条理由：钩子那 5 秒是「观众还没决定要不要听」的窗口，
    // 底部先压一行来源说明，等于在第一屏就把这条视频自降成搬运内容。
    showDisclaimer: false,
    packageName: "hook",
  });
}

createBody(brief, rows, readOptionalBodyTimings(version));

console.log(workDir);
