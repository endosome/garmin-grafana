"""The FIT profile as a JSON-able document, built at run time from the installed official SDK (plan A5).

Nothing Garmin-derived is committed: the document and the ``fit.profile_*`` rows come from the ``Profile`` module of
the pinned ``garmin-fit-sdk``, whose version is recorded with every file decoded.
"""
import json
from functools import lru_cache
from importlib import metadata


def sdk_version():
    return metadata.version('garmin-fit-sdk')


def _scalar_list(values, default):
    return list(values) if values else [default]


def _field(raw):
    """One field of a message in the document's shape; subfields keep their reference-field maps."""
    return {
        'name': raw['name'],
        'type': raw['type'],
        'base_type': raw['base_type'],
        'array': raw['array'] == 'true',
        'scale': _scalar_list(raw['scale'], 1),
        'offset': _scalar_list(raw['offset'], 0),
        'units': raw['units'] if isinstance(raw['units'], str) else list(raw['units']),
        'bits': list(raw['bits']),
        'components': list(raw['components']),
        'accumulate': bool(raw['is_accumulated']),
        'sub_fields': [{
            'name': s['name'], 'type': s['type'],
            'scale': _scalar_list(s['scale'], 1), 'offset': _scalar_list(s['offset'], 0),
            'units': s['units'], 'bits': list(s['bits']), 'components': list(s['components']),
            'map': [dict(m) for m in s['map']],
        } for s in raw['sub_fields']],
    }


@lru_cache(maxsize=1)
def build_profile():
    """The profile document: ``version``, ``types`` (name to {raw value: name}) and ``messages`` (number to message)."""
    from garmin_fit_sdk import Profile
    version = '{major}.{minor}.{patch}'.format(**Profile['version'])
    types = {name: {str(k): v for k, v in values.items()} for name, values in Profile['types'].items()}
    messages = {str(num): {
        'name': message['name'],
        'fields': {str(fnum): _field(field) for fnum, field in message['fields'].items()},
    } for num, message in Profile['messages'].items()}
    document = {'sdk_version': sdk_version(), 'version': version, 'types': types, 'messages': messages}
    return json.loads(json.dumps(document, sort_keys=True))


def dumps(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'))


class Profile:
    """Lookups over a profile document. Anything the document does not name falls through as raw."""

    def __init__(self, document=None):
        self.document = document if document is not None else build_profile()
        self.version = self.document['version']
        self._names = {int(num): m['name'] for num, m in self.document['messages'].items()}
        self._numbers = {name: num for num, name in self._names.items()}

    def message_name(self, number):
        return self._names.get(number)

    def message_number(self, name):
        return self._numbers[name]

    def message(self, number):
        return self.document['messages'].get(str(number))

    def field(self, number, field_number):
        message = self.message(number)
        return message['fields'].get(str(field_number)) if message else None

    def field_number(self, message, name):
        """The number of a named field of a message given by number or name."""
        number = message if isinstance(message, int) else self._numbers[message]
        for fnum, field in self.document['messages'][str(number)]['fields'].items():
            if field['name'] == name:
                return int(fnum)
        raise KeyError(f'{message}.{name}')

    def type_values(self, type_name):
        return self.document['types'].get(type_name)
