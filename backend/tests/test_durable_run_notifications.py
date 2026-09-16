"""Email failures cannot change a chapter outcome or its starter."""
import smtplib
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest
from app import models
from app.db import Base
from app.services import run_notifications as mail, run_state, run_control

@pytest.fixture
def store(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setenv('AEGIS_SMTP_HOST', 'smtp.example.test')
    monkeypatch.setenv('AEGIS_NOTIFICATION_FROM', 'aegis@example.test')
    monkeypatch.delenv('AEGIS_SMTP_USER', raising=False)
    with factory() as db:
        chapter = models.Chapter(chapter_code='notification-test', board='CBSE', grade='9', subject='Science', chapter_title='Tissues')
        job = models.UploadJob(owner_sub='uploader', started_by_sub='starter', started_by_email='starter@example.test', module='build_concepts', filename='source.pdf', status='converted')
        db.add_all([chapter, job]); db.flush()
        row = models.ChapterBatchRow(chapter_id=chapter.id, job_id=job.id)
        db.add(row); db.flush()
        task = models.ChapterBatchTask(batch_row_id=row.id, job_id=job.id, kind='step01', state='done', attempt=1, enqueued_by_email='retry-person@example.test')
        db.add(task); db.commit()
        yield db, factory, job, task
    engine.dispose()

def test_starter_receives_exactly_one_stage_outcome(store):
    db, factory, job, task = store
    mail.queue_task_result(db, task, state='done'); db.flush()
    mail.queue_task_result(db, task, state='done'); db.commit()
    item = db.query(models.RunNotification).one()
    assert item.recipient == 'starter@example.test'
    assert 'ready for review' in item.subject
    sent = []
    assert mail.deliver_pending(factory, send=lambda item: sent.append(item.recipient)) == 1
    assert mail.deliver_pending(factory, send=lambda item: sent.append(item.recipient)) == 0
    assert sent == ['starter@example.test']
    db.refresh(task)
    assert task.state == 'done'

def test_pending_deployment_does_not_email_failure(store):
    db, factory, job, task = store
    mail.queue_task_result(db, task, state='queued', error='deployment')
    assert db.query(models.RunNotification).count() == 0

def test_missing_sender_keeps_notification_pending(store, monkeypatch):
    db, factory, job, task = store
    mail.queue_task_result(db, task, state='failed', error='secret provider payload'); db.commit()
    monkeypatch.delenv('AEGIS_SMTP_HOST')
    assert mail.deliver_pending(factory, send=lambda _: pytest.fail('must not send')) == 0
    item = db.query(models.RunNotification).one()
    assert item.status == 'pending' and 'secret provider payload' not in item.body
    assert mail.configuration_status()['status'] == 'sender_not_configured'

def test_safe_connection_retry_and_ambiguous_send_are_distinct(store):
    db, factory, job, task = store
    mail.queue_task_result(db, task, state='done'); db.commit()
    def disconnected(_):
        raise mail.DeliveryNotStarted('connection refused')
    mail.deliver_pending(factory, send=disconnected)
    db.expire_all(); item = db.query(models.RunNotification).one()
    assert item.status == 'pending' and item.attempts == 1
    assert item.available_at > datetime.utcnow()
    item.available_at = datetime.utcnow(); db.commit()
    def uncertain(_):
        raise smtplib.SMTPServerDisconnected('after DATA')
    mail.deliver_pending(factory, send=uncertain)
    db.expire_all()
    assert db.get(models.RunNotification, item.id).status == 'delivery_unknown'
    assert mail.deliver_pending(factory, send=lambda _: pytest.fail('ambiguous resend')) == 0

def test_suspend_preserves_progress_and_closes_clock():
    before = run_state.new(run_id='stable-run', now=100, stage='Authoring Concepts', progress=.45)
    paused = run_state.suspend(before, now=110)
    later = run_state.snapshot(paused, now=1000)
    assert later['status'] == 'waiting' and later['active_elapsed_seconds'] == 10
    assert later['stage'] == before['stage'] and later['progress'] == .45
    resumed = run_state.start(later, now=1000, stage=later['stage'])
    assert resumed['run_id'] == 'stable-run'
    assert run_state.snapshot(resumed, now=1005)['active_elapsed_seconds'] == 15
    run_state.validate(paused)

def test_pause_bypasses_content_failure_handlers():
    run_control.request_pause()
    try:
        with pytest.raises(run_control.RunDeferred):
            try:
                run_control.check()
            except Exception:
                pytest.fail('deployment treated as content failure')
    finally:
        run_control.reset()


def test_signal_pause_precedes_server_http_drain(monkeypatch):
    import signal
    seen = []
    handlers = {signal.SIGTERM: lambda *_: seen.append(run_control.pausing()),
                signal.SIGINT: signal.SIG_DFL}
    original = handlers[signal.SIGTERM]
    monkeypatch.setattr(signal, 'getsignal', lambda signum: handlers[signum])
    monkeypatch.setattr(signal, 'signal', lambda signum, fn: handlers.__setitem__(signum, fn))
    run_control.reset()
    restore = run_control.install_shutdown_handlers()
    try:
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert seen == [True]
        assert handlers[signal.SIGINT] == signal.SIG_DFL
    finally:
        restore()
        run_control.reset()
    assert handlers[signal.SIGTERM] is original
