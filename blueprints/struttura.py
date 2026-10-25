"""Struttura: parametri, tipologie, camere, piani tariffari, stagioni, canali, eventi, restrizioni e costi fissi."""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

import hotel as h
from models import (REPARTI, Camera, Canale, CostoFisso, Evento, Impostazione, PianoTariffario, Prenotazione, Restrizione,
                    Stagione, TipoCamera, db)

bp = Blueprint('struttura', __name__)

# entità: (modello, titolo, campi [(nome, etichetta, tipo, opzioni)])
ENTITA = {
    'tipo': (TipoCamera, 'Tipologie di camera', [
        ('codice', 'Codice', 'str', None), ('nome', 'Nome', 'str', None), ('posti_base', 'Persone nel prezzo', 'int', None),
        ('posti_max', 'Persone max', 'int', None), ('prezzo_base', 'Listino a notte €', 'float', None),
        ('supplemento_persona', 'Suppl. persona €', 'float', None), ('costo_variabile', 'Costo camera venduta €', 'float', None),
        ('prezzo_minimo', 'Prezzo min €', 'float?', None), ('prezzo_massimo', 'Prezzo max €', 'float?', None),
        ('ordine', 'Ordine', 'int', None), ('descrizione', 'Descrizione', 'str', None)]),
    'camera': (Camera, 'Camere', [
        ('numero', 'Numero', 'str', None), ('tipo_id', 'Tipologia', 'tipo', None), ('piano', 'Piano', 'str', None),
        ('attiva', 'In uso', 'bool', None), ('note', 'Note', 'str', None)]),
    'piano': (PianoTariffario, 'Piani tariffari', [
        ('codice', 'Codice', 'str', None), ('nome', 'Nome', 'str', None), ('variazione_pct', 'Variazione sul listino %', 'float', None),
        ('colazione', 'Colazione inclusa', 'bool', None), ('prezzo_colazione', 'Colazione € a persona', 'float', None),
        ('costo_colazione', 'Costo colazione €', 'float', None), ('rimborsabile', 'Rimborsabile', 'bool', None),
        ('giorni_cancellazione', 'Canc. gratuita fino a giorni prima', 'int', None), ('caparra_pct', 'Caparra %', 'float', None),
        ('attivo', 'In vendita', 'bool', None)]),
    'stagione': (Stagione, 'Stagioni', [
        ('nome', 'Nome', 'str', None), ('dal', 'Dal', 'date', None), ('al', 'Al (compreso)', 'date', None),
        ('coefficiente', 'Coefficiente (1 = listino)', 'float', None)]),
    'canale': (Canale, 'Canali di vendita', [
        ('nome', 'Nome', 'str', None), ('tipo', 'Tipo', 'scelta', {'diretto': 'Diretto', 'ota': 'Portale (OTA)', 'agenzia': 'Agenzia / TO'}),
        ('commissione_pct', 'Commissione %', 'float', None), ('colore', 'Colore', 'color', None), ('attivo', 'Attivo', 'bool', None)]),
    'evento': (Evento, 'Eventi e fiere', [
        ('nome', 'Nome', 'str', None), ('dal', 'Dal', 'date', None), ('al', 'Al', 'date', None),
        ('impatto_pct', 'Effetto sulla domanda %', 'float', None), ('note', 'Note', 'str', None)]),
    'restrizione': (Restrizione, 'Restrizioni', [
        ('data', 'Data', 'date', None), ('tipo_id', 'Tipologia (vuoto = tutte)', 'tipo?', None), ('min_notti', 'Soggiorno minimo', 'int', None),
        ('chiuso_arrivo', 'Chiuso all\'arrivo', 'bool', None), ('chiuso', 'Chiuso alla vendita', 'bool', None)]),
    'costo': (CostoFisso, 'Costi fissi mensili', [
        ('voce', 'Voce', 'str', None), ('reparto', 'Reparto (USALI)', 'scelta', REPARTI), ('importo_mensile', 'Importo al mese €', 'float', None)]),
}


def _valore(tipo, v):
    v = (v or '').strip()
    if tipo == 'bool':
        return bool(v)
    if tipo in ('int',):
        return int(v or 0)
    if tipo in ('float',):
        return float(v.replace(',', '.') or 0)
    if tipo == 'float?':
        return float(v.replace(',', '.')) if v else None
    if tipo == 'date':
        return date.fromisoformat(v)
    if tipo in ('tipo', 'tipo?'):
        return int(v) if v else None
    return v[:300]


def _controlla(chi, obj):
    if chi == 'tipo':
        if obj.posti_max < obj.posti_base or obj.posti_base < 1:
            raise h.Errore('Le persone massime devono essere almeno quelle comprese nel prezzo')
        if obj.prezzo_base <= 0:
            raise h.Errore('Il listino deve essere maggiore di zero')
    if chi == 'camera' and not obj.tipo_id:
        raise h.Errore('Scegli la tipologia della camera')
    if chi in ('stagione', 'evento') and obj.al < obj.dal:
        raise h.Errore('La data finale è prima di quella iniziale')
    if chi == 'stagione' and not (0.3 <= obj.coefficiente <= 4):
        raise h.Errore('Coefficiente tra 0,3 e 4 (1 = listino)')
    if chi == 'canale' and not (0 <= obj.commissione_pct < 60):
        raise h.Errore('Commissione tra 0 e 60%')
    for campo in ('codice', 'nome', 'numero', 'voce'):
        if hasattr(obj, campo) and getattr(obj, campo) == '' and campo in [c[0] for c in ENTITA[chi][2]]:
            raise h.Errore('Compila tutti i campi obbligatori')


@bp.route('/struttura', methods=['GET', 'POST'])
def pagina():
    sez = request.args.get('sez') or request.form.get('sez') or 'parametri'
    if request.method == 'POST':
        from accesso import _csrf_ok
        if not _csrf_ok():
            flash('Sessione scaduta: riprova', 'err')
            return redirect(url_for('struttura.pagina', sez=sez))
        az = request.form.get('azione')
        try:
            if az == 'parametri':
                for k, (et, default, tipo) in h.PARAMETRI.items():
                    if k in request.form:
                        v = request.form.get(k, '').strip().replace(',', '.') if tipo is not str else request.form.get(k, '').strip()
                        if tipo is not str and v:
                            tipo(v)
                        Impostazione.scrivi(k, v[:200])
                msg = 'Parametri salvati'
            elif az in ('salva', 'elimina') and sez in ENTITA:
                modello, titolo, campi = ENTITA[sez]
                oid = request.form.get('id', type=int)
                obj = db.get_or_404(modello, oid) if oid else modello()
                if az == 'elimina':
                    if sez == 'tipo' and Prenotazione.query.filter_by(tipo_id=obj.id).first():
                        raise h.Errore('Ci sono prenotazioni su questa tipologia: non si può eliminare')
                    if sez == 'camera' and Prenotazione.query.filter(Prenotazione.camera_id == obj.id, Prenotazione.stato == 'in_casa').first():
                        raise h.Errore('Camera occupata: non si può eliminare')
                    if sez in ('piano', 'canale') and Prenotazione.query.filter(getattr(Prenotazione, f'{sez}_id') == obj.id).first():
                        raise h.Errore('Ci sono prenotazioni con questo valore: disattivalo invece di eliminarlo')
                    db.session.delete(obj)
                    msg = 'Eliminato'
                else:
                    for nome, et, tipo, opz in campi:
                        if tipo == 'bool':
                            setattr(obj, nome, bool(request.form.get(nome)))
                        elif nome in request.form:
                            v = _valore(tipo, request.form.get(nome))
                            if tipo == 'scelta' and v not in opz:
                                raise h.Errore(f'{et}: valore non valido')
                            setattr(obj, nome, v)
                    _controlla(sez, obj)
                    db.session.add(obj)
                    msg = 'Salvato'
            elif az == 'camere_serie':
                tipo_id = request.form.get('tipo_id', type=int)
                da, a = request.form.get('da', type=int), request.form.get('a', type=int)
                if not tipo_id or not da or not a or a < da or a - da > 99:
                    raise h.Errore('Indica tipologia e numeri da… a… (massimo 100 camere)')
                n = 0
                for num in range(da, a + 1):
                    if not Camera.query.filter_by(numero=str(num)).first():
                        db.session.add(Camera(numero=str(num), tipo_id=tipo_id, piano=str(num)[0] if num >= 100 else '0'))
                        n += 1
                msg = f'Create {n} camere'
            else:
                raise h.Errore('Azione sconosciuta')
            db.session.commit()
            try:
                from corso import controlla_in_silenzio
                controlla_in_silenzio()
            except Exception:   # noqa: BLE001
                db.session.rollback()
            flash(msg, 'ok')
        except h.Errore as e:
            db.session.rollback()
            flash(str(e), 'err')
        except (ValueError, TypeError):
            db.session.rollback()
            flash('Controlla i valori inseriti (numeri e date)', 'err')
        except Exception:   # noqa: BLE001 — es. codice o numero già usato
            db.session.rollback()
            flash('Non salvato: codice o numero già presente?', 'err')
        return redirect(url_for('struttura.pagina', sez=sez))
    elenchi = {}
    for k, (modello, titolo, campi) in ENTITA.items():
        q = modello.query
        if k == 'camera':
            q = q.order_by(Camera.numero)
        elif k in ('stagione', 'evento'):
            q = q.order_by(modello.dal)
        elif k == 'restrizione':
            q = q.filter(Restrizione.data >= date.today()).order_by(Restrizione.data)
        elif k == 'tipo':
            q = q.order_by(TipoCamera.ordine, TipoCamera.id)
        else:
            q = q.order_by(modello.id)
        elenchi[k] = q.all()
    tipi = elenchi['tipo']
    soglie = {}
    if tipi:
        from dynaprice import soglia_prezzo
        soglie = {t.id: soglia_prezzo(t) for t in tipi}
    parametri = [(k, et, h.par(k)) for k, (et, d, t) in h.PARAMETRI.items()]
    totale_fissi = sum(c.importo_mensile or 0 for c in elenchi['costo'])
    return render_template('struttura/pagina.html', sez=sez, ENTITA=ENTITA, elenchi=elenchi, tipi=tipi, parametri=parametri,
                           soglie=soglie, totale_fissi=totale_fissi, n_camere=len(h.camere_attive()))
