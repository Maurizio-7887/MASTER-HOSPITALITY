"""Gemini: modello ritirato (404) -> il successivo; catena tutta ritirata -> il Flash più nuovo dall'elenco."""
from unittest import mock

import ai


class R:
    def __init__(self, codice, dati): self.status_code, self._d = codice, dati
    def json(self): return self._d


def test_ricambio_modelli(app):
    app.config['GEMINI_API_KEY'] = 'k'
    chiamati = []

    def post(url, **kw):
        chiamati.append(url.split('/models/')[1].split(':')[0])
        if 'gemini-4.2-flash' in url:
            return R(200, {'candidates': [{'content': {'parts': [{'text': 'Prezzi su del 10%'}]}}]})
        return R(404, {'error': {'message': 'no longer available to new users'}})
    with mock.patch.object(ai.requests, 'post', post), \
            mock.patch('modelli_ai.scopri_rest', return_value=['gemini-4.2-flash']):
        testo, err = ai.chiedi('ciao')
    assert testo == 'Prezzi su del 10%' and err is None
    assert chiamati[-1] == 'gemini-4.2-flash' and 'gemini-3.8-flash' in chiamati
