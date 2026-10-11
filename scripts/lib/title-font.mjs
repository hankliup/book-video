// 正文段常驻《书名》+ 作者层的字体读取入口（JS 侧）。规则本体在 title-font.json，
// Python 侧的同一份读法是 lib/title_font.py —— 两条渲染路径都不许自己写 font-family。
//
// 为什么两份代码却要同一份数据：本机实际交付的像素走 Pillow（title.png 由
// render_scene_map_template_fallback.py 画），而 HyperFrames 预览走 Chrome 的 CSS。
// 两边各写一次字体名，早晚会分叉成「Python 用了苹方、预览用了黑体」，而那正是本仓库
// 在音频与字幕轴上反复踩过的坑（修的人说绿了、判的人说红了）。所以这里只把 JSON 的
// priority 顺序拼成 CSS 值，并且复刻 load() 的关键断言，让 JS 侧成为第二个口径的机会
// 在自己身上就断掉。
//
// 顺带修掉的旧分叉：CSS 在 .book-title/.book-author 上写 font-weight:900，face 本身
// 是 Medium ⇒ 浏览器合成假粗，而 Pillow 不会，同一份文案两条路径字重不同。
// 现在两边都取真实 face（css_font_weight），不再靠合成。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const TITLE_FONT = JSON.parse(
  fs.readFileSync(path.join(here, "title-font.json"), "utf8"),
);

if (TITLE_FONT.scope !== "body-book-mark") {
  throw new Error('title-font.json: scope 只能是 "body-book-mark"——字幕/片头/封面的字体各有裁决，'
    + "混进来就是越权改片头");
}
if (!Array.isArray(TITLE_FONT.priority) || TITLE_FONT.priority.length === 0) {
  throw new Error("title-font.json: priority 至少要有一个候选家族");
}
for (const [i, entry] of TITLE_FONT.priority.entries()) {
  if (!entry?.family || !entry?.style || !Array.isArray(entry.css_names) || !entry.css_names.length) {
    throw new Error(`title-font.json: priority[${i}] 必须同时有 family/style/css_names`);
  }
}
if (!TITLE_FONT.fallback?.family) {
  throw new Error("title-font.json: fallback.family 缺失——装不到那两家时要能明确报错，不能静默");
}
const WEIGHT = Number(TITLE_FONT.css_font_weight);
if (!(WEIGHT > 0)) {
  throw new Error("title-font.json: css_font_weight 必须是正数（见 _css_weight_comment）");
}

// 与 title_font.py 的 css_names() 同一条顺序：priority 依次、各自 css_names 依次，最后兜底。
export function titleFontNames() {
  const out = [];
  for (const entry of TITLE_FONT.priority) {
    for (const name of entry.css_names) if (!out.includes(name)) out.push(name);
  }
  for (const name of TITLE_FONT.fallback.css_names ?? []) if (!out.includes(name)) out.push(name);
  return out;
}

function quote(name) {
  return /\s|-|[^\x00-\x7F]/.test(name) && !name.startsWith('"') ? `"${name}"` : name;
}

export function titleFontFamilyCss() {
  return `${titleFontNames().map(quote).join(", ")}, sans-serif`;
}

export function titleFontWeightCss() {
  return WEIGHT;
}

// 各浏览器暴露的名字不一样（PostScript 名 / 家族名 / 中文本地名），而 Pillow 侧拿到的是
// **具体那一个 face**（title_font.resolve() 按 family+style 反查 index）。为了两条路径
// 画的是同一款，src 的顺序是 postscript 优先、家族名兜底：PostScript 名钉死唯一 face
// （fc-query 实测 PingFang SC Medium = PingFangSC-Medium），万一机器上名字不同，
// 再落回家族名让浏览器自己按 font-weight 配 face。声明只在装了该家族时生效，
// 没装的浏览器会跳过这一条、继续用 font-family 栈里的下一项。
export function titleFontFaceCss() {
  const blocks = [];
  for (const entry of TITLE_FONT.priority) {
    const seen = [];
    for (const n of [...(entry.postscript ?? []), ...(entry.css_names ?? [])]) {
      if (!seen.includes(n)) seen.push(n);
    }
    const src = seen.map((n) => `local("${n}")`).join(", ");
    blocks.push(`      @font-face { font-family: ${quote(entry.css_names[0])}; src: ${src}; }`);
  }
  return blocks.join("\n");
}
