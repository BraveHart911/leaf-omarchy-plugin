import QtQuick
import Quickshell
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.bravehart911.leaf-control"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "▦"
    tooltipText: "Leaf · Apps, workspaces and system\nRight-click for settings"
    onPressed: function(buttonCode) {
      const launcher = decodeURIComponent(String(Qt.resolvedUrl("launch-leaf")).replace(/^file:\/\//, ""))
      if (buttonCode === Qt.LeftButton)
        Quickshell.execDetached(["sh", launcher])
      else if (buttonCode === Qt.RightButton)
        Quickshell.execDetached(["sh", launcher, "--settings"])
    }
  }
}
