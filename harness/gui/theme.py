"""Soft-magenta cyberpunk theme: QSS for widgets + CSS for chat HTML."""

# Widget stylesheet (applied to the QApplication).
QSS = """
* {
    font-family: "Segoe UI", "Inter", sans-serif;
}
QMainWindow, QWidget#central {
    background-color: #0d0714;
}
QLabel#title {
    color: #ff8fe0;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 4px;
}
QLabel#subtitle {
    color: #8f739f;
    font-size: 11px;
    letter-spacing: 1px;
}
QComboBox, QToolButton {
    background-color: #1c1029;
    color: #f2e4fb;
    border: 1px solid #4a2560;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}
QComboBox:hover, QToolButton:hover {
    border: 1px solid #ff4fd8;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #1c1029;
    color: #f2e4fb;
    selection-background-color: #4a1748;
    border: 1px solid #ff4fd8;
}
QTextEdit#input {
    background-color: #150b22;
    color: #f5eefb;
    border: 1px solid #4a2560;
    border-radius: 10px;
    padding: 8px;
    font-size: 13px;
    selection-background-color: #7a2f7e;
}
QTextEdit#input:focus {
    border: 1px solid #ff4fd8;
}
QPushButton#send {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ff4fd8, stop:1 #a64fd8);
    color: #1c0716;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 1px;
    border: none;
    border-radius: 10px;
    padding: 10px 26px;
}
QPushButton#send:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ff7fe4, stop:1 #c06fe8);
}
QPushButton#send:disabled {
    background: #3a234f;
    color: #8f739f;
}
QToolButton#attach {
    font-size: 16px;
    font-weight: bold;
    color: #ff8fe0;
}
QTextBrowser#chat {
    background-color: #0d0714;
    border: none;
    font-size: 13px;
}
QScrollBar:vertical {
    background: #0d0714;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #4a2560;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #ff4fd8;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QStatusBar {
    background-color: #0d0714;
    color: #8f739f;
    font-size: 11px;
}
QStatusBar QLabel#statusdot {
    color: #ff4fd8;
    font-size: 14px;
}
QProgressBar {
    background-color: #1c1029;
    border: 1px solid #4a2560;
    border-radius: 6px;
    text-align: center;
    color: #f2e4fb;
    font-size: 11px;
    max-height: 14px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ff4fd8, stop:1 #a64fd8);
    border-radius: 5px;
}
"""

# CSS for the HTML inside the chat QTextBrowser (Qt rich-text subset).
CHAT_CSS = """
<style>
body { color: #f2eafc; font-size: 13px; }
.msg { margin: 10px 6px; padding: 10px 14px; border-radius: 12px; }
.user { background-color: #3d1440; border: 1px solid #8a3a8c; margin-left: 90px; }
.assistant { background-color: #1a0f28; border: 1px solid #3a234f; margin-right: 60px; }
.error { background-color: #2b0f1c; border: 1px solid #a83a5e; margin-right: 60px; }
.notice { background-color: #241335; border: 1px dashed #ff4fd8; margin-right: 60px; }
.who { color: #ff8fe0; font-size: 10px; letter-spacing: 2px; margin-bottom: 4px; }
.assistant .who { color: #b07cc0; }
p { margin: 4px 0; }
.heading { color: #ff8fe0; font-size: 14px; margin: 8px 0 4px 0; }
code { background-color: #2b1840; color: #ffd7f4; padding: 1px 5px; border-radius: 4px; }
pre.code { background-color: #100817; border: 1px solid #3a234f; border-radius: 8px;
           padding: 10px; color: #e8d5f5; white-space: pre-wrap; }
.codelang { color: #8f739f; font-size: 10px; margin-bottom: 4px; }
ul, ol { margin: 4px 0 4px 18px; padding: 0; }
li { margin: 2px 0; }
.tools { color: #8f739f; font-size: 11px; margin: 2px 6px 10px 6px; }
a { color: #ff8fe0; }
</style>
"""
