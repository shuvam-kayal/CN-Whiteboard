# LAN Collaborative Whiteboard

A Python desktop whiteboard project. Slice 3 adds independent rooms and participant sessions over the threaded TCP server and newline-delimited JSON protocol. Whiteboard interaction features will be added in later slices.

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
