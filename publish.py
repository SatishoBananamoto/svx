#!/usr/bin/env python3
"""Publish caliber to PyPI.

Usage:
    kv_run(argv=["python3", "publish.py"], env="pypi")

Reads PYPI_TOKEN from environment, sets TWINE_USERNAME/TWINE_PASSWORD,
then calls twine upload directly (no subprocess, stays in same process).
"""
import os
import sys
import glob

token = os.environ.get("PYPI_TOKEN")
if not token:
    print("Error: PYPI_TOKEN not set")
    sys.exit(1)

# Set twine env vars so twine.cli picks them up
os.environ["TWINE_USERNAME"] = "__token__"
os.environ["TWINE_PASSWORD"] = token

# Find dist files
dist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
dists = glob.glob(os.path.join(dist_dir, "*"))
if not dists:
    print(f"Error: no files in {dist_dir}")
    sys.exit(1)

print(f"Uploading: {[os.path.basename(d) for d in dists]}")

# Call twine directly in-process (no subprocess)
sys.argv = ["twine", "upload"] + dists
from twine.__main__ import main
sys.exit(main())
