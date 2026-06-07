from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json
import os
from dotenv import load_dotenv
from groq import Groq
from datetime import datetime
import dateparser
from supabase import create_client, Client

load_dotenv()

# ==========================================
# Initialize AI & DB Clients
# ==========================================
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
# Security: Block dangerous SQL keywords
# ==========================================
FORBIDDEN_SQL = ["drop", "delete", "update", "insert", "truncate", "alter", "--", ";", "/*", "*/", "xp_", "exec"]

def is_safe_where_clause(clause: str) -> bool:
    clause_lower = clause.lower()
    return not any(word in clause_lower for word in FORBIDDEN_SQL)


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

    # ------------------------------------------
    # STEP 1: Date Resolution
    # ------------------------------------------
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

    # ------------------------------------------
    # STEP 2: Ask Groq to generate a SQL WHERE clause
    # ------------------------------------------
    schema_context = f"""You are a SQL filter generator for an Indian agricultural market price database.

Table: mandi_prices
Columns:
  - state      (TEXT)  e.g. 'Keralam', 'Karnataka', 'Maharashtra', 'Punjab', 'Rajasthan', 'Uttar Pradesh'
  - market     (TEXT)  e.g. 'Azadpur', 'Kanjirappally Market', 'Pune'
  - commodity  (TEXT)  e.g. 'Tomato', 'Onion', 'Potato', 'Wheat', 'Rice', 'Apple', 'Banana', 'Garlic'
  - arrival_date (DATE) — already filtered to {target_start} to {target_end}, do NOT add date conditions
  - modal_price (NUMERIC) — price in INR per quintal

RULES:
1. Return ONLY a SQL WHERE clause — no SELECT, no FROM, no table name, no semicolon.
2. Use ILIKE for commodity, state, and market matching. Example: commodity ILIKE '%tomato%'
3. If the user wants highest/max/top/expensive prices, append exactly: ||ORDER:DESC||
4. If the user wants lowest/min/cheapest/bottom prices, append exactly: ||ORDER:ASC||
5. If no specific commodity/state/market filter is needed, return exactly: 1=1
6. Combine multiple filters with AND. Example: commodity ILIKE '%onion%' AND state ILIKE '%maharashtra%'
7. Do not add any explanation, markdown, or extra text. Only the WHERE clause."""

    try:
        filter_response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": schema_context},
                {"role": "user", "content": request.message}
            ],
            temperature=0.0,
            max_tokens=150
        )
        raw_filter = filter_response.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Filter generation failed: {str(e)}")

    # ------------------------------------------
    # STEP 3: Parse WHERE clause and ORDER intent
    # ------------------------------------------
    order_by = "arrival_date DESC, modal_price DESC"  # sensible default

    if "||ORDER:DESC||" in raw_filter:
        where_clause = raw_filter.replace("||ORDER:DESC||", "").strip()
        order_by = "modal_price DESC"
    elif "||ORDER:ASC||" in raw_filter:
        where_clause = raw_filter.replace("||ORDER:ASC||", "").strip()
        order_by = "modal_price ASC"
    else:
        where_clause = raw_filter

    # Fallback if Groq returns something unexpected
    if not where_clause or len(where_clause) < 3:
        where_clause = "1=1"

    print(f"[QUERY] where={where_clause} | order={order_by} | records_returned={len(market_data) if 'market_data' in dir() else 'pending'}")

    # ------------------------------------------
    # STEP 4: Security check before SQL execution
    # ------------------------------------------
    if not is_safe_where_clause(where_clause):
        return {"reply": "⚠️ Query could not be processed safely. Please rephrase your question."}

    # ------------------------------------------
    # STEP 5: Execute raw SQL via Supabase RPC
    # ------------------------------------------
    sql_query = f"""
        SELECT state, market, commodity, arrival_date::text, modal_price
        FROM mandi_prices
        WHERE arrival_date BETWEEN '{target_start}' AND '{target_end}'
        AND ({where_clause})
        ORDER BY {order_by}
        LIMIT 80
    """

    try:
        db_response = supabase.rpc("run_query", {"sql": sql_query}).execute()
        market_data = db_response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Query Failed: {str(e)}")

    if not market_data:
        return {
            "reply": (
                f"No data found matching your query for "
                f"{target_start.strftime('%b %d')} to {target_end.strftime('%b %d')}. "
                f"Try broadening your search — check the commodity name or date range."
            )
        }

    # ------------------------------------------
    # STEP 6: Send exact results to Groq for answer
    # ------------------------------------------
    flattened_data = json.dumps(market_data, separators=(',', ':'), default=str)

    answer_instruction = (
        f"You are Agri Mandi Bot, an assistant for Indian agricultural market prices. "
        f"Answer the user's question directly using ONLY the data provided below. "
        f"Date range: {target_start.strftime('%Y-%m-%d')} to {target_end.strftime('%Y-%m-%d')}. "
        f"Use ONE markdown table maximum. Do not repeat commodity summaries separately. "
        f"Show Min, Max, Avg only when explicitly asked. "
        f"Prices are in INR per quintal. "
        f"If the data clearly answers the question, answer it — do not say data is unavailable. "
        f"\n\nDATA:\n{flattened_data}"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": answer_instruction},
                {"role": "user", "content": request.message}
            ],
            temperature=0.2,
            max_tokens=1000
        )
        return {"reply": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq inference failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
