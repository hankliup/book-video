#!/usr/bin/env node
// 最终 mux：intro + body-xwz + intro-story + body-story + BGM + SFX
// 直接抄 render-episode-final.mjs 的 filter_complex，针对 _render_chunk_fallback_rxdl 的产出。
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

function slugifyEpisodeName(name) {
  const ascii = name.normalize("NFKD").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
  const digest = createHash("sha1").update(name).digest("hex").slice(0, 10);
  return ascii ? `${ascii}-${digest}` : `book-${digest}`;
}

const ROOT = process.cwd();
const EP = process.argv[2];
if (!EP) { console.error('Usage: node scripts/_final_mux_rxdl.mjs <episode>'); process.exit(1); }
const EP_DIR = path.join(ROOT, 'episodes', EP);
const SLUG = slugifyEpisodeName(EP);
// slugify 结果形如 "book-a04c35a315"，去掉前缀只取哈希部分作为目录名
const SLUG_HASH = SLUG.replace(/^book-/, '');
const INTRO_MP4 = path.join(ROOT, 'tmp', `preview-${SLUG}`, 'intro', 'renders', 'intro.mp4');
const BODY_MP4 = path.join(EP_DIR, 'renders', 'body-xwz.mp4');
const INTRO_STORY = path.join(ROOT, 'tmp', `preview-${SLUG}`, 'audio', 'intro-voiceover-story.mp3');
const BODY_STORY = path.join(EP_DIR, 'audio', 'body-voiceover-story.mp3');
const BGM_FILE = process.argv[3] || 'Where are you（low）.mp3';
const BGM_DIR = path.join(ROOT, 'assets', 'bgm');
const BGM = fs.existsSync(path.join(BGM_DIR, BGM_FILE)) ? path.join(BGM_DIR, BGM_FILE) : BGM_FILE;
const SCROLL_SFX = path.join(ROOT, 'assets', 'sfx', 'gear-scroll.mp3');
const WATER_SFX = path.join(ROOT, 'assets', 'sfx', 'water.mp3');
const OUT = path.join(EP_DIR, 'renders', `${SLUG}-final-${BGM_FILE.replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '')}-story-voice-bgm-standard.mp4`);

const INTRO_TRIM = 1.83;
const INTRO_OFFSET = Math.round(INTRO_TRIM * 1000);
const SCROLL_START = 1.08, SCROLL_END = 2.38;
const SCROLL_VOL = 1.4, SCROLL_FADE = 0.2;
const scrollDur = +(SCROLL_END - SCROLL_START).toFixed(2);
const scrollDelay = Math.round(SCROLL_START * 1000);
const scrollFadeStart = +(scrollDur - SCROLL_FADE).toFixed(2);
const WATER_START = +(INTRO_TRIM + 0.32).toFixed(2);
const WATER_VOL = 1.0;
const BGM_VOL = 0.32;
const INTRO_VOICE_DUR = spawnSync('ffprobe', [
  '-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',
  path.join(ROOT,'assets/template-audio/intro-voiceover.mp3'),
], { encoding: 'utf8' }).stdout.trim();
console.log('intro voice dur', INTRO_VOICE_DUR);

const timings = JSON.parse(fs.readFileSync(path.join(EP_DIR, 'audio', 'body-timings.json'), 'utf8'));
const bodyDuration = timings.duration;
const finalDuration = +(INTRO_TRIM + bodyDuration).toFixed(2);
console.log('bodyDuration', bodyDuration, 'finalDuration', finalDuration);

// 确保 body-voiceover-story.mp3 已处理过
if (!fs.existsSync(BODY_STORY)) {
  console.log('processing body voiceover story…');
  const r = spawnSync('node', ['scripts/process-voiceover.mjs', path.join(EP_DIR, 'audio', 'body-voiceover.mp3'), BODY_STORY, 'story'], { cwd: ROOT, stdio: 'inherit' });
  if (r.status !== 0) { console.error('body voiceover story processing failed'); process.exit(1); }
}

const args = [
  '-y',
  '-i', INTRO_MP4,
  '-i', BODY_MP4,
  '-i', INTRO_STORY,
  '-i', BODY_STORY,
  '-stream_loop', '-1',
  '-i', BGM,
  '-i', SCROLL_SFX,
  '-i', WATER_SFX,
  '-filter_complex',
  [
    `[0:v]trim=0:${INTRO_TRIM},setpts=PTS-STARTPTS[v0]`,
    `[1:v]trim=0:${bodyDuration},setpts=PTS-STARTPTS[v1]`,
    '[v0][v1]concat=n=2:v=1:a=0[v]',
    '[2:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[introa]',
    `[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_OFFSET}|${INTRO_OFFSET},volume=1.0[bodya]`,
    `[4:a]atrim=0:${+(bodyDuration).toFixed(2)},asetpts=PTS-STARTPTS,aresample=48000,adelay=${INTRO_OFFSET}|${INTRO_OFFSET},volume=${BGM_VOL},afade=t=in:st=0:d=0.5[bgm]`,
    `[5:a]atrim=0:${scrollDur},asetpts=PTS-STARTPTS,aresample=48000,volume=${SCROLL_VOL},afade=t=in:st=0:d=0.01,afade=t=out:st=${scrollFadeStart}:d=${SCROLL_FADE},adelay=${scrollDelay}|${scrollDelay}[scrollsfx]`,
    `[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=${WATER_VOL},adelay=${Math.round(WATER_START*1000)}|${Math.round(WATER_START*1000)}[watersfx]`,
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
  ].join(';'),
  '-map', '[v]',
  '-map', '[a]',
  '-c:v', 'libx264',
  '-pix_fmt', 'yuv420p',
  '-profile:v', 'high',
  '-level', '4.1',
  '-c:a', 'aac',
  '-b:a', '192k',
  '-movflags', '+faststart',
  '-shortest',
  OUT,
];
console.log('ffmpeg starting…');
const r = spawnSync('ffmpeg', args, { stdio: 'inherit' });
if (r.status !== 0) {
  console.error('ffmpeg failed', r.status);
  process.exit(1);
}
console.log('OK', OUT);
console.log('duration', spawnSync('ffprobe', ['-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1', OUT], { encoding: 'utf8' }).stdout.trim());

// 技术校验
const probe = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=codec_type,width,height', '-of', 'json', OUT], { encoding: 'utf8' });
const pj = JSON.parse(probe.stdout || '{}');
const v = pj.streams?.find((s) => s.codec_type === 'video');
if (!v || v.width !== 720 || v.height !== 960) throw new Error(`Invalid dimensions ${v?.width}x${v?.height}`);
const dur = spawnSync('ffprobe', ['-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1', OUT], { encoding: 'utf8' }).stdout.trim();
if (Number(dur) > 60.05 && process.env.ALLOW_OVER_60_SECONDS !== '1') throw new Error(`Final ${dur}s > 60s`);
console.log('technical check passed');
