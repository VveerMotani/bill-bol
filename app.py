import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from sarvamai import SarvamAI

# Load .env for local runs; on the host, set SARVAM_API_KEY as an environment variable
env_file = Path(__file__).with_name(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

app = Flask(__name__)
client = SarvamAI(api_subscription_key=os.environ.get("SARVAM_API_KEY"))

BILL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "spoken_name": {"type": "string"},
                    "english_name": {"type": "string"},
                    "price_list_item": {"type": ["string", "null"]},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "spoken_rate": {"type": ["number", "null"]},
                },
                "required": ["spoken_name", "english_name", "price_list_item", "quantity", "unit", "spoken_rate"],
                "additionalProperties": False,
            },
        },
        "discount": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["none", "percent", "flat"]},
                "value": {"type": "number"},
            },
            "required": ["type", "value"],
            "additionalProperties": False,
        },
    },
    "required": ["items", "discount"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You turn a shopkeeper's spoken order (any Indian language, or mixed with English) into bill lines as JSON.
Rules:
- Go through the order clause by clause. Every product mentioned becomes one entry, including products mentioned only with a special rate. Never drop one.
- spoken_name: only the product name as spoken, original script. english_name: the literal English name of that same product (मैगी = Maggi, not a category).
- price_list_item: the exact price list name only if it is the SAME product as english_name (match across languages), else null. Same unit or packaging does not make it the same product; products not on the list get null.
- quantity/unit: in the price list unit when matched (500 gram at a per-kg price = 0.5 kg). No quantity said = 1.
- spoken_rate: per-unit rupee rate the speaker states for that product (special rate, 'for you', 'wala'), else null. Never invent prices.
- discount on the whole bill: percent or flat rupees, else type none, value 0.

Example. Price list: Rice | kg | 60 ; Milk | litre | 56 ; Bread | packet | 45
Order: "एक किलो चावल, दो पैकेट बिस्कुट बीस रुपये वाले, दूध आपके लिए पचास रुपये लीटर, सौ रुपये कम कर दो"
Output: {"items":[{"spoken_name":"चावल","english_name":"Rice","price_list_item":"Rice","quantity":1,"unit":"kg","spoken_rate":null},{"spoken_name":"बिस्कुट","english_name":"Biscuits","price_list_item":null,"quantity":2,"unit":"packet","spoken_rate":20},{"spoken_name":"दूध","english_name":"Milk","price_list_item":"Milk","quantity":1,"unit":"litre","spoken_rate":50}],"discount":{"type":"flat","value":100}}"""

EDIT_PROMPT = """

Editing an existing bill: you are also given the current bill as JSON. The spoken text is an instruction to change it: add items, remove items, change a quantity or rate, or change the discount.
Return the COMPLETE updated bill: keep every current item exactly as it is unless the instruction changes it, apply the changes, and keep the current discount unless the instruction changes it.
"Ek kilo aur chawal" / "one more kg rice" adds to the existing quantity; "chawal teen kilo kar do" / "make rice 3 kg" sets it."""


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/bill")
def bill():
    price_list = json.loads(request.form.get("price_list") or "[]")
    current_bill = request.form.get("current_bill")

    # Phone orders arrive as text from the voice agent; spoken orders as audio
    text = request.form.get("text", "").strip()
    if text:
        return jsonify(transcript=text, language=request.form.get("language"),
                       **parse_order(text, price_list, current_bill))

    audio = request.files.get("audio")
    if not audio:
        return jsonify(error="No audio received"), 400
    stt = client.speech_to_text.transcribe(
        file=(audio.filename or "order.webm", audio.read(), audio.mimetype),
        model="saaras:v4",
        language_code="unknown",
    )
    if not stt.transcript.strip():
        return jsonify(error="Couldn't hear anything, please try again"), 422

    return jsonify(transcript=stt.transcript, language=stt.language_code,
                   **parse_order(stt.transcript, price_list, current_bill))


# Phone orders from the Sarvam voice agent's place_order tool, kept in Upstash Redis.
# Without Upstash credentials (local runs) they live in memory.
REDIS_URL = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
local_orders = []


def redis(*command):
    req = urllib.request.Request(REDIS_URL, data=json.dumps(command).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.loads(res.read())["result"]


@app.post("/api/orders")
def place_order():
    data = request.get_json(silent=True) or {}
    if not str(data.get("order_text", "")).strip():
        return jsonify(status="error", error="order_text is required"), 400
    order = {"id": uuid.uuid4().hex[:8], "received_at": int(time.time() * 1000),
             **{k: data.get(k) for k in ("customer_name", "pickup_time", "order_text", "language",
                                         "caller_phone", "transcript", "interaction_id")}}
    if REDIS_URL:
        redis("LPUSH", "orders", json.dumps(order, ensure_ascii=False))
        redis("LTRIM", "orders", 0, 49)
    else:
        local_orders.insert(0, order)
        del local_orders[50:]
    return jsonify(status="ok", order_id=order["id"])


@app.get("/api/orders")
def list_orders():
    orders = [json.loads(o) for o in redis("LRANGE", "orders", 0, 49)] if REDIS_URL else local_orders
    return jsonify(orders=orders)


def parse_order(transcript, price_list, current_bill=None):
    lines = "\n".join(f"{p['name']} | {p['unit']} | {p['price']}" for p in price_list) or "(empty)"
    system, user = SYSTEM_PROMPT, f"Price list:\n{lines}\n\nOrder: {transcript}"
    if current_bill:
        system += EDIT_PROMPT
        user = f"Price list:\n{lines}\n\nCurrent bill: {current_bill}\n\nInstruction: {transcript}"
    chat = client.chat.completions(
        model="sarvam-105b-conversations",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "bill", "schema": BILL_SCHEMA, "strict": True},
        },
        temperature=0,
        reasoning_effort="low",
        max_tokens=4096,
    )
    content = chat.choices[0].message.content
    if not content:
        raise ValueError("Couldn't read the order, please try again")
    return json.loads(content)


@app.errorhandler(ValueError)
def bad_parse(err):
    return jsonify(error=str(err)), 502


if __name__ == "__main__":
    app.run(debug=True)
