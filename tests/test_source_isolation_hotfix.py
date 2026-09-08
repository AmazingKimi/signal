import pytest
from app.source_urls import valid_source_url,require_source_url
from app import store

@pytest.mark.parametrize('url',[
 'https://example.test/fixture-sculpture','https://example.com/item','http://localhost:8778/item',
 'http://127.0.0.1/item','https://foo.invalid/item','https://mock.seller.com/item',
 'https://dummy.com/item','javascript:alert(1)','https://user:pass@seller.com/a'])
def test_rejects_placeholder_and_local_sources(url,monkeypatch):
    monkeypatch.delenv('SIGNAL_TEST_DATA',raising=False)
    assert not valid_source_url(url)
    with pytest.raises(ValueError): require_source_url(url)

def test_retains_real_source_url():
    assert valid_source_url('https://www.phillips.com/detail/takis/166700')
    assert valid_source_url('https://www.pamono.com/chair?currency=EUR')

def test_test_flag_cannot_bypass_real_database(monkeypatch,tmp_path):
    monkeypatch.setenv('SIGNAL_TEST_DATA','1')
    monkeypatch.setattr(store,'DB_PATH',str(tmp_path/'real.db'))
    with pytest.raises(ValueError): require_source_url('https://example.com/a')

def test_production_pipeline_rejects_before_any_snapshot(monkeypatch):
    from app.radar import create_radar,run_radar
    monkeypatch.delenv('SIGNAL_TEST_DATA',raising=False)
    monkeypatch.setattr('app.radar.expand_queries',lambda _:['chair'])
    radar=create_radar('chair',rules={'name':'isolation','include_keywords':['chair'],'category':'FURNITURE'})
    class Provider:
        name='fixture';status='OK'
        def search(self,*a,**k): return [{'title':'chair available','url':'https://example.com/fixture'}]
    run=run_radar(radar['id'],Provider(),send_email=False)
    assert run['new_discoveries']==0 and run['new_listings']==0
