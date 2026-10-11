#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const ROOT = process.cwd();
const [episode, version, chunkIdx] = process.argv.slice(2);
const episodeDir = path.join(ROOT, 'episodes', episode);
const bodyDir = path.join(episodeDir, 'audio');
const scriptRows = fs.readFileSync(path.join(episodeDir, 'script-body.csv'), 'utf8')
  .split('\n').slice(1).filter(Boolean).map(l=>[l.split(',')].flat()[0]);
const bt = JSON.parse(fs.readFileSync(path.join(episodeDir, 'audio', 'body-timings.json'), 'utf8'));
const caps = bt.captions;
const chunkLen = 60;
const start = Number(chunkIdx)*chunkLen;
const idx = caps.findIndex(c=>c.start>=start);
const endTime = Math.min(start+chunkLen, bt.duration);
console.log('chunk', chunkIdx, start, endTime);
