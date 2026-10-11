import fs from "node:fs";
import path from "node:path";

function versionColumn(csvPath) {
  if (!fs.existsSync(csvPath)) return new Set();
  return new Set(
    fs.readFileSync(csvPath, "utf8")
      .trim()
      .split(/\r?\n/u)
      .slice(1)
      .filter(Boolean)
      .map((line) => line.split(",", 1)[0].replace(/^"|"$/gu, ""))
      .filter(Boolean),
  );
}

export function csvVersions(csvPath) {
  return versionColumn(csvPath);
}

/** 段级版本：`resolveScriptVersion` 回答的是「整期用哪一版正文」——它先读 brief.json 的
 * `scriptVersion`，再读 `script-body.csv` 的唯一版本。可三段结构的钩子文案在 `script-hook.csv` 里，
 * 两份 CSV 的版本号没有义务相同：《围城》正文是 B 版，钩子只有 A 版两行，于是建轴链在
 * `create-body-timings-precise.mjs --segment=hook` 上抛 `No script rows found for version B`
 * （2026-10-07 第五批），而 `snap-body-timings-vad.py` 会拿到 0 行、把除零当成下一个报错。
 * 所以先在**本段自己的 CSV** 里问一遍：整期版本在这份文件里没有行、而这份文件只有一个版本，
 * 就用那一个；两个版本都在（真的需要人来选）就原样交回，让原来的报错继续说话。 */
export function resolveSegmentVersion(episodeDir, scriptPath, requestedVersion = "") {
  const episode = resolveScriptVersion(episodeDir, requestedVersion) || "A";
  if (requestedVersion) return { version: episode, changed: false };
  const present = [...versionColumn(scriptPath)];
  if (present.length && !present.includes(episode) && present.length === 1) {
    return { version: present[0], changed: true, from: episode };
  }
  return { version: episode, changed: false };
}

export function resolveScriptVersion(episodeDir, requestedVersion = "") {
  if (requestedVersion) return requestedVersion;

  const briefPath = path.join(episodeDir, "brief.json");
  if (fs.existsSync(briefPath)) {
    const brief = JSON.parse(fs.readFileSync(briefPath, "utf8"));
    if (brief.scriptVersion) return String(brief.scriptVersion);
  }

  const scriptPath = path.join(episodeDir, "script-body.csv");
  if (fs.existsSync(scriptPath)) {
    const versions = new Set(
      fs.readFileSync(scriptPath, "utf8")
        .trim()
        .split(/\r?\n/u)
        .slice(1)
        .filter(Boolean)
        .map((line) => line.split(",", 1)[0].replace(/^"|"$/gu, ""))
        .filter(Boolean),
    );
    if (versions.size === 1) return [...versions][0];
  }

  return "A_reference_like";
}
