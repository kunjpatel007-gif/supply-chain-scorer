@echo off
echo Stopping Oracle Database 21c XE Services...

net stop OracleServiceXE
net stop OracleOraDB21Home1TNSListener
net stop OracleOraDB21Home1MTSRecoveryService

echo.
echo Oracle Services have been successfully stopped!
pause
