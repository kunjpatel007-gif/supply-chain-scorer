"""
backfill_seller_ids.py
----------------------
One-time: tag every delivery and review with the seller(s) of its order (field `sellerIds`).
After this, the app loads a seller's deliveries and reviews in 2 queries instead of ~1 per 30 orders,
which makes the seller page, live re-scoring and every CRUD save fast for big sellers.

Safe to re-run: rows that already carry the right tag are skipped.

    python etl/backfill_seller_ids.py --dry-run     # count only
    python etl/backfill_seller_ids.py
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.features import fetch_all  # noqa: E402
from backend.firebase_client import db  # noqa: E402

BATCH = 400


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sellers = defaultdict(set)
    for d in fetch_all(db, 'po_lines'):
        r = d.to_dict() or {}
        if r.get('poId') and r.get('sellerId'):
            sellers[r['poId']].add(r['sellerId'])

    for coll in ('deliveries', 'quality_inspections'):
        batch, n, todo = db.batch(), 0, 0
        for d in fetch_all(db, coll):
            r = d.to_dict() or {}
            tag = sorted(sellers.get(r.get('poId'), ()))
            if not tag or r.get('sellerIds') == tag:
                continue
            todo += 1
            if args.dry_run:
                continue
            batch.update(d.reference, {'sellerIds': tag})
            n += 1
            if n >= BATCH:
                batch.commit(); batch, n = db.batch(), 0
                print(f"  {coll}: {todo} tagged...", flush=True)
        if n:
            batch.commit()
        print(f"{coll}: {todo} rows {'need tagging' if args.dry_run else 'tagged'}")


if __name__ == '__main__':
    main()
