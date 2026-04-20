import random
import time

from .email_service import send_email

WARMUP_PHRASES = [
    ("Checking in", "Hey, just wanted to reach out and say hello. Hope you're having a great week!"),
    ("Quick hello", "Hi there! Just testing this email setup. Everything looks good on my end."),
    ("Update", "Just following up with a friendly note. Nothing urgent, just keeping in touch."),
    ("Hope you're well", "Hope all is good with you. Sending a quick message to stay in contact."),
    ("Greetings", "A short hello from me. Hope everything is going smoothly on your side."),
]

def run_warmup(warmup_emails, count, delay_min, delay_max, log_fn, stop_event):
    log_fn(f"ðŸ”¥ Starting warmup â€” sending {count} emails to warmup list...")
    sent = 0
    for i in range(count):
        if stop_event.is_set():
            log_fn("â›” Warmup stopped.")
            break
        target = warmup_emails[i % len(warmup_emails)].strip()
        subj, body = random.choice(WARMUP_PHRASES)
        ok = send_email(target, subj, body, log_fn)
        if ok:
            sent += 1
        wait = random.randint(delay_min, delay_max)
        log_fn(f"   â³ Waiting {wait}s before next warmup email...")
        for _ in range(wait):
            if stop_event.is_set(): break
            time.sleep(1)
    log_fn(f"ðŸ”¥ Warmup complete. Sent {sent}/{count} emails.")
