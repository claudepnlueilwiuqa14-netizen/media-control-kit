#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py — ps_bridge 端到端真验证（在真 Photoshop 里跑，不是模拟）

    python verify.py

会真的新建一个 1280x720 文档、加文字层、加普通层、上高斯模糊、设蒙版、
导出 PNG/JPG 到 out/，最后不保存关掉。每一步打印真实返回。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ps_cli import Photoshop, PsError          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def show(title, res):
    print("─" * 68)
    print("▶ %s" % title)
    if isinstance(res, dict):
        print(json.dumps(res, ensure_ascii=False)[:600])
    else:
        print(str(res)[:600])
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    ps = Photoshop()
    steps, fails = 0, []

    def step(name, fn, *a):
        nonlocal steps
        steps += 1
        try:
            r = fn(*a)
        except PsError as e:
            fails.append((name, str(e)[:200]))
            print("✗ %s -> %s" % (name, str(e)[:200]))
            return None
        ok = True if not isinstance(r, dict) else r.get("ok", True)
        if ok is False:
            fails.append((name, str(r)[:200]))
        print("%s %s" % ("✓" if ok is not False else "✗", name))
        return r

    show("连接状态", ps.status())

    step("新建文档 1280x720", ps.call, "newDocument", 1280, 720, 72, "zt_verify", "RGB", "white")
    step("文档信息", ps.call, "documentInfo")
    step("加文字层", ps.call, "addTextLayer", "王承泽最棒",
         {"size": 96, "color": [255, 214, 74], "position": [200, 400], "name": "标题"})
    step("文字信息", ps.call, "getTextInfo", "标题")
    step("改字号 120", ps.call, "setTextStyle", "标题", {"size": 120})
    step("加普通层", ps.call, "addLayer", "底图")
    step("矩形选区", ps.call, "selectRect", 100, 100, 600, 500)
    step("填充选区", ps.call, "fillSelection", [70, 234, 208])
    step("取消选区", ps.call, "deselect")
    step("高斯模糊 8px", ps.call, "gaussianBlur", 8, "底图")
    step("图层不透明度 70", ps.call, "setLayerOpacity", "底图", 70)
    step("混合模式 multiply", ps.call, "setLayerBlendMode", "底图", "MULTIPLY")
    step("加图层蒙版", ps.call, "addMask", "底图", "reveal")
    step("蒙版羽化 12", ps.call, "setMaskFeather", "底图", 12)
    step("加亮度对比度调整层", ps.call, "addBrightnessContrast")
    step("加色相饱和度调整层", ps.call, "addHueSaturation")
    step("列出图层", ps.call, "listLayers")
    step("列出通道", ps.call, "listChannels")
    step("转智能对象", ps.call, "convertToSmartObject", "底图")
    step("导出 PNG", ps.call, "exportPNG", os.path.join(OUT, "verify.png").replace("\\", "/"))
    step("导出 JPG", ps.call, "exportJPEG", os.path.join(OUT, "verify.jpg").replace("\\", "/"), 10)
    step("导出 PSD", ps.call, "exportPSD", os.path.join(OUT, "verify.psd").replace("\\", "/"))
    step("列出动作集", ps.call, "listActionSets")
    step("关闭(不保存)", ps.call, "closeDocument", False)

    print("─" * 68)
    print("步骤 %d，失败 %d" % (steps, len(fails)))
    for n, e in fails:
        print("  ✗ %s : %s" % (n, e))
    for f in ("verify.png", "verify.jpg", "verify.psd"):
        p = os.path.join(OUT, f)
        print("  产物 %-12s %s bytes" % (f, os.path.getsize(p) if os.path.exists(p) else "缺失"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
