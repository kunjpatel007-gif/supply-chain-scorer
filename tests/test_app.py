import os

import pytest

from conftest import BAD, EMPTY, GOOD


# ---------------------------------------------------------------- security --
def test_app_refuses_to_start_without_secrets(monkeypatch):
    from flask_webapp.app import create_app
    monkeypatch.delenv('ADMIN_PASSWORD')
    monkeypatch.setattr('flask_webapp.app.load_env', lambda: None)
    with pytest.raises(RuntimeError, match='ADMIN_PASSWORD'):
        create_app()


def test_default_password_rejected(client):
    r = client.post('/login', data={'password': 'admin123'})
    assert r.status_code == 401
    assert client.get('/').status_code == 302


def test_csrf_blocks_forms_without_token(db):
    from flask_webapp.app import create_app
    c = create_app({'TESTING': True}).test_client()   # CSRF on
    r = c.post('/login', data={'password': os.environ['ADMIN_PASSWORD']})
    assert r.status_code == 400


def test_pages_require_login(client):
    for path in ('/', '/simulate', f'/seller/{GOOD}', '/category/high'):
        r = client.get(path)
        assert r.status_code == 302 and '/login' in r.headers['Location']
    r = client.post('/simulate', data={'seller_id': GOOD, 'delay_days': '5', 'review_text': 'bad'})
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert 'session=' not in r.headers['Location']


# ---------------------------------------------------------------- features --
def test_features_same_in_batch_and_single_seller(db):
    from backend.features import FEATURE_COLS, compute_seller_features, fetch_frames
    batch = compute_seller_features(fetch_frames(db))
    for sid in (GOOD, BAD):
        single = compute_seller_features(fetch_frames(db, sid))
        assert single.loc[sid, FEATURE_COLS].equals(batch.loc[sid, FEATURE_COLS])


def test_feature_values(db):
    from backend.features import compute_seller_features, fetch_frames
    f = compute_seller_features(fetch_frames(db))
    assert f.loc[BAD, 'pct_late_deliveries'] == 1.0
    assert f.loc[BAD, 'negative_review_rate'] == 1.0
    assert f.loc[BAD, 'defect_rate'] == 5.0
    assert f.loc[GOOD, 'pct_late_deliveries'] == 0.0
    assert f.loc[EMPTY, 'total_orders'] == 0
    assert f.loc[EMPTY, 'avg_review_score'] != f.loc[EMPTY, 'avg_review_score']  # NaN, not 0 or 5


# ---------------------------------------------------------------- pipeline --
def test_pipeline_scores_consistently(db):
    from pipeline.risk_scoring_pipeline import run_pipeline
    from backend.scoring import current_period
    run_pipeline(db)
    period = current_period()
    good = db.docs('risk_scores')[f'{GOOD}_{period}']
    bad = db.docs('risk_scores')[f'{BAD}_{period}']
    assert bad['WeightedRiskScore'] > good['WeightedRiskScore']
    assert bad['RiskCategory'] == 'High' and good['RiskCategory'] == 'Low'
    assert f'{EMPTY}_{period}' not in db.docs('risk_scores')        # no orders, no score
    meta = db.docs('system_config')['dashboard_metadata']
    assert meta['category_counts'] == {'High': 1, 'Medium': 0, 'Low': 1}
    assert meta['top_risky'][0]['SellerID'] == BAD
    alerts = list(db.docs('alert_log').values())
    assert [a['SellerID'] for a in alerts] == [BAD]
    run_pipeline(db)                                                 # re-run: no duplicate alerts
    assert len(db.docs('alert_log')) == 1


# ------------------------------------------------------------------- pages --
def test_dashboard_and_lists(logged_in, db):
    from pipeline.risk_scoring_pipeline import run_pipeline
    run_pipeline(db)
    r = logged_in.get('/')
    assert r.status_code == 200 and b'recife, PE' in r.data
    r = logged_in.get('/category/high')
    assert r.status_code == 200 and BAD.encode() in r.data and GOOD.encode() not in r.data


def test_search_uses_cache_not_firestore(logged_in, db):
    logged_in.get('/')                       # warms cache
    before = db.reads
    r = logged_in.post('/search', data={'seller_id': 'recife'})
    assert r.status_code == 302 and BAD in r.headers['Location']
    assert db.reads == before


def test_seller_page(logged_in, db):
    r = logged_in.get(f'/seller/{BAD}')
    assert r.status_code == 200
    assert b'Critical Failure' in r.data           # defects section now filled
    assert b'Risk score' in r.data
    r = logged_in.get(f'/seller/{EMPTY}')
    assert r.status_code == 200 and b'Not enough data' in r.data and b'Not scored yet' in r.data
    assert logged_in.get('/seller/nope').status_code == 404


# --------------------------------------------------------------- simulator --
def test_simulator_writes_what_the_app_reads(logged_in, db):
    from pipeline.risk_scoring_pipeline import run_pipeline
    from backend.scoring import current_period
    run_pipeline(db)
    period = current_period()
    before = db.docs('risk_scores')[f'{GOOD}_{period}']['WeightedRiskScore']

    for _ in range(30):   # pile up bad events on the good seller
        r = logged_in.post('/simulate', data={'seller_id': GOOD, 'delay_days': '20',
                                              'review_text': 'Arrived broken, terrible quality, awful seller'})
        assert r.status_code == 302
    page = logged_in.get(f'/seller/{GOOD}')
    assert b'Event logged' in page.data                       # flash message is shown

    qi = [d for d in db.docs('quality_inspections').values() if d.get('simulated')]
    assert qi and all('vaderSeverityWeight' in d and 'VaderSeverityWeight' not in d for d in qi)
    assert all(0 <= d['vaderSeverityWeight'] <= 5 for d in qi)
    rec = db.docs('risk_scores')[f'{GOOD}_{period}']
    assert set(rec) >= {'SellerID', 'EvaluationPeriod', 'WeightedRiskScore', 'RiskCategory', 'RiskTrend'}
    assert rec['WeightedRiskScore'] > before
    assert GOOD not in db.docs('risk_scores')                   # old wrong doc id not used
    meta = db.docs('system_config')['dashboard_metadata']
    assert sum(meta['category_counts'].values()) == 2           # counts moved, not double-counted
    assert any(t['SellerID'] == GOOD for t in meta['top_risky'])


def test_simulator_rejects_unknown_seller_and_bad_input(logged_in, db):
    n = len(db.docs('po_lines'))
    for data in ({'seller_id': 'ghost', 'delay_days': '3', 'review_text': 'x'},
                 {'seller_id': GOOD, 'delay_days': '-1', 'review_text': 'x'},
                 {'seller_id': GOOD, 'delay_days': 'abc', 'review_text': 'x'}):
        assert logged_in.post('/simulate', data=data).status_code == 302
    assert len(db.docs('po_lines')) == n


# -------------------------------------------------------------------- CRUD --
def test_add_edit_delete_seller(logged_in, db):
    assert logged_in.post('/seller/new', data={'seller_id': '', 'name': 'X'}).status_code == 400
    assert logged_in.post('/seller/new', data={'seller_id': GOOD, 'name': 'X'}).status_code == 400
    r = logged_in.post('/seller/new', data={'seller_id': 'acme_01', 'name': 'Acme', 'city': 'rio', 'state': 'RJ'})
    assert r.status_code == 302 and db.docs('sellers')['acme_01']['sellerName'] == 'Acme'
    logged_in.post('/seller/acme_01/edit', data={'name': 'Acme Ltd', 'city': 'rio', 'state': 'RJ'})
    assert db.docs('sellers')['acme_01']['sellerName'] == 'Acme Ltd'
    r = logged_in.post('/search', data={'seller_id': 'acme ltd'})
    assert 'acme_01' in r.headers['Location']                  # cache updated
    logged_in.post('/seller/acme_01/delete')
    assert 'acme_01' not in db.docs('sellers')
