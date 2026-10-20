"""Storage-independent records and validation shared by connectors and imports."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from hashlib import sha256
import json
import re
from typing import Any, Protocol, Iterable


class RecordBatch(list):
    """Records plus complete (dataset, activity ID) snapshots, including empty ones."""
    def __init__(self, values=(), *, replacements=()):
        super().__init__(values)
        self.replacements = list(replacements)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse_timestamp(value: str | datetime) -> tuple[datetime, int, str]:
    original = value.isoformat() if isinstance(value, datetime) else value
    match = re.search(r'\.(\d+)', original)
    fraction = match.group(1) if match else ''
    if len(fraction) > 9:
        raise ValueError('Timestamps beyond nanosecond precision are unsupported')
    dt = datetime.fromisoformat(original.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp requires an explicit UTC offset')
    return dt.astimezone(timezone.utc), int((fraction + '000000000')[6:9]), original


@dataclass(frozen=True)
class Record:
    dataset: str
    time: datetime
    values: dict[str, Any]
    tags: dict[str, Any] = field(default_factory=dict)
    device: str = 'unknown'
    key: str = ''
    nanoseconds: int = 0
    original_time: str = ''
    local_date: date | None = None
    timezone: str | None = None

    @classmethod
    def from_mapping(cls, item, *, local_date=None, timezone=None, device_id=None):
        dataset = item['measurement']
        if not dataset or not isinstance(item['fields'], dict) or not item['fields']:
            raise ValueError('A named dataset and nonempty fields are required')
        values, tags = dict(item['fields']), dict(item.get('tags', {}))
        canonical(values)  # Reject NaN/Infinity and non-JSON values, never replace with zero.
        canonical(tags)
        dt, ns, original = parse_timestamp(item['time'])
        device = str(tags.get('DeviceID') or values.get('Device_ID') or device_id or tags.get('Device') or 'unknown')
        activity = tags.get('ActivityID', values.get('Activity_ID'))
        if dataset == 'StrengthExerciseSet':
            identity = [device, str(activity), values['SetOrder']]
        elif dataset == 'StrengthHRZones':
            identity = [device, str(activity), values['ZoneNumber']]
        elif dataset == 'ActivitySummary' and activity is not None:
            identity = [device, str(activity), 'end' if values.get('activityName') == 'END' else 'start']
        else:
            identity_tags = {k: str(v) for k, v in tags.items() if k not in ('Database_Name', 'User_ID')}
            identity = [device, item.get('source_id'), identity_tags, dt.isoformat(), ns]
        key = sha256(canonical(identity).encode()).hexdigest()
        return cls(dataset, dt, values, tags, device, key, ns, original, local_date, timezone)

    @property
    def is_sample(self):
        return 'intraday' in self.dataset.lower() or self.dataset in ('ActivityGPS', 'HRV_Intraday')


class RecordStore(Protocol):
    def write(self, records: Iterable[Record], *, replacements=(), checkpoint=None) -> int: ...
