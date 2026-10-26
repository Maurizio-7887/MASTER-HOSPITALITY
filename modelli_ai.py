"""Modelli Gemini con ricambio automatico.

Google ritira i modelli (es. «gemini-2.5-flash is no longer available to new users»): invece di cambiare il codice
ogni volta, il modello si sceglie con la variabile GEMINI_MODEL e, se Google risponde 404 / NOT_FOUND, la chiamata
si ripete da sola con il modello successivo della catena. Il modello che funziona viene ricordato per le chiamate dopo.

Variabili facoltative su Railway: GEMINI_MODEL (testo), GEMINI_IMAGE_MODEL (immagini)."""
import os

CATENA_TESTO = [m for m in (os.environ.get('GEMINI_MODEL', '').strip(), 'gemini-3.8-flash', 'gemini-flash-latest',
                            'gemini-2.5-flash') if m]
MODELLO_TESTO = CATENA_TESTO[0]
_IMMAGINE = os.environ.get('GEMINI_IMAGE_MODEL', '').strip()


def _ritirato(errore):
    t = str(errore)
    return '404' in t or 'NOT_FOUND' in t or 'no longer available' in t or 'not found' in t.lower()


_SCARTA = ('lite', 'image', 'tts', 'live', 'audio', 'preview', 'exp', 'thinking', 'embedding', 'learnlm', 'robotics')


def migliori_flash(nomi):
    """Dai nomi dei modelli disponibili per la chiave, i Flash di testo dal più nuovo (gemini-3.8-flash, gemini-3.6-flash...)."""
    import re
    trovati = []
    for n in nomi:
        n = str(n).split('/')[-1]
        if any(x in n for x in _SCARTA):
            continue
        m = re.fullmatch(r'gemini-(\d+(?:\.\d+)?)-flash(?:-\d{3})?', n)
        if m:
            trovati.append((float(m.group(1)), n))
        elif n == 'gemini-flash-latest':
            trovati.append((999.0, n))
    return [n for _, n in sorted(trovati, reverse=True)]


def scopri(client):
    """Chiede a Google quali modelli sono attivi per questa chiave (si usa solo quando tutta la catena risponde 404)."""
    try:
        nomi = [getattr(m, 'name', '') for m in client.models.list()]
    except Exception as e:  # noqa: BLE001
        print(f'Gemini: elenco dei modelli non disponibile: {e}')
        return []
    return migliori_flash(nomi)


def flash_immagine(client):
    """Modelli Flash che generano immagini (Nano Banana), dal più nuovo."""
    import re
    try:
        nomi = [str(getattr(m, 'name', '')).split('/')[-1] for m in client.models.list()]
    except Exception:  # noqa: BLE001
        return []
    trovati = [(float(m.group(1)), n) for n in nomi
               if (m := re.fullmatch(r'gemini-(\d+(?:\.\d+)?)-flash-image(?:-[a-z0-9-]+)?', n)) and 'lite' not in n]
    return [n for _, n in sorted(trovati, reverse=True)]


def scopri_rest(chiave):
    import requests
    try:
        r = requests.get('https://generativelanguage.googleapis.com/v1beta/models', params={'pageSize': 200},
                         headers={'x-goog-api-key': chiave}, timeout=15)
        return migliori_flash(m.get('name', '') for m in r.json().get('models', []))
    except Exception as e:  # noqa: BLE001
        print(f'Gemini: elenco dei modelli non disponibile: {e}')
        return []


def proteggi(client):
    """Avvolge client.models.generate_content: 404 sul modello -> riprova con il successivo della catena."""
    if client is None or getattr(client, '_modelli_protetti', False):
        return client
    originale = client.models.generate_content
    stato = {'testo': MODELLO_TESTO}

    def generate_content(*args, model=None, **kwargs):
        if model and 'image' in model:
            richiesto_img = _IMMAGINE or stato.get('immagine') or model
            try:
                return originale(*args, model=richiesto_img, **kwargs)
            except Exception as e:  # noqa: BLE001
                if not _ritirato(e):
                    raise
                for m in flash_immagine(client):   # Nano Banana ritirato: il più nuovo disponibile
                    if m == richiesto_img:
                        continue
                    try:
                        risposta = originale(*args, model=m, **kwargs)
                        print(f'Gemini: modello immagini {richiesto_img} non disponibile, uso {m}')
                        stato['immagine'] = m
                        return risposta
                    except Exception as e2:  # noqa: BLE001
                        if not _ritirato(e2):
                            raise
                raise
        richiesto = model or stato['testo']
        # un modello già scoperto ritirato non si richiama più: si parte da quello che funziona
        candidati = [stato['testo']] + [m for m in [richiesto] + CATENA_TESTO if m != stato['testo']] \
            if richiesto in CATENA_TESTO or richiesto == stato.get('ritirato') else [richiesto] + CATENA_TESTO
        ultimo, provati = None, set()
        for giro in (1, 2):
            if giro == 2:   # tutta la catena ritirata: si chiede a Google l'elenco e si prova il Flash più nuovo
                candidati = [m for m in scopri(client) if m not in provati]
            for m in dict.fromkeys(candidati):
                provati.add(m)
                try:
                    risposta = originale(*args, model=m, **kwargs)
                    if m != richiesto:
                        print(f'Gemini: modello {richiesto} non disponibile, uso {m}')
                        stato['ritirato'] = richiesto
                    stato['testo'] = m
                    return risposta
                except Exception as e:  # noqa: BLE001
                    if not _ritirato(e):
                        raise
                    ultimo = e
        raise ultimo

    try:
        client.models.generate_content = generate_content
        client._modelli_protetti = True
    except Exception as e:  # noqa: BLE001
        print(f'Ricambio automatico dei modelli Gemini non attivo: {e}')
    return client


def candidati_rest():
    """Per le chiamate REST: i modelli da provare in ordine (al 404 si passa al successivo)."""
    return list(dict.fromkeys(CATENA_TESTO))


def ritirato(errore_o_testo):
    return _ritirato(errore_o_testo)
