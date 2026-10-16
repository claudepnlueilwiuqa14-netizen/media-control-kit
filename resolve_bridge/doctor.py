#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doctor.py — 本机体检：Resolve / Photoshop / Premiere / After Effects 到底能怎么控

真的去查：注册表、文件系统、进程、COM、Studio 脚本接口、文件桥连通性，
然后给出**可以直接复制执行**的下一步命令。

    python doctor.py
    python doctor.py --json          # 机器可读
    python doctor.py --install       # 顺手把桥脚本复制到 Resolve 的 Scripts 目录
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys

try:
    import winreg
except ImportError:                      # 非 Windows
    winreg = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))          # mediacontrol/
BRIDGE_ROOT = os.path.join(HERE, "root")


# --------------------------------------------------------------- 工具 ----
def _exists(p):
    return bool(p) and os.path.exists(p)


def find_resolve_exe():
    """按常见位置 + 各盘符浅搜找 Resolve.exe。"""
    cands = [
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
        r"D:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
        r"E:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
    ]
    for c in cands:
        if _exists(c):
            return c
    for drive in ("C:\\", "D:\\", "E:\\", "F:\\"):
        if not os.path.isdir(drive):
            continue
        for base in ("Program Files", "Program Files (x86)"):
            p = os.path.join(drive, base, "Blackmagic Design", "DaVinci Resolve", "Resolve.exe")
            if _exists(p):
                return p
    return None


def reg_version():
    if not winreg:
        return None
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Blackmagic Design\DaVinci Resolve")
        val, _ = winreg.QueryValueEx(k, "Version")
        return val
    except Exception:                                        # noqa: BLE001
        return None


def _sub(*args, timeout=20):
    """跑子进程。Windows 中文输出是 GBK，必须 errors=replace，否则解码直接崩。"""
    try:
        return subprocess.run(list(args), capture_output=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except Exception as e:                                   # noqa: BLE001
        return None


def resolve_running():
    p = _sub("tasklist", "/FI", "IMAGENAME eq Resolve.exe")
    return bool(p and "Resolve.exe" in (p.stdout or ""))


def script_api_dirs():
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    api = os.path.join(pd, "Blackmagic Design", "DaVinci Resolve",
                       "Support", "Developer", "Scripting")
    mod = os.path.join(api, "Modules", "DaVinciResolveScript.py")
    return api, mod


def script_menu_dirs():
    """Resolve 启动时会扫描这些目录，把 .lua/.py 列进 Workspace > Scripts 菜单。"""
    appdata = os.environ.get("APPDATA", "")
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return [
        ("用户级(推荐)", os.path.join(appdata, "Blackmagic Design", "DaVinci Resolve",
                                      "Support", "Fusion", "Scripts")),
        ("全用户级", os.path.join(pd, "Blackmagic Design", "DaVinci Resolve",
                                  "Fusion", "Scripts")),
    ]


def find_fusionscript(exe):
    if not exe:
        return None
    p = os.path.join(os.path.dirname(exe), "fusionscript.dll")
    return p if _exists(p) else None


def com_app(prog_ids, js=""):
    """试着用 COM 连一个 Adobe 应用，跑一句脚本。prog_ids 可以是多个候选。

    实测（2026-10-16，本机 Adobe 2025 全家桶）：
      HKCR 里只有 Photoshop.Application（还有 .190），
      **没有** AfterEffects.Application，也**没有** Premiere.Application。
      所以 PS 能走 COM，AE / PR 必须走别的路（见 ae_cli.py / pr_cli.py）。
    """
    if isinstance(prog_ids, str):
        prog_ids = [prog_ids]
    try:
        import pythoncom
        import win32com.client as w
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": "缺少 pywin32: %s" % e}
    last = None
    for pid in prog_ids:
        try:
            pythoncom.CoInitialize()
            a = w.Dispatch(pid)
            ver = None
            try:
                ver = a.Version
            except Exception:                                # noqa: BLE001
                pass
            ran = None
            if js:
                try:
                    ran = a.DoJavaScript(js)
                except Exception as e:                       # noqa: BLE001
                    ran = "DoJavaScript 失败: %s" % e
            return {"ok": True, "prog_id": pid, "version": ver, "probe": ran}
        except Exception as e:                               # noqa: BLE001
            last = str(e)
    return {"ok": False, "prog_ids_tried": prog_ids, "reason": last}


def find_ae_exe():
    for c in (r"D:\新建文件夹\Adobe After Effects 2025\Support Files\AfterFX.exe",
              r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe",
              r"C:\Program Files\Adobe\Adobe After Effects\Support Files\AfterFX.exe"):
        if _exists(c):
            return c
    for drive in ("C:\\", "D:\\", "E:\\"):
        if not os.path.isdir(drive):
            continue
        for root, dirs, files in os.walk(os.path.join(drive, "Program Files")):
            if "AfterFX.exe" in files:
                p = os.path.join(root, "AfterFX.exe")
                return p
            depth = root[len(drive):].count(os.sep)
            if depth >= 3:
                dirs[:] = []
    return None


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
                    if _exists(exe):
                        return exe
    return None


def adobe_scripts_dir():
    """AE / PR 都会从这里加载 File > Scripts 菜单里的 .jsx。"""
    d = os.path.join(os.path.expanduser("~"), "Documents", "Adobe Scripts")
    return d if os.path.isdir(d) else None


def studio_probe():
    sys.path.insert(0, HERE)
    try:
        import studio_api
        return studio_api.probe_json()
    except Exception as e:                                   # noqa: BLE001
        return {"available": False, "reason": "probe 异常: %s" % e}


def bridge_ping():
    sys.path.insert(0, HERE)
    try:
        import client
        b = client.Bridge(BRIDGE_ROOT)
        r = b.ping(timeout=8)
        return {"ok": r.get("status") == "OK", "detail": r}
    except TimeoutError as e:
        return {"ok": False, "reason": "超时: %s" % e}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": str(e)}


def lua_syntax_ok(path):
    """极简 Lua 结构自检：块关键字配平 + 引号闭合。
    Resolve 的 Lua 没有外部解释器，只能这样粗略查一遍语法明显错误。"""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        return False, str(e)
    # 去掉长注释/长字符串，避免误判
    import re
    src = re.sub(r"--\[=*\[.*?\]=*\]", "", src, flags=re.S)
    src = re.sub(r"\[=*\[.*?\]=*\]", '""', src, flags=re.S)
    src = re.sub(r"--[^\n]*", "", src)
    src = re.sub(r'"(\\.|[^"\\])*"', '""', src)
    src = re.sub(r"'(\\.|[^'\\])*'", "''", src)
    # 只有 function/if/for/while/repeat 需要配 end；
    # do / then / else / elseif 自带在同一结构里，不能重复计数（早期版本把 then 也
    # +1 了，导致明明配平的文件报“缺 N 个 end”）。
    BLOCK_OPEN = ("function", "if", "for", "while", "repeat")
    opens = 0
    for m in re.finditer(r"\b(function|if|for|while|do|then|else|elseif|repeat)\b|\bend\b", src):
        w = m.group(0)
        if w == "end":
            opens -= 1
            if opens < 0:
                return False, "多出来的 end"
        elif w in BLOCK_OPEN:
            opens += 1
    if opens > 0:
        return False, "缺 %d 个 end" % opens
    return True, "结构配平"


# --------------------------------------------------------------- 安装 ----
def install_bridge(menu_dir=None):
    """把 free_bridge.lua 和一个启动器复制进 Resolve 的 Scripts 目录。"""
    src = os.path.join(HERE, "free_bridge.lua")
    if not _exists(src):
        return {"ok": False, "reason": "free_bridge.lua 不在 %s" % HERE}
    targets = []
    if menu_dir:
        targets.append(menu_dir)
    else:
        for _, d in script_menu_dirs():
            if os.path.isdir(os.path.dirname(d)):
                targets.append(d)
    if not targets:
        return {"ok": False, "reason": "没找到 Resolve 脚本目录"}
    written = []
    for t in targets:
        util = os.path.join(t, "Utility")
        os.makedirs(util, exist_ok=True)
        shutil.copy2(src, os.path.join(util, "free_bridge.lua"))
        root_fs = BRIDGE_ROOT.replace("\\", "\\\\")
        launcher = (
            '-- zt_bridge_listen : 启动 media-control-kit 文件桥\n'
            '-- 用 Workspace > Scripts > Utility > zt_bridge_listen 运行\n'
            'local ROOT = [==[%s]==]\n'
            'dofile(ROOT .. "free_bridge.lua")({ root = ROOT })\n'
        ) % root_fs
        lp = os.path.join(util, "zt_bridge_listen.lua")
        with open(lp, "w", encoding="utf-8") as f:
            f.write(launcher)
        written.append({"dir": util, "files": ["free_bridge.lua", "zt_bridge_listen.lua"]})
    # 桥根目录也要放一份，且建好 inbox/outbox
    for d in ("", "inbox", "outbox", "renders", "archive"):
        os.makedirs(os.path.join(BRIDGE_ROOT, d) if d else BRIDGE_ROOT, exist_ok=True)
    shutil.copy2(src, os.path.join(BRIDGE_ROOT, "free_bridge.lua"))
    return {"ok": True, "written": written, "bridge_root": BRIDGE_ROOT}


# --------------------------------------------------------------- 主流程 ----
def run(do_install=False):
    rep = {"generated_by": "media-control-kit/doctor.py", "platform": sys.platform}

    # -------- Resolve --------
    exe = find_resolve_exe()
    ver = reg_version()
    lib = find_fusionscript(exe)
    api, mod = script_api_dirs()
    menus = script_menu_dirs()
    running = resolve_running()

    rep["resolve"] = {
        "exe": exe,
        "registry_version": ver,
        "fusionscript_dll": lib,
        "running": running,
        "script_api_dir": api if _exists(api) else None,
        "has_DaVinciResolveScript_module": _exists(mod),
        "script_menu_dirs": [{"label": lb, "path": p, "exists": os.path.isdir(p)}
                             for lb, p in menus],
        "official_readme": (os.path.join(api, "README.txt") if _exists(api) else None),
    }

    # Studio API：子进程探测，避免段错误带崩自己
    s = {"available": False, "reason": "未探测（Resolve 未运行则跳过）"}
    if running:
        s = studio_probe()
    else:
        s = studio_probe()   # 也探一下，拿真实失败原因
    rep["resolve"]["studio_api"] = s
    rep["resolve"]["is_studio"] = bool(s.get("available"))

    # 文件桥
    lua_ok, lua_msg = lua_syntax_ok(os.path.join(HERE, "free_bridge.lua"))
    ping = {"ok": False, "reason": "未探测"}
    if running:
        ping = bridge_ping()
    rep["resolve"]["free_bridge"] = {
        "bridge_root": BRIDGE_ROOT,
        "lua_structure_check": {"ok": lua_ok, "msg": lua_msg},
        "ping": ping if running else {"ok": False, "reason": "Resolve 没运行，跳过 ping"},
    }

    # -------- Adobe --------
    ps_js = ('var r={v:app.version,docs:app.documents.length};'
             'r.v + " | " + String(r.docs)')
    rep["photoshop"] = com_app(["Photoshop.Application", "Photoshop.Application.190"], ps_js)
    ae_exe = find_ae_exe()
    pr_exe = find_pr_exe()
    rep["aftereffects"] = {
        "com": com_app(["AfterEffects.Application", "AfterEffects.Application.25"]),
        "exe": ae_exe,
        "cli_mode": ("AfterFX.exe -r <script.jsx>  （真实支持的命令行方式，"
                     "见 ae/ae_cli.py）" if ae_exe else "没找到 AfterFX.exe"),
    }
    rep["premiere"] = {
        "com": com_app(["Premiere.Application", "Premiere.Application.25"]),
        "exe": pr_exe,
        "cli_mode": ("脚本放进 %s 后用 PR 的 File > Scripts 运行；或 CEP/UXP 面板"
                     "（PR 不支持命令行 -r）" % (adobe_scripts_dir() or
                                                r"C:\Users\<你>\Documents\Adobe Scripts")),
    }

    # -------- 建议 --------
    tips = []
    if not exe:
        tips.append("没找到 Resolve.exe —— 确认达芬奇装了；装了就改 doctor.py 里的候选路径。")
    if not running:
        tips.append("Resolve 没运行：先手动打开达芬奇，再跑 python doctor.py 复检。")
    if s.get("available"):
        tips.append("【Studio 版】原生 API 可用，直接用 studio_api.StudioAPI()，最快。")
    else:
        tips.append("【免费版/连不上】走文件桥：%s" % (s.get("reason") or ""))
        tips.append("  1) python doctor.py --install      # 把桥脚本放进 Resolve 脚本目录")
        tips.append("  2) 达芬奇里：Workspace > Scripts > Utility > zt_bridge_listen")
        tips.append("  3) python client.py ping           # 通了就能用")
    if rep["photoshop"].get("ok"):
        tips.append("Photoshop COM 可用（版本 %s）——ps_cli.py 走这条通道。"
                    % rep["photoshop"].get("version"))
    else:
        tips.append("Photoshop COM 不可用：%s" % rep["photoshop"].get("reason"))
    for name, key, cli in (("After Effects", "aftereffects", "ae/ae_cli.py"),
                           ("Premiere", "premiere", "pr/pr_cli.py")):
        d = rep[key]
        if not d["com"].get("ok"):
            tips.append("%s 没有 COM 接口（Adobe 就没注册），走 %s 的通道：%s"
                        % (name, cli, d.get("cli_mode")))
    rep["recommendations"] = tips

    if do_install:
        rep["install"] = install_bridge()
    return rep


def pretty(rep):
    L = []
    A = L.append
    A("=" * 70)
    A("media-control-kit · 本机体检")
    A("=" * 70)
    r = rep["resolve"]
    A("")
    A("[DaVinci Resolve]")
    A("  程序路径   : %s" % (r["exe"] or "未找到"))
    A("  注册表版本 : %s" % (r["registry_version"] or "未知"))
    A("  是否运行中 : %s" % ("是" if r["running"] else "否"))
    A("  fusionscript.dll : %s" % (r["fusionscript_dll"] or "未找到"))
    A("  官方脚本API目录  : %s" % (r["script_api_dir"] or "未找到"))
    A("  DaVinciResolveScript.py : %s" % ("有" if r["has_DaVinciResolveScript_module"] else "无"))
    for m in r["script_menu_dirs"]:
        A("  脚本菜单目录[%s] : %s (%s)" % (m["label"], m["path"],
                                            "存在" if m["exists"] else "不存在"))
    A("")
    A("  Studio 原生 API : %s" % ("可用" if r["is_studio"] else "不可用"))
    st = r["studio_api"]
    if r["is_studio"]:
        A("    产品=%s 版本=%s 当前页=%s" % (st.get("product"), st.get("version"), st.get("page")))
    else:
        A("    原因: %s" % st.get("reason"))
    A("")
    fb = r["free_bridge"]
    A("  免费版文件桥 :")
    A("    桥根目录   : %s" % fb["bridge_root"])
    A("    Lua 结构自检: %s (%s)" % ("通过" if fb["lua_structure_check"]["ok"] else "失败",
                                     fb["lua_structure_check"]["msg"]))
    p = fb["ping"]
    A("    ping       : %s" % ("通" if p.get("ok") else ("不通 —— %s" % p.get("reason"))))
    A("")
    A("[Adobe]")
    ps = rep["photoshop"]
    if ps.get("ok"):
        A("  Photoshop     : COM 已连接 (%s) version=%s probe=%s"
          % (ps.get("prog_id"), ps.get("version"), ps.get("probe")))
    else:
        A("  Photoshop     : COM 不可用 —— %s" % ps.get("reason"))
    for label, key in (("After Effects", "aftereffects"), ("Premiere", "premiere")):
        d = rep[key]
        A("  %-14s: COM %s；程序=%s" % (label,
          "可用" if d["com"].get("ok") else "不可用（Adobe 未注册 Application ProgID，正常）",
          d.get("exe") or "未找到"))
        A("  %-14s  可用通道: %s" % ("", d.get("cli_mode")))
    A("")
    A("[下一步该干什么]")
    for i, t in enumerate(rep["recommendations"], 1):
        A("  %d) %s" % (i, t))
    if "install" in rep:
        A("")
        A("[安装结果]")
        A("  " + json.dumps(rep["install"], ensure_ascii=False, indent=2).replace("\n", "\n  "))
    A("")
    A("=" * 70)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="media-control-kit 本机体检")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--install", action="store_true", help="把桥脚本装进 Resolve 脚本目录")
    ap.add_argument("--install-dir", default=None, help="指定 Resolve 脚本目录")
    a = ap.parse_args(argv)
    if a.install and a.install_dir:
        print(json.dumps(install_bridge(a.install_dir), ensure_ascii=False, indent=2))
        return 0
    rep = run(do_install=a.install)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(pretty(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
