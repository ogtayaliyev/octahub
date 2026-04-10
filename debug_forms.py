import requests
from bs4 import BeautifulSoup

url = "https://www.dooit.fr/contactus"
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

try:
    res = requests.get(url, headers=headers, timeout=10, verify=False)
    print(f"Status: {res.status_code}")
    soup = BeautifulSoup(res.text, 'html.parser')
    forms = soup.find_all('form')
    print(f"Forms found: {len(forms)}")
    for f in forms:
        print(f"Form ID: {f.get('id')} Class: {f.get('class')}")
    
    inputs = soup.find_all(['input', 'textarea'])
    print(f"Total inputs found: {len(inputs)}")
    for i in inputs[:5]:
        print(f"Input: {i.get('name')} {i.get('type')}")

except Exception as e:
    print(f"Error: {e}")
