"""Ogni contatto arriva anche nel CRM (webhook del sito: /api/webhook/lead_sito).

Variabili d'ambiente (le stesse del sito): CRM_WEBHOOK_URL e CRM_API_KEY. Se mancano non fa nulla.
Gira in un thread e non solleva mai eccezioni: un CRM spento non blocca mai una richiesta o una registrazione.
Passa da HTTPS, quindi funziona anche dove l'SMTP è bloccato (Railway Hobby)."""
import json
import os
import threading
import urllib.error
import urllib.request


def _invia(payload):
    url = os.environ.get("CRM_WEBHOOK_URL", "").strip()
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    if os.environ.get("CRM_API_KEY"):
        headers["X-API-Key"] = os.environ["CRM_API_KEY"]
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"CRM: {payload.get('email')} -> {r.status}")
    except urllib.error.HTTPError as e:
        print(f"CRM: {payload.get('email')} -> {e.code} {e.read()[:120]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"CRM non raggiungibile per {payload.get('email')}: {e}")


def avvisa(nome, email, messaggio, fonte, categoria="richiesta_servizio", azienda="", telefono="", event_id="", sincrono=False):
    payload = {"nome": (nome or email or "").strip(), "email": (email or "").strip(), "azienda": azienda or "",
               "telefono": telefono or "", "messaggio": messaggio, "fonte": fonte, "event_id": event_id[:120],
               "qualifica_ai": {"categoria": categoria, "motivo": fonte, "prospect": categoria in ("richiesta_servizio", "iscrizione_corso")}}
    if not payload["email"]:
        return
    if sincrono:
        _invia(payload)
    else:
        threading.Thread(target=_invia, args=(payload,), daemon=True).start()
