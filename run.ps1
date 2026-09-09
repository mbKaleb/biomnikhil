# Start the app under waitress (Windows-native WSGI server).
. "$PSScriptRoot\scripts\activate.ps1"
waitress-serve --listen=127.0.0.1:8000 wsgi:app
