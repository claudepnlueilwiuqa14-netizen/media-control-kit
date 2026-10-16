/**
 * ae_bridge.jsx — After Effects ExtendScript 能力库
 *
 * 用法（配合 ae_cli.py）：
 *     python ae_cli.py run "result = AE.comp(1920,1080,30,10,'demo')"
 *     python ae_cli.py call comp 1920 1080 30 10 demo
 *
 * 或直接在 AE 里：文件 > 脚本 > 运行脚本文件，选一个调用脚本。
 *
 * ⚠ 实测（本机 After Effects 2025，装在 D:\新建文件夹\）：
 *   1) AE **没有** COM Application 接口（HKCR 里只有 AfterEffects.Project.25，
 *      没有 AfterEffects.Application），所以不能像 Photoshop 那样用 pywin32 直连。
 *   2) 真实可用的自动化通道是命令行：
 *          AfterFX.exe -r <脚本.jsx>          （-r = run script）
 *      以及把 .jsx 放进 文件 > 脚本 目录手动执行。
 *   3) ExtendScript 是 ES3：没有 JSON、没有 Array.forEach，一律用 for 循环。
 *   4) 结果通过脚本自己写一个 JSON 文件（AE.json(path, obj)）带出来，
 *      因为命令行跑完 AE 会退出，返回值拿不到。
 */

if (typeof AE === "undefined") { var AE = {}; }

(function (AE) {

    // ------------------------------------------------------------ 基础 ----
    AE.json = function (v) {
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
            for (var i = 0; i < v.length; i++) p.push(AE.json(v[i]));
            return "[" + p.join(",") + "]";
        }
        if (t === "object") {
            var q = [];
            for (var k in v) if (v.hasOwnProperty(k)) q.push(AE.json(k) + ":" + AE.json(v[k]));
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

    /** 把结果写到文件（命令行模式下拿不到返回值，只能落盘） */
    AE.save = function (obj, path) {
        var f = new File(path);
        f.open("w");
        f.write(AE.json(obj));
        f.close();
        return path;
    };

    function proj() {
        if (!app.project) { var p = app.newProject(); }
        return app.project;
    }

    // ------------------------------------------------------ 项目/合成 ----
    AE.comp = safe(function (w, h, fps, duration, name, bg) {
        var c = proj().items.addComp(name || "comp", w || 1920, h || 1080, 1,
                                     duration || 10, fps || 30);
        if (bg) c.bgColor = bg;
        return ok({ name: c.name, id: c.id, width: c.width, height: c.height,
                    duration: c.duration, fps: c.frameRate });
    });

    AE.listComps = safe(function () {
        var out = [], it = proj().items;
        for (var i = 1; i <= it.length; i++) {
            if (it[i] instanceof CompItem) {
                out.push({ name: it[i].name, id: it[i].id,
                           width: it[i].width, height: it[i].height,
                           duration: it[i].duration, fps: it[i].frameRate });
            }
        }
        return ok({ comps: out });
    });

    AE.getComp = safe(function (nameOrIndex) {
        var it = proj().items;
        var c = (typeof nameOrIndex === "number") ? it[nameOrIndex] : null;
        if (!c) {
            for (var i = 1; i <= it.length; i++) {
                if (it[i] instanceof CompItem && it[i].name === nameOrIndex) { c = it[i]; break; }
            }
        }
        if (!c) throw new Error("找不到合成: " + nameOrIndex);
        return ok({ name: c.name, id: c.id, layers: c.numLayers });
    });

    AE.activeComp = safe(function () {
        var c = app.project.activeItem;
        if (!c || !(c instanceof CompItem)) throw new Error("没有激活的合成");
        return ok({ name: c.name, layers: c.numLayers, duration: c.duration });
    });

    // -------------------------------------------------------- 素材 ----
    AE.importFile = safe(function (path, compName) {
        var io = new ImportOptions(new File(path));
        var item = proj().importFile(io);
        if (compName) {
            var target = null, it = proj().items;
            for (var i = 1; i <= it.length; i++) {
                if (it[i] instanceof CompItem && it[i].name === compName) { target = it[i]; break; }
            }
            if (target) target.layers.add(item);
        }
        return ok({ name: item.name, id: item.id, duration: item.duration });
    });

    AE.listFootage = safe(function () {
        var out = [], it = proj().items;
        for (var i = 1; i <= it.length; i++) {
            out.push({ name: it[i].name, typeName: it[i].typeName, id: it[i].id });
        }
        return ok({ items: out });
    });

    // -------------------------------------------------------- 图层 ----
    AE.addSolid = safe(function (compName, color, name, w, h) {
        var c = AE._comp(compName);
        var l = c.layers.addSolid(color || [0, 0, 0], name || "solid",
                                  w || c.width, h || c.height, 1, c.duration);
        return ok({ layer: l.name, index: l.index });
    });

    AE.addText = safe(function (compName, text, opts) {
        opts = opts || {};
        var c = AE._comp(compName);
        var l = c.layers.addText(text);
        var tp = l.property("Source Text").value;
        tp.fontSize = opts.size || 72;
        tp.text = text;
        if (opts.color) tp.fillColor = opts.color;
        if (opts.font) tp.font = opts.font;
        l.property("Source Text").setValue(tp);
        if (opts.name) l.name = opts.name;
        return ok({ layer: l.name, index: l.index, size: tp.fontSize });
    });

    AE.addNull = safe(function (compName, name) {
        var c = AE._comp(compName);
        var l = c.layers.addNull();
        l.name = name || "null";
        return ok({ layer: l.name, index: l.index });
    });

    AE.addAdjustment = safe(function (compName, name) {
        var c = AE._comp(compName);
        var l = c.layers.addSolid([1, 1, 1], name || "adjustment", c.width, c.height, 1, c.duration);
        l.adjustmentLayer = true;
        return ok({ layer: l.name, index: l.index });
    });

    AE.addShape = safe(function (compName, name) {
        var c = AE._comp(compName);
        var l = c.layers.addShape();
        l.name = name || "shape";
        return ok({ layer: l.name, index: l.index });
    });

    AE._comp = function (nameOrIndex) {
        var it = proj().items;
        if (nameOrIndex === undefined || nameOrIndex === null) {
            var c = app.project.activeItem;
            if (c && c instanceof CompItem) return c;
            throw new Error("没有激活的合成，也没指定名字");
        }
        if (typeof nameOrIndex === "number") {
            var t = it[nameOrIndex];
            if (t && t instanceof CompItem) return t;
            throw new Error("索引 " + nameOrIndex + " 不是合成");
        }
        for (var i = 1; i <= it.length; i++) {
            if (it[i] instanceof CompItem && it[i].name === nameOrIndex) return it[i];
        }
        throw new Error("找不到合成: " + nameOrIndex);
    };

    AE._layer = function (compName, layerNameOrIndex) {
        var c = AE._comp(compName);
        if (typeof layerNameOrIndex === "number") return c.layer(layerNameOrIndex);
        var l = c.layer(layerNameOrIndex);
        if (!l) throw new Error("找不到图层: " + layerNameOrIndex);
        return l;
    };

    AE.listLayers = safe(function (compName) {
        var c = AE._comp(compName), out = [];
        for (var i = 1; i <= c.numLayers; i++) {
            var l = c.layer(i);
            out.push({ index: i, name: l.name, enabled: l.enabled,
                       inPoint: l.inPoint, outPoint: l.outPoint,
                       startTime: l.startTime, isText: (l instanceof TextLayer),
                       hasVideo: l.hasVideo, audioActive: l.hasAudio && l.audioActive });
        }
        return ok({ layers: out });
    });

    AE.setLayerProp = safe(function (compName, layerName, prop, value) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        if (p.numKeys > 0) p.setValueAtKey(p.numKeys, value);
        else p.setValue(value);
        return ok({ layer: l.name, prop: prop, value: value });
    });

    AE.getLayerProp = safe(function (compName, layerName, prop) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        return ok({ layer: l.name, prop: prop, value: p.value, numKeys: p.numKeys });
    });

    // ---------------------------------------------------- 关键帧 ----
    AE.setKeyframe = safe(function (compName, layerName, prop, time, value, easeIn, easeOut) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        p.setValueAtTime(time, value);
        var ki = p.nearestKeyIndex(time);
        if (easeIn !== undefined) p.setInterpolationTypeAtKey(ki, easeIn, KeyframeInterpolationType[ easeOut ]);
        return ok({ layer: l.name, prop: prop, time: time, keyIndex: ki });
    });

    AE.removeKeyframe = safe(function (compName, layerName, prop, keyIndex) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        p.removeKey(keyIndex);
        return ok({ removed: keyIndex });
    });

    /** 线性动画：从 from 到 to，共 frames 帧 */
    AE.animate = safe(function (compName, layerName, prop, startTime, endTime, from, to) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        p.setValueAtTime(startTime, from);
        p.setValueAtTime(endTime, to);
        return ok({ layer: l.name, prop: prop, from: from, to: to,
                    start: startTime, end: endTime, numKeys: p.numKeys });
    });

    AE.setEase = safe(function (compName, layerName, prop, keyIndex, easeType) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        p.setInterpolationTypeAtKey(keyIndex, KeyframeInterpolationType[easeType || "BEZIER"],
                                    KeyframeInterpolationType[easeType || "BEZIER"]);
        return ok({ eased: keyIndex, type: easeType || "BEZIER" });
    });

    // ---------------------------------------------------- 表达式 ----
    AE.setExpression = safe(function (compName, layerName, prop, expr) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        if (!p.canSetExpression) throw new Error("该属性不支持表达式: " + prop);
        p.expression = expr;
        return ok({ layer: l.name, prop: prop, expression: expr });
    });

    AE.getExpression = safe(function (compName, layerName, prop) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        return ok({ layer: l.name, prop: prop, expression: p.expression,
                    canSet: p.canSetExpression });
    });

    AE.removeExpression = safe(function (compName, layerName, prop) {
        var l = AE._layer(compName, layerName);
        var p = l.property(prop);
        if (!p) throw new Error("没有属性: " + prop);
        p.expression = "";
        return ok({ removed: prop });
    });

    // -------------------------------------------------------- 效果 ----
    AE.listEffects = safe(function (compName, layerName) {
        var l = AE._layer(compName, layerName);
        var out = [], fx = l.property("ADBE Effect Parade");
        if (!fx) return ok({ effects: out });
        for (var i = 1; i <= fx.numProperties; i++) {
            var e = fx.property(i);
            var params = [];
            for (var j = 1; j <= e.numProperties; j++) {
                params.push({ name: e.property(j).name, value: String(e.property(j).value) });
            }
            out.push({ name: e.name, matchName: e.matchName, enabled: e.enabled, params: params });
        }
        return ok({ effects: out });
    });

    AE.addEffect = safe(function (compName, layerName, effectMatchName) {
        var l = AE._layer(compName, layerName);
        var fx = l.property("ADBE Effect Parade");
        var e = fx.addProperty(effectMatchName);
        return ok({ layer: l.name, effect: e.name, matchName: e.matchName });
    });

    AE.setEffectParam = safe(function (compName, layerName, effectName, paramName, value) {
        var l = AE._layer(compName, layerName);
        var fx = l.property("ADBE Effect Parade");
        var e = fx.property(effectName);
        if (!e) throw new Error("没有效果: " + effectName);
        var p = e.property(paramName);
        if (!p) throw new Error("没有参数: " + paramName);
        p.setValue(value);
        return ok({ effect: effectName, param: paramName, value: value });
    });

    // --------------------------------------------------- 图层操作 ----
    AE.trimLayer = safe(function (compName, layerName, inPoint, outPoint) {
        var l = AE._layer(compName, layerName);
        if (inPoint !== undefined) l.inPoint = inPoint;
        if (outPoint !== undefined) l.outPoint = outPoint;
        return ok({ layer: l.name, inPoint: l.inPoint, outPoint: l.outPoint });
    });

    AE.setLayerTime = safe(function (compName, layerName, startTime) {
        var l = AE._layer(compName, layerName);
        l.startTime = startTime;
        return ok({ layer: l.name, startTime: l.startTime });
    });

    AE.setLayerEnabled = safe(function (compName, layerName, on) {
        var l = AE._layer(compName, layerName);
        l.enabled = !!on;
        return ok({ layer: l.name, enabled: l.enabled });
    });

    AE.setBlendMode = safe(function (compName, layerName, mode) {
        var l = AE._layer(compName, layerName);
        l.blendingMode = BlendingMode[mode] || l.blendingMode;
        return ok({ layer: l.name, blendMode: String(l.blendingMode) });
    });

    AE.setParent = safe(function (compName, childName, parentName) {
        var ch = AE._layer(compName, childName);
        var pa = AE._layer(compName, parentName);
        ch.parent = pa;
        return ok({ child: ch.name, parent: pa.name });
    });

    AE.precompose = safe(function (compName, layerIndices, newName) {
        var c = AE._comp(compName);
        var arr = [];
        for (var i = 0; i < layerIndices.length; i++) arr.push(c.layer(layerIndices[i]));
        var nc = c.layers.precompose(arr, newName || "precomp", true);
        return ok({ precomp: nc.name, id: nc.id });
    });

    AE.addMarker = safe(function (compName, time, comment) {
        var c = AE._comp(compName);
        var m = new MarkerValue(comment || "");
        c.markerProperty.setValueAtTime(time, m);
        return ok({ marker: time, comment: comment });
    });

    // ------------------------------------------- 渲染队列 / 导出 ----
    AE.addToRenderQueue = safe(function (compName, outputPath, template) {
        var c = AE._comp(compName);
        var rq = app.project.renderQueue;
        var item = rq.items.add(c);
        if (template) {
            try { item.applyTemplate(template); } catch (e) { /* 模板不存在就忽略 */ }
        }
        if (outputPath) {
            var om = item.outputModule(1);
            om.file = new File(outputPath);
        }
        return ok({ queued: c.name, queueLength: rq.numItems,
                    status: String(item.status) });
    });

    AE.renderQueueStatus = safe(function () {
        var rq = app.project.renderQueue, out = [];
        for (var i = 1; i <= rq.numItems; i++) {
            out.push({ index: i, comp: rq.item(i).comp.name,
                       status: String(rq.item(i).status),
                       rendering: rq.item(i).status === RQItemStatus.RENDERING });
        }
        return ok({ items: out, rendering: rq.rendering });
    });

    AE.render = safe(function () {
        var rq = app.project.renderQueue;
        if (rq.numItems === 0) throw new Error("渲染队列是空的");
        rq.render();
        return ok({ rendered: true });
    });

    AE.clearRenderQueue = safe(function () {
        var rq = app.project.renderQueue;
        for (var i = rq.numItems; i >= 1; i--) rq.item(i).remove();
        return ok({ cleared: true });
    });

    AE.setOutputModule = safe(function (compName, outputPath, format, template) {
        var c = AE._comp(compName);
        var rq = app.project.renderQueue;
        var item = null;
        for (var i = 1; i <= rq.numItems; i++) if (rq.item(i).comp.id === c.id) { item = rq.item(i); break; }
        if (!item) return err("该合成不在渲染队列里，先 addToRenderQueue");
        var om = item.outputModule(1);
        if (template) om.applyTemplate(template);
        if (format) om.setSettings({ "Output File Info": { "Full Flat Path": outputPath } });
        else om.file = new File(outputPath);
        return ok({ comp: c.name, output: outputPath });
    });

    /** 导出单帧 PNG */
    AE.saveFrame = safe(function (compName, time, outputPath) {
        var c = AE._comp(compName);
        c.saveFrameToPng(time, new File(outputPath));
        return ok({ frame: outputPath, time: time });
    });

    // ------------------------------------------------------ 自检 ----
    AE.selfTest = safe(function () {
        var names = [];
        for (var k in AE) if (AE.hasOwnProperty(k) && typeof AE[k] === "function") names.push(k);
        return ok({ app: app.version, functions: names.length, sample: names.slice(0, 12) });
    });

})(AE);

if (typeof $.global !== "undefined") $.global.AE = AE;

"ae_bridge loaded: " + AE.json(AE.selfTest());
