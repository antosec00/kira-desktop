import QtQuick

// One stroke-based icon set drawn from SVG path data on a 24-unit grid, so
// every control shares the same weight regardless of symbol-font coverage.
Canvas {
    id: icon
    property string name: ""
    property color ink: appearance.colors.accent
    property real strokeWidth: 1.7
    implicitWidth: 20
    implicitHeight: 20
    readonly property var strokes: ({
        "projects": ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
        "models": ["M12 3l9 5-9 5-9-5z", "M3 12l9 5 9-5", "M3 16l9 5 9-5"],
        "activity": ["M3 12h4l3-7 4 14 3-7h4"],
        "file": ["M6 3h8l5 5v13H6z", "M14 3v5h5", "M9 13h6", "M9 17h4"],
        "usage": ["M4 20h16", "M7 16v-5", "M12 16V5", "M17 16V9"],
        "context": ["M5 5h14v14H5z", "M9 9h6v6H9z", "M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"],
        "browser": ["M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M3 12h18", "M12 3a14 14 0 0 1 0 18", "M12 3a14 14 0 0 0 0 18"],
        "memory": ["M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 0-3 3z", "M4 4v16", "M9 9h6"],
        "knowledge": ["M3 5h6a3 3 0 0 1 3 3v12a2.5 2.5 0 0 0-2.5-2.5H3z", "M21 5h-6a3 3 0 0 0-3 3v12a2.5 2.5 0 0 1 2.5-2.5H21z"],
        "attach": ["M21 12.5l-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l9.2-9.2a3.5 3.5 0 0 1 5 5l-9.2 9.2a1.5 1.5 0 0 1-2.1-2.1L16 7.7"],
        "lock": ["M5 11h14v10H5z", "M8 11V7a4 4 0 0 1 8 0v4"],
        "team": ["M9 8m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0", "M2.5 20a6.5 6.5 0 0 1 13 0", "M17 9m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0", "M16 15.5a5 5 0 0 1 5.5 4.5"],
        "goal": ["M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0"],
        "settings": ["M4 7h10M18 7h2M4 17h4M12 17h8", "M16 7m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "M10 17m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"],
        "search": ["M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0", "M20 20l-3.5-3.5"],
        "sidebar": ["M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M9 4v16"],
        "plus": ["M12 5v14M5 12h14"],
        "chevron-down": ["M6 9l6 6 6-6"],
        "chevron-right": ["M9 6l6 6-6 6"],
        "close": ["M6 6l12 12M18 6L6 18"],
        "more": [],
        "export": ["M12 3v12", "M7 10l5 5 5-5", "M4 19h16"],
        "details": ["M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M12 11v6"],
        "resume": ["M3 12a9 9 0 1 0 3-6.7", "M3 4v5h5", "M12 8v4l3 2"],
        "actions": ["M8 6h13M8 12h13M8 18h13"],
        "research": ["M4 19.5V5a2 2 0 0 1 2-2h13v14H6a2 2 0 0 0-2 2z", "M4 19.5A2.5 2.5 0 0 1 6.5 17H19", "M9 7h6"],
        "studio": ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M4 17l6-6 4 4 6-6"],
        "anima": ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z", "M19 17l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"],
        "questions": ["M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.7"],
        "stop": [],
        "send": ["M12 19V5", "M6 11l6-6 6 6"],
        "refresh": ["M3 12a9 9 0 1 0 3-6.7", "M3 4v5h5"],
        "labels": ["M4 7h16M4 12h10M4 17h7"],
        "sun": ["M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0", "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"],
        "moon": ["M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"],
        "external": ["M14 4h6v6M20 4l-8 8", "M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"],
        "queue": ["M4 7h16M4 12h16M4 17h10"],
        "help": ["M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M12 12m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0", "M5.6 5.6l3.9 3.9M14.5 14.5l3.9 3.9M18.4 5.6l-3.9 3.9M9.5 14.5l-3.9 3.9"],
        // File chooser: navigation, places and file kinds.
        "back": ["M19 12H5", "M11 6l-6 6 6 6"],
        "forward": ["M5 12h14", "M13 6l6 6-6 6"],
        "up": ["M12 20V9", "M7 13l5-5 5 5", "M5 4h14"],
        "home": ["M3 11l9-7 9 7", "M5 9.5V20h14V9.5", "M10 20v-5h4v5"],
        "folder": ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
        "folder-plus": ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M12 10.5v6M9 13.5h6"],
        "drive": ["M4 14h16v5H4z", "M4 14l2.5-8h11L20 14", "M16 16.5h1"],
        "desktop": ["M3 4h18v12H3z", "M8 20h8", "M12 16v4"],
        "download": ["M12 4v11", "M7 10l5 5 5-5", "M5 20h14"],
        "image": ["M4 5h16v14H4z", "M4 16l5-5 4 4 3-3 4 4", "M9 9.5m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0"],
        "code": ["M9 8l-5 4 5 4", "M15 8l5 4-5 4", "M13.5 5l-3 14"],
        "archive": ["M4 5h16v4H4z", "M5 9v10h14V9", "M10 13h4"],
        "audio": ["M9 18V6l10-2v12", "M7 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "M17 16m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"],
        "video": ["M4 6h12v12H4z", "M16 10l4-2v8l-4-2"],
        "document": ["M6 3h8l5 5v13H6z", "M14 3v5h5", "M9 12h6", "M9 15.5h6", "M9 19h3"],
        "copy": ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
        "rename": ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
        "eye": ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0"],
        "link": ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
        "check": ["M5 12.5l4.5 4.5L19 7.5"],
        "update": ["M20 11a8 8 0 0 0-14.6-4.5", "M4 4v4h4", "M4 13a8 8 0 0 0 14.6 4.5", "M20 20v-4h-4", "M12 8v6", "M9.5 11.5L12 14l2.5-2.5"]
    })
    // Small solid marks that a stroke would render too thin.
    readonly property var fills: ({
        "more": ["M5 12m-1.6 0a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0-3.2 0", "M12 12m-1.6 0a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0-3.2 0", "M19 12m-1.6 0a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0-3.2 0"],
        "details": ["M12 8m-1.1 0a1.1 1.1 0 1 0 2.2 0a1.1 1.1 0 1 0-2.2 0"],
        "actions": ["M3.5 6m-1.1 0a1.1 1.1 0 1 0 2.2 0a1.1 1.1 0 1 0-2.2 0", "M3.5 12m-1.1 0a1.1 1.1 0 1 0 2.2 0a1.1 1.1 0 1 0-2.2 0", "M3.5 18m-1.1 0a1.1 1.1 0 1 0 2.2 0a1.1 1.1 0 1 0-2.2 0"],
        "questions": ["M12 17m-1.1 0a1.1 1.1 0 1 0 2.2 0a1.1 1.1 0 1 0-2.2 0"],
        "goal": ["M12 12m-1.4 0a1.4 1.4 0 1 0 2.8 0a1.4 1.4 0 1 0-2.8 0"],
        "stop": ["M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"]
    })
    onNameChanged: requestPaint()
    onInkChanged: requestPaint()
    onStrokeWidthChanged: requestPaint()
    onPaint: {
        const c = getContext("2d")
        c.reset()
        c.scale(width / 24, height / 24)
        c.strokeStyle = ink
        c.fillStyle = ink
        c.lineWidth = strokeWidth
        c.lineCap = "round"
        c.lineJoin = "round"
        const outline = strokes[name] || []
        for (let i = 0; i < outline.length; ++i) {
            c.beginPath(); c.path = outline[i]; c.stroke()
        }
        const solid = fills[name] || []
        for (let j = 0; j < solid.length; ++j) {
            c.beginPath(); c.path = solid[j]; c.fill()
        }
    }
}
