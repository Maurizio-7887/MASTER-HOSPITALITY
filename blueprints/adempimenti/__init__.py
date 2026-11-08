"""Adempimenti: Alloggiati Web e imposta di soggiorno."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('adempimenti', __name__, template_folder='templates')

def _pad(v, n):
    return (v or '')[:n].ljust(n)


@bp.route('/alloggiati')
def alloggiati():
    d = _data(request.args.get('data'), date.today())
    pren = Prenotazione.query.filter(Prenotazione.arrivo == d, Prenotazione.stato.in_(('in_casa', 'partita'))).order_by(Prenotazione.id).all()
    if request.args.get('formato') == 'txt':
        righe = []
        for p in pren:
            for a in p.alloggiati:
                righe.append(_pad(a.tipo, 2) + p.arrivo.strftime('%d/%m/%Y') + str(min(p.notti, 30)).rjust(2, '0') + _pad(a.cognome.upper(), 50)
                             + _pad(a.nome.upper(), 30) + ('1' if a.sesso == 'M' else '2' if a.sesso == 'F' else ' ')
                             + (a.data_nascita.strftime('%d/%m/%Y') if a.data_nascita else ' ' * 10) + _pad(a.luogo_nascita, 9)
                             + _pad(a.provincia_nascita, 2) + _pad(a.stato_nascita, 9) + _pad(a.cittadinanza, 9)
                             + (_pad(a.tipo_documento, 5) + _pad(a.numero_documento, 20) + _pad(a.luogo_rilascio, 9) if a.tipo in ('16', '17', '18') else ' ' * 34))
        return Response('\r\n'.join(righe) + ('\r\n' if righe else ''), mimetype='text/plain',
                        headers={'Content-Disposition': f'attachment; filename="alloggiati_{d.isoformat()}.txt"'})
    mancanti = [p for p in pren if len(p.alloggiati) < p.persone]
    return render_template('adempimenti/alloggiati.html', pren=pren, d=d, mancanti=mancanti)


@bp.route('/imposta-soggiorno')
def imposta():
    oggi = date.today()
    mese = request.args.get('mese') or oggi.strftime('%Y-%m')
    try:
        da = date.fromisoformat(mese + '-01')
    except ValueError:
        da = oggi.replace(day=1)
    a = (da.replace(day=28) + timedelta(days=4)).replace(day=1)
    righe, tot = [], {'persone': 0, 'pernottamenti': 0, 'tassati': 0, 'esenti': 0, 'importo': 0.0}
    for p in Prenotazione.query.filter(Prenotazione.partenza > da, Prenotazione.partenza <= a, Prenotazione.stato.in_(('partita', 'in_casa'))).order_by(Prenotazione.partenza).all():
        t = h.tassa(p)
        persone = len(p.alloggiati) or p.persone
        righe.append({'p': p, 't': t, 'persone': persone, 'esenti': max(0, persone - t['persone'])})
        tot['persone'] += persone
        tot['pernottamenti'] += persone * p.notti
        tot['tassati'] += t['persone'] * t['notti']
        tot['esenti'] += max(0, persone - t['persone'])
        tot['importo'] += t['totale']
    return render_template('adempimenti/imposta.html', righe=righe, tot=tot, mese=da.strftime('%Y-%m'), da=da, importo=h.par('tassa_importo'),
                           max_notti=h.par('tassa_max_notti'), eta=h.par('tassa_eta_esenti'), comune=h.par('comune'))
