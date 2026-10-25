"""Attivazione degli spazi clienti.

Senza Stripe (modalità di partenza): il cliente compila la richiesta e sceglie le credenziali, riceve le istruzioni per il
bonifico con un codice da indicare in causale; il gestore vede la richiesta nella pagina Spazi e, ricevuto il bonifico,
la attiva con la scadenza voluta. Variabili: IBAN, INTESTATARIO_CONTO, PREZZO_TESTO, MAIL_AVVISI (+ SMTP_* per l'avviso
via email, facoltativo).

Con Stripe: il cliente compila i dati, paga con Stripe Checkout e trova il suo spazio pronto.

Variabili (Railway):
  STRIPE_SECRET_KEY       chiave segreta (sk_live_… o sk_test_… per le prove)
  STRIPE_PRICE_ID         prezzo ricorrente del piano (price_…)
  STRIPE_WEBHOOK_SECRET   segreto dell'endpoint webhook (whsec_…), endpoint: https://<dominio>/stripe/webhook
  PREZZO_TESTO            testo del prezzo mostrato in pagina, es. "49 € + IVA al mese" (facoltativo)
  GIORNI_PROVA            giorni di prova gratuita prima del primo addebito (facoltativo)
  URL_CONDIZIONI          link alle condizioni del servizio (facoltativo)

Eventi Stripe usati: checkout.session.completed (crea lo spazio), invoice.paid (rinnova la scadenza),
customer.subscription.deleted (fine abbonamento: lo spazio passa in sola lettura alla scadenza).
"""
import hashlib
import hmac
import json
import secrets
import time
from datetime import date, datetime, timedelta

import requests
from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from models import Spazio, Utente, db

bp = Blueprint('abbonamento', __name__)
API = 'https://api.stripe.com/v1'
GRAZIA = 3   # giorni di tolleranza dopo la fine del periodo pagato


class Iscrizione(db.Model):
    """Richiesta di abbonamento in attesa del pagamento."""
    __tablename__ = 'iscrizioni'
    token = db.Column(db.String(48), primary_key=True)
    ragione_sociale = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(60), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    spazio_id = db.Column(db.Integer, nullable=True)
    sessione = db.Column(db.String(120), default='')
    codice = db.Column(db.String(20), default='')          # da indicare nella causale del bonifico
    telefono = db.Column(db.String(40), default='')
    stato = db.Column(db.String(12), default='attesa')      # attesa | attivata | rifiutata
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)


def nuovo_codice():
    """MH-AAAA-NNN progressivo per anno: identifica la richiesta, l'ordine e la successiva fattura."""
    anno = date.today().year
    n = Iscrizione.query.filter(Iscrizione.codice.like(f'MH-{anno}-%')).count() + 1
    while Iscrizione.query.filter_by(codice=f'MH-{anno}-{n:03d}').first():
        n += 1
    return f'MH-{anno}-{n:03d}'


def invia_email(a, oggetto, testo, rispondi_a=None):
    """Invio con le stesse variabili degli altri programmi: SMTP_HOST, SMTP_PORT (465 = SSL), SMTP_USER, SMTP_PASSWORD, SMTP_FROM."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    c = current_app.config
    if not (c.get('SMTP_HOST') and c.get('SMTP_PASSWORD') and a):
        current_app.logger.warning('SMTP non configurato: email a %s non inviata (%s)', a, oggetto)
        return False
    m = EmailMessage()
    m['Subject'], m['To'] = oggetto, a
    m['From'] = c.get('SMTP_FROM') or c.get('SMTP_USER')
    if rispondi_a:
        m['Reply-To'] = rispondi_a
    m.set_content(testo)
    porta = int(c.get('SMTP_PORT') or 465)
    try:
        if porta == 465:
            with smtplib.SMTP_SSL(c['SMTP_HOST'], 465, context=ssl.create_default_context(), timeout=15) as srv:
                srv.login(c['SMTP_USER'], c['SMTP_PASSWORD'])
                srv.send_message(m)
        else:
            with smtplib.SMTP(c['SMTP_HOST'], porta, timeout=15) as srv:
                srv.starttls(context=ssl.create_default_context())
                srv.login(c['SMTP_USER'], c['SMTP_PASSWORD'])
                srv.send_message(m)
        return True
    except Exception:   # noqa: BLE001 — l'invio non deve mai bloccare la richiesta
        current_app.logger.exception('Invio email fallito verso %s', a)
        return False


def avvisa_gestore(isc):
    """Email a te (MAIL_AVVISI, di norma info@mauriziogustinicchiconsulting.it) e conferma al cliente con i dati del bonifico."""
    c = current_app.config
    invia_email(c.get('MAIL_AVVISI'), f'MASTER HOSPITALITY: richiesta di attivazione {isc.codice} — {isc.ragione_sociale}', f"""Nuova richiesta di attivazione di MASTER HOSPITALITY

Codice: {isc.codice}
Ragione sociale: {isc.ragione_sociale}
Referente: {isc.nome}
Email: {isc.email}
Telefono: {isc.telefono or '-'}
Nome utente scelto: {isc.username}

Ricevuto il bonifico, attiva lo spazio dalla pagina Spazi: {url_for('piattaforma.pagina', _external=True)}
""", rispondi_a=isc.email)
    iban = c.get('IBAN')
    invia_email(isc.email, f'MASTER HOSPITALITY — richiesta {isc.codice} ricevuta', f"""Gentile {isc.nome},

abbiamo ricevuto la richiesta di attivazione di MASTER HOSPITALITY per {isc.ragione_sociale}.

{'Importo: ' + c['PREZZO_TESTO'] if c.get('PREZZO_TESTO') else ''}
Intestatario: {c.get('INTESTATARIO_CONTO') or 'Maurizio Gustinicchi Consulting'}
{'IBAN: ' + iban if iban else 'Le coordinate bancarie le riceverà a breve in una email separata.'}
Causale: {isc.codice} — MASTER HOSPITALITY — {isc.ragione_sociale}

Appena riceviamo il bonifico attiviamo lo spazio e la avvisiamo: entrerà da {url_for('accesso.login', _external=True)} con il nome utente «{isc.username}» e la password scelta.

Maurizio Gustinicchi Consulting
info@mauriziogustinicchiconsulting.it
""", rispondi_a=c.get('MAIL_AVVISI'))


def configurato():
    c = current_app.config
    return bool(c.get('STRIPE_SECRET_KEY') and c.get('STRIPE_PRICE_ID'))


def _stripe(metodo, percorso, dati=None):
    r = requests.request(metodo, API + percorso, data=dati, auth=(current_app.config['STRIPE_SECRET_KEY'], ''), timeout=20)
    try:
        corpo = r.json()
    except ValueError:
        corpo = {}
    if r.status_code >= 400:
        raise RuntimeError((corpo.get('error') or {}).get('message') or f'Stripe ha risposto {r.status_code}')
    return corpo


def _url(endpoint, **kw):
    return url_for(endpoint, _external=True, _scheme='https' if not current_app.debug and not current_app.testing else None, **kw)


@bp.route('/abbonati', methods=['GET', 'POST'])
def pagina():
    from accesso import _csrf_ok, _valida
    errore, dati = '', {}
    if request.method == 'POST':
        dati = {k: (request.form.get(k) or '').strip() for k in ('ragione_sociale', 'email', 'nome', 'username')}
        telefono = (request.form.get('telefono') or '').strip()[:40]
        pw, pw2 = request.form.get('password') or '', request.form.get('password2') or ''
        if not _csrf_ok():
            errore = 'Sessione scaduta: riprova'
        elif not all(dati.values()) or '@' not in dati['email']:
            errore = 'Compila tutti i campi con un indirizzo email valido'
        elif current_app.config.get('URL_CONDIZIONI') and not request.form.get('condizioni'):
            errore = 'Per proseguire accetta le condizioni del servizio'
        else:
            errore = _valida(dati['username'], pw, pw2)
            if not errore and Iscrizione.query.filter(db.func.lower(Iscrizione.username) == dati['username'].lower(),
                                                       Iscrizione.spazio_id.is_(None), Iscrizione.stato == 'attesa').first():
                errore = 'Nome utente già in uso da un\'iscrizione in corso: scegline un altro'
        if not errore:
            isc = Iscrizione(token=secrets.token_urlsafe(24), password_hash=generate_password_hash(pw), codice=nuovo_codice(),
                             telefono=telefono, **{k: v[:200] for k, v in dati.items()})
            db.session.add(isc)
            db.session.commit()
            if not configurato():   # senza Stripe: bonifico e attivazione manuale del gestore
                avvisa_gestore(isc)
                return render_template('abbonamento/richiesta.html', isc=isc, iban=current_app.config.get('IBAN') or '',
                                       intestatario=current_app.config.get('INTESTATARIO_CONTO') or 'Maurizio Gustinicchi Consulting',
                                       prezzo=current_app.config.get('PREZZO_TESTO') or '')
            corpo = {'mode': 'subscription', 'line_items[0][price]': current_app.config['STRIPE_PRICE_ID'],
                     'line_items[0][quantity]': 1, 'customer_email': isc.email, 'client_reference_id': isc.token,
                     'metadata[token]': isc.token, 'subscription_data[metadata][token]': isc.token,
                     'success_url': _url('abbonamento.grazie') + '?session_id={CHECKOUT_SESSION_ID}',
                     'cancel_url': _url('abbonamento.pagina') + '?annullato=1', 'locale': 'it',
                     'allow_promotion_codes': 'true', 'billing_address_collection': 'required',
                     'tax_id_collection[enabled]': 'true'}
            prova = current_app.config.get('GIORNI_PROVA')
            if prova:
                corpo['subscription_data[trial_period_days]'] = int(prova)
            try:
                s = _stripe('POST', '/checkout/sessions', corpo)
            except (RuntimeError, requests.RequestException) as e:
                current_app.logger.error('Stripe checkout: %s', e)
                errore = 'Il servizio di pagamento non risponde: riprova tra qualche minuto.'
            else:
                isc.sessione = s.get('id', '')
                db.session.commit()
                return redirect(s['url'], code=303)
    return render_template('abbonamento/pagina.html', errore=errore, dati=dati, attivo=configurato(),
                           prezzo=current_app.config.get('PREZZO_TESTO') or '', prova=current_app.config.get('GIORNI_PROVA'),
                           condizioni=current_app.config.get('URL_CONDIZIONI'), annullato=request.args.get('annullato'))


def attiva(token, cliente='', abbonamento='', fine_periodo=None, scadenza=None):
    """Crea lo spazio del cliente al pagamento (una volta sola). Ritorna lo spazio o None."""
    isc = db.session.get(Iscrizione, token or '')
    if not isc:
        return None
    if isc.spazio_id:
        return db.session.get(Spazio, isc.spazio_id)
    from piattaforma import crea_spazio
    username = isc.username
    if Utente.query.filter(db.func.lower(Utente.username) == username.lower()).first():
        username = f'{username}{secrets.randbelow(900) + 100}'   # raro: preso nel frattempo da un altro
    if scadenza is None:
        scadenza = (date.fromtimestamp(fine_periodo) if fine_periodo else date.today() + timedelta(days=31)) + timedelta(days=GRAZIA)
    sp = crea_spazio(isc.ragione_sociale, 'azienda', scadenza, isc.email, 'vuoto', None)
    db.session.add(Utente(nome=isc.nome, username=username, ruolo='admin', password_hash=isc.password_hash, spazio_id=sp.id))
    sp.stripe_cliente, sp.stripe_abbonamento = cliente or '', abbonamento or ''
    isc.spazio_id, isc.username, isc.stato = sp.id, username, 'attivata'
    db.session.commit()
    invia_email(isc.email, f'MASTER HOSPITALITY attivo — {isc.ragione_sociale}', f"""Gentile {isc.nome},

il suo spazio MASTER HOSPITALITY è attivo{(' fino al ' + scadenza.strftime('%d/%m/%Y')) if scadenza else ''}.

Accesso: {url_for('accesso.login', _external=True)}
Nome utente: {username}
Password: quella scelta nella richiesta

Dalla pagina Utenti può creare gli accessi per reception e governante ed esportare i dati in qualsiasi momento.

Maurizio Gustinicchi Consulting
info@mauriziogustinicchiconsulting.it
""", rispondi_a=current_app.config.get('MAIL_AVVISI'))
    return sp


def firma_valida(payload, intestazione, segreto, tolleranza=300):
    try:
        parti = dict(p.split('=', 1) for p in intestazione.split(','))
        t = int(parti['t'])
    except (ValueError, KeyError, AttributeError):
        return False
    attese = hmac.new(segreto.encode(), f'{t}.'.encode() + payload, hashlib.sha256).hexdigest()
    firme = [v for k, v in (p.split('=', 1) for p in intestazione.split(',')) if k == 'v1']
    return abs(time.time() - t) <= tolleranza and any(hmac.compare_digest(attese, f) for f in firme)


def _spazio_abbonamento(sub_id):
    return Spazio.query.filter_by(stripe_abbonamento=sub_id).first() if sub_id else None


@bp.route('/stripe/webhook', methods=['POST'])
def webhook():
    segreto = current_app.config.get('STRIPE_WEBHOOK_SECRET')
    payload = request.get_data()
    if not segreto or not firma_valida(payload, request.headers.get('Stripe-Signature', ''), segreto):
        return jsonify({'ok': False}), 400
    ev = json.loads(payload)
    tipo, ogg = ev.get('type'), (ev.get('data') or {}).get('object') or {}
    if tipo == 'checkout.session.completed':
        fine = None
        if ogg.get('subscription'):
            try:
                fine = _stripe('GET', f"/subscriptions/{ogg['subscription']}").get('current_period_end')
            except (RuntimeError, requests.RequestException):
                fine = None
        attiva(ogg.get('client_reference_id') or (ogg.get('metadata') or {}).get('token'), ogg.get('customer'),
               ogg.get('subscription'), fine)
    elif tipo == 'invoice.paid':
        sp = _spazio_abbonamento(ogg.get('subscription'))
        righe = ((ogg.get('lines') or {}).get('data') or [{}])
        fine = max([(r.get('period') or {}).get('end') or 0 for r in righe] + [0])
        if sp and fine:
            sp.scadenza = max(sp.scadenza or date.today(), date.fromtimestamp(fine) + timedelta(days=GRAZIA))
            sp.attivo = True
            db.session.commit()
    elif tipo == 'customer.subscription.deleted':
        sp = _spazio_abbonamento(ogg.get('id'))
        if sp:
            fine = ogg.get('current_period_end') or ogg.get('ended_at')
            sp.scadenza = date.fromtimestamp(fine) if fine else date.today()
            db.session.commit()
    return jsonify({'ok': True})


@bp.route('/abbonati/grazie')
def grazie():
    sp, username, errore = None, None, ''
    sid = request.args.get('session_id', '')
    isc = Iscrizione.query.filter_by(sessione=sid).first() if sid else None
    if isc and configurato():
        if not isc.spazio_id:   # se il webhook non è ancora arrivato, controlliamo noi il pagamento
            try:
                s = _stripe('GET', f'/checkout/sessions/{sid}')
                if s.get('status') == 'complete':
                    fine = None
                    if s.get('subscription'):
                        fine = _stripe('GET', f"/subscriptions/{s['subscription']}").get('current_period_end')
                    attiva(isc.token, s.get('customer'), s.get('subscription'), fine)
            except (RuntimeError, requests.RequestException) as e:
                current_app.logger.error('Stripe verifica: %s', e)
                errore = 'Pagamento ricevuto, attivazione in corso: ricarica la pagina tra un minuto.'
        db.session.refresh(isc)
        if isc.spazio_id:
            sp, username = db.session.get(Spazio, isc.spazio_id), isc.username
    return render_template('abbonamento/grazie.html', spazio=sp, username=username,
                           errore=errore or ('' if sp else 'Non trovo questo pagamento. Se hai pagato, scrivici: attiviamo noi lo spazio.'))


@bp.route('/abbonamento/gestisci')
def gestisci():
    """Portale clienti Stripe: carta, fatture, disdetta (per l'amministratore dello spazio)."""
    if not g.get('utente') or g.utente.ruolo != 'admin' or not g.get('spazio') or not g.spazio.stripe_cliente:
        abort(404)
    try:
        s = _stripe('POST', '/billing_portal/sessions', {'customer': g.spazio.stripe_cliente, 'return_url': _url('accesso.utenti')})
    except (RuntimeError, requests.RequestException) as e:
        current_app.logger.error('Stripe portale: %s', e)
        abort(502)
    return redirect(s['url'], code=303)
