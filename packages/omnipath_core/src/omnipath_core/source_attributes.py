"""Ordinary source attributes retained without changing Biolink statement identity.

BioPAX supplies reaction context terms; source-specific attributes retain their
own names where Biolink has no equivalent. These are not edge qualifiers.
"""

CONVERSION_DIRECTION = "biopax:conversionDirection"
CELLULAR_LOCATION = "biopax:cellularLocation"
TRAIT_TYPE = "macdb:trait_type"
PARTICIPANT_ROLE = "connectomedb:participant_role"
SOURCE_RECORD_REFERENCE = "prov:wasDerivedFrom"
SOURCE_RECORD_SHA256_PREFIX = "urn:sha256:"
SOURCE_RECORD_TYPE = "dcterms:type"
