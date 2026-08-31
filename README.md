# Larnaca apartment-hunting agent

Finds **second-hand (resale) apartments** in central Larnaca — **Livadia,
Finikoudes and Mackenzie** — that are within a **10-minute drive of the
coastline** and priced **at least 15% below the local market**.

The agent crawls the main Cyprus property portals, works out a price benchmark
per neighbourhood in €/m², and reports every listing that sits far enough below
it, with the caveats a buyer should check before making an offer.

```
collect (portals) → normalise → de-duplicate → geo-filter → benchmark → deals → report
```

## Install

```bash
pip install -r requirements.txt

# Needed only for portals behind bot protection (Bazaraki, home.cy):
pip install playwright && playwright install chromium
```

## Run

```bash
# The default hunt: 3 areas, 4 portals, 10 min from the sea, 15% below market
python -m larnaca_agent --engine browser

# Wider net: more portals, deeper crawl, a laxer discount bar
python -m larnaca_agent --engine browser \
    --sources bazaraki index.cy scala.cy home.cy dom.com.cy buysellcyprus \
    --max-pages 5 --discount 10

# Keep watch and only report what is new (or newly reduced) since last time
python -m larnaca_agent --engine browser --watch 360 --only-new

# Machine-readable output
python -m larnaca_agent --format json --out out/deals.json
python -m larnaca_agent --format csv  --out out/deals.csv

# Try the pipeline with no network at all, on the bundled synthetic sample
python -m larnaca_agent --from-file fixtures/sample_listings.json --osrm-url ""
```

Useful flags: `--areas`, `--max-drive-min`, `--discount`, `--max-pages`,
`--include-new-builds`, `--dump-listings`, `--no-cache`, `-v`.
Full list: `python -m larnaca_agent --help`.

## How each requirement is implemented

**Within 10 minutes' drive of the coast** — `geo.py` holds ten sample points
along the shore from Mackenzie/airport up to Oroklini. For each listing the
agent takes the shortest drive time to any of them, routed through
[OSRM](https://project-osrm.org) when reachable (`--osrm-url`, the public demo
server by default) and otherwise estimated as crow-flight distance × 1.35 detour
factor at 27 km/h. Listings with coordinates are routed from their own position;
the rest fall back to the neighbourhood centroid and are flagged in the report.

**Second-hand only** — `normalize.classify_resale` drops off-plan and new-build
adverts. Construction year wins over marketing language: a 2005 flat advertised
as "brand new" after a renovation is still resale, while "under construction /
delivery 2028" is dropped. Where a portal gives no signal at all the listing is
kept and marked *resale status unverified*, since resale dominates the stock.
`--include-new-builds` turns the filter off.

**Average price per area** — `analysis.build_benchmarks` computes, per
neighbourhood, the **median €/m²** plus the P25–P75 band, a 10%-trimmed mean and
the median asking price. The median is the reference on purpose: a mean is
dragged around by penthouses and by the very bargains being hunted. An area with
fewer than 8 comparables is flagged low-confidence; with fewer than 4 it borrows
the combined benchmark and says so.

**15%+ below market** — `analysis.find_deals` compares each listing's own €/m²
against its area benchmark and reports everything at or beyond the threshold
(`--discount`), sorted by discount, with the fair value and the money gap.
Discounts above 45% get a warning: in Cyprus that usually means share-of-title,
missing title deeds, or a wrong size in the advert rather than a bargain.

**Portals** — the four requested (`bazaraki`, `index.cy`, `scala.cy`,
`home.cy`), plus four extras worth running: `dom.com.cy`, `buysellcyprus`,
`zyprus` and `offer.com.cy`.

> Two naming notes: **Scala** is at `scala.cy`, not `scala.com.cy`, and the
> "home.mc" in the original brief is almost certainly **`home.cy`** — that is
> the Cyprus portal in that family. Both are set in
> `larnaca_agent/scrapers/portals.py` and easy to change.

Bazaraki's neighbourhood slugs are its own (`larnaka-makenzy`,
`larnaka-finikoudes`, `livadia-larnakas`), and the crawl also sweeps the
adjacent central quarters (Chrysopolitissa, Harbor, Skala) plus the whole
district, letting the geo stage decide what falls inside the ring.

Other portals worth adding later: `remaxcyprus.com`, `propertygallery.com.cy`,
`cyprus-real.estate`, `landbank.com.cy`, `danos.com.cy` and the bank REO
platforms (`altamiracyprus.com`, `gogordian.com`), which is where genuinely
distressed, below-market stock tends to surface.

## When a portal returns nothing

```bash
python -m larnaca_agent --diagnose --engine browser
```

Crawls one page per portal and prints a status table plus, per portal, what
parsed, a sample record, which fields were missing, and the specific next step —
distinguishing *blocked* (rerun with `--engine browser`) from *unreachable*
(connectivity) from *loaded but nothing matched* (selectors need updating).
Every normal run ends with the same status table, so a silently dead portal is
visible instead of quietly counted as "no results".

If listings parse but get dropped for having no size, recover them from their
advert pages:

```bash
python -m larnaca_agent --engine browser --enrich-details 25
```

## Extraction strategy

Portals redesign their markup often, so `scrapers/base.py` tries four layers per
page, most durable first:

1. **JSON-LD** (`schema.org` `Product` / `Offer`) — emitted for SEO, survives redesigns.
2. **Embedded app state** — `__NEXT_DATA__`, `window.__NUXT__`.
3. **CSS selectors** — several candidates per field, first match wins.
4. **Automatic card detection** — if the declared selectors match nothing, the
   card element is *inferred*: find the nodes holding a price, walk up to the
   nearest ancestor that also owns a link, and keep the largest group sharing a
   tag/class signature. A redesign then costs a warning, not a dead scraper.

A portal class therefore only declares its URLs and selector candidates. If one
portal breaks, the run continues and logs a warning instead of failing.

`--engine browser` runs Chromium with a realistic viewport, locale and timezone,
dismisses the cookie banner, and scrolls to trigger lazy-loaded results — which
is what most of these portals need before they render anything.

The crawler waits 1.5 s between requests to the same host, caches pages under
`.cache/`, and honours `robots.txt` (`--ignore-robots` exists for sites where
you have permission; the responsibility for using it is yours). Note that
scraping may be against a portal's terms of service — check before running at
volume, and keep the crawl polite.

## Tests

```bash
python -m pytest tests -q     # 76 tests, no network required
```

`fixtures/sample_listings.json` is **synthetic** data used by the tests and by
the offline demo — it is not real listings.

## Known limitation

Benchmarks are built from **asking prices**, not registered sale prices, so they
measure "cheap relative to what neighbours are asking". For actual transaction
values, cross-check the Cyprus Land Registry / the Central Bank residential
price index. The agent also cannot see condition, floor, view, renovation state
or title-deed status — a 25% discount is a lead to investigate, not a valuation.

---

## בעברית

הסוכן מחפש דירות **יד שנייה** בלרנקה — ליבדיה, פיניקודס ומקנזי — במרחק **עד 10
דקות נסיעה מקו החוף**, מחשב את **מחיר השוק לכל אזור** (חציון €/מ״ר) ומדווח על כל
דירה שמתומחרת **15% ומטה מתחת למחיר השוק** באזור שלה.

הרצה: `python -m larnaca_agent --engine browser`

חשוב לדעת: הבנצ'מרק מבוסס על מחירי **בקשה** ולא על עסקאות שנסגרו, והסוכן לא רואה
מצב תחזוקה, קומה, נוף או מצב טאבו. הנחה של 25% היא **כיוון לבדיקה**, לא הערכת
שווי. הנחות מעל 45% הן בדרך כלל סימן לבעיה (חלק בטאבו, היעדר שטר בעלות) ולא מציאה.

## 3D unit models from the drawing set

`tools/plan3d/` turns the developer's plan sheets (`tools/plan3d/floorplans.pdf`,
four furnished apartment plans) into an explorable 3D model of each unit:

```bash
pip install pymupdf pillow numpy scipy scikit-image shapely

python -m tools.plan3d.extract    # PDF -> viz/units.json + floor textures
python -m tools.plan3d.bundle     # -> viz/index.html, self-contained
```

The sheets are raster renders rather than vector CAD, so the geometry is
recovered by colour-segmenting each page and tracing the masks: the uniform grey
the plans draw walls in, the pale blue strips that mark windows and patio doors,
the timber decking that marks balconies, and the flat grey fills that mark space
belonging to a neighbouring unit. Pixels become metres via a per-page scale
calibrated against the dimensions printed on the sheet; walls extrude to a
2.70 m storey, parapets to 1.05 m, window heads to 2.15 m.

Furniture is a separate step. The sheets draw it as photo-realistic top views,
where a white sofa, a white bath and the pale travertine under both are the same
few pixel values, so colour segmentation cannot tell them apart. The pieces are
therefore listed explicitly in `tools/plan3d/furniture.py` — each entry is the
footprint the plan draws, in that sheet's own pixel coordinates, which the
extractor converts to metres with the same origin and scale it uses for the
walls. Height and form come from a type table, so a bed becomes a divan with a
headboard and a dining table becomes a top on legs. The viewer also gets a
walk-through starting point per unit, chosen as the spot with both room around
it and a long clear view down the flat.

Two details are worth knowing when reading the output. The plans draw kitchen
worktops, vanities and sideboards in the same grey as the walls, so anything
thick but free-standing is modelled at counter height instead of full height,
and the hairline outlines around beds and baths are discarded. And the areas in
`viz/units.json` are measured off the trace — they are not the brochure's
figures, and they disagree with it in places where the printed room dimensions
are themselves inconsistent with the drawing.
