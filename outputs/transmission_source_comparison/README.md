# Transmission source comparison

## Result

The topology stage of the PyPSA clustering methodology was applied using the
model's 38-district to nine-balancing-area map. Capacity aggregation cannot be
completed from this workbook because it has no MW/MVA rating and no consistent
structured voltage or conductor rating.

## Coverage

- Source workbook records: 2,241
- TANTRANSCO records: 154
- Records naming exactly one model area: 47
- Records naming exactly two model areas anywhere in the name: 5
- Records with unambiguous district matches on opposite endpoints: 3
- Current model corridors: 22
- Current corridors with conservative district-explicit evidence: 3
- Current model total corridor capacity: 98,500 MW

## Interpretation

The current 22 corridor ratings are independent assumptions read from
`9_BA/Grid_capacity.txt`; they are not recoverable or validated by the supplied
workbook. Line length is not capacity. To finish the PyPSA-style aggregation,
add a structured table with source substation, destination substation, endpoint
districts (or coordinates), voltage, number of circuits/conductors, and a thermal
rating in MVA/MW or enough conductor data to calculate one.
