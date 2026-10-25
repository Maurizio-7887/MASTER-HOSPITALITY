"""Accesso al programma: utenti con password, ruoli e primo avvio guidato.

- Al primo avvio, se non esistono utenti, si crea l'amministratore: dalle variabili ADMIN_USERNAME e ADMIN_PASSWORD
  oppure dalla pagina /primo-avvio (disponibile solo finché non esiste nessun utente).
- Tutte le pagine richiedono l'accesso. La reception non vede revenue, controllo e struttura; i piani solo l'housekeeping.
- Le chiamate /api/ che modificano dati richiedono JSON: un modulo di un altro sito non può eseguirle.
"""
import hmac
import secrets
import time
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from models import Utente, db

bp = Blueprint('accesso', __name__)

RUOLI = {'admin': 'Direzione (amministratore)', 'revenue': 'Revenue manager', 'reception': 'Reception', 'piani': 'Governante / piani'}
LIBERI = {'accesso.login', 'accesso.primo_avvio', 'static', 'salute', 'abbonamento.pagina', 'abbonamento.grazie',
          'abbonamento.webhook'}
# endpoint permessi ai piani (governante, cameriere ai piani)
PIANI = {'accesso.logout', 'accesso.password', 'front.housekeeping', 'front.api_stato_camera', 'index', 'corso.api_tutor',
         'corso.api_verifica', 'corso.api_vai', 'corso.api_ricomincia'}
# aree vietate alla reception
SOLO_DIREZIONE = ('revenue.', 'controllo.', 'struttura.')
MIN_PASSWORD = 8


def csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_urlsafe(24)
    return session['_csrf']


def _csrf_ok():
    return hmac.compare_digest(request.form.get('_csrf', ''), session.get('_csrf', '') or '-')


def utente_corrente():
    uid = session.get('uid')
    if not uid:
        return None
    u = db.session.get(Utente, uid)
    return u if u and u.attivo else None


def proteggi(app):
    @app.before_request
    def _guardia():
        g.utente = None
        ep = request.endpoint or ''
        if ep in LIBERI:
            return None
        if Utente.query.count() == 0:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'programma da configurare'}), 401
            return redirect(url_for('accesso.primo_avvio'))
        u = utente_corrente()
        if not u:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'non autorizzato'}), 401
            return redirect(url_for('accesso.login', next=request.full_path if request.method == 'GET' else None))
        g.utente = u
        from models import Spazio
        sid = session.get('spazio') if (u.gestore and session.get('spazio')) else u.spazio_id
        g.spazio_id = sid
        g.spazio = db.session.get(Spazio, sid)
        if g.spazio is None:
            session.pop('spazio', None)
            g.spazio_id = u.spazio_id
            g.spazio = db.session.get(Spazio, u.spazio_id)
        if request.path.startswith('/api/') and request.method not in ('GET', 'HEAD') and not request.is_json:
            return jsonify({'ok': False, 'error': 'richiesta non valida'}), 400
        vietato = (u.ruolo == 'piani' and ep not in PIANI) or (u.ruolo == 'reception' and ep.startswith(SOLO_DIREZIONE))
        if vietato:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'operazione non consentita al tuo ruolo'}), 403
            return redirect(url_for('front.housekeeping' if u.ruolo == 'piani' else 'index'))
        if ep.startswith('accesso.utent') and u.ruolo != 'admin':
            abort(403)
        if ep.startswith('piattaforma.') and not u.gestore:
            abort(403)
        # abbonamento scaduto o spazio sospeso: sola lettura (il gestore può sempre intervenire)
        if g.spazio and g.spazio.scaduto() and request.method not in ('GET', 'HEAD') and not u.gestore \
                and ep not in ('accesso.logout', 'accesso.password') and not ep.startswith('abbonamento.'):
            msg = 'Abbonamento scaduto: il programma è in sola lettura. Rinnova l\'abbonamento per tornare a modificare.'
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': msg}), 403
            flash(msg, 'err')
            return redirect(request.referrer or url_for('index'))
        return None

    @app.context_processor
    def _ctx():
        sp = g.get('spazio')
        avviso = None
        if sp is not None:
            from datetime import date
            if sp.scaduto():
                avviso = ('err', 'Abbonamento scaduto o spazio sospeso: puoi consultare i dati ma non modificarli.')
            elif sp.scadenza and (sp.scadenza - date.today()).days <= 7:
                avviso = ('', f'L\'abbonamento scade il {sp.scadenza.strftime("%d/%m/%Y")}.')
        richieste = 0
        if g.get('utente') is not None and g.utente.gestore:
            from abbonamento import Iscrizione
            richieste = Iscrizione.query.filter_by(stato='attesa').count()
        return {'utente': g.get('utente'), 'csrf_token': csrf_token, 'RUOLI': RUOLI, 'spazio': sp, 'avviso_spazio': avviso,
                'richieste_attivazione': richieste,
                'fuori_spazio': bool(g.get('utente') and g.utente.gestore and g.get('spazio_id') != g.utente.spazio_id)}


def _sicuro(dest):
    return dest if dest and dest.startswith('/') and not dest.startswith('//') else None


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if Utente.query.count() == 0:
        return redirect(url_for('accesso.primo_avvio'))
    errore = ''
    if request.method == 'POST':
        u = Utente.query.filter(db.func.lower(Utente.username) == (request.form.get('username') or '').strip().lower()).first()
        if u and u.attivo and check_password_hash(u.password_hash, request.form.get('password') or ''):
            session.clear()
            session['uid'] = u.id
            session.permanent = True
            u.ultimo_accesso = datetime.utcnow()
            db.session.commit()
            if u.ruolo == 'piani':
                return redirect(url_for('front.housekeeping'))
            return redirect(_sicuro(request.form.get('next')) or url_for('index'))
        time.sleep(0.6)  # rallenta i tentativi a raffica
        errore = 'Nome utente o password non corretti'
    return render_template('accesso/login.html', errore=errore, next=_sicuro(request.args.get('next') or request.form.get('next')))


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('accesso.login'))


@bp.route('/primo-avvio', methods=['GET', 'POST'])
def primo_avvio():
    if Utente.query.count() > 0:
        return redirect(url_for('accesso.login'))
    errore = ''
    if request.method == 'POST':
        if not _csrf_ok():
            errore = 'Sessione scaduta: riprova'
        else:
            errore = _valida(request.form.get('username'), request.form.get('password'), request.form.get('password2'))
            if not errore:
                u = Utente(nome=(request.form.get('nome') or 'Amministratore').strip()[:120], ruolo='admin',
                           username=request.form['username'].strip()[:60],
                           password_hash=generate_password_hash(request.form['password']))
                db.session.add(u)
                db.session.commit()
                session.clear()
                session['uid'] = u.id
                return redirect(url_for('index'))
    return render_template('accesso/primo_avvio.html', errore=errore)


def _valida(username, password, password2=None, escludi_id=None):
    username = (username or '').strip()
    if len(username) < 3:
        return 'Il nome utente deve avere almeno 3 caratteri'
    q = Utente.query.filter(db.func.lower(Utente.username) == username.lower())
    if escludi_id:
        q = q.filter(Utente.id != escludi_id)
    if q.first():
        return 'Nome utente già usato'
    if password is not None:
        if len(password) < MIN_PASSWORD:
            return f'La password deve avere almeno {MIN_PASSWORD} caratteri'
        if password2 is not None and password != password2:
            return 'Le due password non coincidono'
    return ''


@bp.route('/utenti', methods=['GET', 'POST'])
def utenti():
    if request.method == 'POST':
        if not _csrf_ok():
            flash('Sessione scaduta: riprova', 'err')
            return redirect(url_for('accesso.utenti'))
        azione = request.form.get('azione')
        if azione == 'carica_demo':
            from models import Impostazione, Prenotazione, TipoCamera
            if Prenotazione.query.first() or TipoCamera.query.first():
                flash('L\'archivio contiene già dati: i dati dimostrativi si caricano solo con l\'archivio vuoto', 'err')
            else:
                from seed_demo import carica_demo
                Impostazione.scrivi('demo_eliminati', '')
                db.session.commit()
                carica_demo()
                flash('Dati dimostrativi caricati', 'ok')
            return redirect(url_for('accesso.utenti'))
        if azione == 'elimina_demo':
            if (request.form.get('conferma') or '').strip().upper() != 'ELIMINA':
                flash('Scrivi ELIMINA per confermare', 'err')
            else:
                from seed_demo import elimina_demo
                elimina_demo()
                flash('Dati dimostrativi eliminati: inserisci la tua struttura da Struttura', 'ok')
            return redirect(url_for('accesso.utenti'))
        if azione == 'nuovo':
            err = _valida(request.form.get('username'), request.form.get('password'))
            ruolo = request.form.get('ruolo') if request.form.get('ruolo') in RUOLI else 'reception'
            if err:
                flash(err, 'err')
            else:
                db.session.add(Utente(nome=(request.form.get('nome') or request.form['username']).strip()[:120], ruolo=ruolo,
                                      username=request.form['username'].strip()[:60], spazio_id=g.spazio_id,
                                      password_hash=generate_password_hash(request.form['password'])))
                db.session.commit()
                flash('Utente creato', 'ok')
        else:
            u = db.get_or_404(Utente, request.form.get('id', type=int))
            if u.spazio_id != g.spazio_id:
                abort(404)
            if azione == 'ruolo' and request.form.get('ruolo') in RUOLI:
                if u.id == g.utente.id and request.form['ruolo'] != 'admin':
                    flash('Non puoi togliere a te stesso il ruolo di amministratore', 'err')
                else:
                    u.ruolo = request.form['ruolo']
            elif azione == 'password':
                pw = request.form.get('password') or ''
                if len(pw) < MIN_PASSWORD:
                    flash(f'La password deve avere almeno {MIN_PASSWORD} caratteri', 'err')
                else:
                    u.password_hash = generate_password_hash(pw)
                    flash(f'Password di {u.username} cambiata', 'ok')
            elif azione == 'attivo':
                if u.id == g.utente.id:
                    flash('Non puoi disattivare te stesso', 'err')
                else:
                    u.attivo = not u.attivo
            db.session.commit()
        return redirect(url_for('accesso.utenti'))
    from models import Impostazione, Prenotazione, TipoCamera
    return render_template('accesso/utenti.html', utenti=Utente.query.filter_by(spazio_id=g.spazio_id).order_by(Utente.attivo.desc(), Utente.nome).all(),
                           ci_sono_dati=bool(Prenotazione.query.first() or TipoCamera.query.first()),
                           demo_eliminati=bool(Impostazione.leggi('demo_eliminati')))


@bp.route('/password', methods=['GET', 'POST'])
def password():
    errore = ''
    if request.method == 'POST':
        u = g.utente
        if not _csrf_ok():
            errore = 'Sessione scaduta: riprova'
        elif not check_password_hash(u.password_hash, request.form.get('attuale') or ''):
            errore = 'La password attuale non è corretta'
        else:
            errore = _valida(u.username, request.form.get('nuova'), request.form.get('nuova2'), escludi_id=u.id)
            if not errore:
                u.password_hash = generate_password_hash(request.form['nuova'])
                db.session.commit()
                flash('Password cambiata', 'ok')
                return redirect(url_for('index'))
    return render_template('accesso/password.html', errore=errore)


def crea_admin_da_ambiente(app):
    """ADMIN_USERNAME e ADMIN_PASSWORD (variabili Railway): crea l'amministratore se non esiste nessun utente."""
    nome, pw = app.config.get('ADMIN_USERNAME'), app.config.get('ADMIN_PASSWORD')
    if nome:   # l'amministratore delle variabili d'ambiente è anche il gestore della piattaforma
        u = Utente.query.filter(db.func.lower(Utente.username) == nome.strip().lower()).first()
        if u and not u.gestore:
            u.gestore = True
            db.session.commit()
    if nome and pw and Utente.query.count() == 0:
        if len(pw) < MIN_PASSWORD:
            app.logger.warning('ADMIN_PASSWORD troppo corta: amministratore non creato')
            return
        db.session.add(Utente(nome='Amministratore', username=nome.strip()[:60], ruolo='admin', gestore=True, spazio_id=1,
                              password_hash=generate_password_hash(pw)))
        db.session.commit()
