import json
import os
import re

from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

claude = Anthropic()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

CLASSIFY_PROMPT = """Du bekommst die Zusammenfassung und Tags eines gespeicherten Artikels. Klassifiziere:
1. Chapter — genau eines von:
   - "private": nicht arbeitsbezogen (z.B. Sport wie Formel 1/Fußball, Politik, Persönliches)
   - "work": arbeitsbezogen (VC, Startups, Business, Tech)
2. Falls chapter == "work", zusätzlich category — genau eines von:
   - "VC": News über VC-Deals, Funding-Runden, das Startup/VC-Ökosystem allgemein
   - "Startup": eigene Geschäftsideen, Markt-/Problem-Inspiration, Produkt-/Tech-/Coding-Themen
   - "People": über eine konkrete, inspirierende oder nützliche Person
   Falls chapter == "private", setze category auf null.

Antworte NUR als JSON: {{"chapter": "private" oder "work", "category": "VC", "Startup", "People" oder null}}

Zusammenfassung: {summary}
Tags: {tags}
Content-Typ: {content_type}
"""


def classify(entry: dict) -> dict:
    prompt = CLASSIFY_PROMPT.format(
        summary=entry.get("summary") or "",
        tags=", ".join(entry.get("tags") or []),
        content_type=entry.get("content_type") or "",
    )
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text.strip()
    raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text, flags=re.MULTILINE).strip()
    return json.loads(raw_text)


def main():
    entries = supabase.table("entries").select("id, summary, tags, content_type").is_("chapter", "null").execute().data
    print(f"{len(entries)} Einträge ohne chapter gefunden")

    for entry in entries:
        result = classify(entry)
        supabase.table("entries").update(
            {"chapter": result["chapter"], "category": result.get("category")}
        ).eq("id", entry["id"]).execute()
        print(f"{entry['id']}: {result}")


if __name__ == "__main__":
    main()
