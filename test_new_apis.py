"""
Test NSE endpoints for:
1. Delivery % — quote-equity API
2. FII/DII flow data — fiidiiTradeReact
"""
import requests, json, time

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}

session = requests.Session()
session.headers.update(headers)

# Warm up
print("Warming up NSE session...")
session.get("https://www.nseindia.com/", timeout=12)
time.sleep(1)

# ── TEST 1: Delivery % via quote-equity ─────────────────────
print("\n=== TEST 1: Delivery % (RELIANCE) ===")
r = session.get("https://www.nseindia.com/api/quote-equity?symbol=RELIANCE", timeout=12)
r.encoding = "utf-8"
print(f"Status: {r.status_code}  len={len(r.content)}")
if r.status_code == 200 and len(r.content) > 100:
    data = r.json()
    # Show top-level keys
    print(f"Top keys: {list(data.keys())}")
    # deliveryAndMarketCap section
    dmc = data.get("securityWiseDP") or data.get("deliveryAndMarketCap") or {}
    print(f"securityWiseDP keys: {list(dmc.keys()) if isinstance(dmc, dict) else type(dmc)}")
    print(f"Full securityWiseDP: {json.dumps(dmc, indent=2)[:600]}")
    # Also check priceInfo
    pi = data.get("priceInfo", {})
    print(f"\npriceInfo: {json.dumps(pi, indent=2)[:400]}")
else:
    print(f"Response: {r.text[:300]}")

time.sleep(1)

# ── TEST 2: FII/DII daily flow ──────────────────────────────
print("\n=== TEST 2: FII/DII Daily Flow ===")
r2 = session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=12)
r2.encoding = "utf-8"
print(f"Status: {r2.status_code}  len={len(r2.content)}")
if r2.status_code == 200 and len(r2.content) > 100:
    data2 = r2.json()
    if isinstance(data2, list):
        print(f"Got {len(data2)} records")
        print(f"Fields: {list(data2[0].keys()) if data2 else 'empty'}")
        print("\nAll records:")
        for rec in data2[:10]:
            print(f"  {json.dumps(rec)}")
    else:
        print(f"Data type: {type(data2)}")
        print(json.dumps(data2, indent=2)[:800])
else:
    print(f"Response: {r2.text[:300]}")

time.sleep(1)

# ── TEST 3: FII/DII historical (last 5 days) ────────────────
print("\n=== TEST 3: FII/DII Historical ===")
r3 = session.get("https://www.nseindia.com/api/fiiDiiData", timeout=12)
r3.encoding = "utf-8"
print(f"Status: {r3.status_code}  len={len(r3.content)}")
if r3.status_code == 200 and len(r3.content) > 100:
    data3 = r3.json()
    print(json.dumps(data3, indent=2)[:1000])
else:
    print(f"Response: {r3.text[:300]}")

print("\nDONE")
