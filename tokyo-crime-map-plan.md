# Tokyo Crime Map: Interactive Filterable Map

## Context

Build a Google-Maps-style interactive web app that visualizes publicly available Tokyo crime data (警視庁 / MPD) at the chome (町丁目) level, with combinable filters.

Decisions already made with the user:

- **Architecture**: real web app with a small API (not a static pre-baked site), for combinable filters and room to grow.
- **Filter scope v1**: crime category (two-level, matching the MPD XLSX taxonomy) + year range.
- **Granularity**: yearly only. No monthly data. The dataset is updated once per year when MPD publishes the full-year file.
- **Repo**: `https://github.com/FireBarret/tokyo-crime-map.git`. Read access now; push access will be attached before the first commit.
- **Hosting**: Cloudflare Pages + Pages Functions + D1, at `tokyo-crime.dariushshaban.com`.
- **Data in hand**: MPD yearly XLSX files from Heisei 26 (2014) through the latest complete year.
- **Bilingual UI**: English and Japanese with a language toggle.

## Source data (inspected directly)

Two yearly files have been opened and parsed: `H31.xlsx` (2019) and `R5.xlsx` (2023). The MPD site itself is unreachable from the planning sandbox (`EGRESS_BLOCKED`).

### Workbook structure

- One sheet per municipality (23 wards, Tama cities/towns/villages, islands), plus a `最終` sheet. R5 has 62 municipality sheets + `最終` (63 total). **Do not hardcode the sheet count**; iterate over all sheets except `最終`.
- `最終` is a ward-level rollup (also contains `海外認知`, `不明`, `合計` rows). Skip it entirely.
- Row 1: title `市区町丁別、罪種及び手口別認知件数`.
- Row 3: municipality name in A, period label near the right, e.g. `累月【2023年 1 - 12】`. **Derive the year from this label, not the filename** (filenames mix eras: H26 to H31, R2 onward). Reject any file whose label is not `1 - 12` (partial year), with a clear error.
- Rows 5 to 7: three-row header.
  - Row 5: major category (`凶悪犯`, `粗暴犯`, `侵入窃盗`, `非侵入窃盗`, `その他`), merged across its columns.
  - Row 6: a `<major>計` subtotal column at the start of each major group.
  - Row 7: detail type names.
- Column A: full town name, municipality + chome in one string, e.g. `千代田区丸の内１丁目`.
- Column B: `総合計` (grand total).
- Columns C:AL (37 columns) = 5 major-subtotal columns + 32 detail columns.
- Data rows start at row 8.

### Taxonomy (from R5, row 5 major / row 7 detail)

| Major | Details |
|---|---|
| 凶悪犯 | 強盗, その他 |
| 粗暴犯 | 凶器準備集合, 暴行, 傷害, 脅迫, 恐喝 |
| 侵入窃盗 | 金庫破り, 学校荒し, 事務所荒し, 出店荒し, 空き巣, 忍込み, 居空き, その他 |
| 非侵入窃盗 | 自動車盗, オートバイ盗, 自転車盗, 車上ねらい, 自販機ねらい, 工事場ねらい, すり, ひったくり, 置引き, 万引き, その他 |
| その他 | 詐欺, 占有離脱物横領, その他知能犯, 賭博, その他刑法犯 |

### Parsing pitfalls (verified)

- **Subtotal columns must be excluded.** The `<major>計` columns are sums of their details. Including them double counts (verified: summing C:AL for 丸の内１丁目 2023 gives exactly 2 × 総合計). Store details only and derive major totals by summing.
- **`その他` appears as a detail name under three different majors.** The detail key must be the pair `(major, detail)`, never the detail name alone.
- **Special rows** per sheet:
  - `<name>計`: municipality total, last row. Drop from data, but use it as a checksum.
  - `<name>以下不詳`: incidents not attributable to a chome. Store in `unlocated_counts`, exclude from the map layer.
- **Taxonomy may drift across 2014 to latest.** Read the header per file, build the `(major, detail)` column map from it, and compare against the canonical taxonomy above. On any unknown or missing column, fail loudly with the file and sheet name. Do not assume fixed column positions.

## Architecture (Cloudflare-native)

```
tokyo-crime-map/
  frontend/                    React + MapLibre GL JS, Cloudflare Pages
    src/
      Map.tsx                  choropleth; joins API counts to boundary features by chome_id
      FilterPanel.tsx          two-level crime tree (major checkbox toggles all its details) + year range
      ChomePopup.tsx           click/hover detail: breakdown by major/detail and by year
      Legend.tsx
      i18n/en.json, ja.json
      api.ts
    public/
      chome-boundaries.topojson   simplified Tokyo chome polygons, with population + area properties
      crime-types.json            {major: {ja, en, details: {detail: {ja, en}}}}
  functions/
    api/
      crimes.ts                GET /api/crimes
      chome/[id].ts            GET /api/chome/:id
      unlocated.ts             GET /api/unlocated
      meta.ts                  GET /api/meta/years
  etl/
    parse_xlsx.py              one yearly XLSX -> long rows (town, year, major, detail, count)
    build_boundaries.py        e-Stat source -> simplified TopoJSON with chome_id, population, area
    load_crimes.py             normalize + join to chome_id, emit per-year seed SQL, write unmatched report
    crime_type_labels.json     canonical taxonomy + English labels (source for frontend/public/crime-types.json)
    reports/                   unmatched_<year>.csv, coverage summary
    tests/                     pytest
  data/raw/                    MPD yearly XLSX files
  schema.sql
  wrangler.toml
  pyproject.toml               Python deps via uv
```

### Why this shape

- **D1** (serverless SQLite) is enough because filtering is purely attribute-based (crime type, year). No PostGIS needed.
- **Geometry is static on the client.** Boundaries are loaded once; the API returns only `{chome_id, count}` rows, and the frontend recolors.
- **Pages Functions** provide `/api/*` in the same deploy as the frontend.
- **MapLibre GL JS**: open source, no Google billing.

### Basemap

MapLibre needs a vector tile source. Default: **OpenFreeMap** (no API key). Alternative: Protomaps PMTiles self-hosted on R2. Include the basemap's required attribution.

### Schema

```sql
CREATE TABLE crime_counts (
  chome_id     TEXT    NOT NULL,
  year         INTEGER NOT NULL,
  crime_major  TEXT    NOT NULL,
  crime_detail TEXT    NOT NULL,
  count        INTEGER NOT NULL,
  PRIMARY KEY (year, crime_major, crime_detail, chome_id)
);
-- covering index for the main aggregate query
CREATE INDEX idx_crime_filter ON crime_counts (crime_major, crime_detail, year, chome_id, count);

CREATE TABLE unlocated_counts (
  municipality TEXT    NOT NULL,
  year         INTEGER NOT NULL,
  crime_major  TEXT    NOT NULL,
  crime_detail TEXT    NOT NULL,
  count        INTEGER NOT NULL,
  PRIMARY KEY (year, municipality, crime_major, crime_detail)
);
```

- **Do not insert zero-count rows.** Absence means zero; this keeps the table and D1 row-read quota small.
- Crime keys are stored in Japanese; the frontend maps them to labels via `crime-types.json`.

### API (v1)

- `GET /api/meta/years` -> `{min, max}` from D1.
- `GET /api/crimes?types=<major>/<detail>,...&start=2019&end=2023` -> `[{chome_id, count}]`, `SUM(count) GROUP BY chome_id`. The frontend expands a selected major into all its details before calling. Omitting `types` means all.
- `GET /api/chome/:id?start=&end=` -> breakdown by `(major, detail)` and by year, for the popup.
- `GET /api/unlocated?types=&start=&end=` -> per-municipality unlocated counts, shown in the filter panel or legend as a note.
- All responses: long `Cache-Control` (data only changes once a year); bust by including a data version in the URL or redeploying.

### Chome boundaries

- Source: e-Stat 国勢調査 **2020** 小地域 (町丁・字等) boundary data for Tokyo, `https://www.e-stat.go.jp/gis/statmap-search`. Alternative: Geoshape, `https://geoshape.ex.nii.ac.jp/`. If egress-blocked, ask the user to download.
- `chome_id` = the e-Stat `KEY_CODE`. Do not generate IDs.
- Attach `population` (2020 census small-area totals) and `area_km2` as feature properties so the frontend can compute rates without extra API calls.
- **Size**: simplify with mapshaper and export TopoJSON. Target under ~10 MB; hard limit is 25 MiB per Pages asset. If it cannot get small enough without visible distortion, switch to PMTiles.
- **Boundary vintage caveat**: chome are renamed, merged or created over 2014 to present (住居表示 changes). 2020 is the single canonical vintage. Rows that don't match are reported, not silently dropped.

### Town-name join (main ETL risk)

Normalize both sides before matching:

- Unicode NFKC (full-width vs half-width).
- Chome numbers: unify kanji numerals, full-width and half-width digits (`一丁目` / `１丁目` / `1丁目`).
- Variant characters: `ヶ` / `ケ` / `ガ`, `之` / `ノ`, etc.
- Strip or handle `大字` / `字` prefixes.
- Municipality prefix: MPD strings include it; ensure the boundary side is built the same way. Island and 郡 names (`西多摩郡瑞穂町`) need care.

Write `etl/reports/unmatched_<year>.csv` (municipality, town string, total count) and a coverage summary: % of rows matched and % of crime count matched, per year and per municipality. Investigate before trusting the map if count coverage is low.

### Normalization and display

- Metric toggle: **raw count** (default), **per km²**, **per 1,000 residents**.
- Per-resident rates explode in commercial areas with tiny residential populations (e.g. 丸の内). Apply a minimum-population threshold for that mode and grey out chome below it, with a legend note. Population is 2020 census for all years; note this in the UI.
- Quantile or Jenks color classes, recomputed on filter change; legend shows class breaks.
- Distinguish "0 crimes" from "no data / unmatched".
- Shareable URL state: filters, metric, year range, language, map position.

### i18n

- UI chrome via `react-i18next` with `en.json` / `ja.json`, plus a toggle.
- Crime taxonomy: `etl/crime_type_labels.json`, `{major: {ja, en, details: {detail: {ja, en}}}}`, built once during parser development and copied to `frontend/public/crime-types.json`. Translate each `その他` in context (e.g. "Other violent", "Other burglary", "Other theft").
- Town names stay Japanese in v1; romanization is a possible follow-up.

### Attribution and disclaimer

- Credit MPD (警視庁) as data source, e-Stat for boundaries and population (政府標準利用規約), and the basemap provider.
- Disclaimer: counts are **reported offenses (認知件数) by place of occurrence**, not a measure of personal risk; chome matching is approximate across years.

## Build steps

1. **User**: attach push access. Scaffold the repo (frontend, functions, etl, schema.sql, wrangler.toml, pyproject.toml). Python via uv; Node LTS; wrangler as a dev dependency.
2. Get the yearly XLSX files into `data/raw/` (user pushes from their machine).
3. Write `etl/parse_xlsx.py` per the pitfalls above. Start with `R5.xlsx` and `H31.xlsx`, then run over all years and confirm the taxonomy check passes for every file.
4. Write `etl/tests/` (pytest), see Verification.
5. Obtain boundaries, write `build_boundaries.py` -> `frontend/public/chome-boundaries.topojson` with `chome_id`, `population`, `area_km2`. Check file size.
6. Write `load_crimes.py`: normalize, join, write unmatched reports, emit **one seed SQL file per year** (each starts with `DELETE ... WHERE year = N` so re-seeding a year is idempotent). Keep files small enough for `wrangler d1 execute --file`; split into chunks if needed.
7. **User**: `wrangler login`; `wrangler d1 create` and paste the database ID into `wrangler.toml`. Apply `schema.sql` and seed locally, verify with `wrangler d1 execute --local`.
8. Build the Pages Functions.
9. Build the frontend: basemap, boundaries, choropleth, two-level filter tree, year range, metric toggle, popup, legend, unlocated note, i18n, URL state.
10. Local dev via `wrangler pages dev`.
11. Seed remote D1, `wrangler pages deploy`, check the `*.pages.dev` URL.
12. **User**: attach `tokyo-crime.dariushshaban.com` in the Cloudflare dashboard.

## Annual update workflow

Document in the README:

1. Drop the new full-year XLSX into `data/raw/`.
2. Run the ETL for that year (parser rejects partial-year files).
3. Review `reports/unmatched_<year>.csv` and the taxonomy check.
4. Apply that year's seed SQL to remote D1 (idempotent).
5. Bump the data version so cached API responses refresh.

## Verification

- **Parser tests (pytest)**:
  - 千代田区丸の内１丁目 総合計: 2019 = 466, 2023 = 344.
  - For every chome row in every file: sum of the 32 detail columns == 総合計.
  - For every sheet: sum of chome rows + `以下不詳` == `<name>計` row, per column.
  - Year parsed from row 3 matches the expected year for known files.
- **ETL coverage**: per-year matched % (rows and counts) printed and saved; no unexplained large gaps.
- **API**: `wrangler pages dev`, check each endpoint's JSON against direct D1 queries.
- **Frontend**: toggle major and detail filters and the year range; choropleth recolors; popup numbers for 丸の内１丁目 match the tests; metric toggle behaves (thresholded chome greyed out).
- **i18n**: toggle language, no missing or fallback strings, including all three `その他` labels.
- **Deploy**: `*.pages.dev` URL works before the custom domain is attached.
