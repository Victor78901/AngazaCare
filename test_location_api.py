#!/usr/bin/env python
"""Quick test script to verify the location API is working."""
import requests
import json

# Test the API endpoint (you'll need to be logged in for this to work)
BASE_URL = "http://127.0.0.1:5000"

# Test with known Nairobi coordinates
test_data = {
    "score": 5,
    "lat": -1.2921,
    "lng": 36.8219
}

print("Testing /api/recommend endpoint...")
print(f"Test data: {test_data}")

try:
    response = requests.post(
        f"{BASE_URL}/api/recommend",
        json=test_data,
        headers={"Content-Type": "application/json"}
    )
    
    print(f"\nResponse status: {response.status_code}")
    print(f"Response headers: {dict(response.headers)}")
    
    try:
        data = response.json()
        print(f"\nResponse data:")
        print(json.dumps(data, indent=2))
        
        if response.status_code == 200:
            print("\n✓ API is working!")
            if data.get('facilities'):
                print(f"✓ Found {len(data['facilities'])} facilities")
            elif data.get('fallback'):
                print("✓ No facilities found, but fallback message provided")
        else:
            print(f"\n✗ API returned error: {data.get('error', 'Unknown error')}")
    except json.JSONDecodeError:
        print(f"\n✗ Invalid JSON response: {response.text[:500]}")
        
except requests.exceptions.ConnectionError:
    print("\n✗ Could not connect to the server. Is it running?")
except Exception as e:
    print(f"\n✗ Test failed with error: {e}")

print("\n" + "="*60)
print("NOTE: This test requires you to be logged in.")
print("The API endpoint requires authentication (@login_required).")
print("="*60)