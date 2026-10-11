#!/usr/bin/env node
// 渲染中间产物的收尾清理。
//
// 为什么需要它：一次正文渲染会在 tmp/<书名>_tpl/ 落下 body.mp4、body-bg.mp4、sub.mov、
// cover-seq/ 逐帧 PNG 和上百张 cap-*.png，单期 180-585 MB（2026-10-11 实测 31 期合计 10.8 GB）。
// 这些全部可由 episodes/<书名>/ 的 caption-track.json + images-body/ 重建——脚本里注释写了
// 「渲染中间目录（放 cap-*.png / body-bg.mp4 / caption-track.json）」，也就是说它本来就是暂存区，
// 不是产物。可它从来没有回收路径：render-hook-batch.sh 里唯一的 rm -rf 指向渲染锁目录，
// AGENTS.md 全文搜「清理」零命中，Delivery 阶段只要求把成片贴进对话。31 期各留一个 570 MB
// 的暂存区，等于把「某期现在处于什么阶段」这件事只交给目录存在性去表达——而 check-image-provenance
// 早就证伪过「文件存在即正确」这个假设（manifest 要 A、磁盘上是 B，八道门全绿）。
//
// 保留什么、为什么：
//   caption-track.json  —— 字幕轴真源。check-episode-delivery.py 找不到 episodes/<书名>/ 下的
//                          副本时，就回落到 tmp/<书名>_tpl/ 找它；删掉会让重验交付时那几道
//                          字幕门禁直接报「找不到字幕轨」而不是报真问题。
//   *.sha              —— body.mp4 的 book-cover / disclaimer 合成指纹。渲染器靠它判断
//                          「背景没变、封面没变，这一层不用重渲」。删掉只是多做一次渲染，
//                          保留它才能让重跑命中幂等快路径。
//   book-cover.json 等 config —— 质检与水波同窗规则的参数快照，check-episode-delivery.py 会读。
//
// 删除的：所有媒体中间件（mp4 / mov）、逐帧序列（cover-seq/）、字幕位图（cap-*.png）、
// _qc/ 质检截图目录。这些没有任何下游消费者——grep 全仓，只有渲染器自己写、只有自己的
// 下一次运行会重写。

import fs from "node:fs";
import path from "node:path";
// 保留判定住在 scripts/lib/cleanup-keep.mjs，门禁 import 的是同一份实现。
// 别在这里再抄一遍常量：两边各写一份，判定口径迟早漂，而漂了的一切照绿。
import { isKeepTarget, PROTECTED } from "./lib/cleanup-keep.mjs";

const ROOT = process.cwd();

// 保留清单与判定实现见 scripts/lib/cleanup-keep.mjs（门禁共用同一份）。

function parseArgs(argv) {
  const opts = { apply: false, only: null, keepUndelivered: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--apply") opts.apply = true;
    else if (a === "--keep-undelivered") opts.keepUndelivered = true;
    else if (a === "--all") opts.keepUndelivered = false;
    else if (a.startsWith("--only=")) opts.only = a.slice("--only=".length);
    else if (a === "--only") opts.only = argv[++i];
  }
  return opts;
}

function episodeNameOf(tplDir) {
  return path.basename(tplDir).replace(/_tpl$/, "");
}

function hasDeliveredFinal(name) {
  const renders = path.join(ROOT, "episodes", name, "renders");
  if (!fs.existsSync(renders)) return null;
  const final = path.join(renders, `${name}-final.mp4`);
  return fs.existsSync(final) ? final : null;
}

// 保留判定见 ./lib/cleanup-keep.mjs

function dirSizeMb(dir) {
  let total = 0;
  const walk = (d) => {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, entry.name);
      if (entry.isDirectory()) walk(p);
      else {
        try { total += fs.statSync(p).size; } catch { /* 竞态删除，跳过 */ }
      }
    }
  };
  walk(dir);
  return total;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const tmpRoot = path.join(ROOT, "tmp");
  if (!fs.existsSync(tmpRoot)) {
    console.log("tmp/ 不存在，无需清理。");
    return;
  }

  const dirs = fs.readdirSync(tmpRoot, { withFileTypes: true })
    .filter((e) => e.isDirectory() && e.name.endsWith("_tpl"))
    .map((e) => path.join(tmpRoot, e.name))
    .filter((d) => !opts.only || episodeNameOf(d) === opts.only)
    .sort();

  if (!dirs.length) {
    console.log(opts.only ? `没有匹配的中间目录：${opts.only}_tpl` : "没有 *_tpl 中间目录。");
    return;
  }

  let freedBytes = 0, keptBytes = 0;
  const undelivered = [];
  const rows = [];

  for (const dir of dirs) {
    const name = episodeNameOf(dir);
    const final = hasDeliveredFinal(name);
    if (!final && opts.keepUndelivered) {
      undelivered.push(name);
      continue;
    }

    const removals = [];
    const keeps = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (PROTECTED.has(entry.name)) continue;
      if (!isKeepTarget(entry.name)) removals.push(entry.name);
      else keeps.push(entry.name);
    }

    const before = dirSizeMb(dir);
    rows.push({ name, before, removals, keeps, final, applied: false });

    if (opts.apply) {
      for (const target of removals) {
        fs.rmSync(path.join(dir, target), { recursive: true, force: true });
      }
      rows[rows.length - 1].applied = true;
      const after = dirSizeMb(dir);
      freedBytes += before - after;
      keptBytes += after;
    } else {
      keptBytes += before;
    }
  }

  const mb = (b) => `${(b / 1024 / 1024).toFixed(1)} MB`;

  for (const r of rows) {
    const tag = r.final ? "成片已交付" : "⚠ 无成片";
    console.log(`  ${r.name.padEnd(16)} ${mb(r.before).padStart(11)}  删除 ${String(r.removals.length).padStart(3)} 项  保留 ${String(r.keeps.length).padStart(2)} 项  (${tag})`);
  }

  if (undelivered.length) {
    console.log(`\n跳过 ${undelivered.length} 期未交付成片的中间目录（--all 可覆盖）：`);
    for (const n of undelivered) console.log(`  ${n}`);
  }

  console.log(`\n${opts.apply ? "已清理" : "干跑（加 --apply 生效）"}：${opts.apply ? mb(freedBytes) : mb(keptBytes)} 可释放 / 保留 ${mb(keptBytes)}`);
  if (!opts.apply && freedBytes === 0) {
    const total = rows.reduce((s, r) => s + r.before, 0);
    console.log(`预计释放：${mb(total)}`);
  }
}

main();