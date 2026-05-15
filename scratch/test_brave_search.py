import os
import requests
import json
from dotenv import load_dotenv

def test_brave_search():
    load_dotenv()
    api_key = os.environ.get('BRAVE_SEARCH_API_KEY')
    if not api_key:
        print("BRAVE_SEARCH_API_KEY not found in .env")
        return

    query = "current weather in London"
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key
    }
    params = {"q": query, "count": 5}

    print(f"Searching for: {query}")
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code == 200:
        data = response.json()
        print("Search successful!")
        # print(json.dumps(data, indent=2))
        web_results = data.get('web', {}).get('results', [])
        for i, result in enumerate(web_results):
            print(f"{i+1}. {result.get('title')}")
            print(f"   URL: {result.get('url')}")
            print(f"   Snippet: {result.get('description')}")
            print("-" * 20)
    else:
        print(f"Search failed with status code {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    test_brave_search()
