"""Run an Apify actor and return its dataset items."""

import time

import requests

API = "https://api.apify.com/v2"


def run_actor(token: str, actor: str, payload: dict, timeout_s: int = 4 * 3600):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{API}/acts/{actor.replace('/', '~')}/runs", json=payload, headers=headers, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Apify {actor} failed to start ({r.status_code}): {r.text[:500]}")
    run = r.json()["data"]

    deadline = time.time() + timeout_s
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise TimeoutError(f"Apify run {run['id']} still running after {timeout_s}s")
        time.sleep(15)
        r = requests.get(f"{API}/actor-runs/{run['id']}", headers=headers, timeout=60)
        r.raise_for_status()
        run = r.json()["data"]
    if run["status"] != "SUCCEEDED":
        reason = run.get("statusMessage") or run.get("exitCode") or "no reason given"
        raise RuntimeError(f"Apify {actor} run {run['id']} ended with status {run['status']}: {reason}")

    items, offset = [], 0
    while True:
        r = requests.get(
            f"{API}/datasets/{run['defaultDatasetId']}/items",
            params={"clean": "true", "offset": offset, "limit": 1000},
            headers=headers,
            timeout=120,
        )
        r.raise_for_status()
        page = r.json()
        items.extend(page)
        if len(page) < 1000:
            return items
        offset += len(page)
