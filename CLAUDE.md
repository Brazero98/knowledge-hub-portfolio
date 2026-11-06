# Knowledge Hub repo — guide for Claude

The **OG Knowledge Hub**: capture articles, auto-summarize them with Claude,
browse them in a local web app, get a weekly email newsletter, and query them
from Claude via an MCP connector. Runs entirely on your Mac — no paid hosting.

## Data — one project, one table (hardcoded)

- Supabase project ref `YOUR_SUPABASE_REF`, table **`entries`** (your saved
  articles). `main.py` **hardcodes** `entries` — never read a configurable table
  name here, so a shared env var can never drag the web app onto another table.
- Credentials: `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` in `.env` (gitignored).

## Components

| File | Role |
|------|------|
| `main.py` | FastAPI app: `/app` web library (HTTP basic auth), capture pipeline, Telegram polling, weekly newsletter, mounted MCP at `/mcp`. |
| `templates/`, `static/` | The Notion-style web UI. |
| `knowledge_mcp.py` | Read-only MCP connector over `entries` — mounted in `main.py` (HTTP) **and** a stdio entrypoint for `claude mcp add knowledge-hub`. |
| `send_digest.py` | Standalone weekly newsletter sender (run by launchd). |
| `backfill_*.py`, `dedupe_entries.py` | One-off maintenance scripts for `entries`. |

## Capture paths (both write to `entries`)

- **Telegram bot** — polling mode (`TELEGRAM_MODE=polling`): send a link to the
  bot and the locally-running app pulls it in. No public server needed.
- **iOS / Mac Shortcut** — POSTs a link to `/webhook/capture`. Needs a public URL
  for the Mac to be reachable from the phone (an ngrok tunnel, run always-on).

## Always-on (macOS launchd)

- `com.example.knowledgehub.webapp` → runs the web app 24/7 (wrapper in
  `~/Library/Application Support/KnowledgeHubWeb/`, logs in `~/Library/Logs/knowledgehub/`).
- `com.example.knowledgehub-newsletter` → weekly digest via `send_digest.py`.
- `com.example.knowledgehub.ngrok` → public tunnel for the Shortcut (if set up).

