"""Hotel dimostrativo «Hotel Le Logge» (Firenze, 3 stelle, 24 camere): struttura, listini, stagioni, eventi, costi e
circa cinque mesi di prenotazioni simulate (tre mesi di storico e la raccolta delle prenotazioni future).

Tutti i nomi di ospiti e i dati sono di fantasia. Le prenotazioni si generano con una curva di domanda realistica
(stagionalità, fine settimana, eventi, anticipo di prenotazione diverso per canale, cancellazioni più frequenti sui
portali), così DynaPrice e il controllo di gestione hanno numeri credibili su cui lavorare.
"""
import random
from datetime import date, datetime, time, timedelta

from werkzeug.security import generate_password_hash

from models import (Addebito, Alloggiato, Camera, Canale, CostoFisso, Evento, Impostazione, NotteVenduta, Ospite, Pagamento,
                    PianoTariffario, Prenotazione, Restrizione, Stagione, TariffaGiorno, TipoCamera, Utente, db)

NOME_DEMO = 'Hotel Le Logge (demo)'

PIANI_BASE = [  # codice, nome, variazione, colazione, prezzo colazione, costo colazione, rimborsabile, giorni canc., caparra %
    ('FLEX', 'Flessibile con colazione', 0, True, 12, 4.5, True, 2, 0),
    ('NR', 'Non rimborsabile con colazione', -12, True, 12, 4.5, False, 0, 100),
    ('RO', 'Solo pernottamento', 0, False, 0, 0, True, 2, 0),
]
CANALI_BASE = [  # nome, tipo, commissione, colore
    ('Diretto (sito e booking engine)', 'diretto', 3, '#2b8a3e'),
    ('Telefono ed email', 'diretto', 0, '#5c940d'),
    ('Booking.com', 'ota', 18, '#1971c2'),
    ('Expedia', 'ota', 20, '#e67700'),
]


def pasqua(anno):
    a, b, c = anno % 19, anno // 100, anno % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mese = (h + m - 7 * n + 114) // 31
    giorno = (h + m - 7 * n + 114) % 31 + 1
    return date(anno, mese, giorno)


def struttura_iniziale(nome='', completa=False):
    """Base per un cliente nuovo: parametri, piani tariffari e canali. Camere e tipologie le inserisce lui.
    Con `completa` aggiunge solo i piani e i canali di base che mancano (serve alla demo)."""
    if PianoTariffario.query.first() and not completa:
        return
    if nome:
        Impostazione.scrivi('nome_struttura', nome)
    codici = {p.codice for p in PianoTariffario.query.all()}
    for cod, n, var, col, pc, cc, rimb, gc, cap in PIANI_BASE:
        if cod not in codici:
            db.session.add(PianoTariffario(codice=cod, nome=n, variazione_pct=var, colazione=col, prezzo_colazione=pc, costo_colazione=cc,
                                           rimborsabile=rimb, giorni_cancellazione=gc, caparra_pct=cap))
    nomi = {c.nome for c in Canale.query.all()}
    for n, t, c, col in CANALI_BASE:
        if n not in nomi:
            db.session.add(Canale(nome=n, tipo=t, commissione_pct=c, colore=col))
    db.session.commit()


def _stagioni(anno):
    return [('Bassa inverno', date(anno, 1, 7), date(anno, 2, 28), 0.72), ('Media primavera', date(anno, 3, 1), date(anno, 3, 31), 0.95),
            ('Alta primavera', date(anno, 4, 1), date(anno, 6, 30), 1.25), ('Estate', date(anno, 7, 1), date(anno, 8, 31), 1.05),
            ('Alta autunno', date(anno, 9, 1), date(anno, 10, 31), 1.22), ('Bassa autunno', date(anno, 11, 1), date(anno, 12, 20), 0.80),
            ('Festività', date(anno, 12, 21), date(anno, 12, 31), 1.10), ('Festività', date(anno, 1, 1), date(anno, 1, 6), 1.10)]


def _nel_mese(anno, mese, giorno_sett, n):
    """n-esimo giorno della settimana (0 = lunedì) del mese; n = -1 l'ultimo."""
    if n > 0:
        d = date(anno, mese, 1)
        d += timedelta(days=(giorno_sett - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = (date(anno, mese, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - giorno_sett) % 7)


def _eventi(anno):
    p = pasqua(anno)
    pitti_g = _nel_mese(anno, 1, 1, 2)   # secondo martedì di gennaio (indicativo)
    pitti_u = _nel_mese(anno, 6, 1, 2) + timedelta(days=7)
    maratona = _nel_mese(anno, 11, 6, -1)
    return [('Pitti Uomo (inverno)', pitti_g, pitti_g + timedelta(days=3), 60), ('Pitti Bimbo', pitti_g + timedelta(days=9), pitti_g + timedelta(days=11), 25),
            ('Taste', _nel_mese(anno, 3, 5, 1), _nel_mese(anno, 3, 5, 1) + timedelta(days=2), 20),
            ('Pasqua e Scoppio del Carro', p - timedelta(days=2), p + timedelta(days=1), 35),
            ('Ponte del 25 aprile e 1° maggio', date(anno, 4, 25), date(anno, 5, 1), 20),
            ('Pitti Uomo (estate)', pitti_u, pitti_u + timedelta(days=3), 55),
            ('San Giovanni e Calcio Storico', date(anno, 6, 23), date(anno, 6, 24), 25),
            ('Firenze Marathon', maratona - timedelta(days=1), maratona, 40),
            ('Capodanno', date(anno, 12, 30), date(anno, 12, 31), 40)]


NOMI = {'IT': (['Rossi', 'Bianchi', 'Ferrari', 'Esposito', 'Romano', 'Colombo', 'Ricci', 'Marino', 'Greco', 'Bruno', 'Gallo', 'Conti', 'Costa', 'Giordano', 'Mancini', 'Lombardi'],
               ['Marco', 'Giulia', 'Luca', 'Francesca', 'Alessandro', 'Chiara', 'Andrea', 'Sara', 'Matteo', 'Elena', 'Paolo', 'Laura'], 'Italia', 'it'),
        'DE': (['Müller', 'Schmidt', 'Schneider', 'Fischer', 'Weber', 'Wagner'], ['Lukas', 'Anna', 'Felix', 'Lena', 'Jonas', 'Mia'], 'Germania', 'de'),
        'US': (['Smith', 'Johnson', 'Williams', 'Brown', 'Miller', 'Davis', 'Wilson'], ['James', 'Emily', 'Michael', 'Olivia', 'David', 'Sophia'], 'Stati Uniti', 'en'),
        'FR': (['Martin', 'Bernard', 'Dubois', 'Laurent', 'Moreau'], ['Louis', 'Camille', 'Hugo', 'Léa', 'Jules'], 'Francia', 'fr'),
        'GB': (['Taylor', 'Evans', 'Thomas', 'Roberts', 'Walker'], ['Oliver', 'Amelia', 'Harry', 'Isla', 'George'], 'Regno Unito', 'en'),
        'ES': (['García', 'Fernández', 'López', 'Martínez'], ['Pablo', 'Lucía', 'Javier', 'Carmen'], 'Spagna', 'es'),
        'JP': (['Sato', 'Suzuki', 'Takahashi', 'Tanaka'], ['Haruto', 'Yui', 'Sota', 'Hina'], 'Giappone', 'en')}
PESI_PAESI = [('IT', 30), ('US', 20), ('DE', 12), ('FR', 10), ('GB', 10), ('ES', 9), ('JP', 9)]


def _scegli(rnd, pesi):
    return rnd.choices([k for k, _ in pesi], weights=[w for _, w in pesi])[0]


def carica_demo(oggi=None, seme=7):
    """Carica l'hotel dimostrativo nello spazio corrente, se l'archivio è vuoto (una volta sola)."""
    if db.engine.dialect.name == 'postgresql':   # due processi all'avvio: uno solo carica
        from sqlalchemy import text
        db.session.execute(text('SELECT pg_advisory_xact_lock(424242)'))
    if TipoCamera.query.first() or Prenotazione.query.first() or Impostazione.leggi('demo_eliminati'):
        db.session.commit()
        return False
    oggi = oggi or date.today()
    rnd = random.Random(seme)
    for k, v in [('nome_struttura', NOME_DEMO), ('categoria', '3 stelle'), ('comune', 'Firenze'), ('tassa_importo', '6'),
                 ('tassa_max_notti', '7'), ('tassa_eta_esenti', '11'), ('margine_minimo', '25'), ('demo', '1')]:
        Impostazione.scrivi(k, v)
    struttura_iniziale(completa=True)
    piani = {p.codice: p for p in PianoTariffario.query.all()}
    if not Canale.query.filter_by(nome='Agenzie e tour operator').first():
        db.session.add(Canale(nome='Agenzie e tour operator', tipo='agenzia', commissione_pct=22, colore='#862e9c'))
    tipi = {}
    for i, (cod, nome, pb, pm, prezzo, supp, costo, desc) in enumerate([
            ('STD', 'Doppia Standard', 2, 3, 135, 35, 24, 'Letto matrimoniale o due letti, vista cortile'),
            ('SUP', 'Doppia Superior', 2, 3, 165, 35, 27, 'Più ampia, vista sui tetti, macchina del caffè'),
            ('SGL', 'Singola', 1, 1, 95, 0, 18, 'Letto alla francese, ideale per chi viaggia per lavoro'),
            ('JS', 'Junior Suite', 2, 4, 220, 40, 34, 'Zona giorno con divano letto, vasca, vista Duomo')]):
        t = TipoCamera(codice=cod, nome=nome, posti_base=pb, posti_max=pm, prezzo_base=prezzo, supplemento_persona=supp,
                       costo_variabile=costo, descrizione=desc, ordine=i)
        db.session.add(t)
        tipi[cod] = t
    db.session.flush()
    numeri = {'STD': ['101', '102', '103', '104', '105', '106', '107', '108', '201', '202'], 'SUP': ['203', '204', '205', '206', '207', '208'],
              'SGL': ['301', '302', '303', '304'], 'JS': ['305', '306', '307', '308']}
    camere = {}
    for cod, nn in numeri.items():
        for n in nn:
            c = Camera(numero=n, tipo_id=tipi[cod].id, piano=n[0], stato='pulita')
            db.session.add(c)
            camere.setdefault(cod, []).append(c)
    for anno in (oggi.year - 1, oggi.year, oggi.year + 1):
        for nome, dal, al, k in _stagioni(anno):
            db.session.add(Stagione(nome=nome, dal=dal, al=al, coefficiente=k))
        for nome, dal, al, imp in _eventi(anno):
            db.session.add(Evento(nome=nome, dal=dal, al=al, impatto_pct=imp, note='Date indicative: verificale ogni anno'))
    for voce, rep, imp in [('Reception: personale (2,5 addetti)', 'camere', 7800), ('Governante e piani: parte fissa', 'camere', 3200),
                           ('Sala colazioni: personale', 'fb', 2600), ('Direzione e amministrazione', 'amministrazione', 3500),
                           ('Commercialista, software e banca', 'amministrazione', 650), ('Sito, foto, pubblicità online', 'commerciale', 900),
                           ('Manutenzioni ordinarie', 'manutenzione', 1300), ('Energia, gas e acqua: parte fissa', 'utenze', 3800),
                           ('Affitto dell\'immobile', 'oneri', 14000), ('Assicurazioni', 'oneri', 600)]:
        db.session.add(CostoFisso(voce=voce, reparto=rep, importo_mensile=imp))
    db.session.flush()

    stagioni = Stagione.query.all()
    eventi = Evento.query.all()
    canali = {c.nome: c for c in Canale.query.all()}
    occupate = {c.id: set() for cc in camere.values() for c in cc}
    canali_pesi = [('Booking.com', 44), ('Expedia', 14), ('Diretto (sito e booking engine)', 20), ('Telefono ed email', 13), ('Agenzie e tour operator', 9)]
    anticipo_medio = {'Booking.com': 28, 'Expedia': 34, 'Diretto (sito e booking engine)': 42, 'Telefono ed email': 30, 'Agenzie e tour operator': 75}
    tasso_canc = {'Booking.com': 0.24, 'Expedia': 0.20, 'Diretto (sito e booking engine)': 0.10, 'Telefono ed email': 0.08, 'Agenzie e tour operator': 0.12}
    from hotel import coefficiente, prezzo_notte
    ospiti = []
    n_codice = {}
    tassa_importo = 6.0
    inizio, fine = oggi - timedelta(days=95), oggi + timedelta(days=160)

    def nuovo_ospite():
        if ospiti and rnd.random() < 0.07:
            return rnd.choice(ospiti)
        paese = _scegli(rnd, PESI_PAESI)
        cognomi, nomi, nazione, lingua = NOMI[paese]
        cg, nm = rnd.choice(cognomi), rnd.choice(nomi)
        o = Ospite(cognome=cg, nome=nm, paese=nazione, lingua=lingua, cittadinanza=nazione,
                   email=f'{nm.lower()}.{cg.lower()}{rnd.randint(1, 99)}@example.com'.replace(' ', '').replace("'", ''),
                   telefono=f'+{rnd.randint(30, 49)} {rnd.randint(300, 399)} {rnd.randint(1000000, 9999999)}' if rnd.random() < 0.8 else '',
                   sesso=rnd.choice('MF'), data_nascita=date(rnd.randint(1955, 2000), rnd.randint(1, 12), rnd.randint(1, 28)),
                   consenso_marketing=rnd.random() < 0.35)
        db.session.add(o)
        ospiti.append(o)
        return o

    d = inizio
    while d < fine:
        k = coefficiente(d, stagioni)
        domanda = {0.72: 0.50, 0.80: 0.58, 0.95: 0.70, 1.05: 0.74, 1.10: 0.78, 1.22: 0.86, 1.25: 0.88}.get(k, 0.7)
        if d.weekday() in (4, 5):
            domanda += 0.10
        for e in eventi:
            if e.dal <= d <= e.al:
                domanda += e.impatto_pct / 200
        arrivi = max(0, int(rnd.gauss(domanda * 24 / 2.6 * 1.18, 2)))   # +18%: parte della domanda cancella
        for _ in range(arrivi):
            cod = _scegli(rnd, [('STD', 45), ('SUP', 25), ('SGL', 14), ('JS', 16)])
            notti = rnd.choices([1, 2, 3, 4, 5, 7], weights=[18, 30, 26, 13, 8, 5])[0]
            canale_nome = _scegli(rnd, canali_pesi)
            anticipo = min(300, int(rnd.expovariate(1 / anticipo_medio[canale_nome])))
            creato = d - timedelta(days=anticipo)
            if creato > oggi:
                continue   # la domanda di quel giorno non è ancora arrivata
            partenza = d + timedelta(days=notti)
            giorni_sogg = {d + timedelta(days=i) for i in range(notti)}
            libere = [c for c in camere[cod] if not (occupate[c.id] & giorni_sogg)]
            cancellata = rnd.random() < tasso_canc[canale_nome]
            piano = piani[_scegli(rnd, [('FLEX', 55), ('NR', 25), ('RO', 20)])]
            if piano.codice == 'NR':
                cancellata = rnd.random() < 0.03
            if not libere and not cancellata:
                continue   # tutto esaurito
            tipo = tipi[cod]
            adulti = 1 if tipo.posti_max == 1 else rnd.choices([1, 2, 3], weights=[12, 80, 8 if tipo.posti_max >= 3 else 0])[0]
            bambini = rnd.choice([0, 1, 2]) if cod == 'JS' and rnd.random() < 0.4 else 0
            canale = canali[canale_nome]
            ospite = nuovo_ospite()
            anno = creato.year
            n_codice[anno] = n_codice.get(anno, 0) + 1
            creato_dt = datetime.combine(creato, time(rnd.randint(8, 22), rnd.randint(0, 59)))
            p = Prenotazione(codice=f'H{anno}-{n_codice[anno]:05d}', ospite=ospite, arrivo=d, partenza=partenza, adulti=adulti, bambini=bambini,
                             tipo_id=tipo.id, piano_id=piano.id, canale_id=canale.id, creato_il=creato_dt,
                             rif_canale=f'{rnd.randint(10 ** 9, 10 ** 10 - 1)}' if canale.tipo == 'ota' else '')
            for i in range(notti):
                pr, col = prezzo_notte(tipo, piano, d + timedelta(days=i), adulti, bambini, stagioni, {})
                p.notti_vendute.append(NotteVenduta(data=d + timedelta(days=i), prezzo=pr, colazione=col))
            p.importo = round(sum(n.prezzo for n in p.notti_vendute), 2)
            p.caparra = round(p.importo * (piano.caparra_pct or 0) / 100, 2)
            if cancellata:
                quando = creato + timedelta(days=rnd.randint(0, max(0, anticipo)))
                if quando > oggi:
                    cancellata = False
            if cancellata:
                p.stato = 'cancellata'
                p.cancellata_il = datetime.combine(quando, time(rnd.randint(8, 22), 0))
                p.penale = 0 if piano.rimborsabile and (d - quando).days >= piano.giorni_cancellazione else (
                    p.importo if not piano.rimborsabile else p.notti_vendute[0].prezzo)
            elif not libere:
                continue
            else:
                cam = libere[0]
                p.camera_id = cam.id
                occupate[cam.id] |= giorni_sogg
                if partenza < oggi:
                    if d < oggi and rnd.random() < 0.025 and canale.tipo == 'ota':
                        p.stato, p.camera_id = 'no_show', None
                        occupate[cam.id] -= giorni_sogg
                        p.penale = p.importo if not piano.rimborsabile else p.notti_vendute[0].prezzo
                        p.cancellata_il = datetime.combine(d + timedelta(days=1), time(10, 0))
                    else:
                        p.stato = 'partita'
                        p.checkin_il = datetime.combine(d, time(rnd.randint(13, 21), rnd.randint(0, 59)))
                        p.checkout_il = datetime.combine(partenza, time(rnd.randint(8, 11), rnd.randint(0, 59)))
                elif d < oggi:   # in casa (chi parte oggi deve ancora fare il check-out)
                    p.stato = 'in_casa'
                    p.checkin_il = datetime.combine(d, time(rnd.randint(13, 21), rnd.randint(0, 59)))
                else:
                    p.stato = 'confermata'
                    if canale.tipo == 'diretto' and canale.commissione_pct == 0 and d > oggi + timedelta(days=5) and rnd.random() < 0.12:
                        p.stato, p.opzione_fino = 'opzione', oggi + timedelta(days=rnd.randint(1, 3))
            db.session.add(p)
            # extra, pagamenti, alloggiati
            if p.stato in ('partita', 'in_casa'):
                fino = min(partenza, oggi + timedelta(days=1))
                for i in range((fino - d).days):
                    g = d + timedelta(days=i)
                    if rnd.random() < 0.22:
                        p.addebiti.append(Addebito(data=g, descrizione=rnd.choice(['Bar: aperitivo', 'Minibar', 'Bar: caffetteria']), reparto='bar',
                                                   quantita=rnd.randint(1, 3), prezzo=rnd.choice([4, 6, 8, 10, 12]), iva_pct=10, costo=2.5))
                if rnd.random() < 0.15:
                    p.addebiti.append(Addebito(data=d, descrizione='Parcheggio convenzionato', reparto='servizi', quantita=notti, prezzo=28, iva_pct=22, costo=18))
                if piano.codice == 'RO' and rnd.random() < 0.3:
                    p.addebiti.append(Addebito(data=d + timedelta(days=1) if notti > 1 else d, descrizione='Colazione', reparto='colazione',
                                               quantita=adulti, prezzo=14, iva_pct=10, costo=4.5))
            prepagato = canale.nome == 'Expedia' or not piano.rimborsabile
            if p.stato in ('partita', 'in_casa', 'confermata', 'opzione', 'cancellata', 'no_show') and prepagato and p.stato != 'opzione':
                p.pagamenti.append(Pagamento(data=creato, importo=p.importo, metodo='ota' if canale.nome == 'Expedia' else 'carta',
                                             nota='Prepagato' if canale.nome == 'Expedia' else 'Caparra non rimborsabile'))
            if p.stato == 'cancellata' and prepagato and p.penale < p.importo:
                p.pagamenti.append(Pagamento(data=p.cancellata_il.date(), importo=-round(p.importo - p.penale, 2), metodo='carta', nota='Rimborso'))
            if p.stato == 'partita':
                tassa = round(adulti * min(notti, 7) * tassa_importo, 2)
                resto = round(p.importo + sum(a.totale for a in p.addebiti) + tassa - sum(x.importo for x in p.pagamenti), 2)
                if resto > 0:
                    p.pagamenti.append(Pagamento(data=partenza, importo=resto, metodo=rnd.choice(['carta', 'carta', 'carta', 'contanti'])))
            if p.stato == 'in_casa':
                p.alloggiati.append(Alloggiato(tipo='16' if p.persone == 1 else '17', cognome=ospite.cognome, nome=ospite.nome, sesso=ospite.sesso,
                                               data_nascita=ospite.data_nascita, cittadinanza=ospite.cittadinanza, stato_nascita=ospite.paese,
                                               tipo_documento='Passaporto' if ospite.paese != 'Italia' else 'Carta d\'identità',
                                               numero_documento=f'X{rnd.randint(1000000, 9999999)}', luogo_rilascio=ospite.paese))
                for j in range(p.persone - 1):
                    nm = rnd.choice(NOMI['IT'][1])
                    bimbo = j >= adulti - 1
                    p.alloggiati.append(Alloggiato(tipo='19', cognome=ospite.cognome, nome=nm, sesso=rnd.choice('MF'),
                                                   data_nascita=date(oggi.year - (rnd.randint(3, 10) if bimbo else rnd.randint(25, 60)), 5, 10),
                                                   cittadinanza=ospite.cittadinanza, stato_nascita=ospite.paese))
        d += timedelta(days=1)
    # qualche preventivo aperto e stato delle camere di oggi
    db.session.flush()
    for p in Prenotazione.query.filter(Prenotazione.stato == 'partita', Prenotazione.partenza >= oggi - timedelta(days=1)).all():
        if p.camera_id:
            db.session.get(Camera, p.camera_id).stato = 'sporca'
    for p in Prenotazione.query.filter(Prenotazione.stato == 'in_casa').all():
        if rnd.random() < 0.6 and p.camera_id:
            db.session.get(Camera, p.camera_id).stato = 'sporca'   # da rifare in giornata
    # restrizione: soggiorno minimo 2 notti al primo giorno di Pitti
    for e in Evento.query.filter(Evento.nome.like('Pitti Uomo%'), Evento.dal >= oggi).all():
        db.session.add(Restrizione(data=e.dal, min_notti=2))
    _utenti_demo()
    db.session.commit()
    return True


def _utenti_demo():
    from flask import current_app
    pw = current_app.config.get('DEMO_PASSWORD')
    if not pw or len(pw) < 8:
        return
    from spazi import spazio_corrente
    for username, nome, ruolo in [('reception.demo', 'Giulia (reception)', 'reception'), ('governante.demo', 'Rosa (governante)', 'piani'),
                                  ('revenue.demo', 'Marco (revenue)', 'revenue')]:
        if not Utente.query.filter(db.func.lower(Utente.username) == username).first():
            db.session.add(Utente(nome=nome, username=username, ruolo=ruolo, password_hash=generate_password_hash(pw), spazio_id=spazio_corrente()))


TABELLE_DATI = [Alloggiato, Pagamento, Addebito, NotteVenduta, Prenotazione, Ospite, TariffaGiorno, Restrizione, Camera, TipoCamera,
                PianoTariffario, Stagione, Canale, Evento, CostoFisso]


def svuota():
    """Cancella tutti i dati alberghieri dello spazio corrente (prima i figli, poi i padri)."""
    for m in TABELLE_DATI:
        m.query.delete(synchronize_session=False)


def elimina_demo():
    """Svuota lo spazio corrente (dati dimostrativi o di prova) e rimette la struttura di base."""
    svuota()
    for u in Utente.query.filter(Utente.username.in_(['reception.demo', 'governante.demo', 'revenue.demo'])).all():
        from spazi import spazio_corrente
        if u.spazio_id == spazio_corrente():
            db.session.delete(u)
    Impostazione.scrivi('demo_eliminati', '1')
    Impostazione.scrivi('demo', '')
    if Impostazione.leggi('nome_struttura') == NOME_DEMO:
        Impostazione.scrivi('nome_struttura', '')
    db.session.commit()
    struttura_iniziale()
