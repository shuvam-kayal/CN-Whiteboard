# LAN Collaborative Whiteboard

A Python desktop whiteboard project. It includes independent rooms, live participant lists and cursors, local pen/eraser drawing, real-time stroke sharing, and room-based group chat over the newline-delimited JSON protocol.

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
python -m pytest
```
