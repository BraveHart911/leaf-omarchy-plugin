-- Optional Leaf gestures for Hyprland. Loaded from the user's input.lua.
local config = os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config")
local script = config .. "/omarchy/plugins/io.github.bravehart911.leaf-control/gestures.py"
local quoted = "'" .. script:gsub("'", "'\\''") .. "'"
for _, direction in ipairs({ "up", "down", "left", "right" }) do
    local command = "python3 " .. quoted .. " " .. direction
    hl.gesture({ fingers = 3, direction = direction, action = function() hl.exec_cmd(command) end })
end
