from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import requests
from dotenv import load_dotenv

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

# 1. REPLACE THE MOCK WITH THE LIVE GOVERNMENT API ENDPOINT
def fetch_live_government_data():
    """
    Hits the official data.gov.in API with a browser disguise to bypass network throttling.
    """
    # Replace this with your actual working key
    API_KEY = os.getenv("GOV_API_KEY")
    RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070" 
    
    api_url = f"https://api.data.gov.in/resource/{RESOURCE_ID}?api-key={API_KEY}&format=json&limit=5000"    

    # THE HUMAN DISGUISE: Tells the government server that Python is just a regular Chrome browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    try:
        print("Contacting official Government servers using browser disguise...")
        # Send the request with our browser disguise and a patient 30-second window
        response = requests.get(api_url, headers=headers, timeout=30)
        data = response.json()
        
        raw_records = data.get("records", [])
        
        mapped_records = []
        for row in raw_records:
            mapped_records.append({
                "State": row.get("state"),
                "Market": row.get("market"),
                "Commodity": row.get("commodity"),
                "Arrival_Date": row.get("arrival_date"),
                "Modal_x0020_Price": str(row.get("modal_price"))
            })
            
        print(f"🌍 API SUCCESS: Retrieved {len(mapped_records)} authentic daily market records!")
        return mapped_records

    except Exception as e:
        print(f"🚨 API Connection Failed: {e}. Reverting to local cache.")
        return []

@app.get("/api/data")
def get_latest_data():
    """Fetches live data, or safely falls back to the cache if the API fails."""
    live_data = fetch_live_government_data()
    
    if live_data:
        # Save the new data to disk
        with open(DATA_FILE, "w") as file:
            json.dump(live_data, file, indent=4)
        # Return the data directly to the frontend (faster than reading the file again!)
        return live_data
        
    # IF THE API FAILS: Safely check if the backup file exists before opening it
    if os.path.exists(DATA_FILE):
        print("API failed, but found local cache. Serving backup data.")
        with open(DATA_FILE, "r") as file:
            return json.load(file)
            
    # IF API FAILS AND NO BACKUP EXISTS YET: Safely return an empty array
    print("API failed and no local cache exists yet. Returning empty dataset.")
    return []

@app.post("/api/scrape")
def force_scrape():
    """Manual trigger syncing function."""
    live_data = fetch_live_government_data()
    if live_data:
        with open(DATA_FILE, "w") as file:
            json.dump(live_data, file, indent=4)
        return {"message": "Success", "records": len(live_data)}
    return {"message": "Failed to sync with Gov servers", "records": 0}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)