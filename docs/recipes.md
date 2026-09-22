# Jarvis-style recipes

Worked end-to-end examples of things the headless harness can do today.
Each recipe gives the goal, an example prompt, which tools fire, and the
safety notes that matter. Run them with the `.exe` or `python -m harness`:

```
ttacode.exe run --headless "<prompt>" --project C:\path\to\project
```

The agent loop is bounded by `max_steps` (default 50) and every file or
shell operation is jailed to the project root — but **read the safety
notes**: some of these install software or push to remotes.

---

## 1. Install Godot on Windows via winget

**Goal:** get the Godot 4 game engine installed without touching a browser.

**Example prompt:**

> Install the Godot 4 game engine on this Windows PC using winget. First
> run `winget search Godot` and confirm the exact package id from the
> results (it is believed to be `GodotEngine.Godot`, but verify — do not
> install a package you haven't confirmed). Then install it silently for
> the current user, and verify with `godot --version`.

**Tools that fire:** `run_command` (winget search → winget install → version check).

**Safety notes:**
- Installing software changes the machine: it can require admin
  consent (UAC) and downloads a large package. The recipe verifies the
  package id *before* installing so a typo can't pull the wrong package.
- Prefer `--scope user` installs when available so nothing touches
  system directories.
- If `winget` isn't on PATH (older Windows), the agent will report that
  honestly instead of guessing.

---

## 2. Run a Python script in the project

**Goal:** execute a script with the project's own interpreter.

**Example prompt:**

> In this project, run `scripts/analyze.py --input data/sample.csv`
> using the project's virtualenv if one exists (`.venv\Scripts\python.exe`
> on Windows, `.venv/bin/python` elsewhere), otherwise the system
> `python`. Show me the tail of the output.

**Tools that fire:** `list_dir` (find the venv / script), `run_command`
(python invocation with a timeout).

**Safety notes:**
- Scripts run with your user privileges and the project's files in
  reach. Don't point the harness at scripts you haven't read.
- Long-running scripts hit `shell_timeout` (default 120 s, configurable
  as `shell_timeout` in `~/.ttacode/config.json`) and are killed with
  their partial output returned — no silent hangs.
- Prefer a project venv so dependency installs don't pollute the system
  Python.

---

## 3. Code-and-push: edit, test, commit, push

**Goal:** make a code change, prove it with tests, and push — no token
limits, no artificial step caps.

**Example prompt:**

> In this project, add a `--dry-run` flag to `tools/backup.py` that
> prints what it would do without writing anything. Run the project's
> test suite (`pytest -q`), and if green, commit on a new branch
> `feature/dry-run` and push it to origin. Show me the test summary and
> the pushed branch name.

**Tools that fire:** `read_file` → `edit_file` (checkpointed — restorable
via `restore_change`) → `run_command` (pytest) → `run_command`
(`git checkout -b`, `git add`, `git commit`, `git push`).

**Safety notes:**
- `edit_file` snapshots every file before mutating it; a bad edit can
  be rolled back.
- Git pushes go to the remote configured in the repo using your stored
  credentials — the harness never asks for tokens, it uses what `git`
  already has. Pushing is the one irreversible step here: the recipe
  pushes a **new branch**, never force-pushes, and runs the tests first.
- Keep `max_steps` generous (default 50) for multi-stage flows like this.

---

## 4. Research-and-save: web search → fetch → notes file

**Goal:** answer a question from the live web and persist the findings.

**Example prompt:**

> Research how Ollama's `keep_alive` setting interacts with VRAM on
> Windows. Use web search, fetch the two most relevant pages, and write
> a concise summary to `notes/ollama-keepalive.md` with source URLs at
> the top.

**Tools that fire:** `web_search` → `web_fetch` (×2) → `write_file`.

**Safety notes:**
- Web content is untrusted input: the harness treats fetched pages as
  data, never as instructions (a page telling the agent to run commands
  is ignored as tool input).
- `web_fetch` truncates pages to a bounded size; huge pages won't blow
  up the context budget.
- The notes file lands inside the project root — the file tools refuse
  writes outside it.

---

## Writing your own recipes

A recipe is just a prompt pattern plus the tools it needs. If you find
yourself repeating one, save it as a **skill** (`~/.ttacode/skills/`,
see `docs/tool-plugins.md` for the `SKILL.md` format) so it's injected
into the system prompt on every run — or as a **tool plugin** if it needs
new capabilities (the worked "CAD to Text" example in
`docs/tool-plugins.md` shows the shape).
