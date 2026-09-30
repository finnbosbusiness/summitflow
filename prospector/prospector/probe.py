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

    print("=== apify/google-search-scraper ===")
    url = "https://www.google.co.uk/search?q=" + quote_plus("kitchen showroom Solihull") + "&gl=uk&hl=en"
    items = run_actor(token, "apify/google-search-scraper", {
        "queries": url, "resultsPerPage": 10, "maxPagesPerQuery": 1, "mobileResults": False,
    }, timeout_s=900)
    print(f"{len(items)} items")
    shape(items[0] if items else {})

    print("=== apify/facebook-ads-scraper ===")
    lib = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=GB"
           "&q=" + quote_plus("Madina Kitchens") + "&search_type=keyword_unordered&media_type=all")
    items = run_actor(token, "apify/facebook-ads-scraper", {
        "startUrls": [{"url": lib}], "resultsLimit": 5, "activeStatus": "active",
    }, timeout_s=900)
    print(f"{len(items)} items")
    shape(items[0] if items else {})


if __name__ == "__main__":
    main()
