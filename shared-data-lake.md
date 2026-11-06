# Installing a shared data lake

An administrator installs the data lake once, and everyone on the computer picks it in the app. The app never downloads into or repairs a shared folder. Biomni loads pickle files from the lake, so anyone who can change those files could run code as whoever opens the app.

Start from a complete lake, such as an install's `data/biomni_data` folder. The shared folder must contain it as a subfolder named `biomni_data`.

## Mac

```sh
sudo mkdir -p "/Library/Application Support/biomnikhil"
sudo cp -R /path/to/biomni_data "/Library/Application Support/biomnikhil/"
sudo chown -R root:wheel "/Library/Application Support/biomnikhil"
sudo chmod -R go-w "/Library/Application Support/biomnikhil"
```

## Linux

```sh
sudo mkdir -p /var/lib/biomnikhil
sudo cp -R /path/to/biomni_data /var/lib/biomnikhil/
sudo chown -R root:root /var/lib/biomnikhil
sudo chmod -R go-w /var/lib/biomnikhil
```

## Windows

In an administrator PowerShell:

```powershell
New-Item -ItemType Directory -Force "$env:ProgramData\biomnikhil"
Copy-Item -Recurse C:\path\to\biomni_data "$env:ProgramData\biomnikhil\"
icacls "$env:ProgramData\biomnikhil" /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" /T
```

This gives Administrators and SYSTEM full control and Users read-only access. The app can't check these permissions on Windows, so this step is what protects the lake there.

## In the app

Choose **Shared folder installed by an administrator** in the download popup and confirm the path. On Mac and Linux, the app refuses the folder if other users can change the lake.
