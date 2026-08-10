import os
import requests
import time
import json

from dotenv import load_dotenv

load_dotenv()

url = "http://127.0.0.1:8000/api/analyze"
payload = {
    "start_date": "2020-01-01",
    "end_date": "2026-12-31"
}
# Memanggil lewat HTTP berarti tunduk pada handshake antar-service; tanpa kunci
# jawabannya 401.
headers = {
    "Content-Type": "application/json",
    "X-Internal-Key": os.getenv("INTERNAL_API_KEY", "")
}

print("Memulai proses benchmark untuk endpoint /api/analyze...")
print(f"Payload: {payload}")

start_time = time.time()
try:
    response = requests.post(url, json=payload, headers=headers)
    end_time = time.time()
    
    elapsed_time = end_time - start_time
    
    print(f"\n[BENCHMARK HASIL]")
    print(f"Status Code: {response.status_code}")
    print(f"Durasi Waktu: {elapsed_time:.2f} detik")
    
    if response.status_code == 200:
        print("\nResponse Body:")
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    else:
        print(f"\nError: {response.text}")
        
except requests.exceptions.RequestException as e:
    print(f"Terjadi kesalahan saat menghubungi API: {e}")
