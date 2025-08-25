#!/usr/bin/env python3
"""
Test script to monitor connection behavior during API calls
"""
import requests
import time
import threading
from concurrent.futures import ThreadPoolExecutor

def test_connection_exhaustion():
    """Test if we can exhaust connections"""
    jellyfin_url = "http://192.168.68.56:8096"
    sessions = []
    
    print("Creating multiple sessions to test connection limits...")
    
    try:
        # Create many sessions rapidly
        for i in range(50):  # Try to create 50 concurrent connections
            session = requests.Session()
            sessions.append(session)
            
            try:
                response = session.get(f"{jellyfin_url}/System/Info", timeout=5)
                print(f"Session {i}: {response.status_code}")
            except Exception as e:
                print(f"Session {i}: ERROR - {e}")
                break
            
            time.sleep(0.1)  # Small delay
            
    finally:
        # Clean up sessions
        for session in sessions:
            session.close()

def test_session_reuse():
    """Test if reusing sessions helps"""
    jellyfin_url = "http://192.168.68.56:8096"
    
    # Single session, multiple requests
    session = requests.Session()
    try:
        for i in range(20):
            response = session.get(f"{jellyfin_url}/System/Info", timeout=5)
            print(f"Reused session request {i}: {response.status_code}")
            time.sleep(0.5)
    except Exception as e:
        print(f"Session reuse error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    print("=== Testing Connection Exhaustion Theory ===")
    print("\n1. Testing multiple new sessions:")
    test_connection_exhaustion()
    
    print("\n2. Testing session reuse:")
    test_session_reuse()