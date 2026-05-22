from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import requests
from dotenv import load_dotenv

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
    RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"    

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
    """Takes user message, attaches local data cache, and asks Gemini."""
    
    # 1. Load the latest offline data cache
    market_data = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as file:
            market_data = json.load(file)
            
    # Flatten the data to save tokens and prevent model confusion
    flattened_data = json.dumps(market_data, separators=(',', ':'))
    
    # 2. Build the strict context prompt
    system_instruction = (
        "You are Agri Mandi Bot. You track Indian agricultural commodity prices. "
        "Answer the user's query based strictly on the following live market data. "
        "Do not invent prices. If the data is not in the JSON below, say you don't know.\n\n"
        f"DATA: {flattened_data}\n\n"
        f"USER QUERY: {request.message}"
    )

    # 3. Call the cloud API securely from the server
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=system_instruction
        )
        return {"reply": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
