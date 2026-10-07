"""Ordinary source attributes retained without changing Biolink statement identity.

BioPAX supplies reaction context terms; source-specific attributes retain their
own names where Biolink has no equivalent. These are not edge qualifiers.
"""

CONVERSION_DIRECTION = "biopax:conversionDirection"
CELLULAR_LOCATION = "biopax:cellularLocation"
# Side of the membrane a transported participant is on in a Rhea equation:
# "in" or "out". A side, not a compartment.
TRANSPORT_SIDE = "rhea:transport_side"
TRAIT_TYPE = "macdb:trait_type"
PARTICIPANT_ROLE = "connectomedb:participant_role"
