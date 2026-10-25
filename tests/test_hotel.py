"""Struttura, prenotazioni, disponibilità, planner, conto, check-in/out, penali, imposta di soggiorno, alloggiati."""
import re
from datetime import date, timedelta

import pytest

import hotel as h
from models import Camera, Canale, Ospite, PianoTariffario, Prenotazione, Stagione, TipoCamera, db

OGGI = date.today()


def tok(c, url='/prenotazioni/nuova'):
    return re.search(r'name="_csrf" value="([^"]+)"', c.get(url).data.decode()).group(1)


@pytest.fixture
def hotel_base(app):
    from seed_demo import struttura_iniziale
    struttura_iniziale('Albergo Prova')
    std = TipoCamera(codice='STD', nome='Doppia', posti_base=2, posti_max=3, prezzo_base=100, supplemento_persona=30, costo_variabile=20)
    sgl = TipoCamera(codice='SGL', nome='Singola', posti_base=1, posti_max=1, prezzo_base=70, costo_variabile=15)
    db.session.add_all([std, sgl])
    db.session.flush()
    db.session.add_all([Camera(numero='101', tipo_id=std.id, piano='1'), Camera(numero='102', tipo_id=std.id, piano='1'),
                        Camera(numero='201', tipo_id=sgl.id, piano='2')])
    db.session.commit()
    piani = {p.codice: p for p in PianoTariffario.query.all()}
    canali = {c.nome: c for c in Canale.query.all()}
    return {'std': std, 'sgl': sgl, 'piani': piani, 'tel': canali['Telefono ed email'], 'booking': canali['Booking.com']}


def nuova(b, arrivo, notti=2, tipo='std', piano='FLEX', cognome='Rossi', canale='tel', **kw):
    o = Ospite(cognome=cognome, nome='Mario', email=f'{cognome.lower()}@example.com')
    db.session.add(o)
    p = h.crea_prenotazione(o, b[tipo].id, b['piani'][piano].id, b[canale].id, arrivo, arrivo + timedelta(days=notti), **kw)
    db.session.commit()
    return p


def test_prezzi_stagione_piano_colazione(hotel_base):
    b = hotel_base
    d = OGGI + timedelta(days=10)
    db.session.add(Stagione(nome='Alta', dal=d, al=d, coefficiente=1.2))
    db.session.commit()
    q = h.preventivo(b['std'].id, b['piani']['FLEX'].id, d, d + timedelta(days=2), adulti=3)
    # notte di alta: 100 × 1,2 + 1 persona extra 30 + colazione 3 × 12; notte normale: 100 + 30 + 36
    assert [n['prezzo'] for n in q['notti']] == [186, 166] and q['notti'][0]['colazione'] == 36
    nr = h.preventivo(b['std'].id, b['piani']['NR'].id, d + timedelta(days=1), d + timedelta(days=2), adulti=2)
    assert nr['totale'] == 88 + 24 and nr['caparra'] == nr['totale']   # -12% sulla camera, caparra 100%
    ro = h.preventivo(b['std'].id, b['piani']['RO'].id, d + timedelta(days=1), d + timedelta(days=2), adulti=2)
    assert ro['totale'] == 100
    with pytest.raises(h.Errore):
        h.preventivo(b['sgl'].id, b['piani']['RO'].id, d, d + timedelta(days=1), adulti=2)


def test_disponibilita_e_assegnazione(hotel_base):
    b = hotel_base
    d = OGGI + timedelta(days=5)
    p1 = nuova(b, d, 3)
    p2 = nuova(b, d + timedelta(days=1), 2, cognome='Bianchi')
    assert {p1.camera.numero, p2.camera.numero} == {'101', '102'}
    with pytest.raises(h.Errore, match='Nessuna camera'):
        nuova(b, d + timedelta(days=2), 1, cognome='Verdi')
    # il preventivo non blocca la camera e non viene bloccato
    pv = nuova(b, d + timedelta(days=2), 1, cognome='Verdi', stato='preventivo')
    assert pv.camera_id is None
    with pytest.raises(h.Errore):
        h.conferma(pv)
    # dopo la partenza dei Rossi la camera torna libera
    p3 = nuova(b, d + timedelta(days=3), 2, cognome='Neri')
    assert p3.camera_id == p1.camera_id


def test_restrizioni(hotel_base):
    from models import Restrizione
    b = hotel_base
    d = OGGI + timedelta(days=20)
    db.session.add(Restrizione(data=d, min_notti=3))
    db.session.commit()
    with pytest.raises(h.Errore, match='soggiorno minimo 3'):
        nuova(b, d, 2)
    assert nuova(b, d, 3).stato == 'confermata'


def test_planner_sposta_e_upgrade(hotel_base, client):
    b = hotel_base
    d = OGGI + timedelta(days=5)
    p1 = nuova(b, d, 2)
    p2 = nuova(b, d, 2, cognome='Bianchi')
    r = client.post(f'/api/prenotazioni/{p1.id}/sposta', json={'camera_id': p2.camera_id})
    assert r.status_code == 400 and 'occupata' in r.get_json()['error']
    r = client.post(f'/api/prenotazioni/{p1.id}/sposta', json={'arrivo': (d + timedelta(days=4)).isoformat()})
    assert r.get_json()['ok']
    db.session.refresh(p1)
    assert p1.arrivo == d + timedelta(days=4) and p1.partenza == d + timedelta(days=6)
    assert [n.data for n in p1.notti_vendute] == [d + timedelta(days=4), d + timedelta(days=5)]
    # upgrade sulla singola: cambia la tipologia, il prezzo resta
    p3 = nuova(b, d + timedelta(days=10), 1, adulti=1, cognome='Solo')
    importo = p3.importo
    cam = Camera.query.filter_by(numero='201').first()
    assert client.post(f'/api/prenotazioni/{p3.id}/sposta', json={'camera_id': cam.id}).get_json()['ok']
    db.session.refresh(p3)
    assert p3.tipo_id == b['sgl'].id and p3.importo == importo
    data = client.get(f'/api/planner?da={d.isoformat()}&giorni=21').get_json()
    assert len(data['camere']) == 3 and any(x['id'] == p1.id for x in data['prenotazioni'])


def test_ciclo_ospite_dal_form(hotel_base, client):
    b = hotel_base
    t = tok(client, '/prenotazioni/nuova')
    r = client.post('/prenotazioni/nuova', data={'_csrf': t, 'arrivo': OGGI.isoformat(), 'partenza': (OGGI + timedelta(days=2)).isoformat(),
                                                 'adulti': 2, 'bambini': 1, 'tipo_id': b['std'].id, 'piano_id': b['piani']['FLEX'].id,
                                                 'canale_id': b['tel'].id, 'cognome': 'Verdi', 'nome': 'Anna', 'email': 'anna@example.com',
                                                 'stato': 'confermata'})
    assert r.status_code == 302
    p = Prenotazione.query.filter(Prenotazione.codice.like('H%')).first()
    pid = p.id
    url = f'/prenotazioni/{pid}'
    # check-in senza alloggiati: rifiutato
    client.post(url, data={'_csrf': t, 'azione': 'check_in'})
    assert db.session.get(Prenotazione, pid).stato == 'confermata'
    client.post(url, data={'_csrf': t, 'azione': 'alloggiato_da_ospite'})
    client.post(url, data={'_csrf': t, 'azione': 'alloggiato', 'cognome': 'Verdi', 'nome': 'Luca', 'tipo': '19'})
    client.post(url, data={'_csrf': t, 'azione': 'alloggiato', 'cognome': 'Verdi', 'nome': 'Sofia', 'tipo': '19',
                           'data_nascita': (OGGI - timedelta(days=365 * 6)).isoformat()})
    client.post(url, data={'_csrf': t, 'azione': 'check_in'})
    p = db.session.get(Prenotazione, pid)
    assert p.stato == 'in_casa'
    client.post(url, data={'_csrf': t, 'azione': 'addebito', 'reparto': 'bar', 'descrizione': 'Aperitivo', 'prezzo': '8', 'quantita': '2', 'iva': '10'})
    c = h.conto(db.session.get(Prenotazione, pid))
    # 2 notti × (100 + 30 + 36) + 16 bar + imposta: 2 adulti × 2 notti × 5 € (la bambina di 6 anni è esente)
    assert c['tassa']['persone'] == 2 and c['tassa']['totale'] == 20
    assert c['totale'] == 2 * 166 + 16 + 20
    client.post(url, data={'_csrf': t, 'azione': 'check_out'})
    assert db.session.get(Prenotazione, pid).stato == 'in_casa'   # saldo aperto
    client.post(url, data={'_csrf': t, 'azione': 'pagamento', 'importo': str(c['saldo']), 'metodo': 'carta'})
    client.post(url, data={'_csrf': t, 'azione': 'check_out'})
    p = db.session.get(Prenotazione, pid)
    assert p.stato == 'partita' and p.camera.stato == 'sporca'
    html = client.get(f'{url}/conto').data.decode()
    assert 'Imposta di soggiorno' in html and 'non valido ai fini fiscali' in html
    assert 'Verdi' in client.get(f'{url}/conferma-email').data.decode()


def test_cancellazioni_e_penali(hotel_base):
    b = hotel_base
    lontana = nuova(b, OGGI + timedelta(days=10))
    assert h.penale_cancellazione(lontana) == 0
    vicina = nuova(b, OGGI + timedelta(days=1), cognome='Bianchi')
    assert h.penale_cancellazione(vicina) == vicina.notti_vendute[0].prezzo
    nr = nuova(b, OGGI + timedelta(days=30), piano='NR', cognome='Neri')
    h.cancella(nr)
    assert nr.stato == 'cancellata' and nr.penale == nr.importo and nr.camera_id is None
    # la camera torna vendibile
    assert h.disponibili_per_notte(b['std'].id, nr.arrivo, nr.partenza)[nr.arrivo] == 2
    oggi = nuova(b, OGGI, cognome='Assente', piano='RO')
    h.no_show(oggi)
    assert oggi.stato == 'no_show' and oggi.penale == 100


def test_opzione_scade(hotel_base):
    b = hotel_base
    p = nuova(b, OGGI + timedelta(days=10), stato='opzione')
    assert p.opzione_fino == OGGI + timedelta(days=3) and p.camera_id
    p.opzione_fino = OGGI - timedelta(days=1)
    db.session.commit()
    assert h.scadi_opzioni() == 1 and p.stato == 'cancellata' and p.penale == 0


def test_gruppo_e_modifica(hotel_base, client):
    b = hotel_base
    t = tok(client)
    d = OGGI + timedelta(days=15)
    client.post('/prenotazioni/nuova', data={'_csrf': t, 'arrivo': d.isoformat(), 'partenza': (d + timedelta(days=2)).isoformat(), 'adulti': 2,
                                             'tipo_id': b['std'].id, 'piano_id': b['piani']['FLEX'].id, 'canale_id': b['tel'].id,
                                             'cognome': 'Coro di Siena', 'gruppo': 'Coro', 'quante': 2})
    g = Prenotazione.query.filter_by(gruppo='Coro').all()
    assert len(g) == 2 and len({p.camera_id for p in g}) == 2
    p = g[0]
    h.modifica(p, b['std'].id, b['piani']['RO'].id, d, d + timedelta(days=3), 2, 0)
    db.session.commit()
    assert p.notti == 3 and p.importo == 300 and len(p.notti_vendute) == 3


def test_tassa_massimo_notti(hotel_base, app):
    b = hotel_base
    from models import Impostazione
    Impostazione.scrivi('tassa_importo', '4')
    Impostazione.scrivi('tassa_max_notti', '3')
    db.session.commit()
    p = nuova(b, OGGI + timedelta(days=3), notti=5)
    assert h.tassa(p)['totale'] == 2 * 3 * 4


def test_alloggiati_tracciato(hotel_base, client):
    from models import Alloggiato
    b = hotel_base
    p = nuova(b, OGGI, 2)
    p.alloggiati.append(Alloggiato(tipo='17', cognome='Rossi', nome='Mario', sesso='M', data_nascita=date(1980, 5, 1), luogo_nascita='048017',
                                   provincia_nascita='FI', stato_nascita='100000100', cittadinanza='100000100', tipo_documento='IDENT',
                                   numero_documento='CA123', luogo_rilascio='048017'))
    p.alloggiati.append(Alloggiato(tipo='19', cognome='Rossi', nome='Anna', sesso='F', data_nascita=date(1982, 1, 2)))
    db.session.commit()
    h.check_in(p)
    db.session.commit()
    txt = client.get(f'/alloggiati?data={OGGI.isoformat()}&formato=txt').data.decode()
    righe = txt.split('\r\n')[:-1]
    assert len(righe) == 2 and all(len(r) == 168 for r in righe)
    assert righe[0].startswith('17' + OGGI.strftime('%d/%m/%Y') + '02ROSSI')
    assert client.get('/imposta-soggiorno').status_code == 200


def test_housekeeping(hotel_base, client):
    cam = Camera.query.filter_by(numero='101').first()
    assert client.post(f'/api/camere/{cam.id}/stato', json={'stato': 'sporca'}).get_json()['ok']
    assert cam.stato == 'sporca'
    assert client.post(f'/api/camere/{cam.id}/stato', json={'stato': 'boh'}).status_code == 400
    assert '101' in client.get('/housekeeping').data.decode()


def test_struttura_crud(app, client):
    t = tok(client, '/struttura')
    client.post('/struttura', data={'_csrf': t, 'sez': 'tipo', 'azione': 'salva', 'codice': 'TRP', 'nome': 'Tripla', 'posti_base': '2',
                                    'posti_max': '3', 'prezzo_base': '175', 'costo_variabile': '28', 'supplemento_persona': '35', 'ordine': '0'})
    tr = TipoCamera.query.filter_by(codice='TRP').first()
    assert tr and tr.prezzo_base == 175
    client.post('/struttura', data={'_csrf': t, 'sez': 'camera', 'azione': 'camere_serie', 'tipo_id': tr.id, 'da': '401', 'a': '403'})
    assert Camera.query.filter_by(tipo_id=tr.id).count() == 3
    r = client.post('/struttura', data={'_csrf': t, 'sez': 'tipo', 'azione': 'salva', 'codice': 'X', 'nome': 'X', 'posti_base': '3',
                                        'posti_max': '2', 'prezzo_base': '10'}, follow_redirects=True)
    assert 'persone massime' in r.data.decode()
    client.post('/struttura', data={'_csrf': t, 'sez': 'parametri', 'azione': 'parametri', 'nome_struttura': 'Hotel Nuovo', 'tassa_importo': '3,5'})
    assert h.par('nome_struttura') == 'Hotel Nuovo' and h.par('tassa_importo') == 3.5
    for sez in ['parametri', 'tipo', 'camera', 'piano', 'stagione', 'canale', 'evento', 'restrizione', 'costo']:
        assert client.get(f'/struttura?sez={sez}').status_code == 200
