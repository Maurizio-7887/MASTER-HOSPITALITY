"""Cassa: punto cassa (bar e ristorante, al banco o in camera), conti in casa, documenti fiscali, aziende con i
crediti (city ledger) e chiusura della giornata (night audit)."""
import json
from datetime import date

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

import cassa as cs
import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import DOCUMENTI, METODI, REP_ADDEBITO, Azienda, ChiusuraGiorno, Documento, Prenotazione, db

bp = Blueprint('cassa', __name__, template_folder='templates')


def _num(v, default=0.0):
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else default
    except ValueError:
        raise h.Errore('Controlla gli importi')


@bp.route('/cassa', methods=['GET', 'POST'])
def pagina():
    if request.method == 'POST' and _csrf():
        f = request.form
        try:
            rep = f.get('reparto') if f.get('reparto') in REP_ADDEBITO else 'bar'
            pid = f.get('prenotazione_id', type=int)
            if pid:      # addebito in camera
                p = db.get_or_404(Prenotazione, pid)
                h.aggiungi_addebito(p, (f.get('descrizione') or REP_ADDEBITO[rep]).strip(), _num(f.get('prezzo')), _num(f.get('quantita'), 1),
                                    rep, _num(f.get('iva'), 10), _num(f.get('costo')))
                msg = f'Addebitato in camera {p.camera.numero if p.camera else ""} a {p.ospite.nominativo}'
            else:
                v = cs.vendita(rep, (f.get('descrizione') or '').strip(), _num(f.get('quantita'), 1), _num(f.get('prezzo')), _num(f.get('iva'), 10),
                               _num(f.get('costo')), f.get('metodo') if f.get('metodo') in METODI else 'contanti')
                msg = f'Vendita registrata: {v.totale:.2f} € con ricevuta'.replace('.', ',')
            db.session.commit()
            _tutor()
            flash(msg, 'ok')
        except h.Errore as e:
            db.session.rollback()
            flash(str(e), 'err')
        return redirect(url_for('cassa.pagina'))
    d = cs.data_di_lavoro()
    return render_template('cassa/pagina.html', in_casa=cs.conti_in_casa(), oggi=cs.giornata(date.today()), data_lavoro=d,
                           REP=REP_ADDEBITO, METODI={k: v for k, v in METODI.items() if k not in ('ota', 'azienda')})


@bp.route('/documenti')
def documenti():
    da = _data(request.args.get('da'), date.today().replace(day=1))
    a = _data(request.args.get('a'), date.today())
    tipo = request.args.get('tipo') or ''
    q = Documento.query.filter(Documento.data >= da, Documento.data <= a)
    if tipo in DOCUMENTI:
        q = q.filter(Documento.tipo == tipo)
    righe = q.order_by(Documento.data, Documento.tipo, Documento.numero).all()
    tot = {'imponibile': sum(x.imponibile for x in righe), 'iva': sum(x.iva for x in righe), 'esente': sum(x.esente for x in righe),
           'totale': sum(x.totale for x in righe)}
    return render_template('cassa/documenti.html', righe=righe, da=da, a=a, tipo=tipo, DOCUMENTI=DOCUMENTI, tot=tot)


@bp.route('/documenti/<int:did>', methods=['GET', 'POST'])
def documento(did):
    d = db.get_or_404(Documento, did)
    if request.method == 'POST' and _csrf():
        try:
            if request.form.get('azione') == 'storna':
                nc = cs.nota_di_credito(d)
                db.session.commit()
                flash(f'Nota di credito {nc.sigla} emessa: ora puoi riemettere il documento corretto dalla prenotazione', 'ok')
                return redirect(url_for('cassa.documento', did=nc.id))
            if request.form.get('azione') == 'incassa':
                cs.incassa_credito(d)
                db.session.commit()
                flash('Credito incassato', 'ok')
        except h.Errore as e:
            db.session.rollback()
            flash(str(e), 'err')
        return redirect(url_for('cassa.documento', did=d.id))
    dati = json.loads(d.righe or '{}')
    return render_template('cassa/documento.html', d=d, dati=dati, DOCUMENTI=DOCUMENTI, s={k: h.par(k) for k in h.PARAMETRI})


@bp.route('/documenti/<int:did>.xml')
def documento_xml(did):
    d = db.get_or_404(Documento, did)
    if not d.xml:
        abort(404)
    nome = f'IT{(h.par("partita_iva") or "00000000000").replace(" ", "")}_{d.anno % 100:02d}{d.numero:03d}.xml'
    return Response(d.xml, mimetype='application/xml', headers={'Content-Disposition': f'attachment; filename={nome}'})


@bp.route('/aziende', methods=['GET', 'POST'])
def aziende():
    if request.method == 'POST' and _csrf():
        f = request.form
        a = db.session.get(Azienda, f.get('id', type=int)) if f.get('id') else Azienda()
        if f.get('azione') == 'elimina' and a.id:
            if Documento.query.filter_by(azienda_id=a.id).first() or Prenotazione.query.filter_by(azienda_id=a.id).first():
                flash('L\'azienda ha documenti o prenotazioni: non si elimina', 'err')
            else:
                db.session.delete(a)
                db.session.commit()
            return redirect(url_for('cassa.aziende'))
        if not (f.get('ragione_sociale') or '').strip():
            flash('Scrivi la ragione sociale', 'err')
            return redirect(url_for('cassa.aziende'))
        for k in ('ragione_sociale', 'partita_iva', 'codice_fiscale', 'codice_sdi', 'pec', 'indirizzo', 'cap', 'comune', 'provincia', 'nazione', 'email'):
            setattr(a, k, (f.get(k) or '').strip()[:160])
        a.codice_sdi = (a.codice_sdi or '0000000').upper()[:7]
        a.nazione = (a.nazione or 'IT').upper()[:2]
        a.giorni_pagamento = f.get('giorni_pagamento', type=int) or 30
        db.session.add(a)
        db.session.commit()
        flash('Azienda salvata', 'ok')
        return redirect(url_for('cassa.aziende'))
    crediti = cs.crediti_aperti()
    return render_template('cassa/aziende.html', aziende=Azienda.query.order_by(Azienda.ragione_sociale).all(), crediti=crediti,
                           totale_crediti=sum(x['d'].totale for x in crediti))


@bp.route('/chiusura', methods=['GET', 'POST'])
def chiusura():
    d = _data(request.values.get('data'), cs.data_di_lavoro())
    if request.method == 'POST' and _csrf():
        try:
            c = cs.chiudi(d, g.utente.nome if g.get('utente') else '', request.form.get('note') or '',
                          forza=bool(request.form.get('forza')) and g.utente.ruolo == 'admin')
            db.session.commit()
            _tutor()
            flash(f'Giornata del {c.data.strftime("%d/%m/%Y")} chiusa', 'ok')
            return redirect(url_for('cassa.chiusura'))
        except h.Errore as e:
            db.session.rollback()
            flash(str(e), 'err')
    chiusa = ChiusuraGiorno.query.filter_by(data=d).first()
    storico = ChiusuraGiorno.query.order_by(ChiusuraGiorno.data.desc()).limit(31).all()
    return render_template('cassa/chiusura.html', d=d, gi=cs.giornata(d), avvisi=cs.controlli_chiusura(d) if not chiusa else [],
                           chiusa=chiusa, storico=storico, METODI=METODI, json=json, data_lavoro=cs.data_di_lavoro())
