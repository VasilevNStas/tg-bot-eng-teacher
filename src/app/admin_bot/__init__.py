"""Separate admin bot process.

Run with ``python -m app.admin_bot``. It shares the database with the user bot
but uses its own token and allowlist, so the two control planes stay isolated.
"""
