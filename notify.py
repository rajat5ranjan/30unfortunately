#!/usr/bin/env python3
"""
notify.py — the veto channel: email out, email replies back in.

    python3 notify.py send "subject" "body"
    python3 notify.py commands           print /skip ids found in unread replies

Email rather than a chat app because it is the one channel that already pushes
to your phone, replying is natural, and both halves are in the standard library
so the publish job keeps its no-install guarantee.

There is no webhook: Actions cannot receive one. publish.py polls for replies
immediately before posting, which is exactly when a veto matters, so the window
is real-time anyway.

Unread/read is the cursor. A reply is processed once and marked seen; no
timestamps to keep in sync.

Silently does nothing when the secrets are absent, so the pipeline runs
unchanged without notifications configured.
"""
import email
import email.message
import imaplib
import os
import re
import smtplib
import sys
from typing import List, Optional

import envfile

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")

SKIP_RE = re.compile(r"\b(?:skip|/skip)\s+((?:[a-z][0-9]{3}[\s,]*)+)", re.I)


def _creds():
    envfile.load()
    addr = os.environ.get("NOTIFY_EMAIL")
    pw = os.environ.get("NOTIFY_EMAIL_PASSWORD")
    return addr, pw, os.environ.get("NOTIFY_TO") or addr


def send(subject: str, body: str) -> bool:
    addr, pw, to = _creds()
    if not (addr and pw):
        print("notify: NOTIFY_EMAIL/NOTIFY_EMAIL_PASSWORD not set — skipping")
        return False
    msg = email.message.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = addr, to, subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=45) as s:
        s.starttls()
        s.login(addr, pw)
        s.send_message(msg)
    print("notify: sent to %s" % to)
    return True


def _body_text(msg) -> str:
    if not msg.is_multipart():
        try:
            return msg.get_payload(decode=True).decode(errors="replace")
        except Exception:
            return ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                return part.get_payload(decode=True).decode(errors="replace")
            except Exception:
                continue
    return ""


def commands() -> List[str]:
    """Post ids vetoed in unread replies. Marks them read so they apply once."""
    addr, pw, _ = _creds()
    if not (addr and pw):
        return []
    ids = []
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=45)
        m.login(addr, pw)
        m.select("INBOX")
        # only replies to our own notifications, not the whole inbox
        typ, data = m.search(None, '(UNSEEN SUBJECT "30unfortunately")')
        for num in (data[0].split() if typ == "OK" else []):
            typ, raw = m.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            text = _body_text(email.message_from_bytes(raw[0][1]))
            # only the reply, not the quoted original below it
            text = re.split(r"\n\s*(?:>|On .*wrote:)", text)[0]
            for match in SKIP_RE.findall(text):
                ids.extend(re.findall(r"[a-z][0-9]{3}", match, re.I))
            m.store(num, "+FLAGS", "\\Seen")
        m.logout()
    except Exception as e:
        print("notify: could not read replies (%s)" % e, file=sys.stderr)
    return [i.lower() for i in dict.fromkeys(ids)]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        send(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "commands":
        print(" ".join(commands()))
    else:
        sys.exit(__doc__)
