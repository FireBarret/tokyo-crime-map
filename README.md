# Tokyo Crime Map

Interactive, filterable choropleth of Tokyo crime data (警視庁 / Metropolitan Police
Department), at the chome (町丁目) level, 2014 onward.

See `tokyo-crime-map-plan.md` for the full architecture and build plan.

## Status

- [x] ETL: `etl/parse_xlsx.py` parses yearly MPD workbooks (.xls/.xlsx, 2014-2025)
      into long-format rows, validated against the canonical crime taxonomy and
      the source file's own `<municipality>計` checksum row.
- [ ] Chome boundaries (e-Stat)
- [ ] `load_crimes.py` join + D1 seed SQL
- [ ] D1 schema + Pages Functions API
- [ ] Frontend (React + MapLibre)

## Development

Python (ETL), via [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run pytest etl/tests/
uv run python3 etl/parse_xlsx.py data/raw/R7.xlsx
```

## Data

Yearly MPD XLSX/XLS files live in `data/raw/`, named by Japanese era + year
(`H26.xls` = Heisei 26 = 2014, `R7.xlsx` = Reiwa 7 = 2025). The workbook's own
row-3 period label (`累月【YYYY年 1 - 12】`), not the filename, is the source of
truth for the year.
