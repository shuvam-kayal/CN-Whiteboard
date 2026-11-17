# LAN Collaborative Whiteboard

A Windows desktop whiteboard for small teams on a private Wi-Fi/LAN. One computer runs the TCP server; other computers run the PySide6 client. Normal application use does not require internet access.

## Architecture and features

- Python 3.12+, PySide6, built-in IPv4 TCP sockets, newline-delimited JSON, and JSON board files.
- Independent rooms, participants, live cursors, and room chat.
- Pen, pencil, marker, highlighter, eraser, line, rectangle, and ellipse tools.
- Collaborative drawing, per-participant undo/redo, local zoom and pan, and a world-coordinate workspace.
- Host-controlled board save/load and restart recovery.
- PNG export sized to the active drawing bounds; oversized exports are rejected with a UI error.

The server is authoritative for room membership, drawing operation ordering, undo/redo state, and saved boards. Each client controls its own viewport.

## Development setup (Windows, Python 3.12)

From the project directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the test suite:

```powershell
python -m pytest -q
```

## Run from source

Start the server in one terminal:

```powershell
python run_server.py
```

It listens on `0.0.0.0:5000` by default. Bind address and port can be set explicitly:

```powershell
python run_server.py --host 0.0.0.0 --port 5000
```

Start a client in another terminal:

```powershell
python run_client.py
```

Enter the server computer's IPv4 address and port in the client. On the same computer, use `127.0.0.1`; on another computer, use the server's Wi-Fi/Ethernet IPv4 address. The address field is deliberately blank at startup to avoid accidentally connecting a LAN user to their own computer. One client creates a room; other clients join using its room code.

## Build Windows applications

The reproducible build uses PyInstaller 6.22.3 in one-folder (`onedir`) mode. The build script checks for Python 3.12, creates or reuses `.venv-packaging`, installs the project and build dependencies, runs all tests, builds both applications, and prepares `dist\LAN-Whiteboard-Release`.

On a build computer with Python 3.12 available through the Windows Python Launcher:

```powershell
.\packaging\Build-Windows.ps1
```

Build dependencies are declared separately in `requirements-build.txt`; they are not needed on deployment computers. The build computer must be able to install those packages or already have them available to pip. The packaged applications themselves make no runtime internet calls.

Expected release layout:

```text
dist\LAN-Whiteboard-Release\
  Server\
    WhiteboardServer.exe
    supporting PyInstaller files
    README-Server.txt
  Client\
    WhiteboardClient.exe
    supporting PyInstaller/Qt files
    README-Client.txt
```

Copy the **entire** Server or Client folder to its destination computer; keep the supporting files beside the executable. Python does not need to be installed on deployment computers.

### Run the packaged server

On one computer, extract the Server folder and run `WhiteboardServer.exe`. It listens on `0.0.0.0:5000` by default. Optional command-line configuration:

```powershell
.\WhiteboardServer.exe --host 0.0.0.0 --port 5000
```

The console reports startup and shutdown and remains open while the server runs.

### Connect packaged clients

On each client computer, extract the Client folder and run `WhiteboardClient.exe`. Enter the server computer's IPv4 address and port `5000`, then create or join a room. A client on the server computer can use `127.0.0.1`.

## Offline LAN deployment and Windows Firewall

1. Connect all computers to the same reachable private Wi-Fi/LAN. Internet access is not required.
2. On the server computer, run `ipconfig` and note the active adapter's IPv4 address.
3. Start `WhiteboardServer.exe` on the server computer.
4. If Windows Firewall blocks clients, open PowerShell as Administrator **on the server computer** and add a private-profile rule:

   ```powershell
   New-NetFirewallRule -DisplayName "LAN Whiteboard TCP 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
   ```

5. On every client computer, start `WhiteboardClient.exe`, enter the server computer's IPv4 address and port `5000`, and create or join a room.
6. Remove the firewall rule after testing if it is no longer needed:

   ```powershell
   Remove-NetFirewallRule -DisplayName "LAN Whiteboard TCP 5000"
   ```

Keep the LAN's Windows network profile set to **Private**. Do not create a Public-profile rule or disable the firewall. All machines must be able to reach the server over the private LAN.

## Board data and logs

Packaged applications keep persistent data outside the extracted release folder, under:

```text
%LOCALAPPDATA%\LANWhiteboard\
  data\boards\
  logs\server.log
  logs\client.log
```

Board files remain across app upgrades and restarts because upgrades do not modify the app-data directory. Back up this directory to preserve saved boards. Source-mode execution continues to use the project directory: `data\boards\` and `logs\`.

## Known limits and verification status

- Server drawing/cursor coordinates are safety-limited to ±10,000,000 world units; rooms are limited to 50,000 operations and protocol snapshots to the existing message-size limit.
- PNG export is bounded to 16,384 pixels per side and 16 million total pixels to protect memory use.
- Username-based board recovery is not authenticated account identity.
- The packaged client uses the Windows ICU runtime (`icuuc.dll` in System32); deploy to a supported Windows version that provides this library.
- Automated tests cover local TCP integration, persistence, and GUI logic. Multi-computer packaged LAN testing must still be performed on the target network; do not treat loopback tests as LAN verification.
- Runtime code uses local TCP and local files only; it has no cloud service, online authentication, runtime download, remote font, or external resource dependency.
