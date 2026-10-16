#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.py — ae_bridge 端到端真验证（在真 After Effects 里跑）"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ae_cli import AfterEffects, AeError          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def main():
    os.makedirs(OUT, exist_ok=True)
    ae = AfterEffects()
    steps, fails = 0, []

    def step(name, fn, *a):
        nonlocal steps
        steps += 1
        try:
            r = fn(*a)
        except AeError as e:
            fails.append((name, str(e)[:160]))
            print("✗ %s -> %s" % (name, str(e)[:160]))
            return None
        ok = r.get("ok", True) if isinstance(r, dict) else True
        if ok is False:
            fails.append((name, str(r)[:160]))
        print("%s %s" % ("✓" if ok is not False else "✗", name))
        return r

    print("AE:", ae.exe)
    step("建合成 1920x1080 30fps 10s", ae.call, "comp", 1920, 1080, 30, 10, "zt_verify")
    step("加文字层", ae.call, "addText", "zt_verify", "王承泽最棒", {"size": 120, "name": "标题"})
    step("加纯色层", ae.call, "addSolid", "zt_verify", [10, 20, 30], "背景", 1920, 1080)
    step("加空对象", ae.call, "addNull", "zt_verify", "控制器")
    step("加调整层", ae.call, "addAdjustment", "zt_verify", "调色层")
    step("图层列表", ae.call, "listLayers", "zt_verify")
    step("位置关键帧 0→3s", ae.call, "animate", "zt_verify", "标题", "Position",
         0, 3, [200, 540, 0], [1700, 540, 0])
    step("缩放关键帧", ae.call, "animate", "zt_verify", "标题", "Scale", 0, 2, [0, 0, 100], [100, 100, 100])
    step("缓动", ae.call, "setEase", "zt_verify", "标题", "Position", 1, "BEZIER")
    step("写表达式(wiggle)", ae.call, "setExpression", "zt_verify", "标题", "Rotation", "wiggle(2, 8)")
    step("读表达式", ae.call, "getExpression", "zt_verify", "标题", "Rotation")
    step("加高斯模糊效果", ae.call, "addEffect", "zt_verify", "背景", "ADBE Gaussian Blur 2")
    step("设效果参数", ae.call, "setEffectParam", "zt_verify", "背景", "Gaussian Blur", "Blurriness", 18)
    step("效果列表", ae.call, "listEffects", "zt_verify", "背景")
    step("父子绑定", ae.call, "setParent", "zt_verify", "标题", "控制器")
    step("混合模式", ae.call, "setBlendMode", "zt_verify", "背景", "SCREEN")
    step("加标记", ae.call, "addMarker", "zt_verify", 1.5, "关键点")
    step("入出点裁剪", ae.call, "trimLayer", "zt_verify", "背景", 0, 8)
    step("加入渲染队列", ae.call, "addToRenderQueue", "zt_verify", (OUT + "/verify.mov").replace("\\", "/"))
    step("渲染队列状态", ae.call, "renderQueueStatus")
    step("导出单帧 PNG", ae.call, "saveFrame", "zt_verify", 1.0, (OUT + "/frame.png").replace("\\", "/"))

    print("─" * 68)
    print("步骤 %d，失败 %d" % (steps, len(fails)))
    for n, e in fails:
        print("  ✗ %s : %s" % (n, e))
    for f in ("frame.png",):
        p = os.path.join(OUT, f)
        print("  产物 %-12s %s bytes" % (f, os.path.getsize(p) if os.path.exists(p) else "缺失"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
