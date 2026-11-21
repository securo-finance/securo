"""Data plugins: manifests, the catalog index, and syncing a workspace to them.

A data plugin is one `securo-plugin.json` that contributes category groups,
categories and rules. Nothing it ships runs: Securo validates the manifest
and creates the items itself, recording which plugin each one came from so
it can later be updated or removed without touching what the user changed.
"""
