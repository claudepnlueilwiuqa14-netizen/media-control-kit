/**
 * CSInterface.js — 最小实现（够用版）
 *
 * 官方的 CSInterface.js 从 Adobe 官方仓库拿（adobe-cep/CEP-Resources），
 * 这里给一个只实现 evalScript 的最小版，避免多一个外部依赖。
 * 真机上 window.__adobe_cep__ 由 CEP 运行时注入，evalScript 就能用。
 */
function CSInterface() {
  var _cep = (typeof window !== "undefined") ? window.__adobe_cep__ : null;

  this.getHostEnvironment = function () {
    if (!_cep) return { appName: "UNKNOWN", appVersion: "0" };
    try { return JSON.parse(_cep.getHostEnvironment()); }
    catch (e) { return { appName: "UNKNOWN", appVersion: "0" }; }
  };

  this.getExtensionID = function () {
    return (_cep && _cep.getExtensionId) ? _cep.getExtensionId() : "unknown";
  };

  /** 在宿主（PR）里执行 ExtendScript，结果通过回调返回字符串 */
  this.evalScript = function (script, callback) {
    if (!_cep || !_cep.evalScript) {
      callback(JSON.stringify({ ok: false, err: "不在 CEP 环境里（用浏览器打开的本页面？）" }));
      return;
    }
    try {
      _cep.evalScript(script, callback);
    } catch (e) {
      callback(JSON.stringify({ ok: false, err: String(e) }));
    }
  };

  /** 打开一个本地路径（Explorer） */
  this.openURLInDefaultBrowser = function (url) {
    if (_cep && _cep.openURLInDefaultBrowser) _cep.openURLInDefaultBrowser(url);
  };
}

// 让 evalScript 返回值是 undefined 时也能安全 parse
if (typeof window !== "undefined") {
  window.CSInterface = CSInterface;
}
