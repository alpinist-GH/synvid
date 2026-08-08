"""Capability-oriented worker providers."""
"""Provider implementations are deliberately imported by their caller.

Keeping this package initializer empty avoids loading optional heavyweight
providers while the registry and security modules establish their contracts.
"""
