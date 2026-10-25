"""Gestione della piattaforma (solo per il gestore): spazi di lavoro dei clienti e degli allievi,
scadenze degli abbonamenti, classi dei corsi. Esportazione dei dati per l'amministratore di ogni spazio."""
import csv
import io
import secrets
import zipfile
from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from models import Prenotazione, Spazio, Utente, db
from spazi import nello_spazio

bp = Blueprint('piattaforma', __name__)

TIPI = {'azienda': 'Azienda cliente', 'formazione': 'Allievo / formazione', 'demo': 'Dimostrazione'}
DATI = {'vuoto': 'Struttura da configurare (piani tariffari e canali di base)', 'demo': 'Hotel dimostrativo (Hotel Le Logge, Firenze)',
        'corso': 'Base del corso di hotel management'}


def password_casuale():
    alfabeto = 'abcdefghjkmnpqrstuvwxyz23456789'
    return '-'.join(''.join(secrets.choice(alfabeto) for _ in range(4)) for _ in range(3))


def _csrf():
    from accesso import _csrf_ok
    if not _csrf_ok():
        flash('Sessione scaduta: riprova', 'err')
        return False
    return True


def crea_spazio(nome, tipo='azienda', scadenza=None, email='', dati='vuoto', admin=None):
    """Crea uno spazio con il suo amministratore. admin = (nome, username, password). Ritorna lo spazio."""
    from accesso import _valida
    if admin:
        err = _valida(admin[1], admin[2])
        if err:
            raise ValueError(err)
    sp = Spazio(nome=nome.strip()[:120] or 'Nuovo spazio', tipo=tipo if tipo in TIPI else 'azienda', scadenza=scadenza,
                email=(email or '').strip()[:200])
    db.session.add(sp)
    db.session.flush()
    if admin:
        db.session.add(Utente(nome=admin[0].strip()[:120] or admin[1], username=admin[1].strip()[:60], ruolo='admin',
                              password_hash=generate_password_hash(admin[2]), spazio_id=sp.id))
    db.session.commit()
    with nello_spazio(sp.id):
        if dati == 'demo':
            from seed_demo import carica_demo
            carica_demo()
        elif dati == 'corso':
            from corso import carica_base_corso
            carica_base_corso()
        else:
            from seed_demo import struttura_iniziale
            struttura_iniziale(nome)
    return sp


def _data(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


@bp.route('/spazi', methods=['GET', 'POST'])
def pagina():
    credenziali = None
    if request.method == 'POST' and _csrf():
        azione = request.form.get('azione')
        try:
            if azione in ('attiva_richiesta', 'rifiuta_richiesta'):
                from abbonamento import Iscrizione, attiva
                isc = db.get_or_404(Iscrizione, request.form.get('token', ''))
                if isc.stato != 'attesa':
                    raise ValueError('richiesta già gestita')
                if azione == 'rifiuta_richiesta':
                    isc.stato = 'rifiutata'
                    db.session.commit()
                    flash(f'Richiesta {isc.codice} archiviata', 'ok')
                else:
                    piu = request.form.get('piu')
                    scad = (date.today() + timedelta(days=366) if piu == 'anno' else _data(request.form.get('data'))
                            if piu == 'data' else date.today() + timedelta(days=31))
                    sp = attiva(isc.token, scadenza=scad)
                    flash(f'Spazio «{sp.nome}» attivato fino al {scad.strftime("%d/%m/%Y") if scad else "—"}: il cliente entra con '
                          f'il nome utente «{isc.username}». Scrivigli a {isc.email}.', 'ok')
            elif azione == 'nuovo':
                sp = crea_spazio(request.form.get('nome', ''), request.form.get('tipo'), _data(request.form.get('scadenza')),
                                 request.form.get('email'), request.form.get('dati'),
                                 (request.form.get('admin_nome', ''), request.form.get('admin_username', ''),
                                  request.form.get('admin_password', '')))
                flash(f'Spazio «{sp.nome}» creato', 'ok')
            elif azione == 'classe':
                n = max(1, min(60, request.form.get('numero', type=int) or 1))
                prefisso = ''.join(ch for ch in (request.form.get('prefisso') or 'allievo').lower() if ch.isalnum() or ch in '._-')[:30] or 'allievo'
                scad = _data(request.form.get('scadenza'))
                corso = (request.form.get('corso') or 'Corso di hotel management').strip()[:80]
                credenziali = []
                k = 1
                while len(credenziali) < n:
                    username = f'{prefisso}{k:02d}'
                    k += 1
                    if Utente.query.filter(db.func.lower(Utente.username) == username).first():
                        continue
                    pw = password_casuale()
                    crea_spazio(f'{corso} · {username}', 'formazione', scad, '', 'corso', (f'Allievo {username}', username, pw))
                    credenziali.append((username, pw))
                flash(f'Creati {n} spazi allievo: stampa o copia le credenziali qui sotto, non verranno più mostrate.', 'ok')
            else:
                sp = db.get_or_404(Spazio, request.form.get('id', type=int))
                if azione == 'scadenza':
                    piu = request.form.get('piu')
                    base = max(sp.scadenza or date.today(), date.today())
                    sp.scadenza = None if piu == 'nessuna' else (base + timedelta(days=31) if piu == 'mese' else
                                                                 base + timedelta(days=366) if piu == 'anno' else _data(request.form.get('data')))
                    flash(f'Scadenza di «{sp.nome}» aggiornata', 'ok')
                elif azione == 'attivo':
                    if sp.id == 1:
                        raise ValueError('lo spazio principale non si può sospendere')
                    sp.attivo = not sp.attivo
                elif azione == 'entra':
                    session['spazio'] = sp.id
                    db.session.commit()
                    return redirect(url_for('index'))
                db.session.commit()
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'err')
        if not credenziali:
            return redirect(url_for('piattaforma.pagina'))
    spazi = Spazio.query.order_by(Spazio.tipo, Spazio.nome).all()
    utenti = {}
    for u in Utente.query.all():
        utenti.setdefault(u.spazio_id, []).append(u)
    commesse = dict(db.session.query(Prenotazione.spazio_id, db.func.count(Prenotazione.id))
                    .execution_options(tutti_gli_spazi=True).group_by(Prenotazione.spazio_id).all())
    from abbonamento import Iscrizione
    richieste = Iscrizione.query.filter_by(stato='attesa').order_by(Iscrizione.creato_il).all()
    return render_template('piattaforma/spazi.html', richieste=richieste, spazi=spazi, utenti=utenti, commesse=commesse, TIPI=TIPI, DATI=DATI,
                           credenziali=credenziali, oggi=date.today())


@bp.route('/spazi/torna')
def torna():
    session.pop('spazio', None)
    return redirect(url_for('piattaforma.pagina'))


@bp.route('/esporta.zip')
def esporta():
    """Tutti i dati dello spazio in CSV (uno per tabella), per l'amministratore: se il cliente se ne va, i dati sono suoi."""
    if not g.get('utente') or g.utente.ruolo != 'admin':
        abort(403)
    import models as m
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for mod in [m.TipoCamera, m.Camera, m.PianoTariffario, m.Stagione, m.Restrizione, m.TariffaGiorno, m.Canale, m.Evento,
                    m.CostoFisso, m.Ospite, m.Prenotazione, m.NotteVenduta, m.Addebito, m.Pagamento, m.Alloggiato]:
            colonne = [c.name for c in mod.__table__.columns]
            out = io.StringIO()
            w = csv.writer(out, delimiter=';')
            w.writerow(colonne)
            for r in mod.query.all():
                w.writerow(['' if getattr(r, c) is None else getattr(r, c) for c in colonne])
            z.writestr(f'{mod.__tablename__}.csv', '\ufeff' + out.getvalue())
    nome = (g.spazio.nome if g.get('spazio') else 'master-hospitality').replace(' ', '_')
    return Response(buf.getvalue(), mimetype='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="{nome}_{date.today().isoformat()}.zip"'})
