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
}
