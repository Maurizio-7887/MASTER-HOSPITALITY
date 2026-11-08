"""Le attività del gestionale: una cartella per attività, con le sue pagine HTML in templates/<attività>/."""
from importlib import import_module

# ordine di registrazione: accesso e piattaforma prima, poi il lavoro quotidiano, poi direzione e corso
NOMI = ['accesso', 'piattaforma', 'abbonamento', 'corso',
        'giornata', 'planner', 'prenotazioni', 'ospiti', 'housekeeping', 'adempimenti',
        'canali', 'booking', 'comunicazioni', 'cassa',
        'revenue', 'controllo', 'struttura']


def ATTIVITA():
    return [import_module(f'blueprints.{n}').bp for n in NOMI]
