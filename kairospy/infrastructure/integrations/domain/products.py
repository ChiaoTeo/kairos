from __future__ import annotations

"""Compatibility export for the shared ProductFamily type.

ProductFamily is a business concept shared by Account, Market, Execution,
Reference, and Integration.  Its canonical definition lives in the domain
model; this module remains only so existing integration imports do not create
a second enum during the migration.
"""

from kairospy.domain.reference import ProductFamily


__all__ = ["ProductFamily"]
