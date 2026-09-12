"""Explicitly connected, local-first dashboard sources.

Adapters never acquire permissions in snapshot methods. Call snapshots on a
worker thread and invoke any permission request only from a user's action.
"""
