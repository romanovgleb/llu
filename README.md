# llu — live LLM usage TUI + Mac widget

Live quota/balance meters for Codex, Cursor, GLM (incl. banked 5h/weekly reset
cards via ZCode login), Kimi, DeepSeek, Groq.

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

**Reset cards:** GLM 5h/weekly banked resets need a fresh ZCode business token.
The desktop app's OAuth token expires within an hour, so llu mints one from the
chat.z.ai browser session (`token` cookie in Brave/Chrome → `POST
api.z.ai/api/auth/z/login`), cached 0600 at `~/.cache/llm-finance/zai_web_token`.

**Tray icon:** hidden by default. The gauge extra uses an `isInserted` binding so
the pump runs headless; removing a visible icon also only hides it (it used to
quit the host and freeze the widget). Re-show: open `LLUWidget` again from
Finder/Spotlight, or `open -a LLUWidget --args --reveal`. Enable launch-at-login
from that menu so the pump comes back after reboot.

Providers and their sources are documented in the pack:
`~/.agents/skills/llm-finance/REFERENCE-usage.md`.
