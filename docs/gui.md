# TTACode desktop app (`ttacode.exe`)

The average-user launcher: double-click, chat, done. No flags, no
terminal. Every message runs the full harness agent loop — the same
shell, file, web, and MCP tools the headless CLI uses.

## What it does

- **Chat** — type and hit SEND (or Enter). The agent thinks, uses tools,
  and answers. Each turn shows which tools it used.
- **Images** — drag & drop an image (or hit `+`) and ask about it. Needs
  a vision-capable model (see below).
- **Model switcher** — dropdown in the top bar lists your installed
  Ollama models with sizes and "fits VRAM" hints. Picking one warms it
  into VRAM.
- **Project folder** — the `Project` button sets the working folder for
  file/shell tools. Ask for a file and the agent builds it there.
- **New chat** — clears the conversation.

## Vision in one click

If you attach an image while the current model can't see:

1. The app checks for an installed vision model and switches to it
   automatically, **or**
2. shows an install card: **one click** runs `ollama pull` with a live
   progress bar, switches to the new model, and sends your message.

No surprise downloads — the install only ever starts when you click the
button. Power users can change the offered model with the `vision_model`
key in `%USERPROFILE%\.ttacode\config.json` (default `qwen2.5vl:7b`).

## First run

1. Install [Ollama](https://ollama.com/download/windows) and pull a
   model: `ollama pull qwen2.5-coder:7b`
2. Download `ttacode.exe` from the
   [releases](https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases/latest)
   and double-click it.

The app warms the model once (first launch takes a bit), then you're
chatting. If Ollama isn't running it tells you exactly what to do —
it never downloads models on its own.

## From source

`python -m harness gui` (needs `pip install PySide6==6.8.3`).
