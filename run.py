"""Top-level launcher — run from project root."""
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from tracker.main import main

if __name__ == "__main__":
    main()
