import fs from 'fs';
import path from 'path';
import { spawnSync } from 'node:child_process';

const episodeDir = process.argv[2];
const version = process.argv[3] || '1';

// 换配音后 body-timings.json 的 duration 仍是旧音频的（create-body-timings 才会写它，
// 而重跑它会把修好的分段 ASR 覆盖掉）。这里直接用 ffprobe 读真实时长并同步，
// 否则末行 end 会超出音频、渲染末尾出现黑场。
function probeDuration(audioPath) {
  const result = spawnSync(
    'ffprobe',
    ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audioPath],
    { encoding: 'utf8' },
  );
  if (result.status !== 0) return null;
  const value = Number(String(result.stdout || '').trim());
  return Number.isFinite(value) && value > 0 ? Number(value.toFixed(2)) : null;
}

if (!episodeDir) {
  console.error('Usage: node backfill_sentence.mjs <episodeDir> [version]');
  process.exit(1);
}

const wordTimingsPath = path.join(episodeDir, 'audio', 'word-timings.json');
const bodyTimingsPath = path.join(episodeDir, 'audio', 'body-timings.json');

if (!fs.existsSync(wordTimingsPath)) {
  console.error(`Word timings not found: ${wordTimingsPath}`);
  process.exit(1);
}
if (!fs.existsSync(bodyTimingsPath)) {
  console.error(`Body timings not found: ${bodyTimingsPath}`);
  process.exit(1);
}

const wordTimings = JSON.parse(fs.readFileSync(wordTimingsPath, 'utf8'));
const bodyTimings = JSON.parse(fs.readFileSync(bodyTimingsPath, 'utf8'));

const byOrder = wordTimings.byOrder || {};
const newCaptions = [];

// 假设 script.csv 的 order 从 1 开始连续
let maxOrder = 0;
for (const key in byOrder) {
  const orderNum = parseInt(key);
  if (!isNaN(orderNum) && orderNum > maxOrder) maxOrder = orderNum;
}

for (let order = 1; order <= maxOrder; order++) {
  const wordList = byOrder[order] || [];
  if (wordList.length === 0) {
    // 没有词，保持原来的时间（如果有）或跳过
    const old = bodyTimings.captions.find(c => c.order === order);
    if (old) {
      newCaptions.push({ order, start: old.start, end: old.end });
    }
    continue;
  }
  // 过滤掉空格字符
  const nonSpace = wordList.filter(w => w.ch !== ' ');
  if (nonSpace.length === 0) {
    const old = bodyTimings.captions.find(c => c.order === order);
    if (old) {
      newCaptions.push({ order, start: old.start, end: old.end });
    }
    continue;
  }
  const firstChar = nonSpace[0];
  const lastChar = nonSpace[nonSpace.length - 1];
  newCaptions.push({
    order,
    start: firstChar.start,
    end: lastChar.end
  });
}

// 校验单调和无重叠
for (let i = 0; i < newCaptions.length; i++) {
  if (newCaptions[i].start < 0) newCaptions[i].start = 0;
  if (i > 0 && newCaptions[i].start < newCaptions[i-1].end) {
    // 有重叠，调整为前一个的 end（或者可以采用其他策略）
    newCaptions[i].start = newCaptions[i-1].end;
  }
  if (newCaptions[i].end <= newCaptions[i].start) {
    newCaptions[i].end = newCaptions[i].start + 0.1; // 最小持续时间
  }
}
// 确保末行不超过总时长（总时长以真实音频为准）
const audioPath = path.join(episodeDir, 'audio', 'body-voiceover.mp3');
const probed = fs.existsSync(audioPath) ? probeDuration(audioPath) : null;
const duration = probed ?? bodyTimings.duration;
if (probed != null && Math.abs(probed - Number(bodyTimings.duration || 0)) > 0.5) {
  console.log(`  duration 同步：${bodyTimings.duration} -> ${probed}（按真实音频时长）`);
  bodyTimings.duration = probed;
}
if (newCaptions.length > 0) {
  const last = newCaptions[newCaptions.length - 1];
  if (last.end > duration) {
    last.end = Number(duration.toFixed(2));
  }
}

// 更新 bodyTimings
bodyTimings.captions = newCaptions;
bodyTimings.captionDisplay = 'sentence';

fs.writeFileSync(bodyTimingsPath, JSON.stringify(bodyTimings, null, 2));
console.log(`Updated ${bodyTimingsPath} with sentence mode captions.`);