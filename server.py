from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json
import os
from dotenv import load_dotenv
from groq import Groq
import re
from datetime import datetime
import dateparser
from supabase import create_client, Client

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
# ROUTE 1: Health Check
# ==========================================
@app.get("/")
def health_check():
    return {"status": "Agri Mandi Bot is running."}


# ==========================================
# ROUTE 2: The Stateless Chat API
# ==========================================
@app.post("/api/chat")
def chat_with_data(request: ChatRequest):
    # 1. Date Resolution Engine
    target_start, target_end = None, None

    if request.start_date and request.end_date:
        try:
            target_start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
            target_end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    if not target_start and request.message:
        parsed_date = dateparser.parse(request.message, settings={'PREFER_DATES_FROM': 'past'})
        if parsed_date:
            target_start = parsed_date.date()
            target_end = parsed_date.date()

    if not target_start:
        return {"reply": "📅 Please select an arrival date or date range using the calendar controls."}

    # 2. Fetch from Supabase
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
        return {"reply": f"No data found for {target_start.strftime('%b %d')} to {target_end.strftime('%b %d')}. The weekly sync may not have reached this date range yet."}

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
        if re.search(rf"\b{re.escape(m)}\b", query_lower):
            matched_markets.add(m)
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

        if matched_states or matched_commodities or matched_markets:
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
            return {"reply": "I found data for those dates, but nothing matched your exact keywords. Try broadening your search."}
        records_to_send = source_data[:80]

    # 6. Groq Inference
    flattened_data = json.dumps(records_to_send, separators=(',', ':'))
    system_instruction = (
        f"You are Agri Mandi Bot. Summarize price data: Min, Max, Avg for "
        f"{target_start.strftime('%Y-%m-%d')} to {target_end.strftime('%Y-%m-%d')}. "
        f"Use Markdown tables.\n\n"
        f"DATA: {flattened_data}\n\n"
        f"USER QUERY: {request.message or 'Summarize the market activity.'}"
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
