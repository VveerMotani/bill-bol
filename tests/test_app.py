import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SARVAM_API_KEY", "test-key")
import app as bill_app  # noqa: E402

PRICES = [{"name": "Rice", "unit": "kg", "price": 60}, {"name": "Milk", "unit": "litre", "price": 56}]


@pytest.fixture
def client(monkeypatch):
    # Use the in-memory inbox, never a real Redis
    monkeypatch.setattr(bill_app, "REDIS_URL", None)
    bill_app.local_orders.clear()
    return bill_app.app.test_client()


def test_phone_order_is_stored_and_listed_newest_first(client):
    for name in ("Priya", "Ramesh"):
        res = client.post("/api/orders", json={"customer_name": name, "pickup_time": "shaam 6 baje",
                                               "order_text": "do kilo chawal", "language": "Hindi"})
        assert res.status_code == 200
        assert res.get_json()["status"] == "ok"

    orders = client.get("/api/orders").get_json()["orders"]
    assert [o["customer_name"] for o in orders] == ["Ramesh", "Priya"]
    assert orders[0]["order_text"] == "do kilo chawal"
    assert orders[0]["id"] and orders[0]["received_at"]


def test_phone_order_keeps_indian_scripts(client):
    text = "ஐந்து கிலோ அரிசி, மூன்று பாக்கெட் Maggi"
    client.post("/api/orders", json={"order_text": text})
    assert client.get("/api/orders").get_json()["orders"][0]["order_text"] == text


def test_phone_order_needs_order_text(client):
    res = client.post("/api/orders", json={"customer_name": "Priya", "order_text": "  "})
    assert res.status_code == 400
    assert client.get("/api/orders").get_json()["orders"] == []


def test_phone_order_ignores_unknown_fields(client):
    client.post("/api/orders", json={"order_text": "ek litre doodh", "price": 999, "total": 1})
    order = client.get("/api/orders").get_json()["orders"][0]
    assert "price" not in order and "total" not in order


def test_inbox_keeps_latest_50(client):
    for i in range(55):
        client.post("/api/orders", json={"order_text": f"order {i}"})
    orders = client.get("/api/orders").get_json()["orders"]
    assert len(orders) == 50
    assert orders[0]["order_text"] == "order 54"


def test_bill_from_phone_order_text_skips_speech_to_text(client, monkeypatch):
    seen = {}

    def fake_parse(transcript, price_list, current_bill=None):
        seen.update(transcript=transcript, price_list=price_list, current_bill=current_bill)
        return {"items": [{"spoken_name": "चावल", "english_name": "Rice", "price_list_item": "Rice",
                           "quantity": 2, "unit": "kg", "spoken_rate": None}],
                "discount": {"type": "none", "value": 0}}

    monkeypatch.setattr(bill_app, "parse_order", fake_parse)
    res = client.post("/api/bill", data={"text": "दो किलो चावल", "language": "Hindi",
                                          "price_list": json.dumps(PRICES)})
    assert res.status_code == 200
    body = res.get_json()
    assert body["transcript"] == "दो किलो चावल"
    assert body["items"][0]["price_list_item"] == "Rice"
    assert seen == {"transcript": "दो किलो चावल", "price_list": PRICES, "current_bill": None}


def test_bill_without_audio_or_text_is_rejected(client):
    res = client.post("/api/bill", data={"price_list": json.dumps(PRICES)})
    assert res.status_code == 400


def test_parse_order_sends_price_list_and_reads_json(monkeypatch):
    captured = {}

    class FakeChat:
        def completions(self, **kwargs):
            captured.update(kwargs)
            message = type("M", (), {"content": json.dumps({"items": [], "discount": {"type": "flat", "value": 100}})})
            return type("R", (), {"choices": [type("C", (), {"message": message})]})

    monkeypatch.setattr(bill_app, "client", type("FakeClient", (), {"chat": FakeChat()})())
    result = bill_app.parse_order("सौ रुपये कम कर दो", PRICES)

    assert result["discount"] == {"type": "flat", "value": 100}
    user_msg = captured["messages"][1]["content"]
    assert "Rice | kg | 60" in user_msg and "सौ रुपये कम कर दो" in user_msg
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["temperature"] == 0


def test_parse_order_in_edit_mode_includes_current_bill(monkeypatch):
    captured = {}

    class FakeChat:
        def completions(self, **kwargs):
            captured.update(kwargs)
            message = type("M", (), {"content": '{"items": [], "discount": {"type": "none", "value": 0}}'})
            return type("R", (), {"choices": [type("C", (), {"message": message})]})

    monkeypatch.setattr(bill_app, "client", type("FakeClient", (), {"chat": FakeChat()})())
    bill_app.parse_order("अंडे हटा दो", PRICES, current_bill='{"items": []}')

    assert "Editing an existing bill" in captured["messages"][0]["content"]
    assert 'Current bill: {"items": []}' in captured["messages"][1]["content"]
