import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Wayland
import Quickshell.Io
import qs.Commons as Commons

Item {
  id: root
  property var shell: null
  property var manifest: null
  readonly property var appLibrary: shell ? shell.appLibrary : null
  property bool opened: false
  property bool windowDragActive: false
  property int keyboardSelection: 0
  property bool livePreviews: true
  property bool otherMonitorApps: true
  property bool switchBothMonitors: false
  property var hostScreen: null
  property string targetMonitorName: ""
  property var wallpapers: ({})
  property var spaces: []
  property var windows: []
  property string spacesSignature: ""
  property string windowsSignature: ""
  property bool pendingOpen: false
  property int ownerPid: 0
  property int stripTop: 120
  property int stripHeight: 180
  property var workspaceSets: ({})
  property bool ownerSeen: false
  property int ownerMisses: 0
  property int pendingMoveWorkspace: 0
  property int moveFeedbackWorkspace: 0
  readonly property color background: Commons.Color.menu.background
  readonly property color foreground: Commons.Color.menu.text
  readonly property color accent: Commons.Color.accent

  function open(payload) {
    let data = ({})
    try { data = JSON.parse(payload || "{}") || ({}) } catch (error) {}
    if (!data.pid) {
      const launcher = decodeURIComponent(String(Qt.resolvedUrl("launch-leaf")).replace(/^file:\/\//, ""))
      Quickshell.execDetached(["sh", launcher])
      return
    }
    root.stripTop = Math.max(0, Number(data.top || 120))
    root.stripHeight = Math.max(100, Number(data.height || 180))
    root.workspaceSets = data.workspaceSets || ({})
    const requested = String(data.monitor || "")
    const nextOwnerPid = Number(data.pid || 0)
    if (root.ownerPid !== nextOwnerPid) {
      root.ownerSeen = false
      root.ownerMisses = 0
    }
    root.ownerPid = nextOwnerPid
    const name = requested || (Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : "")
    const nextScreen = (Quickshell.screens || []).find(s => s.name === name) || (Quickshell.screens || [])[0]
    const screenChanged = root.opened && root.hostScreen && nextScreen
      && root.hostScreen.name !== nextScreen.name
    if (screenChanged) root.opened = false
    root.keyboardSelection = Number(data.selected || 0)
    root.livePreviews = data.livePreviews !== false
    root.otherMonitorApps = data.otherMonitorApps !== false
    root.switchBothMonitors = data.switchBothMonitors === true
    root.targetMonitorName = name
    root.wallpapers = data.wallpapers && typeof data.wallpapers === "object" ? data.wallpapers : ({})
    root.hostScreen = nextScreen
    Hyprland.refreshMonitors()
    Hyprland.refreshWorkspaces()
    Hyprland.refreshToplevels()
    root.refresh()
    if (screenChanged) {
      root.pendingOpen = true
      screenMoveTimer.restart()
    } else {
      root.opened = true
    }
  }

  function keyboardState() {
    return JSON.stringify({selected: root.keyboardSelection, opened: root.opened, monitor: root.targetMonitorName})
  }

  function close() {
    screenMoveTimer.stop()
    root.pendingOpen = false
    root.opened = false
  }
  function dismiss() {
    if (root.shell && typeof root.shell.hide === "function") root.shell.hide("io.github.bravehart911.leaf-control")
    else root.close()
  }
  function selectTarget(kind, value) {
    const monitor = root.targetMonitorName
      || (Hyprland.focusedMonitor ? String(Hyprland.focusedMonitor.name || "") : "")
    const helper = decodeURIComponent(String(Qt.resolvedUrl("select_workspace.py")).replace(/^file:\/\//, ""))
    const mode = kind === "workspace" && root.switchBothMonitors ? "both" : "one"
    Quickshell.execDetached(["python3", helper, kind, String(value), monitor, mode])
    root.dismiss()
  }
  function moveWindows(payload, number) {
    try {
      const data = JSON.parse(payload)
      if (!Array.isArray(data.windows) || !data.windows.length
          || !data.windows.every(a => /^0x[0-9a-f]+$/i.test(a))
          || moveProcess.running) return false
      const helper = decodeURIComponent(String(Qt.resolvedUrl("move_windows.py")).replace(/^file:\/\//, ""))
      root.pendingMoveWorkspace = Number(number)
      moveProcess.command = ["python3", helper, JSON.stringify(data), String(number), root.targetMonitorName]
      moveProcess.running = true
      return true
    } catch (error) { return false }
  }

  function isLeafWindowDrag(dragEvent) {
    return dragEvent && dragEvent.formats
      && dragEvent.formats.indexOf("application/x-leaf-windows") !== -1
  }

  function focusWorkspace(id) {
    const number = Number(id)
    if (!Number.isInteger(number) || number < 1 || number > (root.workspaceSets[root.targetMonitorName] || []).length) return
    root.selectTarget("workspace", number)
  }
  function focusWindow(address) {
    const addressHex = String(address).replace(/^0x/, "")
    if (!/^[A-Fa-f0-9]+$/.test(addressHex)) return
    root.selectTarget("window", addressHex)
  }
  function iconFor(appClass) {
    let icon = "application-x-executable"
    try {
      const appName = String(appClass || "").toLowerCase()
      let entry = DesktopEntries.heuristicLookup(appName)
      if (!entry && appName) entry = (DesktopEntries.applications.values || []).find(e =>
        String(e.name || "").toLowerCase().includes(appName)
        || String(e.id || "").toLowerCase().includes(appName))
      if (entry && entry.icon) icon = String(entry.icon)
    } catch (error) {}
    if (root.appLibrary) return root.appLibrary.iconSource(icon)
    if (icon.charAt(0) === "/") return "file://" + icon
    return Quickshell.iconPath(icon, true)
  }
  function wallpaperUrl(path) {
    if (!path) return ""
    return "file://" + String(path).split("/").map(encodeURIComponent).join("/")
  }
  function appsForWorkspace(id) {
    const seen = new Set()
    const apps = []
    for (const window of root.windows) {
      if (window.workspace !== id) continue
      const key = String(window.appClass || window.address).toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      apps.push(window)
    }
    return apps
  }
  function windowAddressesForApp(id, appClass) {
    const key = String(appClass || "").toLowerCase()
    return root.windows.filter(window => window.workspace === id
      && String(window.appClass || window.address).toLowerCase() === key)
      .map(window => "0x" + String(window.address).replace(/^0x/, ""))
  }

  function refresh() {
    if (root.windowDragActive) return
    const monitors = (Hyprland.monitors.values || []).slice()
    const monitor = monitors.find(m => m.name === root.targetMonitorName)
      || Hyprland.focusedMonitor || monitors[0]
    if (!monitor) return
    const ids = root.workspaceSets[monitor.name] || []
    const tops = (Hyprland.toplevels.values || []).filter(t => t.workspace
      && t.workspace.id >= 1 && Number((t.lastIpcObject || {}).pid) !== root.ownerPid)
    const activeId = monitor.activeWorkspace ? monitor.activeWorkspace.id : ids[0]
    const spaces = ids.map((id, index) => {
      const wallpaper = String(root.wallpapers[String(id)] || "")
      return { id: id, monitor: monitor, label: String(index + 1),
        active: activeId === id, wallpaper: wallpaper }
    })
    const windows = []
    for (const top of tops) {
      const ipc = top.lastIpcObject || {}
      const at = ipc.at || [0, 0]
      const size = ipc.size || [0, 0]
      if (size[0] < 1 || size[1] < 1) continue
      windows.push({
        top: top, workspace: top.workspace.id, address: top.address,
        appClass: ipc.class || ipc.initialClass || "", title: top.title || ipc.class || "Window",
        x: at[0], y: at[1], w: size[0], h: size[1]
      })
    }
    const spacesSignature = JSON.stringify(spaces.map(s => [s.id, s.monitor.name, s.active, s.wallpaper]))
    const windowsSignature = JSON.stringify(windows.map(w => [w.address, w.workspace, w.appClass, w.x, w.y, w.w, w.h]))
    if (spacesSignature !== root.spacesSignature) {
      root.spacesSignature = spacesSignature
      root.spaces = spaces
    }
    if (windowsSignature !== root.windowsSignature) {
      root.windowsSignature = windowsSignature
      root.windows = windows
    }
  }

  // A separate surface must never survive its owning drawer, even after a crash.
  Process {
    id: ownerProbe
    command: ["python3",
      decodeURIComponent(String(Qt.resolvedUrl("owner_probe.py")).replace(/^file:\/\//, "")),
      String(root.ownerPid)]
    stdout: StdioCollector {
      onStreamFinished: {
        if (!root.opened) return
        const state = String(text || "").trim()
        if (state === "alive") {
          root.ownerSeen = true
          root.ownerMisses = 0
        } else if (state === "dead" && root.ownerSeen) {
          root.ownerMisses += 1
          if (root.ownerMisses >= 4) root.dismiss()
        }
      }
    }
  }
  Timer {
    interval: 250
    repeat: true
    running: root.opened
    onTriggered: { if (!ownerProbe.running) ownerProbe.running = true }
  }

  Timer {
    id: screenMoveTimer
    interval: 80
    onTriggered: {
      if (root.pendingOpen) root.opened = true
      root.pendingOpen = false
    }
  }

  Timer {
    interval: 400
    repeat: true
    running: root.opened
    onTriggered: {
      if (root.windowDragActive) return
      // Geometry is an IPC snapshot; reading it again does not update it.
      Hyprland.refreshToplevels()
      geometrySettleTimer.restart()
    }
  }

  Timer {
    id: geometrySettleTimer
    interval: 80
    onTriggered: root.refresh()
  }

  Timer {
    id: moveFeedbackTimer
    interval: 1500
    onTriggered: root.moveFeedbackWorkspace = 0
  }

  Process {
    id: moveProcess
    running: false
    stderr: StdioCollector { id: moveError; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.moveFeedbackWorkspace = root.pendingMoveWorkspace
        moveFeedbackTimer.restart()
        Hyprland.refreshToplevels()
        geometrySettleTimer.restart()
      } else {
        console.warn("Leaf workspace move failed:", String(moveError.text || "unknown error").trim())
      }
      root.pendingMoveWorkspace = 0
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    screen: root.hostScreen || Quickshell.screens[0]
    anchors { top: true; left: true; right: true }
    margins { top: root.stripTop }
    implicitHeight: root.stripHeight
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "leaf-workspaces"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    Row {
      id: strip
      readonly property real cardWidth: Math.min(184, Math.max(36,
        (panel.width - 48 - Math.max(0, root.spaces.length - 1) * spacing) / Math.max(1, root.spaces.length)))
      width: root.spaces.length * cardWidth + Math.max(0, root.spaces.length - 1) * spacing
      height: root.stripHeight
      spacing: 8
      anchors.centerIn: parent

      Repeater {
        model: root.spaces
        delegate: Item {
          id: card
          required property var modelData
          width: strip.cardWidth
          height: strip.height
          readonly property var monitor: modelData.monitor
          readonly property var monitorIpc: monitor.lastIpcObject || ({})
          readonly property bool rotated: Number(monitorIpc.transform || 0) % 2 === 1
          readonly property real logicalWidth: (rotated ? monitorIpc.height : monitorIpc.width) / (monitor.scale || 1) || monitor.width
          readonly property real logicalHeight: (rotated ? monitorIpc.width : monitorIpc.height) / (monitor.scale || 1) || monitor.height
          readonly property var contents: root.windows.filter(w => w.workspace === modelData.id)
          readonly property int number: Number(modelData.label)

          Text {
            id: numberLabel
            anchors.top: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            text: card.modelData.label
            color: card.modelData.active ? root.accent : root.foreground
            opacity: card.modelData.active || root.keyboardSelection === card.number ? 1 : 0.64
            font.pixelSize: 12
            font.bold: card.modelData.active || root.keyboardSelection === card.number
          }

          MouseArea {
            anchors.fill: parent
            z: 0
            cursorShape: Qt.PointingHandCursor
            onClicked: root.focusWorkspace(card.number)
          }

          Rectangle {
            id: miniature
            anchors.top: numberLabel.bottom
            anchors.topMargin: 5
            anchors.horizontalCenter: parent.horizontalCenter
            width: card.width
            height: Math.min(root.stripHeight - 76, 100, card.width * card.logicalHeight / Math.max(1, card.logicalWidth))
            radius: 7
            color: Qt.darker(root.background, 1.08)
            border.width: workspaceDrop.containsDrag || root.keyboardSelection === card.number ? 3 : (card.modelData.active ? 2 : 1)
            border.color: workspaceDrop.containsDrag ? root.accent : root.keyboardSelection === card.number ? root.foreground
              : (card.modelData.active ? root.accent : Qt.rgba(1, 1, 1, 0.19))
            clip: true

            Image {
              id: wallpaper
              x: 0
              y: 0
              width: miniature.width
              height: miniature.height
              source: root.wallpaperUrl(card.modelData.wallpaper)
              sourceSize: Qt.size(Math.ceil(width * 2), Math.ceil(height * 2))
              fillMode: Image.PreserveAspectCrop
              asynchronous: true
              cache: true
              visible: status === Image.Ready
            }

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.focusWorkspace(card.number)
            }

            Repeater {
              model: root.livePreviews ? card.contents : []
              delegate: Item {
                id: windowPreview
                required property var modelData
                property var dragSnapshot: null
                property bool dragImageReady: false
                readonly property int carryWidth: 240
                readonly property int carryHeight: Math.max(48, Math.round(carryWidth * height / Math.max(1, width)))
                readonly property real scaleX: miniature.width / Math.max(1, card.logicalWidth)
                readonly property real scaleY: miniature.height / Math.max(1, card.logicalHeight)
                x: (modelData.x - card.monitor.x) * scaleX
                y: (modelData.y - card.monitor.y) * scaleY
                width: Math.max(1, modelData.w * scaleX)
                height: Math.max(1, modelData.h * scaleY)
                clip: true
                Connections {
                  target: windowPreview.modelData.top
                  function onLastIpcObjectChanged() { geometrySettleTimer.restart() }
                }
                // Capture the actual rendered window before starting the native
                // drag. Keep the grab result alive for the entire drag session.
                Drag.active: windowDrag.active && dragImageReady
                Drag.dragType: Drag.Automatic
                Drag.keys: ["application/x-leaf-windows"]
                Drag.supportedActions: Qt.MoveAction
                Drag.proposedAction: Qt.MoveAction
                Drag.imageSource: dragSnapshot ? dragSnapshot.url : ""
                Drag.imageSourceSize: Qt.size(carryWidth, carryHeight)
                Drag.hotSpot: Qt.point(carryWidth / 2, carryHeight / 2)
                Drag.mimeData: ({ "application/x-leaf-windows": JSON.stringify({windows: ["0x" + String(windowPreview.modelData.address).replace(/^0x/, "")]}) })
                DragHandler {
                  id: windowDrag
                  target: null
                  onActiveChanged: {
                    root.windowDragActive = active
                    if (active) {
                      windowPreview.dragImageReady = false
                      const started = windowPreview.grabToImage(result => {
                        if (!windowDrag.active) return
                        windowPreview.dragSnapshot = result
                        windowPreview.dragImageReady = true
                      }, Qt.size(windowPreview.carryWidth, windowPreview.carryHeight))
                      if (!started) console.warn("Leaf: could not capture window drag preview")
                    } else {
                      windowPreview.dragImageReady = false
                    }
                  }
                }
                HoverHandler { cursorShape: windowDrag.active ? Qt.ClosedHandCursor : Qt.OpenHandCursor }

                Rectangle {
                  anchors.fill: parent
                  radius: 3
                  color: Qt.lighter(root.background, 1.3)
                }
                ScreencopyView {
                  anchors.fill: parent
                  captureSource: root.opened ? windowPreview.modelData.top.wayland : null
                  live: root.opened
                  visible: hasContent
                }
                Image {
                  anchors.centerIn: parent
                  width: Math.min(parent.width * 0.42, 42)
                  height: width
                  source: root.iconFor(windowPreview.modelData.appClass)
                  fillMode: Image.PreserveAspectFit
                  visible: !windowPreview.modelData.top.wayland
                  smooth: true
                }
                Rectangle {
                  anchors.fill: parent
                  color: "transparent"
                  radius: 2
                  border.width: 1
                  border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.8)
                }
                // A MouseArea here takes the exclusive press grab before the
                // DragHandler can promote the gesture to a native drag. Keep
                // preview clicks passive so the handler can own the gesture.
                TapHandler {
                  onTapped: root.focusWorkspace(card.number)
                }
              }
            }
          }

          DropArea {
            id: workspaceDrop
            anchors.fill: miniature
            // DropArea.keys filters Qt Quick Drag.keys, not offered MIME
            // formats. Leaf's running dock is a GTK drag source, so filter
            // by DragEvent.formats instead to accept both GTK and QML drags.
            onEntered: drag => {
              drag.accepted = root.isLeafWindowDrag(drag)
              if (drag.accepted) drag.accept(Qt.MoveAction)
            }
            onPositionChanged: drag => {
              drag.accepted = root.isLeafWindowDrag(drag)
              if (drag.accepted) drag.accept(Qt.MoveAction)
            }
            onDropped: drop => {
              if (root.isLeafWindowDrag(drop)
                  && root.moveWindows(drop.getDataAsString("application/x-leaf-windows"), card.number))
                drop.accept(Qt.MoveAction)
              else
                drop.accepted = false
            }
          }

          // Draw above wallpaper and live previews so selection cannot be covered.
          Rectangle {
            anchors.fill: miniature
            z: 10
            color: workspaceDrop.containsDrag ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.12) : "transparent"
            radius: miniature.radius
            border.width: workspaceDrop.containsDrag || root.moveFeedbackWorkspace === card.number
              || root.keyboardSelection === card.number ? 3 : (card.modelData.active ? 2 : 1)
            border.color: workspaceDrop.containsDrag || root.moveFeedbackWorkspace === card.number ? root.accent : root.keyboardSelection === card.number ? root.foreground
              : (card.modelData.active ? root.accent : Qt.rgba(1, 1, 1, 0.19))
            Behavior on color { ColorAnimation { duration: 150 } }
            Behavior on border.width { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
          }

          Column {
            anchors.top: miniature.bottom
            anchors.topMargin: 7
            anchors.left: parent.left
            width: parent.width
            spacing: 3
            Repeater {
              model: root.otherMonitorApps ? Object.keys(root.workspaceSets) : [card.monitor.name]
              delegate: Item {
                id: monitorRow
                required property string modelData
                readonly property int workspaceId: (root.workspaceSets[modelData] || [])[card.number - 1] || -1
                readonly property var apps: root.appsForWorkspace(workspaceId)
                width: card.width
                height: Math.max(17, icons.implicitHeight)

                Text {
                  x: 2
                  anchors.verticalCenter: parent.verticalCenter
                  text: String(Object.keys(root.workspaceSets).indexOf(monitorRow.modelData) + 1)
                  color: monitorRow.modelData === card.monitor.name
                    ? root.accent : root.foreground
                  opacity: monitorRow.apps.length ? 0.9 : 0.35
                  font.pixelSize: 10
                  font.bold: true
                }

                Flow {
                  id: icons
                  x: 17
                  y: 0
                  width: card.width - 17
                  spacing: 2
                  Repeater {
                    model: monitorRow.apps
                    delegate: Item {
                      id: workspaceAppIcon
                      required property var modelData
                      width: 16
                      height: 16
                      z: appIconDrag.active ? 20 : 0
                      Drag.active: appIconDrag.active
                      Drag.dragType: Drag.Automatic
                      Drag.keys: ["application/x-leaf-windows"]
                      Drag.supportedActions: Qt.MoveAction
                      Drag.proposedAction: Qt.MoveAction
                      Drag.imageSource: root.iconFor(modelData.appClass)
                      Drag.imageSourceSize: Qt.size(72, 72)
                      Drag.hotSpot: Qt.point(workspaceAppIcon.width / 2, workspaceAppIcon.height / 2)
                      Drag.mimeData: ({ "application/x-leaf-windows": JSON.stringify({
                        windows: root.windowAddressesForApp(
                          monitorRow.workspaceId, modelData.appClass || modelData.address
                        )
                      }) })
                      DragHandler {
                        id: appIconDrag
                        target: null
                        onActiveChanged: root.windowDragActive = active
                      }
                      HoverHandler { cursorShape: appIconDrag.active ? Qt.ClosedHandCursor : Qt.OpenHandCursor }
                      Image {
                        id: appGlyph
                        anchors.fill: parent
                        source: root.iconFor(modelData.appClass)
                        fillMode: Image.PreserveAspectFit
                        smooth: true
                        scale: appIconDrag.active ? 1.42 : 1
                        rotation: appIconDrag.active ? -7 : 0
                        z: appIconDrag.active ? 20 : 0
                        Behavior on scale {
                          NumberAnimation { duration: 180; easing.type: Easing.OutBack }
                        }
                        Behavior on rotation { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                      }
                      Rectangle {
                        anchors.centerIn: appGlyph
                        width: 30
                        height: 30
                        radius: 9
                        z: appIconDrag.active ? 19 : -1
                        color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.18)
                        border.width: 1
                        border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.8)
                        scale: appIconDrag.active ? 1.08 : 0.55
                        opacity: appIconDrag.active ? 1 : 0
                        Behavior on scale { NumberAnimation { duration: 180; easing.type: Easing.OutBack } }
                        Behavior on opacity { NumberAnimation { duration: 120 } }
                      }
                      // TapHandler is passive; MouseArea can swallow the
                      // pointer before DragHandler starts the workspace drag.
                      TapHandler {
                        onTapped: root.focusWindow(workspaceAppIcon.modelData.address)
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
