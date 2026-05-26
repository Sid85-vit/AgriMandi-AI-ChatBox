from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import requests
from dotenv import load_dotenv
from groq import Groq

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
    # 1. Load data with a self-healing fallback
    market_data = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as file:
                market_data = json.load(file)
        except json.JSONDecodeError:
            # Catch edge cases where the file was created but left empty
            market_data = []

    # THE FIX: If the file is missing or wiped by Render, fetch it on the fly!
    if not market_data:
        print("DEBUG: Local file missing. Fetching live data for chat...")
        market_data = fetch_live_government_data()
        
        # Save it immediately so future queries are fast again
        if market_data:
            with open(DATA_FILE, "w") as file:
                json.dump(market_data, file, indent=4)

    # If it is STILL empty after a live fetch, the Gov API itself is down.
    if not market_data:
        return {"reply": "The government API is currently unresponsive or returned no data. Please wait a moment and try again."}
            
    query_lower = request.message.lower().strip()
    relevant_records = []
        
    # 2. Safer String-Matching Filter
    for row in market_data:
        state = str(row.get("State", "")).strip().lower()
        commodity = str(row.get("Commodity", "")).strip().lower()
        market = str(row.get("Market", "")).strip().lower()
        
        # Added a length check (>2) to prevent empty strings or tiny acronyms from creating false positives
        if (len(state) > 2 and state in query_lower) or \
           (len(commodity) > 2 and commodity in query_lower) or \
           (len(market) > 2 and market in query_lower):
            relevant_records.append(row)
            
    # 3. Handle Broad Queries & Data Droughts
    if not relevant_records:
        # Check for superlative global queries (Highest/Top)
        if any(word in query_lower for word in ["highest", "max", "top", "most expensive"]):
            # Sort descending by price safely
            sorted_data = sorted(
                market_data, 
                key=lambda x: float(x.get("Modal_Price", 0)) if str(x.get("Modal_Price", 0)).replace('.', '', 1).isdigit() else 0, 
                reverse=True
            )
            records_to_send = sorted_data[:80]
            
        # Check for superlative global queries (Lowest/Bottom)
        elif any(word in query_lower for word in ["lowest", "min", "cheapest", "bottom"]):
            # Filter out zeroes, then sort ascending
            valid_prices = [
                x for x in market_data 
                if str(x.get("Modal_Price", 0)).replace('.', '', 1).isdigit() and float(x.get("Modal_Price", 0)) > 0
            ]
            sorted_data = sorted(valid_prices, key=lambda x: float(x.get("Modal_Price", 0)))
            records_to_send = sorted_data[:80]
            
        else:
            # The True "Data Drought" Diagnostic Handler
            unique_states = list({r.get("State") for r in market_data if r.get("State") and r.get("State") != "Unknown"})
            unique_commodities = list({r.get("Commodity") for r in market_data if r.get("Commodity") and r.get("Commodity") != "Unknown"})
            
            # Dynamically grab up to 3 samples from today's live data
            sample_states = ", ".join(unique_states[:3]) if unique_states else "various states"
            sample_comms = ", ".join(unique_commodities[:3]) if unique_commodities else "various commodities"
            
            return {
                "reply": f"I couldn't find data for that specific request in today's batch. However, today's live records currently feature states like **{sample_states}** and commodities like **{sample_comms}**. Could you try asking about one of those?"
            }
    else:
        records_to_send = relevant_records[:80]
        
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
