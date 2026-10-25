import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DEMO_DATA', '0')


@pytest.fixture
def app():
    from app import create_app
    from models import db
    a = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                    'ADMIN_USERNAME': 'admin', 'ADMIN_PASSWORD': 'password-test'})
    with a.app_context():
        yield a
        db.session.remove()


def accedi(c, username='admin', password='password-test'):
    r = c.post('/login', data={'username': username, 'password': password})
    assert r.status_code == 302, r.data[:300]
    return c


@pytest.fixture
def client(app):
    return accedi(app.test_client())


@pytest.fixture
def demo(app):
    from seed_demo import carica_demo
    assert carica_demo()
    return app
