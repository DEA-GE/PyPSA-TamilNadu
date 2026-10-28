import pandas as pd

from scripts.export_tn_resolved_transactions import select_resolved_transactions


def _row(**updates):
    row = {
        "direction": "export",
        "plant_location_state": "Tamil Nadu",
        "beneficiary_state": "Karnataka",
        "confidence": "verified",
        "included_in_approved": True,
        "exclusion_reason": "",
        "tn_BA": "Vellore",
    }
    row.update(updates)
    return row


def test_selects_verified_tn_flows_and_explicit_puducherry_exception():
    approved = pd.DataFrame([
        _row(),
        _row(direction="import", plant_location_state="Telangana", beneficiary_state="Tamil Nadu"),
        _row(confidence="probable_not_verified"),
        _row(confidence="probable_not_verified", source_family="GNA_TGNA_STATE_VIEW",
             channel="GNA", source_entity="TamilNadu", buyer_entity="UPPCL",
             counterparty_entity="UttarPradesh", beneficiary_state="Uttar Pradesh"),
        _row(direction="internal", beneficiary_state="Tamil Nadu"),
    ])
    excluded = pd.DataFrame([
        _row(
            beneficiary_state="Puducherry",
            included_in_approved=False,
            exclusion_reason="PUDUCHERRY_ENCLAVE_BOUNDARY_UNRESOLVED",
        ),
        _row(
            beneficiary_state="Unknown",
            confidence="unattributed",
            included_in_approved=False,
            exclusion_reason="UNKNOWN_DESTINATION",
        ),
    ])

    result = select_resolved_transactions(approved, excluded)

    assert len(result) == 4
    assert set(result["inclusion_basis"]) == {
        "resolved_import",
        "resolved_export_external",
        "verified_puducherry_exception",
        "resolved_tn_export_register",
    }
    assert set(result["counterparty_state"]) == {
        "Telangana", "Karnataka", "Puducherry", "Uttar Pradesh"
    }
    assert set(result["external_gateway"]) == {
        "Andhra Pradesh", "Karnataka", "Puducherry", "Western Region"
    }
    assert result.loc[
        ~result["inclusion_basis"].eq("resolved_tn_export_register"), "confidence"
    ].eq("verified").all()
