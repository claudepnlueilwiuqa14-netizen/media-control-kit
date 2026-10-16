/**
 * pr_bridge.jsx — Premiere Pro ExtendScript 能力库
 *
 * ⚠ 通道真相（本机实测 2026-10-16，Premiere Pro 2025）：
 *   1) PR **没有** COM Application 接口（和 AE 一样，注册表里没有
 *      Premiere.Application），不能 pywin32 直连。
 *   2) PR **不支持**命令行 -r（那是 AE 才有）。
 *   3) 真实可用的三条路：
 *      a) 把 .jsx 放进 C:\Users\<你>\Documents\Adobe Scripts\
 *         然后在 PR 里 文件 > 脚本 > 选它运行（PR 必须开着）
 *      b) BridgeTalk 跨应用转发：从 Photoshop（它有 COM！）用 BridgeTalk
 *         把脚本发给 premierepro。见 pr_cli.py
 *      c) UXP 插件（PR 22+ 支持 UXP，和 ps/uxp 那套同一思路）
 *
 * 结果返回：和 ae_bridge 一样，用 PR.save(obj, path) 把 JSON 写到文件，
 *         因为脚本是在 PR 内部跑的，返回值带不出来。
 *
 * ⚠ ExtendScript 是 ES3：没有 JSON、没有 Array.forEach，一律 for 循环。
 */

if (typeof PR === "undefined") { var PR = {}; }

(function (PR) {

    // ------------------------------------------------------------ 基础 ----
    PR.json = function (v) {
        var t = typeof v;
        if (v === null || v === undefined) return "null";
        if (t === "boolean") return v ? "true" : "false";
        if (t === "number") return isFinite(v) ? String(v) : "null";
        if (t === "string") {
            return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
                          .replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t") + '"';
        }
        if (v instanceof Array) {
            var p = [];
            for (var i = 0; i < v.length; i++) p.push(PR.json(v[i]));
            return "[" + p.join(",") + "]";
        }
        if (t === "object") {
            var q = [];
            for (var k in v) if (v.hasOwnProperty(k)) q.push(PR.json(k) + ":" + PR.json(v[k]));
            return "{" + q.join(",") + "}";
        }
        return '"' + String(v).replace(/"/g, '\\"') + '"';
    };

    function ok(x) { var a = { ok: true }; if (x) for (var k in x) if (x.hasOwnProperty(k)) a[k] = x[k]; return a; }
    function err(m) { return { ok: false, err: String(m) }; }
    function safe(fn) {
        return function () {
            try { return fn.apply(null, arguments); }
            catch (e) { return err(e.message || e); }
        };
    }

    PR.save = function (obj, path) {
        var f = new File(path);
        f.open("w"); f.write(PR.json(obj)); f.close();
        return path;
    };

    function proj() {
        if (!app.project) throw new Error("没有打开的项目");
        return app.project;
    }
    function seq() {
        var s = proj().activeSequence;
        if (!s) throw new Error("没有激活的序列");
        return s;
    }

    // -------------------------------------------------------- 项目 ----
    PR.projectInfo = safe(function () {
        var p = proj();
        var s = p.activeSequence;
        return ok({ name: p.name, path: String(p.path),
                    sequences: p.sequences.numSequences,
                    rootItems: p.rootItem.children ? p.rootItem.children.numItems : 0,
                    activeSequence: s ? s.name : null });
    });

    PR.newProject = safe(function (path) {
        app.newProject(path);
        return ok({ created: path });
    });

    PR.openProject = safe(function (path) {
        app.openDocument(path);
        return ok({ opened: path });
    });

    PR.saveProject = safe(function () {
        app.project.save();
        return ok({ saved: String(app.project.path) });
    });

    PR.listSequences = safe(function () {
        var p = proj(), out = [];
        for (var i = 0; i < p.sequences.numSequences; i++) {
            var s = p.sequences[i];
            out.push({ index: i, name: s.name,
                       videoTracks: s.videoTracks.numTracks,
                       audioTracks: s.audioTracks.numTracks,
                       frameRate: s.getSettings ? s.getSettings().videoFrameRate.seconds : null,
                       duration: s.end });
        }
        return ok({ sequences: out });
    });

    // -------------------------------------------------------- Bin ----
    PR.createBin = safe(function (name) {
        var b = proj().rootItem.createBin(name);
        return ok({ bin: b.name });
    });

    PR.listBins = safe(function () {
        var out = [], ch = proj().rootItem.children;
        for (var i = 0; i < ch.numItems; i++) {
            out.push({ name: ch[i].name, type: ch[i].type });
        }
        return ok({ items: out });
    });

    // ------------------------------------------------------ 导入素材 ----
    PR.importFiles = safe(function (paths, binName) {
        var p = proj();
        var target = p.rootItem;
        if (binName) {
            var ch = p.rootItem.children;
            for (var i = 0; i < ch.numItems; i++) {
                if (ch[i].name === binName && ch[i].type === ProjectItemType.BIN) { target = ch[i]; break; }
            }
        }
        var arr = [];
        for (var j = 0; j < paths.length; j++) arr.push(paths[j]);
        var okd = p.importFiles(arr, true, target, false);
        return ok({ imported: arr.length, result: okd, target: target.name });
    });

    PR.listProjectItems = safe(function () {
        var out = [], ch = proj().rootItem.children;
        for (var i = 0; i < ch.numItems; i++) {
            out.push({ name: ch[i].name, type: ch[i].type,
                       isSequence: ch[i].type === ProjectItemType.SEQUENCE,
                       mediaPath: ch[i].getMediaPath ? ch[i].getMediaPath() : null });
        }
        return ok({ items: out });
    });

    // ------------------------------------------------------ 序列 ----
    PR.createSequence = safe(function (name, presetPath) {
        var p = proj();
        // 不传 preset 时，试着用 PR 安装目录里的一个默认预设
        if (!presetPath) { presetPath = PR._defaultPreset(); }
        var s = p.createNewSequence(name, presetPath);
        if (!s) throw new Error("createNewSequence 失败，检查 presetPath: " + presetPath);
        p.activeSequence = s;
        return ok({ sequence: s.name, videoTracks: s.videoTracks.numTracks,
                    audioTracks: s.audioTracks.numTracks });
    });

    PR._defaultPreset = function () {
        try {
            var f = Folder.appPackage;
            var cand = [
                f.fsName + "/Settings/SequencePresets/HD 1080p/HD 1080p 25.sqpreset",
                f.fsName + "/Settings/SequencePresets/HD 1080p/HD 1080p 30.sqpreset",
                f.fsName + "/Settings/SequencePresets/HD 1080p/DSLR 1080p30.sqpreset"
            ];
            for (var i = 0; i < cand.length; i++) {
                if (new File(cand[i]).exists) return cand[i];
            }
            // 退而求其次：扫一下目录里第一个 .sqpreset
            var dir = new Folder(f.fsName + "/Settings/SequencePresets/HD 1080p");
            if (dir.exists) {
                var files = dir.getFiles("*.sqpreset");
                if (files && files.length) return files[0].fsName;
            }
        } catch (e) { /* 找不到就返回空 */ }
        return "";
    };

    PR.setActiveSequence = safe(function (indexOrName) {
        var p = proj();
        var s = (typeof indexOrName === "number") ? p.sequences[indexOrName] : null;
        if (!s) {
            for (var i = 0; i < p.sequences.numSequences; i++) {
                if (p.sequences[i].name === indexOrName) { s = p.sequences[i]; break; }
            }
        }
        if (!s) throw new Error("找不到序列: " + indexOrName);
        p.activeSequence = s;
        return ok({ active: s.name });
    });

    PR.sequenceInfo = safe(function () {
        var s = seq();
        var vt = [], at = [];
        for (var i = 0; i < s.videoTracks.numTracks; i++) {
            vt.push({ index: i, name: s.videoTracks[i].name, clips: s.videoTracks[i].clips.numItems });
        }
        for (var j = 0; j < s.audioTracks.numTracks; j++) {
            at.push({ index: j, name: s.audioTracks[j].name, clips: s.audioTracks[j].clips.numItems });
        }
        return ok({ name: s.name, end: s.end, videoTracks: vt, audioTracks: at });
    });

    // ------------------------------------------------------ 剪辑 ----
    PR.insertClip = safe(function (projectItemName, timeSeconds, trackIndex, audioTrackIndex) {
        var s = seq(), p = proj();
        var item = PR._findItem(projectItemName);
        if (!item) throw new Error("项目里找不到素材: " + projectItemName);
        var t = new Time();
        t.seconds = timeSeconds || 0;
        var vIdx = (trackIndex === undefined) ? 0 : trackIndex;
        var okd = s.videoTracks[vIdx].insertClip(item, t);
        if (audioTrackIndex !== undefined && audioTrackIndex >= 0) {
            try { s.audioTracks[audioTrackIndex].insertClip(item, t); } catch (e2) { /* 没音轨就算了 */ }
        }
        return ok({ inserted: item.name, at: timeSeconds, videoTrack: vIdx, result: okd });
    });

    PR.overwriteClip = safe(function (projectItemName, timeSeconds, trackIndex) {
        var s = seq();
        var item = PR._findItem(projectItemName);
        if (!item) throw new Error("项目里找不到素材: " + projectItemName);
        var t = new Time(); t.seconds = timeSeconds || 0;
        var okd = s.videoTracks[trackIndex || 0].overwriteClip(item, t);
        return ok({ overwrite: item.name, at: timeSeconds, result: okd });
    });

    PR._findItem = function (name) {
        var p = app.project;
        function walk(container) {
            if (!container || !container.children) return null;
            for (var i = 0; i < container.children.numItems; i++) {
                var it = container.children[i];
                if (it.name === name) return it;
                if (it.type === ProjectItemType.BIN) {
                    var r = walk(it);
                    if (r) return r;
                }
            }
            return null;
        }
        return walk(p.rootItem);
    };

    PR.listClips = safe(function (trackIndex, trackType) {
        var s = seq();
        var tr = (trackType === "audio") ? s.audioTracks[trackIndex || 0] : s.videoTracks[trackIndex || 0];
        var out = [];
        for (var i = 0; i < tr.clips.numItems; i++) {
            var c = tr.clips[i];
            out.push({ index: i, name: c.name,
                       start: c.start.seconds, end: c.end.seconds,
                       inPoint: c.inPoint.seconds, outPoint: c.outPoint.seconds,
                       duration: c.duration.seconds });
        }
        return ok({ clips: out });
    });

    PR.removeClip = safe(function (trackIndex, clipIndex, trackType) {
        var s = seq();
        var tr = (trackType === "audio") ? s.audioTracks[trackIndex] : s.videoTracks[trackIndex];
        var c = tr.clips[clipIndex];
        var name = c.name;
        c.remove(false, false);
        return ok({ removed: name });
    });

    PR.setClipInOut = safe(function (trackIndex, clipIndex, inSec, outSec) {
        var s = seq();
        var c = s.videoTracks[trackIndex].clips[clipIndex];
        if (inSec !== undefined) { var t1 = new Time(); t1.seconds = inSec; c.inPoint = t1; }
        if (outSec !== undefined) { var t2 = new Time(); t2.seconds = outSec; c.outPoint = t2; }
        return ok({ clip: c.name, inPoint: c.inPoint.seconds, outPoint: c.outPoint.seconds });
    });

    PR.setClipSpeed = safe(function (trackIndex, clipIndex, speedPct) {
        var s = seq();
        var c = s.videoTracks[trackIndex].clips[clipIndex];
        c.setScaleToFit ? null : null;
        // PR 里变速走 QE 或 clip.speed（百分比）
        try {
            c.speed = speedPct;
            return ok({ clip: c.name, speed: speedPct });
        } catch (e) {
            return err("改速度失败（PR 的 ExtendScript 对变速支持有限，需 QE）: " + e.message);
        }
    });

    // ------------------------------------------------------ 转场 ----
    PR.addTransition = safe(function (trackIndex, clipIndex, transitionName, durationSec) {
        // PR 的标准 DOM 没有直接加转场的 API，要走 QE。
        // 这里先尝试 QE，失败就明确告诉用户。
        try {
            app.enableQE();
            var s = seq();
            var qeTrack = qe.project.getActiveSequence().getVideoTrackAt(trackIndex);
            var okd = qeTrack.addTransition(transitionName, durationSec || 1);
            return ok({ transition: transitionName, result: okd });
        } catch (e) {
            return err("加转场需要 QE，当前失败: " + e.message +
                       " —— 建议在 PR 里手动拖，或用 UXP 面板");
        }
    });

    PR.listTransitions = safe(function () {
        try {
            var out = [];
            if (app.project && app.project.videoTransitions) {
                for (var i = 0; i < app.project.videoTransitions.numItems; i++) {
                    out.push(app.project.videoTransitions[i].name);
                }
            }
            return ok({ transitions: out });
        } catch (e) {
            return ok({ transitions: [], note: "当前版本读不到转场列表: " + e.message });
        }
    });

    // ------------------------------------------------------ 效果/关键帧 ----
    PR.listClipComponents = safe(function (trackIndex, clipIndex) {
        var s = seq();
        var c = s.videoTracks[trackIndex].clips[clipIndex];
        var out = [];
        if (!c.components) return ok({ components: out, note: "该片段没有 components" });
        for (var i = 0; i < c.components.numItems; i++) {
            var comp = c.components[i];
            var props = [];
            if (comp.properties) {
                for (var j = 0; j < comp.properties.numItems; j++) {
                    var p = comp.properties[j];
                    props.push({ name: p.displayName, value: String(p.getValue()),
                                 numKeys: p.getKeysCount ? p.getKeysCount() : 0 });
                }
            }
            out.push({ name: comp.displayName, matchName: comp.matchName, properties: props });
        }
        return ok({ clip: c.name, components: out });
    });

    PR.setEffectParam = safe(function (trackIndex, clipIndex, componentName, propName, value) {
        var s = seq();
        var c = s.videoTracks[trackIndex].clips[clipIndex];
        var comp = null;
        for (var i = 0; i < c.components.numItems; i++) {
            if (c.components[i].displayName === componentName) { comp = c.components[i]; break; }
        }
        if (!comp) throw new Error("片段上没有组件: " + componentName);
        var p = null;
        for (var j = 0; j < comp.properties.numItems; j++) {
            if (comp.properties[j].displayName === propName) { p = comp.properties[j]; break; }
        }
        if (!p) throw new Error("组件上没有属性: " + propName);
        p.setValue(value, true);
        return ok({ component: componentName, prop: propName, value: value });
    });

    PR.addKeyframe = safe(function (trackIndex, clipIndex, componentName, propName, timeSec, value) {
        var s = seq();
        var c = s.videoTracks[trackIndex].clips[clipIndex];
        var comp = null;
        for (var i = 0; i < c.components.numItems; i++) {
            if (c.components[i].displayName === componentName) { comp = c.components[i]; break; }
        }
        if (!comp) throw new Error("片段上没有组件: " + componentName);
        var p = null;
        for (var j = 0; j < comp.properties.numItems; j++) {
            if (comp.properties[j].displayName === propName) { p = comp.properties[j]; break; }
        }
        if (!p) throw new Error("组件上没有属性: " + propName);
        var t = new Time(); t.seconds = timeSec;
        p.setValueAtTime(t.seconds, value, 0, true);
        return ok({ keyframe: propName, time: timeSec, value: value });
    });

    // ------------------------------------------------------ 标记 ----
    PR.addMarker = safe(function (timeSec, name, comment) {
        var s = seq();
        var t = new Time(); t.seconds = timeSec;
        s.markers.createMarker(t.seconds);
        return ok({ marker: timeSec, name: name });
    });

    // ------------------------------------------------------ 导出 ----
    PR.exportSequence = safe(function (outputPath, presetPath, useInOut) {
        var s = seq();
        if (!presetPath) {
            presetPath = PR._defaultEncodePreset();
        }
        var okd = app.encoder.encodeSequence(
            s, outputPath, presetPath,
            (useInOut ? EncodeWorkAreaType.ENCODE_IN_TO_OUT : EncodeWorkAreaType.ENCODE_ENTIRE),
            true);
        return ok({ queued: true, output: outputPath, preset: presetPath, started: okd });
    });

    PR._defaultEncodePreset = function () {
        try {
            var f = Folder.appPackage;
            var cand = [
                f.fsName + "/Settings/ExportPresets/Media Encoder/H.264 1080p.epr",
                f.fsName + "/Settings/ExportPresets/Media Encoder/Match Source - H.264.epr"
            ];
            for (var i = 0; i < cand.length; i++) if (new File(cand[i]).exists) return cand[i];
            var dir = new Folder(f.fsName + "/Settings/ExportPresets/Media Encoder");
            if (dir.exists) {
                var files = dir.getFiles("*.epr");
                if (files && files.length) return files[0].fsName;
            }
        } catch (e) { /* ignore */ }
        return "";
    };

    PR.encoderStatus = safe(function () {
        try {
            return ok({ running: app.encoder.running,
                        error: app.encoder.errorEncountered ? String(app.encoder.errorEncountered) : null });
        } catch (e) {
            return err("读不到编码器状态: " + e.message);
        }
    });

    PR.launchEncoder = safe(function () {
        app.encoder.launchEncoder();
        return ok({ launched: true });
    });

    // ------------------------------------------------------ 自检 ----
    PR.selfTest = safe(function () {
        var names = [];
        for (var k in PR) if (PR.hasOwnProperty(k) && typeof PR[k] === "function") names.push(k);
        return ok({ app: app.version, functions: names.length, sample: names.slice(0, 12) });
    });

})(PR);

if (typeof $.global !== "undefined") $.global.PR = PR;

"pr_bridge loaded: " + PR.json(PR.selfTest());
