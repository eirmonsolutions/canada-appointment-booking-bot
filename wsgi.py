#!/usr/bin/env python3
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from visa_bot import app as application # Assumes your Flask app is named 'app' in visa_bot.py

if __name__ == "__main__":
    application.run()