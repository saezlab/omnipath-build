from omnipath_build.reference.reporting import canonical_identity_statistics


def test_absent_full_keys_identify_chemicals_without_inventing_reference_matches():
    original = dict(
        entity_counts=dict(
            eligible_entities=10, fully_resolved=4, partially_resolved=0, unresolved=6
        ),
        unresolved_reasons=[
            dict(unresolved_reason=k, bundles=n, entity_keys=n)
            for k, n in [
                ("supplied_inchikey_not_in_reference", 2),
                ("derived_inchikey_not_in_reference", 1),
                ("conflicting_identifier_evidence", 3),
            ]
        ],
    )
    report = canonical_identity_statistics(original)
    assert report["entity_counts"]["fully_resolved"] == 7
    assert report["entity_counts"]["unresolved"] == 3
    assert report["reference_match_counts"]["fully_resolved"] == 4
    assert original["entity_counts"]["fully_resolved"] == 4
    assert report["unresolved_reasons"] == original["unresolved_reasons"][2:]
    assert canonical_identity_statistics(report) == report
