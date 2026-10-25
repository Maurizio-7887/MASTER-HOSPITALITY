// Helper condivisi da tutte le pagine di Master Hospitality
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

// Ritorna sempre {ok, dati, errore}: gli errori non vengono mai ingoiati in silenzio.
async function api(metodo,url,corpo){
  try{
    const r=await fetch(url,{method:metodo,headers:{'Content-Type':'application/json'},body:corpo===undefined?undefined:JSON.stringify(corpo)});
    let dati=null;try{dati=await r.json();}catch(e){}
    if(r.status===401){location.href='/login';return {ok:false,dati,errore:'Sessione scaduta'};}
    if(!r.ok||(dati&&dati.ok===false))return {ok:false,dati,errore:(dati&&dati.error)||('Errore '+r.status)};
    return {ok:true,dati,errore:null};
  }catch(e){return {ok:false,dati:null,errore:'Server non raggiungibile'};}
}

let _tt;
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.style.display='block';clearTimeout(_tt);_tt=setTimeout(()=>t.style.display='none',3200);}

function euro(n){if(n==null)return '—';const d=Math.abs(n%1)>0.004?2:0;return new Intl.NumberFormat('it-IT',{style:'currency',currency:'EUR',minimumFractionDigits:d,maximumFractionDigits:d}).format(n);}
function num(n,d=1){return n==null?'—':new Intl.NumberFormat('it-IT',{maximumFractionDigits:d}).format(n);}
function dataIt(iso){if(!iso)return '';const [y,m,g]=iso.split('-');return g+'/'+m+'/'+y;}
const GIORNI=['dom','lun','mar','mer','gio','ven','sab'];
function giornoSett(iso){return GIORNI[new Date(iso+'T12:00:00').getDay()];}

window.tutorDopoModifica = window.tutorDopoModifica || (() => {});
