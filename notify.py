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
timestamps to keep in sync. Everything goes through the UID commands: plain
sequence numbers are only valid within one IMAP session, and the read and the
flagging happen in two, so a message expunged in between would shift them and
we would mark the wrong reply done.

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

# Written into the notification body. Deliberately not a real id: the mail is
# sent to the same inbox it is read from, so anything parseable here would veto
# the post the message is announcing.
REPLY_HINT = "To stop one, reply to this email with:  skip <id>   (e.g. skip g0XX)"


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


def fetch_commands(include_seen: bool = False) -> List[tuple]:
    """Read vetoes WITHOUT consuming them: [(uid, [ids])].

    The uid is a real IMAP UID, stable across sessions — see the module note.

    Deliberately does not mark anything read. The caller applies the skips,
    commits, and only then calls mark_done(). If the job dies in between, the
    reply stays unread and is retried next run — a veto that was silently
    dropped would publish a post you asked it not to.
    """
    addr, pw, _ = _creds()
    if not (addr and pw):
        return []
    out = []
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=45)
        m.login(addr, pw)
        m.select("INBOX")
        # only replies to our own notifications, never the rest of the inbox
        # Subject must start "Re:" — the notification is sent to yourself, so
        # it lands in the same inbox and its own instruction line would
        # otherwise parse as a command against the post it is announcing.
        scope = "" if include_seen else "UNSEEN "
        typ, data = m.uid("SEARCH", None,
                          '(%sSUBJECT "Re: 30unfortunately")' % scope)
        for num in (data[0].split() if typ == "OK" and data[0] else []):
            typ, raw = m.uid("FETCH", num, "(BODY.PEEK[])")  # PEEK: no \Seen
            if typ != "OK" or not raw or not isinstance(raw[0], tuple):
                continue
            text = _body_text(email.message_from_bytes(raw[0][1]))
            # only what was typed, not the quoted original beneath it
            text = re.split(r"\n\s*(?:>|On .*wrote:)", text)[0]
            ids = []
            for match in SKIP_RE.findall(text):
                ids.extend(i.lower() for i in re.findall(r"[a-z][0-9]{3}", match, re.I))
            if ids:
                out.append((num, list(dict.fromkeys(ids))))
        m.logout()
    except Exception as e:
        print("notify: could not read replies (%s)" % e, file=sys.stderr)
    return out


def mark_done(uids: List[bytes]) -> None:
    """Advance the cursor, once the skips are safely in the database."""
    addr, pw, _ = _creds()
    if not (addr and pw) or not uids:
        return
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=45)
        m.login(addr, pw)
        m.select("INBOX")
        for uid in uids:
            m.uid("STORE", uid, "+FLAGS", "\\Seen")
        m.logout()
    except Exception as e:
        print("notify: could not mark replies read (%s)" % e, file=sys.stderr)


def commands() -> List[str]:
    """Read-only view, for inspection. Consumes nothing."""
    return [i for _, ids in fetch_commands() for i in ids]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        send(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "commands":
        print(" ".join(commands()))
    else:
        sys.exit(__doc__)
