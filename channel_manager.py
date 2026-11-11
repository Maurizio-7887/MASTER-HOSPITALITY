"""Channel Manager didattico bidirezionale: snapshot ARI e inbox OTA simulata.
Nessuna chiamata a Booking/Expedia: per connettori reali servono accordi/API autorizzate.
"""
from datetime import date, timedelta
from flask import Blueprint, jsonify, request, render_template_string, g
from sqlalchemy import and_
from models import Canale, PianoTariffario, Prenotazione, TipoCamera, Impostazione, db
from hotel import disponibili_per_notte, prezzo_camera, crea_prenotazione, ospite_da_dati, cancella, Errore

bp = Blueprint('channel', __name__)

def _admin():
    return g.utente.ruolo in ('admin', 'revenue')

def snapshot(da, giorni=14):
    giorni = max(1, min(60, int(giorni)))
    tipi = TipoCamera.query.order_by(TipoCamera.ordine).all()
    out=[]
    for i in range(giorni):
        d = da + timedelta(days=i)
        for t in tipi:
            disp = disponibili_per_notte(t.id, d, d+timedelta(days=1))[d]
            out.append({'data':d.isoformat(), 'tipo_id':t.id, 'tipo':t.nome,
                        'disponibili':max(0,disp), 'prezzo':prezzo_camera(t,d),
                        'stop_sell':disp<=0})
    return out

@bp.route('/api/channel-manager/inventario')
def inventario():
    if not _admin():
        return jsonify({'ok':False,'error':'Ruolo non autorizzato'}),403
    try:
        da = date.fromisoformat(request.args.get('da') or date.today().isoformat())
        gg = int(request.args.get('giorni', 14))
        if gg < 1 or gg > 60:
            raise ValueError()
    except ValueError:
        return jsonify({'ok':False,'error':'Date o giorni non validi'}),400
    return jsonify({'ok':True,'modalita':'simulazione', 'righe':snapshot(da,gg)})

@bp.route('/api/channel-manager/prenotazione', methods=['POST'])
def prenotazione():
    if not _admin():
        return jsonify({'ok':False,'error':'Ruolo non autorizzato'}),403
    body=request.get_json(silent=True) or {}
    try:
        canale= db.session.get(Canale,int(body['canale_id']))
        tipo= db.session.get(TipoCamera,int(body['tipo_id']))
        piano= db.session.get(PianoTariffario,int(body['piano_id']))
        ref=str(body['riferimento']).strip()
        if not canale or not canale.attivo or canale.tipo!='ota' or not tipo or not piano or not piano.attivo:
            raise Errore('Seleziona canale OTA, tipologia e piano validi')
        if not 3 <= len(ref) <= 40:
            raise Errore('Riferimento OTA obbligatorio (3-40 caratteri)')
        esistente=Prenotazione.query.filter_by(canale_id=canale.id,rif_canale=ref).first()
        if esistente:
            return jsonify({'ok':True,'duplicato':True,'codice':esistente.codice,'id':esistente.id})
        arrivo=date.fromisoformat(body['arrivo'])
        partenza=date.fromisoformat(body['partenza'])
        if arrivo < date.today() or partenza<=arrivo:
            raise Errore('Date di soggiorno non valide')
        adulti=int(body.get('adulti',2))
        bambini=int(body.get('bambini',0))
        cognome=str(body.get('cognome') or '').strip()
        if not cognome:
            raise Errore('Cognome ospite obbligatorio')
        ospite=ospite_da_dati({'cognome':cognome,'nome':str(body.get('nome') or ''),
                              'email':str(body.get('email') or '')})
        p=crea_prenotazione(ospite,tipo.id,piano.id,canale.id,arrivo,partenza,adulti,bambini,
                           stato='confermata',rif_canale=ref,note='Importata dal Channel Manager simulato')
        Impostazione.scrivi('corso_ota_importazioni', str(int(Impostazione.leggi('corso_ota_importazioni') or '0') + 1))
        db.session.commit()
        return jsonify({'ok':True,'duplicato':False,'id':p.id,'codice':p.codice}),201
    except (KeyError,ValueError,TypeError,Errore) as e:
        db.session.rollback()
        return jsonify({'ok':False,'error':str(e) if isinstance(e,Errore) else 'Dati della prenotazione non validi'}),400

@bp.route('/api/channel-manager/cancella',methods=['POST'])
def cancellazione():
    if not _admin():
        return jsonify({'ok':False,'error':'Ruolo non autorizzato'}),403
    b=request.get_json(silent=True) or {}
    try:
        canale_id=int(b['canale_id'])
        ref=str(b['riferimento']).strip()
        p=Prenotazione.query.filter_by(canale_id=canale_id,rif_canale=ref).first()
        if not p or p.canale.tipo!='ota':
            return jsonify({'ok':False,'error':'Prenotazione OTA non trovata'}),404
        if p.stato=='cancellata':
            return jsonify({'ok':True,'duplicato':True,'codice':p.codice})
        cancella(p)
        Impostazione.scrivi('corso_ota_cancellazioni', str(int(Impostazione.leggi('corso_ota_cancellazioni') or '0') + 1))
        db.session.commit()
        return jsonify({'ok':True,'duplicato':False,'codice':p.codice})
    except (KeyError,ValueError,TypeError,Errore) as e:
        db.session.rollback()
        return jsonify({'ok':False,'error':str(e) if isinstance(e,Errore) else 'Richiesta non valida'}),400

@bp.route('/channel-manager')
def pagina():
    if not _admin():
        return 'Ruolo non autorizzato',403
    canali=Canale.query.filter_by(tipo='ota',attivo=True).all()
    tipi=TipoCamera.query.all()
    piani=PianoTariffario.query.filter_by(attivo=True).all()
    return render_template_string(PAGINA, canali=canali,tipi=tipi,piani=piani,oggi=date.today().isoformat())

PAGINA="""<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Channel Manager · MASTER HOSPITALITY</title>
<style>body{font:16px system-ui;background:#f4f7fb;color:#142c45;margin:25px auto;max-width:1050px;padding:0 18px}section{background:white;border:1px solid #dce5ee;border-radius:12px;padding:22px;margin:18px 0}label{display:inline-block;margin:8px}input,select,button{padding:9px;border-radius:5px;border:1px solid #a5b6c8}button{background:#155a86;color:white;cursor:pointer}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}.scroll{overflow:auto}#msg{white-space:pre-wrap}</style>
<h1>Channel Manager · Laboratorio OTA</h1><p><b>SIMULAZIONE DIDATTICA</b> — nessun collegamento reale a Booking.com o Expedia. I cambiamenti alle prenotazioni aggiornano il PMS e la disponibilità.</p>
<p><a href="/oggi">← Torna al gestionale</a> · <a href="/revenue">DynaPrice</a> · <a href="/api/revenue/ml">Previsioni Machine Learning (JSON)</a></p>
<section><h2>1. Disponibilità e tariffe ARI</h2><label>Dal <input type="date" id="da" value="{{oggi}}"></label><button onclick="aggiorna()">Sincronizza simulazione</button><div class="scroll"><table><thead><tr><th>Data</th><th>Camera</th><th>Disponibili</th><th>Tariffa €</th><th>Vendita</th></tr></thead><tbody id="righe"></tbody></table></div></section>
<section><h2>2. Ricevi una prenotazione OTA simulata</h2>
<p>Le commissioni sono definite in Struttura → Canali. Ripetere lo stesso riferimento non crea duplicati.</p>
<label>Canale <select id="canale">{% for c in canali %}<option value="{{c.id}}">{{c.nome}}</option>{% endfor %}</select></label>
<label>Camera <select id="tipo">{% for t in tipi %}<option value="{{t.id}}">{{t.nome}}</option>{% endfor %}</select></label>
<label>Piano <select id="piano">{% for p in piani %}<option value="{{p.id}}">{{p.nome}}</option>{% endfor %}</select></label>
<label>Arrivo <input type="date" id="arrivo" value="{{oggi}}"></label>
<label>Partenza <input type="date" id="partenza"></label>
<label>Rif. OTA <input id="ref" placeholder="OTA-10001"></label>
<label>Cognome <input id="cognome" placeholder="Rossi"></label>
<label>Nome <input id="nome" placeholder="Mario"></label>
<button onclick="invia(false)">Importa prenotazione</button><button onclick="invia(true)">Ricevi cancellazione OTA</button><p id="msg" role="status"></p></section>
<script>
async function aggiorna(){let r=await fetch('/api/channel-manager/inventario?da='+encodeURIComponent(document.querySelector('#da').value));let j=await r.json();document.querySelector('#righe').innerHTML=(j.righe||[]).map(x=>'<tr><td>'+x.data+'</td><td>'+x.tipo+'</td><td>'+x.disponibili+'</td><td>'+x.prezzo+'</td><td>'+(x.stop_sell?'STOP SELL':'Aperta')+'</td></tr>').join('');}
async function invia(canc){let v=id=>document.getElementById(id).value;let b={canale_id:v('canale'),tipo_id:v('tipo'),piano_id:v('piano'),arrivo:v('arrivo'),partenza:v('partenza'),riferimento:v('ref'),cognome:v('cognome'),nome:v('nome')};let url=canc?'/api/channel-manager/cancella':'/api/channel-manager/prenotazione';let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});let j=await r.json();document.querySelector('#msg').textContent=j.ok?(j.duplicato?'Evento già elaborato: ':'Registrato: ')+j.codice:(j.error||'Errore');aggiorna();}
aggiorna();</script></html>"""
