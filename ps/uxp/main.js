/**
 * main.js — UXP 版 Photoshop 控制（Photoshop 2021+ / v24+ 的新一代插件 API）
 *
 * UXP 与 ExtendScript 的真实差异（重要，别搞混）：
 *   ┌────────────┬───────────────────────────┬──────────────────────────┐
 *   │            │ ExtendScript (老)         │ UXP (新)                 │
 *   ├────────────┼───────────────────────────┼──────────────────────────┤
 *   │ 加载方式   │ .jsx 文件 / ESTK / COM    │ 插件目录 + manifest.json │
 *   │ JS 引擎    │ ES3（没有 JSON！）        │ 现代 V8，ES2020+，有 JSON│
 *   │ 同步性     │ 同步，阻塞 UI             │ 全部 async/await         │
 *   │ 取对象     │ app.activeDocument        │ require("photoshop").app │
 *   │ 底层动作   │ executeAction(ActionDesc) │ batchPlay([...])         │
 *   │ 文件访问   │ File/Folder（随便读写）   │ 需 localFileSystem 授权  │
 *   │ 界面       │ ScriptUI（很老）          │ 真 HTML + CSS            │
 *   └────────────┴───────────────────────────┴──────────────────────────┘
 *
 * 本文件两种 API 都给：
 *   - 现代对象模型：app.activeDocument / doc.createLayer() 等（可读性好）
 *   - batchPlay：底层 ActionManager 描述符（万能口子，覆盖所有菜单参数）
 *
 * 加载：Photoshop 里「插件 > 添加插件」选本目录，或用 UXP Developer Tool
 *       （https://developer.adobe.com/photoshop/uxp/）加载后点「加载」。
 */
const { app, core, action } = require("photoshop");
const batchPlay = action.batchPlay;

const out = () => document.getElementById("out");
function log(v) {
  const el = out();
  const s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  el.textContent = s;
  console.log(s);
}

/** 所有文档操作都必须包在 executeAsModal 里，否则 UXP 会拒绝（有模态保护） */
async function modal(fn, name = "Media Control Kit") {
  return core.executeAsModal(fn, { commandName: name });
}

// ------------------------------------------------------------ 文档 ----
async function docInfo() {
  return modal(async () => {
    const d = app.activeDocument;
    if (!d) return { ok: false, err: "没有打开的文档" };
    return {
      ok: true,
      name: d.name,
      width: d.width,
      height: d.height,
      resolution: d.resolution,
      layers: d.layers.length,
      // UXP 里 id/title 是 modern 属性，ExtendScript 那边没有
      id: d.id,
      title: d.title,
    };
  });
}

async function listLayers() {
  return modal(async () => {
    const d = app.activeDocument;
    if (!d) return { ok: false, err: "没有打开的文档" };
    return {
      ok: true,
      layers: d.layers.map((l) => ({
        id: l.id,
        name: l.name,
        kind: String(l.kind),
        visible: l.visible,
        opacity: l.opacity,
        blendMode: String(l.blendMode),
        locked: l.locked,
        isGroup: !!l.layers,
      })),
    };
  });
}

async function newDocument(w = 1920, h = 1080, res = 72, name = "uxp-doc") {
  return modal(async () => {
    const d = await app.createDocument({
      width: w, height: h, resolution: res, name,
      mode: "RGB", fill: "white",
    });
    return { ok: true, name: d.name, id: d.id };
  });
}

// ------------------------------------------------------------ 图层 ----
async function addText(text, size, colorHex, position) {
  return modal(async () => {
    const d = app.activeDocument;
    if (!d) return { ok: false, err: "没有打开的文档" };
    const layer = await d.createLayer({
      name: text.slice(0, 12),
      opacity: 100,
      mode: "normal",
    });
    // UXP 里文字层走 TextLayer 类型（v24+ 支持）
    const textLayer = await d.createTextLayer({
      name: "文字",
      contents: text,
      textSize: size,
      color: { _obj: "RGBColor", red: 255, green: 214, blue: 74 },
      position: position || { x: d.width * 0.1, y: d.height * 0.45 },
    });
    return { ok: true, layer: textLayer.name, text, size };
  });
}

/**
 * batchPlay —— 万能口子，ExtendScript 的 executeAction 在这里的等价物。
 * 任何菜单项背后的 ActionManager 描述符都能这样直接跑。
 */
async function gaussianBlur(radius) {
  return modal(async () => {
    const result = await batchPlay(
      [
        {
          _obj: "gaussianBlur",
          radius: { _unit: "pixelsUnit", _value: radius },
          _options: { dialogOptions: "dontDisplay" },
        },
      ],
      { synchronousExecution: true }
    );
    return { ok: true, filter: "gaussianBlur", radius, result };
  });
}

async function addBrightnessContrast(brightness = 0, contrast = 0) {
  return modal(async () => {
    const result = await batchPlay(
      [
        {
          _obj: "make",
          _target: [{ _ref: "adjustmentLayer" }],
          using: {
            _obj: "adjustmentLayer",
            type: {
              _obj: "brightnessContrast",
              brightness,
              contrast,
            },
          },
          _options: { dialogOptions: "dontDisplay" },
        },
      ],
      { synchronousExecution: true }
    );
    return { ok: true, kind: "brightnessContrast", brightness, contrast, result };
  });
}

async function listChannels() {
  return modal(async () => {
    const d = app.activeDocument;
    if (!d) return { ok: false, err: "没有打开的文档" };
    const result = await batchPlay(
      [{ _obj: "get", _target: [{ _ref: "channel", _property: "name" }] }],
      { synchronousExecution: true }
    );
    return { ok: true, raw: result, count: d.channels ? d.channels.length : null };
  });
}

/** 导出：UXP 下要用 localFileSystem 拿到的 token 才能写文件 */
async function exportPNG() {
  const fs = require("uxp").storage.localFileSystem;
  const file = await fs.getFileForSaving("export.png");
  if (!file) return { ok: false, err: "用户取消了保存" };
  return modal(async () => {
    const d = app.activeDocument;
    await d.saveAs("png", file, { interlaced: false });
    return { ok: true, path: file.nativePath };
  });
}

// ------------------------------------------------------------ 绑定 ----
function bind(id, fn, getArgs) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("click", async () => {
    try {
      const args = getArgs ? getArgs() : [];
      log(await fn(...args));
    } catch (e) {
      log({ ok: false, err: String(e) });
    }
  });
}

bind("btnInfo", docInfo);
bind("btnLayers", listLayers);
bind("btnChannels", listChannels);
bind("btnText", addText, () => [
  document.getElementById("text").value,
  Number(document.getElementById("size").value) || 96,
  document.getElementById("color").value,
]);
bind("btnBlur", gaussianBlur, () => [12]);
bind("btnAdj", addBrightnessContrast, () => [10, 20]);
bind("btnExport", exportPNG);

// 供 panel 之外的入口（command）调用
module.exports = {
  docInfo, listLayers, newDocument, addText,
  gaussianBlur, addBrightnessContrast, listChannels, exportPNG,
};
