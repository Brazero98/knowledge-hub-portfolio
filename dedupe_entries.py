from collections import defaultdict

from main import supabase


def main():
    entries = (
        supabase.table("entries")
        .select("id, original_url, created_at")
        .not_.is_("original_url", "null")
        .order("created_at", desc=True)
        .execute()
        .data
    )

    by_url = defaultdict(list)
    for e in entries:
        by_url[e["original_url"]].append(e)

    for url, group in by_url.items():
        if len(group) <= 1:
            continue
        keep, *rest = group  # newest first due to ordering above
        print(f"{url}: behalte {keep['id']} ({keep['created_at']}), lösche {len(rest)}")
        for e in rest:
            supabase.table("entries").delete().eq("id", e["id"]).execute()


if __name__ == "__main__":
    main()
