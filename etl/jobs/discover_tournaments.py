import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv
import time

load_dotenv()

BASE_URL = "https://api.osirion.gg/fortnite/v1"
API_KEY = os.getenv("API_KEY") 
SEEN_FILE = "data/events/seen_tournaments.json"

INTERVAL_SECONDS = 2592000  # last 30 days


def fetch_tournaments(interval_seconds: int = INTERVAL_SECONDS):
    url = f"{BASE_URL}/tournaments?intervalS={interval_seconds}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    return data


def check_for_new_tournaments():
    pass


if __name__ == "__main__":
    try:
        check_for_new_tournaments()
    except Exception as e:
        print(f"Error: {e}")
