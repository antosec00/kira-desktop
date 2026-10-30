import QtQuick

// Shared presentation rules for views that show provider identifiers or
// service timestamps. Unknown values stay literal; nothing here changes state.
QtObject {
    // The same names the provider selectors show.
    function providerLabel(backend) {
        switch (String(backend || "")) {
        case "ollama": return "Ollama"
        case "codex": return "Codex"
        case "openai_chatgpt": return qsTr("OpenAI diretto · ChatGPT")
        case "deepseek_account": return qsTr("DeepSeek Account")
        case "deepseek_web": return qsTr("DeepSeek Web · sperimentale")
        case "claude_code": return qsTr("Claude Code · uso personale")
        case "google_cloud": return qsTr("Google Cloud · Vertex AI")
        }
        return String(backend || "")
    }

    // Ollama has its own thinking choice and DeepSeek Web declares no
    // reasoning levels: every view hides the reasoning level for both.
    function offersEffort(backend) {
        return ["ollama", "deepseek_web"].indexOf(String(backend || "")) < 0
    }

    // Service timestamps are ISO 8601 in UTC; show them in the interface
    // language and the computer's time zone, like the Python views do.
    function timestamp(value) {
        const text = String(value || "")
        const moment = new Date(text)
        if (!text.length || isNaN(moment.getTime())) return text
        const language = typeof localization !== "undefined" ? localization.language : ""
        return moment.toLocaleString(Qt.locale(language), "d MMM yyyy, HH:mm")
    }

    function relativeTime(value) {
        const text = String(value || "")
        const moment = new Date(text)
        if (!text.length || isNaN(moment.getTime())) return ""
        const minutes = Math.max(1, Math.floor((Date.now() - moment.getTime()) / 60000))
        if (minutes < 60) return minutes + "m"
        const hours = Math.floor(minutes / 60)
        if (hours < 24) return hours + "h"
        const language = typeof localization !== "undefined" ? localization.language : "it"
        const days = Math.floor(hours / 24)
        if (days < 30) return days + (language === "en" ? "d" : "g")
        const months = Math.floor(days / 30)
        if (months < 12) return months + "mo"
        return Math.floor(days / 365) + (language === "en" ? "y" : "a")
    }
}
