"""Authentication: accounts, one-time codes, sessions and assignments.

Each concern sits behind a small interface so the local implementation can be replaced
(PostgreSQL, an identity provider, MFA, another delivery channel) without touching the
permission model in core/permissions.py or the data scoping in core/rbac.py.
"""
