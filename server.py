from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json
import os
import requests
from dotenv import load_dotenv
from groq import Groq
import re
from datetime import datetime, timedelta
import dateparser
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
from supabase import create_client, Client
import time
import random

load_dotenv()

# Initialize AI & DB Clients
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None


# ==========================================
# CORE: The Safe Data Fetcher (From Gov API)
# ==========================================
def fetch_live_government_data(target_date_str=None):
    time.sleep(random.uniform(0.1, 1.5)) # Anti-ban jitter
    
    API_KEY = os.getenv("GOV_API_KEY")
    RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"    
    LIMIT = 10000 
    offset = 0
    all_mapped_records = []
    
    # 🔥 Circuit Breaker Trackers 🔥
    max_expected_per_day = 30000
    records_fetched_this_session = 0
    previous_page_first_id = None
    
    while True:
        base_url = f"https://api.data.gov.in/resource/{RESOURCE_ID}?api-key={API_KEY}&format=json&limit={LIMIT}&offset={offset}"    

        if target_date_str:
            encoded_date = urllib.parse.quote(target_date_str, safe='')
            api_url = base_url + f"&filters[Arrival_Date]={encoded_date}"
        else:
            api_url = base_url + "&sort[Arrival_Date]=desc"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        }
        
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            data = response.json()
            raw_records = data.get("records", [])
            
            if not raw_records: break
            
            # 🔥 Duplicate Page Circuit Breaker 🔥
            r0 = raw_records[0]
            state_val = r0.get('State') or r0.get('state') or 'unknown'
            market_val = r0.get('Market') or r0.get('market') or 'unknown'
            commodity_val = r0.get('Commodity') or r0.get('commodity') or 'unknown'
            date_val = r0.get('Arrival_Date') or r0.get('arrival_date') or 'unknown'
            
            current_page_first_id = f"{state_val}_{market_val}_{commodity_val}_{date_val}"
            
            if current_page_first_id == previous_page_first_id:
                print(f"⚠️ Pagination Loop detected (Duplicate page). Halting at offset {offset}.")
                break
            previous_page_first_id = current_page_first_id
            
            # 🔥 Absolute Sanity Cap Breaker 🔥
            records_fetched_this_session += len(raw_records)
            if target_date_str and records_fetched_this_session > max_expected_per_day:
                print(f"⚠️ Sanity cap ({max_expected_per_day}) exceeded for {target_date_str}. Halting.")
                break

            valid_records_in_this_batch = 0
            
            for row in raw_records:
                raw_date = str(row.get("Arrival_Date") or row.get("arrival_date") or "").strip()
                
                # 🔥 Government API Glitch Filter 🔥
                if target_date_str and raw_date != target_date_str:
                    continue
                
                valid_records_in_this_batch += 1
                
                pg_date = None
                if raw_date:
                    try:
                        pg_date = datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
                    except: pass
                
                raw_price = str(row.get("Modal_Price") or row.get("modal_price") or "0").replace(',', '').strip()
                try: clean_price = float(raw_price)
                except: clean_price = 0.0

                if pg_date and clean_price > 0:
                    all_mapped_records.append({
                        "state": (row.get("State") or row.get("state") or "Unknown").title(),
                        "market": (row.get("Market") or row.get("market") or "Unknown").title(),
                        "commodity": (row.get("Commodity") or row.get("commodity") or "Unknown").title(),
                        "arrival_date": pg_date,
                        "modal_price": clean_price
                    })

            # Break if we hit the natural end of the data
            if len(raw_records) < LIMIT: break
            
            # 🔥 Prevent Infinite Filter Loop 🔥
            if target_date_str:
                if valid_records_in_this_batch == 0:
                    print(f"⚠️ API ignored date filter for {target_date_str}. Halting pagination.")
                    break    
            else:
                offset += LIMIT
                continue
                
            offset += LIMIT
            
        except Exception as e:
            print(f"API Connection Failed at offset {offset}: {e}")
            break
            
    return all_mapped_records
    

# ==========================================
# CORE: The Background Sync Worker Logic
# ==========================================
def sync_worker_logic():
    today = datetime.now().date()

    def upsert_chunk(chunk):
        supabase.table("mandi_prices").upsert(
            chunk,
            on_conflict="state,market,commodity,arrival_date,modal_price"
        ).execute()

    def get_resume_date():
        res = supabase.table("sync_state").select("value").eq("key", "last_synced_date").execute()
        val = res.data[0]["value"] if res.data else None
        if val:
            return datetime.strptime(val, "%Y-%m-%d").date() + timedelta(days=1)
        return today - timedelta(days=365)

    def mark_date_complete(date_obj):
        supabase.table("sync_state").upsert({
            "key": "last_synced_date",
            "value": date_obj.strftime("%Y-%m-%d"),
            "updated_at": datetime.now().isoformat()
        }, on_conflict="key").execute()

    start_date = get_resume_date()

    if start_date > today:
        print("✅ Database is already completely synchronized to today.")
        return

    # Always re-sync last 3 days to catch partial inserts from prior crashes
    start_date = min(start_date, today - timedelta(days=2))

    missing_dates = [
        (start_date + timedelta(days=x)).strftime("%d/%m/%Y")
        for x in range((today - start_date).days + 1)
    ]

    total_days = len(missing_dates)
    print(f"🛡️ Resumable sync started for {total_days} days from {start_date}...")

    for d_str in missing_dates:
        print(f"Fetching data for: {d_str}")
        day_records = fetch_live_government_data(d_str)

        if day_records:
            chunk_size = 1000
            for i in range(0, len(day_records), chunk_size):
                try:
                    upsert_chunk(day_records[i:i + chunk_size])
                except Exception as e:
                    print(f"🚨 Upsert failed for chunk on {d_str}: {e}")
                    return  # Stop cleanly; next trigger will resume from last good date

            date_obj = datetime.strptime(d_str, "%d/%m/%Y").date()
            mark_date_complete(date_obj)
            print(f"✅ Saved & checkpointed {len(day_records)} records for {d_str}")
        else:
            date_obj = datetime.strptime(d_str, "%d/%m/%Y").date()
            mark_date_complete(date_obj)
            print(f"⚠️ No records for {d_str} — checkpointed and skipped")

    print("🎉 Sync complete!")


# ==========================================
# ROUTE 1: Asynchronous Trigger Endpoint
# ==========================================
@app.get("/api/sync")
def trigger_delta_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_worker_logic)
    
    return {
        "status": "Sync engine successfully delegated to background execution worker.",
        "message": "The server is now handling the historical processing pipeline out-of-band. You can safely close this browser tab. Monitor live progress via your Render logs or watch the row count rise inside your Supabase Table Editor."
    }


# ==========================================
# ROUTE 2: The Stateless Chat API
# ==========================================
@app.post("/api/chat")
def chat_with_data(request: ChatRequest):
    # 1. Date Resolution Engine
    today = datetime.now()
    target_start, target_end = None, None

    if request.start_date and request.end_date:
        try:
            target_start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
            target_end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
        except ValueError: pass

    if not target_start and request.message:
        parsed_date = dateparser.parse(request.message, settings={'PREFER_DATES_FROM': 'past'})
        if parsed_date:
            target_start = parsed_date.date()
            target_end = parsed_date.date()

    if not target_start:
        return {"reply": "📅 Please select an arrival date or date range using the calendar controls."}

    # 2. Fetch directly from Postgres
    try:
        db_response = supabase.table("mandi_prices") \
            .select("*") \
            .gte("arrival_date", target_start.strftime("%Y-%m-%d")) \
            .lte("arrival_date", target_end.strftime("%Y-%m-%d")) \
            .execute()
        market_data = db_response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Query Failed: {str(e)}")

    if not market_data:
        return {"reply": f"No data found in the warehouse for {target_start.strftime('%b %d')} to {target_end.strftime('%b %d')}. (Have you run the sync script yet?)"}

    # 3. Smart Filtering
    query_lower = (request.message or "").lower().strip()
    
    all_states, all_commodities, all_markets = set(), set(), set()
    for row in market_data:
        all_states.add(str(row["state"]).lower())
        all_commodities.add(str(row["commodity"]).lower())
        all_markets.add(str(row["market"]).lower())
        
    query_words = set(re.findall(r'\b\w+\b', query_lower)) 
    
    matched_states = {s for s in all_states if re.search(rf"\b{re.escape(s)}\b", query_lower)}
    matched_commodities = {c for c in all_commodities if re.search(rf"\b{re.escape(c)}\b", query_lower)}
    
    matched_markets = set()
    for m in all_markets:
        if re.search(rf"\b{re.escape(m)}\b", query_lower): matched_markets.add(m)
        else:
            market_words = set(re.findall(r'\b\w+\b', m))
            for word in query_words:
                if len(word) > 3 and word not in ["market", "apmc"] and word in market_words:
                    matched_markets.add(m)

    # 4. Strict Intersection
    relevant_records = []
    for row in market_data:
        state = str(row["state"]).lower()
        commodity = str(row["commodity"]).lower()
        market = str(row["market"]).lower()
        
        state_match = (state in matched_states) if matched_states else True
        commodity_match = (commodity in matched_commodities) if matched_commodities else True
        market_match = (market in matched_markets) if matched_markets else True
        
        if (matched_states or matched_commodities or matched_markets):
            if state_match and commodity_match and market_match:
                relevant_records.append(row)

    # 5. Capping & Sorting
    source_data = relevant_records if relevant_records else market_data
    
    is_highest = any(word in query_lower for word in ["highest", "max", "top", "expensive"])
    is_lowest = any(word in query_lower for word in ["lowest", "min", "cheapest", "bottom"])
    
    if is_highest:
        records_to_send = sorted(source_data, key=lambda x: x["modal_price"], reverse=True)[:80]
    elif is_lowest:
        records_to_send = sorted(source_data, key=lambda x: x["modal_price"])[:80]
    else:
        if not relevant_records and request.message:
            return {"reply": "I found data for those dates, but nothing matched your exact keywords. Try expanding your search."}
        records_to_send = source_data[:80]

    # 6. Groq Inference
    flattened_data = json.dumps(records_to_send, separators=(',', ':'))
    system_instruction = (
        f"You are Agri Mandi Bot. Summarize price data: Min, Max, Avg for {target_start.strftime('%Y-%m-%d')} to {target_end.strftime('%Y-%m-%d')}. Use Markdown tables."
        f"\n\nDATA: {flattened_data}\n\nUSER QUERY: {request.message or 'Summarize the market activity.'}"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": request.message or "Give me a high-level summary."}
            ],
            temperature=0.2
        )
        return {"reply": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
