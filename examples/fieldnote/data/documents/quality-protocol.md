# Observation quality and aggregation protocol

## Fictional protocol
This protocol is authored for the fictional Fieldnote demonstration corpus. It defines application defaults, not a real safety or engineering standard.

## Quality states
`valid` means accepted for this dataset's default summaries. It is not proof that a physical instrument was accurate. `suspect` retains a recorded numeric value for inspection but excludes it from default averages, minima, maxima, comparisons, and trends. `missing` means a scheduled reading has SQL NULL value. Never replace a missing value with zero. Use `quality = 'valid' AND value IS NOT NULL` for default numerical summaries.

## Counts and denominators
All scheduled records, including missing and suspect ones, count toward the scheduled denominator. Valid coverage is valid-record count divided by all scheduled-record count, multiplied by 100. Missing and suspect percentages use the same denominator. Report retained counts with summaries because coverage differs by sensor and period. SQL COUNT(*) counts scheduled rows; COUNT(value) counts recorded numeric values, including suspect values.

## Comparisons and uncertainty
Compare the same metric and unit in the same explicit UTC window. Do not average across different sensor metrics. Do not invent observations, impute missing values silently, or extrapolate four days of data to a season. If the user explicitly asks for suspect-inclusive results, label that choice and show how it differs from the valid-only result. For an undefined metric, site, or time period, clarify the question before claiming a comparison.
