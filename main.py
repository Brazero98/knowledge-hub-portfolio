import hashlib
import json
import logging
import os
import random
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import markdown as markdown_lib
from anthropic import Anthropic
from langdetect import LangDetectException, detect
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from supabase import create_client

from knowledge_mcp import BearerAuthASGI, build_mcp

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("knowledge-hub")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters["tagcolor"] = lambda tag: int(hashlib.md5(tag.encode()).hexdigest(), 16) % 6
security = HTTPBasic()

claude = Anthropic()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
APP_PASSWORD = os.environ["APP_PASSWORD"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
DIGEST_EMAIL_TO = os.environ["DIGEST_EMAIL_TO"]
MCP_TOKEN = os.environ["MCP_TOKEN"]
WEB_APP_URL = os.environ.get("WEB_APP_URL", "http://localhost:8000")
# The OG Knowledge Hub ALWAYS uses its own `entries` table — hardcoded per
# CLAUDE.md. Do NOT read the shared KNOWLEDGE_TABLE env var here: that belongs to
# the separate multi-source feeder tool, and if it were set it would silently
# drag this web app onto the wrong table (the data-mixing we just untangled).
ENTRIES_TABLE = "entries"
# "webhook" = Telegram delivers to a public URL (needs hosting). "polling" = this
# app pulls updates from Telegram itself, so phone capture works with no public
# server — just this app running locally. Set TELEGRAM_MODE=polling in .env.
TELEGRAM_MODE = os.environ.get("TELEGRAM_MODE", "webhook")


def verify_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not secrets.compare_digest(credentials.password, APP_PASSWORD):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

URL_PATTERN = re.compile(r"^https?://\S+$")
MAX_CONTENT_CHARS = 15000

LANGUAGE_NAMES = {
    "de": "Deutsch", "en": "Englisch", "fr": "Französisch", "es": "Spanisch",
    "it": "Italienisch", "pt": "Portugiesisch", "nl": "Niederländisch",
}


def detect_language(text: str) -> str:
    try:
        code = detect(text[:2000])
    except LangDetectException:
        return "der Sprache des folgenden Textes"
    return LANGUAGE_NAMES.get(code, code)


SUMMARY_PROMPT = """Du bekommst einen Artikel/Post-Text. Der Text ist auf {language} — \
deine GESAMTE Antwort (alle Textfelder) MUSS ebenfalls auf {language} sein. \
Übersetze NICHT in eine andere Sprache, selbst wenn du das für hilfreicher hältst. Extrahiere:
1. Einen kurzen Teaser (2-3 Sätze) als knappe Zusammenfassung
2. Eine ausführliche Zusammenfassung der echten Kernargumente — mehrere Absätze, mit \
Aufzählungspunkten für die zentralen Punkte, so dass jemand die Substanz des Artikels erfasst, \
ohne ihn selbst lesen zu müssen
3. 3-5 Key Learnings als kurze Stichpunkte
4. 2-4 Themen-Tags (z.B. "VC", "Startups", "Produktivität", "AI")
5. Content-Typ: article / linkedin_post / paper / note
6. Chapter — genau eines von:
   - "private": nicht arbeitsbezogen (z.B. Sport wie Formel 1/Fußball, Politik, Persönliches)
   - "work": arbeitsbezogen (VC, Startups, Business, Tech)
7. Falls chapter == "work", zusätzlich category als Array mit einem oder mehreren dieser Werte \
(meistens nur einer, aber mehrere möglich falls der Artikel klar in mehrere Bereiche passt):
   - "VC": News über VC-Deals, Funding-Runden, das Startup/VC-Ökosystem allgemein
   - "Startup": eigene Geschäftsideen, Markt-/Problem-Inspiration, Produkt-/Tech-/Coding-Themen
   - "People": über eine konkrete, inspirierende oder nützliche Person
   Falls chapter == "private", setze category auf ein leeres Array [].

Wichtig: Verwende in den Textfeldern keine doppelten Anführungszeichen ("). Falls du etwas zitieren \
willst, benutze einfache Anführungszeichen (') — sonst wird das JSON ungültig.

Antworte NUR als JSON:
{{
  "summary": "...",
  "detailed_summary": "...",
  "key_learnings": ["...", "..."],
  "tags": ["...", "..."],
  "content_type": "...",
  "chapter": "private" oder "work",
  "category": ["VC"] und/oder "Startup", "People", oder [] falls private
}}

Text:
{content}
"""


def is_url(text: str) -> bool:
    return bool(URL_PATTERN.match(text.strip()))


def extract_content(url: str) -> str:
    reader_url = f"https://r.jina.ai/{url}"
    response = httpx.get(reader_url, timeout=30)
    response.raise_for_status()
    return response.text


def summarize_content(content: str) -> dict:
    language = detect_language(content)
    prompt = SUMMARY_PROMPT.format(language=language, content=content[:MAX_CONTENT_CHARS])

    last_error = None
    for attempt in range(2):
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text.strip()
        raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"Claude-JSON ungültig (Versuch {attempt + 1}), retry: {e}")

    raise last_error


def send_telegram_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    httpx.post(url, json=payload, timeout=15)


def answer_callback_query(callback_query_id: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    httpx.post(url, json={"callback_query_id": callback_query_id}, timeout=15)


def find_duplicate(url: str | None) -> dict | None:
    if not url:
        return None
    result = (
        supabase.table(ENTRIES_TABLE)
        .select("id, created_at")
        .eq("original_url", url)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return result[0] if result else None


# in-memory holding area for captures awaiting a duplicate-confirmation reply
PENDING_CAPTURES: dict[str, dict] = {}


def send_duplicate_warning(chat_id: int, source: str, text: str, url: str, existing_date: str) -> None:
    pending_id = secrets.token_hex(4)
    PENDING_CAPTURES[pending_id] = {"source": source, "text": text}

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Trotzdem speichern", "callback_data": f"confirm:{pending_id}"},
            {"text": "❌ Abbrechen", "callback_data": f"cancel:{pending_id}"},
        ]]
    }
    message = f"⚠️ Diesen Link hast du schon am {existing_date} gespeichert:\n{url}\n\nTrotzdem nochmal speichern?"
    send_telegram_message(chat_id, message, reply_markup=keyboard)
    logger.info(f"Duplikat erkannt, warte auf Bestätigung: pending_id={pending_id}")


def send_confirmation(chat_id: int, entry: dict, source_label: str) -> None:
    tags_line = ", ".join(entry["tags"])
    confirmation = f"✅ Gespeichert{source_label}!\n\n{entry['summary']}\n\n🏷 {tags_line}"
    if entry["original_url"]:
        confirmation += f"\n🔗 {entry['original_url']}"
    send_telegram_message(chat_id, confirmation)


def capture(source: str, text: str) -> dict:
    url = text.strip() if is_url(text) else None

    if url:
        logger.info(f"URL erkannt, extrahiere Inhalt: {url}")
        content = extract_content(url)
    else:
        logger.info(f"Reiner Text erkannt: {text!r}")
        content = text

    result = summarize_content(content)
    logger.info(f"Claude-Ergebnis: {result}")

    entry = {
        "source": source,
        "original_url": url,
        "raw_text": None if url else text,
        "extracted_content": content,
        "summary": result["summary"],
        "detailed_summary": result["detailed_summary"],
        "key_learnings": result["key_learnings"],
        "tags": result["tags"],
        "content_type": result["content_type"],
        "chapter": result["chapter"],
        "category": result["category"],
    }
    supabase.table(ENTRIES_TABLE).insert(entry).execute()
    logger.info("Eintrag in Supabase gespeichert")

    return entry


def safe_capture(source: str, text: str) -> dict | None:
    try:
        return capture(source, text)
    except Exception:
        logger.exception(f"Verarbeitung fehlgeschlagen (source={source})")
        return None


def process_and_notify(source: str, text: str, chat_id: int, source_label: str = "") -> None:
    entry = safe_capture(source, text)
    if entry:
        send_confirmation(chat_id, entry, source_label)
    else:
        send_telegram_message(chat_id, "⚠️ Fehler beim Verarbeiten. Bitte nochmal teilen.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/telegram")
async def telegram_webhook(background_tasks: BackgroundTasks, request: Request):
    update = await request.json()

    callback_query = update.get("callback_query")
    if callback_query:
        answer_callback_query(callback_query["id"])
        chat_id = callback_query["message"]["chat"]["id"]
        action, _, pending_id = callback_query.get("data", "").partition(":")
        pending = PENDING_CAPTURES.pop(pending_id, None)

        if not pending:
            send_telegram_message(chat_id, "Diese Anfrage ist abgelaufen.")
        elif action == "cancel":
            send_telegram_message(chat_id, "Abgebrochen, nicht gespeichert.")
        else:
            background_tasks.add_task(process_and_notify, pending["source"], pending["text"], chat_id, "")

        return {"ok": True}

    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")

    if not text:
        return {"ok": True}

    url = text.strip() if is_url(text) else None
    duplicate = find_duplicate(url)
    if duplicate:
        send_duplicate_warning(chat_id, "telegram", text, url, duplicate["created_at"][:10])
        return {"ok": True}

    background_tasks.add_task(process_and_notify, "telegram", text, chat_id, "")
    return {"ok": True}


@app.post("/webhook/capture")
async def capture_webhook(background_tasks: BackgroundTasks, request: Request):
    body = await request.json()
    text = body.get("text")

    if not text:
        return {"ok": False, "error": "Feld 'text' fehlt"}

    url = text.strip() if is_url(text) else None
    duplicate = find_duplicate(url)
    if duplicate:
        send_duplicate_warning(TELEGRAM_CHAT_ID, "ios_shortcut", text, url, duplicate["created_at"][:10])
        return {"ok": True, "duplicate": True}

    background_tasks.add_task(process_and_notify, "ios_shortcut", text, TELEGRAM_CHAT_ID, " (iOS)")
    return {"ok": True}


SECTIONS = [
    {"chapter": "private", "category": None, "title": "Private", "icon": "🏄🏻‍♂️", "nav_key": "private"},
    {"chapter": "work", "category": "VC", "title": "VC", "icon": "💰", "nav_key": "VC"},
    {"chapter": "work", "category": "Startup", "title": "Startup", "icon": "🛠️", "nav_key": "Startup"},
    {"chapter": "work", "category": "People", "title": "People", "icon": "🧑‍🤝‍🧑", "nav_key": "People"},
]


def matches(entry: dict, chapter: str | None, category: str | None) -> bool:
    if chapter and entry.get("chapter") != chapter:
        return False
    if category and category not in (entry.get("category") or []):
        return False
    return True


def send_email(subject: str, html: str) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": "Knowledge Hub <onboarding@resend.dev>",
            "to": [DIGEST_EMAIL_TO],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    response.raise_for_status()


def build_digest_html(sections: list[dict]) -> str:
    parts = [
        "<div style='font-family: -apple-system, sans-serif; max-width: 600px; "
        "margin: 0 auto; color: #37352f;'>",
        "<h1 style='font-size: 22px;'>📚 Dein Knowledge Hub — Wochenrückblick</h1>",
    ]

    for section in sections:
        if not section["entries"]:
            continue
        parts.append(
            f"<h2 style='font-size: 17px; margin-top: 28px; border-bottom: 1px solid #e9e9e7; "
            f"padding-bottom: 6px;'>{section['icon']} {section['title']}</h2>"
        )
        for e in section["entries"]:
            tags_line = ", ".join(e.get("tags") or [])
            parts.append(
                f"<div style='margin: 14px 0; padding: 12px 14px; border: 1px solid #e9e9e7; "
                f"border-radius: 8px;'>"
                f"<p style='margin: 0 0 8px; line-height: 1.5;'>{e['summary']}</p>"
                f"<p style='margin: 0 0 6px; font-size: 12px; color: #9b9a97;'>{tags_line}</p>"
                f"<a href='{WEB_APP_URL}/app/entry/{e['id']}' style='font-size: 13px; "
                f"color: #0b6e99; text-decoration: none;'>Mehr erfahren →</a>"
                f"</div>"
            )

    parts.append(
        f"<p style='margin-top: 32px; font-size: 12px;'>"
        f"<a href='{WEB_APP_URL}/app' style='color: #9b9a97;'>Alle Einträge ansehen</a></p>"
    )
    parts.append("</div>")
    return "".join(parts)


def build_resurface_html(entries: list[dict]) -> str:
    parts = [
        "<div style='font-family: -apple-system, sans-serif; max-width: 600px; "
        "margin: 0 auto; color: #37352f;'>",
        "<h1 style='font-size: 22px;'>📚 Aus deiner Library</h1>",
        "<p style='color: #6b6a67; line-height: 1.5;'>Nichts Neues diese Woche — hier sind ein "
        "paar Einträge aus deiner Sammlung, die einen zweiten Blick lohnen:</p>",
    ]
    for e in entries:
        tags_line = ", ".join(e.get("tags") or [])
        saved = (e.get("created_at") or "")[:10]
        meta = tags_line + (f" · gespeichert {saved}" if saved else "")
        parts.append(
            f"<div style='margin: 14px 0; padding: 12px 14px; border: 1px solid #e9e9e7; "
            f"border-radius: 8px;'>"
            f"<p style='margin: 0 0 8px; line-height: 1.5;'>{e['summary']}</p>"
            f"<p style='margin: 0 0 6px; font-size: 12px; color: #9b9a97;'>{meta}</p>"
            f"<a href='{WEB_APP_URL}/app/entry/{e['id']}' style='font-size: 13px; "
            f"color: #0b6e99; text-decoration: none;'>Mehr erfahren →</a>"
            f"</div>"
        )
    parts.append(
        f"<p style='margin-top: 32px; font-size: 12px;'>"
        f"<a href='{WEB_APP_URL}/app' style='color: #9b9a97;'>Ganze Library ansehen</a></p>"
    )
    parts.append("</div>")
    return "".join(parts)


def send_weekly_digest() -> None:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_entries = (
        supabase.table(ENTRIES_TABLE)
        .select("*")
        .gte("created_at", since)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")

    # New captures this week → the classic "saved this week" digest.
    if recent_entries:
        sections = [
            {"title": s["title"], "icon": s["icon"],
             "entries": [e for e in recent_entries if matches(e, s["chapter"], s["category"])]}
            for s in SECTIONS
        ]
        send_email(f"📚 Knowledge Hub Wochenrückblick — {today}", build_digest_html(sections))
        logger.info(f"Weekly Digest (diese Woche) versendet ({len(recent_entries)} Einträge)")
        return

    # Quiet week → resurface a few older entries so the library stays alive.
    library = supabase.table(ENTRIES_TABLE).select("*").order("created_at", desc=True).execute().data
    if not library:
        logger.info("Weekly Digest: Library leer, kein Versand")
        return
    resurfaced = random.sample(library, min(5, len(library)))
    send_email(f"📚 Aus deiner Library — {today}", build_resurface_html(resurfaced))
    logger.info(f"Weekly Digest (resurfacing) versendet ({len(resurfaced)} Einträge)")


@app.get("/app", response_class=HTMLResponse)
def app_index(
    request: Request,
    chapter: str | None = None,
    category: str | None = None,
    q: str | None = None,
    user: str = Depends(verify_auth),
):
    all_entries = supabase.table(ENTRIES_TABLE).select("*").order("created_at", desc=True).execute().data
    all_tags = sorted({t for e in all_entries for t in (e.get("tags") or [])})

    if q:
        q_lower = q.lower()
        entries = [
            e for e in all_entries
            if q_lower in (e.get("summary") or "").lower()
            or q_lower in (e.get("raw_text") or "").lower()
            or any(q_lower in t.lower() for t in (e.get("tags") or []))
        ]
        return templates.TemplateResponse(
            request,
            "index.html",
            {"mode": "list", "entries": entries, "list_title": f'Suche: "{q}"', "nav_active": None, "q": q, "all_tags": all_tags},
        )

    if chapter or category:
        entries = [e for e in all_entries if matches(e, chapter, category)]
        section = next((s for s in SECTIONS if s["chapter"] == chapter and s["category"] == category), None)
        if section:
            list_title, nav_active = f"{section['icon']} {section['title']}", section["nav_key"]
        elif chapter == "work":
            list_title, nav_active = "🚀 Work", "work"
        else:
            list_title, nav_active = "Ergebnisse", None
        return templates.TemplateResponse(
            request,
            "index.html",
            {"mode": "list", "entries": entries, "list_title": list_title, "nav_active": nav_active, "q": None, "all_tags": all_tags},
        )

    sections = []
    for s in SECTIONS:
        matching = [e for e in all_entries if matches(e, s["chapter"], s["category"])]
        sections.append({
            "title": s["title"],
            "icon": s["icon"],
            "entries": matching[:4],
            "see_all_url": f"/app?chapter={s['chapter']}" + (f"&category={s['category']}" if s["category"] else ""),
        })

    return templates.TemplateResponse(
        request,
        "index.html",
        {"mode": "home", "sections": sections, "nav_active": "home", "q": None, "all_tags": all_tags},
    )


@app.get("/app/entry/{entry_id}", response_class=HTMLResponse)
def app_entry(request: Request, entry_id: str, user: str = Depends(verify_auth)):
    entry = supabase.table(ENTRIES_TABLE).select("*").eq("id", entry_id).single().execute().data
    detailed_html = markdown_lib.markdown(entry.get("detailed_summary") or "")

    return templates.TemplateResponse(
        request,
        "entry.html",
        {"entry": entry, "detailed_html": detailed_html, "nav_active": None, "q": None, "all_tags": []},
    )


@app.post("/app/entry/{entry_id}/delete")
def delete_entry(entry_id: str, user: str = Depends(verify_auth)):
    supabase.table(ENTRIES_TABLE).delete().eq("id", entry_id).execute()
    return RedirectResponse(url="/app", status_code=303)


@app.post("/app/entry/{entry_id}/toggle-category")
def toggle_category(entry_id: str, category: str = Form(...), user: str = Depends(verify_auth)):
    entry = supabase.table(ENTRIES_TABLE).select("category").eq("id", entry_id).single().execute().data
    current = entry.get("category") or []
    if category in current:
        current.remove(category)
    else:
        current.append(category)
    supabase.table(ENTRIES_TABLE).update({"category": current}).eq("id", entry_id).execute()
    return RedirectResponse(url=f"/app/entry/{entry_id}", status_code=303)


@app.post("/app/entry/{entry_id}/add-category")
def add_category(entry_id: str, category: str = Form(...), user: str = Depends(verify_auth)):
    entry = supabase.table(ENTRIES_TABLE).select("category").eq("id", entry_id).single().execute().data
    current = entry.get("category") or []
    if category not in current:
        current.append(category)
        supabase.table(ENTRIES_TABLE).update({"category": current}).eq("id", entry_id).execute()
    return {"ok": True, "category": current}


@app.post("/app/send-digest-now")
def send_digest_now(background_tasks: BackgroundTasks, user: str = Depends(verify_auth)):
    background_tasks.add_task(send_weekly_digest)
    return {"ok": True}


# The weekly newsletter is triggered externally (macOS launchd runs
# send_digest.py on a schedule and survives the laptop sleeping). The in-app
# APScheduler was removed so there is no duplicate send; the "send digest now"
# button still calls send_weekly_digest() directly.


def handle_telegram_update(update: dict) -> None:
    """Process one Telegram update synchronously (used by the polling loop)."""
    callback_query = update.get("callback_query")
    if callback_query:
        answer_callback_query(callback_query["id"])
        chat_id = callback_query["message"]["chat"]["id"]
        action, _, pending_id = callback_query.get("data", "").partition(":")
        pending = PENDING_CAPTURES.pop(pending_id, None)
        if not pending:
            send_telegram_message(chat_id, "Diese Anfrage ist abgelaufen.")
        elif action == "cancel":
            send_telegram_message(chat_id, "Abgebrochen, nicht gespeichert.")
        else:
            process_and_notify(pending["source"], pending["text"], chat_id, "")
        return

    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")
    if not text:
        return

    url = text.strip() if is_url(text) else None
    duplicate = find_duplicate(url)
    if duplicate:
        send_duplicate_warning(chat_id, "telegram", text, url, duplicate["created_at"][:10])
        return
    process_and_notify("telegram", text, chat_id, "")


def telegram_poll_loop() -> None:
    """Long-poll Telegram so phone capture works with no public URL.

    Each update is handled in its own thread so a slow capture (Jina + Claude)
    never blocks the loop. Telegram queues messages for 24h, so links shared
    while this app is down are still picked up on the next poll.
    """
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        # getUpdates and a webhook are mutually exclusive — drop the stale
        # (dead Railway) webhook so polling can take over.
        httpx.get(f"{base}/deleteWebhook", timeout=15)
    except Exception:
        logger.exception("deleteWebhook failed (continuing)")
    logger.info("Telegram polling gestartet")
    offset = None
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            resp = httpx.get(f"{base}/getUpdates", params=params, timeout=65)
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                threading.Thread(target=handle_telegram_update, args=(update,), daemon=True).start()
        except Exception:
            logger.exception("Telegram-Polling-Fehler, retry in 5s")
            time.sleep(5)


def supabase_keepalive_loop() -> None:
    """Ping Supabase every 6h so the free-tier project never auto-pauses.

    The daily feeder that used to keep this project warm moved to its own repo,
    so the OG hub now hits Supabase only on captures / page loads — not enough to
    beat the ~7-day idle pause. This tiny read keeps the timer from tripping.
    Sleeps with the Mac and resumes on wake.
    """
    while True:
        try:
            supabase.table(ENTRIES_TABLE).select("id").limit(1).execute()
            logger.info("Supabase keep-alive ping ok")
        except Exception:
            logger.exception("Supabase keep-alive ping failed")
        time.sleep(6 * 3600)


# --- Read-only MCP connector (Streamable HTTP) mounted in this same app ---
# Reuses the existing `supabase` client. Bearer-token gated. Served at /mcp.
mcp_server, mcp_asgi = build_mcp(supabase, mount_path="/mcp", table=ENTRIES_TABLE)


@asynccontextmanager
async def _mcp_lifespan(_app):
    if TELEGRAM_MODE == "polling":
        threading.Thread(target=telegram_poll_loop, daemon=True).start()
    threading.Thread(target=supabase_keepalive_loop, daemon=True).start()
    async with mcp_server.session_manager.run():
        yield


# The existing FastAPI app was created without a lifespan; attach one so the MCP
# session manager runs. Mounted sub-app lifespans are not auto-run by Starlette.
app.router.lifespan_context = _mcp_lifespan

# Mount at root with the auth wrapper; FastAPI's own routes are registered first
# and take precedence, so only /mcp falls through to the connector.
app.mount("/", BearerAuthASGI(mcp_asgi, MCP_TOKEN))
