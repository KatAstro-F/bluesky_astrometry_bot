import argparse
import re
import sys
from urllib.parse import urlparse, parse_qs

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://app.astrobin.com/",
}


def extract_hash(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "i" in query and query["i"]:
        return query["i"][0]

    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "i" and len(parts) >= 2:
        return parts[1]
    return parts[0]


def fetch_image_record(image_hash, headers=None, timeout=30):
    api_url = f"https://app.astrobin.com/api/v2/images/image/?hash={image_hash}"
    resp = requests.get(api_url, headers=headers or HEADERS, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"API request failed: {resp.status_code} {api_url}")
    data = resp.json()
    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"No results for hash {image_hash}")
    return results[0]


def pick_best_thumbnail(thumbnails):
    best_url = None
    best_width = -1
    for thumb in thumbnails or []:
        url = thumb.get("url") or ""
        match = re.search(r"_(\d+)x(\d+)_", url)
        width = -1
        if match:
            try:
                width = int(match.group(1))
            except ValueError:
                width = -1
        if width > best_width:
            best_width = width
            best_url = url
    return best_url


def get_main_image_url(astrobin_url, headers=None, timeout=30):
    image_hash = extract_hash(astrobin_url)
    if not image_hash:
        return None
    record = fetch_image_record(image_hash, headers=headers, timeout=timeout)
    return pick_best_thumbnail(record.get("thumbnails"))


def download_file(url, out_path):
    resp = requests.get(url, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download: {resp.status_code} {url}")
    with open(out_path, "wb") as f:
        f.write(resp.content)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch the main (largest available) AstroBin image URL from a public link."
    )
    parser.add_argument("url", help="AstroBin page URL (e.g. https://app.astrobin.com/u/... ?i=hash)")
    parser.add_argument("--download", metavar="PATH", help="Optional file path to download the image")
    args = parser.parse_args()

    best_url = get_main_image_url(args.url)
    if not best_url:
        print("No image URL found for the provided AstroBin link.", file=sys.stderr)
        return 3

    print(best_url)
    if args.download:
        download_file(best_url, args.download)
        print(f"Saved to {args.download}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
