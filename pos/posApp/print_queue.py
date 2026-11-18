import logging
import queue
import threading
import time

from django.db import close_old_connections, transaction as db_transaction

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = 10  

_q = queue.Queue()
_worker_started = False
_start_lock = threading.Lock()

def _friendly_error(exc):
    low = str(exc).lower()
    if "cannot open" in low or "could not open port" in low or "semaphore timeout" in low:
        return ("Printer not reachable. Switch it on, make sure it is paired "
                "with this PC and in range, then tap Retry.")
    if "access is denied" in low or "permission" in low:
        return "Printer port is in use by another program. Close it, then tap Retry."
    return "Printing failed. Check the printer, then tap Retry."

def _process(job_id, force=False):
    from .models import PrintJob, Transaction
    from .receipt_printing import print_transaction_receipt

    job = PrintJob.objects.select_related("user").get(pk=job_id)

    while job.attempts < MAX_ATTEMPTS:
        job.attempts += 1
        job.status = PrintJob.PRINTING
        job.save(update_fields=["attempts", "status", "updated_at"])
        try:
            fresh = Transaction.objects.get(pk=job.transaction_id)
            printed = print_transaction_receipt(fresh, job.user, force=force)
            if not printed:
                job.status = PrintJob.FAILED
                job.error = ("Not printed: Auto print is off in Printer Settings, "
                             "or check the printer connection")
                job.save(update_fields=["status", "error", "updated_at"])
                return
            job.status = PrintJob.DONE
            job.error = ""
            job.save(update_fields=["status", "error", "updated_at"])
            return
        except Exception as e:
            # One short line in the log, no traceback. The user sees the message on screen.
            logger.warning("Receipt print failed (job %s, attempt %s): %s",
                           job.pk, job.attempts, type(e).__name__)
            job.error = _friendly_error(e)
            job.save(update_fields=["error", "updated_at"])
            if job.attempts < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY)

    job.status = PrintJob.FAILED
    job.save(update_fields=["status", "updated_at"])


def _worker():
    while True:
        job_id, force = _q.get()         
        try:
            close_old_connections()
            _process(job_id, force)
        except Exception:
            logger.exception("Print worker crashed on job %s", job_id)
        finally:
            close_old_connections()
            _q.task_done()


def _ensure_worker():
    global _worker_started
    with _start_lock:
        if not _worker_started:
            threading.Thread(target=_worker, name="receipt-print-worker", daemon=True).start()
            _worker_started = True

def enqueue_job(job, force=False):
    _ensure_worker()
    db_transaction.on_commit(lambda: _q.put((job.pk, force)))

def enqueue_receipt(transaction, user):
    from .models import PrintJob
    job = PrintJob.objects.create(transaction=transaction, user=user)
    enqueue_job(job)
    return job