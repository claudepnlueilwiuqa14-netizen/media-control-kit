#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ps_cli.py — Photoshop 控制的 Python 侧

通道优先级（本机实测 2026-10-16，Photoshop 26.4.1）：
  1) COM（pywin32）app.DoJavaScript()  —— **本机实测可用，推荐**
  2) MCP 的 ps_jsx 工具                —— 备用；本机实测它报
     "尚未调用 CoInitialize"，同一进程里 COM 没初始化好，当前不可用

用法：
    from ps_cli import Photoshop
    ps = Photoshop()
    ps.load_bridge()                       # 注入 ps_bridge.jsx
    print(ps.call("newDocument", 1920, 1080, 72, "demo"))
    print(ps.call("addTextLayer", "王承泽最棒", {"size": 96, "color": [255, 214, 74]}))
    print(ps.call("exportPNG", "D:/out.png"))

命令行：
    python ps_cli.py status
    python ps_cli.py call newDocument 1920 1080 72 demo
    python ps_cli.py js "app.documents.length"
    python ps_cli.py selftest
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_JSX = os.path.join(HERE, "ps_bridge.jsx")

PROG_IDS = ["Photoshop.Application", "Photoshop.Application.190"]


class PsError(RuntimeError):
    pass


def js(v):
    """Python 值 -> JS 字面量"""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False)
    return json.dumps(str(v), ensure_ascii=False)


class Photoshop:
    def __init__(self, com=True, bridge=True):
        self.app = None
        self._com = None
        if com:
            self._connect_com()
        if bridge and self.app:
            self.load_bridge()

    # ------------------------------------------------------------ 连接 ----
    def _connect_com(self):
        try:
            import pythoncom
            import win32com.client as w
        except Exception as e:                                # noqa: BLE001
            raise PsError("缺少 pywin32，无法用 COM 通道: %s" % e)
        pythoncom.CoInitialize()
        last = None
        for pid in PROG_IDS:
            try:
                self.app = w.Dispatch(pid)
                self._com = pid
                return
            except Exception as e:                            # noqa: BLE001
                last = e
        raise PsError("连不上 Photoshop COM（%s）。程序没开？ProgID: %s"
                      % (last, PROG_IDS))

    @property
    def version(self):
        return self.app.Version if self.app else None

    # ---------------------------------------------------- 执行 JSX ----
    def run_js(self, code):
        """执行任意 ExtendScript，返回最后一句的值（字符串）。"""
        if not self.app:
            raise PsError("Photoshop 没连上")
        try:
            return self.app.DoJavaScript(code)
        except Exception as e:                                # noqa: BLE001
            raise PsError("DoJavaScript 失败: %s" % e)

    def load_bridge(self, path=None):
        """把 ps_bridge.jsx 注入 Photoshop（幂等）。

        ⚠ 实测：Photoshop 的 DoJavaScript **每次调用都是独立的执行上下文**，
        上一次定义的全局变量下一次就没了（实测 "ZT 未定义"）。
        所以不能"注入一次、调用多次"，必须每次调用时按需 evalFile 重新加载。
        """
        p = path or BRIDGE_JSX
        if not os.path.isfile(p):
            raise PsError("找不到 %s" % p)
        self._bridge_path = p
        # 直接 evalFile 一次，顺带验证语法
        return self.run_js('$.evalFile(%s); "loaded"' % js(p))

    def _prelude(self):
        """每次执行都先确保 ZT 存在。"""
        if not getattr(self, "_bridge_path", None):
            return ""
        return 'if (typeof ZT === "undefined") { $.evalFile(%s); }\n' % js(self._bridge_path)

    def call(self, fn, *args):
        """调用 ZT.<fn>(*args)，返回解析后的 dict。"""
        if not self.app:
            raise PsError("Photoshop 没连上")
        code = self._prelude() + "ZT.%s(%s)" % (fn, ", ".join(js(a) for a in args))
        return self.parse(self.run_js(code))

    def call_raw(self, fn, *args):
        code = self._prelude() + "ZT.%s(%s)" % (fn, ", ".join(js(a) for a in args))
        return self.run_js(code)

    @staticmethod
    def parse(raw):
        """把 ExtendScript 返回的 JSON 字符串解析成 dict；失败就原样返回。"""
        if raw is None:
            return {"ok": False, "err": "空返回"}
        if isinstance(raw, (dict, list)):
            return raw
        s = str(raw).strip()
        try:
            return json.loads(s)
        except Exception:                                     # noqa: BLE001
            return {"ok": True, "raw": s}

    # ------------------------------------------------------- 便捷方法 ----
    def status(self):
        out = {"com": self._com, "version": self.version,
               "documents": int(self.run_js("String(app.documents.length)")),
               "bridge": None}
        try:
            out["bridge"] = self.parse(self.run_js(self._prelude() + "ZT.selfTest()"))
        except Exception as e:                                # noqa: BLE001
            out["bridge"] = "未注入: %s" % e
        return out

    def new_document(self, w, h, res=72, name="untitled", fill="white"):
        return self.call("newDocument", w, h, res, name, "RGB", fill)

    def add_text(self, text, size=48, color=None, font=None, position=None, name=None):
        opts = {"size": size}
        if color: opts["color"] = color
        if font: opts["font"] = font
        if position: opts["position"] = position
        if name: opts["name"] = name
        return self.call("addTextLayer", text, opts)

    def export(self, path, fmt="png", quality=10):
        fmt = (fmt or "png").lower()
        fn = {"png": "exportPNG", "jpg": "exportJPEG", "jpeg": "exportJPEG",
              "psd": "exportPSD", "tif": "exportTIFF", "tiff": "exportTIFF"}.get(fmt)
        if not fn:
            raise PsError("不支持的格式: %s" % fmt)
        if fmt in ("jpg", "jpeg"):
            return self.call(fn, path, quality)
        return self.call(fn, path)

    def close(self, save=False):
        return self.call("closeDocument", save)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Photoshop 脚本控制")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("selftest")
    j = sub.add_parser("js"); j.add_argument("code", nargs="+")
    c = sub.add_parser("call"); c.add_argument("fn"); c.add_argument("args", nargs="*")
    e = sub.add_parser("export")
    e.add_argument("path"); e.add_argument("--fmt", default="png")
    a = ap.parse_args(argv)

    ps = Photoshop(bridge=(a.cmd != "js"))
    if a.cmd == "status":
        print(json.dumps(ps.status(), ensure_ascii=False, indent=2))
    elif a.cmd == "selftest":
        print(ps.call_raw("selfTest"))
    elif a.cmd == "js":
        print(ps.run_js(" ".join(a.code)))
    elif a.cmd == "call":
        def conv(x):
            try:
                return json.loads(x)
            except Exception:                                 # noqa: BLE001
                return x
        print(ps.call_raw(a.fn, *[conv(x) for x in a.args]))
    elif a.cmd == "export":
        print(json.dumps(ps.export(a.path, a.fmt), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
