LAN Collaborative Whiteboard — Client

1. Extract this complete Client folder.
2. Run WhiteboardClient.exe.
3. Enter your name, the server computer's Wi-Fi/Ethernet IPv4 address, and port 5000.
4. Create a room or enter a room code shared by the host.

The server address is intentionally blank initially so a client on another computer does not try to connect to its own localhost. For two clients on the same computer, enter 127.0.0.1 when the server is running on that computer.

All computers must be on the same reachable private LAN. Internet access is not needed.

The packaged client uses the Windows ICU runtime library (icuuc.dll). Use a supported Windows version that includes this System32 library.

Client logs are stored outside this folder:
  %LOCALAPPDATA%\LANWhiteboard\logs\client.log

Do not move, rename, or delete the supporting files beside WhiteboardClient.exe.
