"""Redis Streams writer + stats counters. Spec section 7.

Writes signals to `signals:raw`, updates to `signals:updates`, OCR queue,
audit sample stream, and per-channel stats hash. Always uses
`XADD ... MAXLEN ~ N` (approximate trim) per spec.
"""
