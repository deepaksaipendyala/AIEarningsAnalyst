#!/usr/bin/env python3
"""Seed the database with company data from companies.json."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings
from backend.database import init_db, SessionLocal
from backend.models import Company


def main():
    init_db()
    db = SessionLocal()

    companies_path = settings.data_dir / "companies.json"
    with open(companies_path) as f:
        companies = json.load(f)

    for c in companies:
        existing = db.query(Company).filter(Company.ticker == c["ticker"]).first()
        if not existing:
            company = Company(
                ticker=c["ticker"],
                name=c["name"],
                cik=c.get("cik"),
                sector=c.get("sector"),
                fiscal_year_end_month=c.get("fiscal_year_end_month", 12),
            )
            db.add(company)
            print(f"  Added {c['ticker']}")
        else:
            print(f"  Exists {c['ticker']}")

    db.commit()
    db.close()
    print("Done!")


if __name__ == "__main__":
    main()
