#!/usr/bin/env node
// 手工 final mux：intro.mp4 + body-padded.mp4 + intro-voiceover-story + body-voiceover-story
// + BGM + scroll sfx + water sfx。直接抄 render-episode-final.mjs 的 filter_complex，
// 因为 HyperFrames 在沙箱里落盘失败。源 frames 已捕获，重组 body 视频后人工 mux。
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const EP = process.argv[2] || '边城';
const EP_DIR = path.join(ROOT, 'episodes', EP);
const PREVIEW = path.join(ROOT, 'tmp', 'preview-book-5a1b7ca50f');
const INTRO_MP4 = path.join(PREVIEW, 'intro/renders/intro.mp4');
const BODY_MP4 = path.join(PREVIEW, 'body/renders/body-padded.mp4');
const INTRO_STORY = path.join(PREVIEW, 'audio/intro-voiceover-story.mp3');
const BODY_STORY = path.join(EP_DIR, 'audio/body-voiceover-story.mp3');
const BGM_DIR = path.join(ROOT, 'assets/bgm');
const SCROLL_SFX = path.join(ROOT, 'assets/sfx/gear-scroll.mp3');
const WATER_SFX = path.join(ROOT, 'assets/sfx/water.mp3');
const OUT = path.join(EP_DIR, 'renders', `book-边城-final-Where-are-you-story-voice-bgm-standard.mp4`);

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

// bodyDuration 取自 body-timings.json
const timings = JSON.parse(fs.readFileSync(path.join(EP_DIR, 'audio/body-timings.json'), 'utf8'));
const bodyDuration = timings.duration;
const finalDuration = +(INTRO_TRIM + bodyDuration).toFixed(2);
console.log('bodyDuration', bodyDuration, 'finalDuration', finalDuration);

const bgmFile = fs.readdirSync(BGM_DIR).find(n => n.toLowerCase().endsWith('.mp3'));
const bgm = path.join(BGM_DIR, bgmFile);
console.log('bgm', bgmFile);

const args = [
  '-y',
  '-i', INTRO_MP4,
  '-i', BODY_MP4,
  '-i', INTRO_STORY,
  '-i', BODY_STORY,
  '-stream_loop', '-1',
  '-i', bgm,
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
console.log('ffmpeg starting...');
const r = spawnSync('ffmpeg', args, { stdio: 'inherit' });
if (r.status !== 0) {
  console.error('ffmpeg failed', r.status);
  process.exit(1);
}
console.log('OK', OUT);
console.log('duration', spawnSync('ffprobe', ['-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1', OUT], { encoding: 'utf8' }).stdout.trim());
