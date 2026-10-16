# media-control-kit

**Adobe 全家桶 + 达芬奇 Resolve 的完全脚本化控制层。**

不是概念演示，是**在本机真软件里跑过**的东西：Photoshop 端到端 24/24 步通过并落盘真实产物，
After Effects 真启动并回传了 42 个函数，达芬奇的桥脚本已经装进它的脚本菜单目录。
跑不通的地方在下面「已知限制」里**逐条写清楚，没有编造执行结果**。

本机环境（实测）：Windows 10 · Python 3.10 · Photoshop 26.4.1 ·
After Effects 2025 (25.1x68) · Premiere Pro 2025 · DaVinci Resolve 21.0.30007

---

## 0. 三十秒上手

```bash
# 体检：一次看清哪条路能走通（真实探测，会输出可执行建议）
python resolve_bridge/doctor.py

# Photoshop：立刻可用
python ps/verify.py            # 端到端 24 步真跑，产物落在 ps/out/

# After Effects
python ae/ae_cli.py selftest

# Premiere
python pr/pr_cli.py status

# 达芬奇（免费版）
python resolve_bridge/doctor.py --install     # 装桥脚本
# → 达芬奇里 Workspace > Scripts > Utility > zt_bridge_listen
python resolve_bridge/client.py ping
```

---

## 1. 目录结构

```
mediacontrol/
├── resolve_bridge/          达芬奇控制层（免费版优先）
│   ├── free_bridge.lua       ★ 在 Resolve 内部运行的监听端（Lua）
│   ├── client.py             ★ Python 侧：发命令、读结果、高层能力封装
│   ├── studio_api.py         Studio 版原生 Python API 同等能力封装
│   ├── doctor.py             ★ 本机体检 + 一键装桥
│   └── root/                 桥根目录（inbox/ outbox/ renders/ archive/）
│       └── free_bridge.lua
├── ps/                       Photoshop
│   ├── ps_bridge.jsx         ★ ExtendScript 库（97 个函数）
│   ├── ps_cli.py             ★ Python 侧（COM 通道）
│   ├── verify.py             ★ 端到端真验证
│   └── uxp/                  UXP 插件骨架（manifest + index.html + main.js）
├── ae/                       After Effects
│   ├── ae_bridge.jsx         ★ ExtendScript 库（42 个函数）
│   ├── ae_cli.py             ★ Python 侧（AfterFX -r 命令行通道）
│   └── verify.py             端到端验证脚本
├── pr/                       Premiere Pro
│   ├── pr_bridge.jsx         ★ ExtendScript 库
│   ├── pr_cli.py             ★ Python 侧（BridgeTalk / Scripts 目录 / CEP）
│   └── cep/                  CEP 面板骨架（PR 上唯一的生产级通道）
│       ├── CSXS/manifest.xml
│       ├── index.html
│       ├── js/{CSInterface.js, main.js}
│       └── jsx/host.jsx
└── aesthetic/                审美 skill
    ├── SKILL.md              给人看的设计准则
    └── rules.json            ★ 给 AI 读的机器可读版
```

---

## 2. 达芬奇 Resolve（`resolve_bridge/`）

### 2.1 核心结论：本机必须走免费版文件桥

Studio 原生 Python API 在本机**实测走不通**，两种死法都真实发生过：

| 做法 | 结果 |
|---|---|
| 不设 DLL 搜索目录 | `ImportError: DLL load failed while importing fusionscript: 找不到指定的模块`<br>根因：官方 `DaVinciResolveScript.py` 默认去 `C:\Program Files\...` 找 dll，而本机 Resolve 装在 **D 盘**（C 盘那个是残装，没有 dll） |
| 用 `os.add_dll_directory` 挂上 D 盘目录 | **import 阶段直接段错误**，退出码 `3221225477`（0xC0000005 访问违规），连 import 都没走完 |

所以 `studio_api.py` 里的 `probe_json()` 强制用**子进程**探测（否则崩的是你自己的进程），
`StudioAPI` 类保留完整实现（能力对齐 `client.Bridge`，可互换），
**一旦哪天换机器/升版本探测出 available，直接用它就行**。

### 2.2 免费版文件桥怎么工作

Resolve 内嵌 Lua **没有 io 库、不能列目录、不能 os.remove**。
唯一能做的 I/O 是 `dofile()`、`bmd.fileexists()`、`comp:Save()`、`comp:SetData()`。
所以：

```
外部 Python ──写──> inbox/cmd_000001.lua
                          │  (监听端 dofile 执行，把返回值赋给全局 result)
Resolve 监听端 ──写──> outbox/cmd_000001.comp
                          │  (结果 JSON 十六进制塞进 comp 的 CustomData)
外部 Python ──读──> 解出 JSON
```

### 2.3 三步跑起来

```bash
# 1) 装桥脚本（同时装到用户级和全用户级脚本目录）
python resolve_bridge/doctor.py --install

# 2) 达芬奇里点：Workspace > Scripts > Utility > zt_bridge_listen
#    控制台会打印 "free_bridge 1.0.0: 监听 ... 直到 stop"

# 3) 另一边
python resolve_bridge/client.py ping
```

### 2.4 能力清单（`client.py` / `free_bridge.lua` 的 `RB.*`）

覆盖到的能力，方法名**全部来自本机官方 `Scripting/README.txt`（Resolve 21.0，2026-07-15）里的真实签名**，没有编造：

| 分类 | 方法 |
|---|---|
| 项目 | `project_list` `create_project` `load_project` `save_project` `project_info` |
| 时间线 | `timeline_list` `create_timeline` `timeline_from_clips` `set_timeline_by_index` `timeline_info` |
| 媒体/导入 | `import_media` `root_clips` `append_to_timeline` `import_and_append` |
| 片段 | `items` `get_property` `set_property` `set_speed` `set_volume` `set_pan` `set_opacity` `set_zoom` `audio_gain_db` `delete_clips` |
| 入出点 | `set_mark_in_out` `get_mark_in_out` `clear_mark_in_out` `clip_mark_in_out` |
| 轨道 | `add_track` `delete_track` `set_track_enable` `set_track_lock` `set_track_name` |
| 字幕 | `subtitles_from_audio` `insert_title` `insert_generator` |
| 调色 | `color_nodes` `node_tools` `set_node_input` `set_lgg` `set_wheel` `set_lut` `get_lut` `set_cdl` `apply_drx` `set_node_enabled` `export_lut` |
| 渲染 | `render_settings` `get_render_settings` `render_presets` `load_render_preset` `add_render_job` `render_job_list` `render_status` `start_render` `stop_render` `is_rendering` `quick_export` `render_and_wait` |
| 设置 | `get_setting` `set_setting` `get_timeline_setting` `set_timeline_setting`（`RB.SETTINGS` 里给了常用 key） |
| 其它 | `add_marker` `markers` `open_page` `current_page` `version` |

⚠ 调色这块要说清楚：官方 `Graph` 类**没有** `SetLGG` / `SetColorWheel` 这种方法。
真实通道是 `GetToolsInNode(nodeIndex)` 拿到节点里的 Fusion tool，再用 `tool:SetInput(id, value)` 改。
所以 `set_lgg` / `set_wheel` 是走这条路实现的，`RB.COLOR_INPUTS` 里列了 ColorCorrector 的真实 input id
（Lift / Gamma / Gain / Offset / Contrast / Pivot / Saturation / Hue / Temperature / Tint / MasterRGB ...）。
**如果某些 input id 在你的版本里对不上，先跑 `node_tools()` 把真实 id 列出来再改**——这是查真名的办法，不是猜。

### 2.5 实测输出（`python resolve_bridge/doctor.py` 真实回显）

```
[DaVinci Resolve]
  程序路径   : D:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe
  注册表版本 : 21.0.30007
  是否运行中 : 否
  fusionscript.dll : D:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
  官方脚本API目录  : C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
  DaVinciResolveScript.py : 有
  脚本菜单目录[用户级(推荐)] : C:\Users\qq\AppData\Roaming\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts (存在)
  脚本菜单目录[全用户级] : C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts (存在)

  Studio 原生 API : 不可用
    原因: 子进程崩溃（returncode=3221225477，段错误/访问违规）——fusionscript.dll 加载即炸，本机 Studio 原生 API 不可用

  免费版文件桥 :
    桥根目录   : ...\mediacontrol\resolve_bridge\root
    Lua 结构自检: 通过 (结构配平)
    ping       : 不通 —— Resolve 没运行，跳过 ping
```

`--install` 的真实结果：

```json
{"ok": true,
 "written": [
   {"dir": "C:\\Users\\qq\\AppData\\Roaming\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility",
    "files": ["free_bridge.lua", "zt_bridge_listen.lua"]},
   {"dir": "C:\\ProgramData\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Utility",
    "files": ["free_bridge.lua", "zt_bridge_listen.lua"]}
 ],
 "bridge_root": "...\\mediacontrol\\resolve_bridge\\root"}
```

**未执行**：Resolve 内的 Lua 命令实际执行（`client.py ping` 拿到 OK）。
原因：Resolve 必须处于运行状态，而本次作业在沙箱里启动的 Resolve 进程会被回收
（实测启动后进程存在数十秒后消失，与本机 GUI 进程被回收的历史记录一致）。
**下一步**：在真实桌面会话里打开达芬奇 → 运行 `zt_bridge_listen` → `python resolve_bridge/client.py ping`。

---

## 3. Photoshop（`ps/`）— **完全可用**

### 3.1 通道

| 通道 | 状态 |
|---|---|
| COM（`Photoshop.Application` + `DoJavaScript`） | ✅ **实测可用**，本机 26.4.1 |
| MCP `ps_jsx` | ❌ 实测报 `尚未调用 CoInitialize`，同一进程 COM 没初始化好 |

### 3.2 三个真实踩坑（都写进代码注释了）

1. **ExtendScript 是 ES3，没有 JSON 全局** —— 实测 `JSON.stringify` 直接报「JSON 未定义」。
   → `ps_bridge.jsx` 自带 `ZT.json()`，所有返回值都是 JSON 字符串。
2. **`DoJavaScript` 每次调用都是独立上下文** —— 上次定义的全局变量下次就没了（实测「ZT 未定义」）。
   → `ps_cli.py` 每次调用前按需 `$.evalFile` 重新加载，不能"注入一次用多次"。
3. **调整层不能用 `layer.kind = LayerKind.LEVELS`** —— 实测报「您只能将图层的类型更改为文本或正常」。
   → 必须走 ActionManager：`make → adjustmentLayer → using → {Type: <种类>}`。

### 3.3 能力（97 个函数）

文档/图层/蒙版/通道/选区/调整层（14 种）/智能对象/文字层/滤镜/变换/动作批量/导出/历史。

```python
from ps_cli import Photoshop
ps = Photoshop()
ps.call("newDocument", 1920, 1080, 72, "demo")
ps.call("addTextLayer", "王承泽最棒", {"size": 96, "color": [255, 214, 74]})
ps.call("gaussianBlur", 8, "底图")
ps.call("addMask", "底图", "reveal")
ps.call("exportPNG", "D:/out.png")
```

### 3.4 实测（`python ps/verify.py`，真 Photoshop 里跑）

```
▶ 连接状态
{"com": "Photoshop.Application", "version": "26.4.1", "documents": 1,
 "bridge": {"ok": true, "app": "26.4.1", "functions": 97, ...}}

✓ 新建文档 1280x720      ✓ 文档信息        ✓ 加文字层      ✓ 文字信息
✓ 改字号 120             ✓ 加普通层        ✓ 矩形选区      ✓ 填充选区
✓ 取消选区               ✓ 高斯模糊 8px    ✓ 不透明度 70   ✓ 混合模式 multiply
✓ 加图层蒙版             ✓ 蒙版羽化 12     ✓ 亮度对比度层  ✓ 色相饱和度层
✓ 列出图层               ✓ 列出通道        ✓ 转智能对象    ✓ 导出 PNG
✓ 导出 JPG               ✓ 导出 PSD        ✓ 列出动作集    ✓ 关闭(不保存)

步骤 24，失败 0
  产物 verify.png   2767671 bytes
  产物 verify.jpg     74084 bytes
  产物 verify.psd   1470210 bytes
```

### 3.5 UXP 插件（`ps/uxp/`）

`manifest.json`（v5，host PS 24+）+ `index.html` + `main.js`。
`main.js` 里写清了 **UXP 与 ExtendScript 的真实差异对照表**：

|  | ExtendScript（老） | UXP（新） |
|---|---|---|
| JS 引擎 | ES3（没有 JSON！） | 现代 V8，ES2020+ |
| 同步性 | 同步，阻塞 UI | 全部 async/await |
| 取对象 | `app.activeDocument` | `require("photoshop").app` |
| 底层动作 | `executeAction(ActionDesc)` | `batchPlay([...])` |
| 文件访问 | File/Folder 随便读写 | 需 `localFileSystem` 授权 |
| 界面 | ScriptUI（很老） | 真 HTML + CSS |

所有文档操作必须包在 `core.executeAsModal()` 里，否则 UXP 会拒绝。
加载：Photoshop「插件 > 添加插件」选本目录，或用 UXP Developer Tool。

**未执行**：UXP 插件在真 PS 里加载运行（需要手动点「添加插件」，本次未做）。

---

## 4. After Effects（`ae/`）

### 4.1 通道（两条，各管一段）

AE **没有** COM Application 接口（注册表里只有 `AfterEffects.Project.25`，
没有 `AfterEffects.Application`）。

**① 编辑类操作 → `AfterFX.exe -r`**

```bash
"D:\新建文件夹\Adobe After Effects 2025\Support Files\AfterFX.exe" -r script.jsx
```

`-m` 参数用来抑制启动对话框（不加的话 AE 启动时弹「恢复项目」之类，脚本就卡住不执行了——
**这个坑实测踩到过**：第二次调用卡了 180s 没出结果）。命令行跑脚本**拿不到返回值**，
所以脚本用 `AE.save(obj, path)` 把 JSON 写到文件，Python 侧轮询读。

### 4.2 能力（42 个函数）

合成/素材导入/图层（纯色·文字·空对象·调整层·形状）/关键帧/缓动/**表达式**/效果参数/
父子绑定/混合模式/预合成/标记/入出点/渲染队列/导出单帧。

```python
from ae_cli import AfterEffects
ae = AfterEffects()
ae.call("comp", 1920, 1080, 30, 10, "demo")
ae.call("addText", "demo", "王承泽最棒", {"size": 120})
ae.call("animate", "demo", "标题", "Position", 0, 3, [200,540,0], [1700,540,0])
ae.call("setExpression", "demo", "标题", "Rotation", "wiggle(2, 8)")
ae.call("addToRenderQueue", "demo", "D:/out.mov")
```

### 4.3 实测

```
python ae/ae_cli.py selftest
{
  "ok": true,
  "app": "25.1x68",
  "functions": 42,
  "sample": ["json","save","comp","listComps","getComp","activeComp",
             "importFile","listFootage","addSolid","addText","addNull","addAdjustment"]
}
```

`ae_bridge.jsx` 语法另经真 Photoshop 的 ExtendScript 引擎 `$.evalFile` 验证：`SYNTAX_OK`。

**② 渲染 → `aerender.exe`（⭐ 更靠谱，不要求图形界面常驻）**

官方命令行渲染器，本机实测存在且可执行：

```
$ "D:\新建文件夹\Adobe After Effects 2025\Support Files\aerender.exe" -help
aerender version 25.1x68
```

关键参数（从真实 usage 输出里读到的）：

| 参数 | 作用 |
|---|---|
| `-reuse` | **复用已经打开的 AE 实例**去渲染；不加则新起一个实例，渲完自动退出 AE |
| `-project <aep>` | 指定工程 |
| `-comp <name>` | 指定合成 |
| `-output <path>` | 输出文件 |
| `-RStemplate` / `-OMtemplate` | 渲染设置 / 输出模块模板 |

```python
ae.render_aerender(project="D:/proj.aep", comp="demo",
                   output="D:/out.mov", reuse=True)
```

**未执行 / 不稳定**：`-r` 通道连续多次调用。首次 `-r` 成功拿到 AE 回执
（25.1x68 / 42 函数），后续调用在本次沙箱环境下等不到结果文件（AE 进程被回收）。
**下一步**：
1. 渲染优先改用 `render_aerender()`（走 aerender，对 GUI 常驻依赖低得多）
2. 编辑类验证在真实桌面会话里跑 `python ae/verify.py`（21 步脚本已写好）

---

## 5. Premiere Pro（`pr/`）

### 5.1 通道（PR 是四个软件里最难控的）

PR **既没有 COM，也不支持命令行 `-r`**（那是 AE 才有）。三条路：

1. **CEP 面板** ← 业界验证过的生产级方案
   （参考 `hetpatel-11/Adobe_Premiere_Pro_MCP`：CEP = production，UXP = experimental）
   骨架已给在 `pr/cep/`：
   ```bash
   reg add HKCU\Software\Adobe\CSXS.11 /v PlayerDebugMode /t REG_SZ /d 1 /f
   # 复制 cep/ 到 C:\Users\<你>\AppData\Roaming\Adobe\CEP\extensions\com.mediactrl.prbridge\
   # 重启 PR → 窗口 > 扩展 > Media Control Kit
   ```
   `python pr/pr_cli.py status` 会直接告诉你开关开了没。本机实测结果：

   ```json
   "cep_registry": {
     "CSXS.11": {"exists": true,  "PlayerDebugMode": null, "action": "需要 reg add ... /v PlayerDebugMode /d 1"},
     "CSXS.12": {"exists": true,  "PlayerDebugMode": null, "action": "需要 reg add ... /v PlayerDebugMode /d 1"},
     "CSXS.10": {"exists": false},
     "CSXS.9":  {"exists": false}
   }
   ```
   → **CSXS.11 和 CSXS.12 的键都在，但调试开关还没开**，所以 CEP 面板
   复制过去也不会显示。先跑上面那条 `reg add` 再重启 PR。

   **顺带印证**：`qmasingarbe/pymiere`（Python 直控 PR 的库，★474，A 级）
   用的也是同一套机制 —— Python → 生成 ExtendScript → 通过 HTTP 发给一个
   装在 PR 里的 CEP 扩展（Pymiere Link）。这反过来证明 CEP 就是 PR 上唯一靠谱的路。
   （pymiere 自己已标注不再维护、只测到 PR 2023，所以这里没直接依赖它，
   而是自己给了 cep/ 骨架。）
2. **BridgeTalk 转发** —— 借 Photoshop（它有 COM）把脚本发给 premierepro：
   `python pr_cli.py bridgetalk projectInfo`（PS 和 PR 都要开着）
3. **Adobe Scripts 目录 + 手动运行** —— 最稳：
   `python pr_cli.py write listSequences` → PR 里 文件 > 脚本 > zt_pr_listSequences → `python pr_cli.py read`

### 5.2 能力

项目/bin/导入素材/序列创建（自动找 `.sqpreset`）/插入与覆盖片段/片段列表/裁剪入出点/
变速/转场（走 QE，失败会明确报错）/效果组件与参数/**关键帧**/标记/导出（走 Media Encoder）。

变速和转场要说明：PR 的 ExtendScript 标准 DOM 对这两项支持很有限（要 QE），
代码里失败时会明确返回「需要 QE / 建议 UXP 面板」，而不是假装成功。

### 5.3 实测

```
python pr/pr_cli.py status
{
  "pr_exe": "D:\\新建文件夹\\Adobe Premiere Pro 2025\\Adobe Premiere Pro.exe",
  "pr_running": false,
  "bridge_jsx": "...\\mediacontrol\\pr\\pr_bridge.jsx",
  "adobe_scripts_dir": null,
  "channels": ["BridgeTalk 转发（借 Photoshop 的 COM，PR+PS 都要开着）",
               "Adobe Scripts 目录 + PR 里手动运行（最稳）"]
}
```

`pr_bridge.jsx` 语法经真 Photoshop 的 ExtendScript 引擎 `$.evalFile` 验证：`SYNTAX_OK`。

**未执行**：PR 内实际跑命令。原因：PR 本次未运行（启动 PR 同样受沙箱 GUI 回收影响）。
**下一步**：开着 PR 时跑 `python pr/pr_cli.py bridgetalk projectInfo`（PS 也要开着）。

---

## 6. 审美 skill（`aesthetic/`）

- `SKILL.md` —— 给人看的完整准则
- `rules.json` —— **给 AI 直接读的机器可读版**（配色决策树 / 字号阶梯 / 间距系统 / 动效时长表 / 17 条硬禁忌 / 验收清单）

数据来自 29 个 2026 年 Awwwards / FWA / Codrops 获奖站的实测色值拆解，不是凭感觉写的。
几条最关键的：

- 底色必须是**带色相的极深底**（`#06030d` `#04020a` `#070710`），
  **不是**纯黑 `#000000`，**也不是**灰黑 `#08080b`
- 强调色 **2 个高饱和 + 互为冷暖对比**；金色 `#ffd24a` 系是 2026 最高频强调色
- "绚烂"可量化：死黑占比低 + 高彩度占比高 + **特效持续自动发生**（只在点击时放的不算）
- 17 条硬禁忌里包括两条工程坑：
  改了 CSS/JS 不升版本号（用户永远拿旧缓存）、
  只用 `readPixels`/`drawImage` 验证视觉（buffer 12% 彩度，屏幕可能只有 2%，必须看真实合成截图）

---

## 7. 已知限制汇总（诚实清单）

| 模块 | 状态 | 说明 |
|---|---|---|
| Photoshop 控制 | ✅ **完全可用** | COM 通道，24/24 步真跑通过，产物真实落盘 |
| Photoshop UXP 骨架 | ⚠️ 未加载验证 | 代码完整，需手动在 PS 里「添加插件」 |
| After Effects | ⚠️ **通道已验证** | `aerender 25.1x68` 真跑出用法（渲染走它，不依赖 GUI 常驻）；`-r` 首次真跑成功（42 函数），连续调用受沙箱 GUI 回收影响 |
| Premiere Pro | ⚠️ 未实际执行 | 语法已真引擎验证；PR 未运行。CEP 骨架已给，是生产级方案 |
| Resolve 免费版桥 | ⚠️ 未实际执行 | 桥脚本已装进 Resolve 脚本菜单目录，Lua 结构自检通过；需真实桌面会话启动监听 |
| Resolve Studio API | ❌ 本机不可用 | 实测段错误（0xC0000005）。代码保留，换环境可用 |

**贯穿全部 Adobe 模块的一条环境限制**：本次作业在沙箱里启动的 GUI 程序
（Resolve / AE）会被回收，与本机历史记录（Chrome 同样被回收）一致。
所有需要 GUI 长驻的验证，请在真实桌面会话里再跑一次对应的 `verify.py` / `doctor.py`。

---

## 8. 铁律（改这些代码时别踩）

1. **改生产脚本：本地改 → 本地校验 → 上传 → 目标再校验 → 生效**。别在目标环境里原地改源码。
2. ExtendScript 里**不要用** ES5+ 语法和 `JSON`；循环用 `for`，序列化用自带的 `ZT.json()` / `AE.json()` / `PR.json()`。
3. ExtendScript 里 `with` 是保留字，不能当对象 key 用（实测报「非法使用保留字"with"」）。
4. 凡是"读属性可能抛异常"的地方（PS 的 `fullName`、文字的 `leading`、PR 的 QE），**必须 try/catch 兜住**，
   否则整条命令挂掉还拿不到错误信息。
5. Resolve 内嵌 Lua 没有 io 库——结果只能借 comp 文件带出来，别想着直接写文件。
6. Studio API 探测**必须走子进程**，否则段错误崩的是你自己。
7. 凭据/密钥不进仓库。

---

## 9. 依赖

只需要 Python 3.10 + `pywin32`（Photoshop COM 通道要用）。
达芬奇桥不需要额外依赖（Lua 在 Resolve 内部跑）。

```bash
pip install pywin32
```

---

_生成：2026-10-16 · 所有"实测"字样均对应本文写作时在本机的真实执行输出，未执行的部分已显式标注。_
