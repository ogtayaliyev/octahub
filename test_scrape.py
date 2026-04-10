import requests
import json
from urllib.parse import urljoin, urlparse

url = 'https://dooit.fr/'
try:
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
    res = session.get(url, verify=False, timeout=10)
    print("Status:", res.status_code)
    print("Length:", len(res.text))
    
    # simulate some basic parsing
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(res.text, 'html.parser')
    images = []
    for tag in soup.find_all(['img', 'source', 'picture']):
        for attr in ['src', 'data-src', 'srcset', 'data-original']:
            val = tag.get(attr)
            if val:
                images.append(val)
    print("Found img srcs:", len(images))
    if len(images) > 0:
        print("First few:", images[:5])
        
    videos = soup.find_all(['video', 'source'])
    print("Videos:", len(videos))
    
except Exception as e:
    print("Error:", e)
