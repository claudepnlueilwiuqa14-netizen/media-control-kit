#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ae_cli.py — After Effects 控制的 Python 侧

⚠ 通道真相（本机实测 2026-10-16，After Effects 2025）：
   * AE **没有** COM Application 接口（注册表里只有 AfterEffects.Project.25，
     没有 AfterEffects.Application），所以不能像 Photoshop 那样 pywin32 直连。
   * 真实可用的是命令行：
         "D:\\新建文件夹\\Adobe After Effects 2025\\Support Files\\AfterFX.exe" -r script.jsx
     （-r = run script）。跑完 AE 会留在前台。
   * 命令行跑脚本**拿不到返回值**，所以脚本自己把结果写成一个 JSON 文件
     （AE.save(obj, path)），Python 这边轮询读取。

用法：
    from ae_cli import AfterEffects
    ae = AfterEffects()
    print(ae.call("comp", 1920, 1080, 30, 10, "demo"))
    print(ae.call("addText", "demo", "王承泽最棒", {"size": 96}))
    print(ae.call("animate", "demo", 1, "Position", 0, 3, [100,540,0], [1800,540,0]))
    print(ae.call("addToRenderQueue", "demo", "D:/out.mov"))

命令行：
    python ae_cli.py selftest
    python ae_cli.py call comp 1920 1080 30 10 demo
    python ae_cli.py run my_script.jsx
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "ae_bridge.jsx")

AE_EXE_CANDIDATES = [
    r"D:\新建文件夹\Adobe After Effects 2025\Support Files\AfterFX.exe",
    r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe",
    r"C:\Program Files\Adobe\Adobe After Effects\Support Files\AfterFX.exe",
]


class AeError(RuntimeError):
    pass


def js(v):
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(v, ensure_ascii=False)


def find_ae_exe():
    for c in AE_EXE_CANDIDATES:
        if os.path.isfile(c):
            return c
    for drive in ("C:\\", "D:\\", "E:\\"):
        if not os.path.isdir(drive):
            continue
        for base in ("Program Files", "新建文件夹"):
            p = os.path.join(drive, base)
            if not os.path.isdir(p):
                continue
            for name in os.listdir(p):
                if "after effects" in name.lower():
                    exe = os.path.join(p, name, "Support Files", "AfterFX.exe")
                    if os.path.isfile(exe):
                        return exe
    return None


class AfterEffects:
    def __init__(self, exe=None, workdir=None, timeout=180):
        self.exe = exe or find_ae_exe()
        if not self.exe:
            raise AeError("没找到 AfterFX.exe，装了 AE 吗？也可传 exe= 指定")
        self.workdir = workdir or os.path.join(HERE, "work")
        os.makedirs(self.workdir, exist_ok=True)
        self.timeout = timeout
        self.last_result_file = None

    # ------------------------------------------------------------ 执行 ----
    def run_script_file(self, jsx_path, result_path=None, wait=True,
                        suppress_dialogs=True):
        """用 -r 让 AE 执行一个 .jsx 文件。

        -m / -noui 很关键：AE 启动时常弹「恢复上次项目」「许可」之类的对话框，
        弹出来脚本就不会往下走（实测第二次调用卡了 180s 没出结果）。
        """
        cmd = [self.exe]
        if suppress_dialogs:
            cmd.append("-m")          # 抑制对话框
        cmd += ["-r", jsx_path]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:                                # noqa: BLE001
            raise AeError("启动 AE 失败: %s" % e)
        if not wait:
            return {"launched": True, "pid": p.pid, "result_path": result_path}
        if result_path:
            return self._wait_result(result_path)
        try:
            out, err = p.communicate(timeout=self.timeout)
            return {"ok": True, "stdout": (out or b"").decode("utf-8", "replace")[:400],
                    "stderr": (err or b"").decode("utf-8", "replace")[:400]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "err": "AE 执行超时 %ss（它可能还在前台跑）" % self.timeout}

    def _wait_result(self, path, timeout=None):
        timeout = timeout or self.timeout
        t_end = time.time() + timeout
        while time.time() < t_end:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                try:
                    with open(path, encoding="utf-8") as f:
                        return json.loads(f.read())
                except Exception:                             # noqa: BLE001
                    time.sleep(0.3)
                    continue
            time.sleep(0.3)
        raise AeError("%ss 内没等到结果文件 %s —— AE 起来了吗？脚本报错了吗？"
                      % (timeout, path))

    def call(self, fn, *args, timeout=None):
        """调用 AE.<fn>(*args)，返回结果 dict。"""
        res_path = os.path.join(self.workdir, "result_%d.json" % int(time.time() * 1000))
        if os.path.exists(res_path):
            os.remove(res_path)
        self.last_result_file = res_path
        bridge_fs = BRIDGE.replace("\\", "/")
        res_fs = res_path.replace("\\", "/")
        code_lines = [
            'try { $.evalFile("%s"); } catch (e) { }' % bridge_fs,
            'var r = AE.%s(%s);' % (fn, ", ".join(js(a) for a in args)),
            'AE.save(r, "%s");' % res_fs,
        ]
        jsx = os.path.join(self.workdir, "call_%d.jsx" % int(time.time() * 1000))
        with open(jsx, "w", encoding="utf-8") as f:
            f.write("\n".join(code_lines))
        return self.run_script_file(jsx, res_path, wait=True)

    def run_code(self, code, timeout=None):
        """跑一段自定义 JSX（代码里用 AE.* 和 AE.save(结果, path)）。"""
        res_path = os.path.join(self.workdir, "result_%d.json" % int(time.time() * 1000))
        if os.path.exists(res_path):
            os.remove(res_path)
        bridge_fs = BRIDGE.replace("\\", "/")
        res_fs = res_path.replace("\\", "/")
        jsx = os.path.join(self.workdir, "code_%d.jsx" % int(time.time() * 1000))
        with open(jsx, "w", encoding="utf-8") as f:
            f.write('try { $.evalFile("%s"); } catch (e) { }\n' % bridge_fs)
            f.write(code)
        return self.run_script_file(jsx, res_path, wait=True)

    def status(self):
        return {"exe": self.exe, "bridge": BRIDGE if os.path.isfile(BRIDGE) else None,
                "workdir": self.workdir}


def main(argv=None):
    ap = argparse.ArgumentParser(description="After Effects 脚本控制")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("selftest")
    c = sub.add_parser("call"); c.add_argument("fn"); c.add_argument("args", nargs="*")
    r = sub.add_parser("run"); r.add_argument("file")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        print(json.dumps({"exe": find_ae_exe(), "bridge": BRIDGE}, ensure_ascii=False, indent=2))
        return 0
    ae = AfterEffects()
    if a.cmd == "selftest":
        print(json.dumps(ae.call("selfTest"), ensure_ascii=False, indent=2))
    elif a.cmd == "call":
        def conv(x):
            try:
                return json.loads(x)
            except Exception:                                 # noqa: BLE001
                return x
        print(json.dumps(ae.call(a.fn, *[conv(x) for x in a.args]), ensure_ascii=False, indent=2))
    elif a.cmd == "run":
        print(json.dumps(ae.run_script_file(a.file), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
