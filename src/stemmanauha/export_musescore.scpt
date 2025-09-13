tell application "MuseScore 3"
	activate
end tell

delay 0.2 -- Give time to activate

tell application "System Events"
	-- Send Command-Option-E
	keystroke "e" using {command down, option down}
end tell

delay 0.2

tell application "System Events"
	-- Press Tab to move focus
	keystroke tab

	delay 0.1

	-- Press Return to confirm
	keystroke return
end tell
