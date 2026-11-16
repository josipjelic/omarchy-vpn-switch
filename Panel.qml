import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "josipjelic.vpn-switch"

  property var profiles: []
  property string errorText: ""
  property bool busy: false
  property string busyName: ""
  property string action: ""
  property string request: ""
  property bool editing: false
  readonly property string helper: Qt.resolvedUrl("backend.py").toString().replace(/^file:\/\//, "")
  readonly property bool anyConnected: profiles.some(function(p) { return p.connected })
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  implicitWidth: icon.implicitWidth
  implicitHeight: icon.implicitHeight

  function refresh() {
    if (busy || listProcess.running) return
    listProcess.running = true
  }

  function applyResult(text, code, showError) {
    try {
      var data = JSON.parse(text)
      if (!data.ok || code !== 0) {
        if (showError) errorText = data.error || "VPN operation failed"
        return false
      }
      profiles = data.profiles || []
      if (showError) errorText = ""
      return true
    } catch (error) {
      if (showError) errorText = "VPN helper returned an invalid response"
      return false
    }
  }

  function run(act, name, payload) {
    if (busy) return
    busy = true
    action = act
    busyName = name
    errorText = ""
    request = payload || ""
    actionProcess.command = ["python", helper, act, name]
    actionProcess.running = true
  }

  function flip(p) { run(p.connected ? "disconnect" : "connect", p.name, "") }

  function openForm(p) {
    nameField.text = p ? p.name : ""
    serverField.text = p ? p.server : ""
    userField.text = p ? p.username : ""
    passwordField.text = ""
    nameField.enabled = !p
    editing = true
    Qt.callLater(function() { (p ? passwordField : nameField).forceActiveFocus() })
  }

  function saveForm() {
    if (!nameField.text.trim() || !serverField.text.trim() || !userField.text.trim()) {
      errorText = "Enter a name, server and username"
      return
    }
    run("add", nameField.text.trim(), JSON.stringify({
      name: nameField.text.trim(), server: serverField.text.trim(),
      username: userField.text.trim(), password: passwordField.text
    }))
    passwordField.text = ""
  }

  onOpenedChanged: {
    if (opened) refresh()
    else { editing = false; passwordField.text = "" }
  }

  Timer {
    interval: 5000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: listProcess
    command: ["python", root.helper, "list"]
    stdout: StdioCollector { id: listOutput; waitForEnd: true }
    onExited: function(code) {
      if (code === 0) root.applyResult(listOutput.text, code, false)
    }
  }

  Process {
    id: actionProcess
    stdinEnabled: true
    onStarted: {
      if (root.request !== "") write(root.request + "\n")
      root.request = ""
    }
    stdout: StdioCollector { id: actionOutput; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      var ok = root.applyResult(actionOutput.text, code, true)
      if (ok && (root.action === "add" || root.action === "remove")) root.editing = false
      root.busyName = ""
      root.refresh()
    }
  }

  BarIconButton {
    id: icon
    anchors.fill: parent
    bar: root.bar
    text: root.anyConnected ? "󰌆" : "󰦝"
    tooltipText: root.anyConnected ? "VPN connected" : "VPN off"
    onPressed: root.toggle()
  }

  KeyboardPanel {
    anchorItem: icon
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: root.editing ? nameField : null
    contentWidth: fittedContentWidth(Style.space(320))
    contentHeight: fittedContentHeight(column.implicitHeight)

    Column {
      id: column
      width: parent.width
      spacing: Style.space(8)

      Text {
        text: "VPN Switch"
        color: root.barForeground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        visible: root.profiles.length === 0 && !root.editing
        width: parent.width
        wrapMode: Text.WordWrap
        text: "No VPNs yet. Add one and its password is kept in your keyring."
        color: root.barForeground
        opacity: 0.65
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Repeater {
        model: root.editing ? [] : root.profiles
        delegate: Item {
          width: column.width
          height: Style.space(40)

          Column {
            anchors.left: parent.left
            anchors.right: sw.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            Text {
              width: parent.width
              elide: Text.ElideRight
              textFormat: Text.PlainText
              text: modelData.name
              color: root.barForeground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.openForm(modelData) }
            }
            Text {
              width: parent.width
              elide: Text.ElideRight
              textFormat: Text.PlainText
              text: (root.busy && root.busyName === modelData.name)
                    ? (root.action === "connect" ? "Connecting…" : "Working…")
                    : (modelData.connected ? "Connected" : (modelData.hasPassword ? modelData.server : "Password missing"))
              color: root.barForeground
              opacity: 0.65
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          ToggleSwitch {
            id: sw
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            rounded: false
            cursorRing: false
            checked: modelData.connected
            busy: root.busy
            opacity: root.busy ? 0.5 : 1
            onToggled: root.flip(modelData)
          }
        }
      }

      TextField { id: nameField; visible: root.editing; width: parent.width; placeholderText: "Name (Work)"; onAccepted: serverField.forceActiveFocus() }
      TextField { id: serverField; visible: root.editing; width: parent.width; placeholderText: "Server (vpn.example.com)"; onAccepted: userField.forceActiveFocus() }
      TextField { id: userField; visible: root.editing; width: parent.width; placeholderText: "Username"; onAccepted: passwordField.forceActiveFocus() }
      TextField { id: passwordField; visible: root.editing; width: parent.width; placeholderText: "Password (blank keeps saved)"; password: true; onAccepted: root.saveForm() }

      Text {
        visible: root.errorText !== ""
        width: parent.width
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        text: root.errorText
        color: root.bar ? root.bar.urgent : Color.urgent
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Row {
        spacing: Style.space(8)
        width: parent.width

        Rectangle {
          width: root.editing ? (parent.width - Style.space(8)) / 2 : parent.width
          height: Style.space(36)
          radius: Style.cornerRadius
          color: root.bar ? root.bar.foreground : Color.foreground
          opacity: root.busy ? 0.5 : 1
          Text {
            anchors.centerIn: parent
            text: root.editing ? "Save" : "Add VPN"
            color: Color.background
            font.family: root.fontFamily
          }
          MouseArea {
            anchors.fill: parent
            enabled: !root.busy
            cursorShape: Qt.PointingHandCursor
            onClicked: root.editing ? root.saveForm() : root.openForm(null)
          }
        }

        Rectangle {
          visible: root.editing
          width: (parent.width - Style.space(8)) / 2
          height: Style.space(36)
          radius: Style.cornerRadius
          border.width: 1
          border.color: root.barForeground
          color: "transparent"
          Text {
            anchors.centerIn: parent
            text: nameField.enabled ? "Cancel" : "Remove"
            color: root.barForeground
            font.family: root.fontFamily
          }
          MouseArea {
            anchors.fill: parent
            enabled: !root.busy
            cursorShape: Qt.PointingHandCursor
            onClicked: nameField.enabled ? (root.editing = false) : root.run("remove", nameField.text, "")
          }
        }
      }
    }
  }
}
