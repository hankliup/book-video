#!/usr/bin/env node
// Precise sentence-granularity body timings for a long-version episode.
//
// Anchors every script row to real speech onsets in the voiceover via a
// gap-aware LCS alignment of the Whisper transcript onto script-body.csv
// (subtitle truth):
//   - ASR *token* stream (absolute wall-clock ms, monotonic) is the time
//     source; one token may span several characters ("知道");
//   - a global LCS between clean script chars and the token stream matches
//     each script char to the onset of the token that spoke it;
//   - gap onsets (token onsets ≥ 0.4s after the previous token) are
//     forbidden match targets, so a sentence starting after a narration
//     pause can only match the resumption point, never the silence;
//   - row start = start of the row's first matched non-gap token;
//   - row end   = end of the row's last matched token.
// Captions therefore appear exactly when their sentence starts being spoken
// and clear right before the next sentence starts.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { resolveScriptVersion, resolveSegmentVersion } from "./lib/script-version.mjs";

const ROOT = process.cwd();
const positionals = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const [episodeName, requestedVersion, voicePathArg] = positionals;
if (!episodeName) {
  console.error("Usage: node scripts/create-body-timings-precise.mjs <episode-name> [version] [voiceover-path] [--segment=hook]");
  process.exit(1);
}

// 三段结构的钩子段要走同一条建轴链（token-LCS + gap-aware），只是文案/ASR/产物换一份：
//   正文  script-body.csv        asr/body.json        body-timings.json
//   钩子  script-hook.csv       asr/hook.json        hook-timings.json
// 不加 --segment 时行为与历史完全一致。
const segment = (process.argv.find((a) => a.startsWith("--segment=")) || "").split("=")[1] || "body";
if (!["body", "hook"].includes(segment)) {
  console.error(`--segment 只支持 body 或 hook，收到：${segment}`);
  process.exit(1);
}
const isHook = segment === "hook";

// 三段结构下钩子文案是 script-hook.csv、正文是 script-body.csv，历史单段期只有 script-body.csv。
// 语义化命名与 hook-voiceover.mp3 / body-voiceover.mp3、hook-timings.json / body-timings.json 对齐。
// 段内先找语义化文件名，找不到再回落历史 script-body.csv。
function resolveScriptFile() {
  const candidates = isHook
    ? ["script-hook.csv", "script-body.csv"]
    : ["script-body.csv"];
  for (const name of candidates) {
    const p = path.join(episodeDir, name);
    if (fs.existsSync(p)) return p;
  }
  return path.join(episodeDir, candidates[0]);
}

const episodeDir = path.join(ROOT, "episodes", episodeName);
const audioDir = path.join(episodeDir, "audio");
const scriptPath = resolveScriptFile();
const voicePath = voicePathArg
  ? path.resolve(ROOT, voicePathArg)
  : path.join(audioDir, isHook ? "hook-voiceover.mp3" : "body-voiceover.mp3");
const timingsPath = path.join(audioDir, isHook ? "hook-timings.json" : "body-timings.json");

// 版本来源与其他脚本一致：显式入参 > brief.json.scriptVersion > CSV 唯一版本 > "A"。
// 但「整期版本」说的是正文，钩子文案在另一份 CSV 里，所以最后一步要在**本段自己的 CSV** 上复核
// （lib/script-version.mjs 的 resolveSegmentVersion；《围城》正文 B 版 + 钩子 A 版会撞在这里）。
const versionChoice = resolveSegmentVersion(episodeDir, scriptPath, requestedVersion);
const version = versionChoice.version;
if (versionChoice.changed) {
  console.log(`版本按本段文案取 ${version}（整期声明 ${versionChoice.from} 在 ${path.basename(scriptPath)} 里没有行）`);
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"' && quoted && line[index + 1] === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') quoted = !quoted;
    else if (char === "," && !quoted) {
      values.push(current);
      current = "";
    } else current += char;
  }
  values.push(current);
  return values;
}

function readScriptRows(csvPath, ver) {
  const text = fs.readFileSync(csvPath, "utf8").trim();
  const lines = text.split(/\r?\n/u);
  const headers = parseCsvLine(lines.shift());
  return lines
    .filter(Boolean)
    .map((line) => {
      const values = parseCsvLine(line);
      return Object.fromEntries(headers.map((h, idx) => [h, values[idx] || ""]));
    })
    .filter((row) => row.version === ver)
    .sort((a, b) => Number(a.order) - Number(b.order));
}

const rows = readScriptRows(scriptPath, version);
if (!rows.length) throw new Error(`No script rows found for version ${version}`);

// ---------- ASR token stream (wall-clock, monotonic) ----------
// whisper-cli 的 JSON 里有两套时间基准，而**哪一套是绝对的要取决于这份 ASR 怎么跑出来的**：
//   · 整段直跑：`timestamps` 是绝对墙钟，`offsets` 会按 ~30s 窗口回绕；
//   · scripts/asr-segmented.mjs 合并分段：它把段起始偏移只加进了 `offsets`（绝对），
//     `timestamps` 仍是每段各自的相对时间。
// 早先这里写死「prefer absolute timestamps」，于是第二类 ASR 会把整篇正文挤进最后一段
// 的时长里（《高老头》301s 配音 → 136 行字幕全落在前 83s，且单调化兜底把它抹平，
// matchRate 仍然 0.81、requiresAgentReview 仍然 false，门禁一个都不报）。
// 现在两套都取出来打分：回绕次数少的赢，平局取总跨度长的，与来源无关地自愈。
const parseAsrTs = (value) => {
  const m = /^(\d+):(\d+):(\d+)[,.](\d+)\s*$/u.exec(String(value));
  return m ? Number(m[1]) * 3600000 + Number(m[2]) * 60000 + Number(m[3]) * 1000 + Number(m[4]) : null;
};

// 阿拉伯数字折成逐位汉字，与 scripts/lib/caption_speech.py 的 clean() 同一口径：
// Whisper 把念出来的数词写成 "68"，而文案写「六十八岁」，一个字符都对不上 → 这一行
// 既没有行首锚点（《嫌疑人X的献身》2026-10-04 行 120 只能落在 岁@214.29，比它自己开口
// 晚 0.52s）。两侧都折，所以「文案数字 vs ASR 数字」的历史期逐位照旧相等。
// 两侧必须同口径：门禁和修轴的手读 lib/caption_speech，建轴读这里，一边折一边不折
// 就是《恶意》那两个音频文件那类「修的填、门禁的空放」死锁。
const DIGIT_CANON = new Map("0123456789０１２３４５６７８９".split("")
  .map((ch, i) => [ch, "零一二三四五六七八九零一二三四五六七八九"[i]]));
const canonDigits = (text) => Array.from(String(text))
  .map((ch) => DIGIT_CANON.get(ch) ?? ch).join("");

// whisper.cpp 的控制 token：[_TT_数字] / [_BEG_] / [_EOT_] / [_EP_数字] / [zh]。
// 形状判据（整对括号），不是前缀表：以前这里写的是 /^\[[_A-Z]+\d*$/u，正则**漏了闭合
// 的 ]**，于是 [ 一开头就永远匹配不上，一个都没剔掉。实测 60 份 body.json 里有
// [_TT_数字] 2636 个、[_BEG_] 510 个、[_EOT_] 10 个。它们混进 token 流以后不只会污染
// gapOnset（相邻 token 的间隔被这些零时长/段界的假 token 改写，行首吸附跟着偏），
// 在 Python 侧更狠：lib/caption_speech 把整个 token 文本 clean() 后拼成字符流，
// [_TT_628] 留下 _TT_六二八 七个幽灵字符——数字一折，它和文案里真念出来的数字
// 是同一串字符，能抢走一次假命中。两侧同形状：Python 侧是 CONTROL_TOKEN。
const CONTROL_TOKEN = /^\s*\[[^\]]*\]\s*$/u;

function buildAsrTokens(raw, source) {
  const tokens = [];
  for (const seg of raw.transcription || []) {
    for (const tok of seg.tokens || []) {
      const t = String(tok.text || "");
      // 纯空白 token（whisper.cpp 会把词间空格单独切成一个 token）不携带任何语音，
      // 以前只判 `!t`，于是它还留在流里，而 ch 取它的首字符就是一个空格 —— 文案侧
      // 永远不会有空格字符，这个 token 就永远匹配不上，gapOnset 也跟着被它改写。
      if (!t.trim() || CONTROL_TOKEN.test(t)) continue;
      let from;
      let to;
      if (source === "timestamps") {
        from = parseAsrTs(tok.timestamps?.from);
        to = parseAsrTs(tok.timestamps?.to);
      } else {
        from = Number(tok.offsets?.from);
        to = Number(tok.offsets?.to);
      }
      if (!Number.isFinite(from) || !Number.isFinite(to) || from < 0 || to < from) continue;
      // ch 必须是**第一个被念出来的字符**，不是原始文本的第一个字符。whisper.cpp 的
      // BPE 把词边界空格留在 token 文本里（" 不是"），而这里的匹配是「文案单字 == token
      // 的 ch」，于是这个 token 永远匹配不上：文案里没有任何字符是空格。
      // 《反抗者》2026-10-08 行 39「注意，不是我。」：token 流是 注意@80.449-81.089 /
      // " 不是"@81.089-81.989 / 我@81.989-82.449，「不」匹配不到 " 不是"，行首只能落到
      // 「我」上 —— token 重算轴比交付轴（80.449，正是「注意」自己的开口）晚 1.541s，
      // 第 5 道差点击红（上限 1.5s），而两个能量证人（逐行覆盖、-50dB 边界）都判 0 行。
      // Python 侧本来就是对的：asr_char_stream() 把整个 token 文本交给 clean()，
      // 而 PUNCT 里含 \s，空格进不了字符流。这条把 Node 侧拉回同一个字母表，
      // 和「数字逐位折汉字」「控制 token 整族剔除」是同一件事的第三种形状。
      // 全库 old-vs-new（146 期有正文 ASR，逐期比对判定）：LCS 命中字数 69 期上升、
      // 其余不变；判红 2 期 → 0 期（反抗者 1.541→1.18、我们八月见 1.957→0.777），
      // **没有一期从绿翻红**；唯一一处 max 上升是《骆驼祥子》0.52→0.62（仍绿，
      // 行首朝它自己的字挪），不是新的缺陷。
      const tchars = Array.from(canonDigits(t.trim()));
      tokens.push({ text: t, ch: tchars[0], start: from, end: to, nchars: tchars.length });
    }
  }
  return tokens;
}

// 回绕次数 = 时间轴被截断成几段；跨度 = 首尾覆盖的毫秒数。
function scoreAsrTokens(tokens) {
  let regressions = 0;
  for (let k = 1; k < tokens.length; k += 1) {
    if (tokens[k].start < tokens[k - 1].start) regressions += 1;
  }
  const span = tokens.length ? tokens[tokens.length - 1].end - tokens[0].start : 0;
  return { regressions, span };
}

function readAsr() {
  const asrDir = path.join(audioDir, "asr");
  const base = isHook ? "hook" : "body";
  const candidates = [path.join(asrDir, `${base}.json`), path.join(asrDir, `${base}-story.json`)];
  for (const p of candidates) {
    if (!fs.existsSync(p)) continue;
    try {
      const raw = JSON.parse(fs.readFileSync(p, "utf8"));
      let tokens = [];
      let best = null;
      let bestSource = "";
      const scores = {};
      for (const source of ["timestamps", "offsets"]) {
        const tried = buildAsrTokens(raw, source);
        if (!tried.length) continue;
        const s = scoreAsrTokens(tried);
        scores[source] = s;
        if (!best || s.regressions < best.regressions
          || (s.regressions === best.regressions && s.span > best.span)) {
          best = s;
          bestSource = source;
          tokens = tried;
        }
      }
      if (best) {
        // 这里以前按「回绕数 ≤ timestamps 的回绕数」反推来源，于是 offsets 赢的时候
        // （0 ≤ N 恒成立）日志照样打印「时间基准=timestamps」——读日志的人会拿错时间轴
        // 去复核 token 时刻（《雨季不再来》2026-09-29 排查行 16 时就被它带偏过一次）。
        // 现在直接记下真正胜出的那一套，并把两套的读数一起打出来。
        console.log(`[asr] ${path.relative(ROOT, p)} 时间基准=${bestSource}`
          + `（回绕 ${best.regressions} 次 / 跨度 ${(best.span / 1000).toFixed(2)}s；`
          + `timestamps 回绕 ${scores.timestamps?.regressions ?? "-"} 次`
          + ` / offsets 回绕 ${scores.offsets?.regressions ?? "-"} 次）`);
      }
      // Enforce monotonic wall-clock times (safety net for mixed sources).
      for (let k = 1; k < tokens.length; k += 1) {
        if (tokens[k].start < tokens[k - 1].start) tokens[k].start = tokens[k - 1].start;
        if (tokens[k].end < tokens[k].start) tokens[k].end = tokens[k].start;
      }
      // Mark real narration pauses: a token onset ≥ 0.4s after the previous
      // token's onset. A sentence begins at the resumption point after such
      // a pause; the LCS forbids matching onto these onsets.
      // 「after the previous token」量的是**上一 token 收尾之后**的空档，不是两次开口之差：
      // 一个多字 token（"知道"、数字 "1976" 被 BPE 切成 "19"/"76"）本身就能占 0.5s 以上，
      // 按 onset-to-onset 判会把它的下一个字当成「停顿后的重新开口」，而 LCS 恰恰禁止行首
      // 落在 gapOnset 上，于是整行被推到更晚的字上。
      // 《雨季不再来》2026-09-29 行 16「一九七六年，她已经是有名的三毛了」：ASR 把年份
      // 记成拉丁数字 token "19"@34.110-34.150 / "76"@34.150-34.760，"年"@34.760 因此被
      // 打上 gapOnset（34.760−34.150=610ms，其实是 "76" 自己的时长），脚本里的「一九七六」
      // 又对不上拉丁数字，行首只能落到 是@35.590 —— 比这句话的真实开口晚 1.5s，
      // 第 5 道门把这条正确的交付轴判成「行归属漂移」（|Δ| 最大 2.15s）。
      // 改成 end-to-onset 后本期最大 |Δ| 2.15→1.32s 转绿，《对我无害之人》《100个人生遗愿》
      // 两条已交付期数的读数一位没变（0.13/0.88、0.2/0.907），所以这是把证人修对，不是放宽判据。
      const GAP_MS = 400;
      for (let i = 1; i < tokens.length; i += 1) {
        if (tokens[i].start - tokens[i - 1].end >= GAP_MS) tokens[i].gapOnset = true;
      }
      return { path: p, tokens };
    } catch (e) {
      console.warn(`Could not read ASR ${p}: ${e.message}`);
    }
  }
  return null;
}

const asr = readAsr();
if (!asr) throw new Error("No ASR JSON found; rerun create-body-timings.mjs first so asr/body.json exists");

// ---------- LCS between clean script chars and ASR tokens ----------
// Plain (gap-free) LCS over the token stream: one token may span several
// characters; matching is anchored to the token's *first* character, so
// repeated characters cannot drift the alignment. Gap onsets are NOT used to
// reject matches here — they are applied later, at row-boundary selection, to
// snap a row's start to the resumption point after a real narration pause.
//
// The traceback is run **twice off one matrix**, because a row's two ends want
// opposite tie-breaks:
//   `earliest`  行首要的是本行第一次真正发出声的地方
//   `latest`    行尾要的是本行最后一次发声（收住的是句末）
// The tail-biased traceback (latest) is the house default and is length-biased,
// so it lands almost every row in its true wall-clock window. But where a row's
// text is read **twice**, the traceback is a free choice, not an observation, and
// a single taste cannot serve both ends: with 行1 的「太阳照常升起」念两遍
// (0.17-1.57 and 2.20-3.91, both confirmed in the waveform; 行2 只在 4.24 才开口),
//   only latest → 行首 2.25, witness head |Δ| 2.08s > 1.5s → 红
//   only earliest → 行尾 1.20, witness tail |Δ| 3.01s > 3s → 红
// Either single taste declares a correct axis broken, and the token witness can
// only see the one end it broke. A tie is not an observation, but "each end takes
// its own side of it" is a definition, not a preference.
// Returns { scriptTokIdx, scriptTokIdxLatest, direct, lcsLen }: scriptTokIdx[k] =
// ASR token index matched by global script char k, or -1 (filled by the passes
// below with nearest context). `direct[k]` keeps the raw LCS verdict for chars
// whose token index was later overwritten by the fill passes — see the row-head
// comment below.
function lcsScriptCharsToTokens(scriptChars, asrTokens) {
  const n = scriptChars.length;
  const m = asrTokens.length;
  const F = [];
  for (let i = 0; i <= n; i += 1) F.push(new Int32Array(m + 1));
  for (let i = 1; i <= n; i += 1) {
    const sc = scriptChars[i - 1];
    for (let j = 1; j <= m; j += 1) {
      if (sc === asrTokens[j - 1].ch) {
        F[i][j] = F[i - 1][j - 1] + 1;
      } else {
        F[i][j] = Math.max(F[i - 1][j], F[i][j - 1]);
      }
    }
  }
  // true only while the value in scriptTokIdx is the LCS's own verdict. The two
  // fill passes below overwrite -1 slots with *context*, which is a guess about
  // ordering, not an observation about time — a row that opens with a mis-heard
  // character would otherwise inherit the previous sentence's last token and
  // report that as its own start (《玩偶之家》 row 2: 她 was heard as 他, so the
  // head fell back to 「家」 at 0.84 s while the voice starts at 2.46 s).
  const trace = (earliest) => {
    const out = new Int32Array(n).fill(-1);
    const seen = new Array(n).fill(false);
    let i = n;
    let j = m;
    while (i > 0 && j > 0) {
      if (scriptChars[i - 1] === asrTokens[j - 1].ch && F[i][j] === F[i - 1][j - 1] + 1) {
        let jj = j - 1;
        if (earliest) {
          // 平手时左滑到同一最优值的最左匹配。F[i-1][*] 沿 p 单调不减，跌破 want 即可停；
          // 「字符相等」不能写进扫描判据——不相等的那些位置只是「这里不匹配」，隔一个字
          // 继续往左常常还能找到同值同字符的另一个候选（把它焊进 while 条件里等于只在紧邻
          // 位置试一下，那不叫平手取最早，而是改了没改）。
          const want = F[i][j] - 1;
          for (let p = j - 1; p >= 1 && F[i - 1][p - 1] >= want; p -= 1) {
            if (F[i - 1][p - 1] === want && scriptChars[i - 1] === asrTokens[p - 1].ch) jj = p - 1;
          }
        }
        out[i - 1] = jj;
        seen[i - 1] = true;
        i -= 1;
        j = jj;
      } else if (F[i - 1][j] >= F[i][j - 1]) i -= 1;
      else j -= 1;
    }
    return { out, seen };
  };
  const latest = trace(false);
  const earliest = trace(true);
  const scriptTokIdxLatest = latest.out;
  const scriptTokIdx = earliest.out;
  const direct = earliest.seen;
  // Forward-fill: unmatched chars inherit the *previous* matched token,
  // keeping the per-char time stream monotonic.
  let prev = -1;
  for (let k = 0; k < n; k += 1) {
    if (scriptTokIdx[k] >= 0) prev = scriptTokIdx[k];
    else if (prev >= 0) scriptTokIdx[k] = prev;
  }
  // Backward-fill the leading unmatched run.
  let next = -1;
  for (let k = n - 1; k >= 0; k -= 1) {
    if (scriptTokIdx[k] >= 0) next = scriptTokIdx[k];
    else scriptTokIdx[k] = next;
  }
  return { scriptTokIdx, scriptTokIdxLatest, direct, lcsLen: F[n][m] };
}

// ---------- Build per-row boundaries ----------
const STRIP = /[，。？！、；：""''《》（）…—·,.?!;:'"()\s]/gu;
const rowsClean = rows.map((row) => Array.from(canonDigits(String(row.text).replace(STRIP, ""))));

const scriptCum = [];
let running = 0;
for (const chars of rowsClean) {
  scriptCum.push({ start: running, end: running + chars.length });
  running += chars.length;
}
const totalScriptChars = running;

const scriptChars = rowsClean.flat();
const gapBefore = asr.tokens.map((t) => Boolean(t.gapOnset));
const { scriptTokIdx, scriptTokIdxLatest, direct, lcsLen } = lcsScriptCharsToTokens(scriptChars, asr.tokens, gapBefore);

const captions = [];
rows.forEach((row, index) => {
  const cum = scriptCum[index];
  // Row start = first matched token in this row that is NOT a gap onset.
  // Gap onsets are forbidden match targets, so a plain forward scan stops
  // at the previous sentence's tail and resumes at the true onset.
  // Only a *directly* LCS-matched character is an observation that this row's
  // own words are audible at that token; an inherited (filled) index merely
  // carries the previous row's tail forward and would date the row to the
  // wrong sentence. Pass 1: direct + non-gap. Pass 2: direct, gap allowed.
  // Pass 3: any index (row fully mis-heard — nothing better exists).
  let firstIdx = -1;
  for (let k = cum.start; k < cum.end; k += 1) {
    const idx = scriptTokIdx[k];
    if (direct[k] && idx >= 0 && !gapBefore[idx]) {
      firstIdx = idx;
      break;
    }
  }
  if (firstIdx < 0) {
    for (let k = cum.start; k < cum.end; k += 1) {
      const idx = scriptTokIdx[k];
      if (direct[k] && idx >= 0) {
        firstIdx = idx;
        break;
      }
    }
  }
  if (firstIdx < 0) {
    for (let k = cum.start; k < cum.end; k += 1) {
      const idx = scriptTokIdx[k];
      if (idx >= 0 && !gapBefore[idx]) {
        firstIdx = idx;
        break;
      }
    }
  }
  // Pass 4: first matched token regardless of gap flag / directness.
  if (firstIdx < 0) {
    for (let k = cum.start; k < cum.end; k += 1) {
      if (scriptTokIdx[k] >= 0) {
        firstIdx = scriptTokIdx[k];
        break;
      }
    }
  }
  // ── 行首回收（2026-10-10）：行首那若干字被误听时，按位对位找回本行自己的开口 ──
  // 与 scripts/lib/caption_speech.py 的 align()「按位对位」同一条规则、同一不猜条件
  // （gs == ga > 0：两侧悬空**字数相等**才敢一一对上；不相等就宁可照旧），
  // 再加一条只有 Node 侧才需要的「无人争抢」前提：上一行的最后一个字本身是直接命中，
  // 所以 prevTokIdx 之后、本行第一直接命中之前那批 ASR token 不可能是上一行的尾巴。
  // 判决只读参考轴，从不读交付轴。
  if (index > 0 && firstIdx >= 0 && rowsClean[index].length > 0) {
    const lastCharOfPrev = scriptCum[index - 1].end - 1;
    if (direct[lastCharOfPrev]) {
      const prevTokIdx = scriptTokIdx[lastCharOfPrev];
      let firstDirectChar = -1;
      let firstDirectTok = -1;
      for (let k = cum.start; k < cum.end; k += 1) {
        if (direct[k] && scriptTokIdx[k] >= 0) { firstDirectChar = k; firstDirectTok = scriptTokIdx[k]; break; }
      }
      const gs = firstDirectChar < 0 ? cum.end - cum.start : firstDirectChar - cum.start;
      const ga = firstDirectTok - prevTokIdx - 1;
      if (gs > 0 && ga === gs && prevTokIdx >= 0 && firstDirectTok > prevTokIdx + 1) {
        const cand = asr.tokens[prevTokIdx + 1];
        const here = asr.tokens[firstDirectTok];
        if (cand && here && cand.start >= asr.tokens[prevTokIdx].end && cand.start < here.start) {
          firstIdx = prevTokIdx + 1;
        }
      }
    }
  }
  // Row end = last matched token in this row (the sentence's final sound).
  // Backwards scan over the **latest** trace, mirroring the head's earliest trace:
  // a line read twice must close on its second reading, or the axis cuts in the
  // gap between the two (《太阳照常升起》 行1 0.17–1.57 与 2.20–3.91：切在 1.20 会把
  // 第二次裸露在没有字幕的空气里）。latest 与 earliest 只在「本行的字在 ASR 里出现过不止一次」
  // 时才分岔，其余行两者逐字相同，所以这里只是给行尾换一把尺子。
  let lastIdx = -1;
  for (let k = cum.end - 1; k >= cum.start; k -= 1) {
    if (scriptTokIdxLatest[k] >= 0) {
      lastIdx = scriptTokIdxLatest[k];
      break;
    }
  }
  if (firstIdx < 0 || lastIdx < 0) {
    const prev = captions.length ? captions[captions.length - 1] : null;
    const start = prev ? prev.end : 0;
    captions.push({ order: Number(row.order), start: Number(start.toFixed(2)), end: Number((start + 0.3).toFixed(2)), _unmatched: true });
    return;
  }
  // No overlap with the previous caption.
  let s = asr.tokens[firstIdx].start / 1000;
  let e = asr.tokens[lastIdx].end / 1000;
  if (captions.length > 0) {
    const prev = captions[captions.length - 1];
    if (s < prev.end) s = prev.end;
  }
  if (e <= s) e = s + 0.3;
  captions.push({ order: Number(row.order), start: Number(s.toFixed(2)), end: Number(e.toFixed(2)) });
});

const durationResult = spawnSync("ffprobe", [
  "-v", "error",
  "-show_entries", "format=duration",
  "-of", "default=noprint_wrappers=1:nokey=1",
  voicePath,
], { cwd: ROOT, encoding: "utf8" });
const duration = Number((durationResult.stdout || "").trim());
if (!Number.isFinite(duration) || duration <= 0) throw new Error(`Invalid voiceover duration: ${duration}`);

// Final monotonic + duration clamp
let prevEnd = 0;
for (const c of captions) {
  if (c.start < prevEnd) c.start = prevEnd;
  if (c.end <= c.start) c.end = Number((c.start + 0.3).toFixed(2));
  if (c.end > duration) c.end = Number(duration.toFixed(2));
  prevEnd = c.end;
}

const matchedCount = captions.filter((c) => !c._unmatched).length;
console.log(`LCS match rate: ${lcsLen}/${totalScriptChars} script chars matched (${(100 * lcsLen / Math.max(1, totalScriptChars)).toFixed(1)}%)`);
console.log(`ASR source: ${path.relative(ROOT, asr.path)} (tokens: ${asr.tokens.length}, last end: ${asr.tokens[asr.tokens.length - 1]?.end / 1000}s)`);
console.log(`Rows: ${rows.length}, captions: ${captions.length} (all matched: ${matchedCount === captions.length})`);

// --verify：把「已经在用的轴」和「按 ASR token 现算的轴」逐行对比，只报不写。
// 交付门禁用它（2026-09-28《茶馆》）：silence-segments 铺满轴是**顺序 tiling**，形状完美
// （末条覆盖比 0.998、行间零空隙、能量包络全对），但每行的**归属**错了，最大漂到 −16.29s，
// 而字幕边界压测拿字幕轨自己当真值，156/156 点照样全绿。唯一能独立作证的是文本↔时间：
// 重算一遍轴再和交付轴比，漂移立刻现形。
// 容差不对称是有意的：行尾允许 audit-caption-axis.py 按能量边界合法收放（≤1s 级），
// 行首只允许 D/E 两类的合法挪动（≤0.6s 级），再大就是行归属错了。
if (process.argv.includes("--verify")) {
  const againstFlag = process.argv.find((a) => a.startsWith("--against="));
  const target = againstFlag ? path.resolve(ROOT, againstFlag.slice("--against=".length)) : timingsPath;
  if (!fs.existsSync(target)) {
    console.error(`--verify 找不到轴文件：${target}`);
    process.exit(2);
  }
  const shipped = JSON.parse(fs.readFileSync(target, "utf8")).captions || [];
  const byOrder = new Map(shipped.map((c) => [Number(c.order), c]));
  const ds = [];
  const de = [];
  const missing = [];
  for (const c of captions) {
    const s = byOrder.get(c.order);
    if (!s) { missing.push(c.order); continue; }
    ds.push({ order: c.order, d: Number(s.start) - c.start });
    de.push({ order: c.order, d: Number(s.end) - c.end });
  }
  // 真中位数（偶数取中间两数平均），不是「上中位」。
  // 钩子段只有 2 行，上中位 == max，于是「中位 ≤0.35」在钩子段上变成了「任一行都不许挪 0.35」，
  // 把 snap/audit 合法的 ≤0.6s 行首挪动一律判红：《三体1·地球往事》《偏跟山过不去》0.36s、
  // 《你的夏天还好吗》0.60s 全是这种红，而逐行覆盖门禁（这条轴的真正权威）判 0 行。
  // 行归属错的信号是**多秒级整段漂移**（《茶馆》−16.29s），那由 startMax/endMax 兜住，
  // 改成真中位数不会放过它：两行里坏掉一行，max 仍然要点名。
  const med = (xs) => {
    const ax = xs.map((x) => Math.abs(x)).sort((a, b) => a - b);
    if (!ax.length) return 0;
    const mid = Math.floor(ax.length / 2);
    return ax.length % 2 === 0 ? (ax[mid - 1] + ax[mid]) / 2 : ax[mid];
  };
  const mx = (xs) => xs.reduce((m, x) => Math.max(m, Math.abs(x)), 0);
  const limits = { startMed: 0.35, startMax: 1.5, endMed: 1.0, endMax: 3.0 };
  const verdict = {
    rowsCompared: ds.length,
    rowsMissing: missing.length,
    startMedAbs: Number(med(ds.map((x) => x.d)).toFixed(3)),
    startMaxAbs: Number(mx(ds.map((x) => x.d)).toFixed(3)),
    endMedAbs: Number(med(de.map((x) => x.d)).toFixed(3)),
    endMaxAbs: Number(mx(de.map((x) => x.d)).toFixed(3)),
    worstStart: ds.slice().sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 6),
    worstEnd: de.slice().sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 6),
  };
  const redHead = verdict.startMedAbs > limits.startMed || verdict.startMaxAbs > limits.startMax;
  const redTail = verdict.endMedAbs > limits.endMed || verdict.endMaxAbs > limits.endMax;
  const bad = missing.length > 0 || redHead || redTail;
  console.log(`[verify] 对比 ${path.relative(ROOT, target)}：`
    + `行首 |Δ| 中位 ${verdict.startMedAbs}s 最大 ${verdict.startMaxAbs}s，`
    + `行尾 |Δ| 中位 ${verdict.endMedAbs}s 最大 ${verdict.endMaxAbs}s，`
    + `缺行 ${verdict.rowsMissing}`);
  if (bad) {
    console.log(`  阈值：行首中位 ≤${limits.startMed}s 且最大 ≤${limits.startMax}s，`
      + `行尾中位 ≤${limits.endMed}s 且最大 ≤${limits.endMax}s`);
    if (missing.length) {
      console.log(`   缺行 ${missing.slice(0, 8).join(", ")}`);
    }
    // 点名必须跟着**发红的那一维**走。这里以前只印 worst（行首），于是《女儿红》
    // 2026-10-03 的「行尾最大 3.95s > 3s」下面列出的是六行的行首差（0.47–1.15s，全部合法），
    // 读日志的人只会去查那六行，而真正被拖住 3.95s 尾巴的是另一行。
    // 一条只报「红」不报「红在哪一行」的门禁，等于把定位成本转嫁给下一个会话。
    for (const [flag, list, label] of [[redHead, verdict.worstStart, "行首"], [redTail, verdict.worstEnd, "行尾"]]) {
      if (!flag) continue;
      for (const w of list) {
        console.log(`   行 ${w.order} 交付轴${label} ${w.d > 0 ? "+" : ""}${w.d.toFixed(2)}s 于 token 重算轴`);
      }
    }
    console.log("  → 交付轴不是从本次 ASR token 推出来的（多半是静音铺满轴整段漂了行归属）。"
      + "修法：asr-segmented.mjs → asr-to-simplified.py → 本脚本重写轴，"
      + "再跑 audit-caption-axis.py --apply，然后重渲。");
    process.exit(1);
  }
  console.log("✅ 交付轴与 token 重算轴一致（行归属没有漂移）");
  process.exit(0);
}

fs.mkdirSync(audioDir, { recursive: true });
const out = {
  scriptVersion: version,
  duration: Number(duration.toFixed(2)),
  captionDisplay: "sentence",
  source: "LCS-aligned ASR→script sentence timings (create-body-timings-precise.mjs)",
  audio: path.relative(ROOT, voicePath),
  asr: path.relative(ROOT, asr.path),
  skipLeadingSegments: 0,
  alignment: {
    method: "whisper-token LCS alignment (sentence-granularity, gap-aware, token-onset times)",
    speechSegments: "n/a (uses ASR tokens directly)",
    requiresAgentReview: false,
    asrAvailable: true,
    lcsMatchRate: Number((lcsLen / Math.max(1, totalScriptChars)).toFixed(3)),
  },
  captions,
};
fs.writeFileSync(timingsPath, `${JSON.stringify(out, null, 2)}\n`);
console.log(`Wrote ${path.relative(ROOT, timingsPath)}`);

console.log("--- first 6 captions ---");
for (const c of captions.slice(0, 6)) {
  const row = rows[c.order - 1];
  console.log(`  ${c.order} [${c.start}, ${c.end}]  ${(row?.text || "").slice(0, 24)}…`);
}
console.log("--- last 6 captions ---");
for (const c of captions.slice(-6)) {
  const row = rows[c.order - 1];
  console.log(`  ${c.order} [${c.start}, ${c.end}]  ${(row?.text || "").slice(0, 24)}…`);
}
