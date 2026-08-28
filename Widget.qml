import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.brm-src.omarchy-youtube-player"

  readonly property string languageOverride: Quickshell.env("OMARCHY_YOUTUBE_PLAYER_LANG") || ""
  readonly property bool isSpanish: languageOverride === "es" || (languageOverride !== "en" && Qt.locale().name.toLowerCase().startsWith("es"))
  readonly property string lang: isSpanish ? "es" : "en"
  readonly property string helperPath: Qt.resolvedUrl("player.py").toString().replace(/^file:\/\//, "")
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.45)

  property bool popupOpen: false
  property bool busy: false
  property string query: ""
  property string statusMessage: ""
  property string title: ""
  property string channel: ""
  property string currentUrl: ""
  property bool playing: false
  property bool active: false
  property bool audioOnly: false
  property real position: 0
  property real durationSeconds: 0
  property var results: []

  function words(es, en) { return isSpanish ? es : en }
  function run(args) {
    actionProcess.command = ["python3", helperPath].concat(args).concat(["--lang", lang])
    actionProcess.running = true
  }
  function search() {
    var term = searchField.text.trim()
    if (searchProcess.running || term === "") return
    query = term
    busy = true
    statusMessage = words("Buscando…", "Searching…")
    searchProcess.command = ["python3", helperPath, "search", term, "--lang", lang]
    searchProcess.running = true
  }
  function playUrl(url) {
    busy = true
    statusMessage = words("Cargando video…", "Loading video…")
    run(["play", url])
  }
  function refreshStatus() {
    if (statusProcess.running) return
    statusProcess.command = ["python3", helperPath, "status"]
    statusProcess.running = true
  }
  function handleJson(raw, kind) {
    var text = raw.trim()
    if (!text) return
    try {
      var data = JSON.parse(text)
      if (kind === "search") {
        busy = false
        if (!data.ok) {
          results = []
          statusMessage = data.message || words("La búsqueda falló.", "Search failed.")
        } else {
          results = data.results || []
          statusMessage = results.length ? "" : words("Sin resultados.", "No results.")
        }
      } else if (kind === "action") {
        busy = false
        if (!data.ok) statusMessage = data.message || words("El reproductor no está disponible.", "The player is not available.")
        else statusMessage = ""
        refreshStatus()
      } else {
        busy = false
        if (data.ok) {
          active = !!data.active
          playing = !!data.playing
          title = data.title || ""
          channel = data.channel || ""
          currentUrl = data.url || currentUrl
          position = Number(data.position || 0)
          durationSeconds = Number(data.durationSeconds || 0)
          audioOnly = !!data.audioOnly
        }
      }
    } catch (error) {
      busy = false
      statusMessage = words("Respuesta inválida del reproductor.", "Invalid player response.")
    }
  }
  function formatTime(value) {
    var total = Math.max(0, Math.floor(Number(value) || 0))
    var minutes = Math.floor(total / 60)
    var seconds = total % 60
    return minutes + ":" + (seconds < 10 ? "0" : "") + seconds
  }
  function shortTitle(value) {
    var text = String(value || "")
    return text.length > 38 ? text.slice(0, 37) + "…" : text
  }
  function toggle() { popupOpen = !popupOpen; if (popupOpen) { refreshStatus(); Qt.callLater(function() { searchField.forceActiveFocus() }) } }
  function open() { popupOpen = true; refreshStatus(); Qt.callLater(function() { searchField.forceActiveFocus() }) }
  function close() { popupOpen = false }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Timer {
    interval: 1200
    repeat: true
    running: root.active
    onTriggered: root.refreshStatus()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    slotSize: Style.bar.statusSlot
    text: root.playing ? "󰏤" : "󰗃"
    fontSize: Style.font.body
    tooltipText: root.active ? root.title : root.words("Reproductor de YouTube", "YouTube Player")
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.MiddleButton) root.run(["action", "pause"])
      else root.toggle()
    }
  }

  IpcHandler {
    target: "io.github.brm-src.omarchy-youtube-player"
    function open() { root.open(); return "ok" }
    function close() { root.close(); return "ok" }
    function toggle() { root.toggle(); return "ok" }
  }

  Process {
    id: searchProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleJson(text, "search")
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && root.busy) {
        root.busy = false
        root.statusMessage = root.words("No se pudo completar la búsqueda.", "The search could not be completed.")
      }
    }
  }

  Process {
    id: actionProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleJson(text, "action")
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && root.busy) {
        root.busy = false
        root.statusMessage = root.words("No se pudo completar la acción.", "The action could not be completed.")
      }
    }
  }

  Process {
    id: statusProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleJson(text, "status")
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.popupOpen
    focusTarget: searchField
    contentWidth: popup.fittedContentWidth(Style.space(420))
    contentHeight: popup.fittedContentHeight(column.implicitHeight)

    Column {
      id: column
      anchors.fill: parent
      spacing: Style.space(12)

      Row {
        width: parent.width
        spacing: Style.space(8)
        Column {
          width: parent.width - closeButton.width - Style.space(8)
          spacing: Style.space(3)
          Text {
            text: "YOUTUBE"
            color: root.foreground
            font.family: Style.font.menuFamily
            font.pixelSize: Style.font.title
            font.bold: true
            font.letterSpacing: 1.2
          }
          Text {
            width: parent.width
            text: root.words("Reproducción en segundo plano.", "Background playback.")
            color: root.dim
            font.family: Style.font.menuFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
        Button {
          id: closeButton
          focusable: true
          text: "×"
          tooltipText: root.words("Cerrar", "Close")
          onClicked: root.close()
        }
      }

      Row {
        width: parent.width
        spacing: Style.space(6)
        TextField {
          id: searchField
          width: parent.width - searchButton.width - Style.space(6)
          placeholderText: root.words("Buscar en YouTube", "Search YouTube")
          foreground: root.foreground
          font.family: Style.font.menuFamily
          Keys.onEscapePressed: function(event) {
            root.close()
            event.accepted = true
          }
          onAccepted: root.search()
        }
        Button {
          id: searchButton
          focusable: true
          text: root.busy ? "…" : root.words("Buscar", "Search")
          enabled: !root.busy && searchField.text.trim() !== ""
          onClicked: root.search()
        }
      }

      Text {
        visible: root.statusMessage !== ""
        width: parent.width
        text: root.statusMessage
        color: root.dim
        font.family: Style.font.menuFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
      }

      Column {
        visible: root.active
        width: parent.width
        spacing: Style.space(8)
        Text {
          text: root.words("AHORA REPRODUCIENDO", "NOW PLAYING")
          color: root.dim
          font.family: Style.font.menuFamily
          font.pixelSize: Style.font.caption
          font.bold: true
          font.letterSpacing: 1
        }
        Text {
          width: parent.width
          text: root.title
          color: root.foreground
          font.family: Style.font.menuFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          text: root.channel
          visible: text !== ""
          color: root.dim
          font.family: Style.font.menuFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }
        Row {
          width: parent.width
          spacing: Style.space(6)
          Button { focusable: true; text: "−10"; onClicked: root.run(["action", "back"]) }
          Button { focusable: true; text: root.playing ? "󰏤" : "󰐊"; onClicked: root.run(["action", "pause"]) }
          Button { focusable: true; text: "+10"; onClicked: root.run(["action", "forward"]) }
          Button { focusable: true; text: "−"; onClicked: root.run(["action", "volume-down"]) }
          Button { focusable: true; text: "+"; onClicked: root.run(["action", "volume-up"]) }
          Button { focusable: true; text: root.words("Ampliar", "Fullscreen"); onClicked: root.run(["action", "fullscreen"]) }
          Button { focusable: true; text: root.words("Detener", "Stop"); onClicked: root.run(["action", "stop"]) }
        }
        Row {
          width: parent.width
          spacing: Style.space(6)
          Button { focusable: true; text: root.audioOnly ? root.words("Mostrar video", "Show video") : root.words("Solo audio", "Audio only"); onClicked: root.run(["action", root.audioOnly ? "show-video" : "audio-only"]) }
        }
        Row {
          width: parent.width
          spacing: Style.space(6)
          Rectangle {
            width: parent.width - timeLabel.width - Style.space(6)
            height: Style.space(3)
            anchors.verticalCenter: parent.verticalCenter
            radius: height / 2
            color: Qt.darker(root.foreground, 2.3)
            Rectangle {
              width: parent.width * (root.durationSeconds > 0 ? Math.min(1, root.position / root.durationSeconds) : 0)
              height: parent.height
              radius: height / 2
              color: Color.accent
            }
          }
          Text {
            id: timeLabel
            text: root.formatTime(root.position) + " / " + root.formatTime(root.durationSeconds)
            color: root.dim
            font.family: Style.font.menuFamily
            font.pixelSize: Style.font.caption
          }
        }
      }

      PanelSeparator { visible: root.active && root.results.length > 0; foreground: root.foreground }

      Text {
        visible: root.results.length > 0
        text: root.words("RESULTADOS", "SEARCH RESULTS")
        color: root.dim
        font.family: Style.font.menuFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 1
      }

      ListView {
        id: resultList
        visible: root.results.length > 0
        width: parent.width
        height: Math.min(contentHeight, Style.space(300))
        clip: true
        interactive: contentHeight > height
        boundsBehavior: Flickable.StopAtBounds
        spacing: Style.space(5)
        model: root.results
        delegate: Row {
          required property var modelData
          width: resultList.width
          height: Style.space(52)
          spacing: Style.space(8)

          Image {
            width: Style.space(58)
            height: Style.space(36)
            anchors.verticalCenter: parent.verticalCenter
            source: modelData.thumbnail || ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            clip: true
          }

          Button {
            width: parent.width - Style.space(66)
            height: parent.height
            text: root.shortTitle(modelData.title)
            tooltipText: (modelData.duration ? modelData.duration + "  ·  " : "") + modelData.title
            leftAlign: true
            focusable: true
            onClicked: root.playUrl(modelData.url)
          }
        }
      }

      Text {
        visible: !root.active && root.results.length === 0 && root.statusMessage === ""
        width: parent.width
        text: root.words("Busca un video público para comenzar.", "Search for a public video to begin.")
        color: root.dim
        font.family: Style.font.menuFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
      }
    }
  }
}
