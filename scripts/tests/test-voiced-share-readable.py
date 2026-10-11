#!/usr/bin/env python3
"""T2 的「读完所需的富余停留不计空放」回归测试：短行要能放行，长停留必须照旧判红。

为什么钉这一条（2026-10-04《人性的枷锁》行 72「而是：」）：
这一行 2 个字，自己的语音只有 197.68–197.82 那 0.14 秒，可它必须在上屏待满 0.55 秒才
读得完。行尾被下一行的行首 197.95 顶死，行首只能收回上一句的停顿里（196.97–197.68 全程
听不见人声），于是任何轴移动都追不上 45%：把窗口收到 0.55 秒，密度也只有 42%；把窗口放到
0.90 秒（snap 的可读地板），密度掉到 26%。门禁在这里等于要求「一行字少到读不完、又不许
多待一秒」，判红的是口径，不是时间轴——和《奥兰多》行 42 那次一样。

修法是给 `lib/caption_speech.voiced_share()` 加一个 `readable` 参数（调用方传规则 A 同一个
`reading_need(字数)`），把**落在真空静上、且不超过 `readable − 本行自己念出来的秒数`** 的那截
富余停留不计空放。六个方向一起钉死，并自带双向对照：
  ① 行 72 的几何：旧口径必须判红、新口径必须放行 —— 断言不是空转。
  ② 整窗压在真空静上的字幕（词根本不在屏上）：新口径照旧红，原始密度兜底线也照旧红。
  ③ 把行 72 的窗口拉长到 3.0 秒，且那多出来的 2.6 秒之后确实没有人声（另配一份突发）：
     新口径仍然红 —— 豁免只到「读完这一行所需」为止，不会被拿来洗一条赖着不走的字幕。
  ④ 15 字的长行需要 2.88 秒才能读完：多待的 1.6 秒静音在新口径下不计空放（这是设计）。
  ⑤ `readable ≤ 本行自己的语音时长`、或这行根本钉不出自身窗（own=None，锚点薄弱）时，
     必须与旧口径逐位相同 —— 正常长度的行和可疑的行都不受影响。
  ⑥ 不传 `readable` 时必须与旧口径逐位相同 —— 没接上的调用方不被这次改动波及。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.caption_speech import burst_share, reading_need, voiced_share  # noqa: E402

FLOOR, RAW_FLOOR = 0.45, 0.20       # 门禁 check-caption-speech-align.py 的 T2 两条线
# 《人性的枷锁》正文配音 194~201 秒的实测突发（lib.speech_bursts，阈值 −16.9 dBFS）
BURSTS = [(195.19, 196.68),       # 行 71「可能不是怎样获得幸福。」
          (197.68, 198.06),       # 行 72「而是：」自己的语音
          (198.55, 200.77),       # 行 73「当你终于不再执着于成为某一种人。」
          (201.38, 203.17), (203.72, 205.79)]
ROW72_OWN = (197.68, 197.82)      # audible_windows 给行 72 钉出的自身可听窗
ROW72_WIN = (197.01, 197.91)      # snap 按 0.90s 可读地板铺完之后的上屏窗


def t2(bursts, win, own, chars=None, old=False):
    """门禁 T2 的**完整**判据：voiced_share 低于 --min-voiced，或整窗原始密度低于 --min-raw。
    测试断言门禁的结论而不是其中一半的数值——豁免只动 voiced 那半，raw 那半照旧守着
    「整条字幕压在静音上就是空放」。"""
    cs, ce = win
    pad = None if (old or chars is None) else reading_need(chars)
    v = voiced_share(bursts, cs, ce, own, readable=pad)
    r = burst_share(bursts, cs, ce)
    return (v < FLOOR or r < RAW_FLOOR), v, r


def main():
    fails = []
    need72 = reading_need(2)                       # 2 字 → 0.55s 地板，不是 2/5.2=0.38s

    # ① 行 72：旧红新绿
    red_old, old72, _ = t2(BURSTS, ROW72_WIN, ROW72_OWN, old=True)
    red_new, new72, raw72 = t2(BURSTS, ROW72_WIN, ROW72_OWN, 2)
    if red_old is False:
        fails.append(f"① 对照失效：旧口径给行 72 打了 {old72*100:.0f}%，没有复现那次判红")
    if red_new:
        fails.append(f"① 新口径仍然判红行 72（窗内语音 {new72*100:.0f}% 原始 {raw72*100:.0f}%）："
                     "富余停留没被豁免，而这一行在轴上无处可挪")

    # ② 整窗压在停顿里、词根本不在屏上 → 两条线里 raw 那半照旧红
    if any(b0 < 193.90 and b1 > 193.00 for b0, b1 in BURSTS):
        fails.append("② 合成波形在 193.00–193.90 之间竟然有语音，夹具失效")
    parked_red, parked, parked_raw = t2(BURSTS, (193.00, 193.90), ROW72_OWN, 2)
    if not parked_red:
        fails.append(f"② 一条完全落在静音里的字幕被放行了（{parked*100:.0f}%）")
    if t2(BURSTS, (193.00, 193.90), ROW72_OWN, old=True)[0] is False:
        fails.append("② 对照失效：整窗静音在旧口径下竟然不是红")

    # ③ 赖着不走：2 字的行在上屏待满 3.0 秒，多出来的全是别人的停顿
    #    （另配一份突发：198.06 之后到 203 秒之间没有人声，才是「赖屏」而不是「压住别人的词」）
    bursts3 = [(197.68, 198.06), (203.00, 205.00)]
    long_red, long72, long_raw = t2(bursts3, (197.68, 200.68), ROW72_OWN, 2)
    if not long_red:
        fails.append(f"③ 2 个字的行把字幕挂了 3.0 秒仍被判绿（窗内语音 {long72*100:.0f}% "
                     f"原始 {long_raw*100:.0f}%）：豁免超过了「读完所需」，这条线白留了")

    # ④ 长行：15 字读完要 2.88s，多待的 1.6s 静音不该算空放
    own_long, win_long = (183.14, 184.14), (183.14, 185.74)   # 2.6s 窗，1.6s 是纯停顿
    bursts4 = BURSTS + [own_long]
    if not t2(bursts4, win_long, own_long, old=True)[0]:
        fails.append("④ 对照失效：15 字行在旧口径下本就绿，这条断言是空转")
    if t2(bursts4, win_long, own_long, 15)[0]:
        fails.append(f"④ 15 字行按读完所需多待 1.6 秒仍被判红（{t2(bursts4, win_long, own_long, 15)[1]*100:.0f}%）")

    # ⑤⑥ 不受影响的两类调用：readable 用不上 / 根本没传 readable
    for win, own, chars in [((195.19, 196.68), (195.19, 196.68), 11),
                            ((198.55, 200.77), (198.55, 200.77), 2),
                            (ROW72_WIN, None, 2),                    # 锚点薄弱：宁可严
                            ((179.03, 179.89), (179.03, 179.89), 4)]:
        if voiced_share(BURSTS, win[0], win[1], own,
                        readable=None if chars is None else reading_need(chars)) != \
           voiced_share(BURSTS, win[0], win[1], own):
            fails.append(f"⑤ readable={reading_need(chars):.2f} 在窗 {win} 上改变了旧结果")
    for win, own in [(ROW72_WIN, ROW72_OWN), ((195.19, 196.97), (195.19, 196.68)),
                     ((197.68, 200.68), ROW72_OWN)]:
        if voiced_share(BURSTS, win[0], win[1], own) != \
           voiced_share(BURSTS, win[0], win[1], own, readable=None):
            fails.append(f"⑥ 不传 readable 时结果变了（窗 {win}）—— 默认口径被这次改动波及")

    print(f"行72 旧 {old72*100:.0f}%红 → 新 {new72*100:.0f}%绿（原始密度 {raw72*100:.0f}% 仍守着 "
          f"{RAW_FLOOR*100:.0f}% 兜底线）   2 字读完需 {need72:.2f}s   "
          f"整窗静音 {parked*100:.0f}%/{parked_raw*100:.0f}% 仍红   "
          f"3s 赖屏 {long72*100:.0f}% 仍红   15字行 {t2(bursts4, win_long, own_long, 15)[1]*100:.0f}% 绿")
    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print("✅ 富余停留只豁免「读完所需」这一截：短行放行 / 整窗静音仍红 / 长停留仍红 / "
          "锚点薄弱行与不传参的调用方与旧口径逐位相同")


if __name__ == "__main__":
    main()
