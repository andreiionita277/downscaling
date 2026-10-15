import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (research script; MSc thesis)"}
resp = requests.get("https://www.stringmeteo.com/stations/rilamon/", headers=HEADERS, timeout=15)
resp.encoding = 'utf-8'
soup = BeautifulSoup(resp.text, 'html.parser')

print(soup.get_text(separator='\n', strip=True)[:3000])
