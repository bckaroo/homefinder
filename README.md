# Transit Housing Analysis Map

PROJ-041 · DevOS-tracked.

Housing affordability crossed with peak-hour transit reachability: what does a
commute to the employment core actually cost you in rent, tract by tract.

## Scope

1. **Reachability** — build a peak-hour multimodal graph from GTFS feeds (MTA
   subway, Metro-North, LIRR, PATH), walk transfers at hub stations, Dijkstra
   from an employment anchor (initially Penn Station).
2. **Affordability** — ACS 5-year housing cost and income by census tract
   (B25031, B25064, household income), rent burden computed per tract.
3. **Score** — combine commute minutes and rent burden into a single sortable
   score with exposed weights.
4. **Map** — interactive tract choropleth with toggleable layers, station
   points, legend, title block, and source note.

## Layout

```
scripts/     pipeline stages (fetch, graph, join, score)
web/         map front end
data/        derived outputs (raw downloads are gitignored)
docs/        methodology and data provenance
notes/       DEVLOG.md — read this first
```

## Task board

`devos list --project PROJ-041` — or the dashboard at http://localhost:3102
