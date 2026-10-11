// 正文段常驻声明的唯一读取入口（JS 侧）。规则本体见 disclaimer.json，
// Python 侧的同一份读法是 lib/disclaimer.py——两条渲染路径都不许自己抄文案或几何。
//
// 这里只负责把同一份数字拼成 CSS 与 HTML：
//   · 字号/下边距/灰度/间距全部来自 JSON，改一次两条路径同时生效；
//   · 层必须挂在 #root 上、不参与 .scene 的淡入淡出，也不带 data-layout-* 动画，
//     否则它会跟着一层一起消失，变成「一行字幕似有似无」的新缺陷；
//   · 位置落在底部保留带之外（disclaimer.py 的 geometry() 会在 import 阶段拒收
//     压进字幕取样区/闪屏取证区的配置，这里做同样的数值断言，不让 JS 侧成为第二个口径）。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const DISCLAIMER = JSON.parse(
  fs.readFileSync(path.join(here, "disclaimer.json"), "utf8"),
);

const [CW, CH] = DISCLAIMER.canvas ?? [720, 960];
const SIZE = Math.round(DISCLAIMER.font_size_px);
const BOTTOM = Math.round(DISCLAIMER.bottom_px);
if (!Array.isArray(DISCLAIMER.parts) || DISCLAIMER.parts.length < 2) {
  throw new Error("disclaimer.json: parts 必须是 ≥2 条文案");
}
if (DISCLAIMER.segment !== "body") {
  throw new Error('disclaimer.json: segment 只能是 "body"（常驻声明属于正文段）');
}
// 与 disclaimer.py 同一条断言：墨迹框不许进保留带。
const FALLBACK_ASCENT_EM = 0.86;
const top = CH - BOTTOM - Math.round(SIZE * FALLBACK_ASCENT_EM);
const bandTop = Math.max(0, top - (DISCLAIMER.band_pad_px ?? 8));
const bandBottom = Math.min(CH, CH - BOTTOM + (DISCLAIMER.band_pad_px ?? 8));
for (const [name, item] of Object.entries(DISCLAIMER.reserved_bands ?? {})) {
  let y0 = item.y0;
  let y1 = item.y1;
  if (y0 === undefined && Array.isArray(item.rows)) {
    const scale = item.canvas_height / item.probe_rows;
    [y0, y1] = [item.rows[0] * scale, item.rows[1] * scale];
  }
  if (y0 === undefined) continue;
  if (bandTop < y1 && bandBottom > y0) {
    throw new Error(
      `disclaimer.json 的字号/下边距把常驻声明画进了保留带 ${name}` +
      `（y ${y0}..${y1}，真源 ${item.owner} 的 ${item.constant}）`,
    );
  }
}

const rgba = DISCLAIMER.color_rgba;
const [r, g, b, a] = rgba;
const sh = DISCLAIMER.shadow;
const [sr, sg, sb, sa] = sh.rgba;

export const disclaimerText = () => DISCLAIMER.parts.join("　");

/** 常驻声明的 CSS 规则（一行一元素，flex 居中，gap 用 em 与 Pillow 的 gap_px 同口径）。 */
export function disclaimerCss() {
  return [
    `      .disclaimer { position: absolute; left: 0; right: 0; bottom: ${BOTTOM}px;`,
    `        z-index: 7; display: flex; justify-content: center; align-items: baseline;`,
    `        gap: ${DISCLAIMER.gap_em}em; font-size: ${SIZE}px; line-height: 1; font-weight: ${DISCLAIMER.css_font_weight ?? 500};`,
    `        letter-spacing: 0; color: rgba(${r}, ${g}, ${b}, ${a});`,
    `        text-shadow: 0 ${sh.offset_y_px}px ${sh.blur_px}px rgba(${sr}, ${sg}, ${sb}, ${sa});`,
    `        opacity: 1; pointer-events: none; }`,
    `      .disclaimer span { white-space: nowrap; }`,
  ].join("\n");
}

/** 常驻声明的 HTML：一个不参与任何动画的 root 层。 */
export function disclaimerHtml(esc) {
  const spans = DISCLAIMER.parts.map((t) => `<span>${esc(t)}</span>`).join("\n        ");
  return `      <div class="disclaimer" data-layout-ignore>\n        ${spans}\n      </div>`;
}
