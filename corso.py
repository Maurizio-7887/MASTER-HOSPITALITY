"""Corso di hotel management con tutor laterale.

Ogni allievo ha il suo spazio con l'Hotel Le Logge già avviato (camere, listini, tre mesi di storico e le prenotazioni
future). Il tutor propone un passo alla volta, dice in quale pagina andare e verifica da solo sui dati se il passo è
fatto. Le domande di controllo di gestione si verificano sul valore calcolato dal programma nello spazio dell'allievo.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request

from models import (Addebito, Camera, Evento, Impostazione, PianoTariffario, Prenotazione, Restrizione, TariffaGiorno,
                    TipoCamera, db)

bp = Blueprint('corso', __name__)


def _oggi():
    return date.today()


def _int(k, d=0):
    try:
        return int(Impostazione.leggi(k) or d)
    except ValueError:
        return d


def _cancellate_oggi():
    inizio = datetime.combine(_oggi(), datetime.min.time())
    return Prenotazione.query.filter(Prenotazione.stato == 'cancellata', Prenotazione.cancellata_il >= inizio).count()


def carica_base_corso():
    """Hotel Le Logge con storico e prenotazioni future, più il punto di partenza del tutor."""
    from seed_demo import carica_demo
    carica_demo()
    Impostazione.scrivi('corso', '1')
    Impostazione.scrivi('tutor_passo', '0')
    Impostazione.scrivi('tutor_max', '0')
    Impostazione.scrivi('corso_canc_base', str(_cancellate_oggi()))
    Impostazione.scrivi('corso_simulazioni', '0')
    db.session.commit()


# ---------------- verifiche ----------------

def _ok(cond, msg):
    return (True, '') if cond else (False, msg)


def _tipo(cod):
    return TipoCamera.query.filter(db.func.upper(TipoCamera.codice) == cod).first()


def _verdi():
    from models import Ospite
    return (Prenotazione.query.join(Ospite, Ospite.id == Prenotazione.ospite_id).filter(db.func.lower(Ospite.cognome) == 'verdi')
            .order_by(Prenotazione.id.desc()).first())


def v_nome():
    from seed_demo import NOME_DEMO
    from hotel import par
    n = par('nome_struttura')
    return _ok(n and n != NOME_DEMO and 'demo' not in n.lower(), 'Il nome della struttura è ancora quello della demo: cambialo in Struttura › Parametri.')


def v_tripla():
    t = _tipo('TRP')
    if not t:
        return False, 'Non trovo la tipologia con codice TRP.'
    return _ok(t.posti_max >= 3 and abs(t.prezzo_base - 175) < 0.01 and abs((t.costo_variabile or 0) - 28) < 0.01,
               f'Controlla la Tripla: persone max {t.posti_max} (servono 3), listino {t.prezzo_base:.0f} € (175), costo camera {t.costo_variabile or 0:.0f} € (28).')


def v_camere_tripla():
    t = _tipo('TRP')
    nums = {c.numero for c in Camera.query.filter_by(tipo_id=t.id).all()} if t else set()
    return _ok({'401', '402'} <= nums, 'Servono le camere 401 e 402 di tipologia Tripla.' + (f' Ora ci sono: {", ".join(sorted(nums))}.' if nums else ''))


def v_piano():
    p = PianoTariffario.query.filter(db.func.upper(PianoTariffario.codice) == 'LS').first()
    if not p:
        return False, 'Non trovo il piano tariffario con codice LS.'
    return _ok(p.variazione_pct <= -10 and p.rimborsabile and p.colazione, 'Il piano LS deve avere variazione −10%, colazione inclusa ed essere rimborsabile.')


def v_evento():
    e = Evento.query.filter(Evento.nome.ilike('%congresso%'), Evento.dal >= _oggi()).first()
    if not e:
        return False, 'Non trovo un evento futuro con «Congresso» nel nome.'
    return _ok((e.impatto_pct or 0) >= 30, f'L\'effetto sulla domanda del congresso è {e.impatto_pct or 0:.0f}%: deve essere almeno 30%.')


def v_restrizione():
    e = Evento.query.filter(Evento.nome.ilike('%congresso%'), Evento.dal >= _oggi()).first()
    if not e:
        return False, 'Prima crea l\'evento del congresso.'
    r = Restrizione.query.filter(Restrizione.data >= e.dal, Restrizione.data <= e.al, Restrizione.min_notti >= 3).first()
    return _ok(r, f'Serve una restrizione di soggiorno minimo 3 notti in una data del congresso ({e.dal.strftime("%d/%m")}–{e.al.strftime("%d/%m")}).')


def v_preventivo():
    p = _verdi()
    if not p:
        return False, 'Non trovo una prenotazione intestata a Verdi.'
    t = _tipo('TRP')
    return _ok(t and p.tipo_id == t.id and p.adulti == 2 and p.bambini == 1 and p.notti == 3,
               'Il preventivo Verdi deve essere in Tripla, per 2 adulti e 1 bambino, 3 notti.')


def v_conferma_verdi():
    p = _verdi()
    return _ok(p and p.stato == 'confermata' and p.camera_id, 'Conferma il preventivo Verdi dalla sua scheda: deve risultare confermato con una camera assegnata.')


def v_gruppo():
    n = Prenotazione.query.filter(Prenotazione.gruppo.ilike('%coro%'), Prenotazione.stato.in_(('opzione', 'confermata'))).count()
    return _ok(n >= 3, f'Prenotazioni del gruppo «Coro» trovate: {n} su 3.')


def v_sposta_verdi():
    p = _verdi()
    return _ok(p and p.camera and p.camera.numero == '402', 'La prenotazione Verdi deve stare nella camera 402: trascinala nel planner o cambiala dalla scheda.')


def v_checkin():
    inizio = datetime.combine(_oggi(), datetime.min.time())
    n = Prenotazione.query.filter(Prenotazione.stato.in_(('in_casa', 'partita')), Prenotazione.checkin_il >= inizio).count()
    return _ok(n >= 1, 'Nessun check-in fatto oggi: apri un arrivo da «Oggi», registra gli alloggiati e premi Check-in.')


def v_cena():
    return _ok(Addebito.query.filter(Addebito.reparto == 'ristorante').first(), 'Addebita una «Cena» (reparto Ristorante) sul conto di un ospite in casa.')


def _partiti_oggi():
    inizio = datetime.combine(_oggi(), datetime.min.time())
    return Prenotazione.query.filter(Prenotazione.stato == 'partita', Prenotazione.checkout_il >= inizio).all()


def v_checkout():
    return _ok(_partiti_oggi(), 'Nessun check-out fatto oggi: da «Oggi» apri una partenza, incassa il saldo e premi Check-out.')


def v_pulita():
    camere = [p.camera for p in _partiti_oggi() if p.camera]
    return _ok(any(c.stato in ('pulita', 'ispezionata') for c in camere),
               'La camera dell\'ospite partito risulta ancora da rifare: segnala pulita da Housekeeping.' if camere else 'Prima fai un check-out.')


def v_cancella():
    return _ok(_cancellate_oggi() > _int('corso_canc_base'), 'Cancella una prenotazione confermata futura dalla sua scheda (guarda la penale proposta).')


def v_dynaprice():
    oggi = _oggi()
    n = TariffaGiorno.query.filter(TariffaGiorno.origine == 'dynaprice', TariffaGiorno.data >= oggi,
                                   TariffaGiorno.data < oggi + timedelta(days=14)).count()
    return _ok(n >= 14, f'Prezzi DynaPrice applicati nei prossimi 14 giorni: {n}. Seleziona i giorni e premi «Applica i suggeriti».')


def v_simula():
    return _ok(_int('corso_simulazioni') >= 1, 'Premi «Simula le vendite» in DynaPrice.')


def _mese_scorso():
    from controllo import analisi
    primo = _oggi().replace(day=1)
    da = (primo - timedelta(days=1)).replace(day=1)
    return analisi(da, primo)


def d_revpar(risposta):
    r = _mese_scorso()['kpi']
    try:
        v = float(str(risposta).replace('€', '').replace(',', '.').strip())
    except ValueError:
        return False, 'Scrivi un numero, per esempio 98,50.'
    giusto = r['revpar'] or 0
    return _ok(abs(v - giusto) <= 1, 'Non è il valore giusto: in Controllo scegli il mese scorso e leggi il RevPAR (ricavi camere netti diviso camere disponibili).')


def d_canale(risposta):
    canali = _mese_scorso()['canali']
    peggiore = min(canali.items(), key=lambda kv: kv[1]['margine_notte'] if kv[1]['margine_notte'] is not None else 1e9)[0] if canali else ''
    t = (risposta or '').strip().lower()
    return _ok(t and (t in peggiore.lower() or peggiore.lower().split(' ')[0] in t),
               'Non è quello: in Controllo, mese scorso, guarda la colonna «Margine per notte» della tabella dei canali.')


LEZIONI = [
    {'titolo': '1 · La struttura', 'passi': [
        {'titolo': 'Benvenuto all\'Hotel Le Logge', 'info': True, 'dove': '/oggi',
         'testo': '<p>Da oggi lavori all\'<b>Hotel Le Logge</b>, 3 stelle a Firenze, 24 camere. In <b>Oggi</b> trovi arrivi, partenze, camere da rifare e l\'occupazione dei prossimi 14 giorni.</p><p>Il tutor ti guida un passo alla volta e controlla da solo il tuo lavoro.</p>'},
        {'titolo': 'Dai il tuo nome all\'hotel', 'verifica': v_nome, 'dove': '/struttura?sez=parametri',
         'testo': '<p>In <b>Struttura › Parametri</b> cambia il nome della struttura, per esempio «Hotel Le Logge – Mario Rossi», e salva.</p><p class="tu-mute">Nella stessa pagina ci sono IVA, imposta di soggiorno e margine minimo di DynaPrice.</p>'},
        {'titolo': 'Crea la tipologia Tripla', 'verifica': v_tripla, 'dove': '/struttura?sez=tipo',
         'testo': '<p>In <b>Struttura › Tipologie</b> aggiungi: codice <b>TRP</b>, nome «Tripla», 2 persone nel prezzo, <b>3</b> persone max, listino <b>175 €</b>, supplemento 35 €, costo camera venduta <b>28 €</b>.</p><p class="tu-mute">Il costo della camera venduta (pulizia, biancheria, cortesie, energia) serve a DynaPrice per non scendere sotto il margine.</p>'},
        {'titolo': 'Crea le camere 401 e 402', 'verifica': v_camere_tripla, 'dove': '/struttura?sez=camera',
         'testo': '<p>In <b>Struttura › Camere</b> usa «Crea una serie»: tipologia Tripla, dal numero <b>401</b> al <b>402</b>.</p>'},
    ]},
    {'titolo': '2 · Tariffe e regole di vendita', 'passi': [
        {'titolo': 'Un piano per i soggiorni lunghi', 'verifica': v_piano, 'dove': '/struttura?sez=piano',
         'testo': '<p>In <b>Struttura › Piani tariffari</b> crea il piano <b>LS</b> «Soggiorno lungo»: variazione <b>−10%</b>, colazione inclusa (12 €, costo 4,5 €), rimborsabile, cancellazione gratuita fino a 3 giorni prima.</p>'},
        {'titolo': 'Segna un evento in città', 'verifica': v_evento, 'dove': '/struttura?sez=evento',
         'testo': '<p>Fra un mese c\'è un congresso medico. In <b>Struttura › Eventi</b> aggiungi «Congresso medico» su 3 giorni a partire da fra un mese, effetto sulla domanda <b>30%</b>.</p><p class="tu-mute">DynaPrice userà l\'evento nella previsione dell\'occupazione.</p>'},
        {'titolo': 'Soggiorno minimo nei giorni del congresso', 'verifica': v_restrizione, 'dove': '/struttura?sez=restrizione',
         'testo': '<p>Proteggi le notti di picco: in <b>Struttura › Restrizioni</b> metti <b>soggiorno minimo 3 notti</b> sul primo giorno del congresso.</p><p class="tu-mute">Così chi arriva quel giorno occupa anche le notti di contorno, che altrimenti resterebbero vuote.</p>'},
    ]},
    {'titolo': '3 · Prenotazioni e planner', 'passi': [
        {'titolo': 'Preventivo per la famiglia Verdi', 'verifica': v_preventivo, 'dove': '/prenotazioni/nuova',
         'testo': '<p>La famiglia <b>Verdi</b> (2 adulti e 1 bambino) chiede 3 notti fra due settimane. In <b>Nuova prenotazione</b> scegli Tripla, piano Flessibile, canale Telefono, stato <b>Preventivo</b>.</p><p class="tu-mute">Guarda il dettaglio notte per notte e le alternative delle altre tipologie.</p>'},
        {'titolo': 'Conferma il preventivo', 'verifica': v_conferma_verdi, 'dove': '/prenotazioni?stato=preventivo',
         'testo': '<p>I Verdi accettano. Apri la loro scheda e premi <b>Conferma</b>: il programma assegna da solo una camera libera.</p>'},
        {'titolo': 'Un gruppo di tre camere', 'verifica': v_gruppo, 'dove': '/prenotazioni/nuova',
         'testo': '<p>Il <b>Coro di Siena</b> chiede 3 Doppie Standard per 2 notti fra tre settimane. Nuova prenotazione: intestatario «Coro di Siena», nome gruppo «Coro», <b>numero camere 3</b>.</p>'},
        {'titolo': 'Sposta i Verdi nella camera 402', 'verifica': v_sposta_verdi, 'dove': '/planner',
         'testo': '<p>I Verdi chiedono la camera più silenziosa, la <b>402</b>. Nel <b>Planner</b> trascina la loro barra sulla riga 402 (oppure cambia camera dalla scheda).</p>'},
    ]},
    {'titolo': '4 · Front office', 'passi': [
        {'titolo': 'Check-in di un arrivo di oggi', 'verifica': v_checkin, 'dove': '/oggi',
         'testo': '<p>Da <b>Oggi</b> apri un arrivo. Nella scheda registra gli <b>alloggiati</b> (uno per persona, con documento per il capofamiglia), poi premi <b>Check-in</b>.</p><p class="tu-mute">I dati servono per la comunicazione in Questura (Alloggiati Web) e per l\'imposta di soggiorno.</p>'},
        {'titolo': 'Addebita una cena', 'verifica': v_cena, 'dove': '/prenotazioni?stato=in_casa',
         'testo': '<p>Un ospite in casa ha cenato nel ristorante convenzionato con addebito in camera: nel <b>conto</b> aggiungi «Cena», reparto <b>Ristorante</b>, 2 × 35 €, costo 22 €.</p>'},
        {'titolo': 'Check-out con saldo', 'verifica': v_checkout, 'dove': '/oggi',
         'testo': '<p>Da <b>Oggi › Partenze</b> apri un ospite, controlla il conto (notti, extra, imposta di soggiorno), incassa il saldo e premi <b>Check-out</b>. Stampa il riepilogo del conto.</p>'},
        {'titolo': 'Camera pronta', 'verifica': v_pulita, 'dove': '/housekeeping',
         'testo': '<p>Dopo il check-out la camera passa «da rifare». In <b>Housekeeping</b> segnala <b>pulita</b> la camera appena liberata.</p>'},
        {'titolo': 'Una cancellazione', 'verifica': v_cancella, 'dove': '/prenotazioni?stato=confermata',
         'testo': '<p>Un ospite cancella. Apri una prenotazione confermata futura e premi <b>Cancella</b>: il programma propone la penale secondo il piano tariffario (zero se in tempo, prima notte o intero soggiorno se non rimborsabile).</p>'},
    ]},
    {'titolo': '5 · DynaPrice', 'passi': [
        {'titolo': 'Leggere il calendario dei prezzi', 'info': True, 'dove': '/revenue',
         'testo': '<p>Per ogni notte: camere vendute (<b>OTB</b>), <b>pickup</b> degli ultimi 7 giorni, <b>previsione</b> dell\'occupazione finale e, per tipologia, prezzo in vigore e <b>suggerito</b>.</p><p>Il suggerito sale con l\'occupazione prevista e non scende mai sotto la <b>soglia</b>: il prezzo che, tolti IVA, commissione del canale più caro e costo della camera, lascia il margine minimo.</p>'},
        {'titolo': 'Applica i prezzi suggeriti', 'verifica': v_dynaprice, 'dove': '/revenue',
         'testo': '<p>In <b>DynaPrice</b> seleziona i prossimi 14 giorni (casella in testa alla colonna) e premi <b>Applica i suggeriti</b>.</p>'},
        {'titolo': 'Simula una settimana di vendite', 'verifica': v_simula, 'dove': '/revenue',
         'testo': '<p>Premi <b>Simula le vendite</b> (7 giorni): arrivano richieste da portali, sito e telefono. Con prezzi troppo alti se ne perdono; con prezzi bassi si riempie prima, ma si incassa meno.</p><p class="tu-mute">Confronta occupazione e ADR prima e dopo.</p>'},
        {'titolo': 'Chi rischia di cancellare', 'info': True, 'dove': '/revenue/rischio',
         'testo': '<p>In <b>Rischio cancellazioni</b> ogni prenotazione ha una probabilità e i motivi (anticipo, canale, piano, caparra, ospite abituale). La somma per notte suggerisce quante camere vendere in <b>overbooking</b>.</p>'},
    ]},
    {'titolo': '6 · Controllo di gestione', 'passi': [
        {'titolo': 'Gli indicatori del mese', 'info': True, 'dove': '/controllo',
         'testo': '<p>In <b>Controllo</b>: occupazione, <b>ADR</b> (prezzo medio per camera venduta), <b>RevPAR</b> (ricavo per camera disponibile), <b>GOP</b> e <b>GOPPAR</b>, conto economico per reparti (USALI) e margine per canale.</p>'},
        {'titolo': 'Domanda: il RevPAR del mese scorso', 'domanda': d_revpar, 'dove': '/controllo',
         'testo': '<p>In <b>Controllo</b> scegli il <b>mese scorso</b>. Quanto vale il RevPAR? Scrivilo qui sotto in euro.</p>'},
        {'titolo': 'Domanda: il canale meno conveniente', 'domanda': d_canale, 'dove': '/controllo',
         'testo': '<p>Sempre sul mese scorso: quale canale lascia il <b>margine per notte più basso</b>? Scrivi il nome.</p><p class="tu-mute">Non sempre è quello con la commissione più alta: contano anche prezzo medio, durata e piano tariffario.</p>'},
    ]},
]
PASSI = [(i, p) for i, l in enumerate(LEZIONI) for p in l['passi']]


def stato(esito=None):
    i = min(_int('tutor_passo'), len(PASSI))
    mx = max(_int('tutor_max'), i)
    out = {'indice': i, 'massimo': mx, 'totale': len(PASSI), 'finito': i >= len(PASSI), 'esito': esito,
           'lezioni': [{'titolo': l['titolo'], 'passi': [p['titolo'] for p in l['passi']]} for l in LEZIONI]}
    if i < len(PASSI):
        lez, p = PASSI[i]
        out['passo'] = {'lezione': lez, 'titolo': p['titolo'], 'testo': p['testo'], 'dove': p.get('dove'), 'info': bool(p.get('info')),
                        'domanda': bool(p.get('domanda'))}
    return out


def _avanza(i):
    Impostazione.scrivi('tutor_passo', str(i + 1))
    Impostazione.scrivi('tutor_max', str(max(_int('tutor_max'), i + 1)))
    db.session.commit()


def attivo():
    return Impostazione.leggi('tutor_passo') is not None


def controlla_in_silenzio():
    """Dopo un'operazione: se il passo corrente è verificabile e risulta fatto, si va avanti (anche più passi)."""
    if not attivo():
        return
    for _ in range(3):
        i = _int('tutor_passo')
        if i >= len(PASSI):
            return
        p = PASSI[i][1]
        if not p.get('verifica'):
            return
        ok, _ = p['verifica']()
        if not ok:
            return
        _avanza(i)


@bp.route('/api/tutor')
def api_tutor():
    if not attivo():
        return jsonify({'ok': False, 'error': 'Il corso non è attivo in questo spazio'}), 404
    return jsonify({'ok': True, **stato()})


@bp.route('/api/tutor/verifica', methods=['POST'])
def api_verifica():
    if not attivo():
        return jsonify({'ok': False, 'error': 'Il corso non è attivo in questo spazio'}), 404
    i = _int('tutor_passo')
    if i >= len(PASSI):
        return jsonify({'ok': True, **stato()})
    p = PASSI[i][1]
    if p.get('info'):
        ok, msg = True, ''
    elif p.get('domanda'):
        ok, msg = p['domanda']((request.get_json(silent=True) or {}).get('risposta'))
    else:
        ok, msg = p['verifica']()
    if ok:
        _avanza(i)
        controlla_in_silenzio()
    return jsonify({'ok': True, **stato({'riuscito': ok, 'messaggio': msg})})


@bp.route('/api/tutor/vai', methods=['POST'])
def api_vai():
    n = (request.get_json(silent=True) or {}).get('indice')
    if not attivo() or not isinstance(n, int) or n < 0 or n > _int('tutor_max'):
        return jsonify({'ok': False, 'error': 'Passo non raggiungibile'}), 400
    Impostazione.scrivi('tutor_passo', str(n))
    db.session.commit()
    return jsonify({'ok': True, **stato()})


@bp.route('/api/tutor/ricomincia', methods=['POST'])
def api_ricomincia():
    """Riporta l'hotel dell'allievo allo stato iniziale e il tutor al primo passo."""
    from seed_demo import svuota
    svuota()
    Impostazione.scrivi('demo_eliminati', '')
    db.session.commit()
    carica_base_corso()
    return jsonify({'ok': True, **stato()})


@bp.route('/api/tutor/attiva', methods=['POST'])
def api_attiva():
    """Il docente (o chi vuole esercitarsi) attiva il tutor nello spazio corrente senza ricaricare i dati."""
    from flask import g
    if g.utente.ruolo != 'admin':
        return jsonify({'ok': False, 'error': 'Solo l\'amministratore'}), 403
    if not attivo():
        Impostazione.scrivi('tutor_passo', '0')
        Impostazione.scrivi('tutor_max', '0')
        Impostazione.scrivi('corso_canc_base', str(_cancellate_oggi()))
        Impostazione.scrivi('corso_simulazioni', '0')
        db.session.commit()
    return jsonify({'ok': True, **stato()})
