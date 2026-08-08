"""Capability-oriented worker providers."""
"""Provider implementations are deliberately imported by their caller.

Keeping this package initializer empty avoids loading optional heavyweight
providers while the registry and security modules establish their contracts.
"""
"""Provider package.

Concrete providers are intentionally imported from their own modules. Keeping
this package initializer dependency-free prevents the model registry's import
of ``providers.base`` from loading inference or security code prematurely.
"""
