"""La richiesta di attivazione arriva nel CRM come Prospect (oltre all'email a info@)."""
import re

import avviso_crm


class Subito:
    def __init__(self, target, args=(), daemon=None): self.t, self.a = target, args
    def start(self): self.t(*self.a)


def test_richiesta_arriva_nel_crm(app, monkeypatch):
    ricevuti = []
    monkeypatch.setattr(avviso_crm, '_invia', ricevuti.append)
    monkeypatch.setattr(avviso_crm.threading, 'Thread', Subito)
    c = app.test_client()
    t = re.search(r'name="_csrf" value="([^"]+)"', c.get('/abbonati').data.decode()).group(1)
    c.post('/abbonati', data={'_csrf': t, 'ragione_sociale': 'Rose Srl', 'email': 'rosa@example.com', 'nome': 'Rosa Rossi',
                              'telefono': '333 1234567', 'username': 'rosa.rose', 'password': 'password-rose', 'password2': 'password-rose'})
    assert len(ricevuti) == 1
    p = ricevuti[0]
    assert p['email'] == 'rosa@example.com' and p['azienda'] == 'Rose Srl' and p['qualifica_ai']['prospect']
    assert 'richiesta di attivazione' in p['fonte'] and p['event_id']
