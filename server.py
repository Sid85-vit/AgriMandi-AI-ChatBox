import os, requests, time, random, urllib.parse
from datetime import datetime, timedelta
from supabase import create_client

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def fetch_day(date_str, max_retries=3):
    API_KEY = os.environ["GOV_API_KEY"]
    RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"
    LIMIT = 10000
    offset = 0
    records = []

    for attempt in range(max_retries):
        try:
            while True:
                time.sleep(random.uniform(2, 5))
                encoded = urllib.parse.quote(date_str, safe='')
                url = (
                    f"https://api.data.gov.in/resource/{RESOURCE_ID}"
                    f"?api-key={API_KEY}&format=json&limit={LIMIT}"
                    f"&offset={offset}&filters[Arrival_Date]={encoded}"
                )
                resp = requests.get(url, timeout=30, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                })
                raw = resp.json().get("records", [])
                if not raw:
                    break

                valid = 0
                for row in raw:
                    raw_date = str(row.get("Arrival_Date") or row.get("arrival_date") or "").strip()
                    if raw_date != date_str:
                        continue
                    valid += 1
                    try:
                        pg_date = datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
                        price = float(str(row.get("Modal_Price") or row.get("modal_price") or "0").replace(',', ''))
                        if pg_date and price > 0:
                            records.append({
                                "state": (row.get("State") or row.get("state") or "Unknown").title(),
                                "market": (row.get("Market") or row.get("market") or "Unknown").title(),
                                "commodity": (row.get("Commodity") or row.get("commodity") or "Unknown").title(),
                                "arrival_date": pg_date,
                                "modal_price": price
                            })
                    except:
                        continue

                if len(raw) < LIMIT or valid == 0:
                    break
                offset += LIMIT
            break  # success — exit retry loop

        except Exception as e:
            print(f"  Attempt {attempt + 1}/{max_retries} failed for {date_str}: {e}")
            time.sleep(10 * (attempt + 1))

    # Deduplicate within batch before returning
    seen = set()
    unique = []
    for r in records:
        k = (r["state"], r["market"], r["commodity"], r["arrival_date"], r["modal_price"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique


def get_start_date():
    today = datetime.now().date()
    try:
        res = supabase.table("sync_state").select("value").eq("key", "last_synced_date").execute()
        val = res.data[0]["value"] if res.data else None
        if val:
            # Re-sync last 3 days to catch any partial inserts
            return datetime.strptime(val, "%Y-%m-%d").date() - timedelta(days=3)
    except Exception as e:
        print(f"Could not read sync_state: {e}")
    return today - timedelta(days=365)


def mark_complete(date_obj):
    for attempt in range(3):
        try:
            supabase.table("sync_state").upsert({
                "key": "last_synced_date",
                "value": date_obj.strftime("%Y-%m-%d"),
                "updated_at": datetime.now().isoformat()
            }, on_conflict="key").execute()
            return
        except Exception as e:
            print(f"  Checkpoint retry {attempt + 1}/3: {e}")
            time.sleep(3 * (attempt + 1))
    print(f"  Failed to checkpoint {date_obj} — will re-sync next run")


def sync():
    today = datetime.now().date()
    start = get_start_date()

    if start > today:
        print("Already up to date.")
        return

    dates = [start + timedelta(days=x) for x in range((today - start).days + 1)]
    print(f"Syncing {len(dates)} days from {start} to {today}")

    for date_obj in dates:
        d_str = date_obj.strftime("%d/%m/%Y")
        print(f"Fetching {d_str}...")
        records = fetch_day(d_str)

        if records:
            for i in range(0, len(records), 1000):
                try:
                    supabase.table("mandi_prices").upsert(
                        records[i:i + 1000],
                        on_conflict="state,market,commodity,arrival_date,modal_price"
                    ).execute()
                except Exception as e:
                    print(f"  Insert failed for chunk on {d_str}: {e}")
            print(f"  Saved {len(records)} records for {d_str}")
        else:
            print(f"  No data for {d_str}")

        mark_complete(date_obj)

    print("Sync complete.")


if __name__ == "__main__":
    sync()
