"""The Monolith theme: every page renders from real backend data, forms are protected, classic is untouched."""
import re

from conftest import BAD, EMPTY, GOOD


def _scored(db):
    from pipeline.risk_scoring_pipeline import run_pipeline
    run_pipeline(db)
    return db.docs('system_config')['dashboard_metadata']


def test_every_page_renders(mono, db):
    _scored(db)
    for path in ('/', '/category/high', '/category/medium', '/category/low', f'/seller/{BAD}', f'/seller/{GOOD}',
                 f'/seller/{EMPTY}', '/simulate', f'/simulate?seller={BAD}', '/seller/new',
                 f'/seller/{GOOD}/edit'):
        r = mono.get(path)
        assert r.status_code == 200, path
        assert b'Instrument Serif' in r.data, path          # really the Monolith base
    assert mono.get('/seller/nope').status_code == 404


def test_dashboard_numbers_come_from_backend(mono, db):
    meta = _scored(db)
    html = mono.get('/').get_data(as_text=True)
    for cat, n in meta['category_counts'].items():
        assert f'data-count="{n}"' in html
    assert html.count('class="b ') == 50                    # one bar per histogram bin
    assert sum(meta['score_histogram']) == 2
    assert meta['period'] in html


def test_every_form_has_csrf_token(mono, db):
    _scored(db)
    for path in ('/', '/simulate', '/seller/new', f'/seller/{GOOD}/edit'):
        html = mono.get(path).get_data(as_text=True)
        forms = re.findall(r'<form[^>]*method="POST".*?</form>', html, re.S | re.I)
        assert forms, path
        assert all('name="csrf_token"' in f for f in forms), path


def test_dropdown_api_returns_real_scores(mono, db):
    _scored(db)
    rows = mono.get('/api/sellers?q=recife').get_json()
    assert rows and rows[0]['id'] == BAD and rows[0]['category'] == 'High' and rows[0]['score'] > 60
    assert mono.get('/api/sellers?q=').get_json() == []
    before = db.reads
    mono.get('/api/sellers?q=campinas')
    assert db.reads == before                                 # served from memory


def test_category_list_served_from_cache(mono, db):
    _scored(db)
    mono.get('/category/high')
    before = db.reads
    html = mono.get('/category/low').get_data(as_text=True)
    assert GOOD in html and BAD not in html
    assert db.reads - before <= 2                             # just the dashboard summary doc


def test_seller_page_explains_score(mono, db):
    _scored(db)
    html = mono.get(f'/seller/{BAD}').get_data(as_text=True)
    assert 'What moves this score' not in html and 'AUC' not in html   # score only, no model talk
    assert 'Critical Failure' in html                         # NLP defects from Firestore
    assert 'R$' in html                                       # Olist prices are in reais


def test_simulation_updates_histogram_and_cache(mono, db):
    meta = _scored(db)
    assert sum(meta['score_histogram']) == 2
    for _ in range(25):
        last = mono.post('/simulate', follow_redirects=True,
                         data={'seller_id': GOOD, 'delay_days': '20', 'csrf_token': mono.csrf,
                               'review_text': 'Arrived broken, terrible quality, awful seller'})
    assert 'Risk score' in last.get_data(as_text=True) and '\u2192' in last.get_data(as_text=True)
    meta = db.docs('system_config')['dashboard_metadata']
    assert sum(meta['score_histogram']) == 2                  # moved between bins, not double-counted
    rows = mono.get(f'/api/sellers?q={GOOD}').get_json()
    rec = [r for r in db.docs('risk_scores').values() if r['SellerID'] == GOOD][0]
    assert abs(rows[0]['score'] - rec['WeightedRiskScore']) < 1e-6


def test_classic_theme_untouched(client, db):
    client.post('/login', data={'password': __import__('os').environ['ADMIN_PASSWORD']})
    r = client.get('/')
    assert r.status_code == 200 and b'Instrument Serif' not in r.data
    assert client.get('/model').status_code == 404            # no model page in the classic theme


def test_motion_assets_cached_and_compressed(mono, db):
    _scored(db)
    html = mono.get('/').get_data(as_text=True)
    css = re.search(r'href="(/static/monolith/motion\.css\?v=[0-9a-f]+)"', html).group(1)
    js = re.search(r'src="(/static/monolith/motion\.js\?v=[0-9a-f]+)"', html).group(1)
    for url in (css, js):
        r = mono.get(url, headers={'Accept-Encoding': 'gzip'})
        assert r.status_code == 200 and r.headers['Content-Encoding'] == 'gzip'
        assert 'max-age=31536000' in r.headers['Cache-Control']
    font = re.search(r'(/static/fonts/instrument-serif-latin-400-normal\.woff2\?v=[0-9a-f]+)', html).group(1)
    assert 'max-age=31536000' in mono.get(font).headers['Cache-Control']


def test_html_is_gzipped_when_accepted(mono, db):
    import gzip as gz
    _scored(db)
    r = mono.get('/category/low', headers={'Accept-Encoding': 'gzip'})
    assert r.headers['Content-Encoding'] == 'gzip' and 'Accept-Encoding' in r.headers['Vary']
    assert GOOD in gz.decompress(r.get_data()).decode()
    assert 'Content-Encoding' not in mono.get('/category/low').headers      # no gzip unless asked


def test_rows_are_real_links_without_bulk_transition_names(mono, db):
    _scored(db)
    for path in ('/', '/category/high', '/category/low'):
        html = mono.get(path).get_data(as_text=True)
        assert 'onclick' not in html, path
        assert 'view-transition-name:s-' not in html, path               # names are assigned just-in-time
    html = mono.get('/category/low').get_data(as_text=True)
    assert f'class="lrow' in html and f'data-sid="{GOOD}"' in html
    assert 'view-transition-name:title' in mono.get(f'/seller/{GOOD}').get_data(as_text=True)


def test_graph_shows_even_without_stored_histogram(mono, db):
    meta = _scored(db)
    meta.pop('score_histogram')
    db.collection('system_config').document('dashboard_metadata').set(meta)
    html = mono.get('/').get_data(as_text=True)
    assert html.count('class="b ') == 50 and 'AUC' not in html and '/model' not in html
    assert mono.get('/model').status_code == 404
    assert mono.get('/favicon.ico').status_code == 204
