-- A black curtain over the picture: the first picture of a file comes out of the black, and stopping goes into
-- it (the sound fading with it) before the player closes; Matinee's own page is black behind it at that moment
-- and comes up out of the black too (style.css, #curtain). Drawn as an overlay, so the video itself is left
-- alone and hardware decoding is kept.
local curtain = mp.create_osd_overlay("ass-events")
curtain.res_x, curtain.res_y, curtain.z = 1920, 1080, -1
local curtain_timer, fade_pending, quitting = nil, false, false

local function draw_curtain(opacity) -- 0: clear, 1: black
    if opacity <= 0 then
        curtain:remove()
        return
    end
    curtain.data = string.format("{\\an7\\pos(0,0)\\bord0\\shad0\\4a&HFF&\\1c&H000000&\\1a&H%02X&\\p1}m 0 0 l 1920 0 1920 1080 0 1080{\\p0}",
        math.floor((1 - math.min(opacity, 1)) * 255 + 0.5))
    curtain:update()
end

-- from one opacity to another in a time; done() afterwards
local function fade(from, to, seconds, done)
    if curtain_timer then curtain_timer:kill() end
    local start = mp.get_time()
    draw_curtain(from)
    curtain_timer = mp.add_periodic_timer(1 / 60, function()
        local k = math.min(1, (mp.get_time() - start) / seconds)
        k = k * k * (3 - 2 * k) -- eased at both ends
        draw_curtain(from + (to - from) * k)
        if to > from then -- going into the black: the sound fades too (volume-gain, so the volume Matinee keeps stays)
            mp.set_property_number("volume-gain", -40 * k)
        end
        if k >= 1 then
            curtain_timer:kill()
            curtain_timer = nil
            if done then done() end
        end
    end)
end

-- The bar at the bottom (in place of mpv's own controls): a soft shade from below, the title,
-- a thin line of the progress with the time played on its left and the time left on its right; on a channel live
-- "LIVE" instead of the line. It comes with a seek, OK and pausing (kino-show), stays while paused and goes 3 s after.
local bar = mp.create_osd_overlay("ass-events")
bar.res_x, bar.res_y = 1920, 1080
local W, EDGE = 1920, 86 -- the screen and its edge left and right (4.5vw in Matinee's pages)
local bar_until, bar_timer = 0, nil
local panel_shown = false -- the info panel (below): the bar stays away meanwhile
local scrub_to = nil -- while seeking with the arrows: where the playback is to go (one seek when it is done)
local hover_to = nil -- the mouse over the bar's line: the time under it (its picture shows as while seeking)
local hover_end = function() end -- the bar gone: no time or picture of the mouse's left (set below)
-- "Ends at 23:41" beside the title, in Matinee's language (the backend passes the text, os.date's format in it); natural:
-- the desktop scrolls "naturally" (swiped up is the wheel down), so the wheel's volume is turned round: up is louder;
-- menu: on a desktop how to reach the info panel, under the bar (a laptop's keyboard has no menu key)
local opts = { ends = "Ends at %H:%M", natural = false, menu = "" }
require("mp.options").read_options(opts, "kino")

local function clock(s)
    s = math.max(0, math.floor(s + 0.5))
    local h, m = math.floor(s / 3600), math.floor(s % 3600 / 60)
    if h > 0 then return string.format("%d:%02d:%02d", h, m, s % 60) end
    return string.format("%d:%02d", m, s % 60)
end

-- a pill from x, y, w wide and h high: its ends half circles (quarters as bezier curves)
local function pill(x, y, w, h)
    local r = h / 2
    local k = r * 0.5523
    return string.format("m %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f "
        .. "l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f",
        x + r, y, x + w - r, y,
        x + w - r + k, y, x + w, y + r - k, x + w, y + r,
        x + w, y + r + k, x + w - r + k, y + h, x + w - r, y + h,
        x + r, y + h,
        x + r - k, y + h, x, y + r + k, x, y + r,
        x, y + r - k, x + r - k, y, x + r, y)
end

local function shape(alpha, color, path) -- a filled drawing: alpha 00 opaque to FF clear, color BGR
    -- the messages' box (background-box in mpv.conf) is drawn around a drawing too, from the screen's corner to its
    -- far end, in the shadow's color: made clear here, as for the texts
    return string.format("{\\an7\\pos(0,0)\\bord0\\shad0\\4a&HFF&\\1a&H%s&\\1c&H%s&\\p1}%s{\\p0}", alpha, color, path)
end

local function text(an, x, y, size, alpha, s, bold, color)
    -- shown as it is, never read as ASS: a backslash loses its meaning by a zero-width letter after it (as mpv's
    -- own scripts do it; doubled, "\\n" still broke the line)
    s = s:gsub("\\", "\\\239\187\191"):gsub("{", "\\{"):gsub("\n", " ")
    -- the messages' box (background-box in mpv.conf) is drawn in the shadow's color: made clear here
    return string.format("{\\an%d\\pos(%d,%d)\\fs%d\\b%d\\bord0\\shad0\\4a&HFF&\\1c&H%s&\\1a&H%s&}%s",
        an, x, y, size, bold and 1 or 0, color or "F7F5F5", alpha, s)
end

local function chars(s) return select(2, s:gsub("[^\128-\191]", "")) end -- letters, not bytes

-- a text broken into lines of about this many pixels (Inter: a letter about half its size wide), at most so many,
-- the last ending with "…" if there is more
local function wrap(s, size, width, most)
    local per, lines, line = math.floor(width / (size * 0.48)), {}, ""
    for word in s:gmatch("%S+") do
        if line ~= "" and chars(line) + 1 + chars(word) > per then
            if #lines == most - 1 then
                lines[#lines + 1] = line .. "…"
                return lines
            end
            lines[#lines + 1] = line
            line = word
        else
            line = line == "" and word or line .. " " .. word
        end
    end
    if line ~= "" then lines[#lines + 1] = line end
    return lines
end

-- "Ends at 23:41": when the video ends played on from a time
local function ends_at(from, dur)
    local speed = mp.get_property_number("speed") or 1
    return (os.date(opts.ends, math.floor(os.time() + (dur - from) / speed + 0.5)):gsub(" 0(%d:)", " %1"))
end

local function draw_bar()
    local pos, dur = mp.get_property_number("time-pos"), mp.get_property_number("duration")
    local shown = scrub_to or pos -- the time left and the end are those of where it goes
    local live = mp.get_property_native("user-data/kino/live") == true
    local out = {}
    for i = 0, 59 do -- the shade: 60 thin bands, darker towards the bottom (eased), too fine to be seen as steps
        local k = (i + 1) / 60
        out[#out + 1] = shape(string.format("%02X", math.floor(255 - 185 * k * k)), "000000",
            string.format("m 0 %.1f l %d %.1f %d %.1f 0 %.1f", 690 + i * 6.5, W, 690 + i * 6.5, W, 697 + i * 6.5, 697 + i * 6.5))
    end
    -- a long title ("show – S01E02 – episode", a video's) ends with "…" before the end's time on the right, rather
    -- than running under it and off the screen (bold: wider than wrap's letters, hence the larger size)
    local title = wrap(mp.get_property("media-title") or "", 53, W - 2 * EDGE - (live and 0 or 280), 1)[1] or ""
    out[#out + 1] = text(1, EDGE, 932, 46, "00", title, true)
    if live then
        out[#out + 1] = shape("00", "2B30C4", pill(EDGE, 958, 92, 40)) -- Matinee's red for live (#c4302b)
        out[#out + 1] = text(5, EDGE + 46, 978, 26, "00", "LIVE", true)
    elseif pos and dur and dur > 0 then
        local width = W - 2 * EDGE
        local function at(s) return EDGE + width * math.max(0, math.min(1, s / dur)) end
        out[#out + 1] = shape("A8", "FFFFFF", pill(EDGE, 962, width, 8))
        out[#out + 1] = shape("00", "FFFFFF", pill(EDGE, 962, math.max(8, at(pos) - EDGE), 8))
        for _, c in ipairs(mp.get_property_native("chapter-list") or {}) do -- the chapters: small gaps in the line
            if c.time > 1 and c.time < dur - 1 then
                out[#out + 1] = shape("20", "000000", string.format("m %.1f 962 l %.1f 962 %.1f 970 %.1f 970",
                    at(c.time) - 2, at(c.time) + 2, at(c.time) + 2, at(c.time) - 2))
            end
        end
        if scrub_to then -- the mark of where it goes, its time over it
            local x = at(scrub_to)
            out[#out + 1] = shape("00", "FFFFFF", string.format("m %.1f 946 l %.1f 946 %.1f 984 %.1f 984", x - 3, x + 3, x + 3, x - 3))
            out[#out + 1] = text(2, math.max(EDGE + 40, math.min(W - EDGE - 40, x)), 940, 30, "00", clock(scrub_to), true)
        elseif hover_to then -- where a click would go: a fainter mark, its time over it
            local x = at(hover_to)
            out[#out + 1] = shape("50", "FFFFFF", string.format("m %.1f 950 l %.1f 950 %.1f 978 %.1f 978", x - 2, x + 2, x + 2, x - 2))
            out[#out + 1] = text(2, math.max(EDGE + 40, math.min(W - EDGE - 40, x)), 940, 30, "00", clock(hover_to), true)
        end
        out[#out + 1] = text(7, EDGE, 986, 30, "40", clock(pos))
        out[#out + 1] = text(9, W - EDGE, 986, 30, "40", "−" .. clock(dur - shown))
        out[#out + 1] = text(3, W - EDGE, 932, 30, "40", ends_at(shown, dur))
        if opts.menu ~= "" then out[#out + 1] = text(8, W / 2, 986, 26, "70", opts.menu) end
    end
    bar.data = table.concat(out, "\n")
    bar:update()
end

local function hide_bar()
    hover_end()
    if bar_timer then bar_timer:kill() bar_timer = nil end
    bar:remove()
end

local function show_bar(seconds)
    if quitting or panel_shown then return end
    bar_until = mp.get_time() + (seconds or 3)
    draw_bar()
    if bar_timer then return end
    bar_timer = mp.add_periodic_timer(0.25, function()
        if mp.get_time() > bar_until and not mp.get_property_native("pause") then hide_bar() else draw_bar() end
    end)
end
mp.register_script_message("kino-show", function() show_bar() end)

-- While seeking, the picture of where the mark is, over it (Jellyfin's trickplay pictures, if it made them; the
-- backend puts what is needed into user-data/kino/trickplay): the backend cuts it out of its tile when asked
-- (kino-preview index height) into a raw file, which is shown with overlay-add as soon as it is there.
local utils = require("mp.utils")
local preview_timer, preview_on = nil, false
local function preview_hide()
    if preview_timer then preview_timer:kill() preview_timer = nil end
    if preview_on then
        mp.commandv("overlay-remove", "1")
        preview_on = false
        mp.set_property("user-data/kino/preview", "")
    end
end
local function preview_show()
    local tp = mp.get_property_native("user-data/kino/trickplay")
    local to = scrub_to or hover_to
    if type(tp) ~= "table" or not to then return end
    local index = math.max(0, math.min(tp.count - 1, math.floor(to * 1000 / tp.interval)))
    local sw, sh = mp.get_property_number("osd-width", 0), mp.get_property_number("osd-height", 0)
    if sw <= 0 or sh <= 0 then sw, sh = 1920, 1080 end -- no window (the tests)
    local k = sh / 1080
    local h = math.floor(216 * k + 0.5) -- a fifth of the screen high
    local w = math.floor(h * tp.w / tp.h + 0.5)
    local file = string.format("%s/%d-%d.bgra", tp.dir, h, index)
    local function show()
        local now = scrub_to or hover_to
        if not now or not utils.file_info(file) then return false end
        local dur = mp.get_property_number("duration") or 1
        local mark = (EDGE + (W - 2 * EDGE) * math.max(0, math.min(1, now / dur))) * sw / W
        local x = math.floor(math.max(EDGE * sw / W, math.min(mark - w / 2, (W - EDGE) * sw / W - w)))
        mp.commandv("overlay-add", "1", tostring(x), tostring(math.floor(900 * k - h)), file, "0", "bgra",
            tostring(w), tostring(h), tostring(w * 4)) -- over the mark's time
        preview_on = true
        mp.set_property_number("user-data/kino/preview", index)
        return true
    end
    if preview_timer then preview_timer:kill() preview_timer = nil end
    if show() then return end
    mp.commandv("script-message", "kino-preview", tostring(index), tostring(h))
    local tries = 0
    preview_timer = mp.add_periodic_timer(0.1, function() -- the picture before stays until this one is there
        tries = tries + 1
        if show() or tries > 50 then preview_timer:kill() preview_timer = nil end
    end)
end

hover_end = function()
    if not hover_to then return end
    hover_to = nil
    if not scrub_to then preview_hide() end
end

-- Seeking: left and right (and the media keys' steps) move a mark along the line, its time
-- over it, and the playback goes there with one seek only: while it plays a moment after the last press, while
-- paused with OK or play (Back leaves it where it was). Held, the steps grow. One seek instead of one per press:
-- each seek has to load the stream again. On a channel live, or without a length, a seek at once as before.
local scrub_timer, hold_timer = nil, nil
local function scrub_done()
    if scrub_timer then scrub_timer:kill() scrub_timer = nil end
    preview_hide()
    local to = scrub_to
    scrub_to = nil
    if to then mp.commandv("seek", tostring(to), "absolute") end
    show_bar()
end
local function scrub_cancel()
    if scrub_timer then scrub_timer:kill() scrub_timer = nil end
    preview_hide()
    scrub_to = nil
    show_bar()
end
local function scrub(step)
    local pos, dur = mp.get_property_number("time-pos"), mp.get_property_number("duration")
    if mp.get_property_native("user-data/kino/live") == true or not pos or not dur or dur <= 0 then
        mp.commandv("seek", tostring(step))
        show_bar()
        return
    end
    scrub_to = math.max(0, math.min(dur - 1, (scrub_to or pos) + step))
    show_bar()
    preview_show()
    if scrub_timer then scrub_timer:kill() scrub_timer = nil end
    if not mp.get_property_native("pause") then scrub_timer = mp.add_timeout(0.7, scrub_done) end
end
-- a key: one step when pressed, and held more steps, larger the longer (keys that only report presses: one each)
local function scrub_key(step)
    return function(t)
        mp.commandv("script-message", "kino-active")
        if t.event == "down" or t.event == "press" then
            if hold_timer then hold_timer:kill() hold_timer = nil end
            scrub(step)
            if t.event == "down" then
                local start = mp.get_time()
                hold_timer = mp.add_periodic_timer(0.2, function()
                    local held = mp.get_time() - start
                    if held < 0.5 then return end
                    scrub(step * (held > 4 and 6 or held > 2 and 3 or 1))
                end)
            end
        elseif t.event == "up" then
            if hold_timer then hold_timer:kill() hold_timer = nil end
        end
    end
end
for name, step in pairs({ left = -10, right = 10, rewind = -30, forward = 30, back10 = -600, ahead10 = 600 }) do
    mp.add_key_binding(nil, "seek-" .. name, scrub_key(step), { complex = true })
end

-- The info panel at the top (in place of mpv's own lists): down opens it on Info (the name, a
-- line about it, its description, when it ends; then chapters, audio, subtitles and speed), up on Audio, the chapters key on Chapters. Left and right go from
-- tab to tab, down into a list and through it, up back to the tabs (and from the tabs closes it), OK picks; Back,
-- or 10 s without a key, closes it. The video plays on. The words come from the backend (user-data/kino/ui), the
-- name and description too (user-data/kino/about).
local panel = mp.create_osd_overlay("ass-events")
panel.res_x, panel.res_y, panel.z = 1920, 1080, 1
local P = { tab = 1, row = 0, top = 1, idle = nil, tick = nil, find = false } -- row 0: on the tabs
-- find: the subtitles tab shows subtitles from the internet (its last row, "Find Subtitles…"), until the tabs again
local PANEL_KEYS = { UP = "up", KP_UP = "up", DOWN = "down", KP_DOWN = "down", LEFT = "left", KP_LEFT = "left",
    RIGHT = "right", KP_RIGHT = "right", ENTER = "ok", KP_ENTER = "ok", ESC = "close", BS = "close",
    GO_BACK = "close", MBTN_BACK = "close", MBTN_RIGHT = "close", CANCEL = "close", MENU = "close", TAB = "close", TOOLS = "close", c = "close" }
local PX, PY, PW, ROWS, ROW_H, LIST_W = EDGE, 50, W - 2 * EDGE, 6, 58, 1000
local CODECS = { ac3 = "Dolby Digital", eac3 = "Dolby Digital Plus", truehd = "Dolby TrueHD", dts = "DTS",
    aac = "AAC", flac = "FLAC", opus = "Opus", mp3 = "MP3", vorbis = "Vorbis", pcm_s16le = "PCM", pcm_s24le = "PCM" }

local function ui(key, fallback)
    local u = mp.get_property_native("user-data/kino/ui")
    return type(u) == "table" and u[key] or fallback
end

local function lang_name(code)
    if type(code) ~= "string" or code == "" or code == "und" then return nil end
    return (ui("langs", {}))[code:lower()] or code:upper()
end

local function rrect(x, y, w, h, r) -- a rectangle with round corners
    local k = r * 0.5523
    return string.format("m %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f l %.1f %.1f "
        .. "b %.1f %.1f %.1f %.1f %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f l %.1f %.1f "
        .. "b %.1f %.1f %.1f %.1f %.1f %.1f",
        x + r, y, x + w - r, y, x + w - r + k, y, x + w, y + r - k, x + w, y + r,
        x + w, y + h - r, x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h,
        x + r, y + h, x + r - k, y + h, x, y + h - r + k, x, y + h - r,
        x, y + r, x, y + r - k, x + r - k, y, x + r, y)
end

local function panel_tabs()
    local out = { { id = "info", name = ui("info", "Info") } }
    if #(mp.get_property_native("chapter-list") or {}) > 1 then
        out[#out + 1] = { id = "chapters", name = ui("chapters", "Chapters") }
    end
    out[#out + 1] = { id = "audio", name = ui("audio", "Audio") }
    out[#out + 1] = { id = "subtitles", name = ui("subtitles", "Subtitles") }
    if not mp.get_property_native("user-data/kino/live") then -- a channel live plays as it comes
        out[#out + 1] = { id = "speed", name = ui("speed", "Speed") }
    end
    -- the other Matinees of the account that are on (the backend: user-data/kino/handoff): go on there
    if #(mp.get_property_native("user-data/kino/handoff") or {}) > 0 then
        out[#out + 1] = { id = "handoff", name = ui("handoff", "Continue on") }
    end
    return out
end

-- the speeds to choose (the pitch stays: mpv stretches the sound)
local SPEEDS = { 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2 }
local function speed_name(v)
    if v == 1 then return ui("normal", "Normal") end
    return (string.format("%g", v):gsub("%.", ui("decimal", "."))) .. "×"
end

-- a tab's list: each row its name, a grey addition, whether it is the one playing, and what OK does
local function panel_rows(id)
    local out = {}
    if id == "chapters" then
        local current = mp.get_property_number("chapter")
        for i, c in ipairs(mp.get_property_native("chapter-list") or {}) do
            local name = (c.title and c.title ~= "") and c.title or (ui("chapter", "Chapter {n}"):gsub("{n}", tostring(i)))
            out[#out + 1] = { name = name, extra = clock(c.time), on = current == i - 1, close = true,
                pick = function() mp.set_property_number("chapter", i - 1) end }
        end
    elseif id == "speed" then
        local now = mp.get_property_number("speed") or 1
        for _, v in ipairs(SPEEDS) do
            out[#out + 1] = { name = speed_name(v), extra = "", on = math.abs(now - v) < 0.01,
                pick = function() mp.set_property_number("speed", v) end }
        end
    elseif id == "handoff" then
        for _, d in ipairs(mp.get_property_native("user-data/kino/handoff") or {}) do
            out[#out + 1] = { name = d.name, extra = "", close = true, pick = function()
                -- the backend ends it here once the player is closed, and tells that device where it was
                mp.commandv("script-message", "kino-handoff", d.id)
                mp.osd_message((ui("handing", "Going on on {name}…"):gsub("{name}", function() return d.name end)), 2)
                mp.add_timeout(1.2, function() mp.commandv("script-message", "kino-quit") end)
            end }
        end
    elseif id == "subtitles" and P.find then
        -- found by Jellyfin (backend/library.py: find_remote_subs); OK downloads the one chosen, which is then picked
        local found = mp.get_property_native("user-data/kino/subfind") or {}
        local say = found.state == "failed" and ui("failed", "The search for subtitles did not work")
            or found.state ~= "done" and ui("looking", "Looking for subtitles…")
            or #(found.items or {}) == 0 and ui("none", "No subtitles found")
        if say then
            out[1] = { name = say, extra = "", pick = function() end }
        else
            for _, s in ipairs(found.items) do
                out[#out + 1] = { name = s.name, extra = s.extra or "", close = true,
                    pick = function() mp.commandv("script-message", "kino-get-sub", s.id) end }
            end
        end
    elseif id == "audio" or id == "subtitles" then
        local kind = id == "audio" and "audio" or "sub"
        if kind == "sub" then
            out[1] = { name = ui("off", "Off"), extra = "", on = not mp.get_property_native("current-tracks/sub"),
                pick = function() mp.set_property("sid", "no") end }
        end
        local sublangs = mp.get_property_native("user-data/kino/sublangs") or {}
        for _, t in ipairs(mp.get_property_native("track-list") or {}) do
            if t.type == kind then
                local name = lang_name(t.lang or sublangs[t["external-filename"] or ""])
                local extra = {}
                if t.title and t.title ~= "" then
                    if name and t.title:lower() ~= name:lower() then extra[#extra + 1] = t.title else name = name or t.title end
                end
                name = name or ui(id, id) .. " " .. t.id
                if kind == "audio" then
                    local n = t["demux-channel-count"]
                    local layout = n and (n >= 8 and "7.1" or n >= 6 and "5.1" or n == 2 and "Stereo" or n == 1 and "Mono") or nil
                    local codec = CODECS[t.codec or ""]
                    if codec or layout then extra[#extra + 1] = table.concat({ codec, layout }, " ") end
                elseif t.forced then
                    extra[#extra + 1] = ui("forced", "Forced")
                end
                local track = t.id
                out[#out + 1] = { name = name, extra = table.concat(extra, " · "), on = t.selected,
                    pick = function() mp.set_property_number(kind == "audio" and "aid" or "sid", track) end }
            end
        end
        if kind == "sub" and mp.get_property_native("user-data/kino/subsearch") then -- a Jellyfin movie or episode
            out[#out + 1] = { name = ui("find", "Find Subtitles…"), extra = "", pick = function()
                mp.set_property_native("user-data/kino/subfind", { state = "looking" })
                P.find, P.row, P.top = true, 1, 1
                mp.commandv("script-message", "kino-find-subs")
            end }
        end
    end
    return out
end

local function draw_panel()
    local tabs = panel_tabs()
    P.tab = math.max(1, math.min(P.tab, #tabs))
    local id = tabs[P.tab].id
    local out, body = {}, {}
    -- the tabs: the chosen one on a light pill while they have the focus, otherwise bold
    local x = PX + 40
    for i, t in ipairs(tabs) do
        local w = chars(t.name) * 15 + 52
        if i == P.tab and P.row == 0 then
            body[#body + 1] = shape("00", "F5F5F7", pill(x, PY + 34, w, 52))
            body[#body + 1] = text(5, x + w / 2, PY + 60, 30, "00", t.name, true, "1E1C1C")
        else
            body[#body + 1] = text(5, x + w / 2, PY + 60, 30, i == P.tab and "00" or "70", t.name, i == P.tab)
        end
        x = x + w + 4
    end
    local y, bottom = PY + 130, PY + 130
    if id == "info" then
        local about = mp.get_property_native("user-data/kino/about") or {}
        local title = (about.title and about.title ~= "") and about.title or mp.get_property("media-title") or ""
        body[#body + 1] = text(7, PX + 40, y, 40, "00", title, true)
        local pos, dur = mp.get_property_number("time-pos"), mp.get_property_number("duration")
        if pos and dur and dur > 0 and not mp.get_property_native("user-data/kino/live") then
            body[#body + 1] = text(9, PX + PW - 40, y + 6, 28, "40", ends_at(pos, dur))
        end
        y = y + 56
        if about.line and about.line ~= "" then
            body[#body + 1] = text(7, PX + 40, y, 28, "50", about.line)
            y = y + 46
        end
        for _, line in ipairs(wrap(about.text or "", 30, PW - 80, 4)) do
            body[#body + 1] = text(7, PX + 40, y, 30, "20", line)
            y = y + 42
        end
        bottom = y + 30
    else
        local rows = panel_rows(id)
        if P.row > 0 then
            P.row = math.min(P.row, #rows)
            if P.row < P.top then P.top = P.row elseif P.row >= P.top + ROWS then P.top = P.row - ROWS + 1 end
        end
        for i = P.top, math.min(#rows, P.top + ROWS - 1) do
            local r, focus = rows[i], i == P.row
            local ink = focus and "1E1C1C" or "F7F5F5"
            if focus then body[#body + 1] = shape("00", "F5F5F7", rrect(PX + 24, y, LIST_W, ROW_H - 6, 14)) end
            if r.on then body[#body + 1] = shape("00", ink, pill(PX + 48, y + (ROW_H - 6) / 2 - 6, 12, 12)) end
            body[#body + 1] = text(4, PX + 80, y + (ROW_H - 6) / 2, 30, "00", r.name, r.on, ink)
            if r.extra ~= "" then
                body[#body + 1] = text(6, PX + 24 + LIST_W - 28, y + (ROW_H - 6) / 2, 26, focus and "30" or "60", r.extra, false, ink)
            end
            y = y + ROW_H
        end
        bottom = y + 24
    end
    P.bottom = bottom -- where a click is still in it (the mouse, below)
    out[1] = shape("1C", "1E1C1C", rrect(PX, PY, PW, bottom - PY, 28)) -- the panel: dark, a little of the picture through
    for _, b in ipairs(body) do out[#out + 1] = b end
    panel.data = table.concat(out, "\n")
    panel:update()
    mp.set_property("user-data/kino/panel", id)
end

local function panel_close()
    if not panel_shown then return end
    panel_shown = false
    P.find = false
    for key in pairs(PANEL_KEYS) do mp.remove_key_binding("kino-panel-" .. key) end
    if P.idle then P.idle:kill() P.idle = nil end
    if P.tick then P.tick:kill() P.tick = nil end
    panel:remove()
    mp.set_property("user-data/kino/panel", "")
end

local function panel_key(what)
    mp.commandv("script-message", "kino-active")
    if P.idle then P.idle:kill() end
    P.idle = mp.add_timeout(10, panel_close)
    local tabs = panel_tabs()
    local rows = panel_rows(tabs[math.min(P.tab, #tabs)].id)
    if what == "close" then return panel_close() end
    if what == "left" or what == "right" then
        P.tab = math.max(1, math.min(#tabs, P.tab + (what == "left" and -1 or 1)))
        P.row, P.top, P.find = 0, 1, false
    elseif what == "down" then
        P.row = math.min(#rows, P.row + 1)
    elseif what == "up" then
        if P.row == 0 then return panel_close() end
        P.row = P.row - 1
        if P.row == 0 then P.find = false end -- the subtitles at hand again
    elseif what == "ok" then
        if P.row == 0 then
            if #rows == 0 then return panel_close() end -- Info: OK closes it
            P.row = 1
            for i, r in ipairs(rows) do if r.on then P.row = i end end
        else
            rows[P.row].pick()
            if rows[P.row].close then return panel_close() end
            mp.add_timeout(0.15, function() if panel_shown then draw_panel() end end) -- once mpv has switched
        end
    end
    draw_panel()
end

-- open on a tab; into its list, on the row playing, when asked
local function panel_open(id, into_list)
    if panel_shown or quitting then return end
    if scrub_to then scrub_cancel() end
    hide_bar()
    panel_shown = true
    P.tab, P.row, P.top = 1, 0, 1
    for i, t in ipairs(panel_tabs()) do if t.id == id then P.tab = i end end
    if into_list then
        for i, r in ipairs(panel_rows(id)) do if r.on or i == 1 and P.row == 0 then P.row = i end end
    end
    for key, what in pairs(PANEL_KEYS) do
        mp.add_forced_key_binding(key, "kino-panel-" .. key, function() panel_key(what) end, { repeatable = true })
    end
    P.idle = mp.add_timeout(10, panel_close)
    P.tick = mp.add_periodic_timer(1, draw_panel) -- the end time, the chapter playing
    draw_panel()
end
mp.add_key_binding(nil, "chapters", function()
    mp.commandv("script-message", "kino-active")
    panel_open("chapters", true)
end)
mp.observe_property("pause", "bool", function(_, paused)
    if not paused and scrub_to then scrub_done() end -- played on (space, the remote's play key): there first
    if paused then show_bar() elseif bar_timer then bar_until = mp.get_time() + 2 end
end)

local function fade_quit()
    if quitting then -- pressed again while it fades: at once
        mp.command("quit")
        return
    end
    hide_bar()
    panel_close()
    quitting = true
    fade(0, 1, 0.4, function() mp.command("quit") end)
end
mp.register_script_message("kino-quit", fade_quit)

mp.register_event("start-file", function()
    if quitting then return end -- going into the black to quit: that fade ends the player, it is not cut short
    fade_pending = true
    if curtain_timer then curtain_timer:kill() curtain_timer = nil end
    draw_curtain(1) -- the next episode too comes out of the black
end)
mp.register_event("playback-restart", function() -- also after every seek: only the first picture fades in
    if fade_pending and not quitting then
        fade_pending = false
        fade(1, 0, 0.6)
    end
end)

-- Back key: close an open selection menu, otherwise stop playback
-- Every key counts as someone being there (see "Still watching?" in the backend); the other keys send
-- kino-active from input.conf
mp.add_key_binding(nil, "back", function()
    mp.commandv("script-message", "kino-active")
    if mp.get_property_native("user-data/mpv/console/open") then
        mp.commandv("script-message-to", "console", "disable")
    elseif scrub_to then
        scrub_cancel()
    else
        fade_quit()
    end
end)

-- OK key: skip intro/recap, go to the next episode during the credits, pause otherwise; on a channel live it
-- shows what runs instead (pausing live television is no use). The Matinee backend puts whatever can be skipped right
-- now into user-data/kino/skip, and what runs on the channel into user-data/kino/info.
mp.add_key_binding(nil, "ok", function()
    mp.commandv("script-message", "kino-active")
    local skip = mp.get_property_native("user-data/kino/skip")
    if scrub_to then -- a seek being chosen: there, playing on
        scrub_done()
        mp.set_property_native("pause", false)
    elseif type(skip) == "table" then
        mp.set_property("user-data/kino/skip", "")
        if skip.to == "next" then
            mp.commandv("script-message", "kino-next")
        else
            mp.commandv("seek", tostring(skip.to), "absolute", "exact")
            mp.osd_message("")
        end
    elseif type(mp.get_property_native("user-data/kino/info")) == "string" then
        mp.osd_message(mp.get_property_native("user-data/kino/info"), 6)
    else
        mp.command("cycle pause")
        show_bar()
    end
end)

-- Up and down: louder and quieter at once, held on and on (a step every 0.2 s, not at the key's own repeat rate).
-- The backend changes it (kino-volume: the sound of the whole device on the TV computer, a Bluetooth speaker too)
-- and shows it. On a channel live (user-data/kino/live) the next channel and the one before instead, like a TV's
-- channel keys (the order of the live row); there Menu gives the volume (see below)
local function live() return mp.get_property_native("user-data/kino/live") == true end
local last_step = 0
local function up_down(step, zap)
    return function(t)
        mp.commandv("script-message", "kino-active")
        if live() then
            if t.event == "down" or t.event == "press" then mp.commandv("script-message", zap) end
        elseif t.event ~= "up" and (t.event ~= "repeat" or mp.get_time() - last_step >= 0.2) then
            last_step = mp.get_time()
            mp.commandv("script-message", "kino-volume", tostring(step))
        end
    end
end
mp.add_key_binding(nil, "up", up_down(5, "kino-zap-next"), { complex = true, repeatable = true })
mp.add_key_binding(nil, "down", up_down(-5, "kino-zap-prev"), { complex = true, repeatable = true })

-- Play/pause key (a Fire TV remote's arrives as PLAY): pause or play; held for a moment, the sleep timer
-- instead (30, 60, 90 minutes, at the end, off: the backend shows which). Keys that only report a press pause.
local play_down = nil
mp.add_key_binding(nil, "play", function(t)
    mp.commandv("script-message", "kino-active")
    if t.event == "down" then
        play_down = mp.get_time()
    elseif t.event == "up" or t.event == "press" then
        if play_down and mp.get_time() - play_down >= 0.7 then
            mp.commandv("script-message", "kino-sleep")
        elseif scrub_to then
            scrub_done()
            mp.set_property_native("pause", false)
        else
            mp.command("cycle pause")
            show_bar()
        end
        play_down = nil
    end
end, { complex = true })

-- The volume on a channel live, where up and down change the channel (Menu there, see "menu"). For three seconds
-- after the last press up and right make it louder, down and left quieter, Back or Menu end it. The backend changes it (kino-volume, the sound of the whole device, a Bluetooth
-- speaker too) and shows it; 0 only shows it.
local VOLUME_KEYS = { UP = 5, RIGHT = 5, KP_UP = 5, KP_RIGHT = 5, DOWN = -5, LEFT = -5, KP_DOWN = -5, KP_LEFT = -5 }
local VOLUME_CLOSE = { "MENU", "TAB", "ESC", "BS", "GO_BACK", "MBTN_BACK", "CANCEL", "ENTER", "KP_ENTER" }
local volume_timer = nil
local function volume_end()
    if not volume_timer then return end
    volume_timer:kill()
    volume_timer = nil
    for key in pairs(VOLUME_KEYS) do mp.remove_key_binding("kino-volume-" .. key) end
    for _, key in ipairs(VOLUME_CLOSE) do mp.remove_key_binding("kino-volume-" .. key) end
    mp.osd_message("")
end
local function volume_step(step)
    mp.commandv("script-message", "kino-active")
    mp.commandv("script-message", "kino-volume", tostring(step))
    if volume_timer then volume_timer:kill() end
    volume_timer = mp.add_timeout(3, volume_end)
end
local function volume_open()
    for key, step in pairs(VOLUME_KEYS) do
        mp.add_forced_key_binding(key, "kino-volume-" .. key, function() volume_step(step) end, { repeatable = true })
    end
    for _, key in ipairs(VOLUME_CLOSE) do mp.add_forced_key_binding(key, "kino-volume-" .. key, volume_end) end
    volume_step(0)
end
mp.add_key_binding(nil, "volume", volume_open)

-- Menu key: the info panel (info, chapters, audio, subtitles, speed); on a channel live the volume
mp.add_key_binding(nil, "menu", function()
    mp.commandv("script-message", "kino-active")
    if live() then volume_open() else panel_open("info") end
end)

-- A mouse or trackpad (a Mac's): moving it shows the bar, and the pointer for a second (mpv.conf); over the bar's line
-- the time under it, with its picture; a click pauses and plays, on the bar's line it goes there, on an offer at the
-- bottom right (skip the intro, the next episode) it takes it; a double click switches full screen (input.conf); a right click opens the
-- info panel (on a channel live the volume) and closes it again. In the panel the pointer picks the row it is over, a
-- click takes it, a click on a tab shows that one and a click beside the panel closes it. The wheel is louder and
-- quieter (the panel: up and down), sideways (a trackpad's swipe) it seeks as left and right do: one step every
-- WHEEL_STEP at most, however many a trackpad sends.
local WHEEL_STEP = 0.12
local function pointer() -- where the mouse is, in the overlays' 1920 × 1080
    local m = mp.get_property_native("mouse-pos")
    local w, h = mp.get_property_number("osd-width", 0), mp.get_property_number("osd-height", 0)
    if type(m) ~= "table" or w <= 0 or h <= 0 then return nil end
    return m.x * W / w, m.y * 1080 / h
end

-- what of the open panel is at x, y: "tab", "row" (with which) or "in"; nil beside it
local function panel_at(x, y)
    local tabs = panel_tabs()
    if y >= PY + 34 and y <= PY + 86 then
        local tx = PX + 40
        for i, t in ipairs(tabs) do
            local w = chars(t.name) * 15 + 52
            if x >= tx and x <= tx + w then return "tab", i end
            tx = tx + w + 4
        end
    end
    local id = tabs[math.max(1, math.min(P.tab, #tabs))].id
    if id ~= "info" and x >= PX + 24 and x <= PX + 24 + LIST_W and y >= PY + 130 then
        local i = P.top + math.floor((y - PY - 130) / ROW_H)
        if i < P.top + ROWS and i <= #panel_rows(id) then return "row", i end
    end
    if x >= PX and x <= PX + PW and y >= PY and y <= (P.bottom or PY) then return "in" end
    return nil
end

local function panel_still() -- someone is there: the panel stays its 10 s from now
    mp.commandv("script-message", "kino-active")
    if P.idle then P.idle:kill() end
    P.idle = mp.add_timeout(10, panel_close)
end

-- a double click sends its two clicks first: one of them on the panel (a row picked, the panel closed by it), the
-- rest of the double click is not for the film (no pause, no full screen)
local panel_clicked = -1
mp.add_key_binding(nil, "click", function()
    mp.commandv("script-message", "kino-active")
    if quitting then return end
    local x, y = pointer()
    if panel_shown then
        panel_clicked = mp.get_time()
        local what, i
        if x then what, i = panel_at(x, y) end
        if what == "tab" then
            panel_still()
            P.tab, P.row, P.top, P.find = i, 0, 1, false
            draw_panel()
        elseif what == "row" then
            P.row = i
            panel_key("ok")
        elseif not what then
            panel_close()
        end
        return
    end
    -- the offer at the bottom right (skip the intro, the next episode, "Still watching?"): taken as with OK
    if x and type(mp.get_property_native("user-data/kino/skip")) == "table" and x >= W / 2 and y >= 640 and y <= 860 then
        mp.commandv("script-binding", "kino/ok")
        return
    end
    local dur = mp.get_property_number("duration")
    if x and bar_timer and not live() and dur and dur > 0 and y >= 930 and y <= 1000 and x >= EDGE - 20 and x <= W - EDGE + 20 then
        if scrub_to then scrub_cancel() end -- on the line: there, wherever the keys' mark was
        mp.commandv("seek", tostring(math.max(0, math.min(dur - 1, (x - EDGE) / (W - 2 * EDGE) * dur))), "absolute")
        show_bar()
        return
    end
    if mp.get_time() - panel_clicked < 0.6 then return end -- the second click of one on the panel
    mp.command("cycle pause") -- played on while seeking with the keys: there first (see "pause" above)
    show_bar()
end)
mp.add_key_binding(nil, "dblclick", function()
    mp.commandv("script-message", "kino-active")
    if panel_shown or mp.get_time() - panel_clicked < 0.6 then return end
    mp.command("cycle fullscreen")
end)

local wheel_at = 0
local function wheel(dir)
    return function()
        mp.commandv("script-message", "kino-active")
        local now = mp.get_time()
        if quitting or now - wheel_at < WHEEL_STEP then return end
        wheel_at = now
        if panel_shown then
            if dir == "up" and P.row == 0 then return end -- not out of it: closed only on purpose
            return panel_key(dir)
        end
        if dir == "up" or dir == "down" then
            local louder = (dir == "up") ~= opts.natural
            mp.commandv("script-message", "kino-volume", louder and "5" or "-5")
        else
            scrub(dir == "left" and -10 or 10)
        end
    end
end
for _, dir in ipairs({ "up", "down", "left", "right" }) do mp.add_key_binding(nil, "wheel-" .. dir, wheel(dir)) end

-- the mouse moved: the bar (in the panel: the row under it)
local moved_from = nil
mp.observe_property("mouse-pos", "native", function(_, m)
    if type(m) ~= "table" then return end
    if not m.hover then -- out of the window: no time or picture of where it was (paused, the bar stays)
        if hover_to then
            hover_end()
            draw_bar()
        end
        return
    end
    local at = m.x .. "," .. m.y
    if at == moved_from then return end
    local first = moved_from == nil
    moved_from = at
    if first or quitting then return end -- where it was when the player opened: not moved
    if panel_shown then
        local x, y = pointer()
        local what, i = panel_at(x or -1, y or -1)
        if what == "row" and i ~= P.row then
            P.row = i
            panel_still()
            draw_panel()
        end
        return
    end
    show_bar(2)
    -- over the bar's line: the time there and its picture
    local x, y = pointer()
    local dur = mp.get_property_number("duration")
    local to = nil
    if x and not live() and dur and dur > 0 and y >= 930 and y <= 1000 and x >= EDGE - 20 and x <= W - EDGE + 20 then
        to = math.max(0, math.min(dur - 1, (x - EDGE) / (W - 2 * EDGE) * dur))
    end
    if to == hover_to then return end
    hover_to = to
    if to and not scrub_to then
        preview_show()
    elseif not scrub_to then
        preview_hide()
    end
    draw_bar()
end)
