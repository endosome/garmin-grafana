"""The intermediate representation: definitions and data messages with raw base-type values, in file order.

A value is an int or float, a list of them (an array), a string, a list of strings or ``{'hex': ...}`` for bytes that
are not UTF-8. A field whose value is invalid is absent from ``fields`` (plan, "Invalid values").
"""
import json
from dataclasses import dataclass, field

DEFAULT_PROTOCOL = 0x20
DEFAULT_PROFILE = 2132
TIMESTAMP_FIELD = 253
UINT32 = 0x86


@dataclass(frozen=True)
class Definition:
    id: int
    global_num: int
    arch: int  # 0 little endian, 1 big endian
    fields: tuple  # (field number, size in bytes, base type byte)
    dev_fields: tuple = ()  # (field number, size in bytes, developer data index)

    def key(self):
        return self.global_num, self.arch, self.fields, self.dev_fields


@dataclass
class Message:
    seq: int
    def_id: int
    global_num: int
    fields: dict = field(default_factory=dict)  # field number -> raw value
    dev: dict = field(default_factory=dict)  # (developer data index, field number) -> raw value


@dataclass
class FitFile:
    protocol: int = DEFAULT_PROTOCOL
    profile: int = DEFAULT_PROFILE
    definitions: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    # ids of definitions the decoder extended with a compressed header's timestamp (not stored; the CSV converter lists that timestamp first)
    compressed: set = field(default_factory=set, compare=False, repr=False)

    def add_definition(self, global_num, fields, dev_fields=(), arch=0):
        """The definition with these layouts, created if the file has none yet."""
        fields, dev_fields = tuple(map(tuple, fields)), tuple(map(tuple, dev_fields))
        for definition in self.definitions:
            if definition.key() == (global_num, arch, fields, dev_fields):
                return definition
        definition = Definition(len(self.definitions), global_num, arch, fields, dev_fields)
        self.definitions.append(definition)
        return definition

    def add_message(self, definition, fields, dev=None):
        message = Message(len(self.messages), definition.id, definition.global_num, dict(fields), dict(dev or {}))
        self.messages.append(message)
        return message


def canonical(fit_file):
    """The R4 oracle's view: (global number, sorted raw fields, sorted developer fields) per message, in order."""
    return [(m.global_num, sorted(m.fields.items()), sorted(m.dev.items())) for m in fit_file.messages]


def dev_key(index, number):
    return f'{index}:{number}'


def parse_dev_key(text):
    index, number = text.split(':')
    return int(index), int(number)


def dumps(fit_file):
    """JSONL: a header line, the definitions, then the messages."""
    lines = [{'header': {'protocol': fit_file.protocol, 'profile': fit_file.profile}}]
    lines += [{'def': {'id': d.id, 'global': d.global_num, 'arch': d.arch,
                       'fields': [list(f) for f in d.fields], 'dev': [list(f) for f in d.dev_fields]}}
              for d in fit_file.definitions]
    lines += [{'msg': {'seq': m.seq, 'def': m.def_id, 'global': m.global_num,
                       'f': {str(k): v for k, v in m.fields.items()},
                       'd': {dev_key(*k): v for k, v in m.dev.items()}}}
              for m in fit_file.messages]
    return ''.join(json.dumps(line, sort_keys=True, separators=(',', ':')) + '\n' for line in lines)


def loads(text):
    fit_file = FitFile()
    for line in text.splitlines():
        item = json.loads(line)
        if 'header' in item:
            fit_file.protocol, fit_file.profile = item['header']['protocol'], item['header']['profile']
        elif 'def' in item:
            d = item['def']
            fit_file.definitions.append(Definition(d['id'], d['global'], d['arch'],
                                                   tuple(map(tuple, d['fields'])), tuple(map(tuple, d['dev']))))
        else:
            m = item['msg']
            fit_file.messages.append(Message(m['seq'], m['def'], m['global'],
                                             {int(k): v for k, v in m['f'].items()},
                                             {parse_dev_key(k): v for k, v in m['d'].items()}))
    return fit_file
