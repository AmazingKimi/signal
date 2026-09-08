"""Observed candidate state and deterministic changes; no network or LLM calls."""
import json
from .models import Comparable, now_iso
from .watch import make_fingerprint
from .store import _conn
from .source_urls import require_source_url


def init(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS candidate_snapshots (
      candidate_id INTEGER PRIMARY KEY, radar_id INTEGER NOT NULL,
      fingerprint TEXT NOT NULL, canonical_url TEXT NOT NULL,
      state_json TEXT NOT NULL, first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL, last_changed_at TEXT NOT NULL,
      UNIQUE(radar_id,fingerprint));
    CREATE TABLE IF NOT EXISTS candidate_changes (
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL, run_id INTEGER NOT NULL,
      event_type TEXT NOT NULL, priority TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL, notification_queued INTEGER NOT NULL DEFAULT 0);
    ''')


def observe(radar_id, run_id, record, canonical_url, notifications=False):
    require_source_url(canonical_url)
    status = record.get('availability', 'UNKNOWN')
    status = {'FOR_SALE':'AVAILABLE'}.get(status, status)
    if status not in {'AVAILABLE','INQUIRE','UPCOMING_AUCTION','LIVE_AUCTION','SOLD','REMOVED','UNKNOWN'}:
        status = 'UNKNOWN'
    state = dict(canonical_url=canonical_url, source=record.get('source_name'),
                 title=record.get('title'), price=record.get('asking_price'),
                 currency=record.get('currency'), availability_status=status,
                 auction_status=status if 'AUCTION' in status else None,
                 estimate_low=record.get('estimate_low'), estimate_high=record.get('estimate_high'),
                 location=record.get('location'))
    comparable = Comparable(title=record['title'], price=record.get('asking_price') or 0,
                            source_url=canonical_url, year=record.get('year'),
                            attributes={'canonical_url':canonical_url})
    fp = make_fingerprint(comparable, {'maker':record.get('maker'), 'target':record.get('object_name')})
    conn = _conn()
    try:
        init(conn)
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT candidate_id,state_json FROM candidate_snapshots WHERE radar_id=? AND fingerprint=?',
                           (radar_id, fp)).fetchone()
        now = now_iso(); events = []
        def event(kind, priority, **payload):
            events.append(dict(event_type=kind, priority=priority, payload=payload, created_at=now))
        if old:
            cid, serialized = old; prev = json.loads(serialized)
            # Absence of a price in a later snippet is not proof it was removed.
            if state['price'] is None:
                state['price'], state['currency'] = prev['price'], prev['currency']
            if status == 'UNKNOWN':
                state['availability_status'] = prev['availability_status']
                state['auction_status'] = prev['auction_status']
            if prev['price'] is None and state['price'] is not None:
                event('PRICE_ADDED','MEDIUM', old_price=None,new_price=state['price'],currency=state['currency'])
            elif state['price'] is not None and (state['price'],state['currency']) != (prev['price'],prev['currency']):
                same_currency = state['currency'] == prev['currency']
                delta = state['price'] - prev['price'] if same_currency else None
                payload = dict(old_price=prev['price'],new_price=state['price'],old_currency=prev['currency'],
                               currency=state['currency'],change_absolute=delta,
                               change_percent=100*delta/prev['price'] if delta is not None and prev['price'] else None)
                event('PRICE_CHANGED','MEDIUM',**payload)
                if delta is not None and delta != 0:
                    event('PRICE_DROPPED' if delta < 0 else 'PRICE_INCREASED','HIGH' if delta < 0 else 'LOW',**payload)
            if state['availability_status'] != prev['availability_status']:
                event('STATUS_CHANGED','HIGH' if status == 'LIVE_AUCTION' else 'MEDIUM',
                      old_status=prev['availability_status'],new_status=state['availability_status'])
                if prev['availability_status'] in {'SOLD','REMOVED'} and status in {'AVAILABLE','INQUIRE','UPCOMING_AUCTION','LIVE_AUCTION'}:
                    event('RELISTED','HIGH'); event('RETURNED_TO_MARKET','HIGH')
            if canonical_url != prev['canonical_url']:
                event('SOURCE_CHANGED','MEDIUM',old_source=prev['source'],new_source=state['source'])
            conn.execute('UPDATE candidate_snapshots SET state_json=?,last_seen_at=?,last_changed_at=CASE WHEN ? THEN ? ELSE last_changed_at END WHERE candidate_id=?',
                         (json.dumps(state,ensure_ascii=False),now,bool(events),now,cid))
        else:
            cur = conn.execute('INSERT INTO candidate_snapshots(radar_id,fingerprint,canonical_url,state_json,first_seen_at,last_seen_at,last_changed_at) VALUES(?,?,?,?,?,?,?)',
                               (radar_id,fp,canonical_url,json.dumps(state,ensure_ascii=False),now,now,now))
            cid=cur.lastrowid
            event('NEW_LISTING','HIGH')
        for e in events:
            conn.execute('INSERT INTO candidate_changes(candidate_id,run_id,event_type,priority,payload_json,created_at,notification_queued) VALUES(?,?,?,?,?,?,?)',
                         (cid,run_id,e['event_type'],e['priority'],json.dumps(e['payload']),now,int(notifications and e['priority']=='HIGH')))
        conn.commit()
        record['asking_price']=state['price']
        record['currency']=state['currency']
        return cid, events
    finally:
        conn.close()


def details(candidate_id):
    conn=_conn()
    try:
        row=conn.execute('SELECT first_seen_at,last_seen_at,last_changed_at FROM candidate_snapshots WHERE candidate_id=?',(candidate_id,)).fetchone()
        if not row: return {}
        events=conn.execute('SELECT event_type,payload_json,created_at FROM candidate_changes WHERE candidate_id=? ORDER BY id DESC',(candidate_id,)).fetchall()
        latest_run=conn.execute('SELECT run_id FROM candidate_changes WHERE candidate_id=? ORDER BY id DESC LIMIT 1',(candidate_id,)).fetchone()
        events=conn.execute('SELECT event_type,payload_json,created_at FROM candidate_changes WHERE candidate_id=? AND run_id=? ORDER BY id DESC',(candidate_id,latest_run[0])).fetchall() if latest_run else []
        latest=[dict(event_type=e[0],payload=json.loads(e[1]),created_at=e[2]) for e in events]
        return dict(first_seen_at=row[0],last_seen_at=row[1],last_changed_at=row[2],changes=latest)
    finally: conn.close()


def tracked(radar_id, canonical_url):
    conn=_conn()
    try:
        init(conn)
        return conn.execute('SELECT candidate_id FROM candidate_snapshots WHERE radar_id=? AND canonical_url=?',
                            (radar_id,canonical_url)).fetchone() is not None
    finally: conn.close()
