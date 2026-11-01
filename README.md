# Bill बोल

**Speak a kirana order in any Indian language. Get the bill.**

**Live demo:** https://sarvam-blush-rho.vercel.app

A shopkeeper taps the mic and says the order the way they talk, in Hindi, Tamil, Bengali or any mix with English: *"दो किलो चावल, एक लीटर तेल, मैगी तीन पैकेट बीस रुपये वाले, सौ रुपये कम कर दो"*. Bill बोल turns it into an itemised bill using the shop's own price list.

Customers can also **phone in their order**. A Sarvam voice agent takes it, reads it back, and sends it to the shop's screen, where it becomes a bill in one tap.

Built with [Sarvam AI](https://www.sarvam.ai) for the Sarvam AI hackathon.

## What it does

**Spoken billing**
- Speak an order in any Indian language, or a mix with English.
- Items are matched to the shop's price list across languages (चावल → Rice) and units are converted (500 gram at a per-kg price = 0.5 kg).
- Special rates said aloud ("बीस रुपये वाले", "आपके लिए पचास रुपये") override the list price and are tagged on the bill.
- Discounts said aloud, as a percentage or a flat amount, are applied to the total.
- Items not on the price list are highlighted. The shopkeeper types a rate and can save the item to the price list with one tap.
- Change the current bill by voice: *"अंडे हटा दो, एक किलो चावल और"*.
- The price list is edited in the app and saved in the browser.

**Phone orders (Sarvam voice agent "Dukaan Sahayak")**
- The customer calls and orders in their own language, and can switch language mid-call.
- The agent asks for missing quantities, asks if anything else is needed, takes the name and pickup time, reads the whole order back, applies changes, and gets a clear yes.
- It never quotes prices, never promises stock and never upsells. Asked about cost, it says the bill is made at the shop.
- On confirmation it calls the `place_order` tool, which POSTs the order to `/api/orders`.
- The order appears in the **📞 Phone orders** tab within about 5 seconds. **Make bill** runs it through the same parser and price list as a spoken order.

## How it works

```
Shopkeeper mic ──audio──▶ /api/bill ──▶ Sarvam STT (saaras:v4) ──▶ transcript ─┐
                                                                               ▼
Phone call ──▶ Sarvam voice agent ──place_order──▶ /api/orders ──▶ Upstash Redis │
                                                        │                      │
Phone orders tab ◀── polls GET /api/orders ◀────────────┘                      │
      │ "Make bill" (order text)                                               ▼
      └──────────────────────────────▶ /api/bill ──▶ sarvam-105b-conversations
                                                     (JSON schema: items, qty, unit,
                                                      spoken rate, discount)
                                                              │
Browser ◀─────────────────────────────────────────────────────┘
computes rates and totals from the price list
```

- **Speech to text:** Sarvam `saaras:v4` with automatic language detection.
- **Order parsing:** `sarvam-105b-conversations` with a strict JSON schema. The model only extracts items, quantities, units, spoken rates and discounts; it never computes money.
- **Totals:** calculated in the browser from the shop's price list, so the arithmetic is exact.
- **Phone agent:** built on Sarvam's Samvaad voice-agent platform. Its `place_order` tool sends `customer_name`, `pickup_time`, `order_text` (in the customer's words), `language`, `caller_phone`, `transcript` and `interaction_id`.
- **Order inbox:** Upstash Redis (the last 50 orders), through Vercel's Upstash integration.

## Project layout

```
app.py                Flask app: /api/bill (audio or text → bill lines), /api/orders (phone-order inbox)
templates/index.html  The whole UI: mic, bill, phone orders, price list
requirements.txt      flask, sarvamai
hello_sarvam.py       One-line check that your Sarvam API key works
```

## Run it locally

Requires Python 3.10+ and a [Sarvam API key](https://dashboard.sarvam.ai).

```bash
pip install -r requirements.txt
echo "SARVAM_API_KEY=your_key_here" > .env
python app.py
```

Open http://localhost:5000. Without Upstash credentials, phone orders are kept in memory, which is fine for local testing.

Try the phone-order inbox without the voice agent:

```bash
curl -X POST http://localhost:5000/api/orders -H "Content-Type: application/json" -d '{"customer_name":"Ramesh","pickup_time":"shaam 6 baje","order_text":"do kilo chawal, ek litre tel, teen packet Maggi","language":"Hindi"}'
```

## Deploy on Vercel

1. Import the repo (or run `vercel deploy --prod` from this folder).
2. Add `SARVAM_API_KEY` under Settings → Environment Variables.
3. Under Storage, create an **Upstash for Redis** database and connect it to the project. This adds `KV_REST_API_URL` and `KV_REST_API_TOKEN`; `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` also work.
4. Redeploy.

## API

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/api/bill` | form: `audio` (or `text`), `price_list` (JSON), optional `current_bill` (JSON) | `transcript`, `language`, `items[]`, `discount` |
| `POST` | `/api/orders` | JSON: `order_text` (required), `customer_name`, `pickup_time`, `language`, `caller_phone`, `transcript`, `interaction_id` | `{"status": "ok", "order_id": "…"}` |
| `GET` | `/api/orders` | none | `{"orders": [...]}`, newest first |

## Setting up the voice agent

In Sarvam's Samvaad platform, create an agent with:
- **Greeting:** "Namaste! Main dukaan se bol raha hoon. Aapka order bataiye — kya kya chahiye?"
- **Languages:** Hindi plus the Indian languages your customers speak, with language detection on.
- **An API tool `place_order`:** `POST https://<your-deployment>/api/orders`, 10 s timeout. The agent fills `customer_name`, `pickup_time`, `order_text` and `transcript`. `language`, `caller_phone` and `interaction_id` come from the call's `language_name`, `user_identifier` and `interaction_id` variables.
- **Prompt rules:** take items with quantity and unit, read the full order back and get a yes before calling `place_order` once. Never quote prices, promise stock or upsell.

## Limitations

- `/api/orders` has no authentication. Add a shared secret header before real use.
- The price list lives in the shopkeeper's browser, so each device keeps its own.
- The inbox keeps the 50 most recent orders.
