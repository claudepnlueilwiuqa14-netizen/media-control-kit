/**
 * host.jsx — CEP 面板在 Premiere 侧的宿主脚本
 * manifest.xml 的 <ScriptPath> 指向这里，面板启动时会被加载。
 * 真正的实现都在 pr_bridge.jsx，这里只负责把它挂进来并暴露一个统一入口。
 */

#include "../../../pr_bridge.jsx"

/** CEP 调用的统一入口：callPR("listSequences", "[]") -> JSON 字符串 */
function callPR(fn, argsJson) {
    try {
        var args = JSON.parse(argsJson || "[]");
        var r = PR[fn].apply(PR, args);
        return PR.json(r);
    } catch (e) {
        return PR.json({ ok: false, err: String(e.message || e) });
    }
}

/** 列出所有可用函数 */
function listPRFunctions() {
    var out = [];
    for (var k in PR) {
        if (PR.hasOwnProperty(k) && typeof PR[k] === "function") out.push(k);
    }
    return PR.json({ ok: true, functions: out });
}

"host.jsx loaded";
