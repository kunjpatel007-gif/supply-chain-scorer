@echo off
echo Starting Oracle Database 21c XE Services...

net start OracleServiceXE
net start OracleOraDB21Home1TNSListener

echo.
echo Oracle Services are now running!
pause
