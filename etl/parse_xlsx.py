"""Parse one MPD yearly crime workbook (.xls or .xlsx) into long-format rows.

Workbook shape (verified against H26.xls through R7.xlsx, one sheet per
municipality plus a "最終" rollup sheet):

  Row 1   title: 市区町丁別、罪種及び手口別認知件数
  Row 3   col A: municipality name; a later column: period label 累月【YYYY年 1 - 12】
  Row 5   major category, merged across its columns (凶悪犯, 粗暴犯, ...)
  Row 6   "<major>計" subtotal column marker, one per major group
  Row 7   detail type name
  Row 8+  data rows: col A = municipality + chome, col B = 総合計 (grand total)

Column positions are not assumed fixed: the (major, detail) -> column map is
built by reading rows 5-7 and validated against the canonical taxonomy.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import xlrd

TITLE = "市区町丁別、罪種及び手口別認知件数"
PERIOD_RE = re.compile(r"累月【\s*(\d{4})年\s*(\d+)\s*-\s*(\d+)\s*】")
ROLLUP_SHEET = "最終"

with (Path(__file__).parent / "crime_type_labels.json").open(encoding="utf-8") as f:
    CANONICAL_TAXONOMY = {major: list(v["details"].keys()) for major, v in json.load(f).items()}

CANONICAL_PAIRS = {(major, detail) for major, details in CANONICAL_TAXONOMY.items() for detail in details}


class TaxonomyError(ValueError):
    pass


class PeriodError(ValueError):
    pass


class ChecksumError(ValueError):
    pass


@dataclass(frozen=True)
class CrimeRow:
    town: str
    major: str
    detail: str
    count: int


@dataclass(frozen=True)
class SheetResult:
    municipality: str
    year: int
    chome_rows: list[CrimeRow]
    unlocated_rows: list[CrimeRow]


@dataclass(frozen=True)
class ParsedFile:
    path: Path
    year: int
    sheets: list[SheetResult] = field(default_factory=list)


class _Sheet:
    """Uniform 1-indexed (row, col) accessor over an xlrd or openpyxl sheet."""

    def __init__(self, get_cell, nrows: int):
        self._get_cell = get_cell
        self.nrows = nrows

    def cell(self, row: int, col: int):
        return self._get_cell(row, col)


def _open_sheet_xls(book: xlrd.book.Book, name: str) -> _Sheet:
    ws = book.sheet_by_name(name)

    def get_cell(row: int, col: int):
        r, c = row - 1, col - 1
        if r < 0 or c < 0 or r >= ws.nrows or c >= ws.ncols:
            return None
        v = ws.cell_value(r, c)
        return v if v != "" else None

    return _Sheet(get_cell, ws.nrows)


def _open_sheet_xlsx(wb, name: str) -> _Sheet:
    ws = wb[name]

    def get_cell(row: int, col: int):
        return ws.cell(row=row, column=col).value

    return _Sheet(get_cell, ws.max_row)


_WHITESPACE_RE = re.compile(r"\s+")


def _clean(v) -> str:
    if v is None:
        return ""
    s = unicodedata.normalize("NFKC", str(v))
    return _WHITESPACE_RE.sub("", s)


def _sheet_names(path: Path) -> tuple[list[str], object]:
    if path.suffix == ".xls":
        book = xlrd.open_workbook(str(path))
        return book.sheet_names(), book
    wb = openpyxl.load_workbook(str(path), data_only=True)
    return wb.sheetnames, wb


def _open_sheet(path: Path, book, name: str) -> _Sheet:
    if path.suffix == ".xls":
        return _open_sheet_xls(book, name)
    return _open_sheet_xlsx(book, name)


def _extract_year(sheet: _Sheet, path: Path, sheet_name: str) -> int:
    row3 = [sheet.cell(3, c) for c in range(1, 40)]
    label = next((_clean(v) for v in row3 if v and "累月" in str(v)), None)
    if label is None:
        raise PeriodError(f"{path.name}:{sheet_name}: no 累月 period label found in row 3")
    m = PERIOD_RE.search(label)
    if not m:
        raise PeriodError(f"{path.name}:{sheet_name}: unrecognized period label {label!r}")
    year, start, end = int(m.group(1)), m.group(2), m.group(3)
    if (start, end) != ("1", "12"):
        raise PeriodError(f"{path.name}:{sheet_name}: partial-year file ({start} - {end}), expected 1 - 12")
    return year


def _build_column_map(sheet: _Sheet, path: Path, sheet_name: str) -> dict[int, tuple[str, str]]:
    """Map column index -> (major, detail) for detail columns; excludes subtotal columns."""
    col_map: dict[int, tuple[str, str]] = {}
    current_major = None
    max_col = 45
    for c in range(3, max_col + 1):
        major_cell = _clean(sheet.cell(5, c))
        subtotal_cell = _clean(sheet.cell(6, c))
        detail_cell = _clean(sheet.cell(7, c))
        if major_cell:
            current_major = major_cell
        if detail_cell:
            if current_major is None:
                raise TaxonomyError(f"{path.name}:{sheet_name}: detail column {c} ({detail_cell}) has no major category")
            col_map[c] = (current_major, detail_cell)
        elif subtotal_cell:
            continue  # "<major>計" subtotal column, excluded
        elif major_cell:
            raise TaxonomyError(f"{path.name}:{sheet_name}: column {c} has major {major_cell!r} but no subtotal or detail marker")

    found_pairs = set(col_map.values())
    if found_pairs != CANONICAL_PAIRS:
        missing = CANONICAL_PAIRS - found_pairs
        extra = found_pairs - CANONICAL_PAIRS
        raise TaxonomyError(
            f"{path.name}:{sheet_name}: taxonomy mismatch. missing={missing} extra={extra}"
        )
    return col_map


def _parse_sheet(path: Path, book, sheet_name: str) -> SheetResult:
    sheet = _open_sheet(path, book, sheet_name)
    year = _extract_year(sheet, path, sheet_name)
    col_map = _build_column_map(sheet, path, sheet_name)
    municipality = _clean(sheet.cell(3, 1))
    if not municipality:
        raise ValueError(f"{path.name}:{sheet_name}: no municipality name in row 3 col A")

    total_row_name = f"{municipality}計"
    unlocated_row_name = f"{municipality}以下不詳"

    chome_rows: list[CrimeRow] = []
    unlocated_rows: list[CrimeRow] = []
    checksum_row: dict[int, float] = {}

    r = 8
    while r <= sheet.nrows:
        name = _clean(sheet.cell(r, 1))
        if not name:
            r += 1
            continue
        if name == total_row_name:
            checksum_row = {c: sheet.cell(r, c) or 0 for c in col_map}
            checksum_row["total"] = sheet.cell(r, 2) or 0
            r += 1
            continue

        declared_total = sheet.cell(r, 2)
        declared_total = int(declared_total) if declared_total not in (None, "") else 0
        detail_values: dict[int, int] = {}
        for c, (major, detail) in col_map.items():
            v = sheet.cell(r, c)
            detail_values[c] = int(v) if v not in (None, "") else 0

        detail_sum = sum(detail_values.values())
        if detail_sum != declared_total:
            raise ChecksumError(
                f"{path.name}:{sheet_name}: row {r} ({name}): sum of details {detail_sum} != 総合計 {declared_total}"
            )

        target = unlocated_rows if name == unlocated_row_name else chome_rows
        town = municipality if name == unlocated_row_name else name
        for c, (major, detail) in col_map.items():
            target.append(CrimeRow(town=town, major=major, detail=detail, count=detail_values[c]))
        r += 1

    if checksum_row:
        for c, (major, detail) in col_map.items():
            computed = sum(row.count for row in chome_rows + unlocated_rows if (row.major, row.detail) == (major, detail))
            expected = int(checksum_row.get(c, 0))
            if computed != expected:
                raise ChecksumError(
                    f"{path.name}:{sheet_name}: column ({major}/{detail}) chome+unlocated sum {computed} != {total_row_name} value {expected}"
                )

    return SheetResult(municipality=municipality, year=year, chome_rows=chome_rows, unlocated_rows=unlocated_rows)


def parse_file(path: Path) -> ParsedFile:
    path = Path(path)
    names, book = _sheet_names(path)
    sheets = []
    year = None
    for name in names:
        if name == ROLLUP_SHEET:
            continue
        result = _parse_sheet(path, book, name)
        if year is None:
            year = result.year
        elif year != result.year:
            raise PeriodError(f"{path.name}: sheet {name} year {result.year} != {year} from earlier sheet")
        sheets.append(result)
    if year is None:
        raise ValueError(f"{path.name}: no municipality sheets found")
    return ParsedFile(path=path, year=year, sheets=sheets)


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        parsed = parse_file(Path(arg))
        n_chome_rows = sum(len(s.chome_rows) for s in parsed.sheets)
        n_unlocated_rows = sum(len(s.unlocated_rows) for s in parsed.sheets)
        print(f"{arg}: year={parsed.year} sheets={len(parsed.sheets)} chome_rows={n_chome_rows} unlocated_rows={n_unlocated_rows}")
