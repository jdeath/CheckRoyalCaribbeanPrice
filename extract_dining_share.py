"""
Extract diningSelection from a Royal Caribbean booking token via the RSC stream.

GET https://www.royalcaribbean.com/usa/en/booked/overview?token=...&country=USA
Headers: Accept: text/x-component  |  RSC: 1

This returns the Next.js React Server Component payload as plain text.
diningSelection appears as unescaped JSON — a single regex pull extracts it.
"""

import re
import json
import requests

TOKEN = (

"XXXX"
   
)
COUNTRY = "USA"

RSC_URL = "https://www.royalcaribbean.com/usa/en/booked/overview"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/x-component",
    "RSC": "1",
}


def _extract_json_array(text: str, key: str):
    """Find "key": [ ... ] handling arbitrary nesting."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not m:
        return None
    start = m.end() - 1  # position of opening [
    depth, i = 0, start
    in_string, escape = False, False
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\" and in_string:
            escape = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        i += 1
    return None


def extract_fields(token: str = TOKEN, country: str = COUNTRY) -> dict:
    resp = requests.get(RSC_URL, params={"token": token, "country": country}, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    text = resp.text
    result = {}

    dining = _extract_json_array(text, "diningSelection")
    if dining is not None:
        result["diningSelection"] = dining

    prices = _extract_json_array(text, "prices")
    if prices is not None:
        result["prices"] = prices
    
    pricingAddOns = _extract_json_array(text, "pricingAddOns")
    if pricingAddOns is not None:
        result["pricingAddOns"] = pricingAddOns
        
    return result


if __name__ == "__main__":
    data = extract_fields()
    print(json.dumps(data, indent=2))
