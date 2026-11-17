LAN Collaborative Whiteboard — Server

1. Extract this complete Server folder.
2. Run WhiteboardServer.exe. It binds to 0.0.0.0:5000 by default.
   Optional: WhiteboardServer.exe --host 0.0.0.0 --port 5000
3. Leave its console open while clients are using the whiteboard. Press Ctrl+C to stop it cleanly.
4. On each client computer, use the server computer's Wi-Fi/Ethernet IPv4 address and port 5000.

Windows Firewall (server computer only)
If clients cannot connect, open PowerShell as Administrator and allow TCP 5000 on private networks:

  New-NetFirewallRule -DisplayName "LAN Whiteboard TCP 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private

Remove the rule after testing:

  Remove-NetFirewallRule -DisplayName "LAN Whiteboard TCP 5000"

All computers must be on the same reachable private LAN. Internet access is not needed.

Persistent board JSON files and server logs are stored outside this folder:
  %LOCALAPPDATA%\LANWhiteboard\data\boards\
  %LOCALAPPDATA%\LANWhiteboard\logs\server.log

Do not move, rename, or delete the supporting files beside WhiteboardServer.exe.
