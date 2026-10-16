/**
 * ps_bridge.jsx — Photoshop ExtendScript 能力库
 *
 * 用法（配合 ps_cli.py，或直接在 ExtendScript Toolkit / ESTK 里跑）：
 *     //include "ps_bridge.jsx"
 *     ZT.newDocument(1920, 1080, 72, "demo")
 *     ZT.addTextLayer("王承泽最棒", {size: 96, color:[255,214,74]})
 *     ZT.exportPNG("D:/out.png")
 *
 * ⚠ 实测踩坑（本机 Photoshop 26.4.1）：
 *   1) ExtendScript 是 ES3 引擎，**没有 JSON 全局**（实测 `JSON.stringify` 直接报
 *      "JSON 未定义"）。所以本文件自带 ZT.json()，返回的一律是 JSON 字符串。
 *   2) 没有 Array.forEach / Object.keys 之类 ES5 方法，一律用 for 循环。
 *   3) 所有函数返回 {ok:true, ...} 或 {ok:false, err:"..."} 的 JSON 字符串，
 *      不抛异常，方便 Python 侧统一处理。
 *   4) 单位由 app.preferences.rulerUnits 决定，本库不改全局偏好，需要像素时显式传。
 */

#target photoshop

if (typeof ZT === "undefined") {
    var ZT = {};
}

(function (ZT) {

    // ------------------------------------------------------------ 基础 ----
    /** 极简 JSON 序列化（ES3 环境没有原生 JSON） */
    ZT.json = function (v) {
        var t = typeof v;
        if (v === null || v === undefined) return "null";
        if (t === "boolean") return v ? "true" : "false";
        if (t === "number") {
            if (!isFinite(v)) return "null";
            return String(v);
        }
        if (t === "string") {
            var s = v.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
            s = s.replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t");
            return '"' + s + '"';
        }
        if (v instanceof Array) {
            var parts = [];
            for (var i = 0; i < v.length; i++) parts.push(ZT.json(v[i]));
            return "[" + parts.join(",") + "]";
        }
        if (t === "object") {
            var ps = [];
            for (var k in v) {
                if (v.hasOwnProperty(k)) ps.push(ZT.json(k) + ":" + ZT.json(v[k]));
            }
            return "{" + ps.join(",") + "}";
        }
        return '"' + String(v).replace(/"/g, '\\"') + '"';
    };

    function okWrap(extra) {
        var o = { ok: true };
        if (extra) for (var k in extra) if (extra.hasOwnProperty(k)) o[k] = extra[k];
        return ZT.json(o);
    }

    function errWrap(msg) {
        return ZT.json({ ok: false, err: String(msg) });
    }

    /** 统一包一层 try/catch，任何函数出错都返回 JSON 错误而不是炸掉 */
    function safe(fn) {
        return function () {
            try {
                return fn.apply(null, arguments);
            } catch (e) {
                return errWrap(e.message || e);
            }
        };
    }

    function doc() {
        if (app.documents.length === 0) throw new Error("没有打开的文档");
        return app.activeDocument;
    }

    function rgb(arr) {
        var c = new SolidColor();
        c.rgb.red = arr[0];
        c.rgb.green = arr[1];
        c.rgb.blue = arr[2];
        return c;
    }
    ZT.rgb = rgb;

    function parseColor(c) {
        if (typeof c === "string") { var s = new SolidColor(); s.rgb.hexValue = c; return s; }
        return rgb(c);
    }

    // -------------------------------------------------------- 文档 ----
    ZT.newDocument = safe(function (w, h, res, name, mode, fill) {
        var m = NewDocumentMode.RGB;
        if (mode === "CMYK") m = NewDocumentMode.CMYK;
        else if (mode === "GRAY") m = NewDocumentMode.GRAYSCALE;
        else if (mode === "LAB") m = NewDocumentMode.LAB;
        var f = DocumentFill.WHITE;
        if (fill === "transparent") f = DocumentFill.TRANSPARENT;
        else if (fill === "black") f = DocumentFill.BLACK;
        var d = app.documents.add(
            new UnitValue(w, "px"), new UnitValue(h, "px"),
            res || 72, name || "untitled", m, f);
        return okWrap({ name: d.name, width: String(d.width), height: String(d.height) });
    });

    ZT.openDocument = safe(function (path) {
        var d = app.open(new File(path));
        return okWrap({ name: d.name, path: String(d.fullName) });
    });

    ZT.closeDocument = safe(function (save) {
        doc().close(save ? SaveOptions.SAVECHANGES : SaveOptions.DONOTSAVECHANGES);
        return okWrap({ closed: true });
    });

    ZT.documentInfo = safe(function () {
        var d = doc();
        // 未保存的文档取 fullName 会抛 "尚未存储该文档"，必须单独兜
        var p = null, prof = null;
        try { p = String(d.fullName); } catch (e1) { p = null; }
        try { prof = d.colorProfileName; } catch (e2) { prof = null; }
        return okWrap({
            name: d.name,
            width: String(d.width),
            height: String(d.height),
            resolution: d.resolution,
            mode: String(d.mode),
            layers: d.artLayers.length,
            layerSets: d.layerSets.length,
            saved: p !== null,
            path: p,
            colorProfile: prof
        });
    });

    ZT.listDocuments = safe(function () {
        var out = [];
        for (var i = 0; i < app.documents.length; i++) {
            out.push({ index: i, name: app.documents[i].name });
        }
        return okWrap({ documents: out });
    });

    ZT.setActiveDocument = safe(function (indexOrName) {
        var d = (typeof indexOrName === "number") ? app.documents[indexOrName] : app.documents.getByName(indexOrName);
        app.activeDocument = d;
        return okWrap({ active: d.name });
    });

    ZT.resizeImage = safe(function (w, h, res) {
        var d = doc();
        d.resizeImage(new UnitValue(w, "px"), new UnitValue(h, "px"), res || d.resolution, ResampleMethod.BICUBIC);
        return okWrap({ width: String(d.width), height: String(d.height) });
    });

    ZT.resizeCanvas = safe(function (w, h, anchor) {
        var d = doc();
        d.resizeCanvas(new UnitValue(w, "px"), new UnitValue(h, "px"),
            anchor ? StrToEnum(anchor) : AnchorPosition.MIDDLECENTER);
        return okWrap({ width: String(d.width), height: String(d.height) });
    });

    ZT.trim = safe(function () { doc().trim(TrimType.TRANSPARENT, true, true, true, true); return okWrap({ trimmed: true }); });

    // -------------------------------------------------------- 图层 ----
    ZT.addLayer = safe(function (name) {
        var l = doc().artLayers.add();
        if (name) l.name = name;
        return okWrap({ name: l.name, index: l.itemIndex });
    });

    ZT.addGroup = safe(function (name) {
        var g = doc().layerSets.add();
        if (name) g.name = name;
        return okWrap({ name: g.name });
    });

    /** 按名字找图层（递归进组） */
    ZT.findLayer = function (name, container) {
        container = container || doc();
        for (var i = 0; i < container.artLayers.length; i++) {
            if (container.artLayers[i].name === name) return container.artLayers[i];
        }
        for (var j = 0; j < container.layerSets.length; j++) {
            var r = ZT.findLayer(name, container.layerSets[j]);
            if (r) return r;
        }
        return null;
    };

    ZT.layerInfo = safe(function (nameOrIndex) {
        var d = doc();
        var l = (typeof nameOrIndex === "string") ? ZT.findLayer(nameOrIndex) : d.artLayers[nameOrIndex];
        if (!l) throw new Error("找不到图层: " + nameOrIndex);
        return okWrap({
            name: l.name, kind: String(l.kind), visible: l.visible,
            opacity: l.opacity, blendMode: String(l.blendMode),
            bounds: [String(l.bounds[0]), String(l.bounds[1]), String(l.bounds[2]), String(l.bounds[3])],
            locked: l.allLocked, isBackground: l.isBackgroundLayer
        });
    });

    ZT.listLayers = safe(function () {
        var d = doc(), out = [];
        for (var i = 0; i < d.layers.length; i++) {
            var l = d.layers[i];
            out.push({
                name: l.name,
                kind: String(l.kind),
                typename: l.typename,
                visible: l.visible,
                opacity: l.opacity,
                isGroup: (l.typename === "LayerSet")
            });
        }
        return okWrap({ layers: out });
    });

    ZT.setLayerVisible = safe(function (name, on) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        l.visible = !!on; return okWrap({ name: name, visible: l.visible });
    });

    ZT.setLayerOpacity = safe(function (name, v) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        l.opacity = v; return okWrap({ name: name, opacity: l.opacity });
    });

    ZT.setLayerBlendMode = safe(function (name, mode) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        l.blendMode = StrToEnum(mode);
        return okWrap({ name: name, blendMode: String(l.blendMode) });
    });

    ZT.renameLayer = safe(function (name, newName) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        l.name = newName; return okWrap({ from: name, to: newName });
    });

    ZT.duplicateLayer = safe(function (name, newName) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        var c = l.duplicate(); c.name = newName || (name + " copy");
        return okWrap({ name: c.name });
    });

    ZT.removeLayer = safe(function (name) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        l.remove(); return okWrap({ removed: name });
    });

    ZT.moveLayer = safe(function (name, position) {
        // position: "front" | "back" | "forward" | "backward" | 目标图层名
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        if (position === "front") l.move(doc(), ElementPlacement.PLACEATBEGINNING);
        else if (position === "back") l.move(doc(), ElementPlacement.PLACEATEND);
        else if (position === "forward") l.move(doc(), ElementPlacement.PLACEBEFORE);
        else if (position === "backward") l.move(doc(), ElementPlacement.PLACEAFTER);
        else {
            var t = ZT.findLayer(position);
            if (!t) throw new Error("no target layer " + position);
            l.move(t, ElementPlacement.PLACEBEFORE);
        }
        return okWrap({ moved: name, to: position });
    });

    ZT.rasterizeLayer = safe(function (name) {
        var l = ZT.findLayer(name); if (!l) throw new Error("no layer " + name);
        l.rasterize(RasterizeType.ENTIRELAYER);
        return okWrap({ rasterized: name });
    });

    ZT.mergeVisible = safe(function () { doc().mergeVisibleLayers(); return okWrap({ merged: true }); });
    ZT.flatten = safe(function () { doc().flatten(); return okWrap({ flattened: true }); });

    // -------------------------------------------------------- 蒙版 ----
    ZT.addMask = safe(function (layerName, kind) {
        // kind: "reveal" | "hide" | "fromSelection"
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        var d = doc();
        var id = stringIDToTypeID("make");
        var desc = new ActionDescriptor();
        desc.putClass(charIDToTypeID("Nw  "), charIDToTypeID("Chnl"));
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("Msk "));
        desc.putReference(charIDToTypeID("At  "), ref);
        var k = charIDToTypeID("RvlA");           // reveal all
        if (kind === "hide") k = charIDToTypeID("HdAl");
        else if (kind === "fromSelection") k = charIDToTypeID("RvlS");
        desc.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("UsrM"), k);
        executeAction(id, desc, DialogModes.NO);
        return okWrap({ masked: l.name, kind: kind || "reveal" });
    });

    ZT.removeMask = safe(function (layerName, apply) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("Msk "));
        desc.putReference(charIDToTypeID("null"), ref);
        executeAction(charIDToTypeID("Dlt "), desc, DialogModes.NO);
        return okWrap({ maskRemoved: l.name });
    });

    ZT.setMaskFeather = safe(function (layerName, radius) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
        desc.putReference(charIDToTypeID("null"), ref);
        var d2 = new ActionDescriptor();
        d2.putUnitDouble(charIDToTypeID("Fthr"), charIDToTypeID("#Pxl"), radius);
        desc.putObject(charIDToTypeID("T   "), charIDToTypeID("Chnl"), d2);
        executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);
        return okWrap({ feather: radius });
    });

    ZT.setMaskDensity = safe(function (layerName, density) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
        desc.putReference(charIDToTypeID("null"), ref);
        var d2 = new ActionDescriptor();
        d2.putInteger(charIDToTypeID("Dnst"), density);
        desc.putObject(charIDToTypeID("T   "), charIDToTypeID("Chnl"), d2);
        executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);
        return okWrap({ density: density });
    });

    ZT.enableMask = safe(function (layerName, on) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
        desc.putReference(charIDToTypeID("null"), ref);
        var d2 = new ActionDescriptor();
        d2.putBoolean(charIDToTypeID("enab"), !!on);
        desc.putObject(charIDToTypeID("T   "), charIDToTypeID("Chnl"), d2);
        executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);
        return okWrap({ maskEnabled: !!on });
    });

    // -------------------------------------------------------- 通道 ----
    ZT.listChannels = safe(function () {
        var d = doc(), out = [];
        for (var i = 0; i < d.channels.length; i++) {
            out.push({ name: d.channels[i].name, kind: String(d.channels[i].kind),
                       visible: d.channels[i].visible });
        }
        return okWrap({ channels: out });
    });

    ZT.addAlphaChannel = safe(function (name) {
        var c = doc().channels.add();
        c.kind = ChannelType.MASKEDAREA;
        if (name) c.name = name;
        return okWrap({ channel: c.name });
    });

    ZT.removeChannel = safe(function (name) {
        doc().channels.getByName(name).remove();
        return okWrap({ removed: name });
    });

    ZT.loadChannelAsSelection = safe(function (name) {
        var d = doc();
        var c = d.channels.getByName(name);
        d.selection.load(c);
        return okWrap({ selectionFrom: name });
    });

    // -------------------------------------------------------- 选区 ----
    ZT.selectRect = safe(function (x1, y1, x2, y2, feather, antiAlias) {
        var d = doc();
        var r = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]];
        d.selection.select(r);
        if (feather) d.selection.feather(feather);
        d.selection.antialias = antiAlias !== false;
        return okWrap({ selection: "rect" });
    });

    ZT.selectEllipse = safe(function (x1, y1, x2, y2, feather) {
        var d = doc();
        var r = [];
        var cx = (x1 + x2) / 2, cy = (y1 + y2) / 2, rx = (x2 - x1) / 2, ry = (y2 - y1) / 2;
        for (var a = 0; a < Math.PI * 2; a += Math.PI / 16) {
            r.push([cx + rx * Math.cos(a), cy + ry * Math.sin(a)]);
        }
        d.selection.select(r);
        if (feather) d.selection.feather(feather);
        return okWrap({ selection: "ellipse" });
    });

    ZT.selectAll = safe(function () { doc().selection.selectAll(); return okWrap({ selection: "all" }); });
    ZT.deselect = safe(function () { doc().selection.deselect(); return okWrap({ selection: "none" }); });
    ZT.invertSelection = safe(function () { doc().selection.invert(); return okWrap({ inverted: true }); });

    ZT.modifySelection = safe(function (op, amount) {
        var d = doc();
        if (op === "expand") d.selection.expand(amount);
        else if (op === "contract") d.selection.contract(amount);
        else if (op === "border") d.selection.border(amount);
        else if (op === "smooth") d.selection.smooth(amount);
        else throw new Error("op 必须是 expand/contract/border/smooth");
        return okWrap({ op: op, amount: amount });
    });

    ZT.featherSelection = safe(function (r) { doc().selection.feather(r); return okWrap({ feather: r }); });
    ZT.fillSelection = safe(function (color) {
        doc().selection.fill(parseColor(color || [255, 255, 255]));
        return okWrap({ filled: true });
    });
    ZT.clearSelection = safe(function () { doc().selection.clear(); return okWrap({ cleared: true }); });

    ZT.saveSelection = safe(function (name) {
        var d = doc();
        var c = d.channels.add();
        c.kind = ChannelType.MASKEDAREA;
        c.name = name || "selection";
        d.selection.store(c);
        return okWrap({ savedAs: c.name });
    });

    // ------------------------------------------------------ 调整层 ----
    /**
     * ⚠ 实测（PS 26.4.1）：**不能**靠 `layer.kind = LayerKind.LEVELS` 造调整层，
     * Photoshop 会报 "您只能将图层的类型更改为文本或正常"。
     * 调整层只能走 ActionManager：make -> adjustmentLayer -> using -> {Type: <种类>}。
     */
    function makeAdjustmentLayer(typeId) {
        var d = doc();
        var before = d.layers.length;
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putClass(stringIDToTypeID("adjustmentLayer"));
        desc.putReference(charIDToTypeID("null"), ref);
        var inner = new ActionDescriptor();
        inner.putClass(charIDToTypeID("Type"), stringIDToTypeID(typeId));
        desc.putObject(charIDToTypeID("Usng"), stringIDToTypeID("adjustmentLayer"), inner);
        executeAction(charIDToTypeID("Mk  "), desc, DialogModes.NO);
        var l = d.activeLayer;
        return { layer: l, created: d.layers.length !== before };
    }

    function adjust(typeId, label) {
        var r = makeAdjustmentLayer(typeId);
        return okWrap({ layer: r.layer.name, kind: label, created: r.created });
    }

    ZT.addLevels = safe(function () { return adjust("levels", "levels"); });
    ZT.addCurves = safe(function () { return adjust("curves", "curves"); });
    ZT.addExposure = safe(function () { return adjust("exposure", "exposure"); });
    ZT.addVibrance = safe(function () { return adjust("vibrance", "vibrance"); });
    ZT.addHueSaturation = safe(function () { return adjust("hueSaturation", "hueSaturation"); });
    ZT.addColorBalance = safe(function () { return adjust("colorBalance", "colorBalance"); });
    ZT.addBlackWhite = safe(function () { return adjust("blackAndWhite", "blackAndWhite"); });
    ZT.addPhotoFilter = safe(function () { return adjust("photoFilter", "photoFilter"); });
    ZT.addChannelMixer = safe(function () { return adjust("channelMixer", "channelMixer"); });
    ZT.addColorLookup = safe(function () { return adjust("colorLookup", "colorLookup"); });
    ZT.addSelectiveColor = safe(function () { return adjust("selectiveColor", "selectiveColor"); });
    ZT.addGradientMap = safe(function () { return adjust("gradientMap", "gradientMap"); });
    ZT.addInvert = safe(function () { return adjust("invert", "invert"); });
    ZT.addPosterize = safe(function () { return adjust("posterize", "posterize"); });
    ZT.addThreshold = safe(function () { return adjust("threshold", "threshold"); });

    /** 亮度/对比度：ActionManager 建层后再写数值（数值也要走 AM） */
    ZT.addBrightnessContrast = safe(function (brightness, contrast) {
        var r = makeAdjustmentLayer("brightnessContrast");
        if (brightness === undefined && contrast === undefined) {
            return okWrap({ layer: r.layer.name, kind: "brightnessContrast" });
        }
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
        desc.putReference(charIDToTypeID("null"), ref);
        var inner = new ActionDescriptor();
        if (brightness !== undefined) inner.putInteger(charIDToTypeID("Brgh"), brightness);
        if (contrast !== undefined) inner.putInteger(charIDToTypeID("Cntr"), contrast);
        desc.putObject(charIDToTypeID("T   "), stringIDToTypeID("brightnessContrast"), inner);
        executeAction(charIDToTypeID("setd"), desc, DialogModes.NO);
        return okWrap({ layer: r.layer.name, kind: "brightnessContrast",
                        brightness: brightness, contrast: contrast });
    });

    /** 纯色填充层 */
    ZT.addSolidColor = safe(function (color, name) {
        var d = doc();
        var desc = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putClass(stringIDToTypeID("contentLayer"));
        desc.putReference(charIDToTypeID("null"), ref);
        var inner = new ActionDescriptor();
        var cDesc = new ActionDescriptor();
        var c = parseColor(color || [255, 255, 255]);
        cDesc.putDouble(charIDToTypeID("Rd  "), c.rgb.red);
        cDesc.putDouble(charIDToTypeID("Grn "), c.rgb.green);
        cDesc.putDouble(charIDToTypeID("Bl  "), c.rgb.blue);
        inner.putObject(charIDToTypeID("Clr "), charIDToTypeID("RGBC"), cDesc);
        desc.putObject(charIDToTypeID("Usng"), stringIDToTypeID("contentLayer"), inner);
        executeAction(charIDToTypeID("Mk  "), desc, DialogModes.NO);
        if (name) d.activeLayer.name = name;
        return okWrap({ layer: d.activeLayer.name, kind: "solidColor" });
    });

    /** 渐变填充层 */
    ZT.addGradientFill = safe(function (name) {
        var r = makeAdjustmentLayer("gradientFill");
        if (name) r.layer.name = name;
        return okWrap({ layer: r.layer.name, kind: "gradientFill" });
    });

    // ---------------------------------------------------- 智能对象 ----
    ZT.convertToSmartObject = safe(function (layerName) {
        var d = doc();
        var l = layerName ? ZT.findLayer(layerName) : d.activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        d.activeLayer = l;
        // 第二个参数必须传空 ActionDescriptor，传 undefined 会报"命令当前不可用"
        executeAction(stringIDToTypeID("newPlacedLayer"), new ActionDescriptor(), DialogModes.NO);
        return okWrap({ smartObject: d.activeLayer.name });
    });

    ZT.rasterizeSmartObject = safe(function (layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.rasterize(RasterizeType.ENTIRELAYER);
        return okWrap({ rasterized: l.name });
    });

    ZT.placeEmbedded = safe(function (filePath) {
        var d = doc();
        var desc = new ActionDescriptor();
        desc.putPath(charIDToTypeID("null"), new File(filePath));
        desc.putEnumerated(charIDToTypeID("FTcs"), charIDToTypeID("QCSt"), charIDToTypeID("Qcsa"));
        var od = new ActionDescriptor();
        od.putUnitDouble(charIDToTypeID("Hrzn"), charIDToTypeID("#Pxl"), 0);
        od.putUnitDouble(charIDToTypeID("Vrtc"), charIDToTypeID("#Pxl"), 0);
        desc.putObject(charIDToTypeID("Ofst"), charIDToTypeID("Ofst"), od);
        executeAction(charIDToTypeID("Plc "), desc, DialogModes.NO);
        return okWrap({ placed: filePath, layer: d.activeLayer.name });
    });

    ZT.replaceSmartObjectContents = safe(function (layerName, newFilePath) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        doc().activeLayer = l;
        var desc = new ActionDescriptor();
        desc.putPath(stringIDToTypeID("replace"), new File(newFilePath));
        executeAction(stringIDToTypeID("placedLayerReplaceContents"), desc, DialogModes.NO);
        return okWrap({ replaced: l.name, newPath: newFilePath });
    });

    // ------------------------------------------------------ 文字层 ----
    ZT.addTextLayer = safe(function (text, opts) {
        opts = opts || {};
        var d = doc();
        var l = d.artLayers.add();
        l.kind = LayerKind.TEXT;
        var ti = l.textItem;
        ti.contents = text;
        if (opts.size) ti.size = new UnitValue(opts.size, "px");
        if (opts.font) ti.font = opts.font;
        if (opts.color) ti.color = parseColor(opts.color);
        if (opts.position) ti.position = [new UnitValue(opts.position[0], "px"),
                                          new UnitValue(opts.position[1], "px")];
        if (opts.justification) ti.justification = StrToEnum(opts.justification);
        if (opts.leading) ti.leading = opts.leading;
        if (opts.tracking) ti.tracking = opts.tracking;
        if (opts.name) l.name = opts.name;
        return okWrap({ layer: l.name, text: text, size: String(ti.size) });
    });

    ZT.setText = safe(function (layerName, text) {
        var l = ZT.findLayer(layerName); if (!l) throw new Error("no layer " + layerName);
        if (l.kind !== LayerKind.TEXT) throw new Error("不是文字层: " + layerName);
        l.textItem.contents = text;
        return okWrap({ layer: layerName, text: text });
    });

    ZT.setTextStyle = safe(function (layerName, opts) {
        var l = ZT.findLayer(layerName); if (!l) throw new Error("no layer " + layerName);
        var ti = l.textItem;
        if (opts.size !== undefined) ti.size = new UnitValue(opts.size, "px");
        if (opts.font) ti.font = opts.font;
        if (opts.color) ti.color = parseColor(opts.color);
        if (opts.leading !== undefined) ti.leading = opts.leading;
        if (opts.tracking !== undefined) ti.tracking = opts.tracking;
        if (opts.justification) ti.justification = StrToEnum(opts.justification);
        return okWrap({ layer: layerName, size: String(ti.size), font: ti.font });
    });

    ZT.getTextInfo = safe(function (layerName) {
        var l = ZT.findLayer(layerName); if (!l) throw new Error("no layer " + layerName);
        var ti = l.textItem;
        // 自动行距时读 ti.leading 会抛 "文本设置为自动行距"，必须兜住
        var leading = null, tracking = null, color = null;
        try { leading = String(ti.leading); } catch (e1) { leading = "auto"; }
        try { tracking = ti.tracking; } catch (e2) { tracking = null; }
        try { color = ti.color.rgb.hexValue; } catch (e3) { color = null; }
        return okWrap({
            layer: l.name, text: ti.contents, font: ti.font,
            size: String(ti.size), color: color,
            leading: leading, tracking: tracking,
            justification: String(ti.justification),
            position: [String(ti.position[0]), String(ti.position[1])]
        });
    });

    ZT.listFonts = safe(function () {
        var out = [];
        for (var i = 0; i < app.fonts.length; i++) out.push(app.fonts[i].postScriptName);
        return okWrap({ fonts: out });
    });

    // ------------------------------------------------------ 滤镜 ----
    ZT.gaussianBlur = safe(function (radius, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyGaussianBlur(radius);
        return okWrap({ filter: "gaussianBlur", radius: radius });
    });

    ZT.unsharpMask = safe(function (amount, radius, threshold, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyUnSharpMask(amount || 50, radius || 1.0, threshold || 0);
        return okWrap({ filter: "unsharpMask" });
    });

    ZT.addNoise = safe(function (amount, distribution, monochromatic, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyAddNoise(amount || 5,
            distribution === "gaussian" ? NoiseDistribution.GAUSSIAN : NoiseDistribution.UNIFORM,
            monochromatic !== false);
        return okWrap({ filter: "addNoise", amount: amount });
    });

    ZT.motionBlur = safe(function (angle, distance, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyMotionBlur(angle || 0, distance || 10);
        return okWrap({ filter: "motionBlur" });
    });

    ZT.radialBlur = safe(function (amount, method, quality, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyRadialBlur(amount || 10,
            method === "spin" ? RadialBlurMethod.SPIN : RadialBlurMethod.ZOOM,
            quality || RadialBlurQuality.GOOD);
        return okWrap({ filter: "radialBlur" });
    });

    ZT.lensBlur = safe(function (layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyLensBlur();
        return okWrap({ filter: "lensBlur" });
    });

    ZT.dustAndScratches = safe(function (radius, threshold, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyDustAndScratches(radius || 2, threshold || 0);
        return okWrap({ filter: "dustAndScratches" });
    });

    ZT.highPass = safe(function (radius, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.applyHighPass(radius || 3);
        return okWrap({ filter: "highPass" });
    });

    ZT.transformScale = safe(function (wPct, hPct, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.resize(wPct, hPct, AnchorPosition.MIDDLECENTER);
        return okWrap({ scaled: [wPct, hPct] });
    });

    ZT.transformRotate = safe(function (angle, layerName) {
        var l = layerName ? ZT.findLayer(layerName) : doc().activeLayer;
        if (!l) throw new Error("no layer " + layerName);
        l.rotate(angle, AnchorPosition.MIDDLECENTER);
        return okWrap({ rotated: angle });
    });

    // ------------------------------------------------------ 动作 ----
    ZT.listActionSets = safe(function () {
        // 某些 PS 版本/状态下 app.actionSets 不可用（实测报 "undefined 不是对象"），
        // 这里兜住并返回空列表，而不是让整条命令炸掉。
        if (typeof app.actionSets === "undefined" || app.actionSets === null) {
            return okWrap({ actionSets: [], note: "app.actionSets 不可用" });
        }
        var out = [];
        for (var i = 0; i < app.actionSets.length; i++) {
            var s = app.actionSets[i], acts = [];
            if (s && s.actions) {
                for (var j = 0; j < s.actions.length; j++) acts.push(s.actions[j].name);
            }
            out.push({ set: s ? s.name : "?", actions: acts });
        }
        return okWrap({ actionSets: out });
    });

    ZT.playAction = safe(function (setName, actionName) {
        app.doAction(actionName, setName);
        return okWrap({ played: setName + " / " + actionName });
    });

    ZT.loadActionSet = safe(function (filePath) {
        app.loadAction(new File(filePath));
        return okWrap({ loaded: filePath });
    });

    ZT.batch = safe(function (inputFolder, actionSet, actionName, outputFolder, opts) {
        // 用 BatchOptions + app.batch() 走文件 > 自动 > 批处理
        var bo = new BatchOptions();
        bo.source = BatchSourceType.FOLDER;
        bo.sourceFolder = new Folder(inputFolder);
        bo.destination = BatchDestinationType.FOLDER;
        bo.destinationFolder = new Folder(outputFolder);
        bo.action = actionName;
        bo.actionSet = actionSet;
        bo.overrideOpen = (opts && opts.suppressOpenDialogs) !== false;
        bo.overrideSave = (opts && opts.suppressSaveDialogs) !== false;
        if (opts && opts.fileNaming) bo.fileNaming = opts.fileNaming;
        app.batch([], bo);
        return okWrap({ batched: true, from: inputFolder, to: outputFolder });
    });

    // ------------------------------------------------------ 导出 ----
    function ensureFolder(p) {
        var f = new File(p);
        var dir = f.parent;
        if (dir && !dir.exists) dir.create();
        return f;
    }
    ZT._ensureFolder = ensureFolder;

    ZT.exportPNG = safe(function (path, interlaced) {
        var d = doc();
        var o = new PNGSaveOptions();
        o.interlaced = interlaced === true;
        d.saveAs(ensureFolder(path), o, true, Extension.LOWERCASE);
        return okWrap({ exported: path });
    });

    ZT.exportJPEG = safe(function (path, quality) {
        var d = doc();
        var o = new JPEGSaveOptions();
        o.quality = quality || 10;
        o.embedColorProfile = true;
        o.formatOptions = FormatOptions.STANDARDBASELINE;
        d.saveAs(ensureFolder(path), o, true, Extension.LOWERCASE);
        return okWrap({ exported: path, quality: o.quality });
    });

    ZT.exportPSD = safe(function (path, maximize) {
        var d = doc();
        var o = new PhotoshopSaveOptions();
        o.layers = true;
        o.maximizeCompatibility = maximize !== false;
        d.saveAs(ensureFolder(path), o, true, Extension.LOWERCASE);
        return okWrap({ exported: path });
    });

    ZT.exportTIFF = safe(function (path) {
        var d = doc();
        var o = new TiffSaveOptions();
        o.layers = true;
        o.imageCompression = TIFFEncoding.TIFFLZW;
        d.saveAs(ensureFolder(path), o, true, Extension.LOWERCASE);
        return okWrap({ exported: path });
    });

    ZT.exportWeb = safe(function (path, quality, png8) {
        // 存储为 Web 所用格式
        var d = doc();
        var o = new ExportOptionsSaveForWeb();
        o.format = png8 ? SaveDocumentType.PNG : SaveDocumentType.JPEG;
        o.quality = quality || 80;
        o.optimized = true;
        d.exportDocument(ensureFolder(path), ExportType.SAVEFORWEB, o);
        return okWrap({ exported: path });
    });

    ZT.exportLayersToFiles = safe(function (outputDir, prefix) {
        // 把每个顶层图层单独导出成 PNG
        var d = doc();
        var dir = new Folder(outputDir);
        if (!dir.exists) dir.create();
        var out = [];
        for (var i = 0; i < d.layers.length; i++) {
            var l = d.layers[i];
            var wasVisible = l.visible;
            for (var j = 0; j < d.layers.length; j++) d.layers[j].visible = false;
            l.visible = true;
            var f = new File(outputDir + "/" + (prefix || "") + l.name + ".png");
            var o = new PNGSaveOptions();
            d.saveAs(f, o, true, Extension.LOWERCASE);
            l.visible = wasVisible;
            out.push(String(f.fsName));
        }
        return okWrap({ exported: out.length, files: out });
    });

    // ------------------------------------------------------ 历史 ----
    ZT.undo = safe(function () {
        var d = doc();
        d.activeHistoryState = d.historyStates[d.historyStates.length - 2];
        return okWrap({ undone: true });
    });

    ZT.setHistoryState = safe(function (name) {
        var d = doc();
        for (var i = 0; i < d.historyStates.length; i++) {
            if (d.historyStates[i].name === name) {
                d.activeHistoryState = d.historyStates[i];
                return okWrap({ state: name });
            }
        }
        throw new Error("没有历史状态: " + name);
    });

    /** 字符串 -> 枚举，如 "MULTIPLY" / "CENTERJUSTIFIED" */
    function StrToEnum(s) {
        // 常见的几组，够用；找不到就原样返回让 Photoshop 报错
        var map = {
            "MULTIPLY": BlendMode.MULTIPLY, "SCREEN": BlendMode.SCREEN,
            "OVERLAY": BlendMode.OVERLAY, "NORMAL": BlendMode.NORMAL,
            "DARKEN": BlendMode.DARKEN, "LIGHTEN": BlendMode.LIGHTEN,
            "COLORDODGE": BlendMode.COLORDODGE, "COLORBURN": BlendMode.COLORBURN,
            "HARDLIGHT": BlendMode.HARDLIGHT, "SOFTLIGHT": BlendMode.SOFTLIGHT,
            "DIFFERENCE": BlendMode.DIFFERENCE, "EXCLUSION": BlendMode.EXCLUSION,
            "HUE": BlendMode.HUE, "SATURATION": BlendMode.SATURATION,
            "COLORBLEND": BlendMode.COLORBLEND, "LUMINOSITY": BlendMode.LUMINOSITY,
            "LEFTJUSTIFIED": Justification.LEFTJUSTIFIED,
            "CENTERJUSTIFIED": Justification.CENTERJUSTIFIED,
            "RIGHTJUSTIFIED": Justification.RIGHTJUSTIFIED,
            "TOPLEFT": AnchorPosition.TOPLEFT, "TOPCENTER": AnchorPosition.TOPCENTER,
            "MIDDLECENTER": AnchorPosition.MIDDLECENTER,
            "BOTTOMCENTER": AnchorPosition.BOTTOMCENTER
        };
        var k = String(s).toUpperCase();
        return (map[k] !== undefined) ? map[k] : s;
    }
    ZT.StrToEnum = StrToEnum;

    /** 一次性自检：把库里可用的函数名列出来（Python 侧用来确认注入成功） */
    ZT.selfTest = safe(function () {
        var names = [];
        for (var k in ZT) if (ZT.hasOwnProperty(k) && typeof ZT[k] === "function") names.push(k);
        return ZT.json({ ok: true, app: app.version, functions: names.length, sample: names.slice(0, 12) });
    });

})(ZT);

if (typeof $.global !== "undefined") $.global.ZT = ZT;

"ps_bridge loaded: " + (typeof ZT.selfTest === "function" ? ZT.selfTest() : "?");
