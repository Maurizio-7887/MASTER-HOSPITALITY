"""Channel manager: disponibilità, prezzi e restrizioni per canale (ARI), parità tariffaria, invio ai portali,
prenotazioni ricevute dai portali e calendari iCal.

Come i channel manager del mercato (WuBook, Channex, Octorate, SiteMinder) il gestionale è la fonte unica:
- per ogni canale si mappano le tipologie (codice della camera sul portale), si decide quante camere mettere in vendita
  (allotment) e l'eventuale ricarico sul prezzo;
- a ogni cambio di prezzo o di disponibilità si inviano al portale disponibilità, prezzi e restrizioni del periodo (ARI);
- le prenotazioni del portale entrano nel gestionale con il loro codice, una volta sola (le modifiche e le cancellazioni
  aggiornano la stessa prenotazione);
- per Airbnb, Vrbo e gli appartamenti su Booking basta il calendario iCal: si esporta l'occupato e si importa il venduto.

Connessioni: «simulatore» (portale finto per il corso e le prove: riceve gli invii e genera prenotazioni realistiche),
«ical», «wubook» (API Wired XML-RPC) e «channex» (API REST). Le ultime due chiamano davvero i servizi solo con le
credenziali nelle variabili d'ambiente; senza, il gestionale prepara e registra i dati che invierebbe.
"""
import json
import os
import random
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta

import hotel as h
from models import (CONNESSIONI, OCCUPANO, Canale, CanaleTipo, Ospite, PianoTariffario, Prenotazione, Restrizione,
                    Sincronizzazione, TariffaGiorno, TipoCamera, db)


class ErroreCanale(h.Errore):
    pass


# ---------------- mappature ----------------

def mappature(canale):
    return CanaleTipo.query.filter_by(canale_id=canale.id).order_by(CanaleTipo.id).all()


def prepara_canale(canale):
    """Crea le mappature mancanti (una per tipologia) e il token per l'iCal."""
    esistenti = {m.tipo_id for m in mappature(canale)}
    for t in TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all():
        if t.id not in esistenti:
            db.session.add(CanaleTipo(canale_id=canale.id, tipo_id=t.id, codice_ota=f'{t.codice}', attivo=canale.tipo != 'diretto'))
    if not canale.token:
        canale.token = secrets.token_urlsafe(18)
    if not canale.connessione:
        canale.connessione = 'manuale'


def piano_base():
    """Piano tariffario di riferimento per i portali: il primo rimborsabile attivo (di solito FLEX / BAR)."""
    return (PianoTariffario.query.filter_by(attivo=True, rimborsabile=True).order_by(PianoTariffario.id).first()
            or PianoTariffario.query.filter_by(attivo=True).first())


# ---------------- ARI: disponibilità, prezzi, restrizioni per canale ----------------

def vendute_dal_canale(canale_id, tipo_id, d):
    return Prenotazione.query.filter(Prenotazione.canale_id == canale_id, Prenotazione.tipo_id == tipo_id,
                                     Prenotazione.stato.in_(OCCUPANO), Prenotazione.arrivo <= d, Prenotazione.partenza > d).count()


def ari(canale, da, a):
    """Per ogni tipologia mappata e ogni notte: camere in vendita sul canale, prezzo, soggiorno minimo, chiusure."""
    piano = piano_base()
    stagioni = h.Stagione.query.all()
    tariffe = {(t.tipo_id, t.data): t.prezzo for t in TariffaGiorno.query.filter(TariffaGiorno.data >= da, TariffaGiorno.data < a).all()}
    restr = Restrizione.query.filter(Restrizione.data >= da, Restrizione.data < a).all()
    righe = []
    for m in mappature(canale):
        if not m.attivo:
            continue
        t = m.tipo
        disp = h.disponibili_per_notte(t.id, da, a)
        giorni = []
        for d in h.giorni(da, a):
            libere = max(0, disp.get(d, 0))
            if m.allotment is not None:
                libere = min(libere, max(0, m.allotment - vendute_dal_canale(canale.id, t.id, d)))
            prezzo, _ = h.prezzo_notte(t, piano, d, t.posti_base, 0, stagioni, tariffe) if piano else (h.prezzo_camera(t, d, stagioni, tariffe), 0)
            prezzo = round(prezzo * (1 + (m.ricarico_pct or 0) / 100), 2)
            r_giorno = [r for r in restr if r.data == d and r.tipo_id in (None, t.id)]
            giorni.append({'data': d.isoformat(), 'disponibili': libere, 'prezzo': prezzo,
                           'min_notti': max([r.min_notti or 1 for r in r_giorno] or [1]),
                           'chiuso_arrivo': any(r.chiuso_arrivo for r in r_giorno), 'chiuso': any(r.chiuso for r in r_giorno)})
        righe.append({'mappatura': m, 'tipo': t, 'codice_ota': m.codice_ota or t.codice, 'giorni': giorni})
    return righe


def parita(da, a):
    """Parità tariffaria: per ogni tipologia e notte il prezzo pubblico su ogni canale e quanto resta all'hotel
    dopo la commissione. Segnala i canali che vendono sotto il sito (parità rotta) e il canale più conveniente."""
    canali = Canale.query.filter_by(attivo=True).order_by(Canale.id).all()
    diretto = next((c for c in canali if c.tipo == 'diretto'), None)
    tabella = []
    for t in TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all():
        for d in h.giorni(da, a):
            riga = {'tipo': t, 'data': d, 'canali': []}
            for c in canali:
                m = CanaleTipo.query.filter_by(canale_id=c.id, tipo_id=t.id).first()
                ricarico = (m.ricarico_pct or 0) if m else 0
                prezzo = round(h.prezzo_camera(t, d) * (1 + ricarico / 100), 2)
                netto = round(prezzo * (1 - (c.commissione_pct or 0) / 100), 2)
                riga['canali'].append({'canale': c, 'prezzo': prezzo, 'netto': netto})
            base = next((x['prezzo'] for x in riga['canali'] if diretto and x['canale'].id == diretto.id), None)
            for x in riga['canali']:
                x['sotto_il_sito'] = base is not None and x['prezzo'] < base - 0.005
            riga['migliore'] = max(riga['canali'], key=lambda x: x['netto'])['canale'].nome if riga['canali'] else ''
            tabella.append(riga)
    return tabella


# ---------------- registro ----------------

def registra(canale, direzione, oggetto, messaggio, dati=None, esito='ok'):
    s = Sincronizzazione(canale_id=canale.id if canale else None, direzione=direzione, oggetto=oggetto, esito=esito,
                         messaggio=messaggio[:300], dati=json.dumps(dati, default=str, ensure_ascii=False)[:20000] if dati is not None else '')
    db.session.add(s)
    return s


# ---------------- connettori ----------------

class Connettore:
    nome = 'manuale'

    def __init__(self, canale):
        self.canale = canale

    def invia_ari(self, righe, da, a):
        return {'inviato': False, 'messaggio': 'Canale manuale: aggiorna l\'extranet del portale con i dati qui sotto'}

    def ricevi(self):
        return []


class Simulatore(Connettore):
    """Portale finto: accetta gli invii e genera prenotazioni plausibili sulle notti ancora in vendita."""
    nome = 'simulatore'

    def invia_ari(self, righe, da, a):
        notti = sum(len(r['giorni']) for r in righe)
        return {'inviato': True, 'messaggio': f'Portale aggiornato: {len(righe)} tipologie × {notti // max(1, len(righe))} notti'}

    def ricevi(self, quante=None, seme=None, oggi=None):
        rnd = random.Random(seme)
        oggi = oggi or date.today()
        quante = quante if quante is not None else rnd.randint(1, 3)
        righe = ari(self.canale, oggi + timedelta(days=1), oggi + timedelta(days=60))
        nomi = [('Müller', 'Anna', 'DE'), ('Smith', 'John', 'GB'), ('Dupont', 'Claire', 'FR'), ('García', 'Lucía', 'ES'),
                ('Rossi', 'Marco', 'IT'), ('Johnson', 'Emily', 'US'), ('Tanaka', 'Yuki', 'JP'), ('Bianchi', 'Sara', 'IT')]
        out = []
        for _ in range(quante):
            vendibili = [(r, i) for r in righe for i, g_ in enumerate(r['giorni']) if g_['disponibili'] > 0 and not g_['chiuso']
                         and not g_['chiuso_arrivo']]
            if not vendibili:
                break
            r, i = rnd.choice(vendibili)
            notti = max(r['giorni'][i]['min_notti'], rnd.choice([1, 2, 2, 3, 3, 4, 5]))
            notti = min(notti, len(r['giorni']) - i)
            if any(g_['disponibili'] <= 0 for g_ in r['giorni'][i:i + notti]):
                continue
            for g_ in r['giorni'][i:i + notti]:
                g_['disponibili'] -= 1
            cognome, nome, paese = rnd.choice(nomi)
            arrivo = date.fromisoformat(r['giorni'][i]['data'])
            out.append({'id': f'{self.canale.nome[:3].upper()}{rnd.randint(10_000_000, 99_999_999)}', 'stato': 'nuova',
                        'codice_camera': r['codice_ota'], 'arrivo': arrivo.isoformat(), 'partenza': (arrivo + timedelta(days=notti)).isoformat(),
                        'adulti': min(2, r['tipo'].posti_max), 'bambini': 0, 'cognome': cognome, 'nome': nome, 'paese': paese,
                        'email': f'{nome.lower()}.{cognome.lower()}@guest.example'.replace('ü', 'u').replace('í', 'i'),
                        'totale': round(sum(g_['prezzo'] for g_ in r['giorni'][i:i + notti]), 2)})
        return out


class WuBook(Connettore):
    """API Wired di WuBook (XML-RPC): update_avail, update_plan_prices, fetch_new_bookings / mark_bookings.
    Credenziali: WUBOOK_USER, WUBOOK_PASSWORD, WUBOOK_PROVIDER_KEY; codice struttura (lcode) e piano (pid) nel canale."""
    nome = 'wubook'
    URL = 'https://wired.wubook.net/xrws/'

    def _server(self):
        import xmlrpc.client
        user, pw, pkey = (os.environ.get(k, '') for k in ('WUBOOK_USER', 'WUBOOK_PASSWORD', 'WUBOOK_PROVIDER_KEY'))
        if not (user and pw):
            return None, None
        srv = xmlrpc.client.ServerProxy(self.URL)
        res, token = srv.acquire_token(user, pw, pkey)
        if res != 0:
            raise ErroreCanale(f'WuBook: accesso rifiutato ({token})')
        return srv, token

    def lcode(self):
        lcode, _, pid = (self.canale.codice_struttura or '').partition('/')
        return int(lcode) if lcode.isdigit() else 0, int(pid) if pid.isdigit() else 0

    def pacchetto(self, righe, da):
        rooms = [{'id': int(r['codice_ota']) if str(r['codice_ota']).isdigit() else r['codice_ota'],
                  'days': [{'avail': g_['disponibili'], 'min_stay': g_['min_notti'], 'closed_arrival': int(g_['chiuso_arrivo']),
                            'closed': int(g_['chiuso'])} for g_ in r['giorni']]} for r in righe]
        prezzi = {str(r['codice_ota']): [g_['prezzo'] for g_ in r['giorni']] for r in righe}
        return {'dfrom': da.strftime('%d/%m/%Y'), 'rooms': rooms, 'prices': prezzi}

    def invia_ari(self, righe, da, a):
        dati = self.pacchetto(righe, da)
        srv, token = self._server()
        if srv is None:
            return {'inviato': False, 'messaggio': 'WuBook non configurato (WUBOOK_USER/WUBOOK_PASSWORD): dati preparati e registrati', 'dati': dati}
        lcode, pid = self.lcode()
        try:
            res, msg = srv.update_avail(token, lcode, dati['dfrom'], dati['rooms'])
            if res != 0:
                raise ErroreCanale(f'WuBook update_avail: {msg}')
            if pid:
                res, msg = srv.update_plan_prices(token, lcode, pid, dati['dfrom'], dati['prices'])
                if res != 0:
                    raise ErroreCanale(f'WuBook update_plan_prices: {msg}')
        finally:
            srv.release_token(token)
        return {'inviato': True, 'messaggio': 'WuBook aggiornato', 'dati': dati}

    def ricevi(self):
        srv, token = self._server()
        if srv is None:
            return []
        lcode, _ = self.lcode()
        try:
            res, pren = srv.fetch_new_bookings(token, lcode, 1, 1)
            if res != 0:
                raise ErroreCanale(f'WuBook fetch_new_bookings: {pren}')
            out = []
            for b in pren:
                arr = datetime.strptime(b['date_arrival'], '%d/%m/%Y').date()
                par = datetime.strptime(b['date_departure'], '%d/%m/%Y').date()
                camera = str((b.get('rooms') or '').split(',')[0])
                out.append({'id': str(b['reservation_code']), 'stato': 'cancellata' if b.get('status') in (3, 5) else 'nuova',
                            'codice_camera': camera, 'arrivo': arr.isoformat(), 'partenza': par.isoformat(),
                            'adulti': int(b.get('men') or 2), 'bambini': int(b.get('children') or 0),
                            'cognome': b.get('customer_surname') or 'Ospite', 'nome': b.get('customer_name') or '',
                            'email': b.get('customer_mail') or '', 'paese': b.get('customer_country') or '', 'totale': float(b.get('amount') or 0)})
            return out
        finally:
            srv.release_token(token)


class Channex(Connettore):
    """API REST di Channex: /availability, /restrictions e il feed delle revisioni delle prenotazioni.
    Credenziali: CHANNEX_API_KEY (e CHANNEX_URL, predefinito l'ambiente di prova); nel canale l'id della struttura."""
    nome = 'channex'

    def _base(self):
        return os.environ.get('CHANNEX_URL', 'https://staging.channex.io/api/v1'), os.environ.get('CHANNEX_API_KEY', '')

    def pacchetto(self, righe, da):
        prop = self.canale.codice_struttura
        avail, restr = [], []
        for r in righe:
            for g_ in r['giorni']:
                avail.append({'property_id': prop, 'room_type_id': r['codice_ota'], 'date': g_['data'], 'availability': g_['disponibili']})
                restr.append({'property_id': prop, 'rate_plan_id': r['codice_ota'], 'date': g_['data'], 'rate': f"{g_['prezzo']:.2f}",
                              'min_stay_arrival': g_['min_notti'], 'closed_to_arrival': g_['chiuso_arrivo'], 'stop_sell': g_['chiuso']})
        return {'availability': {'values': avail}, 'restrictions': {'values': restr}}

    def invia_ari(self, righe, da, a):
        dati = self.pacchetto(righe, da)
        url, key = self._base()
        if not key:
            return {'inviato': False, 'messaggio': 'Channex non configurato (CHANNEX_API_KEY): dati preparati e registrati', 'dati': dati}
        import requests
        for ep in ('availability', 'restrictions'):
            r = requests.post(f'{url}/{ep}', json=dati[ep], headers={'user-api-key': key}, timeout=20)
            if r.status_code >= 300:
                raise ErroreCanale(f'Channex {ep}: {r.status_code} {r.text[:200]}')
        return {'inviato': True, 'messaggio': 'Channex aggiornato', 'dati': dati}


CONNETTORI = {'manuale': Connettore, 'simulatore': Simulatore, 'wubook': WuBook, 'channex': Channex, 'ical': Connettore}


def connettore(canale):
    return CONNETTORI.get(canale.connessione or 'manuale', Connettore)(canale)


# ---------------- invio e ricezione ----------------

def invia(canale, da=None, a=None):
    """Invia al portale disponibilità, prezzi e restrizioni del periodo (predefinito: 90 giorni da oggi)."""
    da = da or date.today()
    a = a or da + timedelta(days=90)
    righe = ari(canale, da, a)
    try:
        esito = connettore(canale).invia_ari(righe, da, a)
    except Exception as e:   # noqa: BLE001 — l'errore del portale si registra, il gestionale continua
        registra(canale, 'uscita', 'disponibilita', f'Errore: {e}', esito='errore')
        raise ErroreCanale(str(e))
    riepilogo = [{'camera': r['codice_ota'], 'giorni': r['giorni'][:31]} for r in righe]
    registra(canale, 'uscita', 'disponibilita', esito['messaggio'], esito.get('dati') or riepilogo)
    canale.ultimo_sync = datetime.utcnow()
    return esito


def invia_a_tutti(da=None, a=None):
    risultati = []
    for c in Canale.query.filter(Canale.attivo.is_(True), Canale.connessione.in_(('simulatore', 'wubook', 'channex'))).all():
        try:
            risultati.append((c, invia(c, da, a)['messaggio']))
        except ErroreCanale as e:
            risultati.append((c, f'errore: {e}'))
    return risultati


def _ospite(d):
    o = None
    if d.get('email'):
        o = Ospite.query.filter(db.func.lower(Ospite.email) == d['email'].lower()).first()
    if not o:
        o = Ospite(cognome=(d.get('cognome') or 'Ospite')[:60], nome=(d.get('nome') or '')[:60], email=(d.get('email') or '')[:200],
                   paese=(d.get('paese') or '')[:60], telefono=(d.get('telefono') or '')[:40])
        db.session.add(o)
    return o


def importa(canale, d):
    """Registra una prenotazione ricevuta dal portale. Ritorna (prenotazione, esito) con esito nuova|aggiornata|cancellata|doppia|errore."""
    esistente = Prenotazione.query.filter_by(canale_id=canale.id, uid_esterno=str(d['id'])).first()
    if d.get('stato') == 'cancellata':
        if esistente and esistente.stato in ('opzione', 'confermata'):
            h.cancella(esistente, penale=h.penale_cancellazione(esistente))
            registra(canale, 'entrata', 'prenotazione', f'Cancellata dal portale: {esistente.codice} ({d["id"]})', d)
            return esistente, 'cancellata'
        return esistente, 'doppia'
    if esistente:
        return esistente, 'doppia'
    m = next((x for x in mappature(canale) if str(x.codice_ota) == str(d.get('codice_camera'))), None)
    if not m:
        registra(canale, 'entrata', 'prenotazione', f'Camera del portale non mappata: {d.get("codice_camera")}', d, 'errore')
        return None, 'errore'
    piano = piano_base()
    try:
        p = h.crea_prenotazione(_ospite(d), m.tipo_id, piano.id, canale.id, d['arrivo'], d['partenza'], int(d.get('adulti') or 2),
                                int(d.get('bambini') or 0), 'confermata', rif_canale=str(d['id'])[:40],
                                prezzo_totale=float(d.get('totale') or 0) or None, forza=True)
    except h.Errore as e:
        registra(canale, 'entrata', 'prenotazione', f'Non registrata ({e}): {d["id"]}', d, 'errore')
        return None, 'errore'
    p.uid_esterno = str(d['id'])[:120]
    libere = h.disponibili_per_notte(m.tipo_id, p.arrivo, p.partenza)
    overbooking = min(libere.values()) < 0
    registra(canale, 'entrata', 'prenotazione',
             f'{"OVERBOOKING · " if overbooking else ""}Nuova {p.codice} ({d["id"]}): {p.ospite.nominativo}, {p.tipo.codice}, '
             f'{p.arrivo.strftime("%d/%m")}-{p.partenza.strftime("%d/%m")}, {p.importo:.2f} €', d, 'errore' if overbooking else 'ok')
    return p, 'nuova'


def ricevi(canale, **kw):
    try:
        arrivate = connettore(canale).ricevi(**kw) if isinstance(connettore(canale), Simulatore) else connettore(canale).ricevi()
    except Exception as e:   # noqa: BLE001
        registra(canale, 'entrata', 'prenotazione', f'Errore di lettura: {e}', esito='errore')
        raise ErroreCanale(str(e))
    esiti = defaultdict(list)
    for d in arrivate:
        p, esito = importa(canale, d)
        esiti[esito].append(p)
    canale.ultimo_sync = datetime.utcnow()
    return esiti


# ---------------- iCal ----------------

def _ics_testo(s):
    return s.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def ical_export(canale, tipo_id):
    """Calendario delle notti NON disponibili per il portale: una VEVENT per ogni periodo in cui la tipologia è piena
    (o l'allotment del canale è esaurito). Le prenotazioni dello stesso canale non si esportano: le ha già il portale."""
    t = db.session.get(TipoCamera, tipo_id)
    oggi = date.today()
    da, a = oggi, oggi + timedelta(days=365)
    righe = [r for r in ari(canale, da, a) if r['tipo'].id == tipo_id] if t else []
    piena = []
    if righe:
        for g_ in righe[0]['giorni']:
            d = date.fromisoformat(g_['data'])
            proprie = vendute_dal_canale(canale.id, tipo_id, d)
            if g_['disponibili'] + proprie <= 0 or g_['chiuso']:
                piena.append(d)
    blocchi, inizio, prec = [], None, None
    for d in piena:
        if inizio is None:
            inizio = d
        elif (d - prec).days > 1:
            blocchi.append((inizio, prec + timedelta(days=1)))
            inizio = d
        prec = d
    if inizio:
        blocchi.append((inizio, prec + timedelta(days=1)))
    adesso = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    out = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//MASTER HOSPITALITY//Channel manager//IT', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
           f'X-WR-CALNAME:{_ics_testo(h.par("nome_struttura"))} - {_ics_testo(t.nome if t else "")}']
    for i, (d1, d2) in enumerate(blocchi):
        out += ['BEGIN:VEVENT', f'UID:mh-{canale.id}-{tipo_id}-{d1:%Y%m%d}@masterhospitality', f'DTSTAMP:{adesso}',
                f'DTSTART;VALUE=DATE:{d1:%Y%m%d}', f'DTEND;VALUE=DATE:{d2:%Y%m%d}', 'SUMMARY:Non disponibile', 'END:VEVENT']
    out.append('END:VCALENDAR')
    return '\r\n'.join(out) + '\r\n'


def leggi_ics(testo):
    """Eventi di un file .ics: uid, inizio, fine (esclusa), titolo, descrizione. Gestisce le righe spezzate."""
    righe = []
    for r in testo.replace('\r\n', '\n').split('\n'):
        if r[:1] in (' ', '\t') and righe:
            righe[-1] += r[1:]
        else:
            righe.append(r)
    eventi, ev = [], None
    for r in righe:
        if r == 'BEGIN:VEVENT':
            ev = {}
        elif r == 'END:VEVENT' and ev is not None:
            if ev.get('inizio') and ev.get('fine'):
                eventi.append(ev)
            ev = None
        elif ev is not None and ':' in r:
            chiave, _, valore = r.partition(':')
            nome = chiave.split(';')[0].upper()
            if nome in ('DTSTART', 'DTEND'):
                v = valore.strip()[:8]
                try:
                    ev['inizio' if nome == 'DTSTART' else 'fine'] = date(int(v[:4]), int(v[4:6]), int(v[6:8]))
                except ValueError:
                    pass
            elif nome == 'UID':
                ev['uid'] = valore.strip()
            elif nome == 'SUMMARY':
                ev['titolo'] = valore.replace('\\,', ',').replace('\\;', ';').strip()
            elif nome == 'DESCRIPTION':
                ev['descrizione'] = valore.replace('\\n', ' ').replace('\\,', ',')[:300]
    return eventi


def ical_import(canale, testo, tipo_id=None):
    """Importa dal calendario del portale le prenotazioni (Airbnb: «Reserved»); i blocchi del proprietario
    («Not available», «Blocked») si registrano come stop vendite sulla tipologia."""
    m = next((x for x in mappature(canale) if x.attivo and (tipo_id is None or x.tipo_id == tipo_id)), None)
    if not m:
        raise ErroreCanale('Mappa almeno una tipologia per questo canale')
    oggi = date.today()
    nuove, bloccate, doppie, errori = [], 0, 0, []
    for ev in leggi_ics(testo):
        if ev['fine'] <= oggi or ev['fine'] <= ev['inizio']:
            continue
        titolo = (ev.get('titolo') or '').lower()
        uid = ev.get('uid') or f'{ev["inizio"]}-{ev["fine"]}'
        if any(k in titolo for k in ('not available', 'blocked', 'non disponibile', 'closed')):
            for d in h.giorni(max(ev['inizio'], oggi), ev['fine']):
                if not Restrizione.query.filter_by(data=d, tipo_id=m.tipo_id, chiuso=True).first():
                    db.session.add(Restrizione(data=d, tipo_id=m.tipo_id, chiuso=True))
                    bloccate += 1
            continue
        if Prenotazione.query.filter_by(canale_id=canale.id, uid_esterno=uid[:120]).first():
            doppie += 1
            continue
        p, esito = importa(canale, {'id': uid[:120], 'codice_camera': m.codice_ota, 'arrivo': ev['inizio'].isoformat(),
                                    'partenza': ev['fine'].isoformat(), 'adulti': min(2, m.tipo.posti_max),
                                    'cognome': f'Ospite {canale.nome}', 'nome': '', 'totale': 0})
        if p:
            p.uid_esterno = uid[:120]
            p.note = (ev.get('descrizione') or '')[:500]
            nuove.append(p)
        else:
            errori.append(uid)
    registra(canale, 'entrata', 'ical', f'iCal: {len(nuove)} nuove, {doppie} già presenti, {bloccate} notti bloccate, {len(errori)} errori')
    canale.ultimo_sync = datetime.utcnow()
    return {'nuove': nuove, 'doppie': doppie, 'bloccate': bloccate, 'errori': errori}


def scarica_ical(canale):
    if not canale.ical_url:
        raise ErroreCanale('Inserisci l\'indirizzo del calendario del portale')
    import requests
    r = requests.get(canale.ical_url, timeout=20)
    if r.status_code != 200 or 'BEGIN:VCALENDAR' not in r.text:
        raise ErroreCanale(f'Calendario non leggibile ({r.status_code})')
    return ical_import(canale, r.text)


def stato_canali():
    """Per la pagina: canali con connessione, ultimo invio, prenotazioni dell'anno e ultimi messaggi."""
    oggi = date.today()
    out = []
    for c in Canale.query.order_by(Canale.id).all():
        n = Prenotazione.query.filter(Prenotazione.canale_id == c.id, Prenotazione.stato.in_(OCCUPANO),
                                      Prenotazione.arrivo >= oggi.replace(month=1, day=1)).count()
        ultimo = Sincronizzazione.query.filter_by(canale_id=c.id).order_by(Sincronizzazione.id.desc()).first()
        out.append({'c': c, 'prenotazioni': n, 'ultimo': ultimo, 'connessione': CONNESSIONI.get(c.connessione or 'manuale', c.connessione)})
    return out
