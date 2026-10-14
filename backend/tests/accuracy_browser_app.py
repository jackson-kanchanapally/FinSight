"""Disposable browser data created through the real expectation service."""
from tests.thesis_browser_app import app, Session
from tests.test_phase83 import setup, data, CAPTURE, AFTER
from app.services import expectations as service
from tests.test_phase71 import issuer

with Session() as db:
    company, _, _ = setup(db)
    issuer(db, "EMPTY")
    original_clock = service.utcnow
    try:
        service.utcnow = lambda: CAPTURE
        for i in range(3):
            service.save(db, company.id, data(source_reference=f"Browser test {i}",
                expected_value="100" if i < 2 else "80", confidence="0.75", confidence_event="MET_UNDER_FROZEN_TOLERANCE"))
        db.commit()
        service.utcnow = lambda: AFTER
        service.refresh_expectations(db, company.id)
    finally:
        service.utcnow = original_clock
