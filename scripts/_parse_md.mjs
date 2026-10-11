// Parse the user-provided narration .md into script-body.csv (sentence mode).
//
// Goal: produce captions whose on-screen lines NEVER break a word mid-stream.
// In sentence mode each space-separated part becomes a stacked visual line, and a
// single line longer than ~11-12 CJK chars auto-wraps mid-word (font 50-56px in a
// ~652px container). So we guarantee:
//   1) every space-separated part is <= MAX_PART chars (fits one line),
//   2) breaks happen only at punctuation (now spaces) or at natural word
//      boundaries (before function words / after content words) — never mid-word,
//   3) each caption groups <= MAX_PARTS parts (<= 2 stacked lines).
// This also fixes the old behavior where a 22-char hard cut split words across
// two separate captions (e.g. "布恩迪亚家" / "族七代人的故事").
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const MD = resolve(process.argv[2] || "episodes/百年孤独/《百年孤独》.md");
const OUT = resolve(process.argv[3] || "episodes/百年孤独/script-body.csv");
const VERSION = process.argv[4] || "A";
const MAX_CAP = Number(process.argv[5] || 22); // max chars per caption (sum of parts)

const MAX_PART = 12; // max chars per visual line (fits one line at 50px in 652px)
const MAX_PARTS = 2; // max stacked lines per caption

// Break BEFORE these (function words): a natural phrase boundary.
const SPLIT_BEFORE = new Set(
  "的 了 着 过 也 而 却 就 把 被 给 让 与 和 或 对 在 从 向 为 因 所 其 这 那 我 你 他 她 它 们 等 及 并 但 若 将 已 又 还 很 更 最 不 没 别 才 都 全 各 每 任 何 此 该 本 同 相 互 自 吗 呢 吧 啊 呀 哦 嘛 么 之 以 于 则".split(/\s+/)
);
// Break AFTER these (content verbs/nouns): a natural phrase boundary.
const SPLIT_AFTER = new Set(
  "是 有 说 做 看 想 来 去 上 下 里 中 后 前 时 为 把 被 让 给 叫 成 变 走 跑 飞 生 死 爱 恨 知 懂 记 忘 开 关 进 出 回 越".split(/\s+/)
);
// Forbid splitting these common 2-char words (e.g. 彼此, 自己) across a break.
const FORBIDDEN2 = new Set(
  "彼此 自己 什么 怎么 怎样 这样 那样 这里 那里 这时 那时 这个 那个 这些 那些 我们 他们 你们 它们 她们 一个 一些 一样 一直 已经 没有 不是 就是 还是 或者 以及 于是 然而 但是 如果 虽然 因为 所以 对于 通过 由于 进行 成为 真的 觉得 认为 知道 时候 东西 地方 世界 人生 生活 生命 孤独 理解 记忆 命运 历史 故事 百年 家族 布恩迪亚 真正 开始 不断 重复 相似 现代 文明 政治 战争 爱情 关系 现实 荒诞 神秘 镜子 童年 答案 遗憾 状态 自由 选择 未来 过去 现在 曾经 永远 也许 可能 能够 应该 需要 希望 梦想 灵魂 内心 外表 外面 里面 上面 下面 前面 后面 中间 之间 之前 之后 之外 之内 身边 周围 家庭 父母 孩子 朋友 爱人 兄弟 姐妹 自然 社会 国家 城市 村庄 小镇 世纪 年代 时间 空间 死亡 出生 长大 结婚 工作 读书 写作 文学 经典 作品 作家 读者 普通 重要 珍贵 深刻 美丽 美好 幸福 痛苦 快乐 悲伤 热闹 宁静 混乱 繁华 毁灭 忘记 记住 看见 听见 感觉 感受 思考 明白 懂得 学会 改变 保持 继续 停止 结束 出现 消失 发生 存在 变成 叫做 称为 视为 作为 对待 面对 逃避 抗拒 接受 放弃 坚持 努力 奋斗 追求 渴望 向往 期待 等待 害怕 担心 喜欢 讨厌 阅读 书写 表达 传达 反映 揭示 展示 呈现 包含 包括 涉及 关联 联系 影响 决定 导致 造成 产生 形成 发展 变化 进步 退步 增长 减少 扩大 缩小 加强 减弱 提高 降低 打开 关闭 离开 到达 返回 进入 越过 穿过 越过 升起 落下 照亮 掩盖 隐藏 暴露 显露 保存 保留 遗失 丢失 寻找 发现 发明 创造 制作 建造 修建 设计 计划 准备 安排 组织 管理 控制 掌握 把握 运用 使用 利用 采用 采取 实施 执行 完成 实现 达到 获得 取得 失去 丧失 赢得 输掉 战胜 打败 克服 解决 处理 应对 回答 询问 提问 商量 讨论 辩论 争吵 谈判 合作 配合 帮助 支持 反对 拒绝 允许 禁止 命令 要求 请求 建议 提议 主张 声称 否认 承认 承认 道歉 感谢 祝贺 欢迎 送别 告别 迎接 欢送 陪伴 陪同 跟随 领导 引导 指导 教育 培养 训练 学习 研究 调查 分析 总结 概括 归纳 演绎 推理 判断 预测 估计 测量 计算 统计 比较 对照 区别 区分 分离 结合 合并 混合 配合 协调 调整 修正 改进 改善 完善 优化 提升 升级 更新 革新 改革 革命 颠覆 打破 突破 跨越 超越 领先 落后 追赶 超越 竞争 比赛 挑战 考验 锻炼 磨练 修养 修养 修养".split(/\s+/)
);

const PUNCT = /[，。；：、！？—…·（）()“”"'‘’《》\s]/g;

function stripInline(t) {
  return t.replace(/\*\*/g, "").trim();
}

function normalize(t) {
  return t.replace(/[，。；：、！？—…·（）()“”"'‘’《》]/g, " ").replace(/\s+/g, " ").trim();
}

// Find the best index to split a token into two natural parts (prefer near middle).
// Never split a known 2-char word (FORBIDDEN2).
function bestBreak(tok) {
  const chars = [...tok];
  const n = chars.length;
  let best = -1;
  let bestScore = 0;
  let bestDist = Infinity;
  const mid = n / 2;
  for (let i = 1; i < n; i++) {
    if (FORBIDDEN2.has(chars[i - 1] + chars[i])) continue; // would break a word
    const ch = chars[i];
    const prev = chars[i - 1];
    let score = 0;
    if (SPLIT_BEFORE.has(ch)) score += 2;
    if (SPLIT_AFTER.has(prev)) score += 1;
    if (score <= 0) continue;
    const dist = Math.abs(i - mid);
    if (score > bestScore || (score === bestScore && dist < bestDist)) {
      bestScore = score;
      best = i;
      bestDist = dist;
    }
  }
  if (best === -1) {
    // no ideal boundary: pick the split nearest middle that still avoids FORBIDDEN2
    for (let d = 1; d <= n; d++) {
      const a = Math.floor(mid - d);
      const b = Math.ceil(mid + d);
      if (a > 0 && a < n && !FORBIDDEN2.has(chars[a - 1] + chars[a])) return a;
      if (b > 0 && b < n && !FORBIDDEN2.has(chars[b - 1] + chars[b])) return b;
    }
    return Math.floor(n / 2);
  }
  return best;
}

// Split a single token (no internal spaces) into parts each <= MAX_PART, breaking
// only at natural word boundaries.
function splitToken(tok) {
  const chars = [...tok];
  if (chars.length <= MAX_PART) return [tok];
  const i = bestBreak(tok);
  const left = chars.slice(0, i).join("");
  const right = chars.slice(i).join("");
  return [...splitToken(left), ...splitToken(right)];
}

function splitParagraph(line) {
  const norm = normalize(line);
  if (!norm) return [];
  const tokens = norm.split(/\s+/).filter(Boolean);
  const parts = [];
  for (const tok of tokens) {
    for (const p of splitToken(tok)) parts.push(p);
  }
  return parts;
}

// Greedy group parts into captions (<= MAX_PARTS parts, <= MAX_CAP chars).
function groupParts(parts) {
  const caps = [];
  let cur = [];
  for (const p of parts) {
    const candLen = cur.length ? cur.join(" ").length + 1 + p.length : p.length;
    if (cur.length && (cur.length >= MAX_PARTS || candLen > MAX_CAP)) {
      caps.push(cur.join(" "));
      cur = [];
    }
    cur.push(p);
  }
  if (cur.length) caps.push(cur.join(" "));
  return caps;
}

const raw = readFileSync(MD, "utf8");
const lines = raw.split(/\r?\n/);

const captions = [];
let order = 0;
for (const line of lines) {
  const s = stripInline(line);
  if (!s) continue;
  if (s === "---") continue;
  const parts = splitParagraph(s);
  for (const cap of groupParts(parts)) {
    order += 1;
    captions.push({ order, text: cap });
  }
}

console.log(`Total captions: ${captions.length}`);
const over = captions.filter((c) => [...c.text.replace(/\s+/g, "")].length > MAX_CAP);
console.log(`Captions over ${MAX_CAP} chars (incl. spaces): ${over.length}`);
for (const c of over) console.log(`  [${c.order}] ${c.text}`);
// diagnostic: any visual line (space-split part) longer than MAX_PART?
const longParts = [];
for (const c of captions) {
  for (const part of c.text.split(/\s+/)) {
    if ([...part].length > MAX_PART) longParts.push({ order: c.order, part, len: [...part].length });
  }
}
console.log(`Visual lines over ${MAX_PART} chars: ${longParts.length}`);
for (const p of longParts) console.log(`  [${p.order}] (${p.len}) ${p.part}`);

const csv = ["version,order,text", ...captions.map((c) => `${VERSION},${c.order},${c.text}`)].join("\n");
writeFileSync(OUT, csv + "\n", "utf8");
console.log(`Wrote ${OUT}`);

// Verification: flag any 2-char word split across a caption boundary
// (last char of caption i + first char of caption i+1 forming a FORBIDDEN2 word).
const boundarySplits = [];
for (let i = 0; i < captions.length - 1; i++) {
  const a = [...captions[i].text.replace(/\s+/g, "")].pop();
  const b = [...captions[i + 1].text.replace(/\s+/g, "")][0];
  if (a != null && b != null && FORBIDDEN2.has(a + b)) {
    boundarySplits.push({ between: [captions[i].order, captions[i + 1].order], word: a + b, a: captions[i].text, b: captions[i + 1].text });
  }
}
console.log(`Cross-caption word splits: ${boundarySplits.length}`);
for (const s of boundarySplits) console.log(`  [${s.between[0]}] ${s.a}  ||  [${s.between[1]}] ${s.b}  (${s.word})`);
