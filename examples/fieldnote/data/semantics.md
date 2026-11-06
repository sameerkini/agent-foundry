# Fieldnote data semantics

All sites, observations, maintenance notes, and protocols are fictional and newly authored for this project. The database contains 3 sites, 9 sensors, 108 scheduled observations (101 valid, 4 suspect, 3 missing), and 6 maintenance events.

Use explicit sensor units. Default numerical summaries exclude suspect and missing readings; a missing reading is SQL NULL and never zero. Coverage counts all scheduled rows in its denominator. Dates and timestamps are UTC. An arithmetic sample mean is not a time-weighted mean. The six manifest documents give the detailed protocols.

Rebuild reproducibly with `python scripts/build_demo.py`. Evaluation references are kept outside the runtime workspace; they are not retrieval sources.
