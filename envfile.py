#!/usr/bin/env python3
"""Read .env into the process environment. No dependency; the file is gitignored.

Existing environment variables win, so GitHub Actions secrets are never
shadowed by a stale .env that happens to be lying around.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def load(path=None):
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
