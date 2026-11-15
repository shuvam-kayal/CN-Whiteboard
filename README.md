# LAN Collaborative Whiteboard

A Python desktop whiteboard project. It includes independent rooms, live participant lists and cursors, local pen/eraser and line/rectangle/ellipse drawing with synchronized undo/redo, real-time operation sharing, room-based group chat, and PNG export over the newline-delimited JSON protocol.

## Requirements

- Python 3.12 or newer
- Windows (instructions below)

## Setup on Windows

From the project root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

Launch the desktop client:

```powershell
python run_client.py
```

Create a room from the client, then have other clients join with its room code. Select a pen color and size in the toolbar; completed pen and eraser strokes are sent to other room participants.

The room host can use **Save board** and **Load board** in the whiteboard toolbar. Board snapshots are validated JSON files in `data/boards/`. When a server restarts, the first participant to join a room with a saved board resumes as its host, and the current board is sent to each joining participant.

Participants can undo and redo their own drawing operations with the toolbar buttons. On restart, saved participant IDs are resumed by matching the saved username; usernames are not authenticated accounts.

Choose **Line**, **Rectangle**, or **Ellipse** from the Shape selector and drag on the canvas. **Export PNG** saves the completed whiteboard at its current canvas dimensions without the surrounding application interface.

Start the TCP server (defaults to all IPv4 interfaces on port 5000):

```powershell
python run_server.py
```

Configure the bind address and port with `python run_server.py --host 0.0.0.0 --port 5000`.

In another terminal, connect an interactive test client:

```powershell
python tcp_test_client.py --host 127.0.0.1 --port 5000
```

Enter text to receive a JSON echo response, or type `/quit` to disconnect.

## Tests

```powershell
python -m pytest -q
```

## Manual LAN test (Windows)

This test uses the local Wi-Fi/LAN only; the machines do not need internet access. Python and the project dependencies must already be installed on the computers before going offline.

1. Connect the server computer and at least one client computer to the same private Wi-Fi/LAN. On the server computer, run `ipconfig` and note the IPv4 address of the active Wi-Fi/Ethernet adapter (for example, `192.168.1.25`). Do not use `127.0.0.1` on another computer.
2. If Windows Firewall blocks inbound connections, open **PowerShell as Administrator** on the server computer and add a private-network-only rule for the TCP server port:

   ```powershell
   New-NetFirewallRule -DisplayName "LAN Whiteboard TCP 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
   ```

   Remove the rule after testing with:

   ```powershell
   Remove-NetFirewallRule -DisplayName "LAN Whiteboard TCP 5000"
   ```

   Keep the Windows network profile set to **Private** for the trusted LAN. Do not create a Public-profile rule or disable the firewall.
3. On the server computer, from the project directory (and with its project environment active if used), run:

   ```powershell
   python run_server.py --host 0.0.0.0 --port 5000
   ```

   The server logs its listening address and connection events in this terminal.
4. On each client computer, run `python run_client.py`. Enter the server computer's LAN IPv4 address and port `5000`, create a room on one computer, and join by room code on the others.
5. Draw with each participant, move cursors, exchange chat, and try Undo/Redo. Save the board before testing recovery. To test reconnection, temporarily stop and restart the server, then check whether the client reconnects and receives the saved board snapshot.
6. If a client cannot connect, confirm both devices are on the same subnet, the server is still listening on `0.0.0.0:5000`, the client entered the server's LAN IPv4 address, and the private firewall rule is enabled. No internet access is needed for the active LAN test.

No multi-computer LAN verification has been performed as part of this implementation.
