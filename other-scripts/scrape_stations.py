import requests
from bs4 import BeautifulSoup, NavigableString
from urllib.parse import urljoin
import csv
import re
from collections import Counter

URL = "https://www.stringmeteo.com/stations/index.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (research script; MSc thesis on ERA5 downscaling, contact: nandhnigabriela@yahoo.com)"}

resp = requests.get(URL, headers=HEADERS, timeout=15)
resp.encoding = 'utf-8'
soup = BeautifulSoup(resp.text, 'html.parser')

categories = {"Стандартни", "НЕстандартни", "Трайно неработещи"}
current_category = "Unknown"
stations = []
number_pattern = re.compile(r'^(\d+)\.\s*(.+)$')

for node in soup.body.descendants:
    if isinstance(node, NavigableString):
        text = node.strip()
        if text in categories:
            current_category = text
    elif node.name == 'a':
        href = node.get('href', '')
        text = node.get_text(strip=True)
        match = number_pattern.match(text)
        if match and href:
            absolute_url = urljoin(URL, href)
            stations.append({
                'number': match.group(1),
                'name': match.group(2),
                'url': absolute_url,
                'category': current_category
            })

seen = set()
unique_stations = []
for s in stations:
    if s['url'] not in seen:
        seen.add(s['url'])
        unique_stations.append(s)

with open('stations_list.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['number', 'name', 'url', 'category'])
    writer.writeheader()
    writer.writerows(unique_stations)

print(f"Saved {len(unique_stations)} stations to stations_list.csv")
cat_counts = Counter(s['category'] for s in unique_stations)
for cat, count in cat_counts.items():
    print(f"  {cat}: {count}")
