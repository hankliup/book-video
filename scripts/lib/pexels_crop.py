#!/usr/bin/env python3
"""Pexels 原图 → 成片底图的那次裁切，全仓库只有这一份实现。

为什么单独成 lib：fetch-pexels-by-id.py 用它把图落盘，verify-pexels-fetch.py 用同一份实现
重新按 photo id 取一次、裁一次，再和磁盘上的 _raw/ 底图逐像素比——那一步要证明的是
「盘上这张图就是清单声称的那张照片」，只要两边的裁切口径差一点（居中 vs 窗口、3:4 vs 2:3、
重采样滤波不同），比对就会假红或假绿。以前这份裁切长在取图脚本里，第二份实现只能抄，
抄的那一刻这条证据链就断了（AGENTS.md 反复强调的同一件事：口径写第二遍一定漂移）。

TARGET_W / TARGET_H = 1024x1365：与 720x960 渲染帧同比例（3:4），保证成片只会下采样不会放大。
"""
from PIL import Image

TARGET_W, TARGET_H = 1024, 1365  # 3:4，与 720x960 渲染帧同比例


def crop_3x4(img, box=None):
    """裁成 3:4。box=[x0,y0,x1,y1]（0~1 的分数）先把原图限定到这个窗口再居中裁。

    为什么需要：Pexels 的竖构图经常在画面一角留下一个穿短裤的游客、一块现代路牌或一段
    双黄线——它们在全尺寸复核时看得见，但整张图的光线和构图又是这一段唯一合适的氛围。
    以前只能二选一（换图或把穿帮物上屏），而 3:4 裁切本来就只用到原图的一部分，
    把裁切窗挪开就能在不换照片、不改光线的前提下把杂物排除在画幅之外。
    """
    if box:
        w0, h0 = img.size
        x0, y0, x1, y1 = [float(v) for v in box]
        img = img.crop((int(w0 * x0), int(h0 * y0), int(w0 * x1), int(h0 * y1)))
    w, h = img.size
    th = int(w * TARGET_H / TARGET_W)
    if h >= th:
        top = (h - th) // 2
        img = img.crop((0, top, w, top + th))
    else:
        tw = int(h * TARGET_W / TARGET_H)
        left = (w - tw) // 2
        img = img.crop((left, 0, left + tw, h))
    return img.resize((TARGET_W, TARGET_H), Image.LANCZOS)
