export const MAX_BODY_SCRIPT_LINES = 200;
export const MAX_BODY_SCRIPT_CHARS = 10000;

// 口播语速（字/秒）。不是拍脑袋的数：取 episodes/待上传/ 下 99 期已交付成片，
// 用 ffprobe 量到的成片时长减去 3.0s 片头，再除以该期 script-body.csv 的正文字数。
// 中位 4.69，p10-p90 为 4.42-5.05，最慢 4.08，最快 5.90（围城，异常快）。
// 字数 → 时长的换算只能引用这一个常数，否则「4-5 分钟」会在不同会话里算出不同的字数。
export const NARRATION_RATE_CPS = 4.69;
export const INTRO_TRIM_SECONDS = 3.0;

// 两档交付口径。默认是 long：99 期已交付成片里有 98 期落在 797 字以上，
// 中位 1220 字（约 4.3 分钟），house style 就是长版；short 是少数例外。
// 写稿门禁一度把「18-20 行」当目标读，于是给一本体量足够的书也写出了 211 字（2026-09-29 穆斯林的葬礼初稿），
// 于是把下限当目标用。short 只在用户明确要短版时用。
export const SCRIPT_LENGTH_PRESETS = {
  short: { id: "short", minChars: 180, maxChars: 300, minLines: 18, maxLines: 22, seconds: [55, 70] },
  long: { id: "long", minChars: 1100, maxChars: 1450, minLines: 60, minLinesMax: MAX_BODY_SCRIPT_LINES, seconds: [240, 300] },
};
export const DEFAULT_SCRIPT_PRESET = "long";

// 分类档与「口径」不是一回事：medium / extended 都在 99 期已交付成片里真实存在
// （800-1050 字的有 33 期，1500 字以上的有 6 期），所以它们只出 note，不判失败。
export function classifyScriptLength(chars) {
  if (chars < SCRIPT_LENGTH_PRESETS.short.minChars) return "underflow";
  if (chars <= SCRIPT_LENGTH_PRESETS.short.maxChars) return "short";
  if (chars < SCRIPT_LENGTH_PRESETS.long.minChars) return "medium";
  if (chars <= SCRIPT_LENGTH_PRESETS.long.maxChars) return "long";
  return "extended";
}

export function estimateNarrationSeconds(chars, rate = NARRATION_RATE_CPS) {
  return Math.round((chars / rate) * 10) / 10;
}

function minutes(seconds) {
  return `${Math.round((seconds / 60) * 10) / 10} 分钟`;
}

function presetRange(id) {
  const p = SCRIPT_LENGTH_PRESETS[id];
  return `${p.minChars}-${p.maxChars} 字（${minutes(p.seconds[0])}-${minutes(p.seconds[1])}）`;
}

// 长度判定只对「两种口径都不沾」的稿子动真格：短于 short 下限才算稿子坏了。
// short / medium / long / extended 一律只出 note，避免把 33 期 800-1050 字的历史成片判红。
export function checkScriptLength(chars, lines, expectedPreset = DEFAULT_SCRIPT_PRESET) {
  const band = classifyScriptLength(chars);
  const seconds = estimateNarrationSeconds(chars);
  const notes = [];
  const errors = [];
  const fallback = SCRIPT_LENGTH_PRESETS[DEFAULT_SCRIPT_PRESET];
  if (band === "underflow") {
    errors.push(
      `正文 ${chars} 字，连短版下限（${SCRIPT_LENGTH_PRESETS.short.minChars} 字）都不到，约 ${seconds}s，读不成一期视频。` +
        `默认长版口径是 ${fallback.minChars}-${fallback.maxChars} 字。`,
    );
  }
  if (band === "short") {
    notes.push(
      `正文 ${chars} 字 / ${lines} 行，属于短版（约 ${seconds}s）。默认口径是长版 ${presetRange(DEFAULT_SCRIPT_PRESET)}，` +
        `按 ${NARRATION_RATE_CPS} 字/秒换算。确认本期就要短版，加 --short 放行。`,
    );
  }
  if (band === "medium") {
    notes.push(`正文 ${chars} 字 / ${lines} 行，落在短版与长版之间，约 ${minutes(seconds)}。默认口径是长版 ${presetRange(DEFAULT_SCRIPT_PRESET)}。`);
  }
  if (band === "extended") {
    notes.push(`正文 ${chars} 字 / ${lines} 行，约 ${minutes(seconds)}，已超过长版上限 ${fallback.maxChars} 字。`);
  }
  if (band === "long") {
    notes.push(`正文 ${chars} 字 / ${lines} 行，长版口径，约 ${minutes(seconds)}。`);
  }
  if (expectedPreset === "short" && band !== "short") {
    notes.push(`按 --short 校验，但正文 ${chars} 字不在短版区间 ${presetRange("short")}。`);
  }
  return { band, expectedPreset, estimatedSeconds: seconds, estimatedMinutes: Math.round((seconds / 60) * 10) / 10, errors, notes };
}

// 字幕正文禁止的装饰性符号（书名号/引号/破折号/括号）。
// 普通标点 ，。？！；、：… 与译名间隔号 · 不在其中：渲染器按标点断句，去掉会变成无法断句的长串。
// 规则放在 warnings 而非 errors：写稿门禁 validate-script.mjs 会把 warnings 升级为 errors，
// 而 create-body-timings / create-episode-preview 等下游工具只看 errors，
// 便于历史期（CSV 仍带符号）在补做时间轴或预览时不被卡住。
const DECORATIVE_CHARS = ["《", "》", "“", "”", "‘", "’", "「", "」", "『", "』", "【", "】", "（", "）", "—", "–", "―"];

export function findDecorativeSymbols(rows) {
  const hits = [];
  for (const row of rows) {
    const chars = [...new Set(Array.from(String(row.text || "")).filter((ch) => DECORATIVE_CHARS.includes(ch)))];
    if (chars.length) hits.push({ order: row.order, chars: chars.join("") });
  }
  return hits;
}

export function validateBodyScript(rows, options = {}) {
  const lines = rows.length;
  const chars = Array.from(rows.map((row) => String(row.text || "")).join("")).length;
  const errors = [];
  const warnings = [];
  if (lines > MAX_BODY_SCRIPT_LINES) errors.push(`正文最多 ${MAX_BODY_SCRIPT_LINES} 行，当前 ${lines} 行`);
  if (chars > MAX_BODY_SCRIPT_CHARS) errors.push(`正文最多 ${MAX_BODY_SCRIPT_CHARS} 个汉字，当前 ${chars} 个字符`);
  const length = checkScriptLength(chars, lines, options.expectedPreset || DEFAULT_SCRIPT_PRESET);
  errors.push(...length.errors);
  if (!options.allowDecorativeSymbols) {
    const hits = findDecorativeSymbols(rows);
    if (hits.length) {
      const detail = hits.slice(0, 8).map((hit) => `第 ${hit.order} 行「${hit.chars}」`).join("、");
      warnings.push(
        `字幕正文不能含装饰性特殊符号（书名号/引号/破折号/括号）：${detail}${hits.length > 8 ? ` 等 ${hits.length} 行` : ""}。` +
          `先跑 python3 scripts/strip-subtitle-symbols.py "<书名>" --apply（只改符号不改字，保留 ，。？！；、：…）`,
      );
    }
  }
  return { lines, chars, lengthBand: length.band, expectedPreset: length.expectedPreset, estimatedSeconds: length.estimatedSeconds, estimatedMinutes: length.estimatedMinutes, errors, warnings, notes: length.notes };
}
