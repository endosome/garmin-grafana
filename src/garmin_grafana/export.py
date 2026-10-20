"""Stream an account's PostgreSQL records into a portable JSONL ZIP archive."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import quote
import zipfile
from .storage import connect


def date_range(*, start=None, end=None, last_n_days=None, now=None):
    now = now or datetime.now(timezone.utc)
    if last_n_days is not None:
        if last_n_days <= 0:
            raise ValueError('last-n-days must be positive')
        return now - timedelta(days=last_n_days), now
    start_time = datetime.strptime(start, '%Y-%m-%d').replace(tzinfo=timezone.utc) if start else now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    end_time = datetime.strptime(end, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1) if end else now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if start_time >= end_time:
        raise ValueError('Start date must be on or before end date')
    return start_time, end_time


def export(connection, path, *, account, start, end, provider='garmin'):
    count = 0
    with connection.transaction(), connection.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute('SELECT id FROM sources WHERE provider=%s AND account=%s', (provider,account))
        source = cur.fetchone()
        if source is None:
            raise ValueError('Source account not found')
        cur.execute('SELECT DISTINCT dataset FROM all_records WHERE source_id=%s AND time >= %s AND time < %s ORDER BY dataset', (source[0],start,end))
        datasets = [row[0] for row in cur.fetchall()]
        with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',json.dumps({'version':1,'provider':provider,'account':account,'start':start.isoformat(),'end_exclusive':end.isoformat(),'datasets':datasets}))
            for dataset in datasets:
                with connection.cursor(name='export_records') as rows:
                    rows.itersize=1000
                    rows.execute('''SELECT original_time,nanoseconds,device_id,local_date,timezone,tags,fields,record_key
                        FROM all_records WHERE source_id=%s AND dataset=%s AND time >= %s AND time < %s
                        ORDER BY time,nanoseconds,record_key''',(source[0],dataset,start,end))
                    with archive.open(quote(dataset,safe='')+'.jsonl','w') as output:
                        for original, ns, device, day, zone, tags, fields, key in rows:
                            item={'dataset':dataset,'time':original,'nanoseconds':ns,'device':device,
                                  'local_date':day.isoformat() if day else None,'timezone':zone,
                                  'tags':tags,'values':fields,'key':key}
                            output.write((json.dumps(item,allow_nan=False)+'\n').encode())
                            count+=1
    return count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--provider',default='garmin')
    parser.add_argument('--output',required=True)
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--last-n-days',type=int)
    args=parser.parse_args()
    start,end=date_range(start=args.start_date,end=args.end_date,last_n_days=args.last_n_days)
    with connect() as connection:
        count=export(connection,args.output,account=args.account,provider=args.provider,start=start,end=end)
    print(f'Exported {count} records')


if __name__=='__main__':
    main()
