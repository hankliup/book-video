#!/bin/bash
# 钩子回补 · 把已批的期数渲染成「钩子 + 固定开场白 + 正文」三段成片并跑交付门禁（现为十道）。
# 用法：bash scripts/render-hook-batch.sh <书名> [<书名> ...]
# 每一期独立日志 tmp/render-b3/<书名>.log，门禁汇总原样落盘，不靠退出码判断成败。
#
# 为什么要 render_segment（2026-10-03《偏跟山过不去》）：HyperFrames 的 npx 缓存偶发缺
# core/dist/hyperframe.manifest.json（"Build core runtime artifacts before rendering"），
# 同一条命令紧接着重试往往就好了——钩子段就是这么失败的，而紧随其后的 intro 段渲成功了。
# 更要紧的是失败之后会发生什么：兜底渲染器拒绝出片（"有 script-hook.csv 但缺钩子段素材"），
# 已交付的旧成片原样留在 renders/，八道门禁量的是**磁盘上的新图 + 那部旧成片**，于是它会
# 给出 8 个绿灯，尽管成片里跑的还是换掉之前那张和别的期撞了的照片。绿灯是旧的，缺陷是新的。
# 所以这里断言两件事：① 每段渲完必须真的产出 mp4，缺了就重试，六次不行就跳过本期不进门禁；
# ② 门禁之后加一道 check-hook-frame-match.py，用画面本身核对每张钩子图确实上过屏。
# （2026-10-07 补上第三件断言见 render_segment 内的伪影扫描，和 2026-10-05 的组成指纹并列：
#  「渲出来的这一份本身可以是脏帧」——用户 10-06 第①条规则要求的 check-render-artifacts.py
#   此前只写在 playbook 里，没有任何调用点，等于规则只有文档没有机器执行。）
#
# 2026-10-05 加第三件（render_segment 里的渲前/渲后组成指纹）：**渲的过程本身**可以是脏的。
# 一次 render 跑几十秒，同期另一个会话重跑 create-episode-preview.mjs 就会把这份工程目录在中间
# 改掉，产物于是是「一半旧一半新」的像素，而门禁对它照样能给十道绿灯（《李尔王》第四次报闪屏
# 就是这个：index.html 改于 10:10:42、渲染 10:10:58 收尾、交付片带的是 `.scene` 还没有 z-index
# 那一版之前的形状）。现在两个指纹对不上就重渲，对上则把「组成 + 成片 + 生成器」三方指纹写成
# renders/<段>.provenance.json —— 本仓库没有 git，那是产物与源码之间唯一一份对账单。
# 同一份还镜像到 episodes/<书名>/renders/（预览目录的 renders/ 会被下一次生成整片 rm -rf，
# 账本跟着像素一起消失就永远读不回来），门禁之后再 `verify` 读一遍：账本对不上今天的生成器
# 就说明本期带的是上一代形状的像素，十道绿灯一起响也不会变，只有重渲这一条路。
set -u
# ${BASH_SOURCE[0]:-$0} 而不是 $0：本文件的函数要被回归测试 **source** 进去跑（见
# scripts/tests/test-render-serialization.py）。用 $0 时，`bash -c 'source scripts/render-hook-batch.sh'`
# 里 $0 是 "bash"，dirname 得到 "."，ROOT 于是指到仓库的上一级目录——所有真源路径集体读不到，
# 判据当场从「等」退化成「全都缺席」。实测过一遍才发现。
SELF_SRC="${BASH_SOURCE[0]:-$0}"
cd "$(dirname "$SELF_SRC")/.." || exit 1
ROOT="$PWD"
LOGDIR="tmp/render-b3"
mkdir -p "$LOGDIR"

# ---------------------------------------------------------------------------
# 渲前串行化判据（2026-10-08 立）
#
# 这一段此前只活在 tmp/wait-for-settle-then-render-荒凉山庄.sh 那一份**当期手写**的脚本里。
# 手写一次的代价是下一期照旧裸跑，而裸跑在这台机器上今天真的翻过车：
#  ⑴ 13:13《昆虫记》六次 attempt 全 `ENOTEMPTY … /private/tmp/npm-cache/_npx/<hash>/node_modules/
#     get-uri`：两个会话同时 `npx hyperframes render`，抢同一份 npm 缓存里的同一棵 _npx 安装树。
#     谁都不报错，只是永远渲不出文件，而这条链会把六次机会全烧在同一个竞态上。
#  ⑵ 2026-10-05《李尔王》／2026-10-06：另一个会话正在改写共享真源时开渲，成片是「一半旧一半新」
#     的像素，十道门禁照绿——按仓库规矩只有重渲一条解法。判据＝这批文件连续 SETTLE_SECONDS
#     没有任何 mtime 变化（用的是**最后一次改动距今多久**，不是「我开始等了多久」：后者让一批
#     十期每期都白等 240 秒，而机器早就静了四十分钟）。
#  ⑶ 本书封层现在有**三个**读者（兜底渲染器、门 14、水波门的逐帧足迹），几何／时刻／动效
#     只有 scripts/lib/book-cover.json 一个真源。只要有一侧还停在旧形状，渲出来的那 1.4 秒
#     就对着一个算错预期的门——要么门红，更糟的是门绿而像素是第三种形状（2026-10-08 13:41~13:44
#     三方是几分钟内逐个落地的，中间任何时刻开渲都是第三种形状）。
#
# 为什么互斥锁和 others_rendering() 两个都要留：锁只约束**走这条链**的会话；一个裸
# `npx hyperframes render` 或另一个仓库的批处理根本不看 ${RENDER_LOCK_DIR}，只有 pgrep 那
# 双眼睛能看见它。反过来，锁解决的是「同一台机器上两个都守规矩的批」之间 20 秒轮询窗口
# 里的竞态——轮询判据是「此刻没人」，锁才是「这段时间归我」。
#
# 缓存目录钉在仓库内（$ROOT/tmp/npm-cache-hf），不是 /private/tmp/npm-cache：后者是全局的，
# 任何一份 shell 配置、任何一个别的仓库都会撞进来，而仓库内这份的清理跟着 tmp/ 一起走。
# 不在共享脚本里硬写代理——代理是机器属性，交给调用方的环境（本仓库另有一条 memory 记着
# 本机取图必须走代理，那是 scripts/fetch-*.py 的事，不是渲染的事）。
# ---------------------------------------------------------------------------
SETTLE_SECONDS="${HF_SETTLE_SECONDS:-240}"
SETTLE_POLL_SECONDS="${HF_SETTLE_POLL_SECONDS:-20}"
SETTLE_MAX_WAIT="${HF_SETTLE_MAX_WAIT:-21600}"
# 复查那一拍的长度也必须可注入：它和上面三个一样是「等多久」，写死 15 秒的话
# scripts/tests/test-render-serialization.py 里每一趟 settle 正例都要白等 15 秒，
# 而一条要跑两分钟的回归不会被跑第二遍——判据就从常驻变成摆设。
SETTLE_RECHECK_SECONDS="${HF_SETTLE_RECHECK_SECONDS:-15}"
# 跳过判据必须带理由，且理由长度要过下限：`HF_SETTLE_BYPASS=1` 是「我不想等」，不是理由。
# 和 --allow-overloaded-opener + opener_override_note 同一套约定——例外落在盘上，不落在我俩的对话里。
BYPASS_MIN_REASON_CHARS="${HF_SETTLE_BYPASS_MIN_REASON_CHARS:-8}"
NPM_CACHE_DIR="${HF_NPM_CACHE_DIR:-$ROOT/tmp/npm-cache-hf}"
export npm_config_cache="$NPM_CACHE_DIR"
RENDER_LOCK_DIR="${HF_RENDER_LOCK_DIR:-$ROOT/tmp/render.lock}"
LOCK_MAX_WAIT="${HF_LOCK_MAX_WAIT:-1800}"
LOCK_POLL_SECONDS="${HF_LOCK_POLL_SECONDS:-10}"

# 参与「静没静」判定的共享真源。漏一个就等于那个文件可以被随便改而没人重新计时——
# check-water-ripple.py 是 2026-10-08 才补进来的那条：水波门的逐帧足迹现在按 lib 的 motion
# 推封面掩码，它和渲染器不同步，渲出来的 1.4 秒就对着一个算错预期的门。
WATCH_FILES=(
  "templates/shared-video-template/intro/index.html"
  "scripts/lib/intro-template.json"
  "scripts/create-episode-preview.mjs"
  "scripts/render_scene_map_template_fallback.py"
  "scripts/render-episode-final.mjs"
  "scripts/render-hook-batch.sh"
  "scripts/check.mjs"
  "scripts/check-luminance-flicker.py"
  "scripts/check-episode-delivery.py"
  "scripts/check-water-ripple.py"
  "scripts/lib/water_ripple.py"
  "scripts/lib/water-ripple.json"
  "scripts/lib/book-cover.json"
  "scripts/lib/book_cover.py"
  "scripts/check-book-cover-overlay.py"
  "scripts/lib/disclaimer.json"
  "scripts/lib/disclaimer.py"
  "scripts/lib/title-font.json"
  "scripts/lib/title_font.py"
  "scripts/lib/caption_speech.py"
  "scripts/composition-digest.py"
  "scripts/check-render-artifacts.py"
  "scripts/check-hook-frame-match.py"
  "scripts/check-axis-before-render.py"
)
# 缺席必须当「正在被改写」而不是「跳过」：渲染器被删掉的那一秒开渲，撞上的是一期都没有像素。
REQUIRED_SOURCES=(
  "scripts/create-episode-preview.mjs"
  "scripts/render_scene_map_template_fallback.py"
  "scripts/lib/book-cover.json"
  "scripts/lib/book_cover.py"
  "scripts/check-book-cover-overlay.py"
  "scripts/check-water-ripple.py"
  "scripts/lib/water_ripple.py"
  "scripts/composition-digest.py"
)

watch_snapshot() {  # watch_snapshot <仓库根> → 每行 "<mtime> <path>"
  local root="$1" f
  for f in "${WATCH_FILES[@]}"; do
    if [ -f "$root/$f" ]; then
      stat -f "%m %N" "$root/$f"
    else
      echo "0 $root/$f"
    fi
  done
}

watch_missing() {  # watch_missing <仓库根> → 列出 REQUIRED_SOURCES 里此刻不存在的
  local root="$1" f
  for f in "${REQUIRED_SOURCES[@]}"; do
    [ -f "$root/$f" ] || echo "$f"
  done
}

watch_newest_mtime() {  # watch_newest_mtime <快照> → 最后一次改动（绝对秒）
  awk 'BEGIN{m=0} {if ($1+0 > m+0) m=$1} END{print m+0}'
}

# 判据钉在**可执行体**上，不是命令行字符串。pgrep -fl 量的是整条命令行，而本机同时开着好几个
# 会话，其中一个正在用 `zsh -c 'for i in …; do grep -c zoom_at …/render_scene_map_template_fallback.py; …'`
# 这类轮询监控同一批共享真源：参数里带着渲染器文件名，却一行像素都不渲。2026-10-08 13:47 上一版
# 判据就把「机器上没人渲」读成「有他期在渲」，队列原地空等（pgrep 命中那条 zsh）。
# 所以现在取 pid 的 comm：只有解释器／包管理器本身（node / npx / npm / python*）在跑，且参数里
# 出现 hyperframes 或兜底渲染器，才算忙。参数仍宽到「任何碰 hyperframes 的 npm」——那是今天
# 13:13 缓存锁被掏空的教训，不收窄。
# 书名自排除走 awk 的 index()，不去 grep 转义后的命令行：`ps -o args=` 会把中文转义成 M-xx，
# 拿书名去匹配那份永远匹配不上，于是「自排除」变成「自阻塞」。
others_rendering() {  # others_rendering <本期书名> → 有他期在渲时把那些进程打到 stdout
  local book="${1:-}" line pid cm
  pgrep -fl "hyperframes|render_scene_map_template_fallback\.py" 2>/dev/null \
    | awk -v self="$book" 'self != "" && index($0, self) > 0 { next } { print }' \
    | while IFS= read -r line; do
        [ -z "$line" ] && continue
        pid="${line%% *}"
        cm="$(ps -o comm= -p "$pid" 2>/dev/null)"
        cm="${cm##*/}"
        case "$cm" in
          node|npx|npm|Python|python|python3*) ;;
          *) continue ;;
        esac
        echo "$pid $cm ${line#* }"
      done
}

# 本书封层的三方一致：lib 声明了动效，渲染器、门 14、水波门就必须都已经读它。
# lib 解析失败（有人正在写这个 json）与 motion=none（本来就不需要动效）两种情况分开处理。
cover_layer_ready() {  # cover_layer_ready <仓库根> → 0 一致 / 1 分叉或正在写
  python3 - "$1" <<'PY'
import json, os, re, sys
root = sys.argv[1]
try:
    cfg = json.load(open(os.path.join(root, "scripts/lib/book-cover.json"), encoding="utf-8"))
except Exception as e:
    print(f"lib/book-cover.json 此刻解析失败（{type(e).__name__}: {e}）——有人正在写它")
    sys.exit(1)
typ = str((cfg.get("motion") or {}).get("type", "none"))
if typ in ("", "none"):
    print("封面动效：lib 未声明（none），渲染器／门禁无需改动")
    sys.exit(0)
# 三个读者各自要引用的 API：少一个就是「渲的是一版动效、门按另一版取证」。
readers = (
    ("render_scene_map_template_fallback.py", (r"cover_frame_sequence", r"_cover_motion")),
    ("check-book-cover-overlay.py", (r"zoom_at",)),
    ("check-water-ripple.py", (r"zoom_at", r"per_frame_cover_masks")),
)
bad = []
for rel, pats in readers:
    p = os.path.join(root, "scripts", rel)
    try:
        src = open(p, encoding="utf-8").read()
    except OSError:
        bad.append(f"{rel} 读不到")
        continue
    for pat in pats:
        if not re.search(pat, src):
            bad.append(f"{rel} 没有引用 {pat}")
lib = open(os.path.join(root, "scripts/lib/book_cover.py"), encoding="utf-8").read()
if "zoom_in" in typ and "def zoom_at" not in lib:
    bad.append("lib/book_cover.py 没有 zoom_at")
if bad:
    print("封面动效三方不一致：" + "；".join(bad))
    sys.exit(1)
print(f"封面动效三方一致：lib motion.type={typ}，渲染器／门 14／水波门共读同一真源")
PY
}

bypass_reason_ok() {  # bypass_reason_ok → 0 给了真理由并打印它 / 1 没 bypass 或理由太短
  local reason="${HF_SETTLE_BYPASS:-}"
  [ -z "$reason" ] && return 1
  if [ "${#reason}" -lt "$BYPASS_MIN_REASON_CHARS" ]; then
    echo "[settle] ⚠️ HF_SETTLE_BYPASS 给了 '$reason'：不足 $BYPASS_MIN_REASON_CHARS 个字符。"
    echo "[settle]        跳过判据要写**为什么**（哪一期、为什么已确认无他期），不是写「我不想等」——继续等"
    return 1
  fi
  echo "[settle] ⚠️ 已跳过渲前串行化判据，理由（落在本期日志里）：$reason"
  return 0
}

settle_before_render() {  # settle_before_render <书名> [<仓库根>] → 0 可以渲 / 1 等不到
  # 静默判据取「最后一次改动距今多久」，不取「我已经连续看了多久」：后者让一批十期每期都
  # 白等 SETTLE_SECONDS，而机器往往早就静了四十分钟——一条总要付息的判据会被绕过，绕久了就删掉。
  # 代价说清楚：静默窗口只覆盖 WATCH_FILES 那 24 个真源，别人在改**没列进来的**文件时这里看不见。
  # 所以名单要跟着新的共享真源长（check.mjs 钉住水波门、封层 lib、渲染器、预览生成器不许被摘掉）。
  local book="$1" root="${2:-$ROOT}" stamp stamp2 last newest quiet BUSY BUSY2 MISS MISS2 t0
  if bypass_reason_ok; then
    return 0
  fi
  t0=$(date +%s)
  last=""
  while :; do
    BUSY="$(others_rendering "$book")"
    MISS="$(watch_missing "$root")"
    if [ -n "$BUSY" ]; then
      echo "[settle] $(date '+%T') 有他期渲染在跑，等：$(echo "$BUSY" | head -2 | tr '\n' ' ')"
      last=""
    elif [ -n "$MISS" ]; then
      echo "[settle] $(date '+%T') 共享真源缺席（有人正在重构），等：$(echo "$MISS" | tr '\n' ' ')"
      last=""
    elif ! cover_layer_ready "$root"; then
      echo "[settle] $(date '+%T') 本书封层真源还没落齐，等它三方对齐"
      last=""
    else
      stamp="$(watch_snapshot "$root")"
      if [ -n "$last" ] && [ "$stamp" != "$last" ]; then
        # mtime 没变而快照变了＝有真源被删掉或新建（0 那行），也是「正在改」的一种
        echo "[settle] $(date '+%T') 共享真源集合刚被改写过，重新取证"
        last="$stamp"
      else
        last="$stamp"
        newest="$(echo "$stamp" | watch_newest_mtime)"
        quiet=$(( $(date +%s) - newest ))
        if [ "$quiet" -lt "$SETTLE_SECONDS" ]; then
          echo "[settle] $(date '+%T') 真源最后改动距今 ${quiet}s < ${SETTLE_SECONDS}s，继续观察"
        else
          # 连续两轮复查：静默窗口本身可能被一次刚落盘的写入或刚 fork 的渲染骗过。
          echo "[settle] $(date '+%T') 真源已静默 ${quiet}s，复查他期渲染＋封层真源…"
          sleep "$SETTLE_RECHECK_SECONDS"
          BUSY2="$(others_rendering "$book")"
          MISS2="$(watch_missing "$root")"
          stamp2="$(watch_snapshot "$root")"
          if [ -n "$BUSY2" ]; then
            echo "[settle] $(date '+%T') 复查发现新起的渲染，继续等：$(echo "$BUSY2" | head -1)"
            last=""
          elif [ "$stamp2" != "$stamp" ] || [ -n "$MISS2" ] \
               || ! cover_layer_ready "$root"; then
            echo "[settle] $(date '+%T') 复查发现真源又变了／又缺席了／封层又分叉了，继续等"
            last=""
          else
            echo "[settle] ✅ $(date '+%T') 机器已静默 $(( $(date +%s) - newest ))s、无他期渲染、" \
                 "封面三方一致、npm 缓存=$npm_config_cache —— 开跑 $book"
            return 0
          fi
        fi
      fi
    fi
    if [ $(( $(date +%s) - t0 )) -ge "$SETTLE_MAX_WAIT" ]; then
      echo "[settle] ❌ 等了 $(( $(date +%s) - t0 ))s 还没静下来 —— 本期不渲、不进门禁" \
           "（不是缺陷，是这台机器上有人在改共享层；要么等，要么给 HF_SETTLE_BYPASS 写一条真理由）"
      return 1
    fi
    sleep "$SETTLE_POLL_SECONDS"
  done
}

render_lock_acquire() {  # render_lock_acquire <标签> → 0 拿到锁 / 1 等不到
  local label="$1" t0 waited owner pid
  mkdir -p "$(dirname "$RENDER_LOCK_DIR")"
  t0=$(date +%s)
  while :; do
    if mkdir "$RENDER_LOCK_DIR" 2>/dev/null; then
      printf '%s\t%s\t%s\t%s\n' "$$" "$label" "$(date '+%F %T')" "$ROOT" \
        > "$RENDER_LOCK_DIR/owner"
      return 0
    fi
    owner=""
    [ -f "$RENDER_LOCK_DIR/owner" ] && owner=$(head -1 "$RENDER_LOCK_DIR/owner" 2>/dev/null)
    pid="${owner%%	*}"
    case "$pid" in
      ""|*[!0-9]*) pid="" ;;
    esac
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      echo "[lock] 接管陈旧锁：持有者已不在（${owner}），${RENDER_LOCK_DIR}"
      rm -rf "$RENDER_LOCK_DIR"
      continue
    fi
    waited=$(( $(date +%s) - t0 ))
    if [ "$waited" -ge "$LOCK_MAX_WAIT" ]; then
      echo "[lock] ❌ 等了 ${waited}s 没拿到渲染锁（持有者：${owner:-未知}）——" \
           "npx 缓存正被别的会话占着，本期不进门禁"
      return 1
    fi
    echo "[lock] 已等 ${waited}s，锁被占（持有者：${owner:-未知}），${LOCK_POLL_SECONDS}s 后重试"
    sleep "$LOCK_POLL_SECONDS"
  done
}

render_lock_release() {
  rm -rf "$RENDER_LOCK_DIR" 2>/dev/null || true
}

# 拿锁跑一条命令，跑完放锁。存在的理由只有一个：**渲染不是唯一会碰 _npx 树的调用点**——
# `npm run check` 也跑 `npx --yes hyperframes@… lint/validate/inspect`，用的是同一棵树。
# 13:13《昆虫记》那六次 ENOTEMPTY 是「两个 npx 抢一棵树」，只在渲染侧排队等于修了一半：
# 一边排队、一边直冲，撞车照旧。所以锁的协议（含陈旧锁接管、超时文案、仓库内缓存 export）
# 留在这一个文件里，check.mjs 通过 source + 调这个函数来复用，而不是在 Node 里再写一份
# mkdir 锁——两份实现对「锁归谁」的口径迟早会漂，而漂了的一切照绿。
# 用法：render_lock_run <标签> <工作目录> <命令> [参数…]
#   → 0..254 = 被包命令的退出码；75 = 没拿到锁（调用方必须把它和「命令自己失败」分开报，
#     否则一次别人正在渲染就会伪装成一次模板有问题）
render_lock_run() {
  local label="$1" dir="$2" rc=0
  shift 2
  if ! render_lock_acquire "$label"; then
    return 75
  fi
  ( cd "$dir" && "$@" ) || rc=$?
  render_lock_release
  return "$rc"
}


render_segment() {  # render_segment <段名> <工程目录> [<到期数目录的持久对账单>]
  local seg="$1" dir="$2" keep="${3:-}" out attempt before after scan_rc blarg counts red_n det_n ep tag
  # 伪影明细和 baseline 都必须按**期**命名。2026-10-07 量这次栅格竞态时才发现：
  # 两者都叫 .artifact-<段>*，一批里下一期的同段会覆盖上一期的证据（我一度以为手里那份
  # 《人类群星闪耀时》的明细就是它自己的，实际是《无名的裘德》跑完后盖上去的），
  # 更糟的是 baseline——上一期渲坏的 mp4 会被当成这一期「上一次渲染」，于是把一次偶发的
  # 半张瓦片读成「同一帧再现＝设计内」，正好是给用户的闪现买单条豁免的那类误判。
  ep=$(basename "$(dirname "$(dirname "${keep:-$dir}")")")
  tag="$LOGDIR/.artifact-$ep-$seg"
  out="$dir/renders/$seg.mp4"
  # 渲染**之前**先给工程目录按一次指纹。它和渲染之后的那一次是同一件事的两端：
  # 一次 hyperframes render 要跑几十秒，而这段时间里同期别的会话完全可能把同一份
  # index.html 重写掉（2026-10-05《李尔王》第四次报闪屏的真源就是这么来的——
  # index.html 改于 10:10:42，渲染 10:10:58 收尾，交付片带的是改前像素，十道门禁全绿，
  # 因为每一条问的都是「画面对不对」，没有一条问「这份像素是谁渲出来的」）。
  before=$(python3 scripts/composition-digest.py digest "$dir") || {
    echo "[render] ❌ $seg 渲之前算不出工程指纹：$dir —— 本期不进门禁"
    return 1
  }
  for attempt in 1 2 3 4 5 6; do
    if [ -f "$out" ]; then
      echo "[render] $seg 已有上一次的产物，先删掉再渲（留着就会拿旧像素过门禁）"
      rm -f "$out"
    fi
    # 锁只圈住 `npm run render` 这一句，不圈整段重试：一次 render 几十秒，六次 attempt 加
    # 兜底渲染器有十几分钟，把锁持有到底等于一个人占着整台机器。真正会和别人撞的只有
    # npx 那棵 _npx 安装树，所以互斥范围就取它——其余时间让别的会话渲。
    if ! render_lock_acquire "$ep/$seg #$attempt"; then
      echo "[render] ❌ $seg 拿不到 npx 渲染锁 —— 本期不进门禁（原因见上一行）"
      return 1
    fi
    ( cd "$dir" && npm run render 2>&1 | tail -4 )
    render_lock_release
    if [ -f "$out" ]; then
      after=$(python3 scripts/composition-digest.py digest "$dir")
      if [ "$after" != "$before" ]; then
        echo "[render] $seg 第 $attempt 次：页面在渲染进行中被改写过"
        echo "[render]        渲前 $before"
        echo "[render]        渲后 $after"
        echo "[render]        —— 这份 mp4 里的像素既不是渲前那版也不是渲后那版，不作数，重渲"
        rm -f "$out" "$dir/renders/$seg.provenance.json"
        [ -n "$keep" ] && rm -f "$keep"
        sleep 3
        before="$after"          # 下一轮以**当前**这份组成为准；它再被改就再重渲
        continue
      fi
      # 第四道断言（用户 2026-10-06 第①条规则，2026-10-07 才真的接上）：**这一份渲本身体**可以是脏的。
      # headless Chrome 偶尔在某帧只栅格化了一半合成层，透出下面的黑底——单帧亮度脉冲、下一帧立刻
      # 回落。交付门禁量不到它：《一颗简单的心》2026-10-07 那一记底带 38.3→27.5→38.3（钩子段第
      # 202/203 帧，y 880..959 从照片塌成 15..19 的平场）在第 10 道门里读成开场峰值 2.4、内容跳变
      # 3.1、越带 0 处，十三道全绿，而它就是用户报的「钩子视频闪现」。
      # 豁免来自**组合自己声明的单帧刀口**（--composition），不是「另一次渲染也有」，也不是
      # 「组合里出现过这个时刻」：能造出「跳一帧又立刻跳回」的只有 `tl.set`、`tl.call`、写了
      # duration 且 ≤ 一帧半的 tween（片头 `.template-blackout` 的 0.04s 砸黑一拍），以及
      # revealCaption 的**收场**。渐变（字幕 0.16s 出场、照片 4~5.5s 推镜）在帧序列上是单调的，
      # 它给不出回头，所以一律不豁免——第二版按窗长展开豁免，r3 那次 t=5.03s 的底带塌陷（r1/r2
      # 同一帧都干净）就被 4.69s 的字幕淡入买成了「设计内」，而那一记正是用户报的闪现本身。
      # 为什么放弃上一版的 baseline 判据：实测这记底带塌陷在连续两次渲染里落在同一帧同号（像素值都
      # 一样：38.3→27.5），被「可复现＝设计内」放过去，于是「四次都过扫描」的成片仍然带着闪现。
      # 反过来它又不是确定发生的——同一份组成连渲三次是 r1 有、r2 干净、r3 又在那一帧有：栅格化
      # 调度让**某一帧特别容易**没画完，所以「两次同帧」既不能证明是设计，也不能证明重渲没用。
      # baseline 于是只留作取证（打印几处是上一轮同帧再现），判决全交给组合声明的刀口。
      blarg=""
      [ -f "$tag-baseline.mp4" ] && blarg="--baseline $tag-baseline.mp4"
      python3 scripts/check-render-artifacts.py "$out" --json \
        --composition "$dir/index.html" ${blarg:+$blarg} \
        > "$tag.attempt$attempt.json"; scan_rc=$?
      cp -f "$tag.attempt$attempt.json" "$tag.json"
      if [ "$scan_rc" -ne 0 ]; then
        counts=$(python3 -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(len(d.get("pulses") or []), len(d.get("deterministic") or []))' \
          "$tag.json") || counts="0 0"
        red_n=${counts%% *}
        det_n=${counts##* }
        cp -f "$out" "$tag-baseline.mp4"
        rm -f "$out" "$dir/renders/$seg.provenance.json"
        [ -n "$keep" ] && rm -f "$keep"
        echo "[render] $seg 第 $attempt 次有单帧合成伪影 ${red_n} 处（其中 ${det_n} 处上一次渲染也有同一帧）"
        echo "[render]        明细 $tag.attempt$attempt.json —— 重渲并重扫"
        sleep 2
        continue
      fi
      rm -f "$tag-baseline.mp4"
      echo "[render] $seg OK（第 $attempt 次，组成指纹 ${before}，单帧伪影扫描干净）"
      # 落盘产物↔组成的对账单：本仓库没有 git，这是唯一一份能说明「画面里这些像素
      # 由哪一版组成渲出来」的东西（生成器指纹一起记，因为生成器改了而没人重新生成
      # 工程目录，落盘的 index.html 还是旧形状——《恶意》2026-10-01 那版书名层就是）。
      # --also 不是冗余：预览目录的 renders/ 会被下一次 create-episode-preview.mjs 整片 rm -rf
      # （AGENTS.md 明写的坑），账本跟着它作证的那份像素一起消失就再也读不回来，所以必须
      # 到期数目录（交付的成片住在那里，整目录搬去备份/时也带着它）留一份。
      python3 scripts/composition-digest.py record "$dir" --video "$out" \
        --out "$dir/renders/$seg.provenance.json" ${keep:+--also "$keep"} || return 1
      return 0
    fi
    echo "[render] $seg 第 $attempt 次没产出 $out"
    sleep 3
  done
  echo "[render] ❌ $seg 六次都没渲出来（或撞上页面被改写／六次都带组合刀口解释不了的单帧伪影）—— 本期不进门禁，不进汇总"
  return 1
}

# 2026-10-08 加这道渲前轴闸。第五批有三期（夜莺与玫瑰 / 呐喊 / 动物农场）各花约十分钟渲完三段，
# 然后在第七道门「逐行盖住自身语音」翻红——红的不是像素，是**轴**：那三期的 body-timings.json
# 没跑 snap/audit 两道 pass（audit dry-run 报 changes 34、late 2→0），而这条链上没有任何一步
# 在渲染之前问过轴一句。更糟的是夜莺与玫瑰：轴在 11:13 被补好了，成片却是 11:06 按补之前的轴
# 渲的，交付轨 (caption-track.json) 于是和轴对不上——行63 轴 261.35 起、轨 262.05 起，
# 第七道门量的是轨，所以它红得对，可真正的修法只是「拿现在这份轴重渲一次」。
# 轴侧的三道对账（token 派生 / 逐行覆盖 / 能量边界残留）都是秒级到十几秒的检查，一次十分钟的
# 整片渲染不是它们的对照组，所以这里先量轴、轴红就按 AGENTS.md 的既有次序就地修一轮
# （snap --apply → audit --apply ×2，第二 pass 是行首规则必须跑两次的那条），修完再量一遍，
# 还不绿就不渲——把「十分钟之后才知道轴是错的」换成「十秒之内决定渲不渲」。
axis_guard() {  # axis_guard <书名> → 0 可以渲 / 1 本期跳过
  # 判据和修轴次序都在 scripts/check-axis-before-render.py 里，这里只接线。
  # 不在 bash 里重述一遍阈值——那会变成第四个问「轴对不对」却和第七道门口径不同的地方。
  python3 scripts/check-axis-before-render.py "$1"
}

# 测试缝合：`source` 本文件时（HF_SOURCE_ONLY=1）只取上面那些函数，不开批。
# 为什么要有这个开关而不是让测试去猜命令行：source 一份带顶层循环的脚本会**立刻**用调用方的
# 位置参数跑一期（2026-10-08 写回归时真的踩了一次——`source … 测试书名` 直接进到 settle 等待循环，
# 而 $0 在 `bash -c` 里是 "bash"，dirname 把 ROOT 解析到仓库的上一级）。这不是产品旁路：
# 渲染仍然只有 `bash scripts/render-hook-batch.sh <书名>` 这一条入口。
if [ -n "${HF_SOURCE_ONLY:-}" ]; then
  return 0
fi

for EPISODE in "$@"; do
  LOG="$LOGDIR/$EPISODE.log"
  rc=0
  {
    echo "===== $(date '+%F %T') 开始 $EPISODE ====="
    START_TS=$(date +%s)
    # 到期数目录的持久对账单先清空：本期一旦没渲成，留着上一代的记录就等于给已经不存在的
    # 像素作证（verify 会把它读成「生成器一致」，而磁盘上其实是更早的一版成片）。
    rm -f "episodes/$EPISODE/renders/hook.provenance.json" \
          "episodes/$EPISODE/renders/intro.provenance.json"
    # 预览脚本把工程目录写在 stdout 最后一行，其余进度信息走 stderr，
    # 所以分开收：stdout 进临时文件只取最后一行，stderr 直接进日志。
    PREV=""
    # 渲前串行化判据排在轴闸之前：轴闸十几秒就能判完，但它在**修轴**（snap/audit 落盘）时
    # 会改 episodes/ 里的东西，而 settle 量的是共享真源——先等机器静下来，再决定渲不渲。
    if ! settle_before_render "$EPISODE"; then
      echo "[FATAL] $EPISODE 渲前串行化判据没过 —— 本期不渲、不进门禁（等待轨迹见上面 [settle] 行）"
    elif ! axis_guard "$EPISODE"; then
      echo "[FATAL] $EPISODE 轴闸没过 —— 本期不进门禁（原因见上一行）"
    else
      node scripts/create-episode-preview.mjs "$EPISODE" > "$LOGDIR/.preview-stdout" 2>>"$LOG"
      PREV=$(tail -1 "$LOGDIR/.preview-stdout")
    fi
    if [ -z "$PREV" ]; then
      echo "[FATAL] 轴闸拦下，没生成预览目录 —— 跳过本期（不 exit，否则会带走整批）"
      rc=1
    elif [ ! -d "$PREV" ]; then
      echo "[FATAL] 预览目录没生成：'$PREV' —— 跳过本期（不 exit，否则会带走整批）"
      rc=1
    else
      echo "[preview] $PREV"
      # 渲几段看 script-hook.csv 在不在，和 create-episode-preview.mjs 的 showBookMark、
      # 兜底渲染器的 HAS_HOOK 同一个判据。2026-10-07 重渲两段片《荆棘鸟》（2026-09-27 交付，
      # 还没有钩子段）时，这里无条件渲 hook，报出来的是
      # 「工程目录不存在：tmp/preview-book-…/hook」+「hook 渲之前算不出工程指纹」——
      # 一句读起来像「素材缺失」的话，实际是驱动假设了每部片都有钩子段。生成器本来就没给
      # 两段片造 hook 工程，渲染器也按 hook_dur=0 走（PRE_BODY 退回 INTRO_TRIM，水波/字幕
      # 采样/门 11 的无从证明都跟着对齐），所以缺的那一段不是缺陷，是本期结构。
      HAS_HOOK=no
      if [ -f "episodes/$EPISODE/script-hook.csv" ]; then HAS_HOOK=yes; fi
      echo "[segments] $HAS_HOOK"
      if { [ "$HAS_HOOK" = no ] \
           || render_segment hook "$PREV/hook" "episodes/$EPISODE/renders/hook.provenance.json"; } \
         && render_segment intro "$PREV/intro" "episodes/$EPISODE/renders/intro.provenance.json"; then
        ls -l "$PREV/intro/renders/intro.mp4"
        [ "$HAS_HOOK" = yes ] && ls -l "$PREV/hook/renders/hook.mp4"
        # 渲染器的完整输出落盘，屏幕只看尾部：`| tail -30` 会把崩溃行本身吞掉，
        # 而管道退出码是 tail 的，所以下面必须用 PIPESTATUS 取 python 那一段的 rc。
        EPISODE="$EPISODE" INTRO_TRIM_SECONDS=3.0 PREVIEW="$PREV" \
          FALLBACK_TMP="tmp/${EPISODE}_tpl" \
          python3 scripts/render_scene_map_template_fallback.py > "$LOGDIR/.renderer-$EPISODE.out" 2>&1
        rc_render=${PIPESTATUS[0]}
        tail -30 "$LOGDIR/.renderer-$EPISODE.out"
        # 断言「成片是本次渲出来的」，否则**不跑门禁**。门禁量的是磁盘上的成片：渲染器
        # 一崩，它对着一小时前的旧像素照样能打分，打出来的颜色和新装配层的对错无关
        # （2026-10-04《一地鸡毛》：兜底渲染器在字幕遮罩的 rawvideo 管道上短包崩溃，
        # renders/ 里还是 14:56 那版旧片头，16:17 的门禁把旧像素判成 ❌判据③，
        # 看起来像刚落的装配层改动有缺陷，实际红绿都来自上一份产物）。
        NEWEST=""
        for cand in "episodes/$EPISODE/renders/"*.mp4; do
          [ -f "$cand" ] || continue
          mt=$(stat -f %m "$cand")
          if [ -z "$NEWEST" ] || [ "$mt" -gt "$(stat -f %m "$NEWEST")" ]; then NEWEST="$cand"; fi
        done
        if [ "$rc_render" -ne 0 ]; then
          echo "[FATAL] 兜底渲染器 rc=${rc_render}（完整输出 $LOGDIR/.renderer-$EPISODE.out）—— 本期不进门禁"
          rc=1
        elif [ -z "$NEWEST" ] || [ "$(stat -f %m "$NEWEST")" -lt "$START_TS" ]; then
          echo "[FATAL] renders/ 里没有比本次运行（$(date -r "$START_TS" '+%T')）更新的成片${NEWEST:+，最新仍是 $NEWEST}—— 渲染器没出片，本期不进门禁"
          rc=1
        else
          echo "----- 交付门禁 -----"
          python3 scripts/check-episode-delivery.py "$EPISODE" \
            --tmp "tmp/${EPISODE}_tpl" --intro-trim 3.0 2>&1
          rc=$?   # 汇总读的是门禁自己打的 ✅/❌，这里只留个粗信号；退出码在本机不可全信
          # 门禁之后读那份对账单：十道门问的都是「画面对不对」，这一道问的是
          # 「这些像素是不是此刻这一代生成器渲出来的」。它一红就说明共享层在渲染之后
          # 又被改过（本期带的还是上一代形状），除了重渲没有别的修法。
          echo "----- 组成摘要对账 -----"
          python3 scripts/composition-digest.py verify "$EPISODE" 2>&1
          rc_verify=$?
          if [ "$rc_verify" -ne 0 ]; then
            echo "[FATAL] 组成摘要对账 rc=$rc_verify —— 交付像素与当前生成器不一致，本期不算交付完成"
            rc=$rc_verify
          fi
        fi
      else
        rc=1
      fi
    fi
    echo "===== $(date '+%F %T') 结束 $EPISODE (rc=$rc) ====="
  } > "$LOG" 2>&1
  echo "[done] $EPISODE -> $LOG (rc 见日志末行)"
done
