"""Cassa: conti separati, ricevute e fatture con numerazione, XML FatturaPA, crediti verso le aziende, note di credito,
punto cassa e chiusura della giornata."""
import json
import re
import xml.dom.minidom
from datetime import date, timedelta

import pytest

import cassa as cs
import hotel as h
from models import Azienda, ChiusuraGiorno, Documento, Impostazione, Pagamento, Prenotazione, VenditaCassa, db
from test_hotel import hotel_base, nuova, tok  # noqa: F401

OGGI = date.today()


@pytest.fixture
def soggiorno(hotel_base):
    for k, v in {'ragione_sociale': 'Albergo Prova Srl', 'partita_iva': '01234567890', 'comune': 'Firenze', 'cap': '50100', 'provincia': 'FI'}.items():
        Impostazione.scrivi(k, v)
    az = Azienda(ragione_sociale='Acme Spa', partita_iva='09876543210', codice_sdi='ABC1234', indirizzo='Via Roma 1', cap='20100', comune='Milano',
                 provincia='MI', giorni_pagamento=30)
    db.session.add(az)
    p = nuova(hotel_base, OGGI - timedelta(days=2), 3)
    p.ospite.nome = 'Mario'
    db.session.add(h.alloggiato_da_ospite(p, p.ospite))
    db.session.add(h.alloggiato_da_ospite(p, p.ospite, '19'))
    db.session.flush()
    h.check_in(p, OGGI - timedelta(days=2))
    db.session.commit()
    return {'p': p, 'az': az, 'b': hotel_base}


def test_conti_separati_e_fattura_a_credito(client, soggiorno):
    p, az = soggiorno['p'], soggiorno['az']
    p.azienda_id = az.id
    h.aggiungi_addebito(p, 'Cena', 40, 1, 'ristorante', 10)
    p.addebiti[-1].conto = 'ospite'
    h.aggiungi_addebito(p, 'Sala riunioni', 122, 1, 'servizi', 22)
    p.addebiti[-1].conto = 'azienda'
    db.session.commit()
    cc = cs.conti(p)
    assert cc['azienda']['totale'] == pytest.approx(p.importo + 122)
    assert cc['ospite']['totale'] == pytest.approx(40 + h.tassa(p)['totale'])
    f = cs.emetti(p, 'fattura', 'azienda')
    db.session.commit()
    assert f.a_credito and f.sigla == f'F1/{OGGI.year}' and f.intestatario == 'Acme Spa'
    assert f.iva == pytest.approx(round(p.importo - p.importo / 1.10, 2) + 22, abs=0.02)
    assert cs.conti(p)['azienda']['saldo'] == pytest.approx(0)              # il saldo è passato al credito verso l'azienda
    doc = xml.dom.minidom.parseString(f.xml.encode())                          # XML ben formato
    assert doc.getElementsByTagName('CodiceDestinatario')[0].firstChild.data == 'ABC1234'
    assert doc.getElementsByTagName('ImportoTotaleDocumento')[0].firstChild.data == f'{f.totale:.2f}'
    with pytest.raises(h.Errore):
        cs.emetti(p, 'fattura', 'azienda')                                    # una volta sola
    r = cs.emetti(p, 'ricevuta', 'ospite')
    assert r.sigla == f'R1/{OGGI.year}' and r.esente == pytest.approx(h.tassa(p)['totale'])
    assert [x['d'].id for x in cs.crediti_aperti()] == [f.id]
    cs.incassa_credito(f)
    assert cs.crediti_aperti() == []


def test_nota_di_credito_e_riemissione(client, soggiorno):
    p = soggiorno['p']
    r = cs.emetti(p, 'ricevuta', 'ospite')
    db.session.commit()
    nc = cs.nota_di_credito(r)
    db.session.commit()
    assert nc.totale == -r.totale and nc.sigla == f'NC1/{OGGI.year}'
    r2 = cs.emetti(p, 'ricevuta', 'ospite')                                   # dopo lo storno si riemette
    assert r2.numero == 2


def test_punto_cassa_e_chiusura(client, soggiorno):
    p = soggiorno['p']
    v = cs.vendita('bar', '2 caffè', 2, 1.5, 10, 0.3, 'contanti')
    h.registra_pagamento(p, 50, 'carta')
    db.session.commit()
    assert v.totale == 3 and Documento.query.get(v.documento_id).totale == 3
    gi = cs.giornata(OGGI)
    assert gi['incassi'] == {'carta': 50.0, 'contanti': 3.0} and gi['camere_vendute'] == 1 and gi['banco'] == 3
    # ieri: l'ospite era già in casa, nessun avviso bloccante → la prima chiusura si può fare su oggi
    c = cs.chiudi(OGGI, 'Admin')
    db.session.commit()
    assert c.camere_vendute == 1 and json.loads(c.incassi)['carta'] == 50
    assert Pagamento.query.filter_by(chiusura_id=c.id).count() == 1
    with pytest.raises(h.Errore):
        cs.chiudi(OGGI)
    assert cs.data_di_lavoro() == OGGI + timedelta(days=1)


def test_chiusura_bloccata_da_arrivi_non_registrati(client, soggiorno):
    nuova(soggiorno['b'], OGGI, 1, cognome='Bianchi')                       # arriva oggi, non ha fatto check-in
    avvisi = cs.controlli_chiusura(OGGI)
    assert any(t == 'arrivo' for t, _, _ in avvisi)
    with pytest.raises(h.Errore):
        cs.chiudi(OGGI)
    assert cs.chiudi(OGGI, forza=True)


def test_pagine_cassa(client, soggiorno):
    p = soggiorno['p']
    assert 'Punto cassa' in client.get('/cassa').data.decode()
    t = tok(client, '/cassa')
    r = client.post('/cassa', data={'_csrf': t, 'reparto': 'bar', 'descrizione': 'Spritz', 'quantita': '2', 'prezzo': '6', 'iva': '10',
                                     'prenotazione_id': p.id}, follow_redirects=True)
    assert 'Addebitato in camera' in r.data.decode()
    t = tok(client, f'/prenotazioni/{p.id}')
    r = client.post(f'/prenotazioni/{p.id}', data={'_csrf': t, 'azione': 'documento', 'tipo': 'ricevuta', 'conto': 'ospite'}, follow_redirects=True)
    assert 'Ricevuta R1/' in r.data.decode()
    d = Documento.query.first()
    assert 'Spritz' in client.get(f'/documenti/{d.id}').data.decode()
    assert 'Documenti emessi' in client.get('/documenti').data.decode()
    assert 'Aziende e crediti' in client.get('/aziende').data.decode()
    assert 'Chiusura della giornata' in client.get('/chiusura').data.decode()
    t = tok(client, '/aziende')
    client.post('/aziende', data={'_csrf': t, 'azione': 'salva', 'ragione_sociale': 'Beta Srl', 'partita_iva': '11111111111', 'codice_sdi': 'xyz9999'})
    assert Azienda.query.filter_by(ragione_sociale='Beta Srl').one().codice_sdi == 'XYZ9999'
