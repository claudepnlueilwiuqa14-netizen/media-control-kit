#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
studio_api.py — DaVinci Resolve Studio 原生 Python API 封装

和 client.py（免费版文件桥）提供**同等能力**，可以互换：
    Studio 版 -> StudioAPI()    直接进程内调用，最快
    免费版    -> client.Bridge() 走文件投递，慢一点但能用

⚠⚠ 本机实测（2026-10-16，Resolve 21.0.30007，装在 D 盘）：

    本机 **装的是免费版级别的脚本环境**，原生 Python API 走不通，两种死法都实测过：

      1) 不设 DLL 搜索目录时：
         ImportError: DLL load failed while importing fusionscript: 找不到指定的模块。
         根因：官方 DaVinciResolveScript.py 默认去
         C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\ 找 fusionscript.dll，
         而本机 Resolve 装在 **D 盘**（C 盘那个目录是残装，里面根本没有 dll）。

      2) 用 os.add_dll_directory 把 D 盘安装目录加进去后：
         **import 阶段直接段错误（returncode 139 / SIGSEGV）**，连 import 都没走完。

    结论：Studio 原生 API 在本机不可用，别再试。
    → 用 resolve_bridge/client.py（免费版文件桥），能力是齐的。

    另外两条铁律：
      1) 探测必须用【子进程】（doctor.py 已这么做），否则崩的是你自己的进程；
      2) StudioAPI() 只在探测确认 available 后才实例化。

环境变量（Windows）：
    RESOLVE_SCRIPT_API = %PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting
    RESOLVE_SCRIPT_LIB = <Resolve 安装目录>\\fusionscript.dll
    PYTHONPATH 加上 %RESOLVE_SCRIPT_API%\\Modules\\
"""
import os
import sys
import json

API_DEFAULT = os.path.join(
    os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
    "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting")


def candidate_libs():
    """本机可能出现 fusionscript.dll 的位置。"""
    bases = [
        os.environ.get("RESOLVE_SCRIPT_LIB"),
        r"D:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
    ]
    return [b for b in bases if b and os.path.isfile(b)]


def setup_env(api_dir=None, lib=None):
    api_dir = api_dir or API_DEFAULT
    os.environ["RESOLVE_SCRIPT_API"] = api_dir
    if not lib:
        cands = candidate_libs()
        lib = cands[0] if cands else None
    if lib:
        os.environ["RESOLVE_SCRIPT_LIB"] = lib
        # fusionscript.dll 依赖 Resolve 安装目录里的一堆 dll，必须挂上搜索路径，
        # 否则报 "找不到指定的模块"。注意：挂上之后本机实测会在 import 时段错误，
        # 这是 Resolve 侧的问题，不是路径没配对。
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(os.path.dirname(lib))
        except Exception:                                    # noqa: BLE001
            pass
    mod = os.path.join(api_dir, "Modules")
    if mod not in sys.path:
        sys.path.insert(0, mod)
    return api_dir, lib


def scriptapp(api_dir=None, lib=None):
    """拿到 resolve 对象；失败返回 (None, 原因)。注意可能段错误，建议在子进程里跑。"""
    setup_env(api_dir, lib)
    try:
        import DaVinciResolveScript as dvr
    except Exception as e:                                   # noqa: BLE001
        return None, "import 失败: %s" % e
    try:
        r = dvr.scriptapp("Resolve")
    except Exception as e:                                   # noqa: BLE001
        return None, "scriptapp 失败: %s" % e
    if not r:
        return None, "scriptapp 返回 None（Resolve 没运行，或免费版禁用了外部脚本）"
    return r, None


class NotStudio(RuntimeError):
    pass


class StudioAPI:
    """Studio 版直连。方法名和能力对齐 client.Bridge。"""

    def __init__(self, api_dir=None, lib=None):
        self.resolve, err = scriptapp(api_dir, lib)
        if not self.resolve:
            raise NotStudio(err or "无法连接 Resolve 原生脚本接口")

    # ---------------------------------------------------------- 基础 ----
    @property
    def app(self):
        return self.resolve

    def version(self):
        return {"ok": True, "info": {
            "product": self.resolve.GetProductName(),
            "version": self.resolve.GetVersionString(),
            "fields": list(self.resolve.GetVersion()),
        }}

    def current_page(self):
        return {"ok": True, "page": self.resolve.GetCurrentPage()}

    def open_page(self, page):
        return {"ok": bool(self.resolve.OpenPage(page)), "page": page}

    def _pm(self):
        return self.resolve.GetProjectManager()

    def _proj(self):
        p = self._pm().GetCurrentProject()
        if not p:
            raise NotStudio("当前没有打开的项目")
        return p

    def _tl(self):
        t = self._proj().GetCurrentTimeline()
        if not t:
            raise NotStudio("当前没有时间线")
        return t

    # ---------------------------------------------------------- 项目 ----
    def project_list(self):
        return {"ok": True, "projects": self._pm().GetProjectListInCurrentFolder()}

    def create_project(self, name, media_path=None):
        p = self._pm().CreateProject(name, media_path)
        return {"ok": p is not None, "name": name}

    def load_project(self, name):
        return {"ok": self._pm().LoadProject(name) is not None, "name": name}

    def save_project(self):
        return {"ok": bool(self._pm().SaveProject())}

    def project_info(self):
        p = self._proj()
        tl = p.GetCurrentTimeline()
        return {"ok": True, "project": {
            "name": p.GetName(),
            "timelineCount": p.GetTimelineCount(),
            "currentTimeline": tl.GetName() if tl else None,
        }}

    def get_setting(self, name):
        return {"ok": True, "name": name, "value": self._proj().GetSetting(name)}

    def set_setting(self, name, value):
        return {"ok": bool(self._proj().SetSetting(name, value)), "name": name, "value": value}

    # -------------------------------------------------------- 时间线 ----
    def timeline_list(self):
        p, out = self._proj(), []
        for i in range(1, p.GetTimelineCount() + 1):
            t = p.GetTimelineByIndex(i)
            if t:
                out.append({"index": i, "name": t.GetName(),
                            "startFrame": t.GetStartFrame(), "endFrame": t.GetEndFrame()})
        return {"ok": True, "timelines": out}

    def create_timeline(self, name):
        t = self._proj().GetMediaPool().CreateEmptyTimeline(name)
        return {"ok": t is not None, "name": name}

    def set_timeline_by_index(self, i):
        t = self._proj().GetTimelineByIndex(i)
        return {"ok": bool(self._proj().SetCurrentTimeline(t)) if t else False}

    def timeline_info(self):
        t = self._tl()
        return {"ok": True, "timeline": {
            "name": t.GetName(), "startFrame": t.GetStartFrame(),
            "endFrame": t.GetEndFrame(), "startTimecode": t.GetStartTimecode(),
            "videoTracks": t.GetTrackCount("video"),
            "audioTracks": t.GetTrackCount("audio"),
        }}

    def set_mark_in_out(self, in_f, out_f, kind="all"):
        return {"ok": bool(self._tl().SetMarkInOut(in_f, out_f, kind))}

    def get_mark_in_out(self):
        return {"ok": True, "marks": self._tl().GetMarkInOut()}

    # -------------------------------------------------------- 媒体池 ----
    def import_media(self, paths):
        items = []
        for p in paths:
            items.append(p if isinstance(p, dict) else {"FilePath": p})
        r = self._proj().GetMediaPool().ImportMedia(items)
        out = []
        for it in (r or []):
            out.append({"name": it.GetName(), "mediaId": it.GetMediaId()})
        return {"ok": True, "count": len(out), "clips": out, "_items": r}

    def append_to_timeline(self, clips):
        r = self._proj().GetMediaPool().AppendToTimeline(clips)
        return {"ok": bool(r), "count": len(r) if isinstance(r, list) else None}

    def import_and_append(self, paths, timeline_name=None):
        out = {}
        if timeline_name:
            out["timeline"] = self.create_timeline(timeline_name)
        imp = self.import_media(paths)
        out["import"] = imp
        out["append"] = self.append_to_timeline(imp.get("_items") or [])
        return {"ok": True, **out}

    # ---------------------------------------------------------- 片段 ----
    def _item(self, track_type="video", index=1, n=1):
        lst = self._tl().GetItemListInTrack(track_type, index)
        if not lst or len(lst) < n:
            raise NotStudio("轨道 %s#%d 上没有第 %d 个片段" % (track_type, index, n))
        return lst[n - 1]

    def items(self, track_type="video", index=1):
        out = []
        for it in self._tl().GetItemListInTrack(track_type, index) or []:
            out.append({"name": it.GetName(), "start": it.GetStart(),
                        "end": it.GetEnd(), "duration": it.GetDuration()})
        return {"ok": True, "items": out}

    def get_property(self, key, track_type="video", index=1, n=1):
        return {"ok": True, "key": key, "value": self._item(track_type, index, n).GetProperty(key)}

    def set_property(self, key, value, track_type="video", index=1, n=1):
        return {"ok": bool(self._item(track_type, index, n).SetProperty(key, value))}

    def set_speed(self, speed, track_type="video", index=1, n=1):
        return self.set_property("Speed", speed, track_type, index, n)

    def set_volume(self, db, track_type="audio", index=1, n=1):
        return self.set_property("Volume", db, track_type, index, n)

    def set_pan(self, pan, track_type="audio", index=1, n=1):
        return self.set_property("Pan", pan, track_type, index, n)

    def set_opacity(self, v, track_type="video", index=1, n=1):
        return self.set_property("Opacity", v, track_type, index, n)

    def set_zoom(self, x, y=None, track_type="video", index=1, n=1):
        a = self.set_property("ZoomX", x, track_type, index, n)
        b = self.set_property("ZoomY", y if y is not None else x, track_type, index, n)
        return {"ok": a["ok"] and b["ok"]}

    def audio_gain_db(self, track_index, db):
        lst = self._tl().GetItemListInTrack("audio", track_index) or []
        n = sum(1 for it in lst if it.SetProperty("Volume", db))
        return {"ok": True, "track": track_index, "db": db, "changed": n}

    # ---------------------------------------------------------- 轨道 ----
    def add_track(self, track_type, sub_type=None):
        return {"ok": bool(self._tl().AddTrack(track_type, sub_type))}

    def delete_track(self, track_type, index):
        return {"ok": bool(self._tl().DeleteTrack(track_type, index))}

    def set_track_enable(self, track_type, index, on):
        return {"ok": bool(self._tl().SetTrackEnable(track_type, index, bool(on)))}

    def set_track_lock(self, track_type, index, on):
        return {"ok": bool(self._tl().SetTrackLock(track_type, index, bool(on)))}

    def set_track_name(self, track_type, index, name):
        return {"ok": bool(self._tl().SetTrackName(track_type, index, name))}

    # ---------------------------------------------------------- 字幕 ----
    def subtitles_from_audio(self, settings=None):
        return {"ok": bool(self._tl().CreateSubtitlesFromAudio(settings or {}))}

    def insert_title(self, title_name):
        return {"ok": bool(self._tl().InsertTitleIntoTimeline(title_name))}

    def insert_generator(self, name):
        return {"ok": bool(self._tl().InsertGeneratorIntoTimeline(name))}

    # ---------------------------------------------------------- 调色 ----
    def _graph(self, track_type="video", index=1, n=1, layer=1):
        g = self._item(track_type, index, n).GetNodeGraph(layer)
        if not g:
            raise NotStudio("取不到节点图（需要 Studio 版 + Color 页）")
        return g

    def color_nodes(self, track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        nodes = []
        for i in range(1, g.GetNumNodes() + 1):
            nodes.append({"index": i, "label": g.GetNodeLabel(i)})
        return {"ok": True, "numNodes": g.GetNumNodes(), "nodes": nodes}

    def node_tools(self, node_index, track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        tools = g.GetToolsInNode(node_index)
        out = []
        for k, t in (tools or {}).items():
            out.append({"key": str(k), "name": str(t)})
        return {"ok": True, "node": node_index, "tools": out}

    def set_node_input(self, node_index, input_id, value, tool_key=None,
                       track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        tools = g.GetToolsInNode(node_index) or {}
        t = tools.get(tool_key) if tool_key else (list(tools.values())[0] if tools else None)
        if not t:
            return {"ok": False, "err": "节点 %d 里没有 tool" % node_index}
        return {"ok": bool(t.SetInput(input_id, value))}

    def set_lgg(self, node_index, lift, gamma, gain, track_type="video", index=1, n=1):
        a = self.set_node_input(node_index, "Lift", lift, None, track_type, index, n)
        b = self.set_node_input(node_index, "Gamma", gamma, None, track_type, index, n)
        c = self.set_node_input(node_index, "Gain", gain, None, track_type, index, n)
        return {"ok": a["ok"] and b["ok"] and c["ok"], "lift": a, "gamma": b, "gain": c}

    def set_wheel(self, node_index, which, rgb, track_type="video", index=1, n=1):
        table = {"masterWheel": "MasterRGB", "shadowWheel": "ShadowRGB",
                 "midWheel": "MidRGB", "highlightWheel": "HighlightRGB"}
        return self.set_node_input(node_index, table.get(which, which), rgb,
                                   None, track_type, index, n)

    def set_lut(self, node_index, lut_path, track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        return {"ok": bool(g.SetLUT(node_index, lut_path)), "node": node_index, "lut": lut_path}

    def get_lut(self, node_index, track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        return {"ok": True, "lut": g.GetLUT(node_index)}

    def set_cdl(self, cdl, track_type="video", index=1, n=1):
        return {"ok": bool(self._item(track_type, index, n).SetCDL(cdl)), "cdl": cdl}

    def apply_drx(self, path, mode=0, track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        return {"ok": bool(g.ApplyGradeFromDRX(path, mode)), "drx": path}

    def set_node_enabled(self, node_index, on, track_type="video", index=1, n=1):
        g = self._graph(track_type, index, n)
        return {"ok": bool(g.SetNodeEnabled(node_index, bool(on)))}

    def export_lut(self, path, export_type=1, track_type="video", index=1, n=1):
        return {"ok": bool(self._item(track_type, index, n).ExportLUT(export_type, path))}

    # ---------------------------------------------------------- 渲染 ----
    def render_settings(self, settings):
        return {"ok": bool(self._proj().SetRenderSettings(settings))}

    def get_render_settings(self):
        p = self._proj()
        return {"ok": True, "settings": {
            "formats": p.GetRenderFormats(),
            "presets": p.GetRenderPresetList(),
            "mode": p.GetCurrentRenderMode(),
        }}

    def render_presets(self):
        return {"ok": True, "presets": self._proj().GetRenderPresetList()}

    def load_render_preset(self, name):
        return {"ok": bool(self._proj().LoadRenderPreset(name))}

    def add_render_job(self):
        return {"ok": True, "jobId": self._proj().AddRenderJob()}

    def render_job_list(self):
        return {"ok": True, "jobs": self._proj().GetRenderJobList()}

    def render_status(self, job_id):
        return {"ok": True, "status": self._proj().GetRenderJobStatus(job_id)}

    def start_render(self, job_ids=None, interactive=False):
        p = self._proj()
        ok = p.StartRendering(job_ids, interactive) if job_ids else p.StartRendering(interactive)
        return {"ok": bool(ok)}

    def stop_render(self):
        return {"ok": bool(self._proj().StopRendering())}

    def is_rendering(self):
        return {"ok": True, "rendering": self._proj().IsRenderingInProgress()}

    def quick_export(self, preset, params=None):
        return {"ok": bool(self._proj().RenderWithQuickExport(preset, params or {}))}

    # ---------------------------------------------------------- 标记 ----
    def add_marker(self, frame_id, color="Blue", name="", note="", duration=1, custom_data=""):
        return {"ok": bool(self._tl().AddMarker(frame_id, color, name, note, duration, custom_data))}

    def markers(self):
        return {"ok": True, "markers": self._tl().GetMarkers()}


# --------------------------------------------------------------- 探测 ----
def probe_json(api_dir=None, lib=None, timeout=25):
    """在【子进程】里探测 Studio API 是否可用。返回 dict，绝不抛异常。

    必须子进程：本机实测主进程里 import+scriptapp 会段错误，整个 Python 崩掉。
    """
    import subprocess
    # 注意：子进程代码必须是合法多行 Python，不能用 "if x: y" 这种一行写法
    code = "\n".join([
        "import json, sys",
        "sys.path.insert(0, %r)" % os.path.dirname(os.path.abspath(__file__)),
        "import studio_api as s",
        "r, e = s.scriptapp(%r, %r)" % (api_dir, lib),
        "out = {'available': bool(r)}",
        "if r:",
        "    out['product'] = r.GetProductName()",
        "    out['version'] = r.GetVersionString()",
        "    try:",
        "        out['page'] = r.GetCurrentPage()",
        "    except Exception as ex:",
        "        out['page'] = 'err: ' + str(ex)",
        "else:",
        "    out['reason'] = e",
        "print('@@JSON@@' + json.dumps(out, ensure_ascii=False))",
    ])
    try:
        # 必须 encoding + errors：Windows 中文报错是 GBK，默认 utf-8 解码会直接崩
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "子进程超时 %ss" % timeout}
    out = (p.stdout or "") + (p.stderr or "")
    marker = "@@JSON@@"
    if marker in out:
        try:
            return json.loads(out.split(marker, 1)[1].splitlines()[0])
        except Exception:                                      # noqa: BLE001
            pass
    rc = p.returncode
    # Windows 访问违规是 0xC0000005 = 3221225477（正数！），POSIX 段错误是 -11/139
    if rc in (-1073741819, 3221225477, -11, 139) or rc < 0:
        return {"available": False, "reason":
                "子进程崩溃（returncode=%s，段错误/访问违规）——"
                "fusionscript.dll 加载即炸，本机 Studio 原生 API 不可用" % rc}
    return {"available": False, "reason": "没拿到结果 returncode=%s" % rc,
            "stderr": (p.stderr or "")[:400]}


if __name__ == "__main__":
    print(json.dumps(probe_json(), indent=2, ensure_ascii=False))
