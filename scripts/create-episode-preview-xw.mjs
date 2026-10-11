import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { buildEstimatedCaptionTimings } from "./lib/body-timings.mjs";
import { slugifyEpisodeName } from "./lib/episode-slug.mjs";
import { resolveScriptVersion } from "./lib/script-version.mjs";
import { validateBodyScript } from "./lib/script-policy.mjs";

const ROOT = process.cwd();
const FALLBACK_CAPTION_START = 1.5;
const [episodeName, requestedVersion] = process.argv.slice(2);

if (!episodeName) {
  console.error("Usage: node scripts/create-episode-preview.mjs <episode-name> [script-version]");
  process.exit(1);
}

const episodeDir = path.join(ROOT, "episodes", episodeName);
const version = resolveScriptVersion(episodeDir, requestedVersion);
const briefPath = path.join(episodeDir, "brief.json");
const scriptPath = path.join(episodeDir, "script-body.csv");
const imagesDir = path.join(episodeDir, "images");
const audioTimingsPath = path.join(episodeDir, "audio", "body-timings.json");
const bodyVoicePath = path.join(episodeDir, "audio", "body-voiceover.mp3");

const workSlug = slugifyEpisodeName(episodeName);
const workDir = path.join(ROOT, "tmp", `preview-${workSlug}`);
const introDir = path.join(workDir, "intro");
const bodyDir = path.join(workDir, "body");
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
  const authorTop = fontSize >= 70 ? 166 : 140;
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

// 把字幕文本按标点拆成分句，去掉标点后用空格串起来。
// 配合 .caption span 的 white-space:normal + word-break，
// 浏览器会在空格（= 原来的短语边界）处自然折行，即"用空格断句"。
function wrapCaptionText(text) {
  const STRIP = /[，。？！、；：""''《》（）…—·,.?!;:'"()\s]/g;
  const clauses = [];
  let current = "";
  for (const char of Array.from(String(text || "").trim())) {
    current += char;
    if (/[，。！？；：,.!?;:]/u.test(char)) {
      clauses.push(current);
      current = "";
    }
  }
  if (current) clauses.push(current);
  const lines = clauses
    .map((clause) => clause.replace(STRIP, "").trim())
    .filter(Boolean);
  return lines.map(esc).join(" ");
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

// 把清洗后的字符数组按给定断点（去重已排序）用空格串起来。
function buildBrokenText(chars, breaks) {
  const sorted = [...breaks].sort((a, b) => a - b);
  const out = [];
  let prev = 0;
  for (const b of sorted) {
    if (b <= prev || b >= chars.length) continue;
    out.push(chars.slice(prev, b).join(""));
    out.push(" ");
    prev = b;
  }
  out.push(chars.slice(prev).join(""));
  return out.join("").trim();
}

// 清洗后文本里、每个"原标点之前"的字符下标 = 在该处之后断开的断点（自然短语边界）。
function naturalBoundaries(rawText) {
  const STRIP = /[，。？！、；：""''《》（）…—·,.?!;:'"()\s]/g;
  const chars = Array.from(rawText || "");
  const boundaries = [];
  let cleanedIdx = 0;
  for (const ch of chars) {
    if (STRIP.test(ch)) {
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
// 配合 .caption span 的 white-space:normal，空格处自然折行。无可用时间轴 / 无节奏断点时返回 null。
function rhythmBreakText(rawText, sentenceStart, sentenceEnd, silences) {
  const STRIP = /[，。？！、；：""''《》（）…—·,.?!;:'"()\s]/g;
  const chars = Array.from(String(rawText || "").replace(STRIP, ""));
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
  if (chosen.length === 0) return null; // 交给标点版 wrapCaptionText 兜底
  return buildBrokenText(chars, new Set(chosen));
}

// rhythmBreakText 的"逐行精准对齐"版：除断句文本外，还返回每个节奏段的音频子时间轴。
// 关键：断点时间不再用"匀速字符占比"插值（那假设 narrator 语速恒定、会漂），
// 而是**锚定到本句区间内真实的旁白停顿（silencedetect）的结束时刻**——
// 上一段从本句起点显示到该停顿结束，下一段从该停顿结束开始。
// 即每行字幕恰好覆盖"它被念出来的那段音频"，并一直显示到下一行开始念，精准对齐旁白节奏。
function rhythmBreakSegments(rawText, sentenceStart, sentenceEnd, silences) {
  const STRIP = /[，。？！、；：""''《》（）…—·,.?!;:'"()\s]/g;
  const chars = Array.from(String(rawText || "").replace(STRIP, ""));
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

function readOptionalBodyTimings(version) {
  const fallbackDuration = readAudioDuration(bodyVoicePath);
  if (!fs.existsSync(audioTimingsPath)) {
    if (fallbackDuration) console.warn("Missing body-timings.json; using script duration hints for captions");
    return fallbackDuration ? { duration: fallbackDuration, byOrder: new Map() } : null;
  }
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(audioTimingsPath, "utf8"));
  } catch (error) {
    console.warn(`Could not read body-timings.json; using script duration hints: ${error.message}`);
    return fallbackDuration ? { duration: fallbackDuration, byOrder: new Map() } : null;
  }
  if (raw.scriptVersion && raw.scriptVersion !== version) {
    console.warn(`Ignoring body timings for ${raw.scriptVersion}; using script duration hints for ${version}`);
    return fallbackDuration ? { duration: fallbackDuration, byOrder: new Map() } : null;
  }
  if (raw.alignment?.requiresAgentReview) {
    console.warn(
      `Body timings need Agent review (${raw.alignment.method || "unknown method"}): `
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

function createBody(brief, rows, audioTimings) {
  const displayTitle = getDisplayTitle(brief);
  const titleLayout = getTitleLayout(displayTitle);
  fs.mkdirSync(path.join(bodyDir, "media"), { recursive: true });
  copyFile(path.join(ROOT, "templates", "shared-video-template", "gsap.min.js"), path.join(bodyDir, "gsap.min.js"));
  copyFile(path.join(imagesDir, "result-bridge.png"), path.join(bodyDir, "media", "00-result-bridge.png"));
  // 动态检测 atmosphere-*.png（支持长视频多图），按数字排序后按序铺设场景
  const atmosphereFiles = fs
    .readdirSync(imagesDir)
    .filter((name) => /^atmosphere-\d+\.png$/u.test(name))
    .sort((a, b) => Number(a.match(/atmosphere-(\d+)/u)[1]) - Number(b.match(/atmosphere-(\d+)/u)[1]));
  if (atmosphereFiles.length === 0) throw new Error(`No atmosphere-*.png found in ${imagesDir}`);
  atmosphereFiles.forEach((name, index) => {
    copyFile(path.join(imagesDir, name), path.join(bodyDir, "media", `${String(index + 1).padStart(2, "0")}-atmosphere.png`));
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
  const STRIP_PUNCT = /[，。？！、；：""''《》（）…—·,.?!;:'"()\s]/g;
  // 整段旁白静音检测，用于按旁白节奏断句 + 逐行精准对齐。
  const silenceList = detectSilences(bodyVoicePath, 0.18);
  function pushEnglishCaption(row, order, rowStart, rowEnd) {
    if (!row.en) return;
    const enStart = Number(Math.max(0, rowStart).toFixed(2));
    const enEnd = Number(Math.max(enStart + 0.3, rowEnd).toFixed(2));
    captionRows.push(`      <div class="caption en cen${order}">${esc(String(row.en))}</div>`);
    revealItems.push({ selector: `.cen${order}`, start: enStart, hold: Number((enEnd - enStart).toFixed(2)) });
  }
  // 为每一行字幕构建"逐短语"分段：每个分段携带自己真实的旁白子时间轴，
  // 让它恰好在对应短语被念出来的那一刻才亮起（精准对齐，而非整句在句首一次性全亮）。
  const captionRows = [];
  const revealItems = [];
  const sentenceMode = audioTimings?.display === "sentence";
  rows.forEach((row, index) => {
    const order = Number(row.order);
    const rawText = String(row.text || "");
    const cleanLen = rawText.replace(STRIP_PUNCT, "").length;
    const small = cleanLen >= 9 ? " small" : "";
    const audioTiming = audioTimings?.byOrder?.get(order);
    const hasTiming = audioTiming && Number.isFinite(audioTiming.start) && Number.isFinite(audioTiming.end);
    const rowTiming = speechTimings[index];
    const rowStart = rowTiming ? rowTiming.start : (hasTiming ? Math.max(0, audioTiming.start) : 0.72);
    const rowEnd = rowTiming ? rowTiming.end : (hasTiming ? Math.max(rowStart + 0.3, audioTiming.end) : rowStart + 2);

    // —— 整句同屏模式：整句在句首一次性亮起、句尾一次性熄灭（一句一句显示，不逐短语）——
    if (sentenceMode) {
      const sentStart = Number(Math.max(0, rowStart).toFixed(2));
      const sentEnd = Number(Math.max(sentStart + 0.3, rowEnd).toFixed(2));
      // 仅去标点、保留原文空格，使整句按原短语边界自然折行（不被从词中劈开）。
      const cleaned = String(rawText).replace(/[，。？！、；：""''《》（）…—·,.?!;:'"()]/g, "").trim();
      const parts = cleaned.split(/\s+/).map((s) => s.trim()).filter(Boolean);
      const lineParts = parts.length ? parts : [cleaned];
      const lineSpans = lineParts
        .map((text, i) => `        <span class="seg seg${i}">${esc(text)}</span>`)
        .join("\n");
      captionRows.push(`      <div class="caption c${order}${small} blk">\n${lineSpans}\n      </div>`);
      lineParts.forEach((_, i) => {
        revealItems.push({
          selector: `.c${order} .seg${i}`,
          start: sentStart,
          hold: Number((sentEnd - sentStart).toFixed(2)),
        });
      });
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
        // 按空格切成视觉行；整句所有行同时显示。
        const lineTexts = wordList
          .map((item) => item.ch)
          .join("")
          .split(" ")
          .map((s) => s.trim())
          .filter(Boolean);
        const lines = lineTexts.length > 0 ? lineTexts : [rawText.replace(STRIP_PUNCT, "")];
        const lineSpans = lines
          .map((text, i) => `        <span class="seg seg${i}">${esc(text)}</span>`)
          .join("\n");
        captionRows.push(`      <div class="caption c${order}${small} blk">\n${lineSpans}\n      </div>`);
        lines.forEach((_, i) => {
          revealItems.push({
            selector: `.c${order} .seg${i}`,
            start: sentStart,
            hold: Number((sentEnd - sentStart).toFixed(2)),
          });
        });
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
        ? (rhythmBreakText(rawText, audioTiming.start, audioTiming.end, silenceList) || wrapCaptionText(rawText))
        : wrapCaptionText(rawText);
      segs = [{ text: display, start: rowStart, end: rowEnd }];
    }
    const segSpans = segs
      .map((seg, i) => `        <span class="seg seg${i}">${esc(seg.text)}</span>`)
      .join("\n");
    captionRows.push(`      <div class="caption c${order}${small} blk">\n${segSpans}\n      </div>`);
    segs.forEach((seg, i) => {
      const start = Number(Math.max(0, seg.start).toFixed(2));
      const end = Number(Math.max(start + 0.16, seg.end).toFixed(2));
      revealItems.push({
        selector: `.c${order} .seg${i}`,
        start,
        hold: Number((end - start).toFixed(2)),
      });
    });
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
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * { box-sizing: border-box; }
      @font-face { font-family: "DeYiHei"; src: local("DeYiHei"), local("DeYi Hei"), local("德意黑"); }
      @font-face { font-family: "德意黑"; src: local("德意黑"), local("DeYiHei"), local("DeYi Hei"); }
      @font-face { font-family: "STHeiti"; src: local("STHeiti"), local("STHeitiSC-Medium"); }
      @font-face { font-family: "Hiragino Sans GB"; src: local("Hiragino Sans GB"); }
      html, body { width: 720px; height: 960px; margin: 0; overflow: hidden; background: #000; }
      body { font-family: "DeYiHei", "德意黑", "STHeiti", "Hiragino Sans GB", sans-serif; color: #fff; }
      #root { position: relative; width: 720px; height: 960px; overflow: hidden; background: #000; }
      /* z-index 必须显式：.photo 是 z-index:1，.scene 不写 z-index 时只有 opacity<1 才是层叠上下文，
         于是「只淡入上一层」会被下一层的 .photo 整段盖住，直到 opacity==1 才一帧硬切。见主生成器
         create-episode-preview.mjs 里同一条规则的完整解释 + scripts/tests/test-dissolve-dip.py。 */
      .scene { position: absolute; inset: 0; opacity: 0; overflow: hidden; z-index: 1; }
      .scene:first-of-type { opacity: 1; }
      .photo { position: absolute; inset: -22px; z-index: 1; background-size: cover; background-position: center; background-repeat: no-repeat; transform-origin: 50% 50%; will-change: transform; }
      .bridge .photo { inset: 0; background-image: url("media/00-result-bridge.png"); }
${sceneCss}
      .book-mark { position: absolute; inset: 0; z-index: 8; text-align: center; color: #fff; opacity: 1; transform-origin: 50% 120px; }
      .book-title { position: absolute; left: 28px; right: 28px; top: 70px; display: block; font-size: ${titleLayout.fontSize}px; line-height: 1; font-weight: 900; letter-spacing: 0.04em; white-space: nowrap; text-shadow: 0 7px 18px rgba(0, 0, 0, 0.96); }
      .book-author { position: absolute; left: 36px; right: 36px; top: ${titleLayout.authorTop}px; display: block; font-size: 34px; line-height: 1; font-weight: 900; letter-spacing: 0.06em; white-space: nowrap; text-shadow: 0 5px 14px rgba(0, 0, 0, 0.96); }
      .caption { position: absolute; left: 34px; right: 34px; bottom: 126px; z-index: 9; color: #fff; text-align: center; font-size: 56px; line-height: 1.16; font-weight: 900; letter-spacing: 0; opacity: 1; transform-origin: 50% 55%; will-change: transform, opacity; text-shadow: 0 7px 18px rgba(0, 0, 0, 0.96); }
      .caption span { white-space: normal; overflow-wrap: anywhere; word-break: break-word; }
      .caption .seg { opacity: 0; display: inline; }
      .caption.blk .seg { display: block; }
      .caption.small { font-size: 50px; }
      .caption.en { bottom: 60px; font-size: 30px; line-height: 1.18; font-weight: 700; letter-spacing: 0; color: #ffe9c7; opacity: 0; }
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
      <section class="scene bridge" data-layout-ignore><div class="photo" data-layout-ignore></div></section>
${sceneSections}
      <div class="book-mark" data-layout-ignore>
        <span class="book-title">${esc(titleLayout.wrappedTitle)}</span>
        <span class="book-author">${esc(brief.author)} / 著</span>
      </div>
${captionHtml}
    </main>
    <script>
      window.__timelines = window.__timelines || {};
      var tl = gsap.timeline({ paused: true, defaults: { ease: "power3.out" } });
      var sceneCount = ${sceneCount};
      var safeDur = ${safeDuration};
      var sceneStarts = [0.32];
      for (var k = 1; k < sceneCount; k++) sceneStarts.push(Number((safeDur * k / sceneCount).toFixed(2)));
      tl.fromTo(".s1", { opacity: 0 }, { opacity: 1, duration: 0.64, ease: "sine.inOut" }, 0.32);
      /* 同上：退场的 .bridge 保持 opacity:1，由 .s1 逐渐盖住。 */
      tl.fromTo("#ripple-disp", { attr: { scale: 0 } }, { attr: { scale: 32 }, duration: 0.34, ease: "power2.in" }, 0.32);
      tl.to("#ripple-disp", { attr: { scale: 0 }, duration: 0.38, ease: "power2.out" }, 0.66);
      tl.fromTo("#ripple-turb", { attr: { baseFrequency: "0.012 0.018" } }, { attr: { baseFrequency: "0.006 0.009" }, duration: 0.72, ease: "sine.inOut" }, 0.32);
      tl.fromTo(".bridge .photo", { scale: 1.035, x: 0, y: 0, filter: "url(#water-ripple)" }, { scale: 1.045, x: 0, y: 0, duration: 0.9, ease: "sine.inOut" }, 0);
      for (var k = 1; k <= sceneCount; k++) {
        var startK = sceneStarts[k - 1];
        var endK = k < sceneCount ? sceneStarts[k] : safeDur;
        var panDur = Number((endK - startK + 1.2).toFixed(2));
        if (k === 1) {
          tl.fromTo(".s1 .photo", { scale: 1.035, x: 8, y: -4, filter: "url(#water-ripple)" }, { scale: 1.105, x: -16, y: 12, duration: panDur, ease: "sine.inOut" }, 0);
        } else {
          tl.fromTo(".s" + k, { opacity: 0 }, { opacity: 1, duration: 0.72, ease: "sine.inOut" }, startK);
          /* 退场层不淡出：黑底上两层同时反向淡入淡出的中点是 f·上 + (1−f)²·下，
             比真溶解少 25%·下层 —— 一次会「去了又回」的下探。
             机理、实测对照与回归见 scripts/create-episode-preview.mjs 同名处
             和 scripts/tests/test-dissolve-dip.py。 */
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

  fs.writeFileSync(path.join(bodyDir, "index.html"), html);
  fs.writeFileSync(
    path.join(bodyDir, "package.json"),
    JSON.stringify(
      {
        name: `preview-${workSlug}-body`,
        private: true,
        type: "module",
        scripts: {
          check: "npx --yes hyperframes@0.7.33 lint && npx --yes hyperframes@0.7.33 validate && npx --yes hyperframes@0.7.33 inspect --at 0.8,4,8,12,18,24,30,36,42",
          // --workers 1：取帧用单 Chrome worker，消除多 worker 栅格化竞态造成的单帧合成伪影。
          // 实测与理由见 scripts/create-episode-preview.mjs 同名位置（2026-10-07），check.mjs
          // 扫每一份 create-episode-preview*.mjs，本 fork 漏掉就红。
          render: "npx --yes hyperframes@0.7.33 render --quality standard --workers 1 --output renders/body.mp4",
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
createBody(brief, rows, readOptionalBodyTimings(version));

console.log(workDir);
