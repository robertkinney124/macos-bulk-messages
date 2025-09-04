on run {targetPhone, targetMessage}
	tell application "Messages"
		activate
		set targetPhone to (targetPhone as text)
		set targetMessage to (targetMessage as text)

		-- Find SMS service (requires Text Message Forwarding)
		set smsService to missing value
		repeat with s in services
			if (service type of s) is SMS then
				set smsService to s
				exit repeat
			end if
		end repeat
		if smsService is missing value then error "NO_SMS_SERVICE"

		-- 1) Try an existing SMS chat
		set smsChat to missing value
		repeat with c in chats
			try
				if ((participants of c) contains targetPhone) then
					if (service type of (service of c)) is SMS then
						set smsChat to c
						exit repeat
					end if
				end if
			end try
		end repeat
		if smsChat is not missing value then
			send targetMessage to smsChat
			return "SENT_SMS_EXISTING"
		end if

		-- 2) Create a fresh SMS chat
		try
			set smsChat to make new chat with properties {service:smsService, participants:{targetPhone}}
			send targetMessage to smsChat
			return "SENT_SMS_CHAT"
		on error errMsg number errNum
			-- 3) Final fallback: buddy send
			try
				set theBuddy to buddy targetPhone of smsService
				send targetMessage to theBuddy
				return "SENT_SMS_BUDDY"
			on error errMsg2 number errNum2
				error "SMS_SEND_FAILED: " & errMsg & " / " & errMsg2
			end try
		end try
	end tell
end run
