# Units time and schema guide

## Fictional dataset boundaries
This newly authored Fieldnote demonstration contains three fictional sites, nine sensors, and observations from 2026-04-01 through 2026-04-04. It contains only fictional research operations data and no live station feed.

## Tables and identifiers
`sites` identifies ALP (Alpine Ridge), MAR (Marsh Observatory), and PIN (Pine Valley). `sensors` joins to sites with site_id and declares each metric and unit. `readings` joins to sensors with sensor_id. `maintenance` also joins to sensors with sensor_id. event_date gives a UTC calendar date, not a precise time, so it cannot by itself establish the ordering of two events within that day.

## Metric semantics
Air temperature is measured in degC, relative humidity in %, and battery voltage in V. Convert degC to degF with degF = degC * 9 / 5 + 32 only when requested. A temperature difference of 1 degC equals a difference of 1.8 degF. Percentage growth of a Celsius temperature is generally not physically meaningful because its zero is arbitrary; report an absolute temperature difference instead. Humidity differences are percentage points, distinct from percentage relative change.

## Time and schedules
observed_at is an ISO-8601 UTC timestamp ending in Z. Every sensor has scheduled observations at 00:00, 12:00, and 18:00 UTC each day. These are irregular time intervals, so the ordinary arithmetic average is an average of recorded samples, not a time-weighted daily temperature. The four-day window contains 12 scheduled samples per sensor. A day filter such as substr(observed_at, 1, 10) = '2026-04-02' uses a UTC date. No local time zones or weather stations outside the three named sites are available.
