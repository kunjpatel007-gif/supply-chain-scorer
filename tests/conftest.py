"""
Test setup: fake Firestore + a tiny throwaway model, so tests run anywhere
(no emulator, no Google credentials, no real training data).

    python -m pytest -q
"""
import datetime
import json
import os
import sys
import tempfile
import types

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_firestore import FakeFirestore  # noqa: E402

# --- environment must be ready BEFORE the app is imported -------------------
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['ADMIN_PASSWORD'] = 'correct horse battery staple'
_MODEL_DIR = tempfile.mkdtemp(prefix='risk_model_')
os.environ['RISK_MODEL_DIR'] = _MODEL_DIR

FAKE_DB = FakeFirestore()
_fake_client = types.ModuleType('backend.firebase_client')
_fake_client.db = FAKE_DB
sys.modules['backend.firebase_client'] = _fake_client


def _train_tiny_model():
    """Small model where bad reviews / late deliveries => higher risk."""
    import xgboost as xgb
    from backend.features import FEATURE_COLS
    rng = np.random.default_rng(0)
    n = 600
    X = pd.DataFrame({c: rng.random(n) for c in FEATURE_COLS})
    X['total_orders'] = rng.integers(1, 200, n)
    X['avg_review_score'] = 1 + 4 * rng.random(n)
    y = ((X['negative_review_rate'] + X['pct_late_deliveries']) > 1.0).astype(int)
    m = xgb.XGBClassifier(n_estimators=40, max_depth=3, objective='binary:logistic')
    m.fit(X[FEATURE_COLS], y)
    m.save_model(os.path.join(_MODEL_DIR, 'vendor_risk_model.json'))
    with open(os.path.join(_MODEL_DIR, 'model_card.json'), 'w') as f:
        json.dump({'model_version': 'test-1', 'features': FEATURE_COLS,
                   'bands': {'Medium': 40.0, 'High': 60.0}}, f)


_train_tiny_model()

GOOD, BAD, EMPTY = 'goodseller01', 'badseller01', 'newseller01'


def seed(db):
    """Two sellers with history (one good, one bad) and one with no orders."""
    db._data.clear()
    t0 = datetime.datetime(2018, 1, 1, tzinfo=datetime.timezone.utc)
    for sid, name in ((GOOD, 'campinas, SP'), (BAD, 'recife, PE'), (EMPTY, 'natal, RN')):
        db.collection('sellers').document(sid).set(
            {'sellerId': sid, 'sellerName': name, 'sellerCity': name.split(',')[0], 'sellerState': name[-2:]})
    for d in [{'defectTypeId': 10, 'category': 'Not a Defect', 'severity': 0.0},
              {'defectTypeId': 13, 'category': 'Logistics Issue', 'severity': 3.0},
              {'defectTypeId': 16, 'category': 'Critical Failure', 'severity': 5.0}]:
        db.collection('defect_types').document(d['defectTypeId']).set(d)
    for sid, n, delay, review, dtype, sev in ((GOOD, 20, -5, 5, None, None),
                                               (BAD, 12, 9, 1, 16.0, 5.0)):
        for i in range(n):
            po = f"{sid}-po{i}"
            db.collection('purchase_orders').document(po).set({'poId': po, 'orderDate': t0})
            db.collection('po_lines').document().set(
                {'poId': po, 'sellerId': sid, 'lineNo': 1, 'productId': f'p{i % 3}',
                 'unitPriceAtOrder': 100.0 + i, 'freightValue': 10.0})
            db.collection('deliveries').document().set({'poId': po, 'delayDays': delay})
            db.collection('quality_inspections').document().set(
                {'poId': po, 'reviewScore': review, 'defectTypeId': dtype, 'vaderSeverityWeight': sev})


@pytest.fixture
def db():
    import backend.features as features
    import backend.routes.dashboard as dashboard
    seed(FAKE_DB)
    features._DEFECT_TYPES = None
    dashboard._SELLERS = None
    return FAKE_DB


@pytest.fixture
def app(db):
    from flask_webapp.app import create_app
    return create_app({'TESTING': True, 'WTF_CSRF_ENABLED': False})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(client):
    r = client.post('/login', data={'password': os.environ['ADMIN_PASSWORD']})
    assert r.status_code == 302
    return client
