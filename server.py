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
    market_data = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as file:
            market_data = json.load(file)
            
    query_lower = request.message.lower()
    relevant = [row for row in market_data if any(val in query_lower for val in [str(row.get(k, "")).lower() for k in ["State", "Commodity", "Market"]])]
    
    # Use top 40 if no match, else top 80 of relevant
    records_to_send = (relevant if relevant else market_data)[:80]
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
