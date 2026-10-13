@echo off
rem Gazeguard - stop all services (models, settings and alert history are kept)
cd /d "%~dp0"
docker compose -f docker-compose.webcam.yaml down
pause
