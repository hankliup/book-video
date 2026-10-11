// 字幕上屏标点策略的唯一读取入口。规则本体见 subtitle-punctuation.json。
// 约定：script-body.csv / 文案.md 保留标点（TTS 语气 + 渲染器断句折行都依赖它），
// 只在「把字画上屏」那一刻调用 stripDisplayPunctuation()：行中断点号一律换成换行，
// 屏上零标点、零空白间隙（2026-09-27 起；旧口径的全角空格 U+3000 已废止）。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const SUBTITLE_PUNCTUATION = JSON.parse(
  fs.readFileSync(path.join(here, "subtitle-punctuation.json"), "utf8"),
);

for (const retired of ["displayPauseChar", "displayPause"]) {
  if (retired in SUBTITLE_PUNCTUATION) {
    throw new Error(
      `subtitle-punctuation.json 仍带着已废止的 ${retired}：断点号只能换行，不能换成空白字符`,
    );
  }
}
if (SUBTITLE_PUNCTUATION.displayBreakRender !== "line-break") {
  throw new Error('subtitle-punctuation.json: displayBreakRender 只允许 "line-break"');
}

const STRIP = new Set(SUBTITLE_PUNCTUATION.displayStrip);
const KEEP = new Set(SUBTITLE_PUNCTUATION.displayKeep);
const BREAK = new Set(SUBTITLE_PUNCTUATION.displayBreak ?? []);
const BREAK_TO = SUBTITLE_PUNCTUATION.displayBreakTo;
export const MAX_CAPTION_LINES = SUBTITLE_PUNCTUATION.displayMaxLines ?? 2;

if (BREAK_TO !== "\n") throw new Error("displayBreakTo 只能是换行：空白间隙不是节奏，是缺陷");
for (const ch of KEEP) {
  if (STRIP.has(ch)) throw new Error(`subtitle-punctuation.json: 「${ch}」同时出现在 displayStrip 与 displayKeep`);
  if (BREAK.has(ch)) throw new Error(`subtitle-punctuation.json: 「${ch}」同时出现在 displayBreak 与 displayKeep`);
}
const breakNotStripped = [...BREAK].filter((ch) => !STRIP.has(ch));
if (breakNotStripped.length) {
  throw new Error(
    `subtitle-punctuation.json: displayBreak 的标点必须同时出现在 displayStrip，否则上屏验收门禁查不到它：${breakNotStripped.join("")}`,
  );
}

/** 上屏文本：行中断点号换成换行，其余标点删除，保留 displayKeep（译名间隔号）。返回值可能含 `\n`。 */
export function stripDisplayPunctuation(text) {
  let out = "";
  for (const ch of String(text ?? "")) {
    if (BREAK.has(ch)) out += BREAK_TO;          // 断点号也在 displayStrip 里，必须先判
    else if (!STRIP.has(ch)) out += ch;
  }
  return out
    .replace(/[ \t　]*\n[ \t　]*/g, "\n")     // 换行两侧不残留空白
    .replace(/\n{2,}/g, "\n")                  // 「，，」「……」只断一次
    .replace(/^[\s\n]+|[\s\n]+$/g, "");        // 首尾断点号与空白一并消失
}

/** 一条字幕真正上屏的视觉行列表（已换行、已去标点、行内不留空白）。 */
export function displayLines(text) {
  return stripDisplayPunctuation(text)
    .split("\n")
    .map((line) => line.replace(/\s+/g, ""))
    .filter(Boolean);
}

/** 字幕轨要落的样式标记：门禁据此区分「按新规则渲的」与「历史轨」。 */
export function captionStyle() {
  return `line-break@${SUBTITLE_PUNCTUATION.displayBreakVersion ?? "0"}`;
}

/** 上屏文本里仍残留的标点（用于渲染期断言与预检告警）。 */
export function residualDisplayPunctuation(text) {
  return [...new Set(String(text ?? "").split("").filter((ch) => STRIP.has(ch)))];
}

/** 上屏一行里不该出现的空白间隙（换行是行分隔，不算间隙）。 */
export function displayGaps(text) {
  return [...new Set(String(text ?? "").split("").filter((ch) => ch !== "\n" && /\s/.test(ch)))];
}

export const DISPLAY_STRIP = STRIP;
export const DISPLAY_BREAK = BREAK;
