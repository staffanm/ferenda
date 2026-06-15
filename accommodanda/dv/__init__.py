"""DV vertical — court decisions (vägledande avgöranden, domstol).

Owns its full chain: ``download`` (rättspraxis API harvest) → ``identity``
(entity resolution across the API and legacy stores) → ``parse`` (API
``innehall`` path) / ``legacy`` + ``word`` (the legacy OOXML path) → typed
model (``model``) → JSON artifact.
"""
