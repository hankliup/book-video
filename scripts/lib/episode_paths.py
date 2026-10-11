# -*- coding: utf-8 -*-
"""episode 内目录名的单一口径。

三段结构（钩子 → 固定开场白 → 正文）把正文段的氛围图目录从 images/ 改名为 images-body/，
和 script-body.csv / script-hook.csv / images-hook/ 配成一套能一眼看出归属的名字。
历史剧集留在 images/，不回改。于是每个消费方都要「两个名字都认、优先新名」——
这件事在 5 个脚本里各写一遍，就一定会漏一个：漏掉的那个报「找不到图片目录」，
而图就在隔壁目录，查半天。所以口径只写在这里。
"""
import os

BODY_SUBDIRS = ("images-body", "images")
HOOK_SUBDIR = "images-hook"


def body_images_subdir(ep_dir, env="IMAGES_SUBDIR"):
    """返回正文段氛围图的**相对目录名**。

    env 显式给了值就用它（grade-atmosphere-images.py / fix-bright-regions.py 靠同一个开关
    分别跑正文段和钩子段），否则按 BODY_SUBDIRS 的优先级探测，都没有时回 "images"，
    让调用方的「目录不存在」报错仍然指向历史口径的名字。
    """
    override = os.environ.get(env)
    if override:
        return override
    for name in BODY_SUBDIRS:
        if os.path.isdir(os.path.join(ep_dir, name)):
            return name
    return "images"


def body_images_dir(ep_dir, env="IMAGES_SUBDIR", create=False):
    """正文段氛围图的绝对路径；create=True 时顺带建目录（出图脚本落地前用）。"""
    path = os.path.join(ep_dir, body_images_subdir(ep_dir, env))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def hook_images_dir(ep_dir, create=False):
    """钩子段氛围图的绝对路径（images-hook/）。"""
    path = os.path.join(ep_dir, HOOK_SUBDIR)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def segment_subdir(ep_dir, item, env="IMAGES_SUBDIR"):
    """选型清单里**这一条**该落进哪个相对目录 —— 只按条目自己写的 dir/segment 判。

    为什么不能退回 body_images_subdir(ep_dir)：那个函数读 IMAGES_SUBDIR，而 IMAGES_SUBDIR
    是给调色脚本用的「这一次处理哪一段」开关（IMAGES_SUBDIR=images-hook 跑一遍钩子段），
    它一旦设上，就把「正文段」这个名字整个换成了 images-hook。取图脚本借它来决定正文条目落哪儿，
    《猎人笔记》2026-10-06 实测：prepare-hook-segment.sh 用 IMAGES_SUBDIR=images-hook FORCE=1
    重取钩子图，16 张正文图被逐张重下并写进 images-hook/，把真正的两张钩子图盖掉。
    所以正文条目在这里钉死 images-body：IMAGES_SUBDIR 不许把任何一条正文图路由进钩子目录。
    """
    sub = (item or {}).get("dir")
    if sub:
        return sub
    if (item or {}).get("segment") == "hook":
        return HOOK_SUBDIR
    override = os.environ.get(env)
    if override and override != HOOK_SUBDIR:
        return override
    for name in BODY_SUBDIRS:
        if os.path.isdir(os.path.join(ep_dir, name)):
            return name
    return BODY_SUBDIRS[0]


def segment_images_dir(ep_dir, item, create=False, env="IMAGES_SUBDIR"):
    """按段归属返回绝对目录（取图脚本的唯一入口）。"""
    path = os.path.join(ep_dir, segment_subdir(ep_dir, item, env))
    if create:
        os.makedirs(path, exist_ok=True)
    return path
