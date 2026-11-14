-- 르니톡 (SoopBridge): 와우 안 작은 창에서 친 말을 화면 신호로 PC 프로그램에 넘기면,
-- 프로그램이 크롬 확장을 통해 숲 방송 채팅으로 보낸다.
BINDING_HEADER_SOOPBRIDGE = "르니톡"
BINDING_NAME_SOOPBRIDGE_FOCUS = "르니톡 입력하기"
BINDING_NAME_SOOPBRIDGE_TOGGLE = "르니톡 창 열기/닫기"

local DEFAULTS = { shown = true, point = { "BOTTOMLEFT", "UIParent", "BOTTOMLEFT", 20, 260 }, w = 320, h = 150,
    btnShown = true, btnPoint = { "CENTER", "UIParent", "CENTER", -360, -120 } }
local ICON = "Interface\\AddOns\\SoopBridge\\leuni"
local db, frame, history, input, floatBtn, titleText, targetBtn

-- ===== 화면 신호(픽셀) 전송 =====
-- 와우 채팅 기록 파일은 실시간으로 쓰이지 않아서, 보낼 말을 화면 왼쪽 위 작은 색 블록으로 잠깐 그리고
-- PC 프로그램이 그 색을 읽어 숲 채팅으로 넘긴다. 블록은 보낼 때만 0.6초 정도 보였다 사라진다.
local CELL, COLS, MAXROWS = 8, 128, 6
local LV = { 0, 85 / 255, 170 / 255, 1 }
local strip, cells, seq, outbox, busy = nil, {}, 0, {}, false

local function toSymbols(str)
    local syms, acc, nbits = {}, 0, 0
    for i = 1, #str do
        acc = acc * 256 + str:byte(i); nbits = nbits + 8
        while nbits >= 6 do
            nbits = nbits - 6
            syms[#syms + 1] = math.floor(acc / 2 ^ nbits) % 64
            acc = acc % (2 ^ nbits)
        end
    end
    if nbits > 0 then syms[#syms + 1] = (acc * 2 ^ (6 - nbits)) % 64 end
    return syms
end

local function ensureStrip()
    if strip then return end
    strip = CreateFrame("Frame", "SoopBridgeStrip", UIParent)
    strip:SetFrameStrata("TOOLTIP"); strip:SetFrameLevel(9999)
    strip:EnableMouse(false)
    strip:Hide()
end

local function layoutStrip()
    strip:SetScale(1 / UIParent:GetEffectiveScale())
    strip:ClearAllPoints()
    strip:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
    strip:SetSize(COLS * CELL, MAXROWS * CELL)
end

local function draw(text)
    ensureStrip(); layoutStrip()
    seq = (seq + 1) % 64
    local len, sum = #text, 0
    for i = 1, len do sum = sum + text:byte(i) end
    local syms = { 63, 0, 63, 0, seq, math.floor(len / 64) % 64, len % 64 }
    for _, v in ipairs(toSymbols(text)) do syms[#syms + 1] = v end
    sum = sum % 4096
    syms[#syms + 1] = math.floor(sum / 64); syms[#syms + 1] = sum % 64
    for i = 1, COLS * MAXROWS do
        local t = cells[i]
        local v = syms[i]
        if v then
            if not t then
                t = strip:CreateTexture(nil, "OVERLAY")
                t:SetSize(CELL, CELL)
                t:SetPoint("TOPLEFT", strip, "TOPLEFT", ((i - 1) % COLS) * CELL, -CELL * math.floor((i - 1) / COLS))
                cells[i] = t
            end
            t:SetColorTexture(LV[math.floor(v / 16) % 4 + 1], LV[math.floor(v / 4) % 4 + 1], LV[v % 4 + 1], 1)
            t:Show()
        elseif t then
            t:Hide()
        end
    end
    strip:Show()
end

local function pump()
    if busy then return end
    local text = table.remove(outbox, 1)
    if not text then return end
    busy = true
    draw(text)
    C_Timer.After(0.6, function()
        strip:Hide()
        C_Timer.After(0.15, function() busy = false; pump() end)
    end)
end

local function sendControl(text)
    outbox[#outbox + 1] = text
    pump()
end

local function send(text)
    text = strtrim(text or "")
    if text == "" then return end
    -- UTF-8 글자가 잘리지 않게 최대 약 100글자까지만
    if #text > 300 then text = text:sub(1, 300):gsub("[\128-\191]*$", ""):gsub("[\192-\255]$", "") end
    outbox[#outbox + 1] = text
    if history then history:AddMessage("|cff999999" .. date("%H:%M") .. "|r " .. text) end
    pump()
    return true
end

-- ===== 연결한 방송 표시 =====
-- 와우 애드온은 실행 중에 PC 프로그램한테서 정보를 받을 수 없어서,
-- 와우에서 바꾸면 바로 표시하고, 프로그램에서 바꾼 건 다음 접속//reload 때 Target.lua 로 받아온다.
local function targetName(t)
    if not t or not t.id or t.id == "" then return nil end
    if t.nick and t.nick ~= "" then return t.nick end
    return t.id
end

local function updateTitle()
    if not titleText then return end
    local t = db.target
    local name = targetName(t)
    local s = "|cffc39bf5르니톡|r  "
    if not name then
        s = s .. "|cffff9933방송 선택 (클릭)|r"
    elseif t.pending then
        s = s .. "|cffffffff" .. name .. "|r |cff888888방송 연결 요청함|r"
    else
        s = s .. "|cffffffff" .. name .. "|r |cff888888방송|r"
    end
    titleText:SetText(s)
    if targetBtn then targetBtn:SetWidth(titleText:GetStringWidth() + 4) end
end

local function requestTarget(q)
    q = strtrim(q or "")
    if q == "" then return end
    -- 주소를 붙여넣었으면 아이디만
    local id = q:match("sooplive%.co%.kr/([%w_]+)") or q:match("sooplive%.com/([%w_]+)") or q:match("afreecatv%.com/([%w_]+)")
    if id == "station" then id = q:match("/station/([%w_]+)") end
    local show = id or q
    db.target = { id = show, nick = id and "" or q, t = time(), pending = true }
    sendControl("\001T" .. q)
    if history then history:AddMessage("|cffc39bf5방송 연결 요청:|r " .. show .. " |cff888888(PC 프로그램 창에서 결과 확인)|r") end
    updateTitle()
end

StaticPopupDialogs["SOOPBRIDGE_TARGET"] = {
    text = "어느 방송에 채팅을 보낼까요?\n|cff888888닉네임, 숲 아이디, 방송 주소 다 돼요 (예: 르니, leuni158)|r",
    button1 = "연결", button2 = "취소",
    hasEditBox = true, maxLetters = 120,
    OnShow = function(self)
        local eb = self.editBox or (self.GetEditBox and self:GetEditBox())
        if eb then eb:SetText(targetName(db.target) or ""); eb:HighlightText(); eb:SetFocus() end
    end,
    OnAccept = function(self)
        local eb = self.editBox or (self.GetEditBox and self:GetEditBox())
        if eb then requestTarget(eb:GetText()) end
    end,
    EditBoxOnEnterPressed = function(self)
        requestTarget(self:GetText()); self:GetParent():Hide()
    end,
    EditBoxOnEscapePressed = function(self) self:GetParent():Hide() end,
    timeout = 0, whileDead = true, hideOnEscape = true, preferredIndex = 3,
}

function SoopBridge_ChooseTarget()
    StaticPopup_Show("SOOPBRIDGE_TARGET")
end

local function savePos()
    local p, _, rp, x, y = frame:GetPoint()
    db.point = { p, "UIParent", rp, x, y }
    db.w, db.h = frame:GetSize()
end

local function build()
    frame = CreateFrame("Frame", "SoopBridgeFrame", UIParent, "BackdropTemplate")
    frame:SetSize(db.w, db.h)
    frame:SetPoint(unpack(db.point))
    frame:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8x8", edgeFile = "Interface\\Buttons\\WHITE8x8", edgeSize = 1 })
    frame:SetBackdropColor(0, 0, 0, 0.55)
    frame:SetBackdropBorderColor(0.67, 0.43, 0.94, 0.9)
    frame:SetMovable(true); frame:SetResizable(true); frame:SetClampedToScreen(true)
    if frame.SetResizeBounds then frame:SetResizeBounds(200, 90, 700, 500) end
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", function(self) self:StopMovingOrSizing(); savePos() end)

    local icon = frame:CreateTexture(nil, "OVERLAY")
    icon:SetSize(16, 16); icon:SetPoint("TOPLEFT", 5, -3)
    icon:SetTexture(ICON)
    -- 제목줄: 르니톡 · 연결한 방송 (클릭하면 방송 바꾸기)
    targetBtn = CreateFrame("Button", nil, frame)
    targetBtn:SetPoint("LEFT", icon, "RIGHT", 2, 0)
    targetBtn:SetHeight(16)
    targetBtn:RegisterForDrag("LeftButton")
    targetBtn:SetScript("OnDragStart", function() frame:StartMoving() end)
    targetBtn:SetScript("OnDragStop", function() frame:StopMovingOrSizing(); savePos() end)
    titleText = targetBtn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    titleText:SetPoint("LEFT", 2, 0)
    targetBtn:SetScript("OnClick", SoopBridge_ChooseTarget)
    targetBtn:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:AddLine("|cffc39bf5채팅을 보낼 방송|r")
        GameTooltip:AddLine("클릭: 방송 바꾸기 (닉네임/아이디/주소)", 1, 1, 1)
        GameTooltip:AddLine("드래그: 창 이동", 0.7, 0.7, 0.7)
        if db.target and db.target.id and db.target.id ~= "" and db.target.nick ~= "" and not db.target.pending then
            GameTooltip:AddLine("숲 아이디: " .. db.target.id, 0.7, 0.7, 0.7)
        end
        GameTooltip:AddLine("PC 프로그램에서 바꾼 방송은 /reload 하면 여기 반영돼요", 0.6, 0.6, 0.6, true)
        GameTooltip:Show()
    end)
    targetBtn:SetScript("OnLeave", function() GameTooltip:Hide() end)
    updateTitle()

    local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
    close:SetSize(20, 20); close:SetPoint("TOPRIGHT", 1, 1)
    close:SetScript("OnClick", function() SoopBridge_Toggle() end)

    -- 사용법 버튼 (마우스를 올리면 설명)
    local help = CreateFrame("Button", nil, frame)
    help:SetSize(16, 16); help:SetPoint("RIGHT", close, "LEFT", -2, 0)
    local q = help:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    q:SetPoint("CENTER"); q:SetText("|cffc39bf5?|r")
    help:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine("|cffc39bf5르니톡 사용법|r")
        GameTooltip:AddLine("|cffffffff/l 할말|r  (한글 자판이면 |cffffffff/ㅣ 할말|r)  → 바로 방송 채팅 전송", 0.9, 0.9, 0.9)
        GameTooltip:AddLine("|cffffffff/l|r  → 입력칸으로 바로 이동", 0.9, 0.9, 0.9)
        GameTooltip:AddLine("떠 있는 르니 아이콘 우클릭 → 바로 입력", 0.9, 0.9, 0.9)
        GameTooltip:AddLine("단축키: ESC > 설정 > 단축키 > 애드온 > 르니톡", 0.9, 0.9, 0.9)
        GameTooltip:AddLine("제목줄 클릭 또는 |cffffffff/르니톡 방송 닉네임|r → 보낼 방송 바꾸기", 0.9, 0.9, 0.9)
        GameTooltip:AddLine("/르니톡 아이콘 → 떠 있는 아이콘 숨기기/보이기", 0.7, 0.7, 0.7)
        GameTooltip:Show()
    end)
    help:SetScript("OnLeave", function() GameTooltip:Hide() end)

    local grip = CreateFrame("Button", nil, frame)
    grip:SetSize(14, 14); grip:SetPoint("BOTTOMRIGHT", -1, 1)
    grip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
    grip:SetScript("OnMouseDown", function() frame:StartSizing("BOTTOMRIGHT") end)
    grip:SetScript("OnMouseUp", function() frame:StopMovingOrSizing(); savePos() end)

    input = CreateFrame("EditBox", nil, frame, "BackdropTemplate")
    input:SetPoint("BOTTOMLEFT", 4, 4); input:SetPoint("BOTTOMRIGHT", -18, 4)
    input:SetHeight(22)
    input:SetFontObject(ChatFontNormal)
    input:SetTextInsets(6, 6, 0, 0)
    input:SetAutoFocus(false)
    input:SetMaxLetters(100)
    input:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8x8", edgeFile = "Interface\\Buttons\\WHITE8x8", edgeSize = 1 })
    input:SetBackdropColor(0.1, 0.1, 0.1, 0.8)
    input:SetBackdropBorderColor(0.4, 0.4, 0.4, 0.8)
    input:SetScript("OnEnterPressed", function(self)
        if send(self:GetText()) ~= false then self:SetText("") end
        self:ClearFocus()
    end)
    input:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
    input:SetScript("OnEditFocusGained", function(self) self:SetBackdropBorderColor(0.67, 0.43, 0.94, 1) end)
    input:SetScript("OnEditFocusLost", function(self) self:SetBackdropBorderColor(0.4, 0.4, 0.4, 0.8) end)

    local hint = input:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    hint:SetPoint("LEFT", 6, 0)
    hint:SetText("여기를 클릭하고 입력 → 엔터로 방송 채팅 전송")
    input:SetScript("OnTextChanged", function(self) hint:SetShown(self:GetText() == "" and not self:HasFocus()) end)
    input:HookScript("OnEditFocusGained", function() hint:Hide() end)
    input:HookScript("OnEditFocusLost", function(self) hint:SetShown(self:GetText() == "") end)

    history = CreateFrame("ScrollingMessageFrame", nil, frame)
    history:SetPoint("TOPLEFT", 6, -22); history:SetPoint("BOTTOMRIGHT", input, "TOPRIGHT", 12, 4)
    history:SetFontObject(ChatFontNormal)
    history:SetJustifyH("LEFT")
    history:SetFading(false)
    history:SetMaxLines(100)
    history:EnableMouseWheel(true)
    history:SetScript("OnMouseWheel", function(self, d) if d > 0 then self:ScrollUp() else self:ScrollDown() end end)
    history:AddMessage("|cff888888사용법: 채팅창에 |cffffffff/l 할말|cff888888 (또는 |cffffffff/ㅣ|cff888888) → 바로 방송 채팅 전송 (자세히는 ? 버튼)|r")

    frame:SetShown(db.shown)

    -- ===== 떠 있는 르니 아이콘 (클릭: 창 열기/닫기, 드래그: 이동, 우클릭: 바로 입력) =====
    local btn = CreateFrame("Button", "SoopBridgeFloatButton", UIParent)
    btn:SetSize(40, 40)
    btn:SetPoint(unpack(db.btnPoint))
    btn:SetFrameStrata("MEDIUM")
    btn:SetMovable(true); btn:SetClampedToScreen(true)
    btn:EnableMouse(true)
    btn:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    btn:RegisterForDrag("LeftButton")
    local tex = btn:CreateTexture(nil, "ARTWORK"); tex:SetAllPoints(); tex:SetTexture(ICON)
    local hl = btn:CreateTexture(nil, "HIGHLIGHT"); hl:SetAllPoints(); hl:SetTexture(ICON); hl:SetBlendMode("ADD"); hl:SetAlpha(0.35)
    btn:SetScript("OnDragStart", function(self) self.dragging = true; self:StartMoving() end)
    btn:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
        local p, _, rp, x, y = self:GetPoint()
        db.btnPoint = { p, "UIParent", rp, x, y }
        C_Timer.After(0, function() self.dragging = false end)
    end)
    btn:SetScript("OnClick", function(self, button)
        if self.dragging then return end
        if button == "RightButton" then SoopBridge_Focus() else SoopBridge_Toggle() end
    end)
    btn:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine("|cffc39bf5르니톡|r")
        GameTooltip:AddLine("클릭: 창 열기/닫기", 1, 1, 1)
        GameTooltip:AddLine("우클릭: 바로 입력", 1, 1, 1)
        GameTooltip:AddLine("드래그: 아이콘 옮기기", 1, 1, 1)
        GameTooltip:Show()
    end)
    btn:SetScript("OnLeave", function() GameTooltip:Hide() end)
    btn:SetShown(db.btnShown)
    floatBtn = btn
end

function SoopBridge_Toggle()
    if not frame then return end
    db.shown = not frame:IsShown()
    frame:SetShown(db.shown)
end

function SoopBridge_Focus()
    if not frame then return end
    if not frame:IsShown() then db.shown = true; frame:Show() end
    -- 단축키 글자가 입력칸에 들어가지 않게 다음 프레임에 포커스
    C_Timer.After(0, function() input:SetFocus() end)
end

-- ===== 새 버전 알림 (PC 프로그램이 확인해서 Target.lua 에 적어 줌) =====
local function verNum(v)
    local a, b, c = tostring(v or ""):match("(%d+)%.?(%d*)%.?(%d*)")
    return (tonumber(a) or 0) * 1000000 + (tonumber(b) or 0) * 1000 + (tonumber(c) or 0)
end

local function checkUpdate()
    local info = SoopBridge_AppInfo
    if type(info) ~= "table" or not info.latest or info.latest == "" then return end
    if verNum(info.latest) <= verNum(info.version) then return end
    local msg = "|cffc39bf5르니톡|r 새 버전 |cffffffffv" .. info.latest .. "|r 이 나왔어요! PC의 르니톡 프로그램에서 |cffffd100업데이트|r 버튼을 눌러 주세요."
    DEFAULT_CHAT_FRAME:AddMessage(msg)
    if history then history:AddMessage(msg) end
end

local f = CreateFrame("Frame")
f:RegisterEvent("ADDON_LOADED")
f:RegisterEvent("PLAYER_LOGIN")

f:SetScript("OnEvent", function(_, event, ...)
    if event == "PLAYER_LOGIN" then
        C_Timer.After(5, checkUpdate)  -- 접속 메시지들 사이에 묻히지 않게 조금 뒤에
        return
    end
    if event == "ADDON_LOADED" then
        if ... ~= "SoopBridge" then return end
        SoopBridgeDB = SoopBridgeDB or {}
        for k, v in pairs(DEFAULTS) do if SoopBridgeDB[k] == nil then SoopBridgeDB[k] = v end end
        db = SoopBridgeDB
        -- PC 프로그램이 마지막으로 연결한 방송 (프로그램에서 바꾼 게 더 최근이면 그걸로)
        local app = SoopBridge_AppTarget
        if type(app) == "table" and app.id and app.id ~= "" then
            local mine = db.target
            if not mine or not mine.id or (app.t or 0) >= (mine.t or 0) or mine.pending then
                db.target = { id = app.id, nick = app.nick or "", t = app.t or 0 }
            end
        end
        build()
    end
end)

-- 빠른 전송: /l 할말 (한글 자판 그대로 /ㅣ 도 됨, 뒤에 아무것도 없으면 입력칸으로 이동)
SLASH_LEUNITALKQUICK1, SLASH_LEUNITALKQUICK2 = "/l", "/ㅣ"
SlashCmdList.LEUNITALKQUICK = function(msg)
    msg = strtrim(msg or "")
    if msg == "" then SoopBridge_Focus() else send(msg) end
end

SLASH_SOOPBRIDGE1, SLASH_SOOPBRIDGE2, SLASH_SOOPBRIDGE3 = "/숲", "/르니톡", "/soop"
SlashCmdList.SOOPBRIDGE = function(msg)
    msg = strtrim(msg or "")
    if msg == "" then
        SoopBridge_Toggle()
    elseif msg == "방송" or msg == "연결" then
        SoopBridge_ChooseTarget()
    elseif msg:match("^방송%s") or msg:match("^연결%s") then
        requestTarget((msg:gsub("^%S+%s+", "")))
    elseif msg == "아이콘" or msg == "icon" then
        db.btnShown = not db.btnShown
        if floatBtn then floatBtn:SetShown(db.btnShown) end
    else
        send(msg)
    end
end
