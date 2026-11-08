"""Channel manager (ARI, allotment, simulatore, iCal, parità), booking engine pubblico ed email automatiche."""
import re
from datetime import date, timedelta

import channel as ch
import comunicazioni as cm
import hotel as h
from models import Canale, CanaleTipo, EmailInviata, Prenotazione, Restrizione, Sincronizzazione, db
from test_hotel import hotel_base, nuova  # noqa: F401

OGGI = date.today()


def _canale(b, nome='Booking.com', connessione='simulatore'):
    c = Canale.query.filter_by(nome=nome).first()
    c.connessione = connessione
    ch.prepara_canale(c)
    for m in ch.mappature(c):
        m.attivo = True
    db.session.commit()
    return c


def test_ari_allotment_e_restrizioni(client, hotel_base):
    b = hotel_base
    c = _canale(b)
    m = CanaleTipo.query.filter_by(canale_id=c.id, tipo_id=b['std'].id).first()
    m.allotment = 1
    d = OGGI + timedelta(days=10)
    db.session.add(Restrizione(data=d, tipo_id=b['std'].id, min_notti=3))
    db.session.commit()
    riga = next(r for r in ch.ari(c, d, d + timedelta(days=2)) if r['tipo'].id == b['std'].id)
    assert [g['disponibili'] for g in riga['giorni']] == [1, 1]          # 2 camere libere ma allotment 1
    assert riga['giorni'][0]['min_notti'] == 3
    nuova(b, d, 3, canale='booking')                                     # venduta dal canale: allotment esaurito
    riga = next(r for r in ch.ari(c, d, d + timedelta(days=1)) if r['tipo'].id == b['std'].id)
    assert riga['giorni'][0]['disponibili'] == 0


def test_simulatore_invio_ricezione_senza_doppioni(client, hotel_base):
    c = _canale(hotel_base)
    esito = ch.invia(c)
    assert esito['inviato'] and Sincronizzazione.query.filter_by(canale_id=c.id, direzione='uscita').count() == 1
    esiti = ch.ricevi(c, quante=3, seme=4)
    db.session.commit()
    nuove = [p for p in esiti['nuova'] if p]
    assert nuove and all(p.canale_id == c.id and p.uid_esterno and p.stato == 'confermata' for p in nuove)
    # la stessa prenotazione rimandata dal portale non si duplica; la cancellazione del portale la cancella
    d = {'id': nuove[0].uid_esterno, 'stato': 'nuova', 'codice_camera': nuove[0].tipo.codice, 'arrivo': nuove[0].arrivo.isoformat(),
         'partenza': nuove[0].partenza.isoformat(), 'cognome': 'X'}
    assert ch.importa(c, d)[1] == 'doppia'
    p, esito = ch.importa(c, dict(d, stato='cancellata'))
    assert esito == 'cancellata' and p.stato == 'cancellata'


def test_camera_non_mappata_va_in_errore(client, hotel_base):
    c = _canale(hotel_base)
    p, esito = ch.importa(c, {'id': 'X1', 'codice_camera': 'NON-ESISTE', 'arrivo': (OGGI + timedelta(days=5)).isoformat(),
                              'partenza': (OGGI + timedelta(days=6)).isoformat()})
    assert p is None and esito == 'errore'
    assert Sincronizzazione.query.filter_by(esito='errore').count() == 1


def test_ical_export_e_import(client, hotel_base):
    b = hotel_base
    airbnb = Canale(nome='Airbnb', tipo='ota', commissione_pct=15, attivo=True, connessione='ical')
    db.session.add(airbnb)
    db.session.commit()
    ch.prepara_canale(airbnb)
    db.session.commit()
    d = OGGI + timedelta(days=20)
    nuova(b, d, 2, tipo='sgl', adulti=1)                                            # unica singola: piena per 2 notti
    testo = client.get(f'/ical/{airbnb.token}/{b["sgl"].id}.ics').data.decode()
    assert 'BEGIN:VCALENDAR' in testo and f'DTSTART;VALUE=DATE:{d:%Y%m%d}' in testo and f'DTEND;VALUE=DATE:{d + timedelta(days=2):%Y%m%d}' in testo
    assert client.get('/ical/sbagliato/1.ics').status_code == 404
    a1, a2 = OGGI + timedelta(days=30), OGGI + timedelta(days=33)
    ics = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:abc123@airbnb.com\r\nDTSTART;VALUE=DATE:' + f'{a1:%Y%m%d}' + '\r\nDTEND;VALUE=DATE:'
           + f'{a2:%Y%m%d}' + '\r\nSUMMARY:Reserved\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:blk@airbnb.com\r\nDTSTART;VALUE=DATE:'
           + f'{a2:%Y%m%d}' + '\r\nDTEND;VALUE=DATE:' + f'{a2 + timedelta(days=2):%Y%m%d}' + '\r\nSUMMARY:Airbnb (Not available)\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
    r = ch.ical_import(airbnb, ics, b['std'].id)
    db.session.commit()
    assert len(r['nuove']) == 1 and r['bloccate'] == 2
    p = r['nuove'][0]
    assert p.canale_id == airbnb.id and p.arrivo == a1 and p.partenza == a2 and p.uid_esterno == 'abc123@airbnb.com'
    assert ch.ical_import(airbnb, ics, b['std'].id)['doppie'] == 1          # reimport: nessun doppione


def test_parita_segnala_canale_sotto_il_sito(client, hotel_base):
    c = _canale(hotel_base)
    m = CanaleTipo.query.filter_by(canale_id=c.id, tipo_id=hotel_base['std'].id).first()
    m.ricarico_pct = -10
    db.session.commit()
    t = ch.parita(OGGI, OGGI + timedelta(days=1))
    riga = next(r for r in t if r['tipo'].id == hotel_base['std'].id)
    assert any(x['sotto_il_sito'] and x['canale'].id == c.id for x in riga['canali'])
    assert '1 prezzi sotto' in client.get('/canali/parita?giorni=1').data.decode() or 'sotto il prezzo del sito' in client.get('/canali/parita?giorni=1').data.decode()


def test_pagine_canali(client, hotel_base):
    c = _canale(hotel_base)
    assert 'Channel manager' in client.get('/canali').data.decode()
    pagina = client.get(f'/canali/{c.id}').data.decode()
    assert 'Che cosa vede il portale' in pagina
    tok = re.search(r'name="_csrf" value="([^"]+)"', pagina).group(1)
    r = client.post(f'/canali/{c.id}', data={'_csrf': tok, 'azione': 'ricevi', 'quante': 2}, follow_redirects=True)
    assert 'Prenotazioni ricevute' in r.data.decode()


def test_booking_engine_pubblico(app, hotel_base):
    c = app.test_client()                                              # senza accesso
    arr, par = OGGI + timedelta(days=15), OGGI + timedelta(days=17)
    pagina = c.get(f'/prenota/1?arrivo={arr}&partenza={par}&adulti=2').data.decode()
    assert 'Doppia' in pagina and 'Prenota' in pagina and 'Singola' not in pagina   # la singola non ospita 2 persone
    url = re.search(r'href="(/prenota/1/dati[^"]+)"', pagina).group(1).replace('&amp;', '&')
    form = c.get(url).data.decode()
    tok = re.search(r'name="_csrf" value="([^"]+)"', form).group(1)
    campi = dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form))
    campi.update(cognome='Verdi', nome='Luca', email='luca.verdi@example.com', privacy='1', _csrf=tok)
    r = c.post(url.split('?')[0], data=campi)
    assert r.status_code == 302, r.data[:300]
    p = Prenotazione.query.filter(Prenotazione.note.like('Dal booking engine%')).one()
    assert p.ospite.cognome == 'Verdi' and p.canale.tipo == 'diretto' and p.arrivo == arr
    assert p.codice in c.get(r.headers['Location']).data.decode()
    assert EmailInviata.query.filter_by(prenotazione_id=p.id, evento='conferma').count() == 1
    # senza privacy non si prenota; il robot (campo trappola) viene respinto
    campi2 = dict(campi, privacy='', email='a@b.it')
    assert 'consenso' in c.post(url.split('?')[0], data=campi2, follow_redirects=True).data.decode()
    assert c.post(url.split('?')[0], data=dict(campi, sito_web='http://spam')).status_code == 400
    h.Impostazione.scrivi('booking_attivo', '0') if hasattr(h, 'Impostazione') else None
    from models import Impostazione
    Impostazione.scrivi('booking_attivo', '0')
    db.session.commit()
    assert c.get('/prenota/1').status_code == 404


def test_email_automatiche(client, hotel_base):
    b = hotel_base
    p = nuova(b, OGGI + timedelta(days=3), 2)
    coda = cm.da_inviare()
    assert (p, 'pre_arrivo') in coda
    assert cm.invia_quelle_di_oggi() == 1
    db.session.commit()
    assert cm.da_inviare() == [] and EmailInviata.query.filter_by(evento='pre_arrivo').one().esito == 'anteprima'
    ogg, testo, _ = cm.componi(p, 'conferma')
    assert p.codice in ogg and 'Rossi' in testo
    assert 'Email agli ospiti' in client.get('/comunicazioni').data.decode()
