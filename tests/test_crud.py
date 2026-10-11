"""CRUD on every table: create, read, update, delete, plus the rules a relational DB would enforce."""
import re

from conftest import BAD, EMPTY, GOOD


def _scored(db):
    from pipeline.risk_scoring_pipeline import run_pipeline
    run_pipeline(db)
    return db.docs('system_config')['dashboard_metadata']


def _post(c, url, data, **kw):
    return c.post(url, data={**data, 'csrf_token': c.csrf}, **kw)


def _period():
    from backend.scoring import current_period
    return current_period()


def _one(db, coll, **match):
    return [(i, d) for i, d in db.docs(coll).items() if all(d.get(k) == v for k, v in match.items())]


# ------------------------------------------------------------------ read ----
def test_every_data_page_renders(mono, db):
    from backend.crud import TABLES
    _scored(db)
    assert mono.get('/data/').status_code == 200
    for name, t in TABLES.items():
        r = mono.get(f'/data/{name}')
        assert r.status_code == 200, name
        rows = db.docs(name)
        if rows:
            doc_id = next(iter(rows))
            want = 302 if name == 'sellers' else 200          # sellers open their seller page
            assert mono.get(f'/data/{name}/{doc_id}').status_code == want, name
            assert mono.get(f'/data/{name}/{doc_id}/delete').status_code == 200, name
            if t.editable:
                assert mono.get(f'/data/{name}/{doc_id}/edit').status_code == 200, name
        if t.key_kind != 'model':
            assert mono.get(f'/data/{name}/new').status_code == 200, name
    assert mono.get('/data/nope').status_code == 404
    assert mono.get('/data/purchase_orders/missing').status_code == 404
    assert mono.get(f'/data/purchase_orders?seller={BAD}').status_code == 200


def test_counts_cover_every_table(mono, db):
    from backend.crud import TABLES
    counts = mono.get('/data/counts').get_json()
    assert set(counts) == set(TABLES)
    assert counts['sellers'] == 3 and counts['purchase_orders'] == 32 and counts['po_lines'] == 32


def test_list_pages_and_filters(mono, db):
    for i in range(55):
        db.collection('price_history').document(f'ph{i:03d}').set(
            {'priceRecordId': i, 'sellerId': GOOD, 'productId': 'p0', 'unitPrice': 10.0 + i})
    html = mono.get('/data/price_history').get_data(as_text=True)
    nxt = re.search(r'after=(ph\d+)', html).group(1)
    assert nxt == 'ph049'
    html2 = mono.get(f'/data/price_history?after={nxt}').get_data(as_text=True)
    assert html2.count('class="link') == 5
    html = mono.get(f'/data/po_lines?f=sellerId&v={BAD}').get_data(as_text=True)
    assert html.count('class="link') == 12 and f'{GOOD}-po' not in html


def test_classic_theme_has_full_crud_too(logged_in, db):
    from backend.crud import TABLES
    _scored(db)
    assert logged_in.get('/data/').status_code == 200
    for name, t in TABLES.items():
        assert logged_in.get(f'/data/{name}').status_code == 200, name
        rows = db.docs(name)
        if rows:
            doc_id = next(iter(rows))
            assert logged_in.get(f'/data/{name}/{doc_id}').status_code in (200, 302), name
            assert logged_in.get(f'/data/{name}/{doc_id}/delete').status_code == 200, name
            if t.editable:
                assert logged_in.get(f'/data/{name}/{doc_id}/edit').status_code == 200, name
        if t.key_kind != 'model':
            assert logged_in.get(f'/data/{name}/new').status_code == 200, name
    html = logged_in.get('/data/').get_data(as_text=True)
    assert 'Instrument Serif' not in html                          # really the classic look
    r = logged_in.post('/data/defect_types/new', data={'category': 'Classic Test', 'severity': '1'})
    assert r.status_code == 302 and '17' in db.docs('defect_types')
    logged_in.post('/data/defect_types/17/edit', data={'category': 'Classic Test', 'severity': '2'})
    assert db.docs('defect_types')['17']['severity'] == 2.0
    logged_in.post('/data/defect_types/17/delete')
    assert '17' not in db.docs('defect_types')


def test_data_forms_have_csrf_and_reject_without_it(mono, db):
    _scored(db)
    for path in ('/data/purchase_orders/new', f'/data/purchase_orders/{GOOD}-po1', f'/data/sellers/{GOOD}/delete',
                 '/data/risk_scores', '/data/alert_log/new'):
        html = mono.get(path).get_data(as_text=True)
        forms = re.findall(r'<form[^>]*method="POST".*?</form>', html, re.S | re.I)
        assert forms and all('name="csrf_token"' in f for f in forms), path
    r = mono.post('/data/defect_types/new', data={'category': 'X', 'severity': '1'})
    assert r.status_code == 400 and not _one(db, 'defect_types', category='X')


# ---------------------------------------------------------- full lifecycle ----
def test_order_lifecycle_rescores_the_seller(mono, db):
    _scored(db)
    p = _period()
    before = db.docs('risk_scores')[f'{GOOD}_{p}']['WeightedRiskScore']
    stamp = db.docs('risk_scores')[f'{GOOD}_{p}']['UpdatedAt']

    # CREATE an order with its first line in one batch
    r = _post(mono, '/data/purchase_orders/new', {
        'orderDate': '2018-05-01T10:00', 'expectedDeliveryDate': '2018-05-10T00:00', 'orderStatus': 'shipped',
        'line_sellerId': GOOD, 'line_productId': 'p1', 'line_unitPriceAtOrder': '80', 'line_freightValue': '12.5'})
    assert r.status_code == 302
    po = r.headers['Location'].rsplit('/', 1)[1]
    assert re.fullmatch(r'[0-9a-f]{32}', po)
    order = db.docs('purchase_orders')[po]
    assert order['orderStatus'] == 'shipped' and order['orderDate'].tzinfo is not None
    [(_, line)] = _one(db, 'po_lines', poId=po)
    assert line['sellerId'] == GOOD and line['lineNo'] == 1 and line['unitPriceAtOrder'] == 80.0

    # CREATE a late delivery: days late are derived from the promised date
    r = _post(mono, '/data/deliveries/new', {'poId': po, 'deliveredCarrierDate': '2018-05-03T09:00',
                                             'actualDeliveryDate': '2018-05-30T12:00', 'next': f'/data/purchase_orders/{po}'})
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/data/purchase_orders/{po}')
    [(did, dl)] = _one(db, 'deliveries', poId=po)
    assert dl['delayDays'] == 20 and dl['carrierTransitDelayDays'] == 27 and isinstance(dl['deliveryId'], int)

    # CREATE a 1-star review: flag and sentiment severity are derived
    r = _post(mono, '/data/quality_inspections/new', {'poId': po, 'reviewScore': '1', 'defectTypeId': '13',
                                                      'reviewCommentMessage': 'Terrible. Arrived broken and very late, awful.'})
    assert r.status_code == 302
    [(rid, rv)] = _one(db, 'quality_inspections', poId=po)
    assert rv['rejectionFlag'] == 'Negative' and rv['vaderSeverityWeight'] > 2 and rv['defectTypeId'] == 13
    assert db.docs('risk_scores')[f'{GOOD}_{p}']['UpdatedAt'] > stamp          # re-scored live

    # READ: the order page shows its lines, delivery and review
    html = mono.get(f'/data/purchase_orders/{po}').get_data(as_text=True)
    assert 'Order lines' in html and 'Deliveries' in html and 'Reviews' in html and '20 d late' in html

    # UPDATE: moving the promised date recalculates days late (a trigger)
    r = _post(mono, f'/data/purchase_orders/{po}/edit', {'orderDate': '2018-05-01T10:00',
                                                        'expectedDeliveryDate': '2018-06-05T00:00', 'orderStatus': 'delivered'})
    assert r.status_code == 302
    assert db.docs('deliveries')[did]['delayDays'] == -6
    assert db.docs('purchase_orders')[po]['orderStatus'] == 'delivered'

    # UPDATE a review: stars change, the NLP severity of an unchanged comment is kept
    sev = db.docs('quality_inspections')[rid]['vaderSeverityWeight']
    r = _post(mono, f'/data/quality_inspections/{rid}/edit', {'poId': po, 'reviewScore': '4', 'defectTypeId': '13',
                                                             'inspectionDate': '2018-06-01T00:00',
                                                             'reviewCommentMessage': rv['reviewCommentMessage']})
    assert r.status_code == 302
    rv2 = db.docs('quality_inspections')[rid]
    assert rv2['reviewScore'] == 4 and rv2['rejectionFlag'] == 'Positive' and rv2['vaderSeverityWeight'] == sev

    # DELETE the order: lines, delivery and review go with it
    assert 'data-hold' in mono.get(f'/data/purchase_orders/{po}/delete').get_data(as_text=True)
    r = _post(mono, f'/data/purchase_orders/{po}/delete', {})
    assert r.status_code == 302
    assert po not in db.docs('purchase_orders')
    assert not _one(db, 'po_lines', poId=po) and not _one(db, 'deliveries', poId=po)
    assert not _one(db, 'quality_inspections', poId=po)
    assert abs(db.docs('risk_scores')[f'{GOOD}_{p}']['WeightedRiskScore'] - before) < 1e-6   # back where it was


def test_line_crud_and_unique_line_numbers(mono, db):
    po = f'{GOOD}-po3'
    r = _post(mono, '/data/po_lines/new', {'poId': po, 'sellerId': GOOD, 'productId': 'p2', 'unitPriceAtOrder': '5'})
    assert r.status_code == 302
    [(lid, line)] = [x for x in _one(db, 'po_lines', poId=po) if x[1]['lineNo'] == 2]  # next free number
    r = _post(mono, '/data/po_lines/new', {'poId': po, 'lineNo': '1', 'sellerId': GOOD})
    assert r.status_code == 400 and 'already exists' in r.get_data(as_text=True)
    r = _post(mono, f'/data/po_lines/{lid}/edit', {'poId': po, 'lineNo': '2', 'sellerId': GOOD, 'productId': 'p2',
                                                    'unitPriceAtOrder': '7.25', 'freightValue': ''})
    assert r.status_code == 302 and db.docs('po_lines')[lid]['unitPriceAtOrder'] == 7.25
    assert _post(mono, f'/data/po_lines/{lid}/delete', {}).status_code == 302
    assert lid not in db.docs('po_lines')


def test_foreign_keys_and_types_are_checked(mono, db):
    n = len(db.docs('po_lines'))
    cases = [({'poId': 'ghost', 'sellerId': GOOD}, 'No order with ID ghost'),
             ({'poId': f'{GOOD}-po1', 'sellerId': 'ghost'}, 'No seller with ID ghost'),
             ({'poId': f'{GOOD}-po1', 'sellerId': GOOD, 'productId': 'nope'}, 'No product with ID nope'),
             ({'poId': f'{GOOD}-po1', 'sellerId': GOOD, 'unitPriceAtOrder': '-3'}, 'Between'),
             ({'poId': f'{GOOD}-po1', 'sellerId': GOOD, 'unitPriceAtOrder': 'abc'}, 'Number'),
             ({'sellerId': GOOD}, 'Required')]
    for data, msg in cases:
        r = _post(mono, '/data/po_lines/new', data)
        assert r.status_code == 400 and msg in r.get_data(as_text=True), msg
    assert len(db.docs('po_lines')) == n
    r = _post(mono, '/data/quality_inspections/new', {'poId': f'{GOOD}-po1', 'reviewScore': '9'})
    assert r.status_code == 400
    r = _post(mono, '/data/purchase_orders/new', {'orderDate': 'yesterday', 'orderStatus': 'lost'})
    assert r.status_code == 400 and 'Use a date' in r.get_data(as_text=True) and 'Pick one of' in r.get_data(as_text=True)


def test_one_delivery_per_order(mono, db):
    r = _post(mono, '/data/deliveries/new', {'poId': f'{GOOD}-po1', 'delayDays': '2'})
    assert r.status_code == 400 and 'already has a delivery' in r.get_data(as_text=True)


# ------------------------------------------------------------- deletes ----
def test_restrict_blocks_deleting_a_used_defect_type(mono, db):
    html = mono.get('/data/defect_types/16/delete').get_data(as_text=True)
    assert 'Can’t delete' in html and 'Reviews' in html
    assert _post(mono, '/data/defect_types/16/delete', {}).status_code == 302
    assert '16' in db.docs('defect_types')                     # still there


def test_defect_type_crud(mono, db):
    r = _post(mono, '/data/defect_types/new', {'category': 'Packaging Damage', 'severity': '2.5'})
    assert r.status_code == 302 and '17' in db.docs('defect_types')     # next free ID
    assert _post(mono, '/data/defect_types/new', {'category': 'packaging damage', 'severity': '1'}).status_code == 400
    r = _post(mono, '/data/defect_types/17/edit', {'category': 'Packaging Damage', 'severity': '3'})
    assert r.status_code == 302 and db.docs('defect_types')['17']['severity'] == 3.0
    assert _post(mono, '/data/defect_types/17/delete', {}).status_code == 302
    assert '17' not in db.docs('defect_types')


def test_products_crud_and_restrict(mono, db):
    r = _post(mono, '/data/products/new', {'productId': 'prod-new-1', 'productCategoryName': 'toys'})
    assert r.status_code == 302 and db.docs('products')['prod-new-1']['productCategoryName'] == 'toys'
    assert _post(mono, '/data/products/new', {'productId': 'prod-new-1'}).status_code == 400   # duplicate key
    assert _post(mono, '/data/products/new', {'productId': 'bad id!'}).status_code == 400
    r = _post(mono, '/data/products/prod-new-1/edit', {'productCategoryName': 'games'})
    assert r.status_code == 302 and db.docs('products')['prod-new-1']['productCategoryName'] == 'games'
    assert _post(mono, '/data/products/prod-new-1/delete', {}).status_code == 302
    assert 'Can’t delete' in mono.get('/data/products/p0/delete').get_data(as_text=True)   # used by lines


def test_deleting_a_seller_cascades(mono, db):
    meta = _scored(db)
    p = _period()
    assert meta['category_counts']['High'] == 1
    html = mono.get(f'/data/sellers/{BAD}/delete').get_data(as_text=True)
    assert 'Order lines' in html and 'orders left with no lines' in html and 'Risk scores' in html
    r = _post(mono, f'/data/sellers/{BAD}/delete', {})
    assert r.status_code == 302
    assert BAD not in db.docs('sellers')
    assert not _one(db, 'po_lines', sellerId=BAD)
    assert not [k for k in db.docs('purchase_orders') if k.startswith(BAD)]
    assert not [d for d in db.docs('deliveries').values() if d['poId'].startswith(BAD)]
    assert not [d for d in db.docs('quality_inspections').values() if d['poId'].startswith(BAD)]
    assert f'{BAD}_{p}' not in db.docs('risk_scores') and not _one(db, 'alert_log', SellerID=BAD)
    meta = db.docs('system_config')['dashboard_metadata']
    assert meta['category_counts']['High'] == 0 and sum(meta['score_histogram']) == 1
    assert all(t['SellerID'] != BAD for t in meta['top_risky'])
    assert len(_one(db, 'po_lines', sellerId=GOOD)) == 20       # other seller untouched
    assert mono.get(f'/api/sellers?q={BAD}').get_json() == []   # cache dropped
    from pipeline.risk_scoring_pipeline import run_pipeline
    run_pipeline(db)                                            # no orphan rows bring it back
    assert f'{BAD}_{p}' not in db.docs('risk_scores')


def test_shared_order_keeps_other_sellers_lines(mono, db):
    po = f'{GOOD}-po5'
    db.collection('po_lines').document('shared').set({'poId': po, 'sellerId': BAD, 'lineNo': 2})
    _post(mono, f'/data/sellers/{BAD}/delete', {})
    assert po in db.docs('purchase_orders') and 'shared' not in db.docs('po_lines')
    assert _one(db, 'po_lines', poId=po)


def test_classic_seller_delete_also_cascades(logged_in, db):
    logged_in.post(f'/seller/{BAD}/delete')
    assert BAD not in db.docs('sellers') and not _one(db, 'po_lines', sellerId=BAD)


# --------------------------------------------------- scores and alerts ----
def test_risk_scores_are_created_by_the_model_and_can_be_deleted(mono, db):
    meta = _scored(db)
    p = _period()
    assert mono.get(f'/data/risk_scores/{GOOD}_{p}/edit').status_code == 404    # no hand-typed scores
    assert _post(mono, '/data/risk_scores/score', {'SellerID': 'ghost'}).status_code == 302
    r = _post(mono, '/data/risk_scores/score', {'SellerID': GOOD}, follow_redirects=True)
    assert 'Scored' in r.get_data(as_text=True)
    low = meta['category_counts']['Low']
    assert _post(mono, f'/data/risk_scores/{GOOD}_{p}/delete', {}).status_code == 302
    assert f'{GOOD}_{p}' not in db.docs('risk_scores')
    meta = db.docs('system_config')['dashboard_metadata']
    assert meta['category_counts']['Low'] == low - 1
    assert mono.get(f'/api/sellers?q={GOOD}').get_json()[0]['score'] is None


def test_seller_without_orders_loses_stale_score(mono, db):
    _scored(db)
    p = _period()
    for lid, _ in _one(db, 'po_lines', sellerId=GOOD):
        _post(mono, f'/data/po_lines/{lid}/delete', {})
    assert f'{GOOD}_{p}' not in db.docs('risk_scores')


def test_alert_crud_and_pipeline_keeps_hand_edits(mono, db):
    _scored(db)
    r = _post(mono, '/data/alert_log/new', {'SellerID': GOOD, 'ThresholdCrossedCategory': 'High',
                                            'AlertMessage': 'Buyer escalated by phone', 'AlertStatus': 'Open',
                                            'EscalationLevel': '2', 'Score': ''})
    assert r.status_code == 302
    aid = r.headers['Location'].rsplit('/', 1)[1]
    assert db.docs('alert_log')[aid]['Manual'] is True
    [(bid, _)] = _one(db, 'alert_log', SellerID=BAD)
    assert _post(mono, f'/data/alert_log/{bid}/set', {'field': 'AlertStatus', 'value': 'Acknowledged'}).status_code == 302
    assert db.docs('alert_log')[bid]['AlertStatus'] == 'Acknowledged'
    assert _post(mono, f'/data/alert_log/{bid}/set', {'field': 'AlertMessage', 'value': 'x'}).status_code == 400
    from pipeline.risk_scoring_pipeline import run_pipeline
    run_pipeline(db)
    assert aid in db.docs('alert_log')                                     # manual alert survives
    [(_, a)] = _one(db, 'alert_log', SellerID=BAD)
    assert a['AlertStatus'] == 'Acknowledged'                              # status carried over
    assert _post(mono, f'/data/alert_log/{aid}/delete', {}).status_code == 302
    assert aid not in db.docs('alert_log')


def test_seller_crud_through_data_pages(mono, db):
    r = _post(mono, '/data/sellers/new', {'sellerId': 'acme_02', 'sellerName': 'Acme', 'sellerState': 'RJ',
                                          'sellerZipCodePrefix': '22000'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/seller/acme_02')
    assert db.docs('sellers')['acme_02']['sellerZipCodePrefix'] == 22000
    r = _post(mono, '/data/sellers/acme_02/edit', {'sellerName': 'Acme SA', 'sellerCity': 'rio', 'sellerState': 'RJ',
                                                   'sellerZipCodePrefix': '22000'})
    assert r.status_code == 302 and db.docs('sellers')['acme_02']['sellerName'] == 'Acme SA'
    assert mono.get('/api/sellers?q=acme sa').get_json()[0]['id'] == 'acme_02'
    html = mono.get(f'/seller/{GOOD}').get_data(as_text=True)
    assert '/data/purchase_orders?seller=' in html and f'/data/sellers/{GOOD}/delete' in html
    assert _post(mono, '/data/sellers/acme_02/delete', {}).status_code == 302
    assert 'acme_02' not in db.docs('sellers')
    assert mono.get(f'/seller/{EMPTY}').status_code == 200


def test_next_only_allows_same_site_paths(mono, db):
    r = _post(mono, '/data/products/new', {'productId': 'prod-x9', 'next': '//evil.example/x'})
    assert r.status_code == 302 and 'evil' not in r.headers['Location']


def test_seller_tags_make_one_query_loads_and_stay_in_sync(mono, db):
    from backend.features import compute_seller_features, fetch_frames
    before = compute_seller_features(fetch_frames(db, BAD)).loc[BAD]
    # backfill (same logic as etl/backfill_seller_ids.py)
    lines = {d['poId']: d['sellerId'] for d in db.docs('po_lines').values()}
    for coll in ('deliveries', 'quality_inspections'):
        for d in db.docs(coll).values():
            d['sellerIds'] = [lines[d['poId']]]
    after = compute_seller_features(fetch_frames(db, BAD)).loc[BAD]
    assert after.equals(before)                                     # same features, fewer queries
    # moving a line to another seller re-tags its order's delivery and review
    lid, line = next((i, d) for i, d in db.docs('po_lines').items() if d['sellerId'] == BAD)
    _post(mono, f'/data/po_lines/{lid}/edit', {'poId': line['poId'], 'lineNo': '1', 'sellerId': GOOD})
    tags = [d['sellerIds'] for d in db.docs('deliveries').values() if d['poId'] == line['poId']]
    assert tags == [[GOOD]]
