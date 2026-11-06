"""Read-only MCP connector for the Knowledge Hub.

Exposes saved entries to Claude as a small set of narrow, named, read-only
tools over the MCP Streamable HTTP transport. Reuses the app's existing
Supabase client (the single data path) — no second connection, no writes.
"""

import secrets

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

# Compact shape for scans (teaser + metadata only, keeps token cost down).
COMPACT_COLS = "id, summary, tags, chapter, category, original_url, created_at"
# Full shape, only ever returned by get_entry.
FULL_COLS = (
    "id, summary, detailed_summary, key_learnings, tags, "
    "chapter, category, original_url, created_at"
)

INSTRUCTIONS = (
    "Read-only access to your personal knowledge base of saved articles and posts. "
    "Each entry has a short teaser, a longer structured summary, key learnings, tags, "
    "a chapter (private or work) and, for work, one or more categories (VC, Startup, People). "
    "Use search_knowledge or list_recent to scan compact records (teaser + metadata), then "
    "call get_entry with an id to read the full summary and key learnings of a specific entry."
)


def _compact(row: dict) -> dict:
    return {
        "id": row["id"],
        "teaser": row.get("summary"),
        "tags": row.get("tags") or [],
        "chapter": row.get("chapter"),
        "category": row.get("category") or [],
        "url": row.get("original_url"),
        "created_at": row.get("created_at"),
    }


def _full(row: dict) -> dict:
    return {
        "id": row["id"],
        "teaser": row.get("summary"),
        "summary": row.get("detailed_summary"),
        "key_learnings": row.get("key_learnings") or [],
        "tags": row.get("tags") or [],
        "chapter": row.get("chapter"),
        "category": row.get("category") or [],
        "url": row.get("original_url"),
        "created_at": row.get("created_at"),
    }


def build_mcp(supabase, mount_path: str = "/mcp", table: str = "entries"):
    """Return (MCPServer, asgi_app) — the asgi_app is the Streamable HTTP app.

    Auth is layered on top by the caller (bearer-token ASGI wrapper), so the
    protocol app itself stays simple.
    """
    mcp = MCPServer("Knowledge Hub", instructions=INSTRUCTIONS)

    @mcp.tool()
    def search_knowledge(
        query: str,
        chapter: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        since: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search saved knowledge entries by free text, returning compact records.

        query: text matched (case-insensitive substring) against the teaser and the
            long summary.
        chapter: optional filter, 'private' or 'work'.
        category: optional filter, one of 'VC', 'Startup', 'People' (work entries).
        tags: optional list; entries overlapping any of these tags are returned.
        since: optional ISO date/datetime lower bound on created_at (e.g. '2026-07-01').
        limit: max records (capped at 50). Returns {id, teaser, tags, chapter,
            category, url, created_at}. Use get_entry(id) for the full summary.
        """
        q = supabase.table(table).select(COMPACT_COLS)
        if query:
            pattern = f"%{query}%"
            q = q.or_(f"summary.ilike.{pattern},detailed_summary.ilike.{pattern}")
        if chapter:
            q = q.eq("chapter", chapter)
        if category:
            q = q.contains("category", [category])
        if tags:
            q = q.overlaps("tags", tags)
        if since:
            q = q.gte("created_at", since)
        rows = q.order("created_at", desc=True).limit(min(limit, 50)).execute().data
        return [_compact(r) for r in rows]

    @mcp.tool()
    def list_recent(
        since: str | None = None,
        chapter: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List the most recently saved entries, newest first, as compact records.

        since: optional ISO date/datetime lower bound on created_at.
        chapter: optional filter, 'private' or 'work'.
        category: optional filter, one of 'VC', 'Startup', 'People'.
        limit: max records (capped at 50). Same shape as search_knowledge.
        """
        q = supabase.table(table).select(COMPACT_COLS)
        if since:
            q = q.gte("created_at", since)
        if chapter:
            q = q.eq("chapter", chapter)
        if category:
            q = q.contains("category", [category])
        rows = q.order("created_at", desc=True).limit(min(limit, 50)).execute().data
        return [_compact(r) for r in rows]

    @mcp.tool()
    def get_entry(id: str) -> dict:
        """Fetch the full content of one entry by its id.

        Returns {id, teaser, summary, key_learnings, tags, chapter, category, url,
        created_at}, where `summary` is the long structured summary. Returns
        {"error": "not found"} if no entry has that id.
        """
        rows = (
            supabase.table(table).select(FULL_COLS).eq("id", id).limit(1).execute().data
        )
        if not rows:
            return {"error": "not found"}
        return _full(rows[0])

    # Behind Railway's proxy the Host header is the platform domain, and the bearer
    # token below is the real access gate, so the SDK's DNS-rebinding host check is
    # turned off (it would otherwise reject Claude's cloud requests).
    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    asgi_app = mcp.streamable_http_app(
        streamable_http_path=mount_path,
        json_response=True,
        transport_security=transport_security,
    )
    return mcp, asgi_app


class BearerAuthASGI:
    """ASGI wrapper: require `Authorization: Bearer <token>` on the guarded path.

    Only requests under `guard_prefix` are gated; anything else is delegated
    untouched so it keeps its normal (404) behaviour.
    """

    def __init__(self, app, token: str, guard_prefix: str = "/mcp"):
        self.app = app
        self.token = token
        self.guard_prefix = guard_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.guard_prefix):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode()
        if secrets.compare_digest(provided, f"Bearer {self.token}"):
            await self.app(scope, receive, send)
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})


def _run_stdio() -> None:
    """Run the OG hub's ARTICLE connector as a local stdio MCP server.

    Registered with `claude mcp add knowledge-hub -- <venv python> knowledge_mcp.py`.
    Reads ONLY the OG hub's `entries` table via SUPABASE_* — never the feeder's
    data. (The feeder tool has its own connector, knowledge_mcp_local.py.)
    """
    import os

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv()
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    mcp, _ = build_mcp(sb, table="entries")
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    _run_stdio()
