#!/usr/bin/env python3
"""
Test script to verify clickable links in activity tab work correctly
"""

import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from flask import Flask, url_for
from app import create_app
from app.utils.helpers import encode_url_component, generate_url_slug

def test_clickable_links():
    """Test that media detail URLs are generated correctly"""
    app = create_app()
    
    with app.test_request_context():
        # Test data simulating what we'd have in the template
        server_nickname = "Plex++"
        library_name = "Movies"
        
        # Test movie link generation
        movie_media_id = 123
        movie_title = "Despicable Me"
        movie_url = url_for('libraries.media_detail', 
                           server_nickname=server_nickname, 
                           library_name=encode_url_component(library_name), 
                           media_id=movie_media_id, 
                           slug=generate_url_slug(movie_title))
        
        # Test TV show link generation  
        show_media_id = 456
        show_title = "Breaking Bad"
        show_url = url_for('libraries.media_detail', 
                          server_nickname=server_nickname, 
                          library_name=encode_url_component(library_name), 
                          media_id=show_media_id, 
                          slug=generate_url_slug(show_title))
        
        print("Testing clickable links generation:")
        print(f"Movie URL: {movie_url}")
        print(f"Show URL: {show_url}")
        
        # Verify URL patterns
        expected_movie_pattern = f"/library/{server_nickname}/Movies/{movie_media_id}/despicable-me"
        expected_show_pattern = f"/library/{server_nickname}/Movies/{show_media_id}/breaking-bad"
        
        assert expected_movie_pattern == movie_url, f"Expected {expected_movie_pattern}, got {movie_url}"
        assert expected_show_pattern == show_url, f"Expected {expected_show_pattern}, got {show_url}"
        
        print("✓ Clickable links generation working correctly!")

if __name__ == '__main__':
    test_clickable_links()