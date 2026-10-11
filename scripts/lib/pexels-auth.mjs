#!/usr/bin/env node
/**
 * Pexels 鉴权头的唯一传法（Node 侧）：curl 的配置走 stdin（`-K -`），密钥绝不进 argv。
 *
 * Python 侧是 scripts/lib/pexels_curl.py，两边走同一条 curl 机制、同一份配置行格式。
 * 理由与那条一致，简版：
 *   1) argv 全局可读——这台机器常年并行跑七八个会话和后台渲染，`ps -eww` 一眼看到共享 key；
 *   2) spawnSync 失败时把整条 argv 打回 stderr/日志的写法很常见（child_process 的 error 对象
 *      就带 `cmd`），一次代理抖动就把密钥写进会话记录。
 * `-K -` 让 curl 从 stdin 读 `header = "Authorization: ..."`，密钥只走一次管道，不落 argv 不落盘。
 *
 * 用法：
 *   import { spawnCurl } from "./lib/pexels-auth.mjs";
 *   const res = spawnCurl(["-sS", "--fail", "-m", "30", url], API_KEY, { encoding: "utf8" });
 */
import { spawnSync } from "node:child_process";

const BAD_KEY_CHARS = /["'\\\n\r]/u;

/** curl 配置文件内容（一行 header）。密钥含引号/换行会打断这行配置，直接拒绝。 */
export function authConfig(key) {
  if (!key) throw new Error("缺少 Pexels API Key（应来自仓库 .env，不作为命令行参数传入）");
  if (BAD_KEY_CHARS.test(key)) {
    throw new Error("Pexels API Key 含引号/反斜杠/换行，无法安全写进 curl 配置");
  }
  return `header = "Authorization: ${key}"\n`;
}

/**
 * 跑一次带鉴权的 curl：argv 里只有 `-K -` 和调用方给的选项，没有密钥。
 * options 透传给 spawnSync（encoding 等），`input` 由本函数占用——调用方不要用 stdin。
 */
export function spawnCurl(args, key, options = {}) {
  return spawnSync("curl", ["-K", "-", ...args], { ...options, input: authConfig(key) });
}
