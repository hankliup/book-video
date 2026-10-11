#!/usr/bin/env node
// 片头「书名 ↔ 封面」配对的唯一判据。
//
// 为什么要有这个文件：这条规则只能读 index.html 的 CSS 文本来判，第 11 道门
// （scripts/check-intro-is-original.py）量的是成片像素的**亮度电平**，它对配对是瞎的——
// 2026-10-05 13:13 把六张底板按「可见窗均亮度单调下降」重排成 04/05/02/06/01/03，
// 亮度门、闪屏门、十道交付门全绿，而观众看到的是《奥德赛》压在昆德拉的封面上。
//
// 为什么判据住在 lib 而不是只写在 check.mjs 里：check.mjs 在 2026-10-05 下午会先
// 在「grade-template-pages.py 还在」那一条 throw（并行会话把它加回来了），后面的守卫
// 一条都跑不到。判据抽出来之后，回归测试能当场把「对调两格必须翻红」验掉，
// 不用等 check.mjs 走到第 1200 行。
//
// 六张底图不是氛围板：scroll-04 上印着「悲惨世界 [法]雨果」。每页顶上的 .page-title
// 来自 default-book-list.json 的第 n 条，所以 DOM 第 n 格必须引用 scroll-0n。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
export const INTRO_HTML = path.join(ROOT, "templates/shared-video-template", "intro", "index.html");
export const BOOK_LIST = path.join(ROOT, "templates/shared-video-template", "intro", "default-book-list.json");

const PAIR_RE = /\.b([1-6])\s+\.page-bg\s*\{[^}]*?url\(["']media\/pages\/scroll-(\d\d)\.jpg["']\)/g;
const TARGET_RE = /\.target\s+\.page-bg\s*\{[^}]*?url\(["']media\/pages\/([^"']+)["']\)/;

/**
 * @returns {{problems: string[], slots: number}} slots=解析到的 .bN 规则数，problems 空即配对正确。
 */
export function checkPairing(html, books) {
  const problems = [];
  if (books.length !== 6) {
    problems.push(`default-book-list.json 是 ${books.length} 条，而 index.html 的滚动书单写死 6 格：`
      + "两边一改一不改，第 n 页的书名就会对上别的封面");
  }
  const found = [...html.matchAll(PAIR_RE)];
  if (found.length !== 6) {
    // 解析不到 6 条就等于这条守卫在空转，必须先红，否则下面逐格对账会「全对」。
    problems.push(`index.html 里只解析到 ${found.length}/6 条 .bN .page-bg 底板规则：`
      + "写法一变（规则换行、注释插进中间、选择器改名），配对守卫就会退化成空断言");
  }
  for (const [, slot, plate] of found) {
    const n = Number(slot);
    if (Number(plate) !== n) {
      const shown = books[n - 1] ? `《${books[n - 1].title}》` : "(书单第 n 条缺失)";
      const printed = books[Number(plate) - 1] ? `《${books[Number(plate) - 1].title}》` : "(不在书单里)";
      problems.push(`片头第 ${slot} 格顶上的书名是书单第 ${slot} 本 ${shown}，底图却换成了 scroll-${plate}`
        + `（那是 ${printed} 自己的封面）：书名与封面错配。亮度单调不是这里该管的事——`
        + "用户 2026-10-05 裁决共享片头按原样交付，抗闪屏只许落在钩子段与装配层的钩子侧");
    }
  }
  const target = html.match(TARGET_RE);
  if (!target) {
    problems.push("index.html 里解析不到 .target .page-bg 的底板：结果页的图没人对账，"
      + "指到某张滚动书封就把别的书当成本期答案");
  } else if (target[1] !== "result.png") {
    problems.push(`结果页 .target .page-bg 不再是 result.png（实测 ${target[1]}）：`
      + "它由每期 result-bridge 覆盖生成，指向 scroll-0n 就是拿别人的封面当答案");
  }
  return { problems, slots: found.length };
}

export function loadAndCheck() {
  const html = fs.readFileSync(INTRO_HTML, "utf8");
  const books = JSON.parse(fs.readFileSync(BOOK_LIST, "utf8"));
  return checkPairing(html, books);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { problems, slots } = loadAndCheck();
  for (const p of problems) console.log(`  ❌ ${p}`);
  if (problems.length === 0) {
    console.log(`✅ 片头 ${slots} 格书名与封面逐格对上（.bN ↔ scroll-0N，结果页 = result.png）`);
  }
  process.exit(problems.length ? 1 : 0);
}
