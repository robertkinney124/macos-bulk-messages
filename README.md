Bulk Messages for macOS (iMessage with SMS Fallback)

This project lets you send personalized bulk messages on macOS using the built-in Messages app. Messages are sent via iMessage first, with automatic SMS fallback if delivery fails or isn’t confirmed within a short time window.

Files

bulk_imessage.py
Python wrapper that reads contacts from CSV and sends messages using AppleScript.

Supports {first_name} templating in messages.

iMessage-first, retries via SMS if delivery fails.

Optional link tracking (?cid=<digits>).

Logs every attempt to send_log.csv.

send_imessage.applescript
AppleScript for sending messages via iMessage. Called automatically by bulk_imessage.py.

send_sms_only.applescript
AppleScript for sending messages via SMS. Used as a fallback when iMessage fails.

Usage

Prepare your CSV with at least:

phone,first_name,email
+14085551234,Robert,rob@example.com
+14085559876,Jessica,jess@example.com


Run a dry run to test your template (no messages sent):

python3 bulk_imessage.py contacts.csv \
  --applescript send_imessage.applescript \
  --sms-applescript send_sms_only.applescript \
  --message "Hi {first_name}, this is a test message." \
  --verify-imessage --verify-wait 0.5 --verify-timeout 0.5 \
  --log-file send_log.csv \
  --dry-run --limit 5


Send for real (with instant fallback to SMS if iMessage fails):

python3 bulk_imessage.py contacts.csv \
  --applescript send_imessage.applescript \
  --sms-applescript send_sms_only.applescript \
  --message "Hi {first_name}, Robert from REP Gym Pass. Following up on our email..." \
  --verify-imessage --verify-wait 0.5 --verify-timeout 0.5 \
  --delay 3 \
  --track-link \
  --log-file send_log.csv \
  --no-ref-tag

Notes

Requires: macOS Messages, Python 3, AppleScript, and Full Disk Access for Terminal (to read the Messages database).

SMS fallback: Requires Text Message Forwarding enabled on your iPhone.

Scaling: Use --limit for testing and increase gradually. Add --delay to avoid stressing Messages.

Future plans: A GUI is planned to make this workflow easier for non-technical users.
