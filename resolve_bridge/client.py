#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client.py — free_bridge.lua 的 Python 侧

一端往 inbox 投 Lua 命令，另一端（Resolve 里的监听脚本）执行后把结果塞进
outbox 的 .comp 文件，这里再解出来。免费版 Resolve 唯一可行的外部控制通道。

用法：
    from client import Bridge
    b = Bridge(r"C:\mediacontrol\resolve_bridge_root")
    print(b.ping())
    print(b.version())
    print(b.create_project("demo"))
    print(b.create_timeline("TL1"))
    print(b.import_media([r"D:\clip.mp4"]))
    print(b.append_to_timeline([...]))

命令行：
    python client.py ping
    python client.py exec "result = RB.version()"
    python client.py run demo.lua
    python client.py stop

协议：inbox/cmd_NNNNNN.lua -> outbox/cmd_NNNNNN.comp（CustomData 里是
      hex 编码的 JSON）。state.lua 告诉监听端从哪条继续。同一时刻只允许
      一个客户端。
"""
import argparse
import binascii
import json
import os
import re
import sys
import time

_NUM = re.compile(r"^cmd_(\d+)\.(lua|comp)(\.cancelled)?$")
_PAYLOAD = re.compile(r'payload\s*=\s*"([0-9a-fA-F]*)"')

DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "root")


class BridgeError(RuntimeError):
    pass


def lua_str(v):
    """Python 值 -> Lua 字面量。够用就好，不追求完备。"""
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return "[==[" + v + "]==]"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_str(x) for x in v) + "}"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            key = "[%s]" % lua_str(str(k)) if not re.match(r"^[A-Za-z_]\w*$", str(k)) else str(k)
            parts.append("%s = %s" % (key, lua_str(x)))
        return "{" + ", ".join(parts) + "}"
    return "[==[" + str(v) + "]==]"


class Bridge:
    def __init__(self, root=None, timeout=120.0, poll=0.2, archive=True):
        root = root or os.environ.get("RESOLVE_BRIDGE_ROOT") or DEFAULT_ROOT
        self.root = os.path.abspath(root)
        self.inbox = os.path.join(self.root, "inbox")
        self.outbox = os.path.join(self.root, "outbox")
        self.archive_dir = os.path.join(self.root, "archive")
        self.renders = os.path.join(self.root, "renders")
        for d in (self.inbox, self.outbox, self.archive_dir, self.renders):
            os.makedirs(d, exist_ok=True)
        self.timeout = timeout
        self.poll = poll
        self.archive = archive

    # ---------------------------------------------------------- 簿记 ----
    def _ids(self, folder):
        out = set()
        try:
            for f in os.listdir(folder):
                m = _NUM.match(f)
                if m:
                    out.add(int(m.group(1)))
        except OSError:
            pass
        return out

    def _write_state(self, first_pending):
        tmp = os.path.join(self.root, "state.lua.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("return { first_pending = %d }\n" % first_pending)
        os.replace(tmp, os.path.join(self.root, "state.lua"))

    def _path(self, folder, n, ext):
        return os.path.join(folder, "cmd_%06d.%s" % (n, ext))

    def _tidy(self):
        inbox, outbox = self._ids(self.inbox), self._ids(self.outbox)
        for n in sorted(inbox & outbox):
            self._archive(n)
        everything = inbox | outbox | self._ids(self.archive_dir)
        pending = sorted(inbox - outbox)
        nxt = (max(everything) + 1) if everything else 1
        nxt = max(nxt, self._read_state())
        return nxt, pending

    def _read_state(self):
        try:
            with open(os.path.join(self.root, "state.lua"), encoding="utf-8") as f:
                m = re.search(r"first_pending\s*=\s*(\d+)", f.read())
            return int(m.group(1)) if m else 1
        except OSError:
            return 1

    def _archive(self, n):
        if not self.archive:
            return
        for folder, ext in ((self.inbox, "lua"), (self.outbox, "comp")):
            p = self._path(folder, n, ext)
            if os.path.exists(p):
                try:
                    os.replace(p, self._path(self.archive_dir, n, ext))
                except OSError:
                    pass

    @staticmethod
    def decode(comp_path):
        with open(comp_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        m = _PAYLOAD.search(text)
        if not m:
            raise BridgeError("comp 里没有 payload: %s" % comp_path)
        return json.loads(binascii.unhexlify(m.group(1)).decode("utf-8", "replace"))

    # ------------------------------------------------------ 核心调用 ----
    def run(self, code, timeout=None):
        """在 Resolve 里跑一段 Lua。返回 dict(status,error,printed,result,elapsed,id)。"""
        timeout = self.timeout if timeout is None else timeout
        n, pending = self._tidy()
        if pending:
            raise BridgeError(
                "命令 %s 还在排队——是不是另一个客户端在跑，或者监听端停了？" % pending)
        self._write_state(n)
        cmd = self._path(self.inbox, n, "lua")
        with open(cmd + ".tmp", "w", encoding="utf-8") as f:
            f.write(code)
        os.replace(cmd + ".tmp", cmd)                     # 原子写，监听端不会读到半个文件
        res_path = self._path(self.outbox, n, "comp")
        t_end = time.time() + timeout
        while time.time() < t_end:
            if os.path.exists(res_path):
                try:
                    res = self.decode(res_path)
                except (BridgeError, ValueError, OSError):
                    time.sleep(self.poll)
                    continue
                self._archive(n)
                self._write_state(n + 1)
                return res
            time.sleep(self.poll)
        if os.path.exists(cmd):
            try:
                os.replace(cmd, self._path(self.archive_dir, n, "lua.cancelled"))
            except OSError:
                pass
        self._write_state(n + 1)
        raise TimeoutError(
            "%ss 内没等到 Resolve 回话——监听端起来了吗？\n"
            "  Workspace > Scripts > Utility > zt_bridge_listen" % timeout)

    def run_file(self, path, timeout=None):
        with open(path, encoding="utf-8") as f:
            return self.run(f.read(), timeout)

    def ping(self, timeout=15):
        return self.run(
            'result = { bridge = bridge.version, page = RB.current_page().page, '
            'comp = comp and comp:GetAttrs().COMPS_Name }', timeout)

    def stop(self, timeout=15):
        return self.run("bridge.stop(); result = 'stopping'", timeout)

    def ok(self, code, timeout=None):
        """跑一条命令，出错直接抛；成功返回结果体。"""
        r = self.run(code, timeout)
        if r.get("status") != "OK":
            raise BridgeError(r.get("error") or "命令执行失败")
        return r.get("result")

    # ==================================================== 高层能力 ====
    # 下面每个方法都拼一小段 Lua，交给 Resolve 里的 RB.* 执行。
    # RB.* 的方法名全部来自本机官方 Scripting README（Resolve 21.0）。

    def version(self):
        return self.ok("result = RB.version()")

    def current_page(self):
        return self.ok("result = RB.current_page()")

    def open_page(self, page):
        """media / cut / edit / fusion / color / fairlight / deliver"""
        return self.ok("result = RB.open_page(%s)" % lua_str(page))

    # ---------------------------------------------------------- 项目 ----
    def project_list(self):
        return self.ok("result = RB.project_list()")

    def create_project(self, name, media_path=None):
        return self.ok("result = RB.create_project(%s, %s)"
                       % (lua_str(name), lua_str(media_path)))

    def load_project(self, name):
        return self.ok("result = RB.load_project(%s)" % lua_str(name))

    def save_project(self):
        return self.ok("result = RB.save_project()")

    def project_info(self):
        return self.ok("result = RB.project_info()")

    def get_setting(self, name):
        return self.ok("result = RB.get_setting(%s)" % lua_str(name))

    def set_setting(self, name, value):
        return self.ok("result = RB.set_setting(%s, %s)" % (lua_str(name), lua_str(value)))

    # -------------------------------------------------------- 时间线 ----
    def timeline_list(self):
        return self.ok("result = RB.timeline_list()")

    def create_timeline(self, name):
        return self.ok("result = RB.create_timeline(%s)" % lua_str(name))

    def timeline_from_clips(self, name, clips_expr="clips"):
        """clips_expr 是 Lua 表达式，指向一组 MediaPoolItem。"""
        return self.ok("result = RB.timeline_from_clips(%s, %s)"
                       % (lua_str(name), clips_expr))

    def set_timeline_by_index(self, i):
        return self.ok("result = RB.set_timeline_by_index(%d)" % int(i))

    def timeline_info(self):
        return self.ok("result = RB.timeline_info()")

    def set_mark_in_out(self, in_f, out_f, kind="all"):
        return self.ok("result = RB.set_mark_in_out(%d, %d, %s)"
                       % (int(in_f), int(out_f), lua_str(kind)))

    def get_mark_in_out(self):
        return self.ok("result = RB.get_mark_in_out()")

    def clear_mark_in_out(self, kind="all"):
        return self.ok("result = RB.clear_mark_in_out(%s)" % lua_str(kind))

    # -------------------------------------------------------- 媒体池 ----
    def import_media(self, paths):
        """paths: [r"D:\\a.mp4", ...] 或 [{"FilePath": "...", "ClipName": "..."}]"""
        return self.ok("result = RB.import_media(%s)" % lua_str(list(paths)))

    def root_clips(self):
        return self.ok("result = RB.root_clips()")

    def append_to_timeline(self, clips_expr="clips"):
        """clips_expr 是 Lua 表达式（通常是上一次 import_media 留下的 _items）。"""
        return self.ok("result = RB.append_to_timeline(%s)" % clips_expr)

    def import_and_append(self, paths, timeline_name=None):
        """一次性：建时间线（可选）→ 导入 → 追加。返回两步结果。"""
        code = (
            'local r1 = RB.import_media(%s)\n'
            'if not r1.ok then result = r1; return end\n'
            'local r2 = RB.append_to_timeline(r1._items)\n'
            'r1._items = nil\n'
            'result = { import = r1, append = r2 }' % lua_str(list(paths))
        )
        if timeline_name:
            code = ('local t0 = RB.create_timeline(%s)\n' % lua_str(timeline_name)) + code
            code = code.replace("result = { import = r1, append = r2 }",
                                "result = { timeline = t0, import = r1, append = r2 }")
        return self.ok(code)

    # ---------------------------------------------------------- 片段 ----
    def items(self, track_type="video", index=1):
        return self.ok("result = RB.items(%s, %d)" % (lua_str(track_type), int(index)))

    def get_property(self, key, track_type="video", index=1, n=1):
        return self.ok("result = RB.get_property(%s, %d, %d, %s)"
                       % (lua_str(track_type), int(index), int(n), lua_str(key)))

    def set_property(self, key, value, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_property(%s, %d, %d, %s, %s)"
                       % (lua_str(track_type), int(index), int(n),
                          lua_str(key), lua_str(value)))

    def set_speed(self, speed, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_speed(%s, %d, %d, %s)"
                       % (lua_str(track_type), int(index), int(n), lua_str(speed)))

    def set_volume(self, db, track_type="audio", index=1, n=1):
        return self.ok("result = RB.set_volume(%s, %d, %d, %s)"
                       % (lua_str(track_type), int(index), int(n), lua_str(db)))

    def set_pan(self, pan, track_type="audio", index=1, n=1):
        return self.ok("result = RB.set_pan(%s, %d, %d, %s)"
                       % (lua_str(track_type), int(index), int(n), lua_str(pan)))

    def set_opacity(self, v, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_opacity(%s, %d, %d, %s)"
                       % (lua_str(track_type), int(index), int(n), lua_str(v)))

    def set_zoom(self, x, y=None, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_zoom(%s, %d, %d, %s, %s)"
                       % (lua_str(track_type), int(index), int(n),
                          lua_str(x), lua_str(y if y is not None else x)))

    def audio_gain_db(self, track_index, db):
        """整条音频轨的片段统一设增益 dB。"""
        return self.ok("result = RB.audio_gain_db(%d, %s)" % (int(track_index), lua_str(db)))

    def delete_clips(self, ns, track_type="video", index=1):
        return self.ok("result = RB.delete_clips(%s, %d, %s)"
                       % (lua_str(track_type), int(index), lua_str(list(ns))))

    # ---------------------------------------------------------- 轨道 ----
    def add_track(self, track_type, sub_type=None):
        return self.ok("result = RB.add_track(%s, %s)"
                       % (lua_str(track_type), lua_str(sub_type)))

    def delete_track(self, track_type, index):
        return self.ok("result = RB.delete_track(%s, %d)" % (lua_str(track_type), int(index)))

    def set_track_enable(self, track_type, index, on):
        return self.ok("result = RB.set_track_enable(%s, %d, %s)"
                       % (lua_str(track_type), int(index), "true" if on else "false"))

    def set_track_lock(self, track_type, index, on):
        return self.ok("result = RB.set_track_lock(%s, %d, %s)"
                       % (lua_str(track_type), int(index), "true" if on else "false"))

    def set_track_name(self, track_type, index, name):
        return self.ok("result = RB.set_track_name(%s, %d, %s)"
                       % (lua_str(track_type), int(index), lua_str(name)))

    # ---------------------------------------------------------- 字幕 ----
    def subtitles_from_audio(self, settings=None):
        return self.ok("result = RB.subtitles_from_audio(%s)" % lua_str(settings or {}))

    def insert_title(self, title_name):
        return self.ok("result = RB.insert_title(%s)" % lua_str(title_name))

    def insert_generator(self, name):
        return self.ok("result = RB.insert_generator(%s)" % lua_str(name))

    # ---------------------------------------------------------- 调色 ----
    def color_nodes(self, track_type="video", index=1, n=1):
        return self.ok("result = RB.color_nodes(%s, %d, %d)"
                       % (lua_str(track_type), int(index), int(n)))

    def node_tools(self, node_index, track_type="video", index=1, n=1):
        return self.ok("result = RB.node_tools(%d, %s, %d, %d)"
                       % (int(node_index), lua_str(track_type), int(index), int(n)))

    def set_node_input(self, node_index, input_id, value, tool_key=None,
                       track_type="video", index=1, n=1):
        return self.ok("result = RB.set_node_input(%d, %s, %s, %s, %s, %d, %d)"
                       % (int(node_index), lua_str(tool_key), lua_str(input_id),
                          lua_str(value), lua_str(track_type), int(index), int(n)))

    def set_lgg(self, node_index, lift, gamma, gain, track_type="video", index=1, n=1):
        """lift/gamma/gain 传 {R,G,B} 或 {1,2,3}（Lua 端原样塞给 SetInput）。"""
        return self.ok("result = RB.set_lgg(%d, %s, %s, %s, %s, %d, %d)"
                       % (int(node_index), lua_str(lift), lua_str(gamma), lua_str(gain),
                          lua_str(track_type), int(index), int(n)))

    def set_wheel(self, node_index, which, rgb, track_type="video", index=1, n=1):
        """which: masterWheel / shadowWheel / midWheel / highlightWheel / 原始 input id"""
        return self.ok("result = RB.set_wheel(%d, %s, %s, %s, %d, %d)"
                       % (int(node_index), lua_str(which), lua_str(rgb),
                          lua_str(track_type), int(index), int(n)))

    def set_lut(self, node_index, lut_path, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_lut(%d, %s, %s, %d, %d)"
                       % (int(node_index), lua_str(lut_path),
                          lua_str(track_type), int(index), int(n)))

    def get_lut(self, node_index, track_type="video", index=1, n=1):
        return self.ok("result = RB.get_lut(%d, %s, %d, %d)"
                       % (int(node_index), lua_str(track_type), int(index), int(n)))

    def set_cdl(self, cdl, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_cdl(%s, %s, %d, %d)"
                       % (lua_str(cdl), lua_str(track_type), int(index), int(n)))

    def apply_drx(self, path, mode=0, track_type="video", index=1, n=1):
        return self.ok("result = RB.apply_drx(%s, %d, %s, %d, %d)"
                       % (lua_str(path), int(mode), lua_str(track_type), int(index), int(n)))

    def set_node_enabled(self, node_index, on, track_type="video", index=1, n=1):
        return self.ok("result = RB.set_node_enabled(%d, %s, %s, %d, %d)"
                       % (int(node_index), "true" if on else "false",
                          lua_str(track_type), int(index), int(n)))

    def export_lut(self, path, export_type=1, track_type="video", index=1, n=1):
        return self.ok("result = RB.export_lut(%d, %s, %s, %d, %d)"
                       % (int(export_type), lua_str(path), lua_str(track_type),
                          int(index), int(n)))

    # ---------------------------------------------------------- 渲染 ----
    def render_settings(self, settings):
        """settings 是 dict，键见 Resolve 项目渲染设置（如 TargetDir/CustomName/FormatWidth...）"""
        return self.ok("result = RB.render_settings(%s)" % lua_str(settings))

    def get_render_settings(self):
        return self.ok("result = RB.get_render_settings()")

    def render_presets(self):
        return self.ok("result = RB.render_presets()")

    def load_render_preset(self, name):
        return self.ok("result = RB.load_render_preset(%s)" % lua_str(name))

    def add_render_job(self):
        return self.ok("result = RB.add_render_job()")

    def render_job_list(self):
        return self.ok("result = RB.render_job_list()")

    def render_status(self, job_id):
        return self.ok("result = RB.render_status(%s)" % lua_str(job_id))

    def start_render(self, job_ids=None, interactive=False):
        if job_ids is None:
            return self.ok("result = RB.start_render(nil, %s)"
                           % ("true" if interactive else "false"))
        return self.ok("result = RB.start_render(%s, %s)"
                       % (lua_str(list(job_ids)), "true" if interactive else "false"))

    def stop_render(self):
        return self.ok("result = RB.stop_render()")

    def is_rendering(self):
        return self.ok("result = RB.is_rendering()")

    def quick_export(self, preset, params=None):
        return self.ok("result = RB.quick_export(%s, %s)"
                       % (lua_str(preset), lua_str(params or {})))

    def render_and_wait(self, settings, poll=3.0, timeout=3600.0):
        """设好渲染参数 → 加任务 → 开渲 → 轮询到结束。返回最后状态。"""
        self.render_settings(settings)
        job = self.add_render_job()
        self.start_render()
        t_end = time.time() + timeout
        last = None
        while time.time() < t_end:
            time.sleep(poll)
            st = self.is_rendering()
            last = st
            if not st.get("rendering"):
                break
        return {"job": job, "last": last}

    # ---------------------------------------------------------- 标记 ----
    def add_marker(self, frame_id, color="Blue", name="", note="", duration=1, custom_data=""):
        return self.ok("result = RB.add_marker(%d, %s, %s, %s, %d, %s)"
                       % (int(frame_id), lua_str(color), lua_str(name), lua_str(note),
                          int(duration), lua_str(custom_data)))

    def markers(self):
        return self.ok("result = RB.markers()")


def main(argv=None):
    ap = argparse.ArgumentParser(description="通过 free_bridge 在 DaVinci Resolve 里跑 Lua")
    ap.add_argument("--root", default=os.environ.get("RESOLVE_BRIDGE_ROOT") or DEFAULT_ROOT)
    ap.add_argument("--timeout", type=float, default=120)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    sub.add_parser("stop")
    e = sub.add_parser("exec"); e.add_argument("code")
    r = sub.add_parser("run"); r.add_argument("file")
    a = ap.parse_args(argv)
    b = Bridge(a.root, timeout=a.timeout)
    if a.cmd == "stop":
        try:
            print(b.stop()["result"])
        except TimeoutError:
            print("监听端没应答。如果卡住了，在", b.root, "里建一个名为 stop 的空文件")
        return 0
    if a.cmd == "ping":
        res = b.ping()
    elif a.cmd == "exec":
        res = b.run(a.code, a.timeout)
    else:
        res = b.run_file(a.file, a.timeout)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("status") == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
