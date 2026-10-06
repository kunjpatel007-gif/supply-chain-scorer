import pytest
from flask_webapp.app import create_app
import os

@pytest.fixture
def client():
    # Setup
    os.environ['TESTING'] = 'true'
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test_secret'
    
    with app.test_client() as client:
        yield client

def test_simulator_auth_redaction(client):
    """
    AUDIT REQUIREMENT: Ensure token redaction and safety during redirects.
    This test verifies that accessing the simulator without auth 
    safely redirects to the login page WITHOUT leaking session tokens in the URL.
    """
    response = client.get('/simulate', follow_redirects=False)
    
    # Verify 302 Redirect
    assert response.status_code == 302
    
    # Verify it redirects precisely to the login endpoint
    assert '/login' in response.headers['Location']
    
    # Verify NO tokens or sensitive info is leaked in the redirect URL
    assert 'token=' not in response.headers['Location']
    assert 'session=' not in response.headers['Location']

def test_simulator_post_auth_redaction(client):
    """
    AUDIT REQUIREMENT: Verify POST requests are also protected and redirect safely.
    """
    response = client.post('/simulate', data={
        'seller_id': 'S999',
        'delay_days': '5',
        'review_text': 'Horrible'
    }, follow_redirects=False)
    
    assert response.status_code == 302
    assert '/login' in response.headers['Location']

if __name__ == '__main__':
    pytest.main(['-v', 'test_simulator.py'])
