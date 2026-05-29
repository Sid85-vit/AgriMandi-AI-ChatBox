from fastapi import FastAPI, HTTPException
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
import time
import random

load_dotenv()

# Initialize Groq client only
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

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

def fetch_live_government_data(target_date_str=None):
    time.sleep(random.uniform(0.1, 1.5))
    API_KEY = os.getenv("GOV_API_KEY")
    RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"    

    LIMIT = 10000 
    offset = 0
    all_mapped_records = []
    
    # The Pagination Loop
    while True:
        base_url = f"https://api.data.gov.in/resource/{RESOURCE_ID}?api-key={API_KEY}&format=json&limit={LIMIT}&offset={offset}"    

        if target_date_str:
            encoded_date = urllib.parse.quote(target_date_str, safe='')
            api_url = base_url + f"&filters[Arrival_Date]={encoded_date}"
        else:
            api_url = base_url + "&sort[Arrival_Date]=desc"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            data = response.json()
            
            raw_records = data.get("records", [])
            
            # Break if no records are left
            if not raw_records:
                break
            
            for row in raw_records:
                all_mapped_records.append({
                    "State": row.get("State") or row.get("state") or "Unknown",
                    "Market": row.get("Market") or row.get("market") or "Unknown",
                    "Commodity": row.get("Commodity") or row.get("commodity") or "Unknown",
                    "Arrival_Date": row.get("Arrival_Date") or row.get("arrival_date") or "Unknown",
                    "Modal_Price": str(row.get("Modal_Price") or row.get("modal_price") or "0")
                })

            # Break if we hit the final page
            if len(raw_records) < LIMIT:
                break
            
            # Increment offset for the next loop iteration
            offset += LIMIT
            
        except Exception as e:
            print(f"API Connection Failed at offset {offset}: {e}")
            break
            
    return all_mapped_records

# Stateless health check route
@app.get("/api/data")
def get_latest_data():
    return {"status": "Stateless backend is live and ready for AI routing."}

@app.post("/api/chat")
def chat_with_data(request: ChatRequest):
    def parse_price(val):
        if val is None:
            return 0.0
        clean_str = str(val).replace(',', '').strip()
        try:
            return float(clean_str)
        except ValueError:
            return 0.0

    # 1. Strict Event-Driven Date Resolution Engine
    today = datetime.now()
    target_start = None
    target_end = None

    # Check UI calendar inputs first
    if request.start_date and request.end_date:
        try:
            target_start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
            target_end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    # If UI calendar is empty, attempt NLP parsing on the prompt text
    if not target_start and request.message:
        parsed_date = dateparser.parse(request.message, settings={'PREFER_DATES_FROM': 'past', 'STRICT_PARSING': False})
        if parsed_date:
            target_start = parsed_date.date()
            target_end = parsed_date.date()

    # CRITICAL STOP: If no explicit date is set, halt execution and prompt the user
    if not target_start:
        return {
            "reply": "📅 Please select an arrival date or date range using the calendar controls above to fetch live market records."
        }

    # Generate list of acceptable date strings to match Gov API format (DD/MM/YYYY)
    date_range_strs = [
        (target_start + timedelta(days=x)).strftime("%d/%m/%Y") 
        for x in range((target_end - target_start).days + 1)
    ]

    # 2. Dynamic Parallel Fetching via Gov API Native Date Filters
    market_data = []
    
    max_workers = min(len(date_range_strs), 5)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_live_government_data, d_str) for d_str in date_range_strs]
        for future in futures:
            try:
                records = future.result()
                if records:
                    market_data.extend(records)
            except Exception as e:
                print(f"🚨 Parallel API Fetch Failed for a single thread: {str(e)}")

    if not market_data:
        return {
            "reply": f"I couldn't retrieve any live market records from the government API for the requested dates ({target_start.strftime('%d/%m/%Y')} to {target_end.strftime('%d/%m/%Y')}). The Mandis might be closed, or the government hasn't uploaded this data yet."
        }
            
    # Safely handle empty messages generated by UI auto-fetch
    query_lower = (request.message or "").lower().strip()
    
    # 3. Extract Unique Entity Sets for Mapping
    all_states, all_commodities, all_markets = set(), set(), set()
    for row in market_data:
        if row.get("State"): all_states.add(str(row["State"]).strip().lower())
        if row.get("Commodity"): all_commodities.add(str(row["Commodity"]).strip().lower())
        if row.get("Market"): all_markets.add(str(row["Market"]).strip().lower())
        
    all_states = {s for s in all_states if len(s) > 2 and s != "unknown"}
    all_commodities = {c for c in all_commodities if len(c) > 2 and c != "unknown"}
    all_markets = {m for m in all_markets if len(m) > 2 and m != "unknown"}
    
    # 4. Smart Entity Extraction
    query_words = set(re.findall(r'\b\w+\b', query_lower)) 
    
    matched_states = {s for s in all_states if re.search(rf"\b{re.escape(s)}\b", query_lower)}
    matched_commodities = {c for c in all_commodities if re.search(rf"\b{re.escape(c)}\b", query_lower)}
    
    matched_markets = set()
    for m in all_markets:
        if re.search(rf"\b{re.escape(m)}\b", query_lower):
            matched_markets.add(m)
        else:
            market_words = set(re.findall(r'\b\w+\b', m))
            for word in query_words:
                if len(word) > 3 and word not in ["market", "apmc"] and word in market_words:
                    matched_markets.add(m)

    # 5. Filter with Strict Intersection & Time-Series Date Logic
    relevant_records = []
    for row in market_data:
        arrival_date = str(row.get("Arrival_Date", "")).strip()
        if arrival_date not in date_range_strs:
            continue # Safe structural boundary cross-check

        state = str(row.get("State", "")).strip().lower()
        commodity = str(row.get("Commodity", "")).strip().lower()
        market = str(row.get("Market", "")).strip().lower()
        
        state_match = (state in matched_states) if matched_states else True
        commodity_match = (commodity in matched_commodities) if matched_commodities else True
        market_match = (market in matched_markets) if matched_markets else True
        
        if (matched_states or matched_commodities or matched_markets):
            if state_match and commodity_match and market_match:
                relevant_records.append(row)
            
    # 6. Prioritized Superlative Sorting Engine
    # Fallback to the raw filtered date array if no specific keywords were matched
    source_data = relevant_records if relevant_records else [r for r in market_data if str(r.get("Arrival_Date", "")).strip() in date_range_strs]
    
    is_highest_query = any(word in query_lower for word in ["highest", "max", "top", "most expensive"])
    is_lowest_query = any(word in query_lower for word in ["lowest", "min", "cheapest", "bottom"])
    
    if is_highest_query:
        records_to_send = sorted(source_data, key=lambda x: parse_price(x.get("Modal_Price", 0)), reverse=True)[:80]
    elif is_lowest_query:
        valid_prices = [x for x in source_data if parse_price(x.get("Modal_Price", 0)) > 0]
        records_to_send = sorted(valid_prices, key=lambda x: parse_price(x.get("Modal_Price", 0)))[:80]
    else:
        # If the user typed a specific keyword but it yielded zero matches
        if not relevant_records and request.message:
            return {
                "reply": f"I couldn't find exact data matching your keywords for the requested dates. Try expanding your date range or adjusting the commodity name."
            }
        
        # If it's a generic query OR the calendar's background auto-fetch, send the broad source data
        records_to_send = source_data[:80]
        
    # 7. Compress and ship to Groq
    flattened_data = json.dumps(records_to_send, separators=(',', ':'))
    
    system_instruction = (
        f"You are Agri Mandi Bot. Summarize price data: Min, Max, Avg for the period {target_start.strftime('%Y-%m-%d')} to {target_end.strftime('%Y-%m-%d')}. Use Markdown tables."
        f"\n\nDATA: {flattened_data}\n\nUSER QUERY: {request.message or 'Summarize the market activity for these dates.'}"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": request.message or "Give me a high-level summary of the data."}
            ],
            temperature=0.2
        )
        return {"reply": response.choices[0].message.content}
    except Exception as e:
        print(f"🚨 GROQ CRASH: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
