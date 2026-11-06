# Knowledge Hub — MVP Spec (Stufe 1)
> Für Claude Code. Ziel: funktionierendes Capture + Auto-Summary System, lauffähig in 1-2 Tagen.

---

## Kontext / Warum

Alex sammelt interessante Artikel/Posts (LinkedIn, Instagram, Paper) aber kehrt nie zurück, um sie zu vertiefen. Ziel dieser Stufe 1: ein System, das Inhalte mit minimalem Friction erfasst (Handy + Mac), automatisch zusammenfasst und Key Learnings extrahiert — als Fundament für einen späteren Weekly Digest und eine durchsuchbare Web-App (Stufe 2-4, separat).

**Kein Scope für Stufe 1:** kein UI/Web-App, kein Weekly-Digest-Versand, kein PDF-Upload-Flow. Nur: Input rein → Verarbeitung → Datenbank. Diese Stufe muss für sich allein schon nützlich sein (Summary per Telegram zurück).

---

## Architektur-Überblick

```
[iOS Share Sheet Shortcut]  ─┐
                              ├──> [Webhook Endpoint] ──> [Claude API: Summary + Tags] ──> [Datenbank]
[Telegram Bot]  ─────────────┘                                                                  │
                                                                                                   └──> [Bestätigung zurück an User via Telegram]
```

Ein Endpoint nimmt beide Quellen entgegen (gleiches Datenformat), damit die Verarbeitung nicht dupliziert werden muss.

---

## Tech-Stack (kostengünstig, schnell aufgesetzt)

| Komponente | Wahl | Warum |
|---|---|---|
| Backend/Webhook | Node.js (Express) oder Python (FastAPI) | Claude Code kann beides gut; FastAPI leicht lesbar |
| Datenbank | Supabase (Postgres, Free Tier) | Kostenlos bis 500MB, sofort startklar, kein eigenes Hosting nötig |
| Hosting Backend | Railway oder Render (Free/Hobby Tier) | Einfaches Deployment, geringe Kosten |
| Telegram Bot | Telegram Bot API (kostenlos) | Kein Scraping, offiziell unterstützt, läuft auf Handy + Mac (Telegram Desktop) |
| iOS Capture | Apple Shortcuts (Share Sheet Extension) | Nativ, kostenlos, funktioniert auf iPhone; iOS Shortcuts sind über iCloud auch am Mac nutzbar |
| Summarization | Anthropic API (Claude) | Bereits vorhanden/gewohnt |
| Content-Extraktion (Links) | z.B. `readability` / `trafilatura` (Python) oder Jina AI Reader API (kostenlos, einfach) | Um Artikeltext aus einer URL zu ziehen, bevor er an Claude geht |

**Kostenschätzung:** Bei diesem Volumen (persönliche Nutzung) bewegt sich das im Bereich von wenigen Euro/Monat oder bleibt im Free-Tier. Passt zu Budget-Prinzip "Runway schonen."

---

## Datenmodell (Supabase / Postgres)

```sql
create table entries (
  id uuid primary key default gen_random_uuid(),
  created_at timestamp with time zone default now(),
  source text not null,              -- 'telegram' | 'ios_shortcut'
  original_url text,                 -- falls Link
  raw_text text,                     -- falls direkt eingefügter Text/Notiz
  extracted_content text,            -- extrahierter Artikeltext (vor Summary)
  summary text,                      -- Claude-generierte Zusammenfassung
  key_learnings text[],              -- Array von Kernpunkten
  tags text[],                       -- automatisch vergebene Themen-Tags
  status text default 'new',         -- 'new' | 'reviewed' | 'archived'
  content_type text                  -- 'article' | 'linkedin_post' | 'paper' | 'note'
);
```

Bewusst schlank gehalten — Stufe 2 (Web-App) baut direkt darauf auf, ohne Migration.

---

## Verarbeitungslogik (Kernstück)

1. **Eingang:** Webhook empfängt `{ source, content (URL oder Text), telegram_chat_id }`
2. **Content-Typ erkennen:** Ist es eine URL? → Content-Extraktion (Jina Reader oder trafilatura). Ist es reiner Text? → direkt weiter.
3. **An Claude API senden** mit einem Prompt in etwa dieser Form:

```
Du bekommst einen Artikel/Post-Text. Extrahiere:
1. Eine 2-3 Satz Zusammenfassung
2. 3-5 Key Learnings als Stichpunkte
3. 2-4 Themen-Tags (z.B. "VC", "Startups", "Produktivität", "AI")
4. Content-Typ: article / linkedin_post / paper / note

Antworte NUR als JSON:
{
  "summary": "...",
  "key_learnings": ["...", "..."],
  "tags": ["...", "..."],
  "content_type": "..."
}

Text:
{{extracted_content}}
```

4. **In Datenbank schreiben** (Supabase Insert)
5. **Bestätigung an Telegram zurücksenden:** kurze Zusammenfassung + Tags, damit sofortiges Feedback da ist ("gespeichert ✅")

---

## Schritt-für-Schritt Build-Reihenfolge (für Claude Code)

**Schritt 1 — Projekt-Setup**
- Node/Python-Projekt initialisieren
- Supabase-Projekt anlegen (Alex macht das manuell auf supabase.com, gibt Claude Code die Connection-Keys als `.env`)
- Tabelle `entries` anlegen (SQL oben)

**Schritt 2 — Telegram Bot**
- Bot via BotFather in Telegram erstellen (Alex macht das manuell, 2 Minuten, gibt Token an Claude Code)
- Webhook-Endpoint bauen, der Telegram-Nachrichten empfängt
- Test: Nachricht an Bot senden → landet im Log

**Schritt 3 — Content-Extraktion**
- Bei URL: Jina Reader (`https://r.jina.ai/{url}`, kostenlos, kein Setup) oder trafilatura einbauen
- Test: URL rein → sauberer Artikeltext raus

**Schritt 4 — Claude API Integration**
- Prompt wie oben einbauen
- JSON-Response parsen
- Test: Text rein → strukturiertes JSON raus

**Schritt 5 — Datenbank-Schreiben**
- Supabase-Client einbinden
- Eintrag speichern
- Test: Eintrag erscheint in Supabase Table Editor

**Schritt 6 — Bestätigung zurück**
- Telegram-Antwort mit Summary + Tags senden
- Test: Voller Kreislauf funktioniert end-to-end

**Schritt 7 — iOS Shortcut**
- Shortcut bauen: "Text/URL teilen" → POST Request an denselben Webhook-Endpoint (gleiches JSON-Format wie Telegram)
- Test: Von LinkedIn/Insta teilen → landet in Datenbank

**Schritt 8 — Deployment**
- Backend auf Railway/Render deployen (damit Webhook dauerhaft erreichbar ist, nicht nur lokal)
- Umgebungsvariablen (.env) im Hosting-Dashboard setzen

---

## Was Claude Code von Alex braucht, um zu starten

- [ ] Supabase-Projekt-URL + API-Key
- [ ] Telegram Bot-Token (via BotFather)
- [ ] Anthropic API-Key
- [ ] Präferenz: Node.js oder Python fürs Backend?
- [ ] Präferenz: Railway oder Render fürs Hosting?

---

## Offene Punkte für spätere Stufen (nicht jetzt bauen)

- Stufe 2: Web-App zum Durchsuchen/Filtern nach Tags
- Stufe 3: Weekly Digest E-Mail (Cron-Job über bestehende Datenbank)
- Stufe 4: PDF-Upload + Volltext-Extraktion für lange Paper
- Mögliche Verknüpfung ähnlicher Einträge (Embeddings/Vector Search) — bewusst zurückgestellt, da Stufe 1 erstmal im Alltag getestet werden soll, bevor mehr Komplexität reinkommt (passt zu "Decision-Relevance Test": erst prüfen ob das Grundgerüst trägt)

---

## Hinweis zur Übergabe an Claude Code

Diese Datei 1:1 in den Projekt-Ordner legen (z.B. `SPEC.md`) und Claude Code bitten:
> "Lies SPEC.md und setze Schritt 1-6 um. Frage nach, falls Zugangsdaten fehlen."

Claude Code sollte dann iterativ durch die Schritte gehen, nicht alles auf einmal schreiben.
