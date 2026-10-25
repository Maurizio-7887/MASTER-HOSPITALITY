"""DynaPrice, controllo di gestione, pagine con l'hotel dimostrativo, ruoli, spazi, corso e attivazione."""
import re
from datetime import date, timedelta

import pytest

import controllo as ctl
import dynaprice as dp
import hotel as h
from models import Camera, Canale, Ospite, PianoTariffario, Prenotazione, TariffaGiorno, TipoCamera, db

from test_hotel import hotel_base, nuova, tok  # noqa: F401

OGGI = date.today()


def test_soglia_di_margine(hotel_base):
    t = hotel_base['std']   # costo 20, IVA 10%, margine 25%, canale più caro 20% (Expedia)
    s = dp.soglia_prezzo(t)
    assert s == pytest.approx(20 / ((1 - 0.25) / 1.1 - 0.20), abs=0.01)
    # alla soglia, dopo IVA, commissione e costo resta esattamente il 25% del netto
    assert s / 1.1 - 0.20 * s - 20 == pytest.approx(0.25 * s / 1.1, abs=0.01)


def test_griglia_applica_e_listino(hotel_base):
    b = hotel_base
    d = OGGI + timedelta(days=3)
    nuova(b, d, 1)
    nuova(b, d, 1, cognome='Bianchi')
    g = dp.griglia(OGGI, 7)
    r = next(x for x in g['righe'] if x['data'] == d.isoformat())
    std = r['celle'][0]
    assert std['venduto'] == 2 and std['previsione'] == 1.0 and std['suggerito'] > std['prezzo']   # esaurita: si alza
    assert all(c['suggerito'] >= c['soglia'] for x in g['righe'] for c in x['celle'])
    assert dp.applica([(b['std'].id, (d + timedelta(days=1)).isoformat(), 133)]) == 1
    db.session.commit()
    q = h.preventivo(b['std'].id, b['piani']['RO'].id, d + timedelta(days=1), d + timedelta(days=2))
    assert q['totale'] == 133
    dp.torna_al_listino(None, d, d + timedelta(days=5))
    db.session.commit()
    assert h.preventivo(b['std'].id, b['piani']['RO'].id, d + timedelta(days=1), d + timedelta(days=2))['totale'] == 100


def test_rischio_spiegabile(hotel_base):
    b = hotel_base
    ota = nuova(b, OGGI + timedelta(days=100), canale='booking', cognome='Lontano')
    nr = nuova(b, OGGI + timedelta(days=5), piano='NR', cognome='Sicuro')
    h.registra_pagamento(nr, nr.importo)
    db.session.commit()
    r_ota, motivi = dp.rischio(ota)
    r_nr, _ = dp.rischio(nr)
    assert r_ota > r_nr and any('portale' in m for m in motivi)


def test_controllo_kpi(hotel_base):
    b = hotel_base
    da = OGGI - timedelta(days=40)
    p = nuova(b, da, 2, piano='RO', canale='booking')        # 2 notti × 100 lordi, commissione 18%
    p.stato = 'partita'
    db.session.commit()
    r = ctl.analisi(da, da + timedelta(days=10))
    k = r['kpi']
    assert k['vendute'] == 2 and k['disponibili'] == 30
    assert k['adr'] == pytest.approx(100 / 1.1, abs=0.01)
    assert k['revpar'] == pytest.approx(200 / 1.1 / 30, abs=0.01)
    assert k['commissioni'] == pytest.approx(36)
    c = r['canali']['Booking.com']
    assert c['margine'] == pytest.approx(200 / 1.1 - 36 - 2 * 20, abs=0.01)
    assert r['usali']['gop'] == pytest.approx(r['usali']['reparti_utile'])   # nessun costo fisso


def test_pagine_con_demo(demo, client):
    for url in ['/oggi', '/planner', '/api/planner', '/prenotazioni', '/prenotazioni?stato=tutte', '/prenotazioni/nuova', '/ospiti',
                '/housekeeping', '/alloggiati', '/imposta-soggiorno', '/struttura', '/revenue', '/revenue?giorni=90', '/revenue/rischio',
                '/controllo', f'/controllo?da={(OGGI - timedelta(days=60)).isoformat()}&a={OGGI.isoformat()}', '/controllo/prenotazioni.csv',
                '/utenti', '/spazi', '/ospiti/marketing.csv', '/esporta.zip']:
        r = client.get(url)
        assert r.status_code == 200, url
    p = Prenotazione.query.filter_by(stato='in_casa').first()
    assert client.get(f'/prenotazioni/{p.id}').status_code == 200
    assert client.get(f'/ospiti/{p.ospite_id}').status_code == 200
    k = ctl.analisi(OGGI - timedelta(days=30), OGGI)['kpi']
    assert 0.5 < k['occupazione'] < 1 and 100 < k['adr'] < 250 and k['gop'] > 0


def test_simulatore(demo, client):
    prima = Prenotazione.query.count()
    t = tok(client, '/revenue')
    r = client.post('/revenue/simula', data={'_csrf': t, 'giorni': '7'}, follow_redirects=True)
    assert 'Simulati 7 giorni' in r.data.decode()
    assert Prenotazione.query.count() > prima


def test_ruoli(demo, app, client):
    from werkzeug.security import generate_password_hash
    from models import Utente
    db.session.add(Utente(nome='Giulia', username='giulia', ruolo='reception', password_hash=generate_password_hash('password-rec')))
    db.session.add(Utente(nome='Rosa', username='rosa', ruolo='piani', password_hash=generate_password_hash('password-hk')))
    db.session.commit()
    rec = app.test_client()
    rec.post('/login', data={'username': 'giulia', 'password': 'password-rec'})
    assert rec.get('/prenotazioni').status_code == 200
    assert rec.get('/revenue').status_code == 302 and rec.get('/controllo').status_code == 302 and rec.get('/struttura').status_code == 302
    hk = app.test_client()
    hk.post('/login', data={'username': 'rosa', 'password': 'password-hk'})
    assert hk.get('/housekeeping').status_code == 200 and hk.get('/prenotazioni').status_code == 302
    cam = Camera.query.first()
    assert hk.post(f'/api/camere/{cam.id}/stato', json={'stato': 'pulita'}).get_json()['ok']
    assert hk.post(f'/api/camere/{cam.id}/stato', json={'stato': 'fuori_servizio'}).status_code == 403


def test_spazi_separati_e_classe(demo, app, client):
    t = tok(client, '/spazi')
    client.post('/spazi', data={'_csrf': t, 'azione': 'nuovo', 'nome': 'B&B Il Glicine', 'tipo': 'azienda', 'dati': 'vuoto',
                                'admin_nome': 'Lucia', 'admin_username': 'lucia.bb', 'admin_password': 'password-bb'})
    c = app.test_client()
    c.post('/login', data={'username': 'lucia.bb', 'password': 'password-bb'})
    assert c.get('/api/planner').get_json()['prenotazioni'] == []
    assert 'B&amp;B Il Glicine' in c.get('/oggi').data.decode()
    html = client.post('/spazi', data={'_csrf': t, 'azione': 'classe', 'corso': 'Hotel management Firenze', 'prefisso': 'fi',
                                       'numero': '2'}).data.decode()
    creds = re.findall(r'<td><b>(fi\d+)</b></td><td><code>([^<]+)</code>', html)
    assert len(creds) == 2
    s = app.test_client()
    s.post('/login', data={'username': creds[0][0], 'password': creds[0][1]})
    st = s.get('/api/tutor').get_json()
    assert st['ok'] and st['indice'] == 0 and st['totale'] >= 20
    assert s.post('/api/tutor/verifica', json={}).get_json()['indice'] == 1      # passo informativo
    tt = tok(s, '/struttura')
    s.post('/struttura', data={'_csrf': tt, 'sez': 'parametri', 'azione': 'parametri', 'nome_struttura': 'Hotel Le Logge – Anna'})
    assert s.get('/api/tutor').get_json()['indice'] == 2                         # verificato da solo dopo il salvataggio
    s.post('/struttura', data={'_csrf': tt, 'sez': 'tipo', 'azione': 'salva', 'codice': 'TRP', 'nome': 'Tripla', 'posti_base': '2',
                               'posti_max': '3', 'prezzo_base': '175', 'costo_variabile': '28', 'supplemento_persona': '35', 'ordine': '4'})
    s.post('/struttura', data={'_csrf': tt, 'sez': 'camera', 'azione': 'camere_serie',
                               'tipo_id': _tipo_allievo(s), 'da': '401', 'a': '402'})
    assert s.get('/api/tutor').get_json()['indice'] == 4
    # l'altro allievo non vede le modifiche del primo
    s2 = app.test_client()
    s2.post('/login', data={'username': creds[1][0], 'password': creds[1][1]})
    assert s2.get('/api/tutor').get_json()['indice'] == 0
    assert 'TRP' not in s2.get('/struttura?sez=tipo').data.decode()


def _tipo_allievo(c):
    html = c.get('/struttura?sez=camera').data.decode()
    return re.search(r'<option value="(\d+)">Tripla</option>', html).group(1)


def test_ricomincia_corso(demo, app, client):
    t = tok(client, '/spazi')
    html = client.post('/spazi', data={'_csrf': t, 'azione': 'classe', 'corso': 'Prova', 'prefisso': 'rc', 'numero': '1'}).data.decode()
    u, pw = re.findall(r'<td><b>(rc\d+)</b></td><td><code>([^<]+)</code>', html)[0]
    s = app.test_client()
    s.post('/login', data={'username': u, 'password': pw})
    s.post('/api/tutor/verifica', json={})
    r = s.post('/api/tutor/ricomincia', json={}).get_json()
    assert r['indice'] == 0
    assert s.get('/api/planner').get_json()['prenotazioni']


def test_richiesta_con_bonifico(app, client, monkeypatch):
    import abbonamento
    inviate = []
    monkeypatch.setattr(abbonamento, 'invia_email', lambda a, oggetto, testo, rispondi_a=None: inviate.append((a, oggetto, testo)) or True)
    app.config.update(IBAN='IT00X0000000000000000000000', PREZZO_TESTO='39 € + IVA al mese')
    c = app.test_client()
    t = re.search(r'name="_csrf" value="([^"]+)"', c.get('/abbonati').data.decode()).group(1)
    r = c.post('/abbonati', data={'_csrf': t, 'ragione_sociale': 'B&B Le Rose', 'email': 'rose@example.com', 'nome': 'Rosa',
                                  'username': 'rose.bb', 'password': 'password-rose', 'password2': 'password-rose'})
    html = r.data.decode()
    codice = re.search(r'MH-\d{4}-\d{3}', html).group(0)
    assert inviate[0][0] == 'info@mauriziogustinicchiconsulting.it' and 'MASTER HOSPITALITY' in inviate[0][1]
    pagina = client.get('/spazi').data.decode()
    assert codice in pagina
    isc = abbonamento.Iscrizione.query.filter_by(codice=codice).first()
    client.post('/spazi', data={'_csrf': tok(client, '/spazi'), 'azione': 'attiva_richiesta', 'token': isc.token, 'piu': 'anno'})
    d = app.test_client()
    d.post('/login', data={'username': 'rose.bb', 'password': 'password-rose'})
    assert d.get('/oggi').status_code == 200
    # lo spazio nuovo parte con piani tariffari e canali di base
    assert 'Flessibile con colazione' in d.get('/struttura?sez=piano').data.decode()
