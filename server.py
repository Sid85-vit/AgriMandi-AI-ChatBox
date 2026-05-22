from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import requests
from dotenv import load_dotenv
from groq import Groq

client = Groq()

# NEW: Import the Google GenAI SDK
from google import genai

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILE = "mandi_data.json"

client = genai.Client()

class ChatRequest(BaseModel):
    message: str

def fetch_live_government_data():
    """Hits the official data.gov.in API with a browser disguise."""
    API_KEY = os.getenv("GOV_API_KEY")
    RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"    

    api_url = f"https://api.data.gov.in/resource/{RESOURCE_ID}?api-key={API_KEY}&format=json&limit=5000"    

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    try:
        print("Contacting official Government servers...")
        response = requests.get(api_url, headers=headers, timeout=30)
        data = response.json()
        
        raw_records = data.get("records", [])
        mapped_records = []
        for row in raw_records:
            mapped_records.append({
                "State": row.get("State") or row.get("state") or "Unknown",
                "Market": row.get("Market") or row.get("market") or "Unknown",
                "Commodity": row.get("Commodity") or row.get("commodity") or "Unknown",
                "Arrival_Date": row.get("Arrival_Date") or row.get("arrival_date") or row.get("Arrival Date") or "Unknown",
                "Modal_Price": str(row.get("Modal_Price") or row.get("modal_price") or row.get("Modal Price") or "0")
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

@app.post("/api/scrape")
def force_scrape():
    live_data = fetch_live_government_data()
    if live_data:
        with open(DATA_FILE, "w") as file:
            json.dump(live_data, file, indent=4)
        return {"message": "Success", "records": len(live_data)}
    return {"message": "Failed to sync", "records": 0}

# NEW: The secure Chat Endpoint
@app.post("/api/chat")
def chat_with_data(request: ChatRequest):
    """Smart-filters data to save Groq API tokens, then asks for analysis."""
    
    # 1. Load the data
    market_data = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as file:
            market_data = json.load(file)
            
    # 2. THE TOKEN SAVER: Python Pre-Filtering
    query_lower = request.message.lower()
    relevant_records = []
    
    # Scan the database and only keep rows mentioned in the user's prompt
    for row in market_data:
        state = str(row.get("State", "")).lower()
        commodity = str(row.get("Commodity", "")).lower()
        market = str(row.get("Market", "")).lower()
        
        if state in query_lower or commodity in query_lower or market in query_lower:
            relevant_records.append(row)
            
    # If the user asks a broad question, grab the top 40 records to prevent token overflow
    if not relevant_records:
        relevant_records = market_data[:40]
        
    # Cap the absolute maximum allowed records to 80 to guarantee we stay under Groq limits
    relevant_records = relevant_records[:80]
            
    # Flatten ONLY the filtered, tiny dataset
    flattened_data = json.dumps(relevant_records, separators=(',', ':'))
    
    # 3. Build the strict context prompt
    system_instruction = (
        "You are Agri Mandi Bot. You track Indian agricultural commodity prices. "
        "Answer the user's query based strictly on the following market data. "
        "Do not invent prices. If the data is not in the JSON below, say you don't know.\n\n"
        "FORMATTING RULES:\n"
        "1. Never dump raw lists of dates and prices.\n"
        "2. If asked for prices across multiple dates, summarize the Maximum, Minimum, and Average.\n"
        "3. Use clean Markdown tables to display comparisons.\n\n"
        f"DATA: {flattened_data}"
    )

# 4. Universal Groq Call
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": request.message}
            ]
        )
        return {"reply": response.choices[0].message.content}
        
    except Exception as e:
        print(f"🚨 GROQ SDK CRASHED: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
