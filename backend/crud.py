"""
crud.py
-------
Create, read, update and delete for every table, in one place.

Each table is described once in TABLES: its fields, primary key, foreign keys
and what happens to linked rows on delete. The /data pages, forms, record pages
and delete previews are all generated from these descriptions.

Firestore enforces none of the usual relational rules, so this module does:
  * types, required fields and ranges ............ clean()
  * foreign keys must point at an existing row .... clean()
  * unique keys: IDs, one delivery per order, line numbers within an order
  * ON DELETE CASCADE / RESTRICT .................. delete_plan()
  * derived columns: days late, review flag, sentiment severity
and re-scores every seller whose lines, deliveries or reviews changed.
"""
from __future__ import annotations

import datetime
import logging
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as _field

from google.cloud.firestore_v1.base_query import FieldFilter

from backend import scoring
from backend.firebase_client import db

log = logging.getLogger(__name__)

PAGE = 50            # rows per list page
LINKED = 12          # linked rows shown on a record page
BATCH = 400          # Firestore allows 500 writes per batch
KEY_RE = re.compile(r'^[A-Za-z0-9_-]{3,64}$')
UTC = datetime.timezone.utc


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = 'str'          # str | text | int | float | datetime | choice | stars
    required: bool = False
    ref: str | None = None     # foreign key: the table this points at
    choices: tuple = ()
    lo: float | None = None
    hi: float | None = None
    maxlen: int = 120
    auto: bool = False         # filled in by the server (generated or derived); read-only in forms
    hint: str = ''
    column: bool = False       # shown as a column in the table list
    money: bool = False


@dataclass(frozen=True)
class Table:
    name: str                  # Firestore collection
    label: str                 # plural, for headings
    one: str                   # singular
    key: str | None            # field that doubles as the document ID (None = Firestore auto ID)
    key_kind: str              # given | uuid | next | auto | model
    fields: tuple
    note: str = ''
    sort: tuple | None = None  # default order of the unfiltered list: (field, direction)
    serial: str | None = None  # integer column numbered max + 1 on insert
    editable: bool = True

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)

    @property
    def columns(self):
        return [f for f in self.fields if f.column]

    def form_fields(self, creating):
        """Fields a person fills in. The primary key only on create (it never changes)."""
        out = []
        for f in self.fields:
            if f.auto:
                continue
            if f.name == self.key and not creating:
                continue
            out.append(f)
        return out


ORDER_STATUSES = ('created', 'approved', 'invoiced', 'processing', 'shipped', 'delivered', 'canceled', 'unavailable')

TABLES = {t.name: t for t in (
    Table('sellers', 'Sellers', 'seller', 'sellerId', 'given', note='Marketplace sellers; each one is scored monthly.', fields=(
        Field('sellerId', 'Seller ID', required=True, maxlen=64, column=True, hint='3–64 letters, digits, - or _'),
        Field('sellerName', 'Name', required=True, column=True),
        Field('sellerCity', 'City', column=True),
        Field('sellerState', 'State', maxlen=2, column=True),
        Field('sellerZipCodePrefix', 'ZIP prefix', kind='int', lo=0, hi=99999))),
    Table('products', 'Products', 'product', 'productId', 'given', note='Catalogue items sold on order lines.', fields=(
        Field('productId', 'Product ID', required=True, maxlen=64, column=True, hint='3–64 letters, digits, - or _'),
        Field('productCategoryName', 'Category', column=True))),
    Table('purchase_orders', 'Orders', 'order', 'poId', 'uuid', note='Order headers; sellers are linked through order lines.',
          sort=('orderDate', 'DESCENDING'), fields=(
        Field('poId', 'Order ID', auto=True, column=True),
        Field('orderDate', 'Ordered', kind='datetime', required=True, column=True),
        Field('expectedDeliveryDate', 'Promised delivery', kind='datetime', column=True,
              hint='Changing it recalculates days late on this order’s delivery'),
        Field('orderStatus', 'Status', kind='choice', choices=ORDER_STATUSES, required=True, column=True))),
    Table('po_lines', 'Order lines', 'order line', None, 'auto', note='What was sold, by which seller, at what price.', fields=(
        Field('poId', 'Order', ref='purchase_orders', required=True, column=True),
        Field('lineNo', 'Line', kind='int', lo=1, hi=999, column=True, hint='Blank = next free number in the order'),
        Field('sellerId', 'Seller', ref='sellers', required=True, column=True),
        Field('productId', 'Product', ref='products', column=True),
        Field('unitPriceAtOrder', 'Price', kind='float', lo=0, hi=1_000_000, column=True, money=True),
        Field('freightValue', 'Freight', kind='float', lo=0, hi=1_000_000, column=True, money=True),
        Field('shippingLimitDate', 'Ship by', kind='datetime'))),
    Table('deliveries', 'Deliveries', 'delivery', None, 'auto', serial='deliveryId',
          note='One per order; days late feed the risk model.', fields=(
        Field('deliveryId', 'Delivery #', kind='int', auto=True, column=True),
        Field('poId', 'Order', ref='purchase_orders', required=True, column=True),
        Field('deliveredCarrierDate', 'Handed to carrier', kind='datetime'),
        Field('actualDeliveryDate', 'Delivered', kind='datetime', column=True),
        Field('delayDays', 'Days vs promise', kind='int', lo=-365, hi=365, column=True,
              hint='Blank = worked out from the delivered and promised dates'),
        Field('carrierTransitDelayDays', 'Days in transit', kind='int', auto=True),
        Field('sellerDispatchDelayDays', 'Dispatch vs ship-by (days)', kind='int', auto=True))),
    Table('quality_inspections', 'Reviews', 'review', None, 'auto', serial='inspectionId',
          note='Customer reviews; stars, text sentiment and defect type feed the model.', fields=(
        Field('inspectionId', 'Review #', kind='int', auto=True, column=True),
        Field('poId', 'Order', ref='purchase_orders', required=True, column=True),
        Field('inspectionDate', 'Reviewed', kind='datetime', column=True, hint='Blank = now'),
        Field('reviewScore', 'Stars', kind='stars', lo=1, hi=5, required=True, column=True),
        Field('rejectionFlag', 'Flag', auto=True, column=True),
        Field('reviewCommentTitle', 'Title', maxlen=120),
        Field('reviewCommentMessage', 'Comment', kind='text', maxlen=2000,
              hint='English text is scored for sentiment (VADER) to set severity'),
        Field('defectTypeId', 'Defect type', kind='int', ref='defect_types', column=True),
        Field('vaderSeverityWeight', 'Severity 0–5', kind='float', auto=True, column=True))),
    Table('defect_types', 'Defect types', 'defect type', 'defectTypeId', 'next',
          note='Lookup table for review defects; severity weights the defect score.', fields=(
        Field('defectTypeId', 'ID', kind='int', lo=1, hi=9999, column=True, hint='Blank = next free ID'),
        Field('category', 'Category', required=True, column=True),
        Field('severity', 'Severity 0–5', kind='float', lo=0, hi=5, required=True, column=True))),
    Table('price_history', 'Price history', 'price record', None, 'auto', serial='priceRecordId',
          note='Past prices per seller and product.', sort=None, fields=(
        Field('priceRecordId', 'Record #', kind='int', auto=True, column=True),
        Field('sellerId', 'Seller', ref='sellers', required=True, column=True),
        Field('productId', 'Product', ref='products', required=True, column=True),
        Field('priceDate', 'Date', kind='datetime', required=True, column=True),
        Field('unitPrice', 'Price', kind='float', lo=0, hi=1_000_000, required=True, column=True, money=True),
        Field('priceChangePercent', 'Change %', kind='float', lo=-100, hi=100_000, column=True))),
    Table('risk_scores', 'Risk scores', 'risk score', None, 'model', editable=False, sort=('UpdatedAt', 'DESCENDING'),
          note='Model output, one per seller per month. Created by scoring, never typed in.', fields=(
        Field('SellerID', 'Seller', ref='sellers', required=True, column=True),
        Field('EvaluationPeriod', 'Period', auto=True, column=True),
        Field('WeightedRiskScore', 'Score', kind='float', auto=True, column=True),
        Field('RiskCategory', 'Band', auto=True, column=True),
        Field('RiskTrend', 'Trend', auto=True, column=True),
        Field('ModelVersion', 'Model', auto=True),
        Field('UpdatedAt', 'Updated', kind='datetime', auto=True, column=True))),
    Table('alert_log', 'Alerts', 'alert', None, 'auto', sort=('AlertDate', 'DESCENDING'),
          note='Raised when a seller turns High; can also be logged by hand.', fields=(
        Field('SellerID', 'Seller', ref='sellers', required=True, column=True),
        Field('AlertDate', 'Raised', kind='datetime', auto=True, column=True),
        Field('EvaluationPeriod', 'Period', auto=True),
        Field('ThresholdCrossedCategory', 'Band', kind='choice', choices=('High', 'Medium', 'Low'), required=True, column=True),
        Field('AlertMessage', 'Message', kind='text', required=True, maxlen=500, column=True),
        Field('AlertStatus', 'Status', kind='choice', choices=('Open', 'Acknowledged', 'Resolved'), required=True, column=True),
        Field('EscalationLevel', 'Escalation', kind='int', lo=1, hi=3, required=True),
        Field('Score', 'Score at alert', kind='float', lo=0, hi=100))),
)}

# What happens to rows that point at a deleted row. Everything not listed cascades.
RESTRICT = {('po_lines', 'productId'), ('quality_inspections', 'defectTypeId')}


def children_of(table_name):
    """[(child table, foreign-key field, 'cascade' | 'restrict')] for rows pointing at this table."""
    return [(t.name, f.name, 'restrict' if (t.name, f.name) in RESTRICT else 'cascade')
            for t in TABLES.values() for f in t.fields if f.ref == table_name]


def er():
    """Tables and foreign keys, for the diagram on /data."""
    edges = [(t.name, f.name, f.ref, 'restrict' if (t.name, f.name) in RESTRICT else 'cascade')
             for t in TABLES.values() for f in t.fields if f.ref]
    return list(TABLES.values()), edges


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def now():
    return datetime.datetime.now(UTC)


def chunks(seq, n=30):          # Firestore 'in' takes at most 30 values
    seq = list(seq)
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def eq(field, value):
    return FieldFilter(field, '==', value)


def count(query):
    """Server-side COUNT(*) (aggregation query: ~1 read per 1,000 rows). None if unavailable."""
    try:
        return int(query.count().get()[0][0].value)
    except Exception as exc:  # older emulators / clients
        log.warning("count() failed: %s", exc)
        return None


def by_field(coll, field, values):
    """All rows of coll whose field is in values (parallel 'in' queries)."""
    parts = chunks(sorted({v for v in values if v is not None}))
    if not parts:
        return []

    def run(part):
        return list(db.collection(coll).where(filter=FieldFilter(field, 'in', part)).stream())

    with ThreadPoolExecutor(max_workers=min(8, len(parts))) as pool:
        return [d for docs in pool.map(run, parts) for d in docs]


def key_value(t, doc_id):
    """The value child rows store for this row (defect type IDs are numbers)."""
    f = t.field(t.key) if t.key else None
    if f is not None and f.kind == 'int':
        try:
            return int(doc_id)
        except ValueError:
            return doc_id
    return doc_id


def get(t, doc_id):
    snap = db.collection(t.name).document(str(doc_id)).get()
    return (snap.to_dict() or {}) if snap.exists else None


def exists(table_name, value):
    if table_name == 'sellers':
        from backend.routes.dashboard import get_sellers
        if value in get_sellers():
            return True
    return db.collection(table_name).document(str(value)).get().exists


def as_utc(v):
    if isinstance(v, datetime.datetime):
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    return None


def days_between(later, earlier):
    a, b = as_utc(later), as_utc(earlier)
    return None if a is None or b is None else (a - b).days


def display(f, v):
    """How a stored value reads on a page."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return '—'
    if isinstance(v, datetime.datetime):
        return as_utc(v).strftime('%d %b %Y, %H:%M')
    if f is not None and f.money and isinstance(v, (int, float)):
        return f"R$ {v:,.2f}"
    if f is not None and f.kind == 'stars':
        n = int(v)
        return '★' * n + '☆' * (5 - n)
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip('0').rstrip('.') if abs(v) < 1e9 else str(v)
    if isinstance(v, int):
        return f"{v:,}" if abs(v) >= 10000 else str(v)
    return str(v)


def form_value(f, v):
    """How a stored value pre-fills an input."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ''
    if isinstance(v, datetime.datetime):
        return as_utc(v).strftime('%Y-%m-%dT%H:%M')
    if isinstance(v, float) and v.is_integer() and f.kind in ('int', 'stars'):
        return str(int(v))
    return str(v)


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
def _parse_dt(raw):
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    raise ValueError('Use a date like 2018-03-14 or 2018-03-14T09:30')


def _coerce(f, raw):
    if f.kind in ('str', 'text'):
        if len(raw) > f.maxlen:
            raise ValueError(f'At most {f.maxlen} characters')
        return raw
    if f.kind == 'datetime':
        return _parse_dt(raw)
    if f.kind == 'choice':
        if raw not in f.choices:
            raise ValueError('Pick one of: ' + ', '.join(f.choices))
        return raw
    if f.kind in ('int', 'stars'):
        try:
            v = int(raw)
        except ValueError:
            raise ValueError('Whole number')
    else:  # float
        try:
            v = float(raw)
        except ValueError:
            raise ValueError('Number')
        if not math.isfinite(v):
            raise ValueError('Number')
    if f.lo is not None and v < f.lo or f.hi is not None and v > f.hi:
        lo = int(f.lo) if f.lo is not None and float(f.lo).is_integer() else f.lo
        hi = int(f.hi) if f.hi is not None and float(f.hi).is_integer() else f.hi
        raise ValueError(f'Between {lo:,} and {hi:,}')
    return v


def clean(t, form, creating, prefix=''):
    """Form -> (typed values, {field: error}). Checks types, ranges, required and foreign keys."""
    data, errors = {}, {}
    for f in t.form_fields(creating):
        raw = (form.get(prefix + f.name) or '').strip()
        if raw == '':
            if f.required and not (creating and f.name == t.key and t.key_kind == 'next'):
                errors[f.name] = 'Required'
            data[f.name] = None
            continue
        try:
            v = _coerce(f, raw)
        except ValueError as exc:
            errors[f.name] = str(exc)
            continue
        if f.ref and not exists(f.ref, v):
            errors[f.name] = f"No {TABLES[f.ref].one} with ID {v}"
            continue
        data[f.name] = v
    if creating and t.key_kind == 'given' and t.key not in errors:
        k = data.get(t.key)
        if k is not None and not KEY_RE.match(k):
            errors[t.key] = '3–64 letters, digits, - or _'
        elif k is not None and db.collection(t.name).document(k).get().exists:
            errors[t.key] = f'{t.one.capitalize()} {k} already exists'
    return data, errors


def _next_int(coll, field):
    docs = list(db.collection(coll).order_by(field, direction='DESCENDING').limit(1).stream())
    try:
        return int((docs[0].to_dict() or {}).get(field)) + 1 if docs else 1
    except (TypeError, ValueError):
        return 1


def sellers_of_orders(po_ids):
    return {(d.to_dict() or {}).get('sellerId') for d in by_field('po_lines', 'poId', po_ids)} - {None}


def retag_orders(po_ids):
    """An order's lines changed: refresh the sellerIds tag on its delivery and reviews."""
    for p in {p for p in po_ids if p}:
        tag = sorted(sellers_of_orders({p}))
        for coll in ('deliveries', 'quality_inspections'):
            for d in db.collection(coll).where(filter=eq('poId', p)).stream():
                if (d.to_dict() or {}).get('sellerIds') != tag:
                    d.reference.update({'sellerIds': tag})


# ----------------------------------------------------------------------------
# Per-table rules (derived columns, unique keys, triggers)
# ----------------------------------------------------------------------------
def _prepare(t, data, old, doc_id, errors, creating):
    """Fill derived columns and check table-specific rules. Returns sellers to re-score."""
    old = old or {}
    rescore = set()

    if t.name == 'po_lines' and 'poId' not in errors:
        siblings = [(d.id, d.to_dict() or {}) for d in
                    db.collection('po_lines').where(filter=eq('poId', data['poId'])).stream()]
        taken = {int(s.get('lineNo')) for i, s in siblings if i != doc_id and s.get('lineNo') is not None}
        if data.get('lineNo') is None:
            data['lineNo'] = max(taken, default=0) + 1
        elif data['lineNo'] in taken:
            errors['lineNo'] = f"Line {data['lineNo']} already exists in this order"
        rescore |= {data.get('sellerId'), old.get('sellerId')}

    elif t.name == 'deliveries' and 'poId' not in errors:
        dup = [d.id for d in db.collection('deliveries').where(filter=eq('poId', data['poId'])).limit(2).stream()
               if d.id != doc_id]
        if dup:
            errors['poId'] = f'This order already has a delivery ({dup[0]})'
        else:
            order = get(TABLES['purchase_orders'], data['poId']) or {}
            if data.get('delayDays') is None:
                data['delayDays'] = days_between(data.get('actualDeliveryDate'), order.get('expectedDeliveryDate'))
            data['carrierTransitDelayDays'] = days_between(data.get('actualDeliveryDate'),
                                                           data.get('deliveredCarrierDate'))
            ship_by = [as_utc(s.get('shippingLimitDate')) for s in
                       (d.to_dict() or {} for d in db.collection('po_lines')
                        .where(filter=eq('poId', data['poId'])).stream())]
            ship_by = [s for s in ship_by if s is not None]
            data['sellerDispatchDelayDays'] = (days_between(data.get('deliveredCarrierDate'), min(ship_by))
                                               if ship_by else None)
            data['sellerIds'] = sorted(sellers_of_orders({data['poId']}))   # lets a seller's rows load in one query
            rescore |= sellers_of_orders({data['poId'], old.get('poId')})

    elif t.name == 'quality_inspections' and 'poId' not in errors:
        from backend.sentiment import severity
        if data.get('inspectionDate') is None:
            data['inspectionDate'] = old.get('inspectionDate') or now()
        if data.get('reviewScore') is not None:
            data['rejectionFlag'] = 'Negative' if data['reviewScore'] <= 3 else 'Positive'
        # Olist reviews carry severity from the NLP pipeline (Portuguese text): only re-derive
        # it when the comment itself is new or changed.
        if creating or (data.get('reviewCommentMessage') or '') != (old.get('reviewCommentMessage') or ''):
            data['vaderSeverityWeight'] = severity(data.get('reviewCommentMessage'))
        data['sellerIds'] = sorted(sellers_of_orders({data['poId']}))
        rescore |= sellers_of_orders({data['poId'], old.get('poId')})

    elif t.name == 'defect_types':
        if creating:
            if data.get('defectTypeId') is None:
                data['defectTypeId'] = _next_int('defect_types', 'defectTypeId')
            elif db.collection('defect_types').document(str(data['defectTypeId'])).get().exists:
                errors['defectTypeId'] = f"Defect type {data['defectTypeId']} already exists"
        name = (data.get('category') or '').lower()
        for d in db.collection('defect_types').stream():
            if d.id != str(doc_id) and (d.to_dict() or {}).get('category', '').lower() == name and name:
                errors['category'] = 'Another defect type already has this name'

    elif t.name == 'alert_log' and creating:
        data.update(AlertDate=now(), EvaluationPeriod=scoring.current_period(), Manual=True)

    return rescore - {None}


# ----------------------------------------------------------------------------
# Create / update
# ----------------------------------------------------------------------------
@dataclass
class Outcome:
    ok: bool
    doc_id: str | None = None
    errors: dict = _field(default_factory=dict)
    rescored: list = _field(default_factory=list)
    note: str = ''


def _rescore(seller_ids, skip=()):
    from backend.rescore import rescore_seller
    todo = sorted(set(seller_ids) - set(skip) - {None})
    if todo:
        bump('risk_scores')
        bump('alert_log')
    return [rescore_seller(s) for s in todo]


def create(t, form):
    if t.key_kind == 'model':
        raise ValueError('Risk scores come from the model; use score_seller()')
    data, errors = clean(t, form, creating=True)
    doc_id = None
    if t.key_kind == 'uuid':
        doc_id = uuid.uuid4().hex
        data[t.key] = doc_id
    rescore = _prepare(t, data, None, doc_id, errors, creating=True)

    # An order can be created with its first line in the same atomic batch.
    line = None
    if t.name == 'purchase_orders' and (form.get('line_sellerId') or '').strip():
        lt = TABLES['po_lines']
        line, line_errors = clean(lt, {k[5:]: v for k, v in form.items() if k.startswith('line_')}
                                  | {'poId': '_'}, creating=True)
        line_errors.pop('poId', None)
        errors.update({'line_' + k: v for k, v in line_errors.items()})
    if errors:
        return Outcome(False, errors=errors)

    if t.key_kind == 'given':
        doc_id = data[t.key]
    elif t.key_kind == 'next':
        doc_id = str(data[t.key])
    if t.serial:
        data[t.serial] = _next_int(t.name, t.serial)
    ref = db.collection(t.name).document(doc_id) if doc_id else db.collection(t.name).document()
    batch = db.batch()
    batch.set(ref, data)
    if line is not None:
        line.update(poId=ref.id, lineNo=1)
        batch.set(db.collection('po_lines').document(), line)
        rescore.add(line['sellerId'])
        bump('po_lines')
    batch.commit()
    bump(t.name)
    _after_write(t, ref.id, data)
    if t.name == 'po_lines':
        retag_orders({data.get('poId')})
    return Outcome(True, ref.id, rescored=_rescore(rescore))


def update(t, doc_id, form):
    if not t.editable:
        raise ValueError(f'{t.label} cannot be edited by hand')
    old = get(t, doc_id)
    if old is None:
        return None
    data, errors = clean(t, form, creating=False)
    rescore = _prepare(t, data, old, doc_id, errors, creating=False)
    if errors:
        return Outcome(False, doc_id, errors=errors)
    ref = db.collection(t.name).document(str(doc_id))
    ref.update(data)
    note = ''
    if t.name == 'purchase_orders' and data.get('expectedDeliveryDate') != old.get('expectedDeliveryDate'):
        # Trigger: the promised date moved, so days-late on this order's delivery changes too.
        for d in db.collection('deliveries').where(filter=eq('poId', doc_id)).stream():
            dd = d.to_dict() or {}
            late = days_between(dd.get('actualDeliveryDate'), data.get('expectedDeliveryDate'))
            if late is not None and late != dd.get('delayDays'):
                d.reference.update({'delayDays': late})
                note = 'Days late on its delivery were recalculated.'
                rescore |= sellers_of_orders({doc_id})
    _after_write(t, doc_id, {**old, **data})
    if t.name == 'po_lines':
        retag_orders({data.get('poId'), old.get('poId')})
    return Outcome(True, doc_id, rescored=_rescore(rescore), note=note)


def _after_write(t, doc_id, data):
    if t.name == 'sellers':
        from backend.routes.dashboard import cache_put
        cache_put(doc_id, {k: data.get(k) for k in ('sellerName', 'sellerCity', 'sellerState')})
    elif t.name == 'defect_types':
        import backend.features as features
        features._DEFECT_TYPES = None


def score_seller(seller_id):
    """'Create' for risk_scores: score the seller now with the live model."""
    if not exists('sellers', seller_id):
        return Outcome(False, errors={'SellerID': f'No seller with ID {seller_id}'})
    bump('risk_scores')
    res = _rescore({seller_id})[0]
    doc = scoring.score_doc_id(seller_id, scoring.current_period()) if res.rec else None
    return Outcome(True, doc, rescored=[res])


# ----------------------------------------------------------------------------
# Delete: preview what cascades or blocks, then do it
# ----------------------------------------------------------------------------
@dataclass
class Step:
    depth: int
    table: Table
    refs: list
    why: str


@dataclass
class Plan:
    table: Table
    doc_id: str
    row: dict
    blocked: list = _field(default_factory=list)   # [(Table, field, count)]
    steps: list = _field(default_factory=list)     # [Step]
    sellers: set = _field(default_factory=set)     # re-score afterwards
    scores: list = _field(default_factory=list)    # risk_scores rows being removed

    @property
    def total(self):
        return 1 + sum(len(s.refs) for s in self.steps)


def delete_plan(t, doc_id):
    row = get(t, doc_id)
    if row is None:
        return None
    plan = Plan(t, str(doc_id), row)
    value = key_value(t, doc_id)
    if t.key is None:
        value = None                       # nothing points at auto-ID rows

    for child, fname, rule in (children_of(t.name) if value is not None else []):
        q = db.collection(child).where(filter=eq(fname, value))
        if rule == 'restrict':
            n = count(q)
            if n is None:
                n = len(list(q.limit(1000).stream()))
            if n:
                plan.blocked.append((TABLES[child], fname, n))
        else:
            docs = list(q.stream())
            if docs:
                plan.steps.append(Step(1, TABLES[child], docs, f'{fname} = {value}'))

    lines = next((s.refs for s in plan.steps if s.table.name == 'po_lines'), [])
    if t.name == 'sellers' and lines:
        # Orders whose only lines belong to this seller go too, with their delivery and reviews.
        po_ids = {(d.to_dict() or {}).get('poId') for d in lines} - {None}
        shared = {(d.to_dict() or {}).get('poId') for d in by_field('po_lines', 'poId', po_ids)
                  if (d.to_dict() or {}).get('sellerId') != doc_id}
        orphans = sorted(po_ids - shared)
        if orphans:
            # references only: no need to read 2,000 orders just to delete them
            refs = [db.collection('purchase_orders').document(p) for p in orphans]
            plan.steps.append(Step(2, TABLES['purchase_orders'], refs, 'orders left with no lines'))
            for coll in ('deliveries', 'quality_inspections'):
                docs = by_field(coll, 'poId', orphans)
                if docs:
                    plan.steps.append(Step(3, TABLES[coll], docs, 'of those orders'))
    if t.name == 'purchase_orders':
        plan.sellers = {(d.to_dict() or {}).get('sellerId') for d in lines} - {None}
    elif t.name == 'po_lines':
        plan.sellers = {row.get('sellerId')} - {None}
    elif t.name in ('deliveries', 'quality_inspections'):
        plan.sellers = sellers_of_orders({row.get('poId')})
    if t.name == 'risk_scores':
        plan.scores = [row]
    elif t.name == 'sellers':
        plan.scores = [d.to_dict() or {} for s in plan.steps if s.table.name == 'risk_scores' for d in s.refs]
    plan.steps.sort(key=lambda s: s.depth)
    return plan


def delete(plan):
    if plan.blocked:
        return Outcome(False, plan.doc_id, errors={'_': 'Blocked by linked rows'})
    refs = [d.reference if hasattr(d, 'reference') else d for s in plan.steps for d in s.refs]
    refs.append(db.collection(plan.table.name).document(plan.doc_id))
    batch, n = db.batch(), 0
    for ref in refs:                      # big cascades span several batches of 400
        batch.delete(ref)
        n += 1
        if n >= BATCH:
            batch.commit()
            batch, n = db.batch(), 0
    if n:
        batch.commit()
    for name in {plan.table.name} | {s.table.name for s in plan.steps}:
        bump(name)

    from backend.routes.dashboard import cache_drop, scores_drop
    for rec in plan.scores:
        scoring.remove_from_meta(db, rec)
        scores_drop(rec.get('SellerID'))
    gone = set()
    if plan.table.name == 'sellers':
        cache_drop(plan.doc_id)
        gone.add(plan.doc_id)
    elif plan.table.name == 'defect_types':
        import backend.features as features
        features._DEFECT_TYPES = None
    if plan.table.name == 'po_lines':
        retag_orders({plan.row.get('poId')})
    return Outcome(True, plan.doc_id, rescored=_rescore(plan.sellers, skip=gone))


# ----------------------------------------------------------------------------
# Read: lists, linked rows, order summaries, counts
# ----------------------------------------------------------------------------
@dataclass
class Page:
    rows: list                 # [(doc_id, dict)]
    next: str | None = None    # cursor for the next page
    page: int = 1
    pages: int | None = None


def typed_filter(t, fname, raw):
    f = t.field(fname)
    if f is None or raw in (None, ''):
        return None
    try:
        return _coerce(f, raw) if f.kind in ('int', 'stars', 'float') else raw
    except ValueError:
        return raw


def list_page(t, fname=None, raw=None, after=None):
    """One page of a table, newest first where the table has a date; filter = one equality."""
    q = db.collection(t.name)
    value = typed_filter(t, fname, raw)
    if value is not None:
        q = q.where(filter=eq(fname, value)).order_by('__name__')
    elif t.sort:
        q = q.order_by(t.sort[0], direction=t.sort[1])
    else:
        q = q.order_by('__name__')
    if after:
        snap = db.collection(t.name).document(after).get()
        if snap.exists:
            q = q.start_after(snap)
    docs = list(q.limit(PAGE + 1).stream())
    more = len(docs) > PAGE
    docs = docs[:PAGE]
    return Page([(d.id, d.to_dict() or {}) for d in docs], next=docs[-1].id if more and docs else None)


_ORDER_INDEX = {}            # sellerId -> (built_at, [poId newest first]); dropped on any order/line write
_ORDER_INDEX_LOCK = threading.Lock()


def _seller_order_index(seller_id):
    with _ORDER_INDEX_LOCK:
        hit = _ORDER_INDEX.get(seller_id)
        if hit and time.time() - hit[0] < COUNTS_TTL:
            return hit[1]
    po_ids = sorted({(d.to_dict() or {}).get('poId') for d in
                     db.collection('po_lines').where(filter=eq('sellerId', seller_id)).stream()} - {None})
    epoch = datetime.datetime(1970, 1, 1, tzinfo=UTC)
    snaps = db.get_all([db.collection('purchase_orders').document(p) for p in po_ids], field_paths=['orderDate'])
    dated = [(as_utc((s.to_dict() or {}).get('orderDate')) or epoch, s.id) for s in snaps if s.exists]
    order = [i for _, i in sorted(dated, reverse=True)]
    with _ORDER_INDEX_LOCK:
        _ORDER_INDEX[seller_id] = (time.time(), order)
    return order


def seller_orders(seller_id, page=1):
    """A seller's orders, newest first. The date index is built once (dates only) and cached;
    each page then reads just its 50 orders."""
    order = _seller_order_index(seller_id)
    pages = max(1, math.ceil(len(order) / PAGE))
    page = min(max(1, page), pages)
    ids = order[(page - 1) * PAGE: page * PAGE]
    snaps = {s.id: s for s in db.get_all([db.collection('purchase_orders').document(p) for p in ids]) if s.exists}
    return Page([(p, snaps[p].to_dict() or {}) for p in ids if p in snaps], page=page, pages=pages), len(order)


def order_summaries(po_ids):
    """{poId: {sellers, items, value, delay, stars}} for a page of orders (6 small queries)."""
    out = {p: {'sellers': set(), 'items': 0, 'value': 0.0, 'delay': None, 'stars': []} for p in po_ids}
    with ThreadPoolExecutor(max_workers=3) as pool:
        lines, dels, revs = pool.map(lambda c: by_field(c, 'poId', po_ids), ('po_lines', 'deliveries', 'quality_inspections'))
    for d in lines:
        r = d.to_dict() or {}
        o = out.get(r.get('poId'))
        if o is None:
            continue
        o['sellers'].add(r.get('sellerId'))
        o['items'] += 1
        try:
            o['value'] += float(r.get('unitPriceAtOrder') or 0) + float(r.get('freightValue') or 0)
        except (TypeError, ValueError):
            pass
    for d in dels:
        r = d.to_dict() or {}
        if r.get('poId') in out:
            out[r['poId']]['delay'] = r.get('delayDays')
    for d in revs:
        r = d.to_dict() or {}
        if r.get('poId') in out and r.get('reviewScore') is not None:
            out[r['poId']]['stars'].append(float(r['reviewScore']))
    for o in out.values():
        o['stars'] = sum(o['stars']) / len(o['stars']) if o['stars'] else None
        o['sellers'] = sorted(o['sellers'] - {None})
    return out


@dataclass
class Linked:
    table: Table
    field: str
    rule: str
    count: int
    rows: list


def linked(t, doc_id):
    """Rows in other tables that point at this one (shown on its record page)."""
    if t.key is None:
        return []
    value = key_value(t, doc_id)
    out = []
    for child, fname, rule in children_of(t.name):
        q = db.collection(child).where(filter=eq(fname, value))
        docs = list(q.limit(LINKED + 1).stream())
        n = len(docs) if len(docs) <= LINKED else (count(q) or len(docs))
        out.append(Linked(TABLES[child], fname, rule, n, [(d.id, d.to_dict() or {}) for d in docs[:LINKED]]))
    return out


_COUNTS = {'at': 0.0, 'data': {}}
_COUNTS_LOCK = threading.Lock()
COUNTS_TTL = 600


def bump(table_name):
    """A table changed: its cached row count (and, for orders/lines, the order index) is stale."""
    with _COUNTS_LOCK:
        _COUNTS['data'].pop(table_name, None)
    if table_name in ('purchase_orders', 'po_lines'):
        with _ORDER_INDEX_LOCK:
            _ORDER_INDEX.clear()


def table_counts(cached_only=False):
    """{table: rows} via COUNT aggregation, cached for 10 minutes. None if not cached and cached_only."""
    with _COUNTS_LOCK:
        if time.time() - _COUNTS['at'] > COUNTS_TTL:
            _COUNTS.update(at=time.time(), data={})
        missing = [n for n in TABLES if n not in _COUNTS['data']]
        if not missing:
            return dict(_COUNTS['data'])
        if cached_only:
            return None
    with ThreadPoolExecutor(max_workers=len(missing)) as pool:     # all tables counted side by side
        got = dict(zip(missing, pool.map(lambda n: count(db.collection(n)), missing)))
    with _COUNTS_LOCK:
        _COUNTS['data'].update(got)
        return dict(_COUNTS['data'])
