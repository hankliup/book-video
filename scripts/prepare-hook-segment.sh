#!/usr/bin/env bash
# 钩子段出片前的一步准备：按清单重取并认证钩子图 → 整批调色 → 补压亮区 → 溯源门禁，
# 然后跑钩子段字幕轴（链路照 AGENTS.md 正文轴顺序、段名换 hook）。
#
# 为什么 A 段不用 && 串到底：`grade-atmosphere-images.py` 在「仍有区域偏亮」时
# 打印 ⚠️ 并以 exit 1 结束（scripts/grade-atmosphere-images.py 尾部），而那正是
# AGENTS.md 要求的「接着跑 fix-bright-regions.py」的信号。用 && 串就会在补压之前
# 整段中断——2026-10-03 第三批 10 期里有 6 期这样停在调色，盘上留下「调过色但没补压」
# 的图。所以这里挨个跑、把退出码降级成提示，真正的断言交给 check-image-provenance.py
# （它同时查尺寸、亮度、调色是否跳过、溯源与跨期撞图）。
set -u
cd "$(dirname "$0")/.."

books=("$@")
if [ ${#books[@]} -eq 0 ]; then
  echo "用法: tmp/prepare-hook-segment.sh <书名> [<书名> ...]" >&2
  exit 2
fi

logdir="tmp/hook-prepare"
mkdir -p "$logdir"

for b in "${books[@]}"; do
  echo "===== $b ====="
  # 这里是子 shell 括号而不是花括号组：块里的每条失败路径都写的是 `exit 1`，
  # 花括号组的 exit 会直接结束整个脚本——2026-10-03 第四批 10 期跑这一步，第一期
  # 卡在溯源门禁（见下面 write-prompts-csv 那段），后面 9 期连日志都没生成，
  # 而外层 `st=$?` 本来就是为「一期失败、继续下一期」写的。
  (
    echo "--- A. 按清单重取钩子图并认证落盘 ---"
    # --segment=hook 必需：这份清单记的是整期 18 张（钩子 2 + 正文 16）。以前这里只靠
    # IMAGES_SUBDIR=images-hook 说「这次处理钩子段」，而正文条目在清单里没有 dir，
    # fetch 的路由就跟着 IMAGES_SUBDIR 走 —— FORCE=1 于是把 16 张**已调色**的正文图
    # 逐张重下、原始亮度落进 images-hook/，盖掉了真正的两张钩子图（《猎人笔记》2026-10-06）。
    # 现在目录由条目自己的 dir/segment 决定，本开关只负责「不动正文那 16 条」。
    IMAGES_SUBDIR=images-hook FORCE=1 python3 scripts/fetch-pexels-by-id.py \
      "episodes/$b" "data/${b}_pexels_ids.json" --segment=hook || { echo "FAILED 取图"; exit 1; }
    IMAGES_SUBDIR=images-hook python3 scripts/grade-atmosphere-images.py "episodes/$b" --restore \
      >/dev/null 2>&1 || echo "  （--restore 没有可还原的 _raw，继续按盘上图调色）"
    IMAGES_SUBDIR=images-hook python3 scripts/grade-atmosphere-images.py "episodes/$b" \
      || echo "  ↑ grade 判仍有区域偏亮（exit 1）：这是要跑补压的信号，不是中断点"
    IMAGES_SUBDIR=images-hook python3 scripts/fix-bright-regions.py "episodes/$b" \
      || echo "  ↑ fix-bright-regions exit $?"
    # 溯源行必须由同一份清单生成，而且必须在门禁**之前**：check-image-provenance.py 认的是
    # prompts.csv 里有没有这张图的 Pexels id／摄影师，而 prompts.csv 的钩子行只有
    # write-prompts-csv.py 会写。这一步以前不在脚本里，是每批手工补的——于是脚本自己
    # 跑不通自己的门禁（2026-10-03 第四批第一期就停在这里）。它按段合并，清单没覆盖的
    # 正文行原样保留（AGENTS.md：一次钩子重锁不能抹掉 14–23 行正文溯源）。
    python3 scripts/write-prompts-csv.py "$b" --apply || { echo "FAILED 溯源行写入"; exit 1; }
    python3 scripts/check-image-provenance.py "$b" || { echo "FAILED 溯源门禁"; exit 1; }

    echo "--- B. 钩子段字幕轴 ---"
    node scripts/process-voiceover.mjs "episodes/$b/钩子.mp3" \
      "episodes/$b/audio/hook-voiceover.mp3" story >/dev/null 2>&1 || { echo "FAILED 配音处理"; exit 1; }
    cp "episodes/$b/audio/hook-voiceover.mp3" "episodes/$b/audio/hook-voiceover-story.mp3" &&
    node scripts/asr-segmented.mjs "$b" "episodes/$b/audio/hook-voiceover.mp3" --asr=hook &&
    python3 scripts/asr-to-simplified.py "episodes/$b/audio/asr/hook.json" &&
    node scripts/create-body-timings-precise.mjs "$b" --segment=hook &&
    python3 scripts/snap-body-timings-vad.py "$b" --segment hook --apply &&
    python3 scripts/audit-caption-axis.py "$b" --segment hook --apply \
      || { echo "FAILED 字幕轴"; exit 1; }
    echo "OK $b"
  ) > "$logdir/$b.log" 2>&1
  st=$?
  if [ $st -ne 0 ]; then
    echo "  FAILED(exit=$st)  见 $logdir/$b.log"
    tail -5 "$logdir/$b.log" | sed 's/^/    /'
  else
    grep -E "已认证落盘|背景图溯源门禁|ASR 字序命中" "$logdir/$b.log" | sed "s/^/  [$b] /"
  fi
done
echo "ALL DONE"
