"""
routes/data.py
--------------
/data: browse and change every table: create, read, update, delete.

Pages are generated from the table descriptions in backend/crud.py, which also
enforces keys, foreign keys, cascades and derived columns, and re-scores sellers
whose data changed. Monolith theme only; the classic theme keeps its own pages.
"""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from backend import crud
from backend.features import get_defect_types
from backend.firebase_client import db
from backend.routes.dashboard import get_seller_map, login_required

data_bp = Blueprint('data', __name__, url_prefix='/data')

DEFAULTS = {
    'purchase_orders': lambda: {'orderDate': crud.form_value(None, crud.now()), 'orderStatus': 'created'},
    'quality_inspections': lambda: {'inspectionDate': crud.form_value(None, crud.now())},
    'alert_log': lambda: {'AlertStatus': 'Open', 'ThresholdCrossedCategory': 'High', 'EscalationLevel': '1'},
    'price_history': lambda: {'priceDate': crud.form_value(None, crud.now())},
}
QUICK = {('purchase_orders', 'orderStatus'), ('alert_log', 'AlertStatus')}   # one-click status changes


@data_bp.record_once
def _template_globals(state):
    state.app.jinja_env.globals['TABLES'] = crud.TABLES


@data_bp.app_template_filter('cell')
def cell(value, f=None):
    return crud.display(f, value)


@data_bp.app_template_filter('short')
def short(value, n=10):
    s = '' if value is None else str(value)
    return s if len(s) <= n + 3 else s[:n] + '…'


def _table(name):
    t = crud.TABLES.get(name)
    if t is None:
        abort(404)
    return t


def _next():
    """Where to go after saving: only same-site paths."""
    nxt = request.values.get('next') or ''
    return nxt if nxt.startswith('/') and not nxt.startswith('//') and '\\' not in nxt else None


def _names():
    """Display names for foreign keys: sellers and defect types."""
    dt = get_defect_types(db)
    defects = {}
    if not dt.empty and 'defectTypeId' in dt:
        for _, r in dt.iterrows():
            try:
                defects[int(r['defectTypeId'])] = r.get('category')
            except (TypeError, ValueError):
                pass
    return {'sellers': get_seller_map(), 'defect_types': defects}


def _url(t, doc_id):
    if t.name == 'sellers':
        return url_for('sellers.seller', seller_id=doc_id)
    return url_for('data.record', table=t.name, doc_id=doc_id)


def _notes(out):
    return ' '.join(f"{r.seller_id}: {r.describe()}" for r in out.rescored if r.describe())


@data_bp.route('/')
@login_required
def index():
    return render_template('data_index.html', tables=list(crud.TABLES.values()),
                           counts=crud.table_counts(cached_only=True))


@data_bp.route('/counts')
@login_required
def counts():
    """Row counts per table (COUNT aggregation, cached 10 min); the index page fetches these."""
    return jsonify(crud.table_counts())


@data_bp.route('/<table>')
@login_required
def rows(table):
    t = _table(table)
    seller = (request.args.get('seller') or '').strip() if t.name == 'purchase_orders' else ''
    f = request.args.get('f') if t.field(request.args.get('f') or '') else None
    v = request.args.get('v') if f else None
    total = None
    if seller:
        page, total = crud.seller_orders(seller, request.args.get('page', 1, type=int))
    else:
        page = crud.list_page(t, f, v, request.args.get('after'))
    summaries = crud.order_summaries([r[0] for r in page.rows]) if t.name == 'purchase_orders' else {}
    return render_template('data_list.html', t=t, page=page, f=f, v=v, seller=seller, total=total,
                           summaries=summaries, names=_names(), quick=QUICK,
                           count=(crud.table_counts(cached_only=True) or {}).get(t.name))


@data_bp.route('/<table>/new', methods=['GET', 'POST'])
@login_required
def create(table):
    t = _table(table)
    if t.key_kind == 'model':
        return redirect(url_for('data.rows', table=t.name))
    if request.method == 'POST':
        out = crud.create(t, request.form)
        if out.ok:
            flash(f"{t.one.capitalize()} created. {_notes(out)}".strip())
            return redirect(_next() or _url(t, out.doc_id))
        return render_template('data_form.html', t=t, creating=True, values=request.form,
                               errors=out.errors, names=_names()), 400
    values = DEFAULTS.get(t.name, dict)()
    values.update({k: v for k, v in request.args.items() if k != 'next'})
    if t.name == 'purchase_orders' and request.args.get('seller'):
        values['line_sellerId'] = request.args['seller']
    return render_template('data_form.html', t=t, creating=True, values=values, errors={}, names=_names())


@data_bp.route('/<table>/<doc_id>')
@login_required
def record(table, doc_id):
    t = _table(table)
    if t.name == 'sellers':
        return redirect(url_for('sellers.seller', seller_id=doc_id))
    row = crud.get(t, doc_id)
    if row is None:
        abort(404)
    summary = crud.order_summaries([doc_id])[doc_id] if t.name == 'purchase_orders' else None
    extra = {k: v for k, v in row.items() if t.field(k) is None}
    return render_template('data_record.html', t=t, doc_id=doc_id, row=row, links=crud.linked(t, doc_id),
                           summary=summary, extra=extra, names=_names(), quick=QUICK)


@data_bp.route('/<table>/<doc_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(table, doc_id):
    t = _table(table)
    if not t.editable:
        abort(404)
    row = crud.get(t, doc_id)
    if row is None:
        abort(404)
    if request.method == 'POST':
        out = crud.update(t, doc_id, request.form)
        if out.ok:
            flash(f"Changes saved. {out.note} {_notes(out)}".strip())
            return redirect(_next() or _url(t, doc_id))
        return render_template('data_form.html', t=t, creating=False, doc_id=doc_id, row=row,
                               values=request.form, errors=out.errors, names=_names()), 400
    values = {f.name: crud.form_value(f, row.get(f.name)) for f in t.fields}
    return render_template('data_form.html', t=t, creating=False, doc_id=doc_id, row=row, values=values,
                           errors={}, names=_names())


@data_bp.route('/<table>/<doc_id>/set', methods=['POST'])
@login_required
def quick_set(table, doc_id):
    """One-click change of a status field (order status, alert status)."""
    t = _table(table)
    fname = request.form.get('field', '')
    if (t.name, fname) not in QUICK:
        abort(400)
    row = crud.get(t, doc_id)
    if row is None:
        abort(404)
    form = {f.name: crud.form_value(f, row.get(f.name)) for f in t.fields}
    form[fname] = request.form.get('value', '')
    out = crud.update(t, doc_id, form)
    if not out.ok:
        flash(' '.join(out.errors.values()))
    return redirect(_next() or _url(t, doc_id))


@data_bp.route('/<table>/<doc_id>/delete', methods=['GET', 'POST'])
@login_required
def remove(table, doc_id):
    t = _table(table)
    plan = crud.delete_plan(t, doc_id)
    if plan is None:
        abort(404)
    if request.method == 'POST':
        if plan.blocked:
            flash(f"Can't delete: other rows still point at this {t.one}.")
            return redirect(url_for('data.remove', table=t.name, doc_id=doc_id))
        out = crud.delete(plan)
        linked = f" and {plan.total - 1:,} linked rows" if plan.total > 1 else ''
        flash(f"Deleted {t.one} {crud.display(None, doc_id)}{linked}. {_notes(out)}".strip())
        home = url_for('dashboard.index') if t.name == 'sellers' else url_for('data.rows', table=t.name)
        if t.name in ('po_lines', 'deliveries', 'quality_inspections') and crud.get(
                crud.TABLES['purchase_orders'], plan.row.get('poId') or '-') is not None:
            home = url_for('data.record', table='purchase_orders', doc_id=plan.row['poId'])   # back to its order
        return redirect(_next() or home)
    return render_template('data_delete.html', t=t, plan=plan, names=_names())


@data_bp.route('/risk_scores/score', methods=['POST'])
@login_required
def score():
    """'Create / update' for risk scores: run the live model on one seller now."""
    sid = (request.form.get('SellerID') or '').strip()
    out = crud.score_seller(sid)
    if not out.ok:
        flash(' '.join(out.errors.values()))
        return redirect(url_for('data.rows', table='risk_scores'))
    flash(f"Scored {sid}. {_notes(out)}".strip())
    return redirect(_next() or url_for('data.rows', table='risk_scores', f='SellerID', v=sid))
