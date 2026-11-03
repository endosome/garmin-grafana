"""A small fitparse-shaped reader over the semantics layer, for code written against ``fitparse.FitFile``.

``FitFile(path or file object).get_messages(name)`` yields messages whose ``get_values()`` give {field name: value} with
scale and offset applied, enum names, naive UTC datetimes, positions still in semicircles, and ``unknown_<number>`` for
what the profile does not name, which is what the collector's FIT parsing was written for.
"""
from datetime import timezone
from pathlib import Path

from . import decoder
from .profile import Profile
from .protocol import FitError
from .semantics import Semantics

FitParseError = FitError
FIELD_DESCRIPTION = 206


class Field:
    def __init__(self, name, value, units=''):
        self.name, self.value, self.units = name, value, units

    def __repr__(self):
        return f'Field({self.name!r}, {self.value!r})'


class Message:
    def __init__(self, name, fields):
        self.name, self.fields = name, fields

    def __iter__(self):
        return iter(self.fields)

    def get_values(self):
        return {field.name: field.value for field in self.fields}

    def get_value(self, name, default=None):
        return next((field.value for field in self.fields if field.name == name), default)


def _plain(value):
    if hasattr(value, 'tzinfo') and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class FitFile:
    def __init__(self, source, check_crc=True):
        if isinstance(source, (str, Path)):
            self._data = Path(source).read_bytes()
        elif isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
        else:
            self._data = source.read()
        self._check_crc = check_crc
        self._messages = None

    def parse(self):
        if self._messages is None:
            self._messages = self._build(decoder.decode(self._data, check_crc=self._check_crc))
        return self._messages

    @property
    def messages(self):
        return self.parse()

    def get_messages(self, name=None):
        for message in self.parse():
            if name is None or message.name == name:
                yield message

    @staticmethod
    def _build(fit_file):
        profile, semantics = Profile(), Semantics()
        developer = {}  # (developer data index, field number) -> description fields
        messages = []
        for raw in fit_file.messages:
            wire = {n: base for n, _, base in fit_file.definitions[raw.def_id].fields}
            named = semantics.named(raw.global_num, raw.fields, expand_sub_fields=True, wire=wire)
            fields = []
            for key, value in named.items():
                if isinstance(key, int):
                    fields.append(Field(f'unknown_{key}', _plain(value)))
                    continue
                units = next((f['units'] for f in profile.message(raw.global_num)['fields'].values() if f['name'] == key), '')
                fields.append(Field(key, _plain(value), units if isinstance(units, str) else ','.join(units)))
            for (index, number), value in raw.dev.items():
                description = developer.get((index, number), {})
                scale, offset = description.get(6), description.get(7) or 0
                if isinstance(value, (int, float)) and scale:
                    value = value / scale - offset
                name = description.get(3)
                name = name[0] if isinstance(name, list) else name
                fields.append(Field(name or f'dev_{index}_{number}', value, description.get(8) or ''))
            if raw.global_num == FIELD_DESCRIPTION:
                developer[(raw.fields.get(0), raw.fields.get(1))] = raw.fields
            messages.append(Message(profile.message_name(raw.global_num) or f'unknown_{raw.global_num}', fields))
        return messages
