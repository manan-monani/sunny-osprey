@echo off
rem Sunny Osprey - stop all services (models, settings and alert history are kept)
cd /d "%~dp0"
docker compose -f docker-compose.webcam.yaml down
pause
