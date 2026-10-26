"""Collegamento a Gemini (stessa chiave degli altri programmi: GEMINI_API_KEY o GOOGLE_API_KEY)."""
import requests
from flask import current_app

URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


def chiedi(prompt, max_token=900):
    """Ritorna (testo, errore)."""
    chiave = current_app.config.get('GEMINI_API_KEY')
    if not chiave:
        return None, 'AI non configurata: imposta la variabile GEMINI_API_KEY'
    corpo = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
             'generationConfig': {'temperature': 0.4, 'maxOutputTokens': max_token, 'thinkingConfig': {'thinkingBudget': 0}}}
    import modelli_ai   # modello ritirato da Google (404): il successivo, poi il Flash più nuovo attivo per la chiave
    candidati = [m for m in [current_app.config.get('GEMINI_MODEL')] + modelli_ai.CATENA_TESTO if m]
    errore = 'Risposta AI vuota'
    for giro in (1, 2):
        if giro == 2:
            candidati = [m for m in modelli_ai.scopri_rest(chiave) if m not in candidati]
        for modello in dict.fromkeys(candidati):
            try:
                r = requests.post(URL.format(model=modello), json=corpo, headers={'x-goog-api-key': chiave}, timeout=40)
                dati = r.json()
            except (requests.RequestException, ValueError):
                return None, 'Il servizio AI non risponde: riprova tra poco'
            if r.status_code == 404:
                errore = (dati.get('error') or {}).get('message') or 'modello non disponibile'
                continue
            if r.status_code >= 400:
                return None, (dati.get('error') or {}).get('message') or f'Errore AI {r.status_code}'
            try:
                return ''.join(p.get('text', '') for p in dati['candidates'][0]['content']['parts']).strip(), None
            except (KeyError, IndexError):
                return None, 'Risposta AI vuota'
    return None, errore
