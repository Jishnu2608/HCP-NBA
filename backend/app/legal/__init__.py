"""Legal documents, consent records and privacy requests.

The documents themselves are versioned JSON files in `legal/documents/`. They are drafts:
facts only the operator can supply are placeholders filled from `NBA_LEGAL_*` settings,
and every page says it is pending legal review until those are set. Nothing here makes the
application compliant with any law; it records what each person accepted, lets them
change it, and routes their requests to a person who can act on them.
"""
