tell application "System Events"
	-- Send Shift+Control+Cmd + R to start
	key code 15 using {shift down, control down, command down}
end tell


delay 0.2 -- Give time to activate

# Click at 70% X and 70% Y of the main screen

do shell script "cliclick c:1080,790"


