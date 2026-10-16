#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pr_cli.py — Premiere Pro 控制的 Python 侧

⚠ 通道真相（本机实测 2026-10-16，Premiere Pro 2025）：
  PR **既没有 COM 接口，也不支持命令行 -r**（那是 AE 才有的）。
  所以给三条路，按可靠性排序：

  1) **BridgeTalk 转发**（推荐，无需手动操作）
     从 Photoshop（它**有** COM，实测能连）用 BridgeTalk 把脚本发给 premierepro。
     前提：PS 和 PR 都在运行。
         python pr_cli.py bridgetalk projectInfo

  2) **Adobe Scripts 目录**（最稳，需手动点一下）
     把脚本写到 C:\\Users\\<你>\\Documents\\Adobe Scripts\\zt_pr_<fn>.jsx
     然后在 PR 里 文件 > 脚本 > zt_pr_<fn>，结果落到 pr/work/result.json
         python pr_cli.py write projectInfo      # 生成脚本
         python pr_cli.py read                   # 读结果

  3) UXP 插件（PR 22+ 支持，见 ps/uxp 那套同思路）

命令行：
    python pr_cli.py status
    python pr_cli.py bridgetalk projectInfo
    python pr_cli.py write listSequences
    python pr_cli.py read
    python pr_cli.py bridgetalk call createSequence "demo" ""
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "pr_bridge.jsx")
WORK = os.path.join(HERE, "work")
ADOBE_SCRIPTS = os.path.join(os.path.expanduser("~"), "Documents", "Adobe Scripts")


class PrError(RuntimeError):
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


def find_pr_exe():
    for drive in ("C:\\", "D:\\", "E:\\"):
        if not os.path.isdir(drive):
            continue
        for base in ("Program Files", "Program Files\\Adobe", "新建文件夹"):
            p = os.path.join(drive, base)
            if not os.path.isdir(p):
                continue
            for name in os.listdir(p):
                if name.lower().startswith("adobe premiere pro"):
                    exe = os.path.join(p, name, "Adobe Premiere Pro.exe")
                    if os.path.isfile(exe):
                        return exe
    return None


def pr_running():
    try:
        p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Adobe Premiere Pro.exe"],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=20)
        return "Adobe Premiere Pro.exe" in (p.stdout or "")
    except Exception:                                         # noqa: BLE001
        return False


class Premiere:
    def __init__(self, workdir=None):
        self.workdir = workdir or WORK
        os.makedirs(self.workdir, exist_ok=True)

    # ---------------------------------------------- 通道1：BridgeTalk ----
    def _ps(self):
        import pythoncom
        import win32com.client as w
        pythoncom.CoInitialize()
        for pid in ("Photoshop.Application", "Photoshop.Application.190"):
            try:
                return w.Dispatch(pid)
            except Exception:                                 # noqa: BLE001
                continue
        raise PrError("连不上 Photoshop —— BridgeTalk 需要借 PS 的通道")

    def bridgetalk(self, fn, *args, timeout=60):
        """借 Photoshop 的 COM，用 BridgeTalk 把脚本发给 Premiere。"""
        res_path = os.path.join(self.workdir, "result_%d.json" % int(time.time() * 1000))
        if os.path.exists(res_path):
            os.remove(res_path)
        res_fs = res_path.replace("\\", "/")
        bridge_fs = BRIDGE.replace("\\", "/")

        # 在 PR 里执行的那段代码
        pr_code = (
            'try { $.evalFile("%s"); } catch (e0) { }\n'
            'var r = PR.%s(%s);\n'
            'PR.save(r, "%s");\n'
            '"done"'
        ) % (bridge_fs, fn, ", ".join(js(a) for a in args), res_fs)

        # 包一层 BridgeTalk：从 PS 发给 premierepro
        outer = r'''
(function () {
  var prCode = %s;
  var bt = new BridgeTalk();
  bt.target = "premierepro";
  bt.body = prCode;
  bt.onError = function (e) { "BT_ERROR:" + e.body; };
  bt.send(20);
  "sent";
})();
''' % js(pr_code)

        ps = self._ps()
        try:
            sent = ps.DoJavaScript(outer)
        except Exception as e:                                # noqa: BLE001
            raise PrError("BridgeTalk 发送失败: %s" % e)

        t_end = time.time() + timeout
        while time.time() < t_end:
            if os.path.exists(res_path) and os.path.getsize(res_path) > 0:
                try:
                    with open(res_path, encoding="utf-8") as f:
                        return json.loads(f.read())
                except Exception:                             # noqa: BLE001
                    time.sleep(0.3)
                    continue
            time.sleep(0.3)
        raise PrError(
            "%ss 内没等到 PR 的结果（BridgeTalk=%s）。\n"
            "  PR 开着吗？在 PR 首选项里允许脚本了吗？\n"
            "  也可以改用 write + 手动运行：python pr_cli.py write %s"
            % (timeout, sent, fn))

    # ------------------------------------------ 通道2：Scripts 目录 ----
    def write_script(self, fn, *args):
        """生成一个 .jsx 放到 Adobe Scripts 目录，用户在 PR 里点一下就跑。"""
        os.makedirs(ADOBE_SCRIPTS, exist_ok=True)
        if not os.path.isfile(BRIDGE):
            raise PrError("找不到 %s" % BRIDGE)
        res_path = os.path.join(self.workdir, "manual_result.json")
        if os.path.exists(res_path):
            os.remove(res_path)
        res_fs = res_path.replace("\\", "/")
        bridge_fs = BRIDGE.replace("\\", "/")
        code = (
            'try { $.evalFile("%s"); } catch (e0) { }\n'
            'var r = PR.%s(%s);\n'
            'PR.save(r, "%s");\n'
            'alert("完成，结果已写入:\\n%s");\n'
        ) % (bridge_fs, fn, ", ".join(js(a) for a in args), res_fs, res_fs)
        out = os.path.join(ADOBE_SCRIPTS, "zt_pr_%s.jsx" % fn)
        with open(out, "w", encoding="utf-8") as f:
            f.write(code)
        return {"script": out, "result_path": res_path,
                "howto": "在 PR 里 文件 > 脚本 > zt_pr_%s，跑完读 python pr_cli.py read" % fn}

    def read(self, name="manual_result.json"):
        p = os.path.join(self.workdir, name)
        if not os.path.exists(p):
            raise PrError("还没有结果文件 %s —— 先在 PR 里跑脚本" % p)
        with open(p, encoding="utf-8") as f:
            return json.loads(f.read())

    def cep_debug_state(self):
        """CEP 面板能不能装，取决于 PlayerDebugMode 有没有开。

        本机实测（PR 2025）：HKCU\\Software\\Adobe 下 **CSXS.11 和 CSXS.12 都存在**，
        但两个都**没有** PlayerDebugMode 值 —— 也就是还没开调试开关，
        CEP 面板装了也不会显示。必须先跑：
            reg add HKCU\\Software\\Adobe\\CSXS.11 /v PlayerDebugMode /t REG_SZ /d 1 /f
        """
        try:
            import winreg
        except ImportError:
            return {"note": "非 Windows"}
        out = {}
        for v in ("CSXS.11", "CSXS.12", "CSXS.10", "CSXS.9"):
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\Adobe\\" + v)
                try:
                    val, _ = winreg.QueryValueEx(k, "PlayerDebugMode")
                    out[v] = {"exists": True, "PlayerDebugMode": val}
                except Exception:                             # noqa: BLE001
                    out[v] = {"exists": True, "PlayerDebugMode": None,
                              "action": "需要 reg add ... /v PlayerDebugMode /d 1"}
            except Exception:                                 # noqa: BLE001
                out[v] = {"exists": False}
        return out

    def status(self):
        return {
            "pr_exe": find_pr_exe(),
            "pr_running": pr_running(),
            "bridge_jsx": BRIDGE if os.path.isfile(BRIDGE) else None,
            "adobe_scripts_dir": ADOBE_SCRIPTS if os.path.isdir(ADOBE_SCRIPTS) else None,
            "cep_registry": self.cep_debug_state(),
            "channels": [
                "CEP 面板（生产级方案，需先开 PlayerDebugMode）—— pr/cep/",
                "BridgeTalk 转发（借 Photoshop 的 COM，PR+PS 都要开着）",
                "Adobe Scripts 目录 + PR 里手动运行（最稳）",
            ],
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Premiere Pro 脚本控制")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("bridgetalk"); b.add_argument("fn"); b.add_argument("args", nargs="*")
    w = sub.add_parser("write"); w.add_argument("fn"); w.add_argument("args", nargs="*")
    sub.add_parser("read")
    a = ap.parse_args(argv)

    pr = Premiere()
    if a.cmd == "status":
        print(json.dumps(pr.status(), ensure_ascii=False, indent=2))
        return 0

    def conv(x):
        try:
            return json.loads(x)
        except Exception:                                     # noqa: BLE001
            return x

    if a.cmd == "bridgetalk":
        print(json.dumps(pr.bridgetalk(a.fn, *[conv(x) for x in a.args]),
                         ensure_ascii=False, indent=2))
    elif a.cmd == "write":
        print(json.dumps(pr.write_script(a.fn, *[conv(x) for x in a.args]),
                         ensure_ascii=False, indent=2))
    elif a.cmd == "read":
        print(json.dumps(pr.read(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
