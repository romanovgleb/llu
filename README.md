# llu — live LLM usage TUI + Mac widget

Live quota/balance meters for Codex, Cursor, GLM, Kimi, DeepSeek, Groq.

```bash
llu                 # live, 60s refresh
llu --once
llu --json          # machine output: {fetched_at, providers:[{name,ok,pct,detail,kind,amount}]}
```

Install: symlink `llu` somewhere on `PATH` (the agents-skills pack `install.sh`
does this when `~/base/code/llu` exists). Needs Python 3 with `rich`
(the pack venv is auto-detected).

## Mac widget

`widget/` holds a native macOS WidgetKit widget (Notification Center /
desktop) that renders the same numbers. The unsandboxed menu-bar pump app runs
`llu --json` every 60 s and writes the snapshot into the widget extension's
sandbox container (`~/Library/Containers/ru.romanovgleb.llu.widget/Data/...`),
then reloads the widget timeline. Build: `widget/build.sh` (requires full
Xcode; the extension is sandboxed — macOS 26 requirement).

Providers and their sources are documented in the pack:
`~/.agents/skills/llm-finance/REFERENCE-usage.md`.
