"""Controllo di gestione: indicatori, conto economico USALI, margine per canale e per prenotazione."""
import csv
import io

from flask import Blueprint, Response, current_app, jsonify, render_template, request

import controllo as ctl

bp = Blueprint('controllo', __name__)


@bp.route('/controllo')
def pagina():
    da, a = ctl.periodo(request.args)
    r = ctl.analisi(da, a)
    serie = ctl.andamento(da, a) if (a - da).days <= 120 else []
    return render_template('controllo/pagina.html', r=r, serie=serie, da=da, a=a, ai=bool(current_app.config.get('GEMINI_API_KEY')))


@bp.route('/controllo/prenotazioni.csv')
def csv_prenotazioni():
    da, a = ctl.periodo(request.args)
    r = ctl.analisi(da, a)
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['codice', 'ospite', 'canale', 'piano', 'tipologia', 'arrivo', 'partenza', 'notti nel periodo', 'ricavi netti', 'costo camere',
                'costo colazioni', 'costo extra', 'commissione', 'margine', 'margine per notte', 'margine %'])
    f = lambda v: '' if v is None else f'{v:.2f}'.replace('.', ',')   # noqa: E731
    for e in r['prenotazioni']:
        p = e['p']
        w.writerow([p.codice, p.ospite.nominativo, p.canale.nome, p.piano.codice, p.tipo.codice, p.arrivo.strftime('%d/%m/%Y'),
                    p.partenza.strftime('%d/%m/%Y'), e['notti'], f(e['ricavi']), f(e['costo_camera']), f(e['costo_colazione']),
                    f(e['costo_extra']), f(e['commissione']), f(e['margine']), f(e['margine_notte']),
                    f(e['margine_pct'] * 100 if e['margine_pct'] is not None else None)])
    return Response('﻿' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="margini_{da.isoformat()}_{a.isoformat()}.csv"'})


@bp.route('/api/controllo/commento', methods=['POST'])
def api_commento():
    import ai
    da, a = ctl.periodo(request.get_json(silent=True) or {})
    r = ctl.analisi(da, a)
    k, u = r['kpi'], r['usali']
    canali = '\n'.join(f"- {n}: {c['notti']} notti, ADR {c['adr'] or 0:.0f}€, commissioni {c['commissione']:.0f}€, margine per notte {c['margine_notte'] or 0:.0f}€"
                       for n, c in r['canali'].items())
    testo, errore = ai.chiedi(
        'Sei un controller esperto di hotel italiani (schema USALI). Commenta in italiano i risultati del periodo per la direzione: '
        'cosa va bene, cosa preoccupa, tre azioni concrete con l\'effetto atteso in euro. Massimo 12 righe.\n\n'
        f"Periodo {da} – {a}. Occupazione {(k['occupazione'] or 0) * 100:.1f}%, ADR {k['adr'] or 0:.2f}€, RevPAR {k['revpar'] or 0:.2f}€, "
        f"TRevPAR {k['trevpar'] or 0:.2f}€, GOP {k['gop']:.0f}€ ({(k['gop_pct'] or 0) * 100:.1f}% dei ricavi), GOPPAR {k['goppar'] or 0:.2f}€, "
        f"CPOR {k['cpor'] or 0:.2f}€, commissioni {k['commissioni']:.0f}€, EBITDA {k['ebitda']:.0f}€.\n"
        f"Reparto camere: ricavi {u['camere']['ricavi']:.0f}€, utile {u['camere']['utile']:.0f}€. F&B: ricavi {u['fb']['ricavi']:.0f}€, "
        f"utile {u['fb']['utile']:.0f}€.\nCanali:\n{canali}")
    if errore:
        return jsonify({'ok': False, 'error': errore}), 400
    return jsonify({'ok': True, 'testo': testo})
