import QtQuick

// Small local vectors keep navigation independent of symbol-font coverage.
Canvas {
    property string name: ""
    property color ink: appearance.colors.accent
    implicitWidth: 20
    implicitHeight: 20
    onNameChanged: requestPaint()
    onInkChanged: requestPaint()
    onPaint: {
        const c = getContext("2d")
        c.reset()
        c.scale(width / 24, height / 24)
        c.strokeStyle = ink
        c.lineWidth = 1.65
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(points) {
            c.beginPath(); c.moveTo(points[0], points[1])
            for (let i = 2; i < points.length; i += 2) c.lineTo(points[i], points[i+1])
            c.stroke()
        }
        if (name === "projects") {
            line([3, 7, 3, 19, 21, 19, 21, 7, 13, 7, 10, 4, 3, 4, 3, 7])
        } else if (name === "models") {
            line([12, 3, 21, 8, 12, 13, 3, 8, 12, 3])
            line([3, 12, 12, 17, 21, 12]); line([3, 16, 12, 21, 21, 16])
        } else if (name === "activity") {
            line([3, 12, 7, 12, 10, 5, 14, 19, 17, 12, 21, 12])
        } else if (name === "file") {
            line([5, 2, 14, 2, 19, 7, 19, 22, 5, 22, 5, 2])
            line([14, 2, 14, 7, 19, 7]); line([8, 12, 16, 12]); line([8, 16, 14, 16])
        } else if (name === "usage") {
            line([4, 20, 20, 20]); line([6, 16, 6, 12]); line([12, 16, 12, 4]); line([18, 16, 18, 8])
        } else if (name === "context") {
            c.strokeRect(4, 5, 16, 14); c.strokeRect(9, 9, 6, 6)
            line([8, 2, 8, 5]); line([16, 2, 16, 5]); line([8, 19, 8, 22]); line([16, 19, 16, 22])
            line([1, 9, 4, 9]); line([1, 15, 4, 15]); line([20, 9, 23, 9]); line([20, 15, 23, 15])
        } else if (name === "browser") {
            c.strokeRect(3, 3, 18, 18); line([3, 8, 21, 8])
            line([7, 5.5, 7.1, 5.5]); line([10, 5.5, 10.1, 5.5])
        } else if (name === "memory") {
            line([12, 5, 12, 21]); line([12, 5, 7, 3, 2, 4, 2, 19, 7, 19, 12, 21, 17, 19, 22, 19, 22, 4, 17, 3, 12, 5])
        } else if (name === "attach") {
            c.beginPath(); c.moveTo(8, 14); c.lineTo(15, 7)
            c.bezierCurveTo(18, 4, 22, 8, 19, 11); c.lineTo(10, 20)
            c.bezierCurveTo(5, 25, -1, 19, 4, 14); c.lineTo(15, 3); c.stroke()
        } else if (name === "lock") {
            c.strokeRect(5, 10, 14, 11)
            c.beginPath(); c.moveTo(8, 10); c.lineTo(8, 6); c.arc(12, 6, 4, Math.PI, 0); c.lineTo(16, 10); c.stroke()
        } else if (name === "team") {
            c.beginPath(); c.arc(9, 7, 3, 0, 2 * Math.PI); c.stroke()
            c.beginPath(); c.arc(17, 8, 2.5, -1.4, 1.4); c.stroke()
            c.beginPath(); c.moveTo(3, 21); c.lineTo(3, 18); c.bezierCurveTo(3, 12, 15, 12, 15, 18); c.lineTo(15, 21); c.stroke()
            c.beginPath(); c.moveTo(18, 14); c.bezierCurveTo(22, 14, 22, 17, 22, 20); c.stroke()
        } else if (name === "goal") {
            line([16, 3, 21, 8, 17, 9, 13, 14, 13, 17, 7, 11, 10, 11, 15, 7, 16, 3])
            line([10, 14, 4, 20])
        } else if (name === "settings") {
            line([4, 6, 20, 6]); line([4, 12, 20, 12]); line([4, 18, 20, 18])
            c.fillStyle = ink
            c.fillRect(7, 3.5, 3, 5); c.fillRect(15, 9.5, 3, 5); c.fillRect(8, 15.5, 3, 5)
        }
    }
}
