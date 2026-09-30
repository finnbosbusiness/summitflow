"""Print the shape of an Apify actor's output (keys and value types, plus a
few short sample values) so parsers can be written against the real format.

    APIFY_TOKEN=... python -m prospector.probe
"""

import json
import os
from urllib.parse import quote_plus

from .apify import run_actor


def shape(value, depth=0, max_depth=4):
    pad = "  " * depth
    if isinstance(value, dict):
        for k, v in list(value.items())[:60]:
            if isinstance(v, (dict, list)) and depth < max_depth:
                print(f"{pad}{k}: {type(v).__name__}{'[' + str(len(v)) + ']' if isinstance(v, list) else ''}")
                shape(v, depth + 1, max_depth)
            else:
                print(f"{pad}{k}: {type(v).__name__} = {json.dumps(v)[:80]}")
    elif isinstance(value, list) and value:
        shape(value[0], depth, max_depth)


def main():
    token = os.environ["APIFY_TOKEN"]
    base = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=GB"
            "&search_type=keyword_unordered&media_type=all&q=")
    urls = [base + quote_plus("kitchens"), base + quote_plus("bathrooms")]
    items = run_actor(token, "apify/facebook-ads-scraper", {
        "startUrls": [{"url": u} for u in urls], "resultsLimit": 3, "activeStatus": "active",
    }, timeout_s=900)
    per_url = {}
    for it in items:
        per_url[it.get("inputUrl")] = per_url.get(it.get("inputUrl"), 0) + 1
    print(f"resultsLimit=3 with 2 URLs -> {len(items)} items total")
    for u, n in per_url.items():
        print(f"  {n} from ...{(u or '')[-20:]}")


if __name__ == "__main__":
    main()
