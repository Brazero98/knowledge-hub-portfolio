# Knowledge Hub

A personal "second brain" for what you read: share an article with one tap and it's
fetched, summarized, tagged, and filed into a searchable library you'll actually
revisit — with a weekly email digest and a Claude (MCP) connector over everything saved.

## The idea

Most people save interesting articles and never go back to them. Knowledge Hub drives
the cost of *capturing* to near zero, then does the *re-surfacing* for you. You share a
link; the system turns it into a structured, searchable entry and keeps nudging it back
into view.

## Architecture — one flow, four stages

```
  Capture ──▶ Summarize ──▶ Store ──▶ Surface
 (Telegram     (Claude:       (Postgres)   ├─ Web library (browse / search / filter)
  bot /         teaser +                    ├─ Weekly email digest
  Shortcut)     long summary +              └─ MCP connector (ask Claude across it all)
                key learnings +
                tags + classification)
```

- **Capture** — one endpoint accepts a URL or text from two sources: a Telegram bot
  (polling mode, so no public server is required) and an iOS/Mac share-sheet Shortcut.
- **Summarize** — the article text is extracted, then Claude returns a short teaser, a
  longer structured summary, key learnings, topic tags, and a two-level classification
  (chapter + category) — in the article's original language.
- **Store** — everything lands in one Postgres table: the single source of truth.
- **Surface** — three read surfaces over the same data: a Notion-style web library, a
  weekly digest email (which resurfaces older entries on quiet weeks), and a read-only
  MCP connector so you can query your whole archive from Claude.

## Components

| File | Role |
|---|---|
| `main.py` | FastAPI app — capture pipeline, web library, Telegram polling, newsletter, mounted MCP |
| `knowledge_mcp.py` | Read-only MCP connector (HTTP + stdio) over the saved entries |
| `send_digest.py` | Standalone weekly newsletter sender |
| `templates/`, `static/` | The web UI |
| `backfill_*.py`, `dedupe_entries.py` | One-off maintenance scripts |

## Design principles

- **Friction-free capture, deliberate re-surfacing** — the whole point is to make saving
  cost nothing and let the system do the remembering.
- **One input, many surfaces** — a single capture path and a single table feed every
  reader (web, email, MCP); no duplicated pipelines.
- **Runs on one machine, no paid hosting** — designed to run entirely locally (plus a
  free-tier database), so it costs nothing to keep alive.
- **The model earns its place** — an LLM is used only where it genuinely beats plain
  code (summarizing and tagging); everything else is boring, explicit logic.

## Running it

Needs Python 3.11+, a Postgres database (e.g. a free Supabase project), an Anthropic API
key, and optionally a Telegram bot and an email-sending key.

1. Copy `.env.example` to `.env` and fill in the values.
2. `pip install -r requirements.txt`
3. `uvicorn main:app` → the library is at `http://localhost:8000/app`.

See `CLAUDE.md` for the full component and operations map.

## Status & honest limits

- **Status:** working, **single-user**, used daily by its author — not hardened for
  multi-user or production.
- **Minimal auth** — HTTP basic auth on the web UI and a bearer token on the MCP
  endpoint. Fine for a personal tool; not for exposing anything sensitive.
- **Tags are LLM-generated and free-form**, so they drift (near-duplicates like
  "AI" / "AI agents"). A controlled vocabulary would fix it.
- **Free-tier storage pauses when idle** and needs the occasional manual resume; a
  keep-alive mitigates but doesn't fully prevent it.
- **Capture quality depends on article extraction** — paywalled or JS-heavy pages
  extract poorly.
- No automated test suite yet.
