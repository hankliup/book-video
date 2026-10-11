// 渲染中间产物清理的**保留判定**，单独成文件是为了让 scripts/check.mjs 能 import 它
// 而不触发 cleanup-tmp.mjs 的 main()（那会真的去删东西）。
//
// 为什么值得单独拆：门禁必须能证明「cover-seq/f_0000.png 会被删」这件事，而不只是证明
// cleanup-tmp.mjs 存在。在脚本源码里 grep 一个字符串证明不了行为——文字出现过，判定未必如此。
// 让 check.mjs import 同一个函数、用同一份 KEEP_EXACT / KEEP_SUFFIX，跑真实输入，
// 判定口径就只有一份，不会两边各写一遍然后漂。和 check-image-provenance.py 与
// fetch-pexels-by-id.py 共用 crop_3x4() 是同一个理由。

import path from "node:path";

// 保留：文件名精确匹配或后缀匹配。
// caption-track.json 是字幕轴真源，check-episode-delivery.py 的 find_tmp_dir()
// 在 episodes/<书名>/ 下找不到副本时会回落到 tmp/<书名>_tpl/ 找它——删了，下次重验
// 交付时那几道字幕门禁会报「找不到字幕轨」，而不是报真正的问题。
// 其余是 book-cover / 水波 / 免责声部的参数快照，那些门禁会读。
export const KEEP_EXACT = new Set([
  "caption-track.json",
  "book-cover.json",
  "water-ripple.json",
  "disclaimer.json",
  "subtitle-punctuation.json",
]);

// 注意后缀是 **-sha 而不是 .sha**：指纹文件真名是 body.mp4.book-cover-sha，后缀带连字符。
// 写成 endsWith(".sha") 会一个都匹配不到，幂等快路径的指纹就被当中间件清掉了
// （2026-10-11 实测：31 期 61 个指纹全部误删）。
// 指纹缺失不影响正确性——下一次渲染会重算——只是要多做一遍 book-cover/disclaimer 合成。
export const KEEP_SUFFIX = ["-sha", ".sha"];

// 安全阀：这些永远不许删。_raw 是 Pexels 原图，grade-atmosphere-images.py --restore
// 靠它把已调色的成图复原成原图；删掉它就不可逆地断了溯源链的一环
//（check-image-provenance.py 查的 _raw sha256 漂移也就无从谈起）。
export const PROTECTED = new Set(["_raw"]);

export function isKeepTarget(rel) {
  // 只保留 _tpl 顶层文件。不拒绝子路径的话，cover-seq/ 下每帧一张的 PNG 会被整体留下，
  // 那正是 31 期里最没有必要保留的部分（单期 4-17 MB，总计数百 MB）。
  if (rel.includes(path.sep)) return false;
  const base = path.basename(rel);
  return KEEP_EXACT.has(base) || KEEP_SUFFIX.some((s) => base.endsWith(s));
}