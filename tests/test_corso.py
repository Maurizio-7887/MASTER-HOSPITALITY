"""Il corso dall'inizio alla fine, come lo farebbe un allievo: ogni passo deve essere fattibile e verificato."""
import re
from datetime import date, timedelta

import controllo as ctl
from models import Camera, Prenotazione, TipoCamera, db

OGGI = date.today()


def _tok(c, url='/prenotazioni/nuova'):
    return re.search(r'name="_csrf" value="([^"]+)"', c.get(url).data.decode()).group(1)


def test_corso_completo(app, client):
    t = _tok(client, '/spazi')
    html = client.post('/spazi', data={'_csrf': t, 'azione': 'classe', 'corso': 'Hotel management', 'prefisso': 'al', 'numero': '1'}).data.decode()
    u, pw = re.findall(r'<td><b>(al\d+)</b></td><td><code>([^<]+)</code>', html)[0]
    s = app.test_client()
    s.post('/login', data={'username': u, 'password': pw})
    tk = _tok(s)
    stato = lambda: s.get('/api/tutor').get_json()   # noqa: E731

    def passo(atteso):
        st = stato()
        assert st['indice'] == atteso, (st['indice'], st.get('passo', {}).get('titolo'), st.get('esito'))

    def verifica(**kw):
        r = s.post('/api/tutor/verifica', json=kw).get_json()
        return r

    def post(url, **dati):
        return s.post(url, data={'_csrf': tk, **dati})

    passo(0); verifica()
    post('/struttura', sez='parametri', azione='parametri', nome_struttura='Hotel Le Logge – Anna Bianchi'); passo(2)
    post('/struttura', sez='tipo', azione='salva', codice='TRP', nome='Tripla', posti_base='2', posti_max='3', prezzo_base='175',
         supplemento_persona='35', costo_variabile='28', ordine='4'); passo(3)
    trp = re.search(r'<option value="(\d+)">Tripla</option>', s.get('/struttura?sez=camera').data.decode()).group(1)
    post('/struttura', sez='camera', azione='camere_serie', tipo_id=trp, da='401', a='402'); passo(4)
    post('/struttura', sez='piano', azione='salva', codice='LS', nome='Soggiorno lungo', variazione_pct='-10', colazione='1', prezzo_colazione='12',
         costo_colazione='4,5', rimborsabile='1', giorni_cancellazione='3', caparra_pct='0', attivo='1'); passo(5)
    d0 = OGGI + timedelta(days=30)
    post('/struttura', sez='evento', azione='salva', nome='Congresso medico', dal=d0.isoformat(), al=(d0 + timedelta(days=2)).isoformat(), impatto_pct='30'); passo(6)
    post('/struttura', sez='restrizione', azione='salva', data=d0.isoformat(), tipo_id='', min_notti='3'); passo(7)
    # prenotazioni
    form = s.get('/prenotazioni/nuova').data.decode()

    def opz(nome, inizio):
        sel = re.search(rf'<select name="{nome}"[^>]*>(.*?)</select>', form, re.S).group(1)
        return next(k for k, v in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', sel) if v.startswith(inizio))
    flex, tel = opz('piano_id', 'Flessibile'), opz('canale_id', 'Telefono')
    dv = OGGI + timedelta(days=14)
    post('/prenotazioni/nuova', arrivo=dv.isoformat(), partenza=(dv + timedelta(days=3)).isoformat(), adulti='2', bambini='1', tipo_id=trp,
         piano_id=flex, canale_id=tel, cognome='Verdi', nome='Paolo', stato='preventivo'); passo(8)
    html = s.get('/prenotazioni?stato=preventivo&q=verdi').data.decode()
    pid = re.search(r'href="/prenotazioni/(\d+)">H', html).group(1)
    post(f'/prenotazioni/{pid}', azione='conferma'); passo(9)
    std = opz('tipo_id', 'Doppia Standard')
    from hotel import preventivo
    from spazi import nello_spazio
    with nello_spazio(_spazio(app, u)):
        dg = next(OGGI + timedelta(days=k) for k in range(21, 80)
                  if preventivo(int(std), int(flex), OGGI + timedelta(days=k), OGGI + timedelta(days=k + 2))['minimo_disponibili'] >= 3)
    post('/prenotazioni/nuova', arrivo=dg.isoformat(), partenza=(dg + timedelta(days=2)).isoformat(), adulti='2', tipo_id=std, piano_id=flex,
         canale_id=tel, cognome='Coro di Siena', gruppo='Coro', quante='3'); passo(10)
    pl = s.get(f'/api/planner?da={dv.isoformat()}').get_json()
    c402 = next(c['id'] for c in pl['camere'] if c['numero'] == '402')
    verdi = next(p for p in pl['prenotazioni'] if p['id'] == int(pid))
    if verdi['camera_id'] != c402:
        assert s.post(f'/api/prenotazioni/{pid}/sposta', json={'camera_id': c402}).get_json()['ok']
    passo(11)
    # front office
    oggi_html = s.get('/oggi').data.decode()
    arrivo = re.search(r'href="/prenotazioni/(\d+)#alloggiati">Check-in', oggi_html).group(1)
    sch = s.get(f'/prenotazioni/{arrivo}').data.decode()
    tot, persone = map(int, re.search(r'Alloggiati \((\d+) di (\d+)\)', sch).groups())
    post(f'/prenotazioni/{arrivo}', azione='alloggiato_da_ospite')
    for i in range(persone - 1):
        post(f'/prenotazioni/{arrivo}', azione='alloggiato', cognome='Ospite', nome=f'N{i}', tipo='19')
    if 'da assegnare' in sch:
        post(f'/prenotazioni/{arrivo}', azione='camera')
    post(f'/prenotazioni/{arrivo}', azione='check_in'); passo(12)
    post(f'/prenotazioni/{arrivo}', azione='addebito', reparto='ristorante', descrizione='Cena', quantita='2', prezzo='35', iva='10', costo='22'); passo(13)
    partenza = re.search(r'href="/prenotazioni/(\d+)#conto">Conto e check-out', s.get('/oggi').data.decode()).group(1)
    saldo = re.search(r'name="importo" value="([\d.]+)"', s.get(f'/prenotazioni/{partenza}').data.decode())
    if saldo:
        post(f'/prenotazioni/{partenza}', azione='pagamento', importo=saldo.group(1), metodo='carta')
    post(f'/prenotazioni/{partenza}', azione='check_out'); passo(14)
    hk = s.get('/housekeeping').data.decode()
    num = re.search(r'Camera (\d+)', s.get(f'/prenotazioni/{partenza}/conto').data.decode()).group(1)
    cid = re.search(rf'id="cam-(\d+)">\s*<div class="bar" style="margin:0"><span class="num">{num}<', hk).group(1)
    assert s.post(f'/api/camere/{cid}/stato', json={'stato': 'pulita'}).get_json()['ok']; passo(15)
    fut = re.findall(r'href="/prenotazioni/(\d+)">H', s.get(f'/prenotazioni?stato=confermata&da={(OGGI + timedelta(days=5)).isoformat()}').data.decode())
    post(f'/prenotazioni/{fut[-1]}', azione='cancella'); passo(16)
    # DynaPrice
    verifica(); passo(17)
    g = s.get('/revenue').data.decode()
    tipi = sorted(set(re.findall(r'data-tipo="(\d+)"', g)))
    prezzi = [{'tipo_id': int(ti), 'data': (OGGI + timedelta(days=k)).isoformat(), 'prezzo': 150} for k in range(14) for ti in tipi]
    assert s.post('/api/revenue/applica', json={'prezzi': prezzi}).get_json()['ok']; passo(18)
    s.post('/revenue/simula', data={'_csrf': tk, 'giorni': '7'}); passo(19)
    verifica(); passo(20)
    verifica(); passo(21)
    # domande di controllo di gestione: prima sbagliata, poi giusta
    assert verifica(risposta='1')['indice'] == 21
    with nello_spazio(_spazio(app, u)):
        primo = OGGI.replace(day=1)
        r = ctl.analisi((primo - timedelta(days=1)).replace(day=1), primo)
        revpar = r['kpi']['revpar']
        peggiore = min(r['canali'].items(), key=lambda kv: kv[1]['margine_notte'])[0]
    assert verifica(risposta=f'{revpar:.2f}'.replace('.', ','))['indice'] == 22
    fine = verifica(risposta=peggiore)
    assert fine['finito'] and fine['indice'] == fine['totale']


def _spazio(app, username):
    from models import Utente
    return Utente.query.filter_by(username=username).first().spazio_id
