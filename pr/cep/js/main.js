/**
 * main.js — CEP 面板前端
 *
 * CEP 的关键机制：面板是 Chromium，ExtendScript 在宿主（PR）里；
 * 两边通过 csInterface.evalScript(jsxCode, callback) 通信。
 * 这是 PR 上唯一被验证为「生产级」的自动化通道（UXP 在 PR 里仍是实验性的）。
 *
 * evalScript 只能传字符串、只回一个字符串，所以约定：
 *   所有 ExtendScript 侧函数都返回 JSON 字符串，这边 JSON.parse。
 */
var cs = new CSInterface();

function $(id) { return document.getElementById(id); }
function log(v) {
  var s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  $("out").textContent = s;
}

function run(jsx, cb) {
  cs.evalScript(jsx, function (res) {
    try {
      var o = JSON.parse(res);
      log(o);
      if (cb) cb(o);
    } catch (e) {
      log({ raw: res, note: "返回的不是 JSON（脚本可能报错了）" });
    }
  });
}

/** 调用 PR.<fn>(...)：先确保 pr_bridge.jsx 已加载 */
function call(fn, args) {
  var jsArgs = (args || []).map(function (a) { return JSON.stringify(a); }).join(", ");
  var jsx = [
    'try { $.evalFile("' + BRIDGE_PATH + '"); } catch (e0) { }',
    'PR.' + fn + '(' + jsArgs + ');'
  ].join("\n");
  run(jsx);
}

// pr_bridge.jsx 的绝对路径（安装时按实际位置改这一行）
var BRIDGE_PATH = "C:/Users/qq/WorkBuddy/2026-10-16-06-54-13/mediacontrol/pr/pr_bridge.jsx";

$("bInfo").onclick = function () { call("projectInfo", []); };
$("bSeqs").onclick = function () { call("listSequences", []); };
$("bNewSeq").onclick = function () { call("createSequence", [$("seqName").value]); };
$("bImport").onclick = function () { call("importFiles", [[$("mediaPath").value]]); };
$("bInsert").onclick = function () {
  call("insertClip", [fileNameOf($("mediaPath").value), 0, 0]);
};
$("bClips").onclick = function () { call("listClips", [0, "video"]); };
$("bFx").onclick = function () { call("listClipComponents", [0, 0]); };
$("bExport").onclick = function () {
  call("exportSequence", ["D:/export.mp4"]);
};

function fileNameOf(p) {
  var parts = p.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1];
}

// 面板加载时先探一下宿主
run('(function(){ return JSON.stringify({app: app.version, project: app.project ? app.project.name : null}); })()');
