from main import summarize_content, supabase


def main():
    entries = (
        supabase.table("entries")
        .select("id, extracted_content")
        .is_("detailed_summary", "null")
        .execute()
        .data
    )
    print(f"{len(entries)} Einträge ohne detailed_summary gefunden")

    for entry in entries:
        content = entry.get("extracted_content")
        if not content:
            print(f"{entry['id']}: kein extracted_content, übersprungen")
            continue

        try:
            result = summarize_content(content)
        except Exception as e:
            print(f"{entry['id']}: Fehler, übersprungen ({e})")
            continue

        supabase.table("entries").update({
            "summary": result["summary"],
            "detailed_summary": result["detailed_summary"],
            "key_learnings": result["key_learnings"],
            "tags": result["tags"],
            "content_type": result["content_type"],
            "chapter": result["chapter"],
            "category": result["category"],
        }).eq("id", entry["id"]).execute()
        print(f"{entry['id']}: neu zusammengefasst ({result['chapter']}/{result['category']})")


if __name__ == "__main__":
    main()
