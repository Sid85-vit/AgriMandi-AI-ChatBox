from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import requests
from dotenv import load_dotenv
from groq import Groq
import re

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

DATA_FILE = "mandi_data.json"

class ChatRequest(BaseModel):
    message: str

def fetch_live_government_data():
    API_KEY = os.getenv("GOV_API_KEY")
    RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"    
    api_url = f"https://api.data.gov.in/resource/{RESOURCE_ID}?api-key={API_KEY}&format=json&limit=5000"    
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(api_url, headers=headers, timeout=30)
        data = response.json()

        print(f"DEBUG: Raw API Response Keys: {data.keys()}")
        if "records" not in data:
            print(f"DEBUG: Full API response: {data}")
            
        raw_records = data.get("records", [])
        
        mapped_records = []
        for row in raw_records:
            mapped_records.append({
                "State": row.get("State") or row.get("state") or "Unknown",
                "Market": row.get("Market") or row.get("market") or "Unknown",
                "Commodity": row.get("Commodity") or row.get("commodity") or "Unknown",
                "Arrival_Date": row.get("Arrival_Date") or row.get("arrival_date") or "Unknown",
                "Modal_Price": str(row.get("Modal_Price") or row.get("modal_price") or "0")
            })
        return mapped_records
    except Exception as e:
        print(f"API Connection Failed: {e}")
        return []

@app.get("/api/data")
def get_latest_data():
    live_data = fetch_live_government_data()
    if live_data:
        with open(DATA_FILE, "w") as file:
            json.dump(live_data, file, indent=4)
        return live_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as file:
            return json.load(file)
    return []

@app.post("/api/chat")
def chat_with_data(request: ChatRequest):
    # 1. Helper utility to safely handle commas, whitespaces, and dirty API values
    def parse_price(val):
        if val is None:
            return 0.0
        clean_str = str(val).replace(',', '').strip()
        try:
            return float(clean_str)
        except ValueError:
            return 0.0

    # 2. Load data with self-healing fallback
    market_data = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as file:
                market_data = json.load(file)
        except json.JSONDecodeError:
            market_data = []

    if not market_data:
        print("DEBUG: Local file missing. Fetching live data for chat...")
        market_data = fetch_live_government_data()
        if market_data:
            with open(DATA_FILE, "w") as file:
                json.dump(market_data, file, indent=4)

    if not market_data:
        return {"reply": "The database is currently empty. Please wait for the next data fetch."}
            
    query_lower = request.message.lower().strip()
    
    # 3. Extract unique available entities from today's dataset for cross-referencing
    all_states = set()
    all_commodities = set()
    all_markets = set()
    for row in market_data:
        if row.get("State"): all_states.add(str(row["State"]).strip().lower())
        if row.get("Commodity"): all_commodities.add(str(row["Commodity"]).strip().lower())
        if row.get("Market"): all_markets.add(str(row["Market"]).strip().lower())
        
    # Clean out empty strings or tiny anomalies
    all_states = {s for s in all_states if len(s) > 2 and s != "unknown"}
    all_commodities = {c for c in all_commodities if len(c) > 2 and c != "unknown"}
    all_markets = {m for m in all_markets if len(m) > 2 and m != "unknown"}
    
    # 4. Smart Entity Extraction: Detect categories using STRICT WORD BOUNDARIES (The Regex Fix)
    # Extract clean words from the query (removes punctuation like question marks)
    query_words = set(re.findall(r'\b\w+\b', query_lower)) 
    
    # This prevents "rice" from matching inside "price", or "goa" inside "goal"
    matched_states = {s for s in all_states if re.search(rf"\b{re.escape(s)}\b", query_lower)}
    matched_commodities = {c for c in all_commodities if re.search(rf"\b{re.escape(c)}\b", query_lower)}
    
    # Advanced token-matching for markets
    matched_markets = set()
    for m in all_markets:
        # Full exact match check first
        if re.search(rf"\b{re.escape(m)}\b", query_lower):
            matched_markets.add(m)
        else:
            # Check if any standalone word from the query matches a core word in the market name
            market_words = set(re.findall(r'\b\w+\b', m))
            for word in query_words:
                if len(word) > 3 and word not in ["market", "apmc"] and word in market_words:
                    matched_markets.add(m)

    # 5. Filter with Strict Intersection Logic (AND rules instead of blind OR rules)
    relevant_records = []
    for row in market_data:
        state = str(row.get("State", "")).strip().lower()
        commodity = str(row.get("Commodity", "")).strip().lower()
        market = str(row.get("Market", "")).strip().lower()
        
        state_match = (state in matched_states) if matched_states else True
        commodity_match = (commodity in matched_commodities) if matched_commodities else True
        market_match = (market in matched_markets) if matched_markets else True
        
        if (matched_states or matched_commodities or matched_markets):
            if state_match and commodity_match and market_match:
                relevant_records.append(row)
            
    # 6. Prioritized Superlative Sorting Engine & Broad Queries
    # If the user filtered data, look within those records. Otherwise, evaluate globally.
    source_data = relevant_records if relevant_records else market_data
    
    is_highest_query = any(word in query_lower for word in ["highest", "max", "top", "most expensive"])
    is_lowest_query = any(word in query_lower for word in ["lowest", "min", "cheapest", "bottom"])
    
    if is_highest_query:
        # Sort descending by cleanly parsed numerical prices
        records_to_send = sorted(source_data, key=lambda x: parse_price(x.get("Modal_Price", 0)), reverse=True)[:80]
        
    elif is_lowest_query:
        # Filter out 0/negative prices for lowest searches to eliminate corrupted records
        valid_prices = [x for x in source_data if parse_price(x.get("Modal_Price", 0)) > 0]
        records_to_send = sorted(valid_prices, key=lambda x: parse_price(x.get("Modal_Price", 0)))[:80]
        
    else:
        # If no superlative was requested but the filtered search came up empty -> True Data Drought
        if not relevant_records:
            unique_states_list = list(all_states)
            unique_comms_list = list(all_commodities)
            
            sample_states = ", ".join([s.title() for s in unique_states_list[:3]]) if unique_states_list else "various regions"
            sample_comms = ", ".join([c.title() for c in unique_comms_list[:3]]) if unique_comms_list else "various crops"
            
            return {
                "reply": f"I couldn't find data for that specific request in today's batch. However, today's live records currently feature states like **{sample_states}** and commodities like **{sample_comms}**. Could you try asking about one of those?"
            }
        # Standard filter response
        records_to_send = relevant_records[:80]
        
    # 7. Compress and ship to Groq
    flattened_data = json.dumps(records_to_send, separators=(',', ':'))
    
    system_instruction = (
        "You are Agri Mandi Bot. Summarize price data: Min, Max, Avg. Use Markdown tables."
        f"\n\nDATA: {flattened_data}\n\nUSER QUERY: {request.message}"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": request.message}
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
