import concurrent.futures
from datetime import datetime,timedelta,timezone
import pytest
from receptionist.booking import BookingStore,SlotTaken,KeyConflict,InvalidBooking
from receptionist.dialogue import Dialogue

START=datetime(2027,1,20,16,tzinfo=timezone.utc)

def test_booking_idempotency_and_overlap(tmp_path):
    db=BookingStore(str(tmp_path/'slots.sqlite'))
    x=db.book('r',START,START+timedelta(minutes=30),'k')
    assert db.book('r',START,START+timedelta(minutes=30),'k')['id']==x['id']
    with pytest.raises(KeyConflict):db.book('r',START+timedelta(minutes=30),START+timedelta(minutes=60),'k')
    with pytest.raises(SlotTaken):db.book('r',START+timedelta(minutes=15),START+timedelta(minutes=45),'k2')
    assert db.book('r',START+timedelta(minutes=30),START+timedelta(minutes=60),'k3')
    assert db.cancel(x['id']) and not db.cancel(x['id'])
    assert len(db.active())==1

def test_slot_race_two_connections(tmp_path):
    path=str(tmp_path/'race.sqlite')
    BookingStore(path)
    def attempt(i):
        try:
            return BookingStore(path).book('r',START,START+timedelta(minutes=30),f'{i}')['id']
        except SlotTaken:return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(attempt,range(40)))
    assert sum(x is not None for x in results)==1
    assert len(BookingStore(path).active())==1

def test_no_confirmation_no_booking_and_dst(tmp_path):
    db=BookingStore(str(tmp_path/'dialogue.sqlite'))
    d=Dialogue(db)
    assert d.handle('book 2027-12-10 14:00','a').action=='confirm_required'
    assert db.active()==[]
    assert 'no appointment' in d.handle('no','a').text
    assert db.active()==[]
    assert d.handle('book 2027-03-14 02:30','a').action=='invalid'
    assert d.handle('book 2027-11-07 01:30','a').action=='invalid'
    assert db.active()==[]

def test_confirm_twice_not_duplicate(tmp_path):
    db=BookingStore(str(tmp_path/'dialogue.sqlite'))
    d=Dialogue(db)
    d.handle('book 2027-12-10 14:00','a')
    yes=d.handle('yes','a')
    assert yes.action=='booked'
    assert len(db.active())==1
    assert d.handle('yes','a').action=='reply'
    assert len(db.active())==1
    assert d.handle('cancel '+yes.committed_id,'a').action=='cancelled'
    assert db.active()==[]
