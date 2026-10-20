import os
import sys

# Put server/ on the import path so tests can import main, database, models, etc.
sys.path.insert(0, os.path.dirname(__file__))

# Set a test secret so authentication.py doesn't fail at import time.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-production")
