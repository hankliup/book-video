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
富余停留不计空放。

⚠️ 2026-10-11 更新：本文件里的 `t2()` 以前只复现 T2 的一半。门禁现在的完整判据是
（`scripts/check-caption-speech-align.py` 178–182 行）：
    raw   = burst_share(bursts, cs, ce)                  # 自适应阈值证人（跟着本期响度走）
    quiet = raw < --min-raw  并且（接了电平时）above_noise_share(sil, cs, ce) < --min-raw
    red   = voiced_share(..., readable=…) < --min-voiced 或 quiet
绝对电平证人（−35 dBFS，`test-acoustic-presence.py`  owns 它自己的六臂）不是本次要测的东西，
但**它是同一条兜底线的第二个操作数**，所以这里要么整条复现、要么就是在给一个已经退休的形状
当证人。留着半条的后果当场就露出来了：旧②拿 193.00–193.90 当「整窗压在真空静上」，可实测
这段窗原始密度 0%、抬在噪声底之上 100%（这一期阈值 −16.9 dBFS 把那段呼吸整个判成无声）——
shipped 口径下它是**绿**的，那条对照早已不在对照任何东西。八个方向一起钉死，并自带双向对照：
  ① 行 72 的几何：旧口径必须判红、新口径必须放行 —— 断言不是空转。
  ② 一条真正压在停顿里、词根本不在屏上的字幕（改拿实测静音跑 196.95–197.65）：两条证人
     都说没有声音，新口径照旧红，旧口径（只看自适应密度）也照旧红。
  ②b 旧②那个 193.00–193.90 的窗在 shipped 口径下必须**是绿的** —— 把「为什么把②挪了位置」
     钉成断言，谁把它挪回去就红在这里，而不是悄悄留下一条永真的对照。
  ③ 把行 72 的窗口拉长到 3.0 秒，且那多出来的时间确实没有人声（突发与静音两份证人配成同
     一个形状）：新口径仍然红 —— 豁免只到「读完这一行所需」为止，不会被拿来洗一条赖着不走的字幕。
  ④ 15 字的长行需要 2.88 秒才能读完：多待的 1.6 秒静音在新口径下不计空放（这是设计）。
  ⑤ `readable ≤ 本行自己的语音时长`、或这行根本钉不出自身窗（own=None，锚点薄弱）时，
     必须与旧口径逐位相同 —— 正常长度的行和可疑的行都不受影响。
  ⑥ 不传 `readable` 时必须与旧口径逐位相同 —— 没接上的调用方不被这次改动波及。
  ⑦ 《美丽新世界》行 52「所以，」（实测夹具）：这条字幕**两半都要**才绿 —— 按掉富余停留
     就红在「窗内语音 3%」，按掉电平证人就红在兜底线，只有 shipped 口径放行。它是「豁免与
     证人相互独立、缺一即误杀」这件事的实物证据（合成轴只能造出「只需要一半」的行）。
  ⑧ 同一期行 86「所以，」（0.10 秒的窗，卡在两个突发之间那 0.19 秒的呼吸里）：只需要电平
     证人这一半 —— 按掉富余停留照旧绿、按掉电平证人翻红。⑦⑧ 一对照就排除掉「其实是同一
     条判据在起作用」这种读法。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.caption_speech import (above_noise_share, burst_share,  # noqa: E402
                                reading_need, voiced_share)

FLOOR, RAW_FLOOR = 0.45, 0.20       # 门禁 check-caption-speech-align.py 的 T2 两条线
# 《人性的枷锁》正文配音 194~201 秒的实测突发（lib.speech_bursts，阈值 −16.9 dBFS）
BURSTS = [(195.19, 196.68),       # 行 71「可能不是怎样获得幸福。」
          (197.68, 198.06),       # 行 72「而是：」自己的语音
          (198.55, 200.77),       # 行 73「当你终于不再执着于成为某一种人。」
          (201.38, 203.17), (203.72, 205.79)]
ROW72_OWN = (197.68, 197.82)      # audible_windows 给行 72 钉出的自身可听窗
ROW72_WIN = (197.01, 197.91)      # snap 按 0.90s 可读地板铺完之后的上屏窗
# 同一份 body-voiceover.mp3 的 silence_runs 实测切片（178~212s，−35 dBFS 噪声底证人）。
# 不是编的：②③④ 三处拿它当操作数，漂移了就红在夹具自证上，而不是红在一条假对照上。
SIL_SDL = [(178.44, 179.02), (179.47, 179.99), (180.51, 180.90), (181.34, 181.80),
           (182.35, 182.73), (183.31, 183.64), (184.45, 185.06), (186.63, 187.31),
           (188.74, 189.18), (190.90, 191.53), (196.95, 197.65), (200.82, 201.36),
           (205.83, 206.62), (209.76, 210.40)]
PARK_WIN = (196.95, 197.65)       # 行 71 说完到行 72 开口之间那截**两条证人都承认**的停顿
# 《美丽新世界》实测夹具（阈值 −16.4 dBFS）：行 52 / 行 86 都是 2 字的「所以，」
BN_52 = [(125.75, 126.85), (127.57, 128.07), (128.49, 130.54)]
SIL_52 = [(127.00, 127.35)]
WIN_52, OWN_52 = (127.24, 127.58), (127.57, 127.58)
BN_86 = [(215.60, 216.81), (217.60, 218.08)]
SIL_86 = [(216.98, 217.41)]
WIN_86, OWN_86 = (217.44, 217.54), (217.44, 217.54)


def t2(bursts, win, own, chars=None, old=False, sil=None):
    """门禁 T2 的**完整**判据：voiced_share 低于 --min-voiced，或者兜底线被跨过
    （原始密度低于 --min-raw，且接了电平证人时窗内抬在噪声底之上的占比也低于 --min-raw）。
    测试断言门禁的结论而不是其中一半的数值——豁免只动 voiced 那半，raw 那半照旧守着
    「整条字幕压在静音上就是空放」；sil=None 就是 --no-acoustic-floor 的那一期口径。"""
    cs, ce = win
    pad = None if (old or chars is None) else reading_need(chars)
    v = voiced_share(bursts, cs, ce, own, readable=pad)
    r = burst_share(bursts, cs, ce)
    an = None if sil is None else above_noise_share(sil, cs, ce)
    quiet = r < RAW_FLOOR
    if quiet and sil is not None:
        quiet = an < RAW_FLOOR
    return (v < FLOOR or quiet), v, r, an


def main():
    fails = []
    need72 = reading_need(2)                       # 2 字 → 0.55s 地板，不是 2/5.2=0.38s

    # ① 行 72：旧红新绿
    red_old, old72, raw_old, _an_old = t2(BURSTS, ROW72_WIN, ROW72_OWN, old=True)
    red_new, new72, raw72, an72 = t2(BURSTS, ROW72_WIN, ROW72_OWN, 2, sil=SIL_SDL)
    if red_old is False:
        fails.append(f"① 对照失效：旧口径给行 72 打了 {old72*100:.0f}%，没有复现那次判红")
    if red_new:
        fails.append(f"① 新口径仍然判红行 72（窗内语音 {new72*100:.0f}% 原始 {raw72*100:.0f}%）："
                     "富余停留没被豁免，而这一行在轴上无处可挪")
    if an72 is None or an72 < RAW_FLOOR:
        fails.append(f"① 夹具失效：行 72 的窗在电平证人眼里不是有声的（{an72}）——"
                     "这条绿是靠兜底线判出来的，不是靠豁免")

    # ② 一条真正压在停顿里的字幕（词根本不在屏上）→ 两条证人一起把它按住
    if any(b0 < PARK_WIN[1] and b1 > PARK_WIN[0] for b0, b1 in BURSTS):
        fails.append(f"② 夹具失效：突发与实测静音跑 {PARK_WIN} 重叠，这段窗不是「整窗无声」")
    if not any(s0 <= PARK_WIN[0] + 1e-9 and s1 >= PARK_WIN[1] - 1e-9 for s0, s1 in SIL_SDL):
        fails.append(f"② 夹具失效：电平证人里没有覆盖 {PARK_WIN} 的静音跑（实测漂移了），"
                     "这条对照会退化成只由自适应密度撑着")
    parked_red, parked, parked_raw, parked_an = t2(BURSTS, PARK_WIN, ROW72_OWN, 2, sil=SIL_SDL)
    if not parked_red:
        fails.append(f"② 一条完全落在静音里的字幕被放行了（窗内语音 {parked*100:.0f}% "
                     f"原始 {parked_raw*100:.0f}% 抬在噪声底之上 {parked_an*100:.0f}%）")
    if parked_an is not None and parked_an >= RAW_FLOOR:
        fails.append(f"② 夹具失效：这段窗在电平证人眼里是「有声」的（{parked_an*100:.0f}%），"
                     "红只剩自适应密度一条腿")
    if t2(BURSTS, PARK_WIN, ROW72_OWN, 2)[0] is False:
        fails.append("② 对照失效：整窗静音在只量自适应密度的旧兜底口径下竟然不是红")
    if t2(BURSTS, PARK_WIN, ROW72_OWN, old=True)[0] is False:
        fails.append("② 对照失效：整窗静音在旧口径（无豁免）下竟然不是红")

    # ②b 旧②用的 193.00–193.90：自适应密度 0%，但电平证人整窗有声 → shipped 口径放行。
    #     这条断言存在的意义就是「为什么②换了位置」——挪回去会红在这里。
    withdrawn_red, withdrawn_v, withdrawn_raw, withdrawn_an = t2(
        BURSTS, (193.00, 193.90), ROW72_OWN, 2, sil=SIL_SDL)
    if withdrawn_red:
        fails.append(f"②b 形状变了：193.00–193.90 在 shipped 口径下不再是绿"
                     f"（语音 {withdrawn_v*100:.0f}% 原始 {withdrawn_raw*100:.0f}% "
                     f"噪声底之上 {withdrawn_an*100:.0f}%）——"
                     "要么电平证人算不出「这段呼吸其实有声」，要么阈值漂了；"
                     "旧②的对照就是靠这个形状退休的，别把它当可用的控制")

    # ③ 赖着不走：2 字的行在上屏待满 3.0 秒，多出来的全是别人的停顿
    #    （突发与静音配成同一个形状：198.06 之后到 203 秒之间两条证人都没有人声，
    #     才是「赖屏」而不是「压住别人的词」）
    bursts3 = [(197.68, 198.06), (203.00, 205.00)]
    sil3 = [(198.06, 203.00)]
    long_red, long72, long_raw, long_an = t2(bursts3, (197.68, 200.68), ROW72_OWN, 2, sil=sil3)
    if not long_red:
        fails.append(f"③ 2 个字的行把字幕挂了 3.0 秒仍被判绿（窗内语音 {long72*100:.0f}% "
                     f"原始 {long_raw*100:.0f}% 噪声底之上 {long_an*100:.0f}%）："
                     "豁免超过了「读完所需」，这条线白留了")
    if t2(bursts3, (197.68, 200.68), ROW72_OWN, old=True)[0] is False:
        fails.append("③ 对照失效：3 秒赖屏在旧口径下竟然不是红")

    # ④ 长行：15 字读完要 2.88s，多待的 1.6s 静音不该算空放
    own_long, win_long = (183.14, 184.14), (183.14, 185.74)   # 2.6s 窗，1.6s 是纯停顿
    bursts4 = [(183.14, 184.14), (186.20, 187.00)]
    sil4 = [(184.14, 186.20), (187.00, 188.00)]               # 与 bursts4 同一个形状
    if not t2(bursts4, win_long, own_long, old=True)[0]:
        fails.append("④ 对照失效：15 字行在旧口径下本就绿，这条断言是空转")
    if t2(bursts4, win_long, own_long, 15)[0]:
        fails.append(f"④ 15 字行按读完所需多待 1.6 秒仍被判红（{t2(bursts4, win_long, own_long, 15)[1]*100:.0f}%）")
    if t2(bursts4, win_long, own_long, 15, sil=sil4)[0]:
        fails.append("④ 接上电平证人后 15 字行翻红了——两条证人配成同一个形状时豁免必须照旧放行")

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

    # ⑦ 美丽新世界 行 52：两半都要 —— 缺一半就误杀一条听得见、也只有两个字的字幕
    if not (burst_share(BN_52, *WIN_52) < RAW_FLOOR
            and above_noise_share(SIL_52, *WIN_52) >= RAW_FLOOR):
        fails.append("⑦ 夹具失效：行 52 不再是「自适应说无声、电平说有声」的那个形状")
    noread52, v52n, _r52n, _a52n = t2(BN_52, WIN_52, OWN_52, None, sil=SIL_52)
    nofloor52, v52f, r52f, _a52f = t2(BN_52, WIN_52, OWN_52, 2)
    shipped52, v52, r52, an52 = t2(BN_52, WIN_52, OWN_52, 2, sil=SIL_52)
    if not noread52:
        fails.append(f"⑦ 按掉富余停留后行 52 仍绿（{v52n*100:.0f}%）：这行其实不靠豁免，"
                     "那⑦就不是「两半都要」的证据")
    if not nofloor52:
        fails.append(f"⑦ 按掉电平证人后行 52 仍绿（原始 {r52f*100:.0f}%）：兜底线的合取没在判断")
    if shipped52:
        fails.append(f"⑦ shipped 口径仍判红行 52（语音 {v52*100:.0f}% 原始 {r52*100:.0f}% "
                     f"噪声底之上 {an52*100:.0f}%）")

    # ⑧ 同一期行 86：只靠电平证人 —— 与⑦对照，排除「两条其实是同一手在救」
    if not (burst_share(BN_86, *WIN_86) < RAW_FLOOR
            and above_noise_share(SIL_86, *WIN_86) >= RAW_FLOOR):
        fails.append("⑧ 夹具失效：行 86 不是「原始 0% 却整窗抬在噪声底之上」的那个形状")
    noread86, v86n, _r86n, _a86n = t2(BN_86, WIN_86, OWN_86, None, sil=SIL_86)
    nofloor86, _v86f, _r86f, _a86f = t2(BN_86, WIN_86, OWN_86, 2)
    shipped86, v86, r86, an86 = t2(BN_86, WIN_86, OWN_86, 2, sil=SIL_86)
    if noread86:
        fails.append(f"⑧ 按掉富余停留后行 86 翻红了（{v86n*100:.0f}%）：它其实和行 52 一样两半都要，"
                     "⑦⑧ 的对照就没做成")
    if not nofloor86:
        fails.append("⑧ 按掉电平证人后行 86 仍绿：那这条绿是豁免给的，不是证人给的")
    if shipped86:
        fails.append(f"⑧ shipped 口径仍判红行 86（语音 {v86*100:.0f}% 噪声底之上 {an86*100:.0f}%）")

    print(f"行72 旧 {old72*100:.0f}%红 → 新 {new72*100:.0f}%绿（原始密度 {raw72*100:.0f}%、"
          f"噪声底之上 {an72*100:.0f}% 都还守着 {RAW_FLOOR*100:.0f}% 兜底线）   "
          f"2 字读完需 {need72:.2f}s   "
          f"整窗静音 {PARK_WIN[0]:.2f}–{PARK_WIN[1]:.2f} "
          f"{parked*100:.0f}%/{parked_raw*100:.0f}%/{parked_an*100:.0f}% 仍红   "
          f"旧②那窗 {withdrawn_an*100:.0f}% 有声已退休   "
          f"3s 赖屏 {long72*100:.0f}% 仍红   15字行 {t2(bursts4, win_long, own_long, 15)[1]*100:.0f}% 绿")
    print(f"美丽新世界 行52 语音 {v52*100:.0f}%/原始 {r52*100:.0f}%/噪声底之上 {an52*100:.0f}% → 绿"
          f"（按掉豁免→红 {v52n*100:.0f}%，按掉电平→红）   "
          f"行86 原始 {r86*100:.0f}%/噪声底之上 {an86*100:.0f}% → 绿（按掉豁免照旧绿）")
    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print("✅ 富余停留只豁免「读完所需」这一截：短行放行 / 整窗静音（两条证人一起作证）仍红 / "
          "长停留仍红 / 锚点薄弱行与不传参的调用方与旧口径逐位相同 / "
          "行52 两半都要、行86 只要电平证人这一半")


if __name__ == "__main__":
    main()
