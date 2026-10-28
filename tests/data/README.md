# Test data

`fitbit_6962181067.csv.gz` is an excerpt of one participant from:

Furberg, R., Brinton, J., Keating, M., & Ortiz, A. (2016). *Crowd-sourced Fitbit
datasets 03.12.2016-05.12.2016* [Data set]. Zenodo.
https://doi.org/10.5281/zenodo.53894, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Changes: one participant (6962181067), 12 April to 12 May 2016; steps summed per
15 minutes, heart rate averaged per 2 minutes, minute sleep levels joined into
runs of one level. `extract_fitbit_persona.py` rebuilds the excerpt from the
original download. `tests/personas.py` moves the dates forward 545 weeks and
writes the data in the shapes the Garmin collectors use.
