tell application "MuseScore 3"
	activate
end tell

delay 0.2 -- Give time to activate

tell application "System Events"
	-- Send Command+A to select all
	keystroke "a" using {command down}

	-- Send Escape key to cancel selection (if needed)
	key code 53

	-- Start playback (spacebar)
	keystroke " "
end tell

delay 1.5 -- Give time to activate
tell application "System Events"
	-- Stop playback (spacebar)
	keystroke " "

	-- Send Command+A to select all
	keystroke "a" using {command down}

	-- Send Escape key to cancel selection (if needed)
	key code 53

end tell