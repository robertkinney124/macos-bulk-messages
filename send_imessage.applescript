-- iMessage-first: send via iMessage buddy if available; otherwise signal NOT_IMESSAGE
on run {targetPhone, targetMessage}
	tell application "Messages"
		activate
		set targetPhone to (targetPhone as text)
		set targetMessage to (targetMessage as text)

		-- Find iMessage service
		set iMessageService to missing value
		repeat with s in services
			if (service type of s) is iMessage then
				set iMessageService to s
				exit repeat
			end if
		end repeat
		if iMessageService is missing value then error "NO_IMESSAGE_SERVICE"

		-- Try common variants for the iMessage buddy id
		set variants to {targetPhone}
		if targetPhone starts with "+" then
			set end of variants to (text 2 thru -1 of targetPhone) -- digits only
		else
			set end of variants to ("+" & targetPhone) -- with +
		end if

		-- 1) Prefer buddy on iMessage service (forces blue route)
		repeat with p in variants
			try
				set imBuddy to buddy (p as text) of iMessageService
				send targetMessage to imBuddy
				return "SENT_IMESSAGE"
			end try
		end repeat

		-- 2) Fall back: existing iMessage chat that already contains this participant
		repeat with c in chats
			try
				if ((participants of c) contains targetPhone) then
					if (service type of (service of c)) is iMessage then
						send targetMessage to c
						return "SENT_IMESSAGE"
					end if
				end if
			end try
		end repeat

		-- Not iMessage-capable here → let Python trigger SMS fallback
		error "NOT_IMESSAGE"
	end tell
end run
