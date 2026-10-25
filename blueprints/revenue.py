"""DynaPrice: calendario dei prezzi con occupazione, previsione e prezzo suggerito; rischio di cancellazione;
simulatore dei canali; commento dell'AI ai numeri."""
from datetime import date, timedelta

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

import dynaprice as dp
from models import TipoCamera, db

bp = Blueprint('revenue', __name__)
GIORNI = ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom']


def _da():
    try:
        return date.fromisoformat(request.args.get('da')) if request.args.get('da') else date.today()
    except ValueError:
        return date.today()


@bp.route('/revenue')
def pagina():
    da = _da()
    n = max(7, min(90, request.args.get('giorni', type=int) or 28))
    gr = dp.griglia(da, n)
    return render_template('revenue/pagina.html', g=gr, da=da, n=n, GIORNI=GIORNI, curva=dp.CURVA, scala=dp.SCALA,
                           ai=bool(current_app.config.get('GEMINI_API_KEY')))


@bp.route('/api/revenue/applica', methods=['POST'])
def api_applica():
    d = request.get_json(silent=True) or {}
    prezzi = [(x.get('tipo_id'), x.get('data'), x.get('prezzo')) for x in d.get('prezzi') or []]
    try:
        n = dp.applica(prezzi)
    except (ValueError, TypeError):
        db.session.rollback()
        return jsonify({'ok': False, 'error': 'Prezzi non validi'}), 400
    db.session.commit()
    _tutor()
    return jsonify({'ok': True, 'applicati': n})


@bp.route('/api/revenue/listino', methods=['POST'])
def api_listino():
    d = request.get_json(silent=True) or {}
    try:
        da, a = date.fromisoformat(d['da']), date.fromisoformat(d['a'])
    except (KeyError, ValueError):
        return jsonify({'ok': False, 'error': 'Date non valide'}), 400
    n = dp.torna_al_listino(d.get('tipo_id'), da, a)
    db.session.commit()
    return jsonify({'ok': True, 'tolti': n})


@bp.route('/revenue/rischio')
def rischio():
    return render_template('revenue/rischio.html', righe=dp.a_rischio())


@bp.route('/revenue/simula', methods=['POST'])
def simula():
    from accesso import _csrf_ok
    if not _csrf_ok():
        flash('Sessione scaduta: riprova', 'err')
        return redirect(url_for('revenue.pagina'))
    giorni = max(1, min(14, request.form.get('giorni', type=int) or 7))
    e = dp.simula_vendite(giorni)
    if e.get('errore'):
        db.session.rollback()
        flash(e['errore'], 'err')
    else:
        from corso import attivo
        from models import Impostazione
        if attivo():
            Impostazione.scrivi('corso_simulazioni', str(int(Impostazione.leggi('corso_simulazioni') or 0) + 1))
        db.session.commit()
        _tutor()
        flash(f'Simulati {giorni} giorni di vendite: {e["richieste"]} richieste, {e["prenotate"]} prenotazioni ({e["notti"]} notti, '
              f'{e["ricavo"]:.0f} €); perse per prezzo {e["perse_prezzo"]}, per tutto esaurito {e["esaurito"]}.', 'ok')
    return redirect(url_for('revenue.pagina', da=request.form.get('da') or None))


@bp.route('/api/revenue/commento', methods=['POST'])
def api_commento():
    """Commento del revenue manager virtuale (Gemini) sui prossimi 28 giorni."""
    import ai
    gr = dp.griglia(date.today(), 28)
    righe = [f"{r['data']} {GIORNI[r['giorno']]}: occupazione {r['occ'] * 100:.0f}%, prevista {r['previsione'] * 100:.0f}%, pickup 7gg {r['pickup']}, "
             f"ADR {r['adr'] or 0:.0f}€, eventi {', '.join(r['eventi']) or '-'}, prezzi " +
             '; '.join(f"{t['codice']} {c['prezzo']:.0f}→{c['suggerito']:.0f}" for t, c in zip(gr['tipi'], r['celle'])) for r in gr['righe']]
    testo, errore = ai.chiedi(
        'Sei un revenue manager esperto di hotel italiani. Commenta in italiano, in modo pratico, i dati dei prossimi 28 giorni: '
        'dove alzare o abbassare i prezzi, dove mettere restrizioni di soggiorno minimo, quali giorni sono a rischio invenduto e cosa fare '
        'sui canali. Massimo 12 righe, elenco puntato, numeri concreti.\n\n' + '\n'.join(righe))
    if errore:
        return jsonify({'ok': False, 'error': errore}), 400
    return jsonify({'ok': True, 'testo': testo})


def _tutor():
    try:
        from corso import controlla_in_silenzio
        controlla_in_silenzio()
    except Exception:   # noqa: BLE001
        db.session.rollback()
