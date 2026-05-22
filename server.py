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
    # 1. Load data
    market_data = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as file:
            market_data = json.load(file)
            
    # 2. Filtering logic (Token Saver)
    query_lower = request.message.lower()
    relevant_records = [row for row in market_data if any(val in query_lower for val in [str(row.get(k, "")).lower() for k in ["State", "Commodity", "Market"]])]
    
    if not relevant_records:
        relevant_records = market_data[:40]
    relevant_records = relevant_records[:80]
    flattened_data = json.dumps(relevant_records, separators=(',', ':'))
    
    # 3. System prompt
    system_instruction = (
        "You are Agri Mandi Bot. Summarize price data: Min, Max, Avg. Use Markdown tables."
        f"\n\nDATA: {flattened_data}\n\nUSER QUERY: {request.message}"
    )

    # 4. Corrected Groq Call
    try:
        # Debugging: check what the client actually has
        # print(f"DEBUG: Client methods: {dir(client)}") 
        
        # Standard SDK path:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": request.message}
            ],
            temperature=0.2
        )
        return {"reply": response.choices[0].message.content}
        
    except AttributeError as ae:
        # If it fails here, the logs will show exactly what attribute was missing
        err_msg = f"AttributeError: {str(ae)}. Available attributes: {dir(client)}"
        print(f"🚨 GROQ SDK CRASHED: {err_msg}")
        raise HTTPException(status_code=500, detail=err_msg)
    except Exception as e:
        print(f"🚨 GROQ SDK CRASHED: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
