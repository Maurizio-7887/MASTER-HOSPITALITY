"""Laboratorio sincronizzazione OTA (solo didattico; nessuna API esterna)."""
from datetime import date, datetime
from flask import Blueprint, g, jsonify, request, render_template_string
from models import Canale, Impostazione, OTAEvento, db
from spazi import spazio_corrente
from channel_manager import snapshot

bp = Blueprint('ota_sync', __name__)

def autorizzato():
    return g.utente.ruolo in ('admin','revenue')

def canale_valido(value):
    try:
        c=db.session.get(Canale,int(value))
    except (ValueError,TypeError):
        c=None
    return c if c and c.tipo=='ota' and c.attivo else None

def registra_progresso(chiave):
    Impostazione.scrivi(chiave,str(int(Impostazione.leggi(chiave) or 0)+1))

@bp.route('/api/ota-lab/eventi')
def eventi():
    if not autorizzato():
        return jsonify({'ok':False,'error':'Ruolo non autorizzato'}),403
    xs=OTAEvento.query.order_by(OTAEvento.id.desc()).limit(40).all()
    return jsonify({'ok':True,'eventi':[{'id':x.id,'canale_id':x.canale_id,'canale':x.canale.nome,
        'tipo':x.tipo,'stato':x.stato,'messaggio':x.messaggio,'creato_il':x.creato_il.isoformat()}
        for x in xs]})

@bp.route('/api/ota-lab/sincronizza',methods=['POST'])
def sincronizza():
    if not autorizzato():
        return jsonify({'ok':False,'error':'Ruolo non autorizzato'}),403
    d=request.get_json(silent=True) or {}
    try:
        canale=canale_valido(d.get('canale_id'))
        da=date.fromisoformat(d.get('da') or date.today().isoformat())
        giorni=int(d.get('giorni',14))
        if not canale or giorni<1 or giorni>60:
            return jsonify({'ok':False,'error':'Canale OTA o intervallo non valido'}),400
    except (TypeError,ValueError):
        return jsonify({'ok':False,'error':'Date o intervallo non validi'}),400
    errore=d.get('simula_errore') is True
    righe=snapshot(da,giorni)
    msg=('Timeout simulato: aggiornamento del portale NON riuscito.'
         if errore else f'{len(righe)} righe ARI inviate al canale didattico {canale.nome}.')
    ev=OTAEvento(canale_id=canale.id,tipo='errore' if errore else 'ari',
                 stato='errore' if errore else 'inviato',messaggio=msg)
    db.session.add(ev)
    registra_progresso('corso_ota_errori' if errore else 'corso_ota_sync')
    db.session.commit()
    return jsonify({'ok':not errore,'modalita':'simulazione','evento_id':ev.id,
                    'messaggio':msg,'righe':[] if errore else righe}),503 if errore else 200

@bp.route('/api/ota-lab/recupera',methods=['POST'])
def recupera():
    if not autorizzato():
        return jsonify({'ok':False,'error':'Ruolo non autorizzato'}),403
    try:
        evento_id=int((request.get_json(silent=True) or {})['evento_id'])
    except (KeyError,TypeError,ValueError):
        return jsonify({'ok':False,'error':'Indica un evento valido'}),400
    ev=db.session.get(OTAEvento,evento_id)
    if not ev or ev.spazio_id != spazio_corrente():
        return jsonify({'ok':False,'error':'Evento non trovato'}),404
    if ev.stato=='risolto':
        return jsonify({'ok':True,'duplicato':True,'evento_id':ev.id})
    if ev.stato!='errore':
        return jsonify({'ok':False,'error':'Non e un errore da recuperare'}),400
    ev.stato='risolto'
    ev.risolto_il=datetime.utcnow()
    ev.messaggio='Risolto: ARI ritrasmesso nel simulatore'
    registra_progresso('corso_ota_recuperi')
    db.session.commit()
    return jsonify({'ok':True,'evento_id':ev.id,'duplicato':False})

@bp.route('/ota-lab')
def pagina():
    if not autorizzato():
        return 'Ruolo non autorizzato',403
    return render_template_string(HTML,canali=Canale.query.filter_by(tipo='ota',attivo=True).all(),
                                  oggi=date.today().isoformat())

HTML = """{% extends 'base.html' %}
{% block titolo %}OTA Lab · MASTER HOSPITALITY{% endblock %}
{% block corpo %}
<h1>OTA Lab — Booking.com ed Expedia simulati</h1>
<p><strong>Solo laboratorio didattico.</strong> Nessuna richiesta viene inviata ai portali reali.
Questo ambiente usa disponibilita e tariffe del tuo PMS.</p>
<p><a href="/channel-manager">Importa o cancella prenotazioni OTA</a> ·
<a href="/revenue">DynaPrice</a> · <a href="/revenue/ml">Machine Learning</a></p>
<section>
<h2>1. Invio ARI (Availability, Rates, Inventory)</h2>
<p>Seleziona un canale e simula una sincronizzazione. Poi provoca un guasto e risolvilo dal registro.</p>
<label>Portale <select id="canale">{% for c in canali %}
<option value="{{c.id}}">{{c.nome}} (commissione {{c.commissione_pct}}%)</option>
{% endfor %}</select></label>
<label>Dal <input type="date" id="da" value="{{oggi}}"></label>
<button onclick="invia(false)">Invia disponibilita e tariffe</button>
<button onclick="invia(true)">Simula errore di timeout</button>
<p id="esito" role="status"></p>
</section>
<section><h2>2. Registro delle sincronizzazioni</h2>
<p>Un timeout resta aperto finche non premi <b>Recupera</b>. Ogni evento appartiene allo spazio di un solo allievo.</p>
<div style="overflow:auto"><table><thead><tr>
<th>Portale</th><th>Operazione</th><th>Stato</th><th>Messaggio</th><th>Azione</th>
</tr></thead><tbody id="registro"></tbody></table></div></section>
<script>
async function registro(){
 const r=await fetch('/api/ota-lab/eventi'),j=await r.json();
 const root=document.querySelector('#registro'); root.replaceChildren();
 for(const e of (j.eventi||[])){
  const tr=document.createElement('tr');
  for(const v of [e.canale,e.tipo,e.stato,e.messaggio]){
   const td=document.createElement('td');td.textContent=String(v);tr.appendChild(td);
  }
  const cell=document.createElement('td');
  if(e.stato==='errore'){
   const b=document.createElement('button');b.textContent='Recupera';
   b.onclick=()=>recupera(e.id);cell.appendChild(b);
  }
  tr.appendChild(cell);root.appendChild(tr);
 }
}
async function invia(errore){
 const b={canale_id:document.querySelector('#canale').value,
          da:document.querySelector('#da').value,giorni:14,simula_errore:errore};
 const r=await fetch('/api/ota-lab/sincronizza',
   {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
 const j=await r.json();document.querySelector('#esito').textContent=j.messaggio||j.error;
 await registro();
}
async function recupera(id){
 const r=await fetch('/api/ota-lab/recupera',
  {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({evento_id:id})});
 const j=await r.json();
 document.querySelector('#esito').textContent=j.ok?'Errore risolto nel simulatore.':j.error;
 await registro();
}
registro();
</script>
{% endblock %}
"""