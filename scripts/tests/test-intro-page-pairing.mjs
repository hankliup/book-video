#!/usr/bin/env node
// 片头「书名 ↔ 封面」配对判据的回归测试。
//
// 这条规则是 2026-10-05 用四期成片换来的：13:13 那次把六张滚动底板按「可见窗均亮度单调
// 下降」重排成 04/05/02/06/01/03，亮度门、闪屏门、十道交付门全绿，而 scroll-04 上印的是
// 「悲惨世界 [法]雨果」——第 1 格顶上的书名是《傲慢与偏见》。13:19 人间草木 / 13:32 解忧
// 杂货店 / 13:37 幽谷百合 / 13:42 曼斯菲尔德庄园四期成片的第一页全是错的，只有两个量化
// 门禁看不见的原因：它们问的是「这格有多亮」，没人问「这格印的是哪本书」。
//
// 所以这里两头都要按：健康产物（当前模板）跑绿，四种错法各跑红，而且「解析不到」必须
// 自己报——一条只会遍历解析结果的守卫，在 CSS 写法一变之后会「全对」。
import fs from "node:fs";
import path from "node:path";
import { checkPairing, ROOT } from "../lib/intro-page-pairing.mjs";

const HTML = fs.readFileSync(
  path.join(ROOT, "templates/shared-video-template/intro/index.html"), "utf8");
const BOOKS = JSON.parse(fs.readFileSync(
  path.join(ROOT, "templates/shared-video-template/intro/default-book-list.json"), "utf8"));

let failures = 0;
function report(name, ok, detail) {
  console.log(`  ${ok ? "✅" : "❌"} ${name}${detail ? `  ${detail}` : ""}`);
  if (!ok) failures++;
}

// 把 .bN 的底板号改写掉，其余一个字不动（对调、重排都从这里构造）。
function replate(html, mapping) {
  return html.replace(/(\.b([1-6])\s+\.page-bg\s*\{[^}]*?scroll-)(\d\d)/g,
    (m, head, slot) => head + String(mapping[Number(slot)]).padStart(2, "0"));
}

// ── 1. 健康产物必须绿 ────────────────────────────────────────────────────────
{
  const { problems, slots } = checkPairing(HTML, BOOKS);
  report("当前共享片头：六格逐格对上、结果页是 result.png",
    problems.length === 0 && slots === 6,
    `slots=${slots} problems=${JSON.stringify(problems)}`);
}

// ── 2. 对调两格必须翻红，且点名到书 ─────────────────────────────────────────
{
  const swapped = replate(HTML, { 1: 2, 2: 1, 3: 3, 4: 4, 5: 5, 6: 6 });
  const { problems } = checkPairing(swapped, BOOKS);
  const text = problems.join("\n");
  report("对调 .b1/.b2 → 两格都红", problems.length === 2, `实报 ${problems.length} 条`);
  report("  ↳ 报错写清了「书名是第 1 本、封面是第 2 本」",
    text.includes(BOOKS[0].title) && text.includes(BOOKS[1].title),
    problems[0] ? problems[0].slice(0, 46) + "…" : "");
}

// ── 3. 13:13 那次真实的重排必须整片红 ───────────────────────────────────────
{
  const as1313 = replate(HTML, { 1: 4, 2: 5, 3: 2, 4: 6, 5: 1, 6: 3 });
  const { problems } = checkPairing(as1313, BOOKS);
  report("2026-10-05 13:13 的「亮度单调」页序（04/05/02/06/01/03）→ 六格全红",
    problems.length === 6, `实报 ${problems.length} 条`);
}

// ── 4. 解析不到就必须自己报，否则「全对」是假的 ─────────────────────────────
{
  const dropped = HTML.replace(/\.b3\s+\.page-bg\s*\{[^}]*\}/, "/* 规则被 CSS 改写带走了 */");
  const { problems, slots } = checkPairing(dropped, BOOKS);
  report("删掉一条 .bN 规则 → 报「只解析到 5/6」",
    slots === 5 && problems.some((p) => p.includes("5/6")),
    `slots=${slots}｜${problems[0] ? problems[0].slice(0, 40) + "…" : "无报错（判据在空转）"}`);
}

// ── 5. 结果页指到某张滚动书封必须红 ─────────────────────────────────────────
{
  const badTarget = HTML.replace(/(\.target\s+\.page-bg\s*\{[^}]*?pages\/)result\.png/, "$1scroll-03.jpg");
  const { problems } = checkPairing(badTarget, BOOKS);
  report("结果页底板改成 scroll-03.jpg → 报「不再是 result.png」",
    problems.some((p) => p.includes("result.png")),
    problems.map((p) => p.slice(0, 30)).join(" / "));
}

// ── 6. 书单与模板格子数脱节必须红 ───────────────────────────────────────────
{
  const { problems } = checkPairing(HTML, BOOKS.slice(0, 5));
  report("default-book-list.json 少一条 → 报「5 条 vs 6 格」",
    problems.some((p) => p.includes("6 格")), `实报 ${problems.length} 条`);
}

if (failures) {
  console.log(`\n片头书名↔封面配对回归：${failures} 项不通过`);
  process.exit(1);
}
console.log("✅ 片头书名↔封面配对回归：健康跑绿，五种错法各跑红");
