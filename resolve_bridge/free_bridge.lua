--[==========================================================================[
free_bridge.lua — DaVinci Resolve 文件桥「监听端」(在 Resolve 内部运行)

适用：DaVinci Resolve 免费版 / Studio 版 都行。
      免费版没有外部 Python 脚本接口（本机实测 DaVinciResolveScript 直接
      segfault），所以外部程序只能靠「文件投递 + 结果回传 comp」这条通道。

为什么这么绕（实测结论，别再试图简化）：
  Resolve 内嵌 Lua 没有 io 库、没有 os.remove/os.rename、不能列目录。
  它唯一能做的 I/O 是：dofile(一个文件)、bmd.fileexists(路径)、
  comp:Save(路径)、comp:SetData()。
  所以「把结果带出来」只能借一个 comp 文件当信封，把 JSON 十六进制写进
  它的 CustomData，外部再读这个文件解出来。

装好之后在 Resolve 里启动（三种方式任选）：
  1) Workspace > Scripts > Utility > zt_bridge_listen      （推荐）
  2) Fusion 页 Console 里粘贴：
     dofile([[C:\...\free_bridge.lua]])({ root = [[C:\...\resolve_bridge_root]] })
  3) 一次性跑（处理完积压命令就退出，适合调试）：
     dofile([[C:\...\free_bridge.lua]])({ root = [[C:\...]], once = true })

启动参数（table）：
  root    桥根目录（含 inbox/ outbox/ renders/），必填
  minutes 监听分钟数，0 = 一直监听直到 bridge.stop() 或出现 stop 文件
  poll    检查 inbox 的间隔秒数（默认 0.5）
  once    true = 只处理当前积压的命令然后返回

命令文件约定：
  外部往 <root>/inbox/cmd_000001.lua 写 Lua 源码，监听端 dofile 执行它。
  命令里把要回传的值赋给全局变量 `result`（任意 Lua 值），
  也可以 print()，打印内容会一起回传。
  结果写到 <root>/outbox/cmd_000001.comp，用 client.py 读。

命令里可直接用的东西：
  comp        当前 Fusion comp（可能为 nil）
  resolve     Resolve 应用对象
  RB          本项目封装的高层 API（见下）
  bridge / H  桥控制（bridge.stop() 停监听、bridge.version）
--]==========================================================================]

local VERSION = "1.0.0"
local fu = fusion or fu or app

local function start(cfg)
  cfg = cfg or {}

  ------------------------------------------------------------------ 路径 ----
  local function script_dir()
    if type(debug) ~= "table" or type(debug.getinfo) ~= "function" then return nil end
    local ok, info = pcall(debug.getinfo, 1, "S")
    if ok and info and info.source and info.source:sub(1, 1) == "@" then
      return info.source:sub(2):match("^(.*[/\\])")
    end
  end
  local ROOT = cfg.root
  if not ROOT then
    local d = script_dir()
    if d then ROOT = d:gsub("[/\\][^/\\]*$", "") end
  end
  assert(ROOT, "free_bridge: 必须传 { root = [[<桥根目录>]] }")
  local SEP = ROOT:find("\\", 1, true) and "\\" or "/"
  if ROOT:sub(-1) ~= SEP then ROOT = ROOT .. SEP end
  local INBOX   = ROOT .. "inbox"   .. SEP
  local OUTBOX  = ROOT .. "outbox"  .. SEP
  local RENDERS = ROOT .. "renders" .. SEP
  local STATE, STOP = ROOT .. "state.lua", ROOT .. "stop"
  local MINUTES = cfg.minutes or 0
  local POLL    = cfg.poll or 0.5
  local ONCE    = cfg.once
  local stop_requested = false

  local function exists(p) return bmd and bmd.fileexists(p) end
  local function C() return fu and fu:GetCurrentComp() end

  ------------------------------------------------------- JSON 编码器 ----
  -- 结果要变成 JSON 才能带出去；Resolve 的 Lua 里没有任何现成 JSON 库。
  local function jstr(s)
    return '"' .. s:gsub('[%c"\\]', function(ch)
      local m = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n',
                  ['\r'] = '\\r', ['\t'] = '\\t', ['\b'] = '\\b', ['\f'] = '\\f' }
      return m[ch] or string.format("\\u%04x", ch:byte())
    end) .. '"'
  end
  local function json(v, depth, seen)
    depth, seen = depth or 0, seen or {}
    local t = type(v)
    if v == nil then return "null"
    elseif t == "boolean" then return tostring(v)
    elseif t == "number" then
      if v ~= v or v == math.huge or v == -math.huge then return "null" end
      if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end
      return string.format("%.14g", v)
    elseif t == "string" then return jstr(v)
    elseif t == "table" then
      if seen[v] then return '"<cycle>"' end
      if depth > 12 then return '"<too deep>"' end
      seen[v] = true
      local n, count = #v, 0
      for _ in pairs(v) do count = count + 1 end
      if count == 0 then seen[v] = nil; return "[]" end
      local parts = {}
      if n > 0 and n == count then
        for i = 1, n do parts[i] = json(v[i], depth + 1, seen) end
        seen[v] = nil
        return "[" .. table.concat(parts, ",") .. "]"
      end
      for k, x in pairs(v) do
        parts[#parts + 1] = jstr(tostring(k)) .. ":" .. json(x, depth + 1, seen)
      end
      seen[v] = nil
      return "{" .. table.concat(parts, ",") .. "}"
    else
      return jstr(tostring(v))          -- userdata / function / 其它
    end
  end
  local function hex(s)
    return (s:gsub(".", function(ch) return string.format("%02x", ch:byte()) end))
  end

  --------------------------------------------------- Resolve 高层 API ----
  -- RB.* 全部基于本机官方 README.txt（Resolve 21.0，2026-07-15）里的真实签名，
  -- 没有编造任何方法名。
  local RB = { version = VERSION }

  local function app()   return resolve or (fu and fu.GetResolve and fu:GetResolve()) end
  local function pm()    return app():GetProjectManager() end
  local function proj()  return pm():GetCurrentProject() end
  local function pool()  return proj():GetMediaPool() end
  local function tl()    return proj():GetCurrentTimeline() end

  RB.app, RB.proj, RB.pool, RB.timeline = app, proj, pool, tl

  -- 出错时统一返回 {ok=false, err=...}，而不是让整条命令炸掉
  local function call(fn, ...)
    local ok, r = pcall(fn, ...)
    if ok then return r end
    return nil, tostring(r)
  end

  ------------------------------------------------------------ 项目 ----
  function RB.project_list()
    local r, e = call(function() return pm():GetProjectListInCurrentFolder() end)
    if not r then return { ok = false, err = e } end
    return { ok = true, projects = r }
  end
  function RB.create_project(name, mediaPath)
    local r, e = call(function() return pm():CreateProject(name, mediaPath) end)
    if not r then return { ok = false, err = e } end
    return { ok = true, project = r, name = name }
  end
  function RB.load_project(name)
    local r, e = call(function() return pm():LoadProject(name) end)
    return { ok = r ~= nil and r ~= false, err = e, project = r and r ~= true and r or nil }
  end
  function RB.save_project()
    local r, e = call(function() return pm():SaveProject() end)
    return { ok = r == true, err = e }
  end
  function RB.close_project(p)
    local r, e = call(function() return pm():CloseProject(p or proj()) end)
    return { ok = r == true, err = e }
  end
  function RB.project_info()
    local ok, r = pcall(function()
      local p = proj()
      if not p then return nil end
      return { name = p:GetName(), timelineCount = p:GetTimelineCount(),
               currentTimeline = (p:GetCurrentTimeline() and p:GetCurrentTimeline():GetName()) or nil }
    end)
    if not ok or not r then return { ok = false, err = ok and "no project open" or tostring(r) } end
    return { ok = true, project = r }
  end

  ------------------------------------------------------------ 时间线 ----
  function RB.timeline_list()
    local ok, r = pcall(function()
      local p, out = proj(), {}
      if not p then return out end
      for i = 1, p:GetTimelineCount() do
        local t = p:GetTimelineByIndex(i)
        if t then
          out[#out + 1] = { index = i, name = t:GetName(),
                            startFrame = t:GetStartFrame(), endFrame = t:GetEndFrame(),
                            videoTracks = t:GetTrackCount("video"),
                            audioTracks = t:GetTrackCount("audio") }
        end
      end
      return out
    end)
    return { ok = ok, err = ok and nil or tostring(r), timelines = ok and r or nil }
  end
  function RB.create_timeline(name)
    local r, e = call(function() return pool():CreateEmptyTimeline(name) end)
    if not r then return { ok = false, err = e } end
    return { ok = true, timeline = r, name = name }
  end
  function RB.timeline_from_clips(name, clips)
    local r, e = call(function() return pool():CreateTimelineFromClips(name, clips) end)
    return { ok = r ~= nil, err = e, timeline = r }
  end
  function RB.set_timeline(t)
    local r, e = call(function() return proj():SetCurrentTimeline(t) end)
    return { ok = r == true, err = e }
  end
  function RB.set_timeline_by_index(i)
    local ok, r = pcall(function()
      local t = proj():GetTimelineByIndex(i)
      if not t then return false end
      return proj():SetCurrentTimeline(t)
    end)
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end
  function RB.timeline_info()
    local ok, r = pcall(function()
      local t = tl()
      if not t then return nil end
      local v, a = t:GetTrackCount("video"), t:GetTrackCount("audio")
      local tracks = {}
      for i = 1, v do
        tracks[#tracks + 1] = { type = "video", index = i, name = t:GetTrackName("video", i),
                                enabled = t:GetIsTrackEnabled("video", i),
                                locked  = t:GetIsTrackLocked("video", i) }
      end
      for i = 1, a do
        tracks[#tracks + 1] = { type = "audio", index = i, name = t:GetTrackName("audio", i),
                                enabled = t:GetIsTrackEnabled("audio", i),
                                locked  = t:GetIsTrackLocked("audio", i) }
      end
      return { name = t:GetName(), startFrame = t:GetStartFrame(), endFrame = t:GetEndFrame(),
               startTimecode = t:GetStartTimecode(), currentTimecode = t:GetCurrentTimecode(),
               videoTracks = v, audioTracks = a, tracks = tracks }
    end)
    return { ok = ok and r ~= nil, err = ok and nil or tostring(r), timeline = ok and r or nil }
  end

  ------------------------------------------------------------ 媒体池 ----
  -- paths: { "D:/a.mp4", ... } 或 { {"FilePath"="...", "ClipName"="..."} }
  function RB.import_media(paths)
    local items = {}
    for i, p in ipairs(paths) do
      if type(p) == "string" then items[i] = { FilePath = p } else items[i] = p end
    end
    local r, e = call(function() return pool():ImportMedia(items) end)
    if not r then
      -- 备选：MediaStorage.AddItemListToMediaPool（对某些版本更稳）
      local mst = app():GetMediaStorage()
      local r2, e2 = call(function()
        local ps = {}
        for i, p in ipairs(paths) do ps[i] = (type(p) == "string") and p or (p.FilePath or p.filePath) end
        return mst:AddItemListToMediaPool(ps)
      end)
      if not r2 then return { ok = false, err = e or e2 } end
      r = r2
    end
    local out = {}
    for i, it in ipairs(r) do
      out[i] = { name = it:GetName(), mediaId = it:GetMediaId(), uniqueId = it:GetUniqueId() }
    end
    return { ok = true, count = #out, clips = out, _items = r }
  end
  function RB.root_clips()
    local ok, r = pcall(function()
      local out = {}
      for _, c in ipairs(pool():GetRootFolder():GetClipList()) do
        out[#out + 1] = { name = c:GetName(), mediaId = c:GetMediaId() }
      end
      return out
    end)
    return { ok = ok, err = ok and nil or tostring(r), clips = ok and r or nil }
  end
  function RB.append_to_timeline(clips)
    local r, e = call(function() return pool():AppendToTimeline(clips) end)
    return { ok = r ~= nil and r ~= false, err = e, count = (type(r) == "table") and #r or nil }
  end

  -------------------------------------------------------- 时间线片段 ----
  -- 取指定轨道上的片段；trackType = "video" | "audio"，index 从 1 开始
  function RB.items(trackType, index)
    local ok, r = pcall(function()
      local t, out = tl(), {}
      if not t then return out end
      for _, it in ipairs(t:GetItemListInTrack(trackType or "video", index or 1)) do
        out[#out + 1] = { name = it:GetName(), start = it:GetStart(), ["end"] = it:GetEnd(),
                          duration = it:GetDuration(),
                          left = it:GetLeftOffset(), right = it:GetRightOffset() }
      end
      return out
    end)
    return { ok = ok, err = ok and nil or tostring(r), items = ok and r or nil }
  end
  -- 第 n 个片段对象（供其它函数用）
  function RB.item(trackType, index, n)
    local t = tl()
    if not t then return nil end
    local list = t:GetItemListInTrack(trackType or "video", index or 1)
    return list and list[n or 1]
  end
  -- SetProperty 是切速度/音量/位移等一切参数的通用口子
  function RB.set_property(trackType, index, n, key, value)
    local it = RB.item(trackType, index, n)
    if not it then return { ok = false, err = "no item at " .. tostring(index) .. "/" .. tostring(n) } end
    local r, e = call(function() return it:SetProperty(key, value) end)
    return { ok = r == true, err = e, key = key, value = value }
  end
  function RB.get_property(trackType, index, n, key)
    local it = RB.item(trackType, index, n)
    if not it then return { ok = false, err = "no item" } end
    local r, e = call(function() return it:GetProperty(key) end)
    return { ok = r ~= nil, err = e, key = key, value = r }
  end
  -- 常用属性速查（真实存在于 Resolve 属性表）
  function RB.set_speed(trackType, index, n, speed)
    return RB.set_property(trackType, index, n, "Speed", speed)
  end
  function RB.set_volume(trackType, index, n, db)
    return RB.set_property(trackType, index, n, "Volume", db)
  end
  function RB.set_pan(trackType, index, n, pan)
    return RB.set_property(trackType, index, n, "Pan", pan)
  end
  function RB.set_opacity(trackType, index, n, v)
    return RB.set_property(trackType, index, n, "Opacity", v)
  end
  function RB.set_zoom(trackType, index, n, x, y)
    local a = RB.set_property(trackType, index, n, "ZoomX", x)
    local b = RB.set_property(trackType, index, n, "ZoomY", y or x)
    return { ok = a.ok and b.ok, zoomX = a, zoomY = b }
  end
  -- 音频增益（Fairlight 轨道级）：TimelineItem 上的 "Gain" 并不存在，
  -- 真实做法：片段级走 SetProperty("Volume")，轨道级走 Fairlight 的
  -- TrackIndex 属性由 SetTrackName/SetTrackEnable 之外没有独立增益 API，
  -- 因此这里给出 clip 级 + 轨道音量(通过每个 clip 循环)两种。
  function RB.audio_gain_db(trackIndex, db)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local list = t:GetItemListInTrack("audio", trackIndex)
    if not list then return { ok = false, err = "no audio track " .. tostring(trackIndex) } end
    local n = 0
    for _, it in ipairs(list) do
      if it:SetProperty("Volume", db) then n = n + 1 end
    end
    return { ok = true, track = trackIndex, db = db, changed = n }
  end
  -- 导出音频幅度做自动化（Resolve 20+ 无直接 API，用 clip 属性近似）

  -------------------------------------------------------- 入出点 / 剪辑 ----
  -- 设置时间线入出点（frame 为单位，相对时间线起点）
  function RB.set_mark_in_out(inF, outF, kind)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:SetMarkInOut(inF, outF, kind or "all") end)
    return { ok = r == true, err = e, ["in"] = inF, out = outF }
  end
  function RB.get_mark_in_out()
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:GetMarkInOut() end)
    return { ok = r ~= nil, err = e, marks = r }
  end
  function RB.clear_mark_in_out(kind)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:ClearMarkInOut(kind or "all") end)
    return { ok = r == true, err = e }
  end
  -- 媒体池素材入出点（决定导入后可用范围）
  function RB.clip_mark_in_out(clip, inF, outF, kind)
    local r, e = call(function() return clip:SetMarkInOut(inF, outF, kind or "all") end)
    return { ok = r == true, err = e }
  end
  function RB.delete_clips(trackType, index, ns)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local list = t:GetItemListInTrack(trackType or "video", index or 1)
    local picked = {}
    for _, n in ipairs(ns) do if list[n] then picked[#picked + 1] = list[n] end end
    local r, e = call(function() return t:DeleteClips(picked, false) end)
    return { ok = r == true, err = e, deleted = #picked }
  end

  ------------------------------------------------------------ 轨道 ----
  function RB.add_track(trackType, subType)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:AddTrack(trackType, subType) end)
    return { ok = r == true, err = e, type = trackType }
  end
  function RB.delete_track(trackType, index)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:DeleteTrack(trackType, index) end)
    return { ok = r == true, err = e }
  end
  function RB.set_track_enable(trackType, index, on)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:SetTrackEnable(trackType, index, on) end)
    return { ok = r == true, err = e }
  end
  function RB.set_track_lock(trackType, index, on)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:SetTrackLock(trackType, index, on) end)
    return { ok = r == true, err = e }
  end
  function RB.set_track_name(trackType, index, name)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:SetTrackName(trackType, index, name) end)
    return { ok = r == true, err = e }
  end

  ------------------------------------------------------------ 字幕 ----
  -- Resolve 20+：Project/Timeline 级 CreateSubtitlesFromAudio({autoCaptionSettings})
  function RB.subtitles_from_audio(settings)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:CreateSubtitlesFromAudio(settings or {}) end)
    return { ok = r == true or type(r) == "table", err = e, result = r }
  end
  -- 插入字幕/标题生成器（Resolve 内置 title 名）
  function RB.insert_title(titleName)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:InsertTitleIntoTimeline(titleName) end)
    return { ok = r == true, err = e, title = titleName }
  end
  function RB.insert_generator(name)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function() return t:InsertGeneratorIntoTimeline(name) end)
    return { ok = r == true, err = e }
  end

  ------------------------------------------------------------ 调色 ----
  -- 取某个片段的调色节点图（NodeGraph / Graph）
  function RB.node_graph(trackType, index, n, layerIdx)
    local it = RB.item(trackType or "video", index or 1, n or 1)
    if not it then return { ok = false, err = "no item" } end
    local g, e = call(function() return it:GetNodeGraph(layerIdx or 1) end)
    if not g then return { ok = false, err = e } end
    return { ok = true, graph = g, numNodes = g:GetNumNodes() }
  end
  function RB.color_nodes(trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local g, out = r.graph, {}
    for i = 1, r.numNodes do
      local ok2, lbl = pcall(function() return g:GetNodeLabel(i) end)
      out[i] = { index = i, label = ok2 and lbl or nil,
                 enabled = select(2, pcall(function() return nil end)) }
    end
    return { ok = true, numNodes = r.numNodes, nodes = out }
  end
  -- 节点上 LUT（nodeIndex 从 1 开始，v16.2 起是 1-based）
  function RB.set_lut(nodeIndex, lutPath, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local ok2, e = call(function() return r.graph:SetLUT(nodeIndex, lutPath) end)
    return { ok = ok2 == true, err = e, node = nodeIndex, lut = lutPath }
  end
  function RB.get_lut(nodeIndex, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local ok2, v = pcall(function() return r.graph:GetLUT(nodeIndex) end)
    return { ok = ok2, err = ok2 and nil or tostring(v), lut = ok2 and v or nil }
  end
  function RB.set_node_enabled(nodeIndex, on, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local ok2, e = call(function() return r.graph:SetNodeEnabled(nodeIndex, on) end)
    return { ok = ok2 == true, err = e }
  end
  function RB.set_node_cache(nodeIndex, mode, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local ok2, e = call(function() return r.graph:SetNodeCacheMode(nodeIndex, mode) end)
    return { ok = ok2 == true, err = e }
  end
  -- CDL（ASC CDL 四组值），真实签名 SetCDL([CDL map])
  function RB.set_cdl(cdl, trackType, index, n)
    local it = RB.item(trackType or "video", index or 1, n or 1)
    if not it then return { ok = false, err = "no item" } end
    local ok2, e = call(function() return it:SetCDL(cdl) end)
    return { ok = ok2 == true, err = e, cdl = cdl }
  end
  -- DRX 调色套用
  function RB.apply_drx(path, mode, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local ok2, e = call(function() return r.graph:ApplyGradeFromDRX(path, mode or 0) end)
    return { ok = ok2 == true, err = e, drx = path }
  end
  -- LGG / 色轮：官方 Graph 类没有直接的 SetLGG/SetColorWheel，
  -- 真实通道是节点里的 Fusion tool —— 先列出来再改。
  function RB.node_tools(nodeIndex, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local ok2, tools = pcall(function() return r.graph:GetToolsInNode(nodeIndex) end)
    if not ok2 then return { ok = false, err = tostring(tools) } end
    local out = {}
    for k, t in pairs(tools or {}) do
      local entry = { key = tostring(k), name = tostring(t) }
      local okA, attrs = pcall(function() return t:GetAttrs() end)
      if okA and attrs then entry.regID = attrs.TOOLS_RegID; entry.toolName = attrs.TOOLS_Name end
      local inputs = {}
      local okI, il = pcall(function() return t:GetInputList() end)
      if okI and il then
        for ik, inp in pairs(il) do
          local okId, id = pcall(function() return inp:GetAttrs().INPS_ID end)
          local okV, val = pcall(function() return inp[fu:GetCurrentComp() and fu:GetCurrentComp().CurrentTime or 0] end)
          if okId then inputs[id] = okV and val or nil end
        end
      end
      entry.inputs = inputs
      out[#out + 1] = entry
    end
    return { ok = true, node = nodeIndex, tools = out }
  end
  -- 直接设节点里某个 tool 的某个 input（LGG/色轮参数都走这里）
  function RB.set_node_input(nodeIndex, toolKey, inputID, value, trackType, index, n)
    local r = RB.node_graph(trackType, index, n)
    if not r.ok then return r end
    local okT, tools = pcall(function() return r.graph:GetToolsInNode(nodeIndex) end)
    if not okT or not tools then return { ok = false, err = "no tools: " .. tostring(tools) } end
    local t = tools[toolKey]
    if not t then
      for _, v in pairs(tools) do t = v; break end   -- 没给 key 就取第一个
    end
    local ok2, e = call(function() return t:SetInput(inputID, value) end)
    return { ok = ok2 == true, err = e, node = nodeIndex, input = inputID, value = value }
  end
  -- 色轮/LGG 常用参数名（Resolve ColorCorrector tool 的真实 input id）
  RB.COLOR_INPUTS = {
    lift = "Lift", gamma = "Gamma", gain = "Gain", offset = "Offset",
    contrast = "Contrast", pivot = "Pivot", saturation = "Saturation",
    hue = "Hue", temperature = "Temperature", tint = "Tint",
    exposure = "Exposure", highlights = "Highlights", shadows = "Shadows",
    masterWheel = "MasterRGB", shadowWheel = "ShadowRGB",
    midWheel = "MidRGB", highlightWheel = "HighlightRGB",
  }
  function RB.set_lgg(nodeIndex, lift, gamma, gain, trackType, index, n)
    local a = RB.set_node_input(nodeIndex, nil, "Lift",  lift,  trackType, index, n)
    local b = RB.set_node_input(nodeIndex, nil, "Gamma", gamma, trackType, index, n)
    local c = RB.set_node_input(nodeIndex, nil, "Gain",  gain,  trackType, index, n)
    return { ok = a.ok and b.ok and c.ok, lift = a, gamma = b, gain = c }
  end
  function RB.set_wheel(nodeIndex, which, rgb, trackType, index, n)
    local id = RB.COLOR_INPUTS[which] or which
    return RB.set_node_input(nodeIndex, nil, id, rgb, trackType, index, n)
  end
  -- 片段级导出 LUT
  function RB.export_lut(exportType, path, trackType, index, n)
    local it = RB.item(trackType or "video", index or 1, n or 1)
    if not it then return { ok = false, err = "no item" } end
    local ok2, e = call(function() return it:ExportLUT(exportType or 1, path) end)
    return { ok = ok2 == true, err = e, path = path }
  end

  ------------------------------------------------------------ 渲染 ----
  function RB.render_settings(t)          -- {settings} 表
    local r, e = call(function() return proj():SetRenderSettings(t) end)
    return { ok = r == true, err = e }
  end
  function RB.get_render_settings()
    local ok, r = pcall(function()
      local p = proj()
      return { formats = p:GetRenderFormats(),
               current = { p:GetCurrentRenderFormatAndCodec() },
               mode = p:GetCurrentRenderMode(),
               presets = p:GetRenderPresetList() }
    end)
    return { ok = ok, err = ok and nil or tostring(r), settings = ok and r or nil }
  end
  function RB.add_render_job()
    local r, e = call(function() return proj():AddRenderJob() end)
    return { ok = r ~= nil, err = e, jobId = r }
  end
  function RB.render_job_list()
    local ok, r = pcall(function() return proj():GetRenderJobList() end)
    return { ok = ok, err = ok and nil or tostring(r), jobs = ok and r or nil }
  end
  function RB.render_status(jobId)
    local ok, r = pcall(function() return proj():GetRenderJobStatus(jobId) end)
    return { ok = ok, err = ok and nil or tostring(r), status = ok and r or nil }
  end
  function RB.start_render(jobIds, interactive)
    local p = proj()
    local ok, r
    if type(jobIds) == "table" then
      ok, r = pcall(function() return p:StartRendering(jobIds, interactive == true) end)
    else
      ok, r = pcall(function() return p:StartRendering(interactive == true) end)
    end
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end
  function RB.stop_render()
    local ok, r = pcall(function() return proj():StopRendering() end)
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end
  function RB.is_rendering()
    local ok, r = pcall(function() return proj():IsRenderingInProgress() end)
    return { ok = ok, rendering = ok and r or nil, err = ok and nil or tostring(r) }
  end
  function RB.delete_render_job(jobId)
    local ok, r = pcall(function() return proj():DeleteRenderJob(jobId) end)
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end
  function RB.quick_export(preset, params)
    local ok, r = pcall(function() return proj():RenderWithQuickExport(preset, params or {}) end)
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end
  function RB.render_presets()
    local ok, r = pcall(function() return proj():GetRenderPresetList() end)
    return { ok = ok, err = ok and nil or tostring(r), presets = ok and r or nil }
  end
  function RB.load_render_preset(name)
    local ok, r = pcall(function() return proj():LoadRenderPreset(name) end)
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end

  ------------------------------------------- 项目/时间线级 Setting ----
  -- GetSetting/SetSetting 是项目级参数总口子（分辨率、帧率、色彩科学…）
  function RB.get_setting(name)
    local ok, r = pcall(function() return proj():GetSetting(name) end)
    return { ok = ok, err = ok and nil or tostring(r), name = name, value = ok and r or nil }
  end
  function RB.set_setting(name, value)
    local ok, r = pcall(function() return proj():SetSetting(name, value) end)
    return { ok = ok and r == true, err = ok and nil or tostring(r), name = name, value = value }
  end
  function RB.get_timeline_setting(name)
    local ok, r = pcall(function() return tl():GetSetting(name) end)
    return { ok = ok, err = ok and nil or tostring(r), name = name, value = ok and r or nil }
  end
  function RB.set_timeline_setting(name, value)
    local ok, r = pcall(function() return tl():SetSetting(name, value) end)
    return { ok = ok and r == true, err = ok and nil or tostring(r) }
  end
  -- 常用 setting 名（真实存在于 Resolve 项目设置）
  RB.SETTINGS = {
    resolution       = "timelineResolution",
    frameRate        = "timelineFrameRate",
    playbackFrameRate= "timelinePlaybackFrameRate",
    colorScience     = "colorScienceMode",
    outputColorSpace = "colorSpaceOutput",
    workingColorSpace= "colorSpaceWorking",
    inputColorSpace  = "colorSpaceInput",
    timelineOutput   = "timelineOutputResolution",
    videoMonitor     = "videoMonitorFormat",
    superScale       = "superScale",
  }

  ------------------------------------------------------------ 标记 ----
  function RB.add_marker(frameId, color, name, note, duration, customData)
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local r, e = call(function()
      return t:AddMarker(frameId, color or "Blue", name or "", note or "", duration or 1, customData or "")
    end)
    return { ok = r == true, err = e, frame = frameId }
  end
  function RB.markers()
    local t = tl()
    if not t then return { ok = false, err = "no timeline" } end
    local ok, r = pcall(function() return t:GetMarkers() end)
    return { ok = ok, err = ok and nil or tostring(r), markers = ok and r or nil }
  end

  ------------------------------------------------------------ 页面 ----
  function RB.open_page(name)
    local ok, r = pcall(function() return app():OpenPage(name) end)
    return { ok = ok and r == true, err = ok and nil or tostring(r), page = name }
  end
  function RB.current_page()
    local ok, r = pcall(function() return app():GetCurrentPage() end)
    return { ok = ok, page = ok and r or nil, err = ok and nil or tostring(r) }
  end
  function RB.version()
    local ok, r = pcall(function()
      return { product = app():GetProductName(), version = app():GetVersionString(),
               fields = app():GetVersion() }
    end)
    return { ok = ok, info = ok and r or nil, err = ok and nil or tostring(r) }
  end

  ------------------------------------------------- 桥控制 / Fusion ----
  local B = { version = VERSION, root = ROOT, renders = RENDERS, RB = RB }
  function B.comp() return C() end
  function B.resolve() return app() end
  function B.tool(name)
    local t = C():FindTool(name)
    if not t then error("no tool named " .. tostring(name)) end
    return t
  end
  function B.tools()
    local out = {}
    for _, t in pairs(C():GetToolList(false)) do out[#out + 1] = t end
    return out
  end
  function B.add(regid, name)
    local t = C():AddTool(regid, -32768, -32768)   -- 放屏幕外，避免自动连线
    if name then t:SetAttrs({ TOOLS_Name = name }) end
    return t
  end
  function B.stop() stop_requested = true end
  function B.seek(frame) C().CurrentTime = frame end

  ------------------------------------------------------ 执行一条命令 ----
  local function save_result(c, base, payload)
    local path = OUTBOX .. base .. ".comp"
    local ok, saved = pcall(function()
      local tc = fu:NewComp(true, false, false)     -- 隐藏临时 comp，不污染用户的
      if not tc then return false end
      tc:SetData("FreeBridge", { id = base, payload = payload })
      local s = tc:Save(path)
      tc:Close()
      return s
    end)
    if ok and saved then return true, "temp" end
    -- 兜底：拿不到隐藏 comp 就用当前 comp
    local c2 = c or C()
    if not c2 then return false, "none" end
    c2:SetData("FreeBridge", { id = base, payload = payload })
    local s = c2:Save(path)
    c2:SetData("FreeBridge", nil)
    return s, "comp"
  end

  local function run(n)
    local base = string.format("cmd_%06d", n)
    local lines, oldprint = {}, print
    print = function(...)
      local parts = {}
      for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
      lines[#lines + 1] = table.concat(parts, "\t")
    end
    result, comp, RB_ = nil, C(), RB
    local c = comp
    local t0 = os.time()
    if c then pcall(function() c:StartUndo("free_bridge " .. base) end) end
    local ok, err = pcall(dofile, INBOX .. base .. ".lua")
    if c then pcall(function() c:EndUndo(true) end) end
    print = oldprint
    local out = { id = n, status = ok and "OK" or "ERROR",
                  error = (not ok) and tostring(err) or nil,
                  printed = lines, result = result,
                  elapsed = os.difftime(os.time(), t0), bridge = VERSION }
    local jok, js = pcall(json, out)
    if not jok then
      js = json({ id = n, status = "ERROR",
                  error = "result encode failed: " .. tostring(js), printed = lines })
    end
    local saved, how = save_result(C() or c, base, hex(js))
    print(string.format("free_bridge: %s -> %s%s%s [%s]", base, out.status,
          out.error and ("  " .. out.error) or "",
          saved and "" or "  (WARNING: 结果没能写出)", how))
    return saved
  end

  local function first_pending()
    if not exists(STATE) then return 1 end
    local ok, st = pcall(dofile, STATE)
    if ok and type(st) == "table" and tonumber(st.first_pending) then
      return tonumber(st.first_pending)
    end
    return 1
  end
  local function name_of(n) return string.format("cmd_%06d", n) end

  ---------------------------------------------------------- 主循环 ----
  print(string.format("free_bridge %s: 监听 %s (%s)", VERSION, ROOT,
        ONCE and "once" or (MINUTES > 0 and (MINUTES .. " 分钟") or "直到 stop")))
  if exists(STOP) then print("free_bridge: 存在 stop 文件，删掉它才会继续监听") end

  local n = first_pending()
  local started = os.time()
  local last_cmd, last_state, last_gc = os.time(), -1e9, os.time()
  local IDLE_AFTER, IDLE_POLL, STATE_EVERY, GC_EVERY = 60, math.max(POLL, 2), 5, 60

  while true do
    local now = os.time()
    if now - last_state >= STATE_EVERY then
      local fp = first_pending(); if fp > n then n = fp end
      last_state = now
    end
    while exists(OUTBOX .. name_of(n) .. ".comp") do n = n + 1 end
    if exists(INBOX .. name_of(n) .. ".lua") then
      run(n); n = n + 1; last_cmd = os.time()
      if stop_requested then break end
    else
      if ONCE then break end
      if exists(STOP) then break end
      if MINUTES > 0 and os.difftime(os.time(), started) > MINUTES * 60 then break end
      if now - last_gc >= GC_EVERY then collectgarbage("collect"); last_gc = now end
      bmd.wait((now - last_cmd > IDLE_AFTER) and IDLE_POLL or POLL)
    end
  end
  print("free_bridge: 已停止")
  return true
end

return start
