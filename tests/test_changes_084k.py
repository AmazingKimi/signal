import pytest
from app import changes
from app.store import _conn


@pytest.fixture
def candidate():
    conn=_conn(); changes.init(conn)
    conn.execute('DELETE FROM candidate_changes'); conn.execute('DELETE FROM candidate_snapshots'); conn.commit(); conn.close()
    return dict(title='Original sculpture',maker='Unknown artist',object_name='sculpture',year='1980',
                source_name='dealer',asking_price=None,currency='USD',availability='INQUIRE')


def types(record, run=1, url='https://dealer.test/item'):
    return changes.observe(900,run,record,url,True)


def test_new_price_drop_unchanged_and_persistence(candidate):
    cid,events=types(candidate)
    assert [e['event_type'] for e in events]==['NEW_LISTING']
    assert not types(candidate,2)[1]
    candidate['asking_price']=20000
    assert types(candidate,3)[1][0]['event_type']=='PRICE_ADDED'
    candidate['asking_price']=17000
    cid2,events=types(candidate,4)
    assert cid==cid2
    drop=next(e for e in events if e['event_type']=='PRICE_DROPPED')
    assert drop['payload']['change_percent']==-15 and drop['payload']['change_absolute']==-3000
    assert not types(candidate,5)[1]
    assert changes.details(cid)['changes']


def test_status_and_relisted(candidate):
    candidate['availability']='UPCOMING_AUCTION'; types(candidate)
    candidate['availability']='LIVE_AUCTION'
    assert types(candidate,2)[1][0]['event_type']=='STATUS_CHANGED'
    candidate['availability']='SOLD'; types(candidate,3)
    candidate['availability']='FOR_SALE'
    assert 'RELISTED' in [e['event_type'] for e in types(candidate,4)[1]]


def test_no_false_drop_from_currency_or_missing_price(candidate):
    candidate['asking_price']=20000; types(candidate)
    candidate.update(asking_price=18000,currency='EUR')
    assert [e['event_type'] for e in types(candidate,2)[1]]==['PRICE_CHANGED']
    candidate['asking_price']=None
    assert not types(candidate,3)[1]


def test_distinct_urls_and_relisted_vin(candidate):
    first=types(candidate)[0]
    assert types(candidate,url='https://dealer.test/other')[0]!=first
    candidate.update(title='Car WP0ZZZ99ZTS392124',availability='SOLD')
    cid=types(candidate,url='https://dealer.test/car')[0]
    candidate['availability']='FOR_SALE'
    cid2,events=types(candidate,2,url='https://other.test/car')
    assert cid2==cid and 'RELISTED' in [e['event_type'] for e in events]


def test_real_run_pipeline_six_states(monkeypatch, candidate):
    from app.radar import create_radar,run_radar,list_discoveries
    radar=create_radar('K integration sculpture',rules=dict(name='K integration sculpture',category='ART',
                       maker_artist='Unknown',model_series='sculpture',include_keywords=['sculpture']))
    monkeypatch.setattr('app.radar.expand_queries',lambda r:['sculpture'])
    class Provider:
        name='fixture'; status='OK'
        title='sculpture price on request'
        def search(self,q,max_results=5):
            return [dict(title=self.title,url='https://integration.test/item',snippet=self.title)]
    provider=Provider()
    def run(title):
        provider.title=title
        return run_radar(radar['id'],provider,send_email=False)
    assert run('sculpture price on request')['new_listings']==1
    assert run('sculpture available USD 20000')['new_discoveries']==1
    assert run('sculpture available USD 17000')['price_changes']==1
    assert run('sculpture available USD 17000')['new_discoveries']==0
    assert run('sculpture sold USD 17000')['status_changes']==1
    assert run('sculpture available USD 17000')['relisted']==1
    rows=[x for x in list_discoveries() if x['radar_id']==radar['id']]
    assert len(rows)==1 and any(e['event_type']=='RELISTED' for e in rows[0]['changes'])
