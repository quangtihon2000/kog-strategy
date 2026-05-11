"""Postgres SignalMessage repository. Spec section 5.7.

Persists every validated signal with `UNIQUE(channel_id, message_id)` so
update detection can map Telegram message ids back to internal `signal_id`s.
"""
