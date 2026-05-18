import os
import json
from dotenv import load_dotenv
from conversational_server.openai_web_search import (
    call_brave_web_search,
    build_web_search_tool_output
)

def verify_integration():
    load_dotenv()
    api_key = os.environ.get('BRAVE_SEARCH_API_KEY')
    if not api_key:
        print("BRAVE_SEARCH_API_KEY not found")
        return

    query = "latest SpaceX launch"
    print(f"Testing Brave Search integration for query: {query}")
    
    try:
        payload = call_brave_web_search(api_key, query, count=3)
        print("API Call Successful")
        
        output_str = build_web_search_tool_output(query, payload=payload, max_sources=3)
        output = json.loads(output_str)
        
        print("\nTool Output Format Check:")
        print(f"OK: {output.get('ok')}")
        print(f"Query: {output.get('query')}")
        print(f"Summary starts with: {output.get('summary')[:100]}...")
        print(f"Sources count: {len(output.get('sources', []))}")
        
        if output.get('ok') and output.get('summary') and output.get('sources'):
            print("\nINTEGRATION VERIFIED SUCCESSFUL")
        else:
            print("\nINTEGRATION FAILED: Missing fields in output")
            
    except Exception as e:
        print(f"INTEGRATION FAILED with error: {e}")

if __name__ == "__main__":
    # Add project root to sys.path to allow imports from conversational_server
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    
    verify_integration()
