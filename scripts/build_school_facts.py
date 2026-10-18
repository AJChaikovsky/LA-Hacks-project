"""Build data/school_facts.json from California Department of Education files. Owner: Dev B, ticket B14 (#48).

    python scripts/build_school_facts.py                 # downloads the CDE files (~65 MB) to a temp dir
    python scripts/build_school_facts.py --dir DIR       # use files already downloaded into DIR

For each school's CDS code in data/schools.json: Census Day enrollment, English learners and students with
disabilities (CDE Census Day enrollment file), plus chronic absenteeism (CDE chronic absenteeism file).
These describe the whole host school, not only the magnet program. Cells CDE suppresses ('*') or that are
missing stay null with a reason; nothing is estimated.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config, load_data  # noqa: E402

OUT = ROOT / "data" / "school_facts.json"


def _rows(path: Path):
    with path.open(encoding="latin-1", newline="") as f:  # CDE files are Windows-1252/Latin-1 tab-separated
        yield from csv.DictReader(f, delimiter="\t")


def _count(value: str | None) -> tuple[int | None, str | None]:
    """(number, None) or (None, reason) for a CDE cell."""
    v = (value or "").strip()
    if v == "*":
        return None, "suppressed"  # CDE hides small counts to protect student privacy
    if not v:
        return None, "not_reported"
    try:
        return int(float(v)), None
    except ValueError:
        return None, "not_reported"


def _school_key(r: dict, county: str, district: str, school: str) -> str | None:
    if r.get(county) and r.get(district) and r.get(school):
        return f"{r[county]}{r[district]}{r[school]}"
    return None


def build(src: Path) -> dict:
    cfg = load_config("school_facts")
    wanted = {s["cds_code"] for s in load_data("schools") if s.get("cds_code")}
    enroll_file, absent_file = src / Path(cfg["enrollment"]["url"]).name, src / Path(cfg["absenteeism"]["url"]).name

    enroll: dict[str, dict[str, str]] = {}
    names: dict[str, str] = {}
    for r in _rows(enroll_file):
        cds = _school_key(r, "CountyCode", "DistrictCode", "SchoolCode")
        if r["AggregateLevel"] == "S" and cds in wanted:
            enroll.setdefault(cds, {})[r["ReportingCategory"]] = r["TOTAL_ENR"]
            names[cds] = r["SchoolName"]

    absent: dict[str, dict[str, str]] = {}
    for r in _rows(absent_file):
        cds = _school_key(r, "County Code", "District Code", "School Code")
        if r["Aggregate Level"] == "S" and cds in wanted and r["Reporting Category"] == "TA":
            absent[cds] = r

    facts = {}
    for cds in sorted(wanted):
        cats = enroll.get(cds, {})
        row = {"cde_name": names.get(cds), "year": cfg["enrollment"]["year"]}
        for field, category in (("enrollment", "TA"), ("english_learners", "SG_EL"),
                                ("students_with_disabilities", "SG_DS")):
            value, reason = _count(cats.get(category)) if cds in enroll else (None, "not_found")
            row[field] = value
            if reason:
                row[f"{field}_null_reason"] = reason
        a = absent.get(cds)
        if a:
            rate, reason = (a["ChronicAbsenteeismRate"].strip(), None)
            try:
                row["chronic_absenteeism_pct"] = float(rate)
            except ValueError:
                row["chronic_absenteeism_pct"], reason = None, "suppressed" if rate == "*" else "not_reported"
        else:
            row["chronic_absenteeism_pct"], reason = None, "not_found"
        if reason:
            row["chronic_absenteeism_pct_null_reason"] = reason
        row["chronic_absenteeism_year"] = cfg["absenteeism"]["year"]
        facts[cds] = row

    return {
        "sources": {k: cfg[k] for k in ("enrollment", "absenteeism")},
        "note": cfg["note"],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schools": facts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, help="folder that already has the CDE .txt files (default: download)")
    args = ap.parse_args()
    src = args.dir
    if src is None:
        src = Path(tempfile.mkdtemp())
        for key in ("enrollment", "absenteeism"):
            url = load_config("school_facts")[key]["url"]
            print(f"downloading {url} …")
            urllib.request.urlretrieve(url, src / Path(url).name)  # noqa: S310 - official CDE file
    data = build(src)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    found = sum(1 for r in data["schools"].values() if r["enrollment"] is not None)
    print(f"wrote {OUT.relative_to(ROOT)}: {found}/{len(data['schools'])} schools with enrollment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
