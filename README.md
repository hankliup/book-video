# Book Video

这是一个通过自然语言制作图书带货短视频的开源工作流。它把选书、文案、氛围图、旁白对齐、字幕轴、渲染和交付验收组织成一套可复用的流程。

Copyright (c) 2026 prototech, endless, and 未济.

## 如何使用

你不需要先学习代码或命令。在 Codex、Qoder 或其他支持 skills 的 Agent 里，直接用自然语言描述你想做的图书视频即可。第一次使用时，Agent 会先检查本地环境；需要你确认的地方，它会直接问你。

仓库自带一个可复用的 Agent skill：

- 真源：`.agents/skills/book-video-production/`
- `SKILL.md`：入口，负责开工检查、任务路由和六个生产阶段的编排
- `references/runbook.md`：按阶段排列的命令、产物地图与红灯对照表
- `references/gate-map.md`：六道生产闸、视觉/听觉规则族与交付门禁对应的脚本

在仓库根目录执行一次，把它接到用户级 skills 目录：

```bash
mkdir -p ~/.agents/skills
ln -s "$(pwd)/.agents/skills/book-video-production" ~/.agents/skills/book-video-production
```

之后可以用 `/book-video-production` 调用，也可以直接说“做一期读书视频”“给这期写钩子”“重渲这期”“验收交付”。Skill 只负责编排和定位；具体规则以 `AGENTS.md`、`docs/book-video-playbook.md` 和 `docs/rules/*.md` 为准，避免出现第二份会过期的规则副本。

## 制作步骤

1. 告诉 Agent 你要做哪本书，或者让它推荐五本适合做短视频的书。
2. Agent 确认书名、作者和版本，写出正文文案与两行钩子，保存为 `episodes/<书名>/script-body.csv`、`script-hook.csv`；同时把抖音/视频号发布文案写进 `brief.json`。
3. 你审核文案；确认后，Agent 按 `docs/rules/` 的门禁挑选 Pexels 实拍背景图，并记录 photo id、摄影师和来源链接。
4. 你录好口播并导出 MP3，把路径告诉 Agent。Agent 用 ASR 只取时间，以 `script-body.csv` 为字幕真源，生成字幕轴和 scene-map。
5. Agent 渲染三段视频（钩子、固定开场白、正文），完成混音和交付门禁，产出 `renders/<书名>-final.mp4`。
6. 交付还包括独立片头 `renders/intro-only.mp4`。全部门禁通过后，才回收 `tmp/` 里的渲染中间产物。
7. 如果你要求换文案、换图或换音频，Agent 会重建受影响的下游产物，重新过门禁后再覆盖旧方案。

你也可以直接用自然语言操作，例如：

- “你好。”
- “推荐五本适合做情绪共鸣类视频的书。”
- “我想制作一本关于孤独和自我成长的书。”
- “把当前文案换成我提供的版本。”
- “这版视频不满意，请保留模板，替换图片和文案。”
- “把这期重渲一遍。”
- “验收这期交付。”

## 本地与私有的边界

从当前版本起，以下内容只留在本机、不进入 Git：

- `assets/bgm/`：BGM 是本地私有素材。当前版本不内置曲目；请自行准备音频并确认拥有使用与再分发权。该目录内的 `.gitignore` 会忽略全部音频。
- `episodes/` 下的成片和生成物、`tmp/` 渲染中间产物、`assets/models/`、`assets/reference-videos/`、`.env`、账号导出与发布登记数据。

## 环境要求

Node.js 22+、`npx`、FFmpeg/FFprobe、`whisper-cli`。首次运行 `node scripts/init.mjs` 会统一检查；缺失项会一次性列出，由你确认后再安装。

## 参考来源

本项目的图书视频工作流参考了[原帖](https://x.com/369Serena/status/2073398014333321498)，特此致谢。

## 版权与许可

本项目的代码、文档和可复用模板采用 [Apache-2.0](LICENSE) 发布，版权方为 prototech（组织名），endless（网名）。Apache-2.0 不代表它自动覆盖第三方工具、字体、模型、图片生成服务或媒体素材。

当前版本不包含 BGM。`assets/bgm/` 是本地私有目录，放在其中的音频不适用 Apache-2.0；版权归相关权利人所有，使用或再分发前请自行确认已取得相应许可。字体、模型、图片生成服务和其他用户媒体同样各自保留其原有条款。

更详细的内部生产规则见 `AGENTS.md` 和 `docs/book-video-playbook.md`。
