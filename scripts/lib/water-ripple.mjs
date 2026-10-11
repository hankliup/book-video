// 水波同窗常量的 JS 读取入口。规则本体见 water-ripple.json（唯一真源）。
// 约定：水波音效与可见涟漪共用这一对常数，JS 渲染路径不得再写 0.32 / 1.06。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const WATER_RIPPLE = JSON.parse(
  fs.readFileSync(path.join(here, "water-ripple.json"), "utf8"),
);

export const WATER_OFFSET_SECONDS = Number(WATER_RIPPLE.offsetSeconds);
export const WATER_DUR_SECONDS = Number(WATER_RIPPLE.durationSeconds);

if (!Number.isFinite(WATER_OFFSET_SECONDS) || WATER_OFFSET_SECONDS < 0) {
  throw new Error("water-ripple.json: offsetSeconds 必须是非负秒数");
}
if (!Number.isFinite(WATER_DUR_SECONDS) || WATER_DUR_SECONDS <= 0) {
  throw new Error("water-ripple.json: durationSeconds 必须是正秒数");
}

/** 成片时间轴上的水波起点 = 片头→正文切点 + offsetSeconds。 */
export function waterStartSeconds(introTrimSeconds) {
  return Number((introTrimSeconds + WATER_OFFSET_SECONDS).toFixed(2));
}

/** ffmpeg adelay 用的毫秒数（左右声道同值）。 */
export function waterDelayMs(introTrimSeconds) {
  const ms = Math.round(waterStartSeconds(introTrimSeconds) * 1000);
  return `${ms}|${ms}`;
}
