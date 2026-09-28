from pathlib import Path

import pytest

from etl.parse_xlsx import CANONICAL_PAIRS, ChecksumError, PeriodError, parse_file

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw"

ALL_YEARLY_FILES = [
    "H26.xls", "H27.xls", "H28.xlsx", "H29.xlsx", "H30.xlsx", "H31.xlsx",
    "R2.xlsx", "R3.xlsx", "R4.xlsx", "R5.xlsx", "R6.xlsx", "R7.xlsx",
]

EXPECTED_YEARS = {
    "H26.xls": 2014, "H27.xls": 2015, "H28.xlsx": 2016, "H29.xlsx": 2017,
    "H30.xlsx": 2018, "H31.xlsx": 2019, "R2.xlsx": 2020, "R3.xlsx": 2021,
    "R4.xlsx": 2022, "R5.xlsx": 2023, "R6.xlsx": 2024, "R7.xlsx": 2025,
}

MARUNOUCHI_1CHOME_TOTAL = {
    ("H31.xlsx", "千代田区丸の内1丁目"): 466,
    ("R5.xlsx", "千代田区丸の内1丁目"): 344,
}


def _town_total(parsed, municipality, town):
    for sheet in parsed.sheets:
        if sheet.municipality == municipality:
            return sum(r.count for r in sheet.chome_rows if r.town == town)
    raise AssertionError(f"municipality {municipality} not found")


@pytest.fixture(scope="module")
def parsed_files():
    return {fname: parse_file(DATA_DIR / fname) for fname in ALL_YEARLY_FILES}


def test_year_parsed_from_row3_label(parsed_files):
    for fname, expected_year in EXPECTED_YEARS.items():
        assert parsed_files[fname].year == expected_year, fname


def test_marunouchi_1chome_known_totals(parsed_files):
    for (fname, town), expected in MARUNOUCHI_1CHOME_TOTAL.items():
        total = _town_total(parsed_files[fname], "千代田区", town)
        assert total == expected, f"{fname} {town}"


@pytest.mark.parametrize("fname", ALL_YEARLY_FILES)
def test_detail_sum_matches_grand_total_and_checksums(fname):
    # parse_file itself raises ChecksumError on any mismatch (per-row detail sum
    # vs 総合計, and per-column chome+unlocated sum vs the "<name>計" row); a
    # clean parse is the assertion.
    parse_file(DATA_DIR / fname)


@pytest.mark.parametrize("fname", ALL_YEARLY_FILES)
def test_all_sheets_present_and_taxonomy_complete(fname):
    # Taxonomy completeness is checked per-sheet inside parse_file (it raises
    # TaxonomyError otherwise); here we just confirm every municipality sheet
    # was parsed and, across the whole file, every canonical pair shows up
    # somewhere (small municipalities may have zero chome rows and route all
    # counts through 以下不詳, so a per-sheet check on chome_rows alone is too strict).
    parsed = parse_file(DATA_DIR / fname)
    assert len(parsed.sheets) == 62
    all_pairs = {
        (r.major, r.detail)
        for sheet in parsed.sheets
        for r in sheet.chome_rows + sheet.unlocated_rows
    }
    assert all_pairs == CANONICAL_PAIRS


def test_rejects_partial_year_file(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "テスト区"
    ws.cell(row=1, column=12, value="市区町丁別、罪種及び手口別認知件数")
    ws.cell(row=3, column=1, value="テスト区")
    ws.cell(row=3, column=33, value="累月【2023年 1 - 6】")
    path = tmp_path / "partial.xlsx"
    wb.save(path)
    with pytest.raises(PeriodError):
        parse_file(path)
